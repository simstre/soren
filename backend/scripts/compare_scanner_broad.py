#!/usr/bin/env python3
"""Compare Approach 2 (67-ticker real backtest scorer) vs Broad scan (~7000 US equities).

Approach 2: Production scanner — 67-ticker SCAN_UNIVERSE → phase 1 daily filter
            → real 7-day backtest scoring → top 4 → 29-day backtest
Broad scan: Standalone experiment — ~7000 US equities from NASDAQ FTP → batch
            yf.download filter → daily-metrics scoring (phase 1.5) → real 7-day
            backtest scoring → top 4 → 29-day backtest
Baseline:   Hand-picked TSLA+CEG+PLTR+RIVN

All 3 arms use the same ReplayEngine 29-day backtest for the final comparison.

Usage:
    cd backend && .venv/bin/python -m scripts.compare_scanner_broad
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ["LOG_LEVEL"] = "ERROR"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import numpy as np
import pandas as pd
import yfinance as yf
import structlog

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
logger = structlog.get_logger()

from app.analysis.indicators import atr as calc_atr
from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine
from app.engine.strategies import get_strategy
from app.scanner.fitness import run_fitness_scan  # production scanner (Approach 2)
from app.scanner.backtest_scorer import score_via_backtest

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "backtest_results"

# ═══════════════════════════════════════════════════════════════════════
# Broad scan: self-contained universe fetcher + filter + scoring
# (None of this touches production code)
# ═══════════════════════════════════════════════════════════════════════

_NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
_EXCLUDE_SUFFIXES = {"W", "R", "U", "Z", "P"}
_ETFS = {
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "IVV", "VEA", "VWO",
    "EFA", "AGG", "BND", "GLD", "SLV", "TLT", "XLF", "XLK", "XLE",
    "XLV", "XLI", "XLC", "XLY", "XLP", "XLB", "XLU", "XLRE", "ARKK",
    "SOXL", "TQQQ", "SQQQ", "UVXY", "VXX", "HYG", "LQD", "EMB",
}


def _is_valid_common_stock(sym: str) -> bool:
    if not sym or len(sym) > 5:
        return False
    if any(c in sym for c in "-.$^+/"):
        return False
    if sym in _ETFS:
        return False
    if len(sym) >= 5 and sym[-1] in _EXCLUDE_SUFFIXES:
        return False
    return True


async def fetch_us_equity_universe() -> list[str]:
    """Fetch full US equity universe from NASDAQ FTP."""
    all_symbols: set[str] = set()
    async with httpx.AsyncClient(timeout=30) as client:
        for url, parser in [(_NASDAQ_URL, _parse_nasdaq), (_OTHER_URL, _parse_other)]:
            resp = await client.get(url)
            if resp.status_code == 200:
                parsed = parser(resp.text)
                all_symbols.update(parsed)
                print(f"  Parsed {len(parsed)} symbols from {url.split('/')[-1]}")
    if not all_symbols:
        raise RuntimeError("NASDAQ FTP returned empty")
    return sorted(all_symbols)


def _parse_nasdaq(text: str) -> list[str]:
    symbols = []
    for line in text.strip().splitlines():
        if line.startswith("Symbol|") or line.startswith("File Creation"):
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        sym = parts[0].strip()
        if parts[3].strip().upper() == "Y":
            continue
        if _is_valid_common_stock(sym):
            symbols.append(sym)
    return symbols


def _parse_other(text: str) -> list[str]:
    symbols = []
    for line in text.strip().splitlines():
        if line.startswith("ACT Symbol|") or line.startswith("File Creation"):
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        sym = parts[0].strip()
        if parts[4].strip().upper() == "Y":
            continue
        if len(parts) > 6 and parts[6].strip().upper() == "Y":
            continue
        if _is_valid_common_stock(sym):
            symbols.append(sym)
    return symbols


def _flatten_batch_columns(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Extract per-symbol DataFrames from a batch yf.download() MultiIndex result."""
    result: dict[str, pd.DataFrame] = {}
    if isinstance(df.columns, pd.MultiIndex):
        try:
            tickers = df.columns.get_level_values("Ticker").unique()
        except KeyError:
            tickers = df.columns.get_level_values(0).unique()
        for sym in tickers:
            try:
                sub = df.xs(sym, level="Ticker", axis=1).copy()
            except (KeyError, TypeError):
                try:
                    sub = df[sym].copy()
                except KeyError:
                    continue
            sub.columns = [c.lower() for c in sub.columns]
            sub = sub.dropna(subset=["close"])
            if len(sub) >= 20:
                result[sym] = sub
    else:
        df.columns = [c.lower() for c in df.columns]
        df = df.dropna(subset=["close"])
        if len(df) >= 20:
            result["SINGLE"] = df
    return result


