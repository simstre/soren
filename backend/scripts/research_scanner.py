#!/usr/bin/env python3
"""Research Scanner — Find winning tickers from first principles.

Analyzes what makes TSLA/CEG/PLTR/RIVN special, builds scoring models from
that analysis, and backtests until we match or beat the baseline.

Pipeline:
  NASDAQ FTP (~7000 symbols)
    → Hard filter (price≥$10, vol≥500K) → ~300-500 survivors
      → Compute 30 metrics per ticker (daily data only)
        → Statistical separation analysis vs 4 known winners
          → 5 scoring models → top 4 per model
            → 29-day v8 backtest per portfolio
              → Compare to baseline, iterate on best model

Usage:
    cd backend && .venv/bin/python -m scripts.research_scanner
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

os.environ["LOG_LEVEL"] = "ERROR"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import numpy as np
import pandas as pd
import yfinance as yf
import structlog

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
logger = structlog.get_logger()

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "backtest_results"

BASELINE_WINNERS = ["TSLA", "CEG", "PLTR", "RIVN"]


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: Data Collection — Universe fetch + batch download + hard filter
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


async def phase1_collect_data() -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Fetch universe, batch download 90d daily data, hard filter. Returns (ticker_dfs, spy_df)."""
    print("\n[Phase 1] Data Collection")
    print("  Fetching US equity universe from NASDAQ...")
    universe = await fetch_us_equity_universe()
    print(f"  Universe: {len(universe)} symbols")

    # Ensure baseline winners are included
    for sym in BASELINE_WINNERS:
        if sym not in universe:
            universe.append(sym)
    universe.sort()

    end = datetime.now()
    start = end - timedelta(days=90)
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    # Fetch SPY for beta/correlation
    print("  Fetching SPY for benchmark calcs...")
    spy_raw = yf.download("SPY", start=start_str, end=end_str, interval="1d",
                          progress=False, auto_adjust=True)
    spy_df = spy_raw.copy()
    if isinstance(spy_df.columns, pd.MultiIndex):
        spy_df.columns = spy_df.columns.droplevel("Ticker")
    spy_df.columns = [c.lower() for c in spy_df.columns]

    # Batch download all tickers
    all_dfs: dict[str, pd.DataFrame] = {}
    chunk_size = 500
    for i in range(0, len(universe), chunk_size):
        chunk = universe[i:i + chunk_size]
        batch_num = i // chunk_size + 1
        total_batches = (len(universe) - 1) // chunk_size + 1
        print(f"  Batch {batch_num}/{total_batches}: {len(chunk)} tickers...", end=" ", flush=True)
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
            passed = 0
            for sym, df in per_sym.items():
                try:
                    price = float(df["close"].iloc[-1])
                    avg_volume = int(df["volume"].mean())
                    # Hard filter: price >= $10, avg volume >= 500K
                    # But always keep baseline winners
                    if sym not in BASELINE_WINNERS:
                        if price < 10 or avg_volume < 500_000:
                            continue
                    all_dfs[sym] = df
                    passed += 1
                except Exception:
                    continue
            print(f"{passed} passed")
        except Exception as e:
            print(f"error: {e}")
            continue

    print(f"  Phase 1 complete: {len(universe)} → {len(all_dfs)} survivors")
    winners_found = [s for s in BASELINE_WINNERS if s in all_dfs]
    print(f"  Baseline winners in data: {winners_found}")
    return all_dfs, spy_df


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: Metric Computation — 30 metrics per ticker
# ═══════════════════════════════════════════════════════════════════════

