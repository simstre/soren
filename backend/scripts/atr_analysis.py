"""Quick ATR analysis on 1m candles for key tickers to understand stop/target sizing."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yfinance as yf
import pandas as pd
from app.analysis.indicators import atr as calc_atr_df

TICKERS = ["NVDA", "TSLA", "PLTR", "AAPL", "AMZN", "CEG", "META", "MSFT", "GOOGL", "SOFI", "SMR", "PFE"]

print(f"{'Ticker':>8s} | {'Price':>8s} | {'ATR14':>8s} | {'ATR%':>6s} | {'2%SL$':>8s} | {'ATR2x$':>8s} | {'ATR3x$':>8s} | {'ATR5x$':>8s} | {'2%>ATR2x':>8s}")
print("-" * 100)

for sym in TICKERS:
    try:
        t = yf.Ticker(sym)
        df = t.history(period="7d", interval="1m")
        if df.empty:
            continue
        df.columns = [c.lower() for c in df.columns]
        atr_vals = calc_atr_df(df, period=14)
        last_atr = atr_vals.iloc[-1]
        price = df["close"].iloc[-1]
        atr_pct = (last_atr / price) * 100
        stop_2pct = price * 0.02
        atr_2x = last_atr * 2
        atr_3x = last_atr * 3
        atr_5x = last_atr * 5

        print(f"{sym:>8s} | ${price:7.2f} | ${last_atr:7.4f} | {atr_pct:5.2f}% | ${stop_2pct:7.2f} | ${atr_2x:7.4f} | ${atr_3x:7.4f} | ${atr_5x:7.4f} | {'YES' if stop_2pct > atr_2x else 'no'}")
    except Exception as e:
        print(f"{sym:>8s} | ERROR: {e}")

# Also analyze max daily moves for these tickers
print("\n\nDAILY MOVE ANALYSIS (last 30 days)")
print(f"{'Ticker':>8s} | {'Avg Daily%':>10s} | {'Max Daily%':>10s} | {'p75 Daily%':>10s} | {'Days>2%':>8s} | {'Days>4%':>8s}")
print("-" * 80)

for sym in TICKERS:
    try:
        t = yf.Ticker(sym)
        df = t.history(period="1mo", interval="1d")
        if df.empty or len(df) < 5:
            continue
        df.columns = [c.lower() for c in df.columns]
        daily_range_pct = ((df["high"] - df["low"]) / df["close"]) * 100
        daily_change_pct = (df["close"].pct_change() * 100).dropna().abs()

        avg_range = daily_range_pct.mean()
        max_range = daily_range_pct.max()
        p75_range = daily_range_pct.quantile(0.75)
        days_gt2 = (daily_range_pct > 2).sum()
        days_gt4 = (daily_range_pct > 4).sum()

        print(f"{sym:>8s} | {avg_range:9.2f}% | {max_range:9.2f}% | {p75_range:9.2f}% | {days_gt2:8d} | {days_gt4:8d}")
    except Exception as e:
        print(f"{sym:>8s} | ERROR: {e}")
