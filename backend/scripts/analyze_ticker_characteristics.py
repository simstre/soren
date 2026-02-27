#!/usr/bin/env python3
"""Analyze what makes TSLA, CEG, PLTR, RIVN outperform with the v8 strategy.

Fetches 70 days of daily data for all tickers in SCAN_UNIVERSE, computes a wide
battery of metrics, and compares the known winners against the rest to identify
distinguishing characteristics that could drive an experimental scanner.

Usage:
    cd backend && .venv/bin/python scripts/analyze_ticker_characteristics.py
"""

import sys
import os
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

from app.scanner.runner import SCAN_UNIVERSE

WINNERS = {"TSLA", "CEG", "PLTR", "RIVN"}

# Remove ETFs from analysis — strategy is for single stocks
EXCLUDE = {"SPY", "QQQ", "IWM", "DIA"}

ALL_TICKERS = [t for t in SCAN_UNIVERSE if t not in EXCLUDE]


def _flatten_columns(df: pd.DataFrame, symbol: str = None) -> pd.DataFrame:
    """Flatten yfinance MultiIndex columns to lowercase single-level."""
    if isinstance(df.columns, pd.MultiIndex):
        # MultiIndex: ('Close', 'TSLA') -> 'close'
        df.columns = [col[0].lower() for col in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]
    return df


def fetch_daily_data(symbols: list[str], days: int = 90) -> dict[str, pd.DataFrame]:
    """Fetch daily OHLCV data for all symbols. Request extra days for indicator warmup."""
    end = datetime.now()
    start = end - timedelta(days=days)
    print(f"Fetching {days}d daily data for {len(symbols)} tickers...")

    data = {}
    # Fetch in batch
    tickers_str = " ".join(symbols)
    raw = yf.download(tickers_str, start=start.strftime("%Y-%m-%d"),
                      end=end.strftime("%Y-%m-%d"), interval="1d",
                      group_by="ticker", progress=False, auto_adjust=True)

    for sym in symbols:
        try:
            if len(symbols) == 1:
                df = raw.copy()
            else:
                # yfinance groups by ticker in the first level of MultiIndex
                # Access via top-level ticker, then flatten
                try:
                    df = raw.xs(sym, level="Ticker", axis=1).copy()
                except (KeyError, TypeError):
                    # Fallback: try old-style access
                    df = raw[sym].copy()
            df = _flatten_columns(df, sym)
            df = df.dropna(subset=["close"])
            if len(df) < 20:
                print(f"  SKIP {sym}: only {len(df)} rows")
                continue
            data[sym] = df
        except Exception as e:
            print(f"  SKIP {sym}: {e}")
    print(f"  Got data for {len(data)} tickers\n")
    return data


def fetch_spy_data(days: int = 90) -> pd.DataFrame:
    """Fetch SPY daily data for beta calculations."""
    end = datetime.now()
    start = end - timedelta(days=days)
    df = yf.download("SPY", start=start.strftime("%Y-%m-%d"),
                     end=end.strftime("%Y-%m-%d"), interval="1d",
                     progress=False, auto_adjust=True)
    df = _flatten_columns(df, "SPY")
    return df


