"use client";

import { useState, useEffect } from "react";
import { useReplay } from "@/hooks/useReplay";
import { useAppStore } from "@/stores/appStore";
import { api } from "@/lib/api";
import type { Strategy } from "@/lib/types";
import {
  Play,
  Square,
  Loader2,
  RotateCcw,
  ChevronDown,
  ChevronUp,
  ArrowLeft,
  Trophy,
  TrendingDown,
  BarChart3,
} from "lucide-react";
import clsx from "clsx";

const PERIOD_PRESETS = [
  { label: "1W", days: 7 },
  { label: "1M", days: 30 },
  { label: "3M", days: 90 },
  { label: "6M", days: 180 },
  { label: "1Y", days: 365 },
];

/** Get the most recent past weekday (yesterday or Friday if weekend). */
function getLastWeekday(): Date {
  const d = new Date();
  d.setDate(d.getDate() - 1); // start from yesterday
  const day = d.getDay();
  if (day === 0) d.setDate(d.getDate() - 2); // Sunday → Friday
  if (day === 6) d.setDate(d.getDate() - 1); // Saturday → Friday
  return d;
}

function toDateStr(d: Date): string {
  return d.toISOString().split("T")[0];
}

/** Map timeframe to default period preset label. */
function getDefaultPeriodLabel(timeframe: string): string {
  switch (timeframe) {
    case "1m": return "1W";
    case "5m": return "1W";
    case "15m": return "1M";
    case "1h": return "1M";
    case "1D": return "6M";
    default: return "1W";
  }
}

/** Get default date range based on timeframe. */
function getDefaultDateRange(timeframe: string): { start: string; end: string } {
  const end = getLastWeekday();
  const start = new Date(end);

  switch (timeframe) {
    case "1m":
      start.setDate(end.getDate() - 2);
      break;
    case "5m":
      start.setDate(end.getDate() - 7);
      break;
    case "15m":
      start.setDate(end.getDate() - 14);
      break;
    case "1h":
      start.setMonth(end.getMonth() - 1);
      break;
    case "1D":
      start.setMonth(end.getMonth() - 6);
      break;
    default:
      start.setDate(end.getDate() - 7);
  }

  return { start: toDateStr(start), end: toDateStr(end) };
}

