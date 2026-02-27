"use client";

import { useState, useEffect } from "react";
import { Play, Square, Loader2, ScanLine } from "lucide-react";
import { api } from "@/lib/api";
import { useAppStore } from "@/stores/appStore";
import { useEngine } from "@/hooks/useEngine";
import type { Strategy } from "@/lib/types";
import clsx from "clsx";

export function TradingControls() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [scanning, setScanning] = useState(false);
  const selectedStrategy = useAppStore((s) => s.selectedStrategy);
  const scanTactic = useAppStore((s) => s.scanTactic);
  const setScanTactic = useAppStore((s) => s.setScanTactic);
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
    setScanning(true);
    try {
      const results = await api.scanFitness(4, selectedStrategy, scanTactic);
      const tickers = results.map((r) => r.symbol);
      await start(selectedStrategy, tickers.length > 0 ? tickers : undefined);
      setTradeHistoryTab("live");
      setPerformanceTab("live");
    } catch {
      await start(selectedStrategy);
    } finally {
      setScanning(false);
    }
  };

  return (
    <div className="p-3 border-b border-zinc-800">
      {selectedDef && !running && (
        <div className="mb-2 text-[10px] text-zinc-500 text-center">
          {selectedDef.timeframe} &middot; {(selectedDef.position_size_pct * 100).toFixed(0)}% position &middot; {(selectedDef.stop_loss_pct * 100).toFixed(1)}% SL
        </div>
      )}
      {!running && (
        <div className="mb-2 flex gap-1">
          {([{ value: "watchlist", label: "Watchlist" }, { value: "market_scan", label: "Market Scan" }] as const).map((t) => (
            <button
              key={t.value}
              onClick={() => setScanTactic(t.value)}
              className={clsx(
                "flex-1 py-1 text-xs rounded transition-colors",
                scanTactic === t.value
                  ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                  : "bg-zinc-900 text-zinc-400 border border-zinc-700 hover:border-zinc-600"
              )}
            >
              {t.label}
            </button>
          ))}
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
  );
}