async def broad_phase1_batch_filter(universe: list[str]) -> list[tuple[str, float, int, float, pd.Series]]:
    """Phase 1: batch yf.download + hard filters (price >= $10, vol >= 500K, ATR 2.5-12%)."""
    candidates = []
    chunk_size = 500
    end = datetime.now()
    start = end - timedelta(days=90)
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    for i in range(0, len(universe), chunk_size):
        chunk = universe[i:i + chunk_size]
        print(f"  Phase 1 batch {i//chunk_size + 1}/{(len(universe)-1)//chunk_size + 1}: "
              f"{len(chunk)} tickers...", end=" ", flush=True)
        try:
            raw = yf.download(
                " ".join(chunk), start=start_str, end=end_str,
                interval="1d", group_by="ticker", progress=False,
                auto_adjust=True, threads=True,
            )
            if raw.empty:
                print("empty")
                continue
            per_sym = _flatten_batch_columns(raw)
            batch_pass = 0
            for sym, df in per_sym.items():
                try:
                    price = float(df["close"].iloc[-1])
                    avg_volume = int(df["volume"].mean())
                    atr_series = calc_atr(df, period=14)
                    current_atr = float(atr_series.iloc[-1]) if not atr_series.empty and not np.isnan(atr_series.iloc[-1]) else 0
                    atr_pct = (current_atr / price * 100) if price > 0 else 0
                    if price < 10 or avg_volume < 500_000:
                        continue
                    if atr_pct < 2.5 or atr_pct > 12:
                        continue
                    daily_returns = df["close"].pct_change()
                    candidates.append((sym, price, avg_volume, atr_pct, daily_returns))
                    batch_pass += 1
                except Exception:
                    continue
            print(f"{batch_pass} passed")
        except Exception as e:
            print(f"error: {e}")
            continue

    candidates.sort(key=lambda c: c[3], reverse=True)
    print(f"  Phase 1 complete: {len(universe)} → {len(candidates)} survivors")
    return candidates


def broad_phase15_daily_score(
    candidates: list[tuple[str, float, int, float, pd.Series]],
    top_n: int = 40,
) -> list[tuple[str, float, int, float, pd.Series]]:
    """Phase 1.5: score survivors with daily metrics only, return top N.

    Scoring formula (same as production _score_daily_metrics_only):
    ATR 25 + Volatility 20 + Trend 20 + Volume 15 + Asymmetry 10 + Price 10 = 100
    """
    scored = []
    for cand in candidates:
        sym, price, avg_volume, atr_pct, daily_returns = cand
        score = _score_daily_metrics(price, avg_volume, atr_pct, daily_returns)
        scored.append((score, cand))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [item[1] for item in scored[:top_n]]
    print(f"  Phase 1.5: {len(candidates)} → {len(top)} "
          f"(top={scored[0][0]:.1f}, cutoff={scored[min(top_n-1, len(scored)-1)][0]:.1f})")
    return top


