"use client";

import { useState, useEffect } from "react";
import { Check, Zap, Play, Square, Loader2, ScanLine } from "lucide-react";
import { api } from "@/lib/api";
import { useAppStore } from "@/stores/appStore";
import { useEngine } from "@/hooks/useEngine";
import type { Strategy } from "@/lib/types";
import clsx from "clsx";

export function StrategySidebar() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [scanning, setScanning] = useState(false);
  const selectedStrategy = useAppStore((s) => s.selectedStrategy);
  const setSelectedStrategy = useAppStore((s) => s.setSelectedStrategy);
  const setTradeHistoryTab = useAppStore((s) => s.setTradeHistoryTab);
  const setPerformanceTab = useAppStore((s) => s.setPerformanceTab);
  const { status, loading, start, stop } = useEngine();
  const running = status?.running ?? false;

  useEffect(() => {
    let cancelled = false;
    api.getStrategies().then((data) => {
      if (!cancelled) setStrategies(data);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const selectedDef = strategies.find((s) => s.id === selectedStrategy);

  const handleStart = async () => {
    // Auto-scan for best tickers, then start engine
    setScanning(true);
    try {
      const results = await api.scanFitness(10, selectedStrategy);
      const tickers = results.map((r) => r.symbol);
      await start(selectedStrategy, tickers.length > 0 ? tickers : undefined);
      setTradeHistoryTab("live");
      setPerformanceTab("live");
    } catch {
      // fallback: start without specific tickers
      await start(selectedStrategy);
    } finally {
      setScanning(false);
    }
  };

  return (
    <aside className="w-56 shrink-0 bg-zinc-950 border-r border-zinc-800 flex flex-col">
      <div className="p-3 border-b border-zinc-800 flex-shrink-0">
        <div className="flex items-center gap-2 text-xs text-zinc-500 uppercase tracking-wider">
          <Zap size={12} />
          Strategies
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-2 space-y-1.5">
        {strategies.map((strategy) => {
          const isSelected = selectedStrategy === strategy.id;
          return (
            <button
              key={strategy.id}
              onClick={() => setSelectedStrategy(strategy.id)}
              className={clsx(
                "w-full text-left rounded-lg p-3 transition-all",
                isSelected
                  ? "bg-blue-500/10 border border-blue-500/30 ring-1 ring-blue-500/20"
                  : "bg-zinc-900/50 border border-zinc-800 hover:border-zinc-700 hover:bg-zinc-900"
              )}
            >
              <div className="flex items-center justify-between mb-1">
                <span
                  className={clsx(
                    "text-sm font-medium",
                    isSelected ? "text-blue-400" : "text-zinc-300"
                  )}
                >
                  {strategy.name}
                </span>
                {isSelected && (
                  <Check size={14} className="text-blue-400 shrink-0" />
                )}
              </div>
              <p className="text-[11px] text-zinc-500 leading-relaxed">
                {strategy.description}
              </p>
              <div className="mt-1.5">
                <span
                  className={clsx(
                    "text-[10px] px-1.5 py-0.5 rounded font-mono",
                    isSelected
                      ? "bg-blue-500/10 text-blue-400/80"
                      : "bg-zinc-700/50 text-zinc-500"
                  )}
                >
                  {strategy.entry_rule}
                </span>
              </div>
              {/* Baked-in params */}
              {isSelected && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 font-mono">
                    {strategy.timeframe}
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 font-mono">
                    {(strategy.position_size_pct * 100).toFixed(0)}% pos
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 font-mono">
                    {(strategy.stop_loss_pct * 100).toFixed(1)}% SL
                  </span>
                </div>
              )}
            </button>
          );
        })}
      </div>

      {/* Start / Stop Trading */}
      <div className="p-3 border-t border-zinc-800 flex-shrink-0">
        {selectedDef && !running && (
          <div className="mb-2 text-[10px] text-zinc-500 text-center">
            {selectedDef.timeframe} &middot; {(selectedDef.position_size_pct * 100).toFixed(0)}% position &middot; {(selectedDef.stop_loss_pct * 100).toFixed(1)}% SL
          </div>
        )}
        <button
          onClick={running ? stop : handleStart}
          disabled={loading || scanning}
          className={clsx(
            "w-full flex items-center justify-center gap-2 py-2.5 rounded-lg font-medium text-sm transition-all",
            running
              ? "bg-red-500/10 text-red-400 hover:bg-red-500/20 border border-red-500/20"
              : "bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 border border-emerald-500/20",
            (loading || scanning) && "opacity-60 cursor-not-allowed"
          )}
        >
          {loading || scanning ? (
            <Loader2 size={14} className="animate-spin" />
          ) : running ? (
            <Square size={16} />
          ) : (
            <Play size={16} />
          )}
          {scanning ? "Scanning..." : running ? "Stop Trading" : "Start Trading"}
        </button>
        {status && running && (
          <div className="mt-2 text-xs text-zinc-500 text-center">
            Active: {status.active_tickers.join(", ")} &middot; {status.timeframe}
          </div>
        )}
      </div>
    </aside>
  );
}