def compute_atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    """ATR as % of price."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return (atr / close.iloc[-1]) * 100


def compute_metrics(sym: str, df: pd.DataFrame, spy_returns: pd.Series) -> dict:
    """Compute all characteristic metrics for a single ticker."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    opn = df["open"]
    volume = df["volume"]

    returns = close.pct_change().dropna()

    # Use last 70 days for most metrics (trim warmup)
    # If we have less than 70, use what we have
    trim = min(70, len(returns))
    ret = returns.iloc[-trim:]
    df_trim = df.iloc[-trim:]

    metrics = {"symbol": sym}

    # 1. ATR% (14-day)
    metrics["atr_pct"] = compute_atr_pct(df, 14)

    # 2. Average daily range (high-low)/close %
    daily_range_pct = ((df_trim["high"] - df_trim["low"]) / df_trim["close"]) * 100
    metrics["avg_daily_range_pct"] = daily_range_pct.mean()

    # 3. Daily return std dev (annualized would be *sqrt(252), keep raw)
    metrics["return_std_pct"] = ret.std() * 100

    # 4. Trend persistence: ratio of consecutive same-sign days to total days
    signs = np.sign(ret.values)
    same_sign_count = sum(1 for i in range(1, len(signs)) if signs[i] == signs[i - 1] and signs[i] != 0)
    total_transitions = len(signs) - 1
    metrics["trend_persistence"] = same_sign_count / total_transitions if total_transitions > 0 else 0

    # 5. Return asymmetry: avg up day magnitude / avg down day magnitude
    up_days = ret[ret > 0]
    down_days = ret[ret < 0]
    avg_up = up_days.mean() if len(up_days) > 0 else 0
    avg_down = abs(down_days.mean()) if len(down_days) > 0 else 0.001
    metrics["return_asymmetry"] = avg_up / avg_down if avg_down > 0 else 1.0

    # 6. Momentum: 20-day return and 50-day return
    if len(close) >= 21:
        metrics["mom_20d"] = (close.iloc[-1] / close.iloc[-21] - 1) * 100
    else:
        metrics["mom_20d"] = 0
    if len(close) >= 51:
        metrics["mom_50d"] = (close.iloc[-1] / close.iloc[-51] - 1) * 100
    else:
        metrics["mom_50d"] = 0

    # 7. Volume consistency: CV of daily volume
    vol_mean = volume.iloc[-trim:].mean()
    vol_std = volume.iloc[-trim:].std()
    metrics["volume_cv"] = vol_std / vol_mean if vol_mean > 0 else 0

    # 8. Mean reversion tendency: autocorrelation of daily returns at lag 1
    if len(ret) > 5:
        metrics["return_autocorr_lag1"] = ret.autocorr(lag=1)
    else:
        metrics["return_autocorr_lag1"] = 0

    # 9. Intraday range captured: |close-open| as % of (high-low)
    intraday_range = df_trim["high"] - df_trim["low"]
    body = (df_trim["close"] - df_trim["open"]).abs()
    # Avoid divide by zero
    valid_mask = intraday_range > 0
    if valid_mask.sum() > 0:
        captured = (body[valid_mask] / intraday_range[valid_mask]).mean() * 100
    else:
        captured = 0
    metrics["intraday_range_captured_pct"] = captured

    # 10. Beta vs SPY
    # Align returns by date
    aligned = pd.DataFrame({"stock": ret, "spy": spy_returns}).dropna()
    if len(aligned) > 10:
        cov = np.cov(aligned["stock"], aligned["spy"])
        beta = cov[0, 1] / cov[1, 1] if cov[1, 1] != 0 else 0
    else:
        beta = 0
    metrics["beta_spy"] = beta

    # 11. Up/down day ratio
    up_count = (ret > 0).sum()
    total_count = (ret != 0).sum()
    metrics["up_day_ratio"] = up_count / total_count if total_count > 0 else 0.5

    # 12. Max consecutive up days / max consecutive down days
    max_up, max_down = 0, 0
    current_up, current_down = 0, 0
    for r in ret.values:
        if r > 0:
            current_up += 1
            current_down = 0
        elif r < 0:
            current_down += 1
            current_up = 0
        else:
            current_up = 0
            current_down = 0
        max_up = max(max_up, current_up)
        max_down = max(max_down, current_down)
    metrics["max_consec_up"] = max_up
    metrics["max_consec_down"] = max_down

    # 13. Average gap (open vs prev close) as %
    prev_close = close.shift(1)
    gaps = ((opn - prev_close) / prev_close).dropna() * 100
    gaps_trim = gaps.iloc[-trim:]
    metrics["avg_gap_pct"] = gaps_trim.abs().mean()

    # 14. Sector/industry
    try:
        ticker_info = yf.Ticker(sym).info
        metrics["sector"] = ticker_info.get("sector", "N/A")
        metrics["industry"] = ticker_info.get("industry", "N/A")
    except Exception:
        metrics["sector"] = "N/A"
        metrics["industry"] = "N/A"

    # === Extra derived metrics useful for v8 strategy ===

    # 15. Range-to-stop ratio: avg daily range vs 2% stop loss
    # If range >> 2%, more room for the trade to work
    metrics["range_vs_stop"] = metrics["avg_daily_range_pct"] / 2.0

    # 16. Trend strength: abs(20d momentum) / volatility — risk-adjusted momentum
    metrics["trend_strength"] = abs(metrics["mom_20d"]) / (metrics["return_std_pct"] * np.sqrt(20)) if metrics["return_std_pct"] > 0 else 0

    # 17. Directional consistency score: up_day_ratio * trend_persistence
    metrics["directional_consistency"] = metrics["up_day_ratio"] * metrics["trend_persistence"]

    # 18. Average absolute daily return (how much does it MOVE regardless of direction)
    metrics["avg_abs_daily_return_pct"] = ret.abs().mean() * 100

    # 19. Days with move > 2% (favorable for v8 which holds until end of day)
    big_move_days = (daily_range_pct > 2.0).sum()
    metrics["pct_days_range_gt_2pct"] = (big_move_days / len(daily_range_pct)) * 100

    # 20. Overnight gap vs intraday move ratio
    intraday_move = ((df_trim["close"] - df_trim["open"]) / df_trim["open"]).abs() * 100
    metrics["avg_intraday_move_pct"] = intraday_move.mean()

    return metrics