def _score_daily_metrics(price: float, avg_volume: float, atr_pct: float, daily_returns: pd.Series) -> float:
    """Daily-metrics-only scoring (self-contained, mirrors production logic)."""
    if daily_returns is None or len(daily_returns.dropna()) < 10:
        return 0.0

    # ATR Range (0-25)
    if atr_pct < 3.0:
        atr_s = (atr_pct / 3.0) * 12
    elif atr_pct <= 4.0:
        atr_s = 12 + (atr_pct - 3.0) * 4
    elif atr_pct <= 7.0:
        atr_s = 16 + (atr_pct - 4.0) / 3.0 * 9
    else:
        atr_s = 25.0
    atr_s = max(0, min(25, atr_s))

    # Daily volatility (0-20)
    daily_vol = daily_returns.std() * 100
    if daily_vol < 0.5:
        vol_s = (daily_vol / 0.5) * 10
    elif daily_vol <= 3.0:
        vol_s = 10 + (daily_vol - 0.5) / 2.5 * 10
    else:
        vol_s = 20.0
    vol_s = max(0, min(20, vol_s))

    # Trend persistence (0-20)
    signs = daily_returns.dropna().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    same_sign = sum(1 for i in range(1, len(signs)) if signs.iloc[i] == signs.iloc[i-1] and signs.iloc[i] != 0)
    total = sum(1 for i in range(1, len(signs)) if signs.iloc[i] != 0 and signs.iloc[i-1] != 0)
    if total >= 5:
        ratio = same_sign / total
        if ratio <= 0.40:
            trend_s = 0.0
        elif ratio >= 0.55:
            trend_s = 20.0
        else:
            trend_s = (ratio - 0.40) / 0.15 * 20
    else:
        trend_s = 0.0

    # Volume (0-15)
    if avg_volume < 5_000_000:
        liq_s = (avg_volume / 5_000_000) * 5
    elif avg_volume <= 50_000_000:
        liq_s = 5 + (avg_volume - 5_000_000) / 45_000_000 * 10
    else:
        liq_s = 15.0
    liq_s = max(0, min(15, liq_s))

    # Return asymmetry (0-10)
    returns_clean = daily_returns.dropna()
    up_ret = returns_clean[returns_clean > 0]
    down_ret = returns_clean[returns_clean < 0]
    if len(up_ret) > 0 and len(down_ret) > 0:
        ratio = up_ret.mean() / (abs(down_ret.mean()) + 1e-6)
        asym_s = min(10, 5 + (ratio - 1.0) * 5)
    else:
        asym_s = 5.0
    asym_s = max(0, min(10, asym_s))

    # Price (0-10)
    price_s = min(10, (price / 50) * 10) if price < 50 else 10.0

    return atr_s + vol_s + trend_s + liq_s + asym_s + price_s


async def run_broad_scan(top_n: int = 4) -> list[str]:
    """Full broad scan pipeline: universe → batch filter → daily score → backtest score → top N."""
    print("\n  Fetching US equity universe from NASDAQ...")
    universe = await fetch_us_equity_universe()
    print(f"  Universe: {len(universe)} symbols\n")

    # Phase 1: batch hard filter
    survivors = await broad_phase1_batch_filter(universe)

    # Phase 1.5: daily metrics scoring → top 40
    top40 = broad_phase15_daily_score(survivors, top_n=40)

    # Phase 2: real 7-day backtest scoring (uses production backtest_scorer)
    print(f"\n  Phase 2: Real backtest scoring {len(top40)} candidates (7d each)...")
    symbols_to_score = [c[0] for c in top40]
    bt_results = await score_via_backtest(symbols_to_score, days=7, strategy="rsi_divergence_pct")

    # Rank by P&L
    ranked = sorted(bt_results, key=lambda r: r[1], reverse=True)
    top_symbols = [r[0] for r in ranked[:top_n]]

    print(f"\n  Phase 2 results (top {top_n}):")
    for sym, pnl, wr, trades in ranked[:top_n]:
        print(f"    {sym:6s}: PnL {pnl:+.2f}%  WR {wr:.1f}%  trades={trades}")

    return top_symbols


# ═══════════════════════════════════════════════════════════════════════
# Shared: 29-day backtest via ReplayEngine (used by all arms)
# ═══════════════════════════════════════════════════════════════════════

async def run_backtest(symbols: list[str], days: int = 29) -> dict:
    """Run a 29-day backtest via ReplayEngine."""
    end = datetime.now()
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    start = end - timedelta(days=days)

    broker = ReplayBrokerAdapter(starting_balance=150_000)
    engine = ReplayEngine(broker)

    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
    try:
        await engine.start(
            symbols=symbols,
            timeframe="1m",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            speed=1.0,
            stop_loss_pct=0.02,
            position_size_pct=0.50,
            strategy="rsi_divergence_pct",
            headless=True,
        )
        if engine._task:
            await engine._task
    finally:
        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))

    r = engine.get_results()
    if not r:
        return {"symbols": symbols, "pnl_pct": 0, "pnl_dollar": 0,
                "win_rate": 0, "trades": 0, "dd_pct": 0, "winning": 0, "losing": 0}
    return {
        "symbols": symbols,
        "pnl_pct": r.total_pnl_pct,
        "pnl_dollar": r.total_pnl,
        "win_rate": r.win_rate,
        "trades": r.total_trades,
        "dd_pct": r.max_drawdown_pct,
        "winning": r.winning_trades,
        "losing": r.losing_trades,
    }


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

