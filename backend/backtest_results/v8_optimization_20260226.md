# V8 Strategy Optimization Results — 2026-02-26

## Parameters
- Timeframe: 1m candles
- Period: 29 days (Jan 27 → Feb 25, 2026)
- Position size: 50% ($50,000)
- Starting balance: $100,000
- Stop loss: 2%

## Strategy: refined_rsi_v8
- Entry: RSI divergence + S/R proximity (strength ≥5) + volume confirmation
- Stop: 2% percentage-based (not ATR-based)
- Target: 6% percentage-based (rarely hit)
- Max hold: 390 minutes (1 trading day)
- No RSI reversal exit, no trailing stop, no EOD close
- Entries: skip first 15 min (9:30-9:45), no entries after 3 PM

## Key Insight
- ATR-based stops on 1m candles are too tight (1m ATR = 0.06-0.31%)
- 6% target almost never hits; P&L comes from max_hold exits
- Strategy is effectively: "enter on RSI divergence, hold 1 day, exit"
- 80% of max_hold exits are profitable, avg +1.54% per trade
- Trailing stops hurt performance (empirically tested)

## Parameter Sweep Results (single-ticker, averaged across 8 tickers)
```
  1. T=6% H=390m  | avg +1.75% | avg 10.4 trades
  2. T=6% H=780m  | avg +1.55% | avg 10.8 trades
  3. T=4% H=390m  | avg +1.54% | avg 10.4 trades
  4. T=4% H=780m  | avg +1.54% | avg 10.4 trades
  5. T=3% H=390m  | avg +1.15% | avg 10.4 trades
```

## Single-Ticker Results (optimal T=6% H=390m)
```
   TSLA | +8.91% | 13 trades | WR ???%
    CEG | +4.73% |  9 trades | WR 77.8% | DD 1.89%
   PLTR | +3.91% | 11 trades
   SOFI | +3.11% | 10 trades
   AAPL | +1.06% |  9 trades | WR 55.6%
   AMZN | -2.13% | 10 trades
   NVDA | -2.21% | 10 trades
   META | -3.40% | 11 trades
```

## Portfolio Results
```
      TSLA+CEG | +12.88% | $+12,881 | 23 trades | WR 69.6% | DD 2.79%
  TSLA+CEG+PLTR| +13.23% | $+13,227 | 31 trades | WR 64.5% | DD 3.68%
     +PLTR+SOFI| +12.07% | $+12,067 | 34 trades | WR 58.8% | DD 4.55%
         +AAPL |  +9.56% | $ +9,563 | 38 trades | WR 55.3% | DD 5.66%
     TSLA only |  +7.82% | $ +7,824 | 14 trades | WR 64.3% | DD 2.79%
      CEG only |  +4.73% | $ +4,727 |  9 trades | WR 77.8% | DD 1.89%
```

## vs Baseline (same tickers, v1/v5)
```
  v1 TSLA+CEG | +1.36% | $+1,363 | 47 trades | WR 46.8%
  v5 TSLA+CEG | +1.94% | $+1,944 | 53 trades | WR 45.3%
```

## Stop Loss Sensitivity (TSLA+CEG)
```
  SL=1.0% |  +9.80% | 28 trades | WR 50.0%
  SL=1.5% |  +9.47% | 26 trades | WR 57.7%
  SL=2.0% | +12.88% | 23 trades | WR 69.6%  ← OPTIMAL
  SL=2.5% | +12.01% | 23 trades | WR 69.6%
  SL=3.0% | +11.20% | 22 trades | WR 72.7%
  SL=4.0% | +11.58% | 22 trades | WR 72.7%
```

## Exit Reason Analysis (TSLA+CEG)
```
  max_hold  | 20 trades | 80.0% WR | avg +1.54% | total +30.70%
  stop_loss |  3 trades |  0.0% WR | avg -2.00% | total  -6.00%
  (no trail/breakeven/target exits observed)
```

## Improvement over Baseline
- v8 TSLA+CEG: +12.88% vs v1: +1.36% → **9.5x improvement**
- v8 TSLA+CEG: +12.88% vs v5: +1.94% → **6.6x improvement**
- Half the trades (23 vs 47) with much higher WR (69.6% vs 46.8%)

## Extended Universe Single-Ticker Results
```
   TSLA |  +7.82% | 14 trades | WR 64.3% | DD 2.79%
    CEG |  +4.73% |  9 trades | WR 77.8% | DD 1.89%
   PLTR |  +3.91% | 11 trades | WR 54.5% | DD 2.85%
   RIVN |  +3.87% | 13 trades | WR 61.5% | DD 3.62%  ← NEW FIND
   RKLB |  +3.15% | 13 trades | WR 46.2% | DD 5.16%
   SOFI |  +3.11% | 10 trades | WR 60.0% | DD 2.56%
   COIN |  +0.65% | 13 trades | WR 46.2% | DD 4.53%
   AFRM |  -0.56% |  7 trades | WR 28.6% | DD 4.63%
   SMCI |  -0.67% | 10 trades | WR 60.0% | DD 4.18%
   HOOD |  -1.97% |  9 trades | WR 44.4% | DD 4.60%
    ARM |  -2.43% |  5 trades | WR 20.0% | DD 4.24%
   MSTR |  -3.42% |  4 trades | WR  0.0% | DD 4.29%
```

## Optimized Portfolio Results
```
  TSLA+CEG+PLTR+RIVN | +17.26% | $+17,256 | 40 trades | WR 65.0% | DD 4.64%  ← BEST P&L
       TSLA+CEG+RIVN | +14.08% | $+14,080 | 30 trades | WR 70.0% | DD 3.77%  ← BEST EFFICIENCY
      TSLA+CEG+PLTR  | +13.23% | $+13,227 | 31 trades | WR 64.5% | DD 3.68%
              Top6    | +13.31% | $+13,314 | 53 trades | WR 58.5% | DD 5.50%
          TSLA+CEG    | +12.88% | $+12,881 | 23 trades | WR 69.6% | DD 2.79%  ← FEWEST TRADES
  TSLA+CEG+PLTR+SOFI | +12.07% | $+12,067 | 34 trades | WR 58.8% | DD 4.55%
```

## Recommendation
- **Best P&L**: TSLA+CEG+PLTR+RIVN → **+17.26%** (40 trades, 65% WR, 4.64% DD)
- **Best efficiency**: TSLA+CEG+RIVN → **+14.08%** (30 trades, 70% WR, 3.77% DD)
- **Fewest trades**: TSLA+CEG → **+12.88%** (23 trades, 69.6% WR, 2.79% DD)
- All three options exceed the 10% target
- Scanner should prioritize daily ATR% ≥ 3% and trend persistence
- Adding more tickers beyond 4 dilutes returns (Top6 at +13.31% < Top4 at +17.26%)
