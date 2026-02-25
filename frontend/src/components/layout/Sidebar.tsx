"use client";

import { useState, useEffect } from "react";
import { PieChart, ShieldAlert, List, X, ScanLine, Loader2, RotateCcw, Clock, Play, Square } from "lucide-react";
import { api } from "@/lib/api";
import { useAppStore } from "@/stores/appStore";
import { useEngine } from "@/hooks/useEngine";
import clsx from "clsx";

const STOP_LOSS_OPTIONS = [
  { value: 0, label: "None" },
  { value: 0.005, label: "0.5%" },
  { value: 0.01, label: "1%" },
  { value: 0.02, label: "2%" },
  { value: 0.03, label: "3%" },
  { value: 0.05, label: "5%" },
  { value: 0.10, label: "10%" },
];

const POSITION_SIZE_OPTIONS = [
  { value: 0.05, label: "5%" },
  { value: 0.10, label: "10%" },
  { value: 0.25, label: "25%" },
  { value: 0.33, label: "33%" },
  { value: 0.50, label: "50%" },
  { value: 1.00, label: "100%" },
];

const CADENCE_OPTIONS = [
  { value: "1m", label: "1m" },
  { value: "5m", label: "5m" },
  { value: "15m", label: "15m" },
  { value: "1h", label: "1h" },
  { value: "1D", label: "1D" },
];