def print_comparison_table(all_metrics: list[dict]):
    """Print formatted comparison of winners vs others."""
    df = pd.DataFrame(all_metrics)

    # Numeric columns for comparison
    numeric_cols = [
        "atr_pct", "avg_daily_range_pct", "return_std_pct", "trend_persistence",
        "return_asymmetry", "mom_20d", "mom_50d", "volume_cv", "return_autocorr_lag1",
        "intraday_range_captured_pct", "beta_spy", "up_day_ratio",
        "max_consec_up", "max_consec_down", "avg_gap_pct",
        "range_vs_stop", "trend_strength", "directional_consistency",
        "avg_abs_daily_return_pct", "pct_days_range_gt_2pct", "avg_intraday_move_pct",
    ]

    df["is_winner"] = df["symbol"].isin(WINNERS)

    # Sort: winners first, then by ATR% descending
    df = df.sort_values(["is_winner", "atr_pct"], ascending=[False, False])

    # --- TABLE 1: Winners ---
    print("=" * 160)
    print("WINNER TICKERS (TSLA, CEG, PLTR, RIVN)")
    print("=" * 160)
    winners_df = df[df["is_winner"]]
    _print_metrics_table(winners_df, numeric_cols)

    # --- TABLE 2: Phase-1 filtered (ATR >= 2.5%) non-winners ---
    print("\n" + "=" * 160)
    print("TOP NON-WINNER TICKERS (ATR% >= 2.5%)")
    print("=" * 160)
    others = df[(~df["is_winner"]) & (df["atr_pct"] >= 2.5)].head(15)
    _print_metrics_table(others, numeric_cols)

    # --- TABLE 3: Statistical comparison ---
    print("\n" + "=" * 160)
    print("STATISTICAL COMPARISON: Winners vs Non-Winners (ATR >= 2.5%)")
    print("=" * 160)
    winners = df[df["is_winner"]]
    non_winners_high_vol = df[(~df["is_winner"]) & (df["atr_pct"] >= 2.5)]
    non_winners_all = df[~df["is_winner"]]

    print(f"\n{'Metric':<32s} | {'Winners Mean':>14s} | {'Winners Med':>12s} | "
          f"{'Hi-Vol Others':>14s} | {'All Others':>14s} | {'Separation':>12s}")
    print("-" * 120)

    separations = {}
    for col in numeric_cols:
        w_mean = winners[col].mean()
        w_med = winners[col].median()
        nw_hv_mean = non_winners_high_vol[col].mean() if len(non_winners_high_vol) > 0 else 0
        nw_all_mean = non_winners_all[col].mean()

        # Separation: how many std devs apart are the means?
        pooled = df[col]
        pooled_std = pooled.std()
        if pooled_std > 0:
            sep = (w_mean - nw_all_mean) / pooled_std
        else:
            sep = 0

        separations[col] = sep

        print(f"  {col:<30s} | {w_mean:>14.4f} | {w_med:>12.4f} | "
              f"{nw_hv_mean:>14.4f} | {nw_all_mean:>14.4f} | {sep:>+11.2f} σ")

    # --- TABLE 4: Ranked separations ---
    print("\n" + "=" * 100)
    print("METRICS RANKED BY SEPARATION POWER (absolute effect size)")
    print("=" * 100)
    sorted_seps = sorted(separations.items(), key=lambda x: abs(x[1]), reverse=True)
    for rank, (col, sep) in enumerate(sorted_seps, 1):
        w_vals = winners[col].values
        bar = "#" * int(min(abs(sep) * 10, 50))
        direction = "winners HIGHER" if sep > 0 else "winners LOWER"
        print(f"  {rank:>2d}. {col:<32s}  {sep:>+6.2f} σ  ({direction})  {bar}")
        # Print winner values
        vals_str = ", ".join(f"{v:.2f}" for v in w_vals)
        print(f"      Winner values: [{vals_str}]")

    # --- TABLE 5: Sector/Industry ---
    print("\n" + "=" * 100)
    print("SECTOR / INDUSTRY BREAKDOWN")
    print("=" * 100)
    for _, row in df.iterrows():
        tag = " *** WINNER ***" if row["is_winner"] else ""
        if row["is_winner"] or row["atr_pct"] >= 2.5:
            print(f"  {row['symbol']:<8s}  {row['sector']:<25s}  {row['industry']:<40s}{tag}")

    return df, separations


