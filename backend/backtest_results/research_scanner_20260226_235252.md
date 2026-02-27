# Research Scanner Results — 2026-02-26 23:52

Strategy: rsi_divergence_pct | Backtest: 29 days | Balance: $150K | Position: 50%
Baseline: ['TSLA', 'CEG', 'PLTR', 'RIVN'] → +15.59%

---

## Metric Separation Rankings (Cohen's d)

| Rank | Metric | Cohen's d | Direction | Winner Mean | Rest Mean | Winner Pctile |
|------|--------|-----------|-----------|-------------|-----------|---------------|
| 1 | dollar_volume | +7.586 | higher | 9501579640.900 | 309085018.326 | 96.1% |
| 2 | max_consec_down | +1.726 | higher | 7.750 | 4.859 | 85.0% |
| 3 | up_day_ratio | -1.435 | lower | 0.419 | 0.516 | 14.4% |
| 4 | close_near_low_ratio | +1.070 | higher | 0.542 | 0.496 | 83.2% |
| 5 | close_near_high_ratio | -1.070 | lower | 0.458 | 0.504 | 16.8% |
| 6 | trend_strength | -0.987 | lower | 0.054 | 0.175 | 17.1% |
| 7 | pct_days_range_gt_2pct | +0.740 | higher | 90.000 | 68.918 | 68.6% |
| 8 | max_consec_up | -0.734 | lower | 4.000 | 5.245 | 16.1% |
| 9 | trend_persistence | +0.726 | higher | 0.547 | 0.498 | 65.8% |
| 10 | beta_spy | +0.670 | higher | 2.029 | 1.132 | 81.1% |
| 11 | directional_consistency | +0.604 | higher | 0.581 | 0.555 | 49.0% |
| 12 | range_expansion_ratio | -0.579 | lower | 0.784 | 0.918 | 29.6% |
| 13 | hurst_exponent | -0.439 | lower | 0.531 | 0.605 | 34.2% |
| 14 | return_asymmetry | +0.410 | higher | 1.344 | 1.141 | 57.7% |
| 15 | avg_gap_pct | +0.356 | higher | 1.098 | 0.854 | 76.1% |
| 16 | volume_cv | -0.334 | lower | 0.462 | 0.602 | 41.2% |
| 17 | volume_price_correlation | -0.325 | lower | -0.112 | -0.000 | 45.3% |
| 18 | avg_abs_daily_return_pct | +0.323 | higher | 2.470 | 2.006 | 74.4% |
| 19 | atr_pct | +0.309 | higher | 5.211 | 4.409 | 66.0% |
| 20 | avg_daily_range_pct | +0.308 | higher | 4.271 | 3.581 | 71.2% |

## Model Selections

- **M1_Percentile**: ['COIN', 'HIMS', 'DDOG', 'CPNG']
- **M2_Centroid**: ['META', 'GOOGL', 'AVGO', 'AMZN']
- **M3_HardGates**: ['AAPL', 'ORCL', 'NFLX', 'LLY']
- **M4_WeightedLinear**: ['NVDA', 'MSFT', 'AAPL', 'MU']
- **M5_Hybrid**: ['COIN', 'DDOG', 'ARM', 'BMNR']

## Backtest Results

| Model | Tickers | P&L% | P&L$ | Trades | WR% | DD% | vs Baseline |
|-------|---------|------|------|--------|-----|-----|-------------|
| Baseline | TSLA,CEG,PLTR,RIVN | +15.59% | $+23,381 | 36 | 61.1% | 4.39% | — |
| Blend_M1+M4 | COIN,HIMS,DDOG,CPNG | +12.61% | $+18,913 | 34 | 58.8% | 4.63% | -2.98pp |
| M1_Percentile | COIN,HIMS,DDOG,CPNG | +12.61% | $+18,913 | 34 | 58.8% | 4.63% | -2.98pp |
| M1_Percentile_PnLReweight | SMX,TCGL,BNAI,MLEC | -1.16% | $-1,745 | 6 | 16.7% | 4.26% | -16.75pp |
| M1_Percentile_top6 | COIN,HIMS,DDOG,CPNG,HOOD,DUO | +12.04% | $+18,060 | 36 | 61.1% | 6.54% | -3.55pp |
| M2_Centroid | META,GOOGL,AVGO,AMZN | -7.16% | $-10,736 | 32 | 34.4% | 10.34% | -22.75pp |
| M3_HardGates | AAPL,ORCL,NFLX,LLY | -3.94% | $-5,909 | 27 | 40.7% | 6.86% | -19.53pp |
| M4_WeightedLinear | NVDA,MSFT,AAPL,MU | +3.41% | $+5,116 | 29 | 55.2% | 4.54% | -12.18pp |
| M5_Hybrid | COIN,DDOG,ARM,BMNR | +0.11% | $+164 | 31 | 41.9% | 7.87% | -15.48pp |

## Conclusions

- **Best overall**: Baseline (+15.59%) — ['TSLA', 'CEG', 'PLTR', 'RIVN']
- **Baseline**: +15.59%
- **Best vs baseline**: +0.00pp
- **Best model (pre-iteration)**: M1_Percentile (+12.61%)

## Winner Profile (top 10 metrics)

- **dollar_volume**: winners=9501579640.900 (range 647955029.737—29158281524.526), universe=327543842.869
- **max_consec_down**: winners=7.750 (range 6.000—11.000), universe=4.865
- **up_day_ratio**: winners=0.419 (range 0.305—0.492), universe=0.516
- **close_near_low_ratio**: winners=0.542 (range 0.525—0.580), universe=0.496
- **close_near_high_ratio**: winners=0.458 (range 0.420—0.475), universe=0.504
- **trend_strength**: winners=0.054 (range 0.013—0.136), universe=0.175
- **pct_days_range_gt_2pct**: winners=90.000 (range 76.667—100.000), universe=68.960
- **max_consec_up**: winners=4.000 (range 3.000—5.000), universe=5.242
- **trend_persistence**: winners=0.547 (range 0.466—0.621), universe=0.498
- **beta_spy**: winners=2.029 (range 1.650—2.442), universe=1.134