export function Sidebar() {
  const positionSizePct = useAppStore((s) => s.positionSizePct);
  const setPositionSizePct = useAppStore((s) => s.setPositionSizePct);
  const stopLossPct = useAppStore((s) => s.stopLossPct);
  const setStopLossPct = useAppStore((s) => s.setStopLossPct);
  const tradingTickers = useAppStore((s) => s.tradingTickers);
  const addTradingTicker = useAppStore((s) => s.addTradingTicker);
  const removeTradingTicker = useAppStore((s) => s.removeTradingTicker);
  const setTradingTickers = useAppStore((s) => s.setTradingTickers);
  const tradeCadence = useAppStore((s) => s.tradeCadence);
  const setTradeCadence = useAppStore((s) => s.setTradeCadence);
  const engineRunning = useAppStore((s) => s.engineRunning);
  const selectedStrategy = useAppStore((s) => s.selectedStrategy);
  const selectedTicker = useAppStore((s) => s.selectedTicker);
  const setTradeHistoryTab = useAppStore((s) => s.setTradeHistoryTab);
  const setPerformanceTab = useAppStore((s) => s.setPerformanceTab);
  const loadRefinedRsiPreset = useAppStore((s) => s.loadRefinedRsiPreset);
  const { status, loading, start, stop } = useEngine();
  const running = status?.running ?? false;
  const [tickerInput, setTickerInput] = useState("");
  const [scanning, setScanning] = useState(false);

  useEffect(() => {
    // Hydrate position size from backend then load preset
    api.getTradingSettings().then((s) => {
      useAppStore.setState({
        positionSizePct: s.max_position_pct,
        stopLossPct: s.stop_loss_pct ?? 0.02,
      });
    }).catch(() => {}).finally(() => {
      // Auto-load refined_rsi preset on startup
      loadRefinedRsiPreset();
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleScanFitness = async () => {
    setScanning(true);
    try {
      const results = await api.scanFitness(10);
      const tickers = results.map((r) => r.symbol);
      setTradingTickers(tickers);
    } catch {
      // keep existing tickers on error
    }
    setScanning(false);
  };

  const handleStartTrading = () => {
    const tickers = tradingTickers.length > 0 ? tradingTickers : [selectedTicker];
    start(tradeCadence, selectedStrategy, tickers);
    setTradeHistoryTab("live");
    setPerformanceTab("live");
  };

  return (
    <aside className="w-64 shrink-0 bg-zinc-900 border-r border-zinc-800 flex flex-col overflow-y-auto">
      {/* Fitness Scanner Button */}
      <div className="p-3 border-b border-zinc-800 flex-shrink-0">
        <button
          onClick={handleScanFitness}
          disabled={scanning}
          className={clsx(
            "w-full flex items-center justify-center gap-2 py-2 rounded-lg font-medium text-sm transition-all",
            "bg-cyan-500/10 text-cyan-400 hover:bg-cyan-500/20 border border-cyan-500/20",
            scanning && "opacity-60 cursor-not-allowed"
          )}
        >
          {scanning ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <ScanLine size={14} />
          )}
          {scanning ? "Scanning Market..." : "Scan for Best Tickers"}
        </button>
      </div>

      {/* Trading Tickers */}
      <div className="p-3 border-b border-zinc-800 flex-shrink-0">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2 text-xs text-zinc-500 uppercase tracking-wider">
            <List size={12} />
            Trading Tickers
          </div>
          {tradingTickers.length > 0 && (
            <button
              onClick={() => setTradingTickers([])}
              className="text-zinc-600 hover:text-zinc-400 transition-colors"
              title="Clear all tickers"
            >
              <RotateCcw size={11} />
            </button>
          )}
        </div>
        <div className="flex gap-1 mb-2">
          <input
            type="text"
            value={tickerInput}
            onChange={(e) => setTickerInput(e.target.value.toUpperCase())}
            onKeyDown={(e) => {
              if (e.key === "Enter" && tickerInput.trim()) {
                addTradingTicker(tickerInput);
                setTickerInput("");
              }
            }}
            placeholder="Add ticker..."
            className="flex-1 px-2 py-1.5 bg-zinc-800 border border-zinc-700 rounded text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-zinc-500"
          />
        </div>
        {tradingTickers.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {tradingTickers.map((t) => (
              <span
                key={t}
                className="inline-flex items-center gap-1 px-2 py-0.5 bg-zinc-800 border border-zinc-700 rounded text-xs text-zinc-300"
              >
                {t}
                <button
                  onClick={() => removeTradingTicker(t)}
                  className="text-zinc-500 hover:text-zinc-300"
                >
                  <X size={10} />
                </button>
              </span>
            ))}
          </div>
        ) : (
          <p className="text-[10px] text-zinc-600">Defaults to chart ticker</p>
        )}
      </div>

      {/* Trade Cadence */}
      <div className="p-3 border-b border-zinc-800 flex-shrink-0">
        <div className="flex items-center gap-2 text-xs text-zinc-500 uppercase tracking-wider mb-2">
          <Clock size={12} />
          Trade Cadence
        </div>
        <div className="flex gap-1">
          {CADENCE_OPTIONS.map((tf) => (
            <button
              key={tf.value}
              onClick={() => setTradeCadence(tf.value)}
              disabled={engineRunning}
              className={clsx(
                "flex-1 py-1.5 text-xs rounded transition-colors",
                tradeCadence === tf.value
                  ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                  : "bg-zinc-800 text-zinc-400 border border-zinc-700 hover:border-zinc-600",
                engineRunning && "opacity-50 cursor-not-allowed"
              )}
            >
              {tf.label}
            </button>
          ))}
        </div>
      </div>

      {/* Position Size */}
      <div className="p-3 border-b border-zinc-800 flex-shrink-0">
        <div className="flex items-center gap-2 text-xs text-zinc-500 uppercase tracking-wider mb-2">
          <PieChart size={12} />
          Position Size
        </div>
        <div className="flex gap-1">
          {POSITION_SIZE_OPTIONS.map((ps) => (
            <button
              key={ps.value}
              onClick={() => setPositionSizePct(ps.value)}
              className={clsx(
                "flex-1 py-1.5 text-xs rounded transition-colors",
                positionSizePct === ps.value
                  ? "bg-purple-500/20 text-purple-400 border border-purple-500/30"
                  : "bg-zinc-800 text-zinc-400 border border-zinc-700 hover:border-zinc-600"
              )}
            >
              {ps.label}
            </button>
          ))}
        </div>
      </div>

      {/* Stop Loss */}
      <div className="p-3 border-b border-zinc-800 flex-shrink-0">
        <div className="flex items-center gap-2 text-xs text-zinc-500 uppercase tracking-wider mb-2">
          <ShieldAlert size={12} />
          Stop Loss
        </div>
        <div className="flex gap-1 flex-wrap">
          {STOP_LOSS_OPTIONS.map((sl) => (
            <button
              key={sl.value}
              onClick={() => setStopLossPct(sl.value)}
              className={clsx(
                "px-2 py-1.5 text-xs rounded transition-colors",
                stopLossPct === sl.value
                  ? "bg-red-500/20 text-red-400 border border-red-500/30"
                  : "bg-zinc-800 text-zinc-400 border border-zinc-700 hover:border-zinc-600"
              )}
            >
              {sl.label}
            </button>
          ))}
        </div>
      </div>

      {/* Start / Stop Trading Button */}
      <div className="p-3 border-b border-zinc-800 flex-shrink-0">
        <button
          onClick={running ? stop : handleStartTrading}
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
            <Loader2 size={14} className="animate-spin" />
          ) : running ? (
            <Square size={16} />
          ) : (
            <Play size={16} />
          )}
          {running ? "Stop Trading" : "Start Trading"}
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