export function ReplayPanel() {
  const { status, results, loading, start, stop, clearResults } =
    useReplay();
  const selectedTicker = useAppStore((s) => s.selectedTicker);
  const selectedStrategy = useAppStore((s) => s.selectedStrategy);

  // Fetch strategies to get the selected one's timeframe
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  useEffect(() => {
    api.getStrategies().then(setStrategies).catch(() => {});
  }, []);
  const selectedDef = strategies.find((s) => s.id === selectedStrategy);
  const timeframe = selectedDef?.timeframe ?? "1m";

  const [startDate, setStartDate] = useState(() => getDefaultDateRange("1m").start);
  const [endDate, setEndDate] = useState(() => getDefaultDateRange("1m").end);
  const [expanded, setExpanded] = useState(false);
  const [activePeriod, setActivePeriod] = useState<string | null>(() => getDefaultPeriodLabel("1m"));
  const [scanningTickers, setScanningTickers] = useState(false);

  // Update date range when strategy timeframe changes
  useEffect(() => {
    const range = getDefaultDateRange(timeframe);
    setStartDate(range.start);
    setEndDate(range.end);
    setActivePeriod(getDefaultPeriodLabel(timeframe));
  }, [timeframe]);

  const handlePeriodPreset = (days: number, label: string) => {
    const end = getLastWeekday();
    const start = new Date(end);
    start.setDate(end.getDate() - days);
    setStartDate(toDateStr(start));
    setEndDate(toDateStr(end));
    setActivePeriod(label);
  };

  const handleDateChange = (setter: (v: string) => void, value: string) => {
    setter(value);
    setActivePeriod(null);
  };

  const isRunning = status?.running ?? false;
  const progress = status?.progress ?? 0;

  const handleStart = async () => {
    // Auto-scan for tickers
    setScanningTickers(true);
    let symbols: string[];
    try {
      const results = await api.scanFitness(10, selectedStrategy);
      symbols = results.map((r) => r.symbol);
      if (symbols.length === 0) symbols = [selectedTicker];
    } catch {
      symbols = [selectedTicker];
    }
    setScanningTickers(false);

    await start({
      symbols,
      timeframe,
      start_date: startDate,
      end_date: endDate,
      speed: 1,
      strategy: selectedStrategy,
    });
  };

  const handleGoBack = () => {
    clearResults();
  };

  return (
    <div className="border-t border-zinc-800">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-3 py-2.5 flex items-center justify-between text-sm font-medium text-zinc-300 hover:text-zinc-100 transition-colors"
      >
        <span className="flex items-center gap-2">
          <RotateCcw size={14} className="text-blue-400" />
          Replay / Backtest
        </span>
        {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-2.5">
          {/* Config (hide when running or showing results) */}
          {!isRunning && !results && (
            <>
              {/* Period presets */}
              <div>
                <label className="block text-xs text-zinc-500 mb-1">
                  Period
                </label>
                <div className="flex gap-1">
                  {PERIOD_PRESETS.map((p) => (
                    <button
                      key={p.label}
                      onClick={() => handlePeriodPreset(p.days, p.label)}
                      className={clsx(
                        "flex-1 py-1 text-xs rounded transition-colors",
                        activePeriod === p.label
                          ? "bg-blue-500/20 text-blue-400 border border-blue-500/30"
                          : "bg-zinc-900 text-zinc-400 border border-zinc-700 hover:border-zinc-600"
                      )}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Custom date range */}
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-xs text-zinc-500 mb-1">
                    Start
                  </label>
                  <input
                    type="date"
                    value={startDate}
                    onChange={(e) => handleDateChange(setStartDate, e.target.value)}
                    className="w-full px-2 py-1.5 bg-zinc-900 border border-zinc-700 rounded text-xs text-zinc-200 focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-xs text-zinc-500 mb-1">
                    End
                  </label>
                  <input
                    type="date"
                    value={endDate}
                    onChange={(e) => handleDateChange(setEndDate, e.target.value)}
                    className="w-full px-2 py-1.5 bg-zinc-900 border border-zinc-700 rounded text-xs text-zinc-200 focus:outline-none focus:border-blue-500"
                  />
                </div>
              </div>

              {/* Start button */}
              <button
                onClick={handleStart}
                disabled={loading || scanningTickers}
                className={clsx(
                  "w-full flex items-center justify-center gap-2 py-2 rounded-lg font-medium text-sm transition-all",
                  "bg-blue-500/10 text-blue-400 hover:bg-blue-500/20 border border-blue-500/20",
                  (loading || scanningTickers) && "opacity-60 cursor-not-allowed"
                )}
              >
                {loading || scanningTickers ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <Play size={16} />
                )}
                {scanningTickers ? "Scanning tickers..." : "Backtest"}
              </button>
            </>
          )}

          {/* Running state */}
          {isRunning && (
            <>
              {/* Progress bar */}
              <div>
                <div className="flex justify-between text-xs text-zinc-500 mb-1">
                  <span className="flex items-center gap-1.5">
                    <Loader2 size={12} className="animate-spin text-blue-400" />
                    Running backtest...
                  </span>
                  <span>
                    {progress.toFixed(1)}%
                  </span>
                </div>
                <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 rounded-full transition-all duration-300"
                    style={{ width: `${progress}%` }}
                  />
                </div>
              </div>

              {/* Stop button */}
              <button
                onClick={stop}
                className="w-full flex items-center justify-center gap-1.5 py-2 rounded-lg text-sm font-medium bg-red-500/10 text-red-400 hover:bg-red-500/20 border border-red-500/20 transition-colors"
              >
                <Square size={14} />
                Stop
              </button>
            </>
          )}

          {/* Results (after completion or stop) */}
          {results && !isRunning && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-medium text-zinc-400">Results</h4>
              </div>

              {/* Metrics grid */}
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-zinc-900 rounded-lg p-2.5">
                  <div className="flex items-center gap-1.5 text-xs text-zinc-500 mb-0.5">
                    <BarChart3 size={12} />
                    Total P&L
                  </div>
                  <div
                    className={clsx(
                      "text-sm font-semibold",
                      results.total_pnl >= 0 ? "text-emerald-400" : "text-red-400"
                    )}
                  >
                    ${results.total_pnl.toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}
                  </div>
                  <div
                    className={clsx(
                      "text-xs",
                      results.total_pnl_pct >= 0 ? "text-emerald-500" : "text-red-500"
                    )}
                  >
                    {results.total_pnl_pct >= 0 ? "+" : ""}
                    {results.total_pnl_pct.toFixed(2)}%
                  </div>
                </div>

                <div className="bg-zinc-900 rounded-lg p-2.5">
                  <div className="flex items-center gap-1.5 text-xs text-zinc-500 mb-0.5">
                    <Trophy size={12} />
                    Win Rate
                  </div>
                  <div className="text-sm font-semibold text-zinc-200">
                    {results.win_rate.toFixed(1)}%
                  </div>
                  <div className="text-xs text-zinc-500">
                    {results.winning_trades}W / {results.losing_trades}L
                  </div>
                </div>

                <div className="bg-zinc-900 rounded-lg p-2.5">
                  <div className="flex items-center gap-1.5 text-xs text-zinc-500 mb-0.5">
                    <TrendingDown size={12} />
                    Max Drawdown
                  </div>
                  <div className="text-sm font-semibold text-red-400">
                    ${results.max_drawdown.toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}
                  </div>
                  <div className="text-xs text-red-500">
                    -{results.max_drawdown_pct.toFixed(2)}%
                  </div>
                </div>

                <div className="bg-zinc-900 rounded-lg p-2.5">
                  <div className="flex items-center gap-1.5 text-xs text-zinc-500 mb-0.5">
                    <BarChart3 size={12} />
                    Total Trades
                  </div>
                  <div className="text-sm font-semibold text-zinc-200">
                    {results.total_trades}
                  </div>
                  <div className="text-xs text-zinc-500">
                    ${results.starting_balance.toLocaleString()} start
                  </div>
                </div>
              </div>

              {/* Go Back button */}
              <button
                onClick={handleGoBack}
                className="w-full flex items-center justify-center gap-2 py-2 rounded-lg font-medium text-sm transition-all bg-zinc-800 text-zinc-300 hover:bg-zinc-700 border border-zinc-700"
              >
                <ArrowLeft size={14} />
                Go Back
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
