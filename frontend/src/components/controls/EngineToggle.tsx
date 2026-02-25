"use client";

import { useEngine } from "@/hooks/useEngine";
import { useAppStore } from "@/stores/appStore";
import { Play, Square, Loader2 } from "lucide-react";
import clsx from "clsx";

export function EngineToggle() {
  const { status, loading, start, stop } = useEngine();
  const setTradeHistoryTab = useAppStore((s) => s.setTradeHistoryTab);
  const setPerformanceTab = useAppStore((s) => s.setPerformanceTab);
  const selectedStrategy = useAppStore((s) => s.selectedStrategy);
  const tradingTickers = useAppStore((s) => s.tradingTickers);
  const selectedTicker = useAppStore((s) => s.selectedTicker);
  const tradeCadence = useAppStore((s) => s.tradeCadence);
  const running = status?.running ?? false;

  return (
    <div className="px-4 py-3 border-b border-zinc-800">
      <button
        onClick={running ? stop : () => { const tickers = tradingTickers.length > 0 ? tradingTickers : [selectedTicker]; start(tradeCadence, selectedStrategy, tickers); setTradeHistoryTab("live"); setPerformanceTab("live"); }}
        disabled={loading}
        className={clsx(
          "w-full flex items-center justify-center gap-2 py-2.5 rounded-lg font-medium text-sm transition-all",
          running
            ? "bg-red-500/10 text-red-400 hover:bg-red-500/20 border border-red-500/20"
            : "bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 border border-emerald-500/20",
          loading && "opacity-60 cursor-not-allowed"
        )}
      >
        {loading ? (
          <Loader2 size={18} className="animate-spin" />
        ) : running ? (
          <Square size={18} />
        ) : (
          <Play size={18} />
        )}
        {running ? "Stop Trading" : "Start Trading"}
      </button>
      {status && running && (
        <div className="mt-2 text-xs text-zinc-500 text-center">
          Active: {status.active_tickers.join(", ")} &middot; {status.timeframe}
        </div>
      )}
    </div>
  );
}