def _print_metrics_table(subset: pd.DataFrame, cols: list[str]):
    """Print a formatted metrics table."""
    display_cols = [
        ("symbol", 8, "s"),
        ("atr_pct", 7, ".2f"),
        ("avg_daily_range_pct", 7, ".2f"),
        ("return_std_pct", 7, ".2f"),
        ("trend_persistence", 6, ".2f"),
        ("return_asymmetry", 6, ".2f"),
        ("mom_20d", 8, ".1f"),
        ("mom_50d", 8, ".1f"),
        ("volume_cv", 6, ".2f"),
        ("return_autocorr_lag1", 7, ".3f"),
        ("beta_spy", 6, ".2f"),
        ("up_day_ratio", 6, ".2f"),
        ("avg_gap_pct", 7, ".2f"),
        ("pct_days_range_gt_2pct", 6, ".0f"),
        ("avg_intraday_move_pct", 7, ".2f"),
        ("range_vs_stop", 6, ".2f"),
        ("trend_strength", 6, ".2f"),
    ]

    # Header
    header = ""
    for col, width, _ in display_cols:
        short_name = col.replace("_pct", "%").replace("return_autocorr_lag1", "autocorr")
        short_name = short_name.replace("avg_daily_range", "range").replace("return_std", "ret_std")
        short_name = short_name.replace("trend_persistence", "trend_p").replace("return_asymmetry", "asym")
        short_name = short_name.replace("volume_cv", "vol_cv").replace("beta_spy", "beta")
        short_name = short_name.replace("up_day_ratio", "up_rat").replace("avg_gap", "gap")
        short_name = short_name.replace("pct_days_range_gt_2", "d>2%").replace("avg_intraday_move", "intra_mv")
        short_name = short_name.replace("range_vs_stop", "rng/sl").replace("trend_strength", "trd_str")
        if len(short_name) > width:
            short_name = short_name[:width]
        header += f" {short_name:>{width}s} |"
    print(header)
    print("-" * len(header))

    for _, row in subset.iterrows():
        line = ""
        for col, width, fmt in display_cols:
            val = row[col]
            if fmt == "s":
                line += f" {str(val):>{width}s} |"
            else:
                line += f" {val:>{width}{fmt}} |"
        print(line)


