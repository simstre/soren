# Backtest History

All results use `rsi_divergence_pct` (v8) strategy unless noted otherwise.
Settings: 1m candles, 2% SL, 6% target, 390-bar max hold, 50% position size, $150K starting balance.

---

## 2026-02-26 — V8 Optimization (baseline results)

Source: `v8_optimization_20260226.md`

| Portfolio | P&L % | Trades | Win Rate | Notes |
|---|---|---|---|---|
| TSLA+CEG+PLTR+RIVN | **+17.26%** | 40 | 65% | Best absolute P&L |
| TSLA+CEG+RIVN | **+14.08%** | 30 | 70% | Best efficiency (fewer trades) |
| TSLA+CEG | +11.52% | 20 | 65% | 2-ticker |

Key findings:
- v8 is ~9.5x better than v1, ~6.6x better than v5 on same tickers
- Optimal SL = 2% (tested 1%-4%)
- Adding more than 4 tickers dilutes returns
- Max hold exits produce 80% of winners (avg +1.54% per trade)

---

## 2026-02-26 — Approach 2 vs Broad Scan (~7000 US equities)

Source: `approach2_vs_broad_20260226_230331.txt`

**Goal**: Test whether scanning the full US equity market (~7000 tickers) finds better v8 candidates than the production 67-ticker universe.

### Scanner selections

| Arm | Tickers | Source |
|---|---|---|
| Approach 2 (67-ticker) | MRVL, SNOW, LRCX, ARM | Production scanner (real backtest scoring) |
| Broad scan (~7000) | RBRK, COHR, MRNA, SNOW | Standalone pipeline (batch filter → daily score → real backtest scoring) |
| Baseline (hand-picked) | TSLA, CEG, PLTR, RIVN | Known best from v8 optimization |

Broad scan discovered 3 tickers outside the 67-ticker list: **RBRK** (Rubrik), **COHR** (Coherent), **MRNA** (Moderna).

### 29-day backtest results

| Approach | P&L % | P&L $ | Trades | Win Rate | Max DD |
|---|---|---|---|---|---|
| **Baseline** | **+15.59%** | +$23,381 | 36 | 61.1% | 4.39% |
| Approach 2 (67) | +9.85% | +$14,768 | 33 | 57.6% | 5.44% |
| Broad (7000) | +9.30% | +$13,952 | 36 | 55.6% | 8.34% |

### Conclusions
- Hand-picked baseline still wins by ~6% over both scanner approaches
- Approach 2 and Broad scan performed similarly (+9.85% vs +9.30%)
- Broad scan's wider universe did NOT find materially better tickers
- Broad scan had higher drawdown (8.34% vs 5.44%) — less stable picks
- Neither scanner selected any of the known winners (TSLA, CEG, PLTR, RIVN) — the 7-day scoring window was unfavorable for those tickers
- The scanner's 7-day scoring window is a limitation: short-term performance doesn't reliably predict 29-day performance