async def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 90)
    print("APPROACH 2 vs BROAD SCAN — 29-day backtests with v8 (rsi_divergence_pct)")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 90)

    approaches = {}

    # ── Step 1: Ticker selection ──
    print("\n--- Step 1: Scanning tickers ---")

    # Approach 2: production scanner (67-ticker universe, real backtest scoring)
    print("\n[1/3] Approach 2: Production scanner (67-ticker, real backtest scoring)...")
    scan_config = get_strategy("rsi_divergence_pct").scan_config
    a2_results = await run_fitness_scan(top_n=4, scan_config=scan_config, scoring_mode="real_backtest")
    tickers_a2 = [r.symbol for r in a2_results]
    approaches["approach2_67"] = tickers_a2
    print(f"  → {tickers_a2}")

    # Broad scan: standalone pipeline (~7000 tickers)
    print("\n[2/3] Broad scan: ~7000 US equities (standalone pipeline)...")
    tickers_broad = await run_broad_scan(top_n=4)
    approaches["broad_7000"] = tickers_broad
    print(f"  → {tickers_broad}")

    # Show new discoveries
    from app.scanner.runner import SCAN_UNIVERSE
    new_discoveries = [s for s in tickers_broad if s not in SCAN_UNIVERSE]
    overlap = [s for s in tickers_broad if s in SCAN_UNIVERSE]
    print(f"  Already in SCAN_UNIVERSE: {overlap}")
    print(f"  NEW discoveries: {new_discoveries}")

    # Baseline
    approaches["baseline"] = ["TSLA", "CEG", "PLTR", "RIVN"]
    print(f"\n[3/3] Baseline: hand-picked → {approaches['baseline']}")

    # ── Step 2: 29-day backtests ──
    print("\n--- Step 2: Running 29-day backtests ---\n")

    results = {}
    for label, tickers in sorted(approaches.items()):
        if not tickers:
            print(f"  {label}: NO TICKERS — skipping")
            continue
        print(f"  {label}: {tickers}...")
        results[label] = await run_backtest(tickers, days=29)
        r = results[label]
        print(f"    → {r['pnl_pct']:+.2f}% | {r['trades']} trades | "
              f"WR {r['win_rate']:.1f}% | DD {r['dd_pct']:.2f}%")

    # ── Step 3: Comparison table ──
    print("\n" + "=" * 90)
    print(f"{'Approach':<20s} | {'Tickers':<28s} | {'P&L%':>8s} | {'P&L$':>10s} | "
          f"{'Trades':>6s} | {'WR%':>6s} | {'DD%':>6s}")
    print("-" * 90)

    for label in sorted(results.keys()):
        r = results[label]
        tickers_str = ",".join(r["symbols"])[:28]
        print(
            f"{label:<20s} | {tickers_str:<28s} | {r['pnl_pct']:>+7.2f}% | "
            f"${r.get('pnl_dollar', 0):>+9,.0f} | {r['trades']:>6d} | "
            f"{r['win_rate']:>5.1f}% | {r['dd_pct']:>5.2f}%"
        )

    print("=" * 90)

    best = max(results.items(), key=lambda x: x[1]["pnl_pct"])
    print(f"\nWINNER: {best[0]} with {best[1]['pnl_pct']:+.2f}% P&L")

    # ── Save ──
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"approach2_vs_broad_{timestamp}.txt"
    with open(out_path, "w") as f:
        f.write(f"Approach 2 vs Broad Scan — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"Strategy: rsi_divergence_pct | Backtest: 29 days\n\n")
        for label in sorted(results.keys()):
            r = results[label]
            f.write(f"{label}: {','.join(r['symbols'])}\n")
            f.write(f"  P&L: {r['pnl_pct']:+.2f}% (${r.get('pnl_dollar',0):+,.0f})\n")
            f.write(f"  WR: {r['win_rate']:.1f}% | Trades: {r['trades']} | DD: {r['dd_pct']:.2f}%\n\n")
        f.write(f"New discoveries from broad: {new_discoveries}\n")
        f.write(f"Winner: {best[0]} ({best[1]['pnl_pct']:+.2f}%)\n")

    print(f"\nResults saved to {out_path}\n")


if __name__ == "__main__":
    asyncio.run(main())
