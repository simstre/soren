import { create } from "zustand";

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
  setSelectedTicker: (ticker: string) => void;
  setTimeframe: (tf: string) => void;
  setEngineRunning: (running: boolean) => void;
  setReplayMode: (mode: boolean) => void;
  setTradeHistoryTab: (tab: "live" | "backtest") => void;
  setPerformanceTab: (tab: "live" | "backtest") => void;
  setSelectedTradeId: (id: number | null) => void;
  setSelectedTrade: (trade: import("@/lib/types").Trade | null) => void;
  setSelectedStrategy: (strategy: string) => void;
}

export const useAppStore = create<AppState>((set) => ({
  selectedTicker: "SPY",
  timeframe: "1m",
  engineRunning: false,
  replayMode: false,
  tradeHistoryTab: "live",
  performanceTab: "live",
  selectedTradeId: null,
  selectedTrade: null,
  selectedStrategy: "refined_rsi_v8",
  setSelectedTicker: (ticker) => set({ selectedTicker: ticker }),
  setTimeframe: (tf) => set({ timeframe: tf }),
  setEngineRunning: (running) => set({ engineRunning: running }),
  setReplayMode: (mode) => set({ replayMode: mode }),
  setTradeHistoryTab: (tab) => set({ tradeHistoryTab: tab }),
  setPerformanceTab: (tab) => set({ performanceTab: tab }),
  setSelectedTradeId: (id) => set({ selectedTradeId: id }),
  setSelectedTrade: (trade) => set({ selectedTrade: trade }),
  setSelectedStrategy: (strategy) => set({ selectedStrategy: strategy }),
}));
