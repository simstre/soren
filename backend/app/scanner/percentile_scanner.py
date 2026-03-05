"""Percentile-based ticker scanner — M1_Percentile model from research scanner.

Scans the full NASDAQ+NYSE universe (~7000 symbols), computes 30 daily metrics,
ranks by percentile similarity to known winners (TSLA/CEG/PLTR/RIVN), and returns
top N tickers as FitnessScore objects for API compatibility.

This is the production version of the M1_Percentile model from research_scanner.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
import structlog

from app.scanner.fitness import FitnessScore
from app.scanner.universe import fetch_us_equity_universe, batch_download_daily, flatten_batch_columns
from app.engine.strategies import ScanConfig

logger = structlog.get_logger()

# ── Known winner profile (from separation analysis 2026-02-26) ──
# Top 5 metrics by |Cohen's d| and their directions for winners.
WINNER_PROFILE: list[tuple[str, str]] = [
    ("dollar_volume", "higher"),
    ("max_consec_down", "higher"),
    ("up_day_ratio", "lower"),
    ("close_near_low_ratio", "higher"),
    ("close_near_high_ratio", "lower"),
]


async def _collect_data() -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Fetch universe, batch download 90d daily data, hard filter."""
    universe = await fetch_us_equity_universe()

    end = datetime.now() + timedelta(days=1)
    start = end - timedelta(days=91)
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    # Fetch SPY for metric computation
    spy_raw = yf.download("SPY", start=start_str, end=end_str, interval="1d",
                          progress=False, auto_adjust=True)
    spy_df = spy_raw.copy()
    if isinstance(spy_df.columns, pd.MultiIndex):
        spy_df.columns = spy_df.columns.droplevel("Ticker")
    spy_df.columns = [c.lower() for c in spy_df.columns]

    # Batch download all tickers with hard filter
    all_dfs = await batch_download_daily(universe, days=90)

    logger.info("percentile_data_collected", universe=len(universe), survivors=len(all_dfs))
    return all_dfs, spy_df


# ── Metric computation (30 metrics) ──

