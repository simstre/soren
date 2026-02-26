import { create } from "zustand";

import { api } from "@/lib/api";

interface AppState {
  selectedTicker: string;
  timeframe: string;
  engineRunning: boolean;
  replayMode: boolean;
  tradeHistoryTab: "live" | "backtest";
  performanceTab: "live" | "backtest";
  selectedTradeId: number | null;
  selectedTrade: import("@/lib/types").Trade | null;
  selectedStrategy: string;
  positionSizePct: number;
  stopLossPct: number;
  tradingTickers: string[];
  tradeCadence: string;
  scanning: boolean;
  setSelectedTicker: (ticker: string) => void;
  setTimeframe: (tf: string) => void;
  setEngineRunning: (running: boolean) => void;
  setReplayMode: (mode: boolean) => void;
  setTradeHistoryTab: (tab: "live" | "backtest") => void;
  setPerformanceTab: (tab: "live" | "backtest") => void;
  setSelectedTradeId: (id: number | null) => void;
  setSelectedTrade: (trade: import("@/lib/types").Trade | null) => void;
  setSelectedStrategy: (strategy: string) => void;
  setPositionSizePct: (pct: number) => void;
  setStopLossPct: (pct: number) => void;
  addTradingTicker: (ticker: string) => void;
  removeTradingTicker: (ticker: string) => void;
  setTradingTickers: (tickers: string[]) => void;
  setTradeCadence: (cadence: string) => void;
  loadRefinedRsiPreset: () => void;
  loadStrategyPreset: (strategyId: string) => void;
}

export const useAppStore = create<AppState>((set, get) => ({
  selectedTicker: "SPY",
  timeframe: "5m",
  engineRunning: false,
  replayMode: false,
  tradeHistoryTab: "live",
  performanceTab: "live",
  selectedTradeId: null,
  selectedTrade: null,
  selectedStrategy: "refined_rsi",
  positionSizePct: 0.05,
  stopLossPct: 0.02,
  tradingTickers: [],
  tradeCadence: "1m",
  scanning: false,
  setSelectedTicker: (ticker) => set({ selectedTicker: ticker }),
  setTimeframe: (tf) => set({ timeframe: tf }),
  setEngineRunning: (running) => set({ engineRunning: running }),
  setReplayMode: (mode) => set({ replayMode: mode }),
  setTradeHistoryTab: (tab) => set({ tradeHistoryTab: tab }),
  setPerformanceTab: (tab) => set({ performanceTab: tab }),
  setSelectedTradeId: (id) => set({ selectedTradeId: id }),
  setSelectedTrade: (trade) => set({ selectedTrade: trade }),
  setSelectedStrategy: (strategy) => {
    set({ selectedStrategy: strategy });
    get().loadStrategyPreset(strategy);
  },
  setPositionSizePct: (pct) => {
    set({ positionSizePct: pct });
    api.updateTradingSettings({ max_position_pct: pct }).catch(() => {});
  },
  setStopLossPct: (pct) => {
    set({ stopLossPct: pct });
    api.updateTradingSettings({ stop_loss_pct: pct }).catch(() => {});
  },
  addTradingTicker: (ticker) =>
    set((s) => {
      const upper = ticker.toUpperCase().trim();
      if (!upper || s.tradingTickers.includes(upper)) return s;
      return { tradingTickers: [...s.tradingTickers, upper] };
    }),
  removeTradingTicker: (ticker) =>
    set((s) => ({ tradingTickers: s.tradingTickers.filter((t) => t !== ticker) })),
  setTradingTickers: (tickers) => set({ tradingTickers: tickers }),
  setTradeCadence: (cadence) => set({ tradeCadence: cadence }),
  loadRefinedRsiPreset: () => {
    get().loadStrategyPreset("refined_rsi");
  },
  loadStrategyPreset: (strategyId) => {
    set({
      selectedStrategy: strategyId,
      tradeCadence: "1m",
      timeframe: "1m",
      positionSizePct: 0.50,
    });
    api.updateTradingSettings({ max_position_pct: 0.50 }).catch(() => {});
    // Scan for best tickers using selected strategy's scan config
    set({ scanning: true });
    api.scanFitness(10, strategyId).then((results) => {
      const tickers = results.map((r) => r.symbol);
      set({ tradingTickers: tickers });
    }).catch(() => {}).finally(() => {
      set({ scanning: false });
    });
  },
}));