def _safe_hurst(series: pd.Series, max_k: int = 20) -> float:
    """Simplified Hurst exponent via R/S analysis."""
    data = series.dropna().values
    n = len(data)
    if n < 40:
        return 0.5
    ks = range(10, min(max_k + 1, n // 2))
    rs_list = []
    for k in ks:
        rs_vals = []
        for start in range(0, n - k, k):
            chunk = data[start:start + k]
            mean_chunk = chunk.mean()
            dev = chunk - mean_chunk
            cumdev = np.cumsum(dev)
            r = cumdev.max() - cumdev.min()
            s = chunk.std(ddof=1)
            if s > 1e-10:
                rs_vals.append(r / s)
        if rs_vals:
            rs_list.append((np.log(k), np.log(np.mean(rs_vals))))
    if len(rs_list) < 3:
        return 0.5
    x = np.array([p[0] for p in rs_list])
    y = np.array([p[1] for p in rs_list])
    slope = np.polyfit(x, y, 1)[0]
    return float(np.clip(slope, 0.0, 1.0))


def compute_metrics(sym: str, df: pd.DataFrame, spy_df: pd.DataFrame) -> dict[str, float] | None:
    """Compute 30 metrics for a single ticker. Returns None if data insufficient."""
    try:
        if len(df) < 30:
            return None

        close = df["close"]
        high = df["high"]
        low = df["low"]
        opn = df["open"]
        volume = df["volume"]
        returns = close.pct_change().dropna()

        if len(returns) < 20:
            return None

        price = float(close.iloc[-1])
        metrics: dict[str, float] = {}

        # ── Volatility (7) ──
        # ATR percent (14-period)
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr_14 = tr.rolling(14).mean()
        metrics["atr_pct"] = float(atr_14.iloc[-1] / price * 100) if not np.isnan(atr_14.iloc[-1]) else 0.0

        # Average daily range %
        daily_range = (high - low) / close * 100
        metrics["avg_daily_range_pct"] = float(daily_range.mean())

        # Return std %
        metrics["return_std_pct"] = float(returns.std() * 100)

        # % days with range > 2%
        range_pct = (high - low) / opn * 100
        metrics["pct_days_range_gt_2pct"] = float((range_pct > 2.0).mean() * 100)

        # Max drawdown from open %
        dd_from_open = (low - opn) / opn * 100
        metrics["max_drawdown_from_open_pct"] = float(dd_from_open.min())

        # Range expansion ratio (5d ATR / 20d ATR)
        atr_5 = tr.rolling(5).mean()
        atr_20 = tr.rolling(20).mean()
        if not np.isnan(atr_5.iloc[-1]) and not np.isnan(atr_20.iloc[-1]) and atr_20.iloc[-1] > 0:
            metrics["range_expansion_ratio"] = float(atr_5.iloc[-1] / atr_20.iloc[-1])
        else:
            metrics["range_expansion_ratio"] = 1.0

        # Idiosyncratic volatility (residual vol after removing SPY)
        spy_ret = spy_df["close"].pct_change().dropna()
        common_idx = returns.index.intersection(spy_ret.index)
        if len(common_idx) >= 20:
            r_stock = returns.loc[common_idx].values
            r_spy = spy_ret.loc[common_idx].values
            if len(r_stock) >= 20 and np.std(r_spy) > 1e-10:
                beta = np.cov(r_stock, r_spy)[0, 1] / np.var(r_spy)
                residual = r_stock - beta * r_spy
                metrics["idiosyncratic_vol"] = float(np.std(residual) * 100)
            else:
                metrics["idiosyncratic_vol"] = float(returns.std() * 100)
        else:
            metrics["idiosyncratic_vol"] = float(returns.std() * 100)

        # ── Price Action (7) ──
        # Intraday range captured (how much of the bar is body vs wick)
        body = (close - opn).abs()
        full_range = high - low
        safe_range = full_range.replace(0, np.nan)
        captured = (body / safe_range).dropna()
        metrics["intraday_range_captured_pct"] = float(captured.mean() * 100)

        # Close near high ratio
        safe_hl = (high - low).replace(0, np.nan)
        close_pos = (close - low) / safe_hl
        metrics["close_near_high_ratio"] = float(close_pos.dropna().mean())

        # Close near low ratio
        metrics["close_near_low_ratio"] = float(1.0 - close_pos.dropna().mean())

        # Average gap %
        gaps = (opn - close.shift(1)) / close.shift(1) * 100
        metrics["avg_gap_pct"] = float(gaps.dropna().abs().mean())

        # Overnight vs intraday ratio
        overnight_move = (opn - close.shift(1)).abs()
        intraday_move = (close - opn).abs()
        safe_intraday = intraday_move.replace(0, np.nan)
        ov_ratio = overnight_move / safe_intraday
        metrics["overnight_vs_intraday_ratio"] = float(ov_ratio.dropna().median())

        # Average absolute daily return %
        metrics["avg_abs_daily_return_pct"] = float(returns.abs().mean() * 100)

        # Average intraday move % (open to close)
        intraday_pct = ((close - opn) / opn * 100).abs()
        metrics["avg_intraday_move_pct"] = float(intraday_pct.mean())

        # ── Momentum/Trend (6) ──
        # 20-day momentum
        if len(close) >= 21:
            metrics["mom_20d"] = float((close.iloc[-1] / close.iloc[-21] - 1) * 100)
        else:
            metrics["mom_20d"] = 0.0

        # 50-day momentum
        if len(close) >= 51:
            metrics["mom_50d"] = float((close.iloc[-1] / close.iloc[-51] - 1) * 100)
        else:
            metrics["mom_50d"] = 0.0

        # Trend persistence (same-sign consecutive returns ratio)
        signs = returns.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        same_sign = sum(1 for i in range(1, len(signs))
                        if signs.iloc[i] == signs.iloc[i-1] and signs.iloc[i] != 0)
        total_pairs = sum(1 for i in range(1, len(signs))
                          if signs.iloc[i] != 0 and signs.iloc[i-1] != 0)
        metrics["trend_persistence"] = float(same_sign / total_pairs) if total_pairs >= 5 else 0.5

        # Trend strength (absolute sum of returns / sum of absolute returns)
        abs_sum = returns.abs().sum()
        metrics["trend_strength"] = float(abs(returns.sum()) / abs_sum) if abs_sum > 0 else 0.0

        # Hurst exponent
        metrics["hurst_exponent"] = _safe_hurst(returns)

        # Absolute 20d momentum (direction-agnostic momentum magnitude)
        metrics["abs_momentum_20d"] = abs(metrics["mom_20d"])

        # ── Volume (3) ──
        # Volume coefficient of variation
        vol_mean = volume.mean()
        vol_std = volume.std()
        metrics["volume_cv"] = float(vol_std / vol_mean) if vol_mean > 0 else 0.0

        # Volume-price correlation
        if len(volume) >= 20:
            metrics["volume_price_correlation"] = float(
                np.corrcoef(volume.values[-60:], close.values[-60:])[0, 1]
            ) if len(volume) >= 60 else float(
                np.corrcoef(volume.values, close.values)[0, 1]
            )
            if np.isnan(metrics["volume_price_correlation"]):
                metrics["volume_price_correlation"] = 0.0
        else:
            metrics["volume_price_correlation"] = 0.0

        # Dollar volume
        metrics["dollar_volume"] = float((close * volume).mean())

        # ── Market (4) ──
        # Beta to SPY
        if len(common_idx) >= 20:
            r_stock = returns.loc[common_idx].values
            r_spy = spy_ret.loc[common_idx].values
            if np.var(r_spy) > 1e-10:
                metrics["beta_spy"] = float(np.cov(r_stock, r_spy)[0, 1] / np.var(r_spy))
            else:
                metrics["beta_spy"] = 1.0
        else:
            metrics["beta_spy"] = 1.0

        # Return asymmetry (mean up / mean down)
        up_ret = returns[returns > 0]
        down_ret = returns[returns < 0]
        if len(up_ret) > 0 and len(down_ret) > 0:
            metrics["return_asymmetry"] = float(up_ret.mean() / (abs(down_ret.mean()) + 1e-10))
        else:
            metrics["return_asymmetry"] = 1.0

        # Up day ratio
        metrics["up_day_ratio"] = float((returns > 0).mean())

        # Return autocorrelation lag 1
        if len(returns) >= 20:
            metrics["return_autocorr_lag1"] = float(returns.autocorr(lag=1))
            if np.isnan(metrics["return_autocorr_lag1"]):
                metrics["return_autocorr_lag1"] = 0.0
        else:
            metrics["return_autocorr_lag1"] = 0.0

        # ── Structure (3) ──
        # Max consecutive up days
        signs_binary = (returns > 0).astype(int)
        max_consec_up = 0
        curr = 0
        for v in signs_binary:
            if v == 1:
                curr += 1
                max_consec_up = max(max_consec_up, curr)
            else:
                curr = 0
        metrics["max_consec_up"] = float(max_consec_up)

        # Max consecutive down days
        signs_down = (returns < 0).astype(int)
        max_consec_down = 0
        curr = 0
        for v in signs_down:
            if v == 1:
                curr += 1
                max_consec_down = max(max_consec_down, curr)
            else:
                curr = 0
        metrics["max_consec_down"] = float(max_consec_down)

        # Directional consistency (max of up_ratio, 1-up_ratio)
        metrics["directional_consistency"] = float(max(metrics["up_day_ratio"],
                                                       1 - metrics["up_day_ratio"]))

        return metrics

    except Exception as e:
        return None


def phase2_compute_all_metrics(
    all_dfs: dict[str, pd.DataFrame], spy_df: pd.DataFrame
) -> pd.DataFrame:
    """Compute 30 metrics for every ticker. Returns DataFrame indexed by symbol."""
    print(f"\n[Phase 2] Computing 30 metrics for {len(all_dfs)} tickers...")
    rows = {}
    failed = 0
    for sym, df in all_dfs.items():
        m = compute_metrics(sym, df, spy_df)
        if m is not None:
            rows[sym] = m
        else:
            failed += 1

    metrics_df = pd.DataFrame.from_dict(rows, orient="index")
    print(f"  Computed metrics for {len(metrics_df)} tickers ({failed} failed)")
    print(f"  Metrics: {list(metrics_df.columns)}")
    return metrics_df


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: Statistical Separation — Cohen's d analysis
# ═══════════════════════════════════════════════════════════════════════

def phase3_separation_analysis(metrics_df: pd.DataFrame) -> pd.DataFrame:
    """Compute Cohen's d for each metric (winners vs rest). Returns ranked DataFrame."""
    print(f"\n[Phase 3] Statistical Separation Analysis")

    winner_mask = metrics_df.index.isin(BASELINE_WINNERS)
    winners = metrics_df[winner_mask]
    rest = metrics_df[~winner_mask]

    print(f"  Winners: {list(winners.index)} ({len(winners)})")
    print(f"  Rest: {len(rest)} tickers")

    results = []
    for col in metrics_df.columns:
        w_vals = winners[col].dropna()
        r_vals = rest[col].dropna()
        if len(w_vals) < 2 or len(r_vals) < 10:
            continue

        w_mean = w_vals.mean()
        r_mean = r_vals.mean()
        pooled_std = np.sqrt(
            ((len(w_vals) - 1) * w_vals.std()**2 + (len(r_vals) - 1) * r_vals.std()**2)
            / (len(w_vals) + len(r_vals) - 2)
        )
        cohens_d = (w_mean - r_mean) / pooled_std if pooled_std > 1e-10 else 0.0

        # Winner percentile in the full distribution
        all_vals = metrics_df[col].dropna()
        w_percentiles = [(all_vals < v).mean() * 100 for v in w_vals]
        avg_w_pctile = np.mean(w_percentiles)

        results.append({
            "metric": col,
            "cohens_d": cohens_d,
            "abs_d": abs(cohens_d),
            "winner_mean": w_mean,
            "rest_mean": r_mean,
            "winner_pctile": avg_w_pctile,
            "direction": "higher" if cohens_d > 0 else "lower",
        })

    sep_df = pd.DataFrame(results).sort_values("abs_d", ascending=False).reset_index(drop=True)

    print(f"\n  Top 15 separating metrics (by |Cohen's d|):")
    print(f"  {'Rank':<5} {'Metric':<30} {'Cohen d':>9} {'Dir':<7} {'Win Mean':>10} {'Rest Mean':>10} {'Win Pctile':>11}")
    print(f"  {'-'*85}")
    for i, row in sep_df.head(15).iterrows():
        print(f"  {i+1:<5} {row['metric']:<30} {row['cohens_d']:>+9.3f} {row['direction']:<7} "
              f"{row['winner_mean']:>10.3f} {row['rest_mean']:>10.3f} {row['winner_pctile']:>10.1f}%")

    return sep_df


# ═══════════════════════════════════════════════════════════════════════
# Phase 4: Five Scoring Models
# ═══════════════════════════════════════════════════════════════════════

def _get_top_metrics(sep_df: pd.DataFrame, n: int) -> list[str]:
    """Return top N metrics by absolute Cohen's d."""
    return sep_df.head(n)["metric"].tolist()


def model1_percentile_rank(metrics_df: pd.DataFrame, sep_df: pd.DataFrame, top_n: int = 4) -> list[str]:
    """Sum percentile ranks of top 5 separating metrics."""
    top5 = _get_top_metrics(sep_df, 5)
    directions = {row["metric"]: row["direction"] for _, row in sep_df.iterrows()}

    scores = {}
    for sym in metrics_df.index:
        if sym in BASELINE_WINNERS:
            continue
        total = 0.0
        for m in top5:
            col = metrics_df[m]
            pctile = (col < metrics_df.loc[sym, m]).mean()
            # If winners are lower on this metric, invert
            if directions.get(m) == "lower":
                pctile = 1.0 - pctile
            total += pctile
        scores[sym] = total

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [s[0] for s in ranked[:top_n]]


def model2_centroid_distance(metrics_df: pd.DataFrame, sep_df: pd.DataFrame, top_n: int = 4) -> list[str]:
    """Z-score distance to winner centroid on top 10 metrics."""
    top10 = _get_top_metrics(sep_df, 10)

    # Z-score normalize
    subset = metrics_df[top10].copy()
    means = subset.mean()
    stds = subset.std().replace(0, 1)
    z_scores = (subset - means) / stds

    # Winner centroid
    winner_mask = z_scores.index.isin(BASELINE_WINNERS)
    centroid = z_scores[winner_mask].mean()

    # Distance from each non-winner to centroid
    distances = {}
    for sym in z_scores.index:
        if sym in BASELINE_WINNERS:
            continue
        dist = np.sqrt(((z_scores.loc[sym] - centroid) ** 2).sum())
        distances[sym] = dist

    ranked = sorted(distances.items(), key=lambda x: x[1])  # smallest distance = most similar
    return [s[0] for s in ranked[:top_n]]


def model3_hard_gates(metrics_df: pd.DataFrame, sep_df: pd.DataFrame, top_n: int = 4) -> list[str]:
    """Threshold gates on top 5 metrics (winner_min * 0.9 or winner_max * 1.1)."""
    top5 = _get_top_metrics(sep_df, 5)
    directions = {row["metric"]: row["direction"] for _, row in sep_df.iterrows()}

    winner_mask = metrics_df.index.isin(BASELINE_WINNERS)
    winners = metrics_df[winner_mask]

    gates = {}
    for m in top5:
        d = directions.get(m, "higher")
        if d == "higher":
            gates[m] = (">=", float(winners[m].min() * 0.9))
        else:
            gates[m] = ("<=", float(winners[m].max() * 1.1))

    # Find tickers passing all gates
    passers = []
    for sym in metrics_df.index:
        if sym in BASELINE_WINNERS:
            continue
        passed = True
        for m, (op, threshold) in gates.items():
            val = metrics_df.loc[sym, m]
            if op == ">=" and val < threshold:
                passed = False
                break
            elif op == "<=" and val > threshold:
                passed = False
                break
        if passed:
            passers.append(sym)

    # If not enough passers, relax to 3 gates
    if len(passers) < top_n:
        top3 = _get_top_metrics(sep_df, 3)
        gates3 = {m: v for m, v in gates.items() if m in top3}
        passers = []
        for sym in metrics_df.index:
            if sym in BASELINE_WINNERS:
                continue
            passed = True
            for m, (op, threshold) in gates3.items():
                val = metrics_df.loc[sym, m]
                if op == ">=" and val < threshold:
                    passed = False
                    break
                elif op == "<=" and val > threshold:
                    passed = False
                    break
            if passed:
                passers.append(sym)

    # Rank passers by centroid distance
    if len(passers) > top_n:
        top10 = _get_top_metrics(sep_df, 10)
        subset = metrics_df.loc[passers, top10]
        means = metrics_df[top10].mean()
        stds = metrics_df[top10].std().replace(0, 1)
        z = (subset - means) / stds
        winner_z = ((metrics_df.loc[metrics_df.index.isin(BASELINE_WINNERS), top10] - means) / stds).mean()
        dists = {sym: float(np.sqrt(((z.loc[sym] - winner_z) ** 2).sum())) for sym in passers}
        passers = [s for s, _ in sorted(dists.items(), key=lambda x: x[1])]

    return passers[:top_n]


def model4_weighted_linear(metrics_df: pd.DataFrame, sep_df: pd.DataFrame, top_n: int = 4) -> list[str]:
    """Cohen's d as weights × z-scores."""
    all_metrics = sep_df["metric"].tolist()
    weights = {row["metric"]: row["cohens_d"] for _, row in sep_df.iterrows()}

    # Z-score normalize
    subset = metrics_df[all_metrics].copy()
    means = subset.mean()
    stds = subset.std().replace(0, 1)
    z_scores = (subset - means) / stds

    scores = {}
    for sym in z_scores.index:
        if sym in BASELINE_WINNERS:
            continue
        total = sum(z_scores.loc[sym, m] * weights[m] for m in all_metrics)
        scores[sym] = total

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [s[0] for s in ranked[:top_n]]


def model5_hybrid(metrics_df: pd.DataFrame, sep_df: pd.DataFrame, top_n: int = 4) -> list[str]:
    """Hard gates on top 3 + soft score on next 7."""
    top3 = _get_top_metrics(sep_df, 3)
    next7 = _get_top_metrics(sep_df, 10)[3:]
    directions = {row["metric"]: row["direction"] for _, row in sep_df.iterrows()}
    weights = {row["metric"]: row["cohens_d"] for _, row in sep_df.iterrows()}

    winner_mask = metrics_df.index.isin(BASELINE_WINNERS)
    winners = metrics_df[winner_mask]

    # Hard gates on top 3
    gates = {}
    for m in top3:
        d = directions.get(m, "higher")
        if d == "higher":
            gates[m] = (">=", float(winners[m].min() * 0.85))
        else:
            gates[m] = ("<=", float(winners[m].max() * 1.15))

    # Find passers
    passers = []
    for sym in metrics_df.index:
        if sym in BASELINE_WINNERS:
            continue
        passed = True
        for m, (op, threshold) in gates.items():
            val = metrics_df.loc[sym, m]
            if op == ">=" and val < threshold:
                passed = False
                break
            elif op == "<=" and val > threshold:
                passed = False
                break
        if passed:
            passers.append(sym)

    if not passers:
        # Fall back to top centroid distance
        return model2_centroid_distance(metrics_df, sep_df, top_n)

    # Soft score on next 7
    subset = metrics_df.loc[passers, next7]
    means = metrics_df[next7].mean()
    stds = metrics_df[next7].std().replace(0, 1)
    z_scores = (subset - means) / stds

    scores = {}
    for sym in passers:
        total = sum(z_scores.loc[sym, m] * weights.get(m, 0) for m in next7)
        scores[sym] = total

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [s[0] for s in ranked[:top_n]]


def phase4_run_models(metrics_df: pd.DataFrame, sep_df: pd.DataFrame) -> dict[str, list[str]]:
    """Run all 5 scoring models. Returns {model_name: [top 4 tickers]}."""
    print(f"\n[Phase 4] Running 5 Scoring Models")

    models = {
        "M1_Percentile": model1_percentile_rank,
        "M2_Centroid": model2_centroid_distance,
        "M3_HardGates": model3_hard_gates,
        "M4_WeightedLinear": model4_weighted_linear,
        "M5_Hybrid": model5_hybrid,
    }

    selections = {}
    for name, func in models.items():
        picks = func(metrics_df, sep_df, top_n=4)
        selections[name] = picks
        print(f"  {name:<20}: {picks}")

    # Check overlap between models
    all_picks = [s for picks in selections.values() for s in picks]
    counts = Counter(all_picks)
    consensus = [(s, c) for s, c in counts.most_common() if c >= 2]
    if consensus:
        print(f"\n  Consensus picks (≥2 models): {[(s, f'{c}/5') for s, c in consensus]}")

    return selections


# ═══════════════════════════════════════════════════════════════════════
# Phase 5: Backtest — 29-day v8 backtest
# ═══════════════════════════════════════════════════════════════════════

async def run_backtest(symbols: list[str], days: int = 29, label: str = "") -> dict:
    """Run a 29-day v8 backtest. Returns result dict."""
    end = datetime.now()
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    start = end - timedelta(days=days)

    broker = ReplayBrokerAdapter(starting_balance=150_000)
    engine = ReplayEngine(broker)

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
    except Exception as e:
        print(f"    ERROR in backtest {label}: {e}")
        return {"symbols": symbols, "label": label, "pnl_pct": 0, "pnl_dollar": 0,
                "win_rate": 0, "trades": 0, "dd_pct": 0, "winning": 0, "losing": 0}

    r = engine.get_results()
    if not r:
        return {"symbols": symbols, "label": label, "pnl_pct": 0, "pnl_dollar": 0,
                "win_rate": 0, "trades": 0, "dd_pct": 0, "winning": 0, "losing": 0}
    return {
        "symbols": symbols,
        "label": label,
        "pnl_pct": r.total_pnl_pct,
        "pnl_dollar": r.total_pnl,
        "win_rate": r.win_rate,
        "trades": r.total_trades,
        "dd_pct": r.max_drawdown_pct,
        "winning": r.winning_trades,
        "losing": r.losing_trades,
    }


async def phase5_backtest_models(selections: dict[str, list[str]]) -> dict[str, dict]:
    """Backtest each model's picks + baseline."""
    print(f"\n[Phase 5] Running 29-day Backtests")

    all_to_test = dict(selections)
    all_to_test["Baseline"] = BASELINE_WINNERS

    results = {}
    for label, syms in all_to_test.items():
        if not syms:
            print(f"  {label}: NO TICKERS — skipping")
            continue
        print(f"  {label}: {syms}...", end=" ", flush=True)
        t0 = time.time()
        r = await run_backtest(syms, days=29, label=label)
        elapsed = time.time() - t0
        results[label] = r
        print(f"→ {r['pnl_pct']:+.2f}% | {r['trades']} trades | "
              f"WR {r['win_rate']:.1f}% | DD {r['dd_pct']:.2f}% | {elapsed:.0f}s")

    return results


# ═══════════════════════════════════════════════════════════════════════
# Phase 6: Iterate on Best Model
# ═══════════════════════════════════════════════════════════════════════

async def phase6_iterate(
    metrics_df: pd.DataFrame, sep_df: pd.DataFrame,
    selections: dict[str, list[str]], bt_results: dict[str, dict],
) -> dict[str, dict]:
    """Take best model and run 3 variations."""
    print(f"\n[Phase 6] Iterating on Best Model")

    # Find best non-baseline model
    model_results = {k: v for k, v in bt_results.items() if k != "Baseline"}
    if not model_results:
        print("  No model results to iterate on")
        return {}

    best_label = max(model_results, key=lambda k: model_results[k]["pnl_pct"])
    best_pnl = model_results[best_label]["pnl_pct"]
    print(f"  Best model: {best_label} ({best_pnl:+.2f}%)")

    # Also find second best for blending
    sorted_models = sorted(model_results.items(), key=lambda x: x[1]["pnl_pct"], reverse=True)
    second_label = sorted_models[1][0] if len(sorted_models) > 1 else best_label

    # Map model names to functions
    model_funcs = {
        "M1_Percentile": model1_percentile_rank,
        "M2_Centroid": model2_centroid_distance,
        "M3_HardGates": model3_hard_gates,
        "M4_WeightedLinear": model4_weighted_linear,
        "M5_Hybrid": model5_hybrid,
    }

    iter_results = {}

    # Variation A: Top 6 instead of 4
    best_func = model_funcs.get(best_label)
    if best_func:
        top6 = best_func(metrics_df, sep_df, top_n=6)
    else:
        top6 = selections[best_label][:6] if best_label in selections else BASELINE_WINNERS
    label_a = f"{best_label}_top6"
    print(f"  Var A ({label_a}): {top6}...", end=" ", flush=True)
    t0 = time.time()
    r = await run_backtest(top6, days=29, label=label_a)
    elapsed = time.time() - t0
    iter_results[label_a] = r
    print(f"→ {r['pnl_pct']:+.2f}% | {r['trades']} trades | {elapsed:.0f}s")

    # Variation B: Blend top-2 models (tickers appearing in both top-6 lists)
    if best_func:
        best_top6 = best_func(metrics_df, sep_df, top_n=6)
    else:
        best_top6 = selections[best_label][:6]
    second_func = model_funcs.get(second_label)
    if second_func:
        second_top6 = second_func(metrics_df, sep_df, top_n=6)
    else:
        second_top6 = selections.get(second_label, BASELINE_WINNERS)[:6]

    overlap = [s for s in best_top6 if s in second_top6]
    if len(overlap) < 4:
        # If not enough overlap, take all overlap + fill from best model
        remaining = [s for s in best_top6 if s not in overlap]
        overlap = overlap + remaining[:4 - len(overlap)]
    blend = overlap[:4]
    label_b = f"Blend_{best_label[:2]}+{second_label[:2]}"
    print(f"  Var B ({label_b}): {blend}...", end=" ", flush=True)
    t0 = time.time()
    r = await run_backtest(blend, days=29, label=label_b)
    elapsed = time.time() - t0
    iter_results[label_b] = r
    print(f"→ {r['pnl_pct']:+.2f}% | {r['trades']} trades | {elapsed:.0f}s")

    # Variation C: Re-weight metrics using backtest P&L correlation
    # Use all model results to correlate metrics with P&L
    ticker_pnl = {}
    for label, res in bt_results.items():
        if label == "Baseline":
            continue
        pnl = res["pnl_pct"]
        for sym in res["symbols"]:
            if sym not in ticker_pnl:
                ticker_pnl[sym] = []
            ticker_pnl[sym].append(pnl)
    # Average P&L per ticker across models
    avg_pnl = {s: np.mean(v) for s, v in ticker_pnl.items()}

    if len(avg_pnl) >= 6:
        # Correlate each metric with backtest P&L
        pnl_series = pd.Series(avg_pnl)
        common = metrics_df.index.intersection(pnl_series.index)
        if len(common) >= 6:
            corr_weights = {}
            for col in metrics_df.columns:
                vals = metrics_df.loc[common, col]
                corr = np.corrcoef(vals.values, pnl_series.loc[common].values)[0, 1]
                if not np.isnan(corr):
                    corr_weights[col] = corr

            # Score all non-winner tickers using correlation weights
            all_metrics = list(corr_weights.keys())
            subset = metrics_df[all_metrics]
            means = subset.mean()
            stds = subset.std().replace(0, 1)
            z_scores = (subset - means) / stds

            scores = {}
            for sym in z_scores.index:
                if sym in BASELINE_WINNERS:
                    continue
                total = sum(z_scores.loc[sym, m] * corr_weights[m] for m in all_metrics)
                scores[sym] = total

            reweighted = [s for s, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:4]]
        else:
            reweighted = selections[best_label][:4]
    else:
        reweighted = selections[best_label][:4]

    label_c = f"{best_label}_PnLReweight"
    print(f"  Var C ({label_c}): {reweighted}...", end=" ", flush=True)
    t0 = time.time()
    r = await run_backtest(reweighted, days=29, label=label_c)
    elapsed = time.time() - t0
    iter_results[label_c] = r
    print(f"→ {r['pnl_pct']:+.2f}% | {r['trades']} trades | {elapsed:.0f}s")

    return iter_results


# ═══════════════════════════════════════════════════════════════════════
# Phase 7: Save Results
# ═══════════════════════════════════════════════════════════════════════

def phase7_save_results(
    sep_df: pd.DataFrame,
    selections: dict[str, list[str]],
    bt_results: dict[str, dict],
    iter_results: dict[str, dict],
    metrics_df: pd.DataFrame,
):
    """Save full results to markdown file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"research_scanner_{timestamp}.md"

    all_results = {**bt_results, **iter_results}
    baseline_pnl = all_results.get("Baseline", {}).get("pnl_pct", 0)

    lines = [
        f"# Research Scanner Results — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"Strategy: rsi_divergence_pct | Backtest: 29 days | Balance: $150K | Position: 50%",
        f"Baseline: {BASELINE_WINNERS} → {baseline_pnl:+.2f}%",
        "",
        "---",
        "",
        "## Metric Separation Rankings (Cohen's d)",
        "",
        f"| Rank | Metric | Cohen's d | Direction | Winner Mean | Rest Mean | Winner Pctile |",
        f"|------|--------|-----------|-----------|-------------|-----------|---------------|",
    ]
    for i, row in sep_df.head(20).iterrows():
        lines.append(
            f"| {i+1} | {row['metric']} | {row['cohens_d']:+.3f} | {row['direction']} | "
            f"{row['winner_mean']:.3f} | {row['rest_mean']:.3f} | {row['winner_pctile']:.1f}% |"
        )

    lines += [
        "",
        "## Model Selections",
        "",
    ]
    for name, picks in selections.items():
        lines.append(f"- **{name}**: {picks}")

    lines += [
        "",
        "## Backtest Results",
        "",
        f"| Model | Tickers | P&L% | P&L$ | Trades | WR% | DD% | vs Baseline |",
        f"|-------|---------|------|------|--------|-----|-----|-------------|",
    ]
    for label in sorted(all_results.keys()):
        r = all_results[label]
        syms = ",".join(r["symbols"])[:28]
        delta = r["pnl_pct"] - baseline_pnl if label != "Baseline" else 0
        delta_str = f"{delta:+.2f}pp" if label != "Baseline" else "—"
        lines.append(
            f"| {label} | {syms} | {r['pnl_pct']:+.2f}% | ${r['pnl_dollar']:+,.0f} | "
            f"{r['trades']} | {r['win_rate']:.1f}% | {r['dd_pct']:.2f}% | {delta_str} |"
        )

    # Best result
    best_label = max(all_results, key=lambda k: all_results[k]["pnl_pct"])
    best = all_results[best_label]
    lines += [
        "",
        "## Conclusions",
        "",
        f"- **Best overall**: {best_label} ({best['pnl_pct']:+.2f}%) — {best['symbols']}",
        f"- **Baseline**: {baseline_pnl:+.2f}%",
        f"- **Best vs baseline**: {best['pnl_pct'] - baseline_pnl:+.2f}pp",
    ]

    # Best model (non-baseline, non-iteration)
    model_only = {k: v for k, v in bt_results.items() if k != "Baseline"}
    if model_only:
        best_model_label = max(model_only, key=lambda k: model_only[k]["pnl_pct"])
        best_model = model_only[best_model_label]
        lines.append(f"- **Best model (pre-iteration)**: {best_model_label} ({best_model['pnl_pct']:+.2f}%)")

    # Winner metric profile
    lines += [
        "",
        "## Winner Profile (top 10 metrics)",
        "",
    ]
    winner_mask = metrics_df.index.isin(BASELINE_WINNERS)
    for _, row in sep_df.head(10).iterrows():
        m = row["metric"]
        w_vals = metrics_df.loc[winner_mask, m]
        lines.append(f"- **{m}**: winners={w_vals.mean():.3f} (range {w_vals.min():.3f}—{w_vals.max():.3f}), "
                      f"universe={metrics_df[m].mean():.3f}")

    content = "\n".join(lines) + "\n"
    with open(out_path, "w") as f:
        f.write(content)

    print(f"\n  Results saved to {out_path}")
    return out_path


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

async def main():
    t_start = time.time()
    print("=" * 90)
    print("RESEARCH SCANNER — First-Principles Ticker Discovery")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Baseline winners: {BASELINE_WINNERS}")
    print("=" * 90)

    # Phase 1: Data Collection
    all_dfs, spy_df = await phase1_collect_data()

    # Phase 2: Metric Computation
    metrics_df = phase2_compute_all_metrics(all_dfs, spy_df)

    # Phase 3: Statistical Separation
    sep_df = phase3_separation_analysis(metrics_df)

    # Phase 4: Scoring Models
    selections = phase4_run_models(metrics_df, sep_df)

    # Phase 5: Backtest All Models + Baseline
    bt_results = await phase5_backtest_models(selections)

    # Phase 6: Iterate on Best Model
    iter_results = await phase6_iterate(metrics_df, sep_df, selections, bt_results)

    # Phase 7: Save Results
    all_results = {**bt_results, **iter_results}
    out_path = phase7_save_results(sep_df, selections, bt_results, iter_results, metrics_df)

    # Final summary
    baseline_pnl = all_results.get("Baseline", {}).get("pnl_pct", 0)
    best_label = max(all_results, key=lambda k: all_results[k]["pnl_pct"])
    best = all_results[best_label]

    elapsed = time.time() - t_start
    print(f"\n{'=' * 90}")
    print(f"FINAL RESULTS")
    print(f"{'=' * 90}")
    print(f"  Baseline:  {baseline_pnl:+.2f}%  ({BASELINE_WINNERS})")
    print(f"  Best:      {best['pnl_pct']:+.2f}%  ({best['symbols']}) [{best_label}]")
    print(f"  Delta:     {best['pnl_pct'] - baseline_pnl:+.2f}pp")
    print(f"  Runtime:   {elapsed/60:.1f} min")
    print(f"  Results:   {out_path}")
    print(f"{'=' * 90}")


if __name__ == "__main__":
    asyncio.run(main())