def find_discriminating_thresholds(df: pd.DataFrame, separations: dict):
    """Find metric thresholds that capture all winners while excluding most others."""
    print("\n" + "=" * 100)
    print("DISCRIMINATING THRESHOLDS ANALYSIS")
    print("=" * 100)

    winners = df[df["is_winner"]]
    non_winners = df[~df["is_winner"]]

    # For each highly separating metric, find thresholds
    top_metrics = sorted(separations.items(), key=lambda x: abs(x[1]), reverse=True)[:10]

    for col, sep in top_metrics:
        w_min = winners[col].min()
        w_max = winners[col].max()
        w_mean = winners[col].mean()

        if sep > 0:
            # Winners are higher — find a lower bound
            threshold = w_min * 0.9  # 10% below winner minimum
            captured = (non_winners[col] >= threshold).sum()
            total_nw = len(non_winners)
            print(f"\n  {col} (winners HIGHER, sep={sep:+.2f}σ):")
            print(f"    Winner range: [{w_min:.3f}, {w_max:.3f}], mean={w_mean:.3f}")
            print(f"    Threshold >= {threshold:.3f}: captures all 4 winners + {captured}/{total_nw} non-winners ({captured/total_nw*100:.0f}%)")

            # Tighter threshold
            threshold2 = w_min
            captured2 = (non_winners[col] >= threshold2).sum()
            print(f"    Threshold >= {threshold2:.3f} (winner min): captures all winners + {captured2}/{total_nw} non-winners ({captured2/total_nw*100:.0f}%)")
        else:
            # Winners are lower — find an upper bound
            threshold = w_max * 1.1
            captured = (non_winners[col] <= threshold).sum()
            total_nw = len(non_winners)
            print(f"\n  {col} (winners LOWER, sep={sep:+.2f}σ):")
            print(f"    Winner range: [{w_min:.3f}, {w_max:.3f}], mean={w_mean:.3f}")
            print(f"    Threshold <= {threshold:.3f}: captures all 4 winners + {captured}/{total_nw} non-winners ({captured/total_nw*100:.0f}%)")

    # --- Multi-metric filter ---
    print("\n" + "=" * 100)
    print("MULTI-METRIC FILTER ANALYSIS")
    print("=" * 100)
    print("\nTesting combinations of top metrics to find filters that select ~4-6 tickers including all winners:\n")

    # Build candidate filters from the top positive-separation metrics
    pos_metrics = [(c, s) for c, s in top_metrics if s > 0.3]

    # Try each metric individually
    print("  --- Single-metric filters ---")
    for col, sep in pos_metrics:
        w_min = winners[col].min()
        passes = df[df[col] >= w_min]
        n_pass = len(passes)
        n_winners_captured = passes["is_winner"].sum()
        pass_tickers = ", ".join(passes["symbol"].tolist())
        print(f"  {col} >= {w_min:.3f}: {n_pass} pass ({n_winners_captured} winners) → {pass_tickers}")

    # Try pairs
    print("\n  --- Two-metric filters ---")
    for i in range(len(pos_metrics)):
        for j in range(i + 1, len(pos_metrics)):
            col_i, _ = pos_metrics[i]
            col_j, _ = pos_metrics[j]
            w_min_i = winners[col_i].min()
            w_min_j = winners[col_j].min()
            passes = df[(df[col_i] >= w_min_i) & (df[col_j] >= w_min_j)]
            n_pass = len(passes)
            n_winners_captured = passes["is_winner"].sum()
            if n_winners_captured == 4 and n_pass <= 10:
                pass_tickers = ", ".join(passes["symbol"].tolist())
                print(f"  {col_i} >= {w_min_i:.3f} AND {col_j} >= {w_min_j:.3f}: "
                      f"{n_pass} pass ({n_winners_captured} winners) → {pass_tickers}")

    # Try triples
    print("\n  --- Three-metric filters ---")
    for i in range(len(pos_metrics)):
        for j in range(i + 1, len(pos_metrics)):
            for k in range(j + 1, len(pos_metrics)):
                col_i, _ = pos_metrics[i]
                col_j, _ = pos_metrics[j]
                col_k, _ = pos_metrics[k]
                w_min_i = winners[col_i].min()
                w_min_j = winners[col_j].min()
                w_min_k = winners[col_k].min()
                passes = df[(df[col_i] >= w_min_i) & (df[col_j] >= w_min_j) & (df[col_k] >= w_min_k)]
                n_pass = len(passes)
                n_winners_captured = passes["is_winner"].sum()
                if n_winners_captured == 4 and n_pass <= 8:
                    pass_tickers = ", ".join(passes["symbol"].tolist())
                    print(f"  {col_i}>={w_min_i:.2f} AND {col_j}>={w_min_j:.2f} AND {col_k}>={w_min_k:.2f}: "
                          f"{n_pass} pass ({n_winners_captured}W) → {pass_tickers}")


