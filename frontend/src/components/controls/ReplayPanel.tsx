"use client";

import { useState, useEffect } from "react";
import { useReplay } from "@/hooks/useReplay";
import { useAppStore } from "@/stores/appStore";
import {
  Play,
  Pause,
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

const SPEED_OPTIONS = [1, 2, 5, 10, 50];

const TIMEFRAME_OPTIONS = [
  { value: "1m", label: "1m" },
  { value: "5m", label: "5m" },
  { value: "15m", label: "15m" },
  { value: "1h", label: "1h" },
  { value: "1D", label: "1D" },
];



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

/** Map timeframe to default period preset label (must match getDefaultDateRange logic). */
function getDefaultPeriodLabel(timeframe: string): string {
  switch (timeframe) {
    case "1m": return "1W";   // 2 days — closest preset
    case "5m": return "1W";   // 7 days
    case "15m": return "1M";  // 14 days — closest preset
    case "1h": return "1M";   // 1 month
    case "1D": return "6M";   // 6 months
    default: return "1W";
  }
}

/** Get default date range based on trade cadence. */
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
  const { status, results, loading, start, stop, pause, resume, setSpeed, clearResults } =
    useReplay();
  const selectedTicker = useAppStore((s) => s.selectedTicker);
  const tradingTickers = useAppStore((s) => s.tradingTickers);
  const selectedStrategy = useAppStore((s) => s.selectedStrategy);
  const positionSizePct = useAppStore((s) => s.positionSizePct);
  const stopLossPct = useAppStore((s) => s.stopLossPct);

  const [timeframe, setTimeframe] = useState("5m");
  const [startDate, setStartDate] = useState(() => getDefaultDateRange("5m").start);
  const [endDate, setEndDate] = useState(() => getDefaultDateRange("5m").end);
  const [speed, setSpeedLocal] = useState(10);
  const [expanded, setExpanded] = useState(true);
  const [activePeriod, setActivePeriod] = useState<string | null>(() => getDefaultPeriodLabel("5m"));

  // Update date range when trade cadence changes
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
  const isPaused = status?.paused ?? false;
  const progress = status?.progress ?? 0;

  const handleStart = async () => {
    const symbols = tradingTickers.length > 0 ? tradingTickers : [selectedTicker];
    await start({
      symbols,
      timeframe,
      start_date: startDate,
      end_date: endDate,
      speed,
      stop_loss_pct: stopLossPct,
      position_size_pct: positionSizePct,
      strategy: selectedStrategy,
    });
  };

  const handleSpeedChange = async (newSpeed: number) => {
    setSpeedLocal(newSpeed);
    if (isRunning) {
      await setSpeed(newSpeed);
    }
  };

  const handleGoBack = () => {
    clearResults();
  };

  return (
    <div className="border-b border-zinc-800">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-2.5 flex items-center justify-between text-sm font-medium text-zinc-300 hover:text-zinc-100 transition-colors"
      >
        <span className="flex items-center gap-2">
          <RotateCcw size={14} className="text-blue-400" />
          Replay / Backtest
        </span>
        {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>

      {expanded && (
        <div className="px-4 pb-3 space-y-3">
          {/* Config (hide when running or showing results) */}
          {!isRunning && !results && (
            <>
              {/* Trade Cadence (Timeframe) */}
              <div>
                <label className="block text-xs text-zinc-500 mb-1">
                  Trade Cadence
                </label>
                <div className="flex gap-1">
                  {TIMEFRAME_OPTIONS.map((tf) => (
                    <button
                      key={tf.value}
                      onClick={() => setTimeframe(tf.value)}
                      className={clsx(
                        "flex-1 py-1 text-xs rounded transition-colors",
                        timeframe === tf.value
                          ? "bg-blue-500/20 text-blue-400 border border-blue-500/30"
                          : "bg-zinc-900 text-zinc-400 border border-zinc-700 hover:border-zinc-600"
                      )}
                    >
                      {tf.label}
                    </button>
                  ))}
                </div>
              </div>

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

              {/* Speed */}
              <div>
                <label className="block text-xs text-zinc-500 mb-1">
                  Speed
                </label>
                <div className="flex gap-1">
                  {SPEED_OPTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => handleSpeedChange(s)}
                      className={clsx(
                        "flex-1 py-1 text-xs rounded transition-colors",
                        speed === s
                          ? "bg-blue-500/20 text-blue-400 border border-blue-500/30"
                          : "bg-zinc-900 text-zinc-400 border border-zinc-700 hover:border-zinc-600"
                      )}
                    >
                      {s}x
                    </button>
                  ))}
                </div>
              </div>

              {/* Start button */}
              <button
                onClick={handleStart}
                disabled={loading}
                className={clsx(
                  "w-full flex items-center justify-center gap-2 py-2 rounded-lg font-medium text-sm transition-all",
                  "bg-blue-500/10 text-blue-400 hover:bg-blue-500/20 border border-blue-500/20",
                  loading && "opacity-60 cursor-not-allowed"
                )}
              >
                {loading ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <Play size={16} />
                )}
                Backtest {tradingTickers.length > 1 ? `${tradingTickers.length} Tickers` : tradingTickers.length === 1 ? tradingTickers[0] : selectedTicker}
              </button>
            </>
          )}

          {/* Playback controls (when running) */}
          {isRunning && (
            <>
              {/* Progress bar */}
              <div>
                <div className="flex justify-between text-xs text-zinc-500 mb-1">
                  <span>
                    {status?.symbol} {status?.timeframe}
                  </span>
                  <span>
                    {status?.bar_index} / {status?.total_bars} ({progress.toFixed(1)}%)
                  </span>
                </div>
                <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 rounded-full transition-all duration-300"
                    style={{ width: `${progress}%` }}
                  />
                </div>
              </div>

              {/* Controls row */}
              <div className="flex gap-2">
                <button
                  onClick={isPaused ? resume : pause}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-sm font-medium bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors"
                >
                  {isPaused ? <Play size={14} /> : <Pause size={14} />}
                  {isPaused ? "Resume" : "Pause"}
                </button>
                <button
                  onClick={stop}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-sm font-medium bg-red-500/10 text-red-400 hover:bg-red-500/20 border border-red-500/20 transition-colors"
                >
                  <Square size={14} />
                  Stop
                </button>
              </div>

              {/* Speed controls */}
              <div>
                <label className="block text-xs text-zinc-500 mb-1">
                  Speed
                </label>
                <div className="flex gap-1">
                  {SPEED_OPTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => handleSpeedChange(s)}
                      className={clsx(
                        "flex-1 py-1 text-xs rounded transition-colors",
                        (status?.speed ?? speed) === s
                          ? "bg-blue-500/20 text-blue-400 border border-blue-500/30"
                          : "bg-zinc-900 text-zinc-400 border border-zinc-700 hover:border-zinc-600"
                      )}
                    >
                      {s}x
                    </button>
                  ))}
                </div>
              </div>
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