def _safe_hurst(series: pd.Series, max_k: int = 20) -> float:
    data = series.dropna().values
    n = len(data)
    if n < 40:
        return 0.5
    ks = range(10, min(max_k + 1, n // 2))
    rs_list = []
    for k in ks:
        rs_vals = []
        for s in range(0, n - k, k):
            chunk = data[s:s + k]
            mean_chunk = chunk.mean()
            dev = chunk - mean_chunk
            cumdev = np.cumsum(dev)
            r = cumdev.max() - cumdev.min()
            std = chunk.std(ddof=1)
            if std > 1e-10:
                rs_vals.append(r / std)
        if rs_vals:
            rs_list.append((np.log(k), np.log(np.mean(rs_vals))))
    if len(rs_list) < 3:
        return 0.5
    x = np.array([p[0] for p in rs_list])
    y = np.array([p[1] for p in rs_list])
    slope = np.polyfit(x, y, 1)[0]
    return float(np.clip(slope, 0.0, 1.0))


def _compute_metrics(sym: str, df: pd.DataFrame, spy_df: pd.DataFrame) -> dict[str, float] | None:
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

        # Volatility (7)
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr_14 = tr.rolling(14).mean()
        metrics["atr_pct"] = float(atr_14.iloc[-1] / price * 100) if not np.isnan(atr_14.iloc[-1]) else 0.0
        daily_range = (high - low) / close * 100
        metrics["avg_daily_range_pct"] = float(daily_range.mean())
        metrics["return_std_pct"] = float(returns.std() * 100)
        range_pct = (high - low) / opn * 100
        metrics["pct_days_range_gt_2pct"] = float((range_pct > 2.0).mean() * 100)
        dd_from_open = (low - opn) / opn * 100
        metrics["max_drawdown_from_open_pct"] = float(dd_from_open.min())
        atr_5 = tr.rolling(5).mean()
        atr_20 = tr.rolling(20).mean()
        if not np.isnan(atr_5.iloc[-1]) and not np.isnan(atr_20.iloc[-1]) and atr_20.iloc[-1] > 0:
            metrics["range_expansion_ratio"] = float(atr_5.iloc[-1] / atr_20.iloc[-1])
        else:
            metrics["range_expansion_ratio"] = 1.0

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

        # Price Action (7)
        body = (close - opn).abs()
        full_range = high - low
        safe_range = full_range.replace(0, np.nan)
        captured = (body / safe_range).dropna()
        metrics["intraday_range_captured_pct"] = float(captured.mean() * 100)
        safe_hl = (high - low).replace(0, np.nan)
        close_pos = (close - low) / safe_hl
        metrics["close_near_high_ratio"] = float(close_pos.dropna().mean())
        metrics["close_near_low_ratio"] = float(1.0 - close_pos.dropna().mean())
        gaps = (opn - close.shift(1)) / close.shift(1) * 100
        metrics["avg_gap_pct"] = float(gaps.dropna().abs().mean())
        overnight_move = (opn - close.shift(1)).abs()
        intraday_move = (close - opn).abs()
        safe_intraday = intraday_move.replace(0, np.nan)
        ov_ratio = overnight_move / safe_intraday
        metrics["overnight_vs_intraday_ratio"] = float(ov_ratio.dropna().median())
        metrics["avg_abs_daily_return_pct"] = float(returns.abs().mean() * 100)
        intraday_pct = ((close - opn) / opn * 100).abs()
        metrics["avg_intraday_move_pct"] = float(intraday_pct.mean())

        # Momentum/Trend (6)
        if len(close) >= 21:
            metrics["mom_20d"] = float((close.iloc[-1] / close.iloc[-21] - 1) * 100)
        else:
            metrics["mom_20d"] = 0.0
        if len(close) >= 51:
            metrics["mom_50d"] = float((close.iloc[-1] / close.iloc[-51] - 1) * 100)
        else:
            metrics["mom_50d"] = 0.0
        signs = returns.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        same_sign = sum(1 for i in range(1, len(signs))
                        if signs.iloc[i] == signs.iloc[i-1] and signs.iloc[i] != 0)
        total_pairs = sum(1 for i in range(1, len(signs))
                          if signs.iloc[i] != 0 and signs.iloc[i-1] != 0)
        metrics["trend_persistence"] = float(same_sign / total_pairs) if total_pairs >= 5 else 0.5
        abs_sum = returns.abs().sum()
        metrics["trend_strength"] = float(abs(returns.sum()) / abs_sum) if abs_sum > 0 else 0.0
        metrics["hurst_exponent"] = _safe_hurst(returns)
        metrics["abs_momentum_20d"] = abs(metrics["mom_20d"])

        # Volume (3)
        vol_mean = volume.mean()
        vol_std = volume.std()
        metrics["volume_cv"] = float(vol_std / vol_mean) if vol_mean > 0 else 0.0
        if len(volume) >= 20:
            v = volume.values[-60:] if len(volume) >= 60 else volume.values
            c = close.values[-60:] if len(close) >= 60 else close.values
            corr = np.corrcoef(v, c)[0, 1]
            metrics["volume_price_correlation"] = float(corr) if not np.isnan(corr) else 0.0
        else:
            metrics["volume_price_correlation"] = 0.0
        metrics["dollar_volume"] = float((close * volume).mean())

        # Market (4)
        if len(common_idx) >= 20:
            r_stock = returns.loc[common_idx].values
            r_spy = spy_ret.loc[common_idx].values
            if np.var(r_spy) > 1e-10:
                metrics["beta_spy"] = float(np.cov(r_stock, r_spy)[0, 1] / np.var(r_spy))
            else:
                metrics["beta_spy"] = 1.0
        else:
            metrics["beta_spy"] = 1.0
        up_ret = returns[returns > 0]
        down_ret = returns[returns < 0]
        if len(up_ret) > 0 and len(down_ret) > 0:
            metrics["return_asymmetry"] = float(up_ret.mean() / (abs(down_ret.mean()) + 1e-10))
        else:
            metrics["return_asymmetry"] = 1.0
        metrics["up_day_ratio"] = float((returns > 0).mean())
        if len(returns) >= 20:
            ac = returns.autocorr(lag=1)
            metrics["return_autocorr_lag1"] = float(ac) if not np.isnan(ac) else 0.0
        else:
            metrics["return_autocorr_lag1"] = 0.0

        # Structure (3)
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

        metrics["directional_consistency"] = float(max(metrics["up_day_ratio"],
                                                       1 - metrics["up_day_ratio"]))

        return metrics

    except Exception:
        return None


# ── M1 Percentile Ranking ──

def _rank_by_percentile(metrics_df: pd.DataFrame, top_n: int = 4) -> list[tuple[str, float]]:
    """Sum percentile ranks of top 5 separating metrics. Returns [(symbol, score)]."""
    scores = {}
    for sym in metrics_df.index:
        total = 0.0
        for metric, direction in WINNER_PROFILE:
            if metric not in metrics_df.columns:
                continue
            col = metrics_df[metric]
            pctile = (col < metrics_df.loc[sym, metric]).mean()
            if direction == "lower":
                pctile = 1.0 - pctile
            total += pctile
        scores[sym] = total

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return ranked[:top_n]


# ── Public API ──

async def run_percentile_scan(
    top_n: int = 4,
    scan_config: ScanConfig | None = None,
) -> list[FitnessScore]:
    """Run percentile-based scan on full US equity universe.

    Returns FitnessScore objects for API compatibility with the fitness scanner.
    """
    logger.info("percentile_scan_start", top_n=top_n)

    # Phase 1: Collect data
    all_dfs, spy_df = await _collect_data()

    # Phase 2: Compute metrics
    rows = {}
    for sym, df in all_dfs.items():
        m = _compute_metrics(sym, df, spy_df)
        if m is not None:
            rows[sym] = m

    metrics_df = pd.DataFrame.from_dict(rows, orient="index")
    logger.info("percentile_metrics_computed", count=len(metrics_df))

    if metrics_df.empty:
        return []

    # Phase 2b: ATR% hard filter — remove tickers outside viable range
    atr_min = 2.5
    atr_max = 12.0
    if scan_config is not None:
        atr_min = getattr(scan_config, "atr_min_pct", atr_min) or atr_min
        atr_max = getattr(scan_config, "atr_max_pct", atr_max) or atr_max
    if "atr_pct" in metrics_df.columns:
        before = len(metrics_df)
        metrics_df = metrics_df[
            (metrics_df["atr_pct"] >= atr_min) & (metrics_df["atr_pct"] <= atr_max)
        ]
        logger.info("percentile_atr_filter", before=before, after=len(metrics_df),
                     atr_min=atr_min, atr_max=atr_max)
    if metrics_df.empty:
        return []

    # Phase 3: Rank by percentile similarity to winner profile
    ranked = _rank_by_percentile(metrics_df, top_n=top_n)

    # Convert to FitnessScore objects
    results: list[FitnessScore] = []
    for sym, score in ranked:
        price = float(all_dfs[sym]["close"].iloc[-1])
        avg_volume = int(all_dfs[sym]["volume"].mean())
        atr_pct = metrics_df.loc[sym, "atr_pct"] if "atr_pct" in metrics_df.columns else 0.0

        results.append(FitnessScore(
            symbol=sym,
            total_score=round(score * 20, 1),  # Scale 0-5 percentile sum to 0-100
            price=round(price, 2),
            avg_volume=avg_volume,
            atr_pct=round(float(atr_pct), 2),
            sr_strong_count=0,
            volume_score=0,
            price_score=0,
            sr_score=0,
            atr_score=0,
            disqualified=False,
            disqualify_reason="",
        ))

    logger.info(
        "percentile_scan_complete",
        universe=len(all_dfs),
        metrics_computed=len(metrics_df),
        top_n=len(results),
        picks=[r.symbol for r in results],
    )

    return results