def propose_scoring_formula(df: pd.DataFrame, separations: dict):
    """Build and test multiple experimental scoring formulas."""
    print("\n" + "=" * 100)
    print("SCORING FORMULA EXPERIMENTS")
    print("=" * 100)

    # ============================================================
    # FORMULA A: Naive volatility-weighted (baseline — what current scanner does)
    # ============================================================
    print("\n  --- FORMULA A: Naive Volatility (just pick highest ATR/range) ---")
    df["score_A"] = _zscore(df, "atr_pct") + _zscore(df, "avg_daily_range_pct")
    _print_ranking(df, "score_A", "A")

    # ============================================================
    # FORMULA B: Separation-weighted z-scores (all positive-sep metrics)
    # ============================================================
    print("\n  --- FORMULA B: Separation-Weighted Z-Scores ---")
    score_b = pd.Series(0.0, index=df.index)
    b_weights = {}
    for col, sep in separations.items():
        if sep > 0.3:  # Only use clearly positive separations
            b_weights[col] = sep
            score_b += sep * _zscore(df, col)
    df["score_B"] = score_b
    print(f"    Weights: {b_weights}")
    _print_ranking(df, "score_B", "B")

    # ============================================================
    # FORMULA C: v8-Tuned — the smart formula
    # ============================================================
    # Key insight: v8 needs tickers that:
    #   1. Move enough (daily range > 2% SL) — scored by range/ATR
    #   2. Are NOT so volatile they whipsaw through stops — penalize extreme vol
    #   3. Have mean-reverting tendencies (neg autocorrelation) — RSI divergence works
    #   4. Have enough down days to create RSI divergence setups
    #   5. High beta but not extreme (correlated to market for predictability)
    print("\n  --- FORMULA C: v8-Tuned Smart Formula ---")

    # Component 1: Range adequacy (want range >> 2% stop but penalize extreme)
    # Sweet spot: range_vs_stop between 1.5 and 3.0
    range_score = _zscore(df, "avg_daily_range_pct")
    # Penalize extreme volatility (diminishing returns above 5% daily range)
    extreme_penalty = np.maximum(0, (df["avg_daily_range_pct"] - 5.0) / 2.0)
    range_score = range_score - extreme_penalty

    # Component 2: Mean reversion (lower/more negative autocorrelation = better for RSI div)
    # Winners have autocorr near -0.06; extreme momentum stocks are positive
    mr_score = -1.0 * _zscore(df, "return_autocorr_lag1")  # negative = good

    # Component 3: Down day prevalence (lower up_day_ratio = more setups)
    # But not TOO low — pure losers won't work either
    # Winners cluster at 0.31-0.49 range
    down_score = -1.0 * _zscore(df, "up_day_ratio")
    # Penalize truly terrible tickers (up_day_ratio < 0.25)
    too_bearish_penalty = np.maximum(0, (0.25 - df["up_day_ratio"]) * 10)
    down_score = down_score - too_bearish_penalty

    # Component 4: Consecutive down days (more = more RSI divergence setups)
    consec_down_score = _zscore(df, "max_consec_down")

    # Component 5: Trend persistence (higher = once it starts moving, it continues)
    trend_score = _zscore(df, "trend_persistence")

    # Component 6: Beta (want 1.5-2.5 sweet spot)
    beta_vals = df["beta_spy"]
    beta_sweet = 1.0 - ((beta_vals - 2.0).abs() / 2.0).clip(0, 1)  # peaks at beta=2
    beta_score = (beta_sweet - beta_sweet.mean()) / beta_sweet.std()

    # Component 7: Intraday move (how much does it move open->close)
    intraday_score = _zscore(df, "avg_intraday_move_pct")

    # Component 8: Return asymmetry (bigger up days vs down days = profit when right)
    asym_score = _zscore(df, "return_asymmetry")

    # Weights tuned to emphasize what makes v8 work:
    df["score_C"] = (
        3.0 * range_score +        # Adequate daily range (primary)
        2.0 * mr_score +            # Mean reversion tendency
        1.5 * consec_down_score +   # Creates divergence setups
        1.5 * down_score +          # Enough down days for entries
        1.0 * trend_score +         # Moves persistently in one direction
        1.0 * beta_score +          # Sweet spot beta ~2
        0.8 * intraday_score +      # Good intraday range
        0.5 * asym_score            # Favorable asymmetry
    )

    print("    Weights:")
    print("      3.0 × range_score (daily range, penalize extreme)")
    print("      2.0 × mean_reversion_score (negative autocorrelation)")
    print("      1.5 × consec_down_score (max consecutive down days)")
    print("      1.5 × down_day_score (lower up_day_ratio)")
    print("      1.0 × trend_persistence_score")
    print("      1.0 × beta_sweet_spot_score (peaks at beta=2)")
    print("      0.8 × intraday_move_score")
    print("      0.5 × return_asymmetry_score")
    _print_ranking(df, "score_C", "C")

    # ============================================================
    # FORMULA D: Hybrid gate+score approach
    # ============================================================
    print("\n  --- FORMULA D: Gate + Score (hard filters + soft scoring) ---")
    # Hard gates: minimum thresholds that ALL winners pass
    gate_mask = (
        (df["avg_daily_range_pct"] >= 3.0) &       # Must have 3%+ daily range
        (df["pct_days_range_gt_2pct"] >= 75) &      # At least 75% of days range > 2%
        (df["beta_spy"] >= 1.2) &                   # Beta > 1.2 (not defensive)
        (df["avg_intraday_move_pct"] >= 1.4)        # Moves 1.4%+ intraday
    )
    df["passes_gate"] = gate_mask

    # Soft score for those passing gate (same as C but simpler)
    df["score_D"] = np.where(
        gate_mask,
        (
            _zscore(df, "avg_daily_range_pct") * 2.0 +
            -1.0 * _zscore(df, "return_autocorr_lag1") * 2.0 +
            _zscore(df, "max_consec_down") * 1.5 +
            -1.0 * _zscore(df, "up_day_ratio") * 1.5 +
            _zscore(df, "trend_persistence") * 1.0 +
            _zscore(df, "return_asymmetry") * 0.5
        ),
        -999  # filtered out
    )

    n_pass = gate_mask.sum()
    n_winners_pass = df[gate_mask]["is_winner"].sum()
    print(f"    Gate passes: {n_pass} tickers ({n_winners_pass} winners)")
    gated = df[gate_mask].sort_values("score_D", ascending=False)
    print(f"\n    Gated tickers (sorted by soft score):")
    for rank, (_, row) in enumerate(gated.iterrows(), 1):
        tag = "  ***" if row["is_winner"] else ""
        print(f"      {rank}. {row['symbol']:<8s} score={row['score_D']:>6.2f}  "
              f"range={row['avg_daily_range_pct']:.2f}%  "
              f"autocorr={row['return_autocorr_lag1']:.3f}  "
              f"consec_dn={row['max_consec_down']:.0f}  "
              f"beta={row['beta_spy']:.2f}{tag}")

    _print_ranking(df, "score_D", "D", filter_col="passes_gate")

    # ============================================================
    # SUMMARY: Compare all formulas
    # ============================================================
    print("\n" + "=" * 100)
    print("FORMULA COMPARISON SUMMARY")
    print("=" * 100)

    for label, col in [("A (Naive Vol)", "score_A"),
                        ("B (Sep-Weighted)", "score_B"),
                        ("C (v8-Tuned)", "score_C"),
                        ("D (Gate+Score)", "score_D")]:
        ranked = df[df[col] > -900].sort_values(col, ascending=False)
        top4 = set(ranked.head(4)["symbol"].tolist())
        top6 = set(ranked.head(6)["symbol"].tolist())
        top8 = set(ranked.head(8)["symbol"].tolist())
        w4 = len(top4 & WINNERS)
        w6 = len(top6 & WINNERS)
        w8 = len(top8 & WINNERS)
        print(f"\n  {label}:")
        print(f"    Top-4: {sorted(top4)} — {w4}/4 winners")
        print(f"    Top-6: {sorted(top6)} — {w6}/4 winners")
        print(f"    Top-8: {sorted(top8)} — {w8}/4 winners")

        # Average rank of winners
        ranks = []
        for sym in WINNERS:
            idx = ranked.index.tolist()
            sym_idx = df[df["symbol"] == sym].index[0]
            if sym_idx in idx:
                ranks.append(idx.index(sym_idx) + 1)
        if ranks:
            print(f"    Winner avg rank: {np.mean(ranks):.1f}, worst rank: {max(ranks)}")

    return df


def _zscore(df: pd.DataFrame, col: str) -> pd.Series:
    """Compute z-score of a column."""
    mean = df[col].mean()
    std = df[col].std()
    return (df[col] - mean) / std if std > 0 else pd.Series(0.0, index=df.index)


def _print_ranking(df: pd.DataFrame, score_col: str, label: str, filter_col: str = None):
    """Print top 20 ranking by score."""
    ranked = df.copy()
    if filter_col:
        ranked = ranked[ranked[filter_col] == True]
    ranked = ranked.sort_values(score_col, ascending=False)

    print(f"\n  {'Rank':<5s} {'Symbol':<8s} {'Score':>8s} {'ATR%':>7s} {'Range%':>7s} {'RetStd%':>7s} "
          f"{'AutoCr':>7s} {'UpRat':>6s} {'CnsDn':>5s} {'Beta':>6s} {'Winner?':>8s}")
    print("  " + "-" * 85)
    for rank, (_, row) in enumerate(ranked.iterrows(), 1):
        tag = "  ***" if row["is_winner"] else ""
        print(f"  {rank:<5d} {row['symbol']:<8s} {row[score_col]:>8.2f} "
              f"{row['atr_pct']:>7.2f} {row['avg_daily_range_pct']:>7.2f} "
              f"{row['return_std_pct']:>7.2f} "
              f"{row['return_autocorr_lag1']:>7.3f} {row['up_day_ratio']:>6.2f} "
              f"{row['max_consec_down']:>5.0f} {row['beta_spy']:>6.2f}{tag}")
        if rank >= 20:
            print("  ... (showing top 20)")
            break

    # Winner stats
    print(f"\n  Formula {label} — Winner rankings:")
    for sym in sorted(WINNERS):
        sym_idx = df[df["symbol"] == sym].index[0]
        idx_list = ranked.index.tolist()
        if sym_idx in idx_list:
            rank = idx_list.index(sym_idx) + 1
            scr = ranked.loc[sym_idx, score_col]
            print(f"    {sym}: rank #{rank}, score={scr:.2f}")
        else:
            print(f"    {sym}: filtered out")


def main():
    print("=" * 100)
    print("TICKER CHARACTERISTICS ANALYSIS — What makes v8 winners special?")
    print(f"Winners: {', '.join(sorted(WINNERS))}")
    print(f"Scan universe: {len(ALL_TICKERS)} tickers (excl. ETFs)")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 100)

    # Fetch data
    data = fetch_daily_data(ALL_TICKERS, days=90)

    # Fetch SPY for beta
    spy_df = fetch_spy_data(days=90)
    spy_returns = spy_df["close"].pct_change().dropna()

    # Compute metrics for all tickers
    print("Computing metrics...")
    all_metrics = []
    for sym, df in data.items():
        try:
            m = compute_metrics(sym, df, spy_returns)
            all_metrics.append(m)
        except Exception as e:
            print(f"  ERROR computing {sym}: {e}")

    print(f"  Computed metrics for {len(all_metrics)} tickers\n")

    # Print tables and analysis
    df, separations = print_comparison_table(all_metrics)

    # Find discriminating thresholds
    find_discriminating_thresholds(df, separations)

    # Propose and test scoring formula
    propose_scoring_formula(df, separations)

    print("\n" + "=" * 100)
    print("ANALYSIS COMPLETE")
    print("=" * 100)


if __name__ == "__main__":
    main()
