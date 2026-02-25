"use client";

import { useMetrics } from "@/hooks/useMetrics";
import { useReplay } from "@/hooks/useReplay";
import { useAppStore } from "@/stores/appStore";
import { formatCurrency, formatPercent } from "@/lib/formatters";
import clsx from "clsx";

const PERIODS = [
  { key: "pnl_1d", label: "1D" },
  { key: "pnl_1w", label: "1W" },
  { key: "pnl_1m", label: "1M" },
  { key: "pnl_1y", label: "1Y" },
  { key: "pnl_ytd", label: "YTD" },
] as const;

function LivePerformance() {
  const { metrics, loading } = useMetrics();

  if (loading || !metrics) {
    return <div className="p-4 text-zinc-500 text-sm">Loading metrics...</div>;
  }

  const totalPnlPositive = metrics.total_pnl >= 0;

  return (
    <div className="space-y-3">
      {/* Balance */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="text-xs text-zinc-500">Starting</div>
          <div className="text-sm text-white font-mono">
            {formatCurrency(metrics.starting_balance)}
          </div>
        </div>
        <div>
          <div className="text-xs text-zinc-500">Current</div>
          <div className="text-sm text-white font-mono">
            {formatCurrency(metrics.current_balance)}
          </div>
        </div>
      </div>

      {/* Total P&L */}
      <div className="flex items-baseline gap-2">
        <span
          className={clsx(
            "text-xl font-bold font-mono",
            totalPnlPositive ? "text-emerald-400" : "text-red-400"
          )}
        >
          {formatCurrency(metrics.total_pnl)}
        </span>
        <span
          className={clsx(
            "text-sm font-mono",
            totalPnlPositive ? "text-emerald-400" : "text-red-400"
          )}
        >
          {formatPercent(metrics.total_pnl_pct)}
        </span>
      </div>

      {/* Period performance */}
      <div className="flex gap-2">
        {PERIODS.map(({ key, label }) => {
          const value = metrics[key];
          const positive = value >= 0;
          return (
            <div
              key={key}
              className="flex-1 bg-zinc-800/50 rounded px-2 py-1.5 text-center"
            >
              <div className="text-[10px] text-zinc-500">{label}</div>
              <div
                className={clsx(
                  "text-xs font-mono font-medium",
                  positive ? "text-emerald-400" : "text-red-400"
                )}
              >
                {formatCurrency(value)}
              </div>
            </div>
          );
        })}
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Win Rate</div>
          <div className="text-xs text-white font-mono">{metrics.win_rate.toFixed(1)}%</div>
        </div>
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Trades</div>
          <div className="text-xs text-white font-mono">{metrics.total_trades}</div>
        </div>
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Open</div>
          <div className="text-xs text-white font-mono">{metrics.open_positions}</div>
        </div>
      </div>

      {/* Long / Short breakdown */}
      <div className="grid grid-cols-2 gap-2 text-center">
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Long Win Rate</div>
          <div className="text-xs text-emerald-400 font-mono">{(metrics.long_win_rate ?? 0).toFixed(1)}%</div>
        </div>
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Short Win Rate</div>
          <div className="text-xs text-blue-400 font-mono">{(metrics.short_win_rate ?? 0).toFixed(1)}%</div>
        </div>
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Long P&L</div>
          <div className={clsx("text-xs font-mono", (metrics.long_pnl ?? 0) >= 0 ? "text-emerald-400" : "text-red-400")}>
            {formatCurrency(metrics.long_pnl ?? 0)}
          </div>
        </div>
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Short P&L</div>
          <div className={clsx("text-xs font-mono", (metrics.short_pnl ?? 0) >= 0 ? "text-emerald-400" : "text-red-400")}>
            {formatCurrency(metrics.short_pnl ?? 0)}
          </div>
        </div>
      </div>
    </div>
  );
}

function BacktestPerformance() {
  const { results } = useReplay();

  if (!results) {
    return <div className="p-2 text-zinc-600 text-sm">No backtest results yet</div>;
  }

  const totalPnlPositive = results.total_pnl >= 0;

  return (
    <div className="space-y-3">
      {/* Balance */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="text-xs text-zinc-500">Starting</div>
          <div className="text-sm text-white font-mono">
            ${results.starting_balance.toLocaleString()}
          </div>
        </div>
        <div>
          <div className="text-xs text-zinc-500">Ending</div>
          <div className="text-sm text-white font-mono">
            ${results.ending_balance.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}
          </div>
        </div>
      </div>

      {/* Total P&L */}
      <div className="flex items-baseline gap-2">
        <span
          className={clsx(
            "text-xl font-bold font-mono",
            totalPnlPositive ? "text-emerald-400" : "text-red-400"
          )}
        >
          ${results.total_pnl.toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })}
        </span>
        <span
          className={clsx(
            "text-sm font-mono",
            totalPnlPositive ? "text-emerald-400" : "text-red-400"
          )}
        >
          {results.total_pnl_pct >= 0 ? "+" : ""}
          {results.total_pnl_pct.toFixed(2)}%
        </span>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-2 text-center">
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Win Rate</div>
          <div className="text-xs text-white font-mono">{results.win_rate.toFixed(1)}%</div>
        </div>
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Trades</div>
          <div className="text-xs text-white font-mono">{results.total_trades}</div>
        </div>
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Max Drawdown</div>
          <div className="text-xs text-red-400 font-mono">
            -{results.max_drawdown_pct.toFixed(2)}%
          </div>
        </div>
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">W / L</div>
          <div className="text-xs text-white font-mono">
            {results.winning_trades} / {results.losing_trades}
          </div>
        </div>
      </div>

      {/* Long / Short breakdown */}
      <div className="grid grid-cols-2 gap-2 text-center">
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Long Win Rate</div>
          <div className="text-xs text-emerald-400 font-mono">{(results.long_win_rate ?? 0).toFixed(1)}%</div>
        </div>
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Short Win Rate</div>
          <div className="text-xs text-blue-400 font-mono">{(results.short_win_rate ?? 0).toFixed(1)}%</div>
        </div>
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Long P&L</div>
          <div className={clsx("text-xs font-mono", (results.long_pnl ?? 0) >= 0 ? "text-emerald-400" : "text-red-400")}>
            ${(results.long_pnl ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
        </div>
        <div className="bg-zinc-800/50 rounded px-2 py-1.5">
          <div className="text-[10px] text-zinc-500">Short P&L</div>
          <div className={clsx("text-xs font-mono", (results.short_pnl ?? 0) >= 0 ? "text-emerald-400" : "text-red-400")}>
            ${(results.short_pnl ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
        </div>
      </div>
    </div>
  );
}

export function InfoPanel() {
  const tab = useAppStore((s) => s.performanceTab);
  const setTab = useAppStore((s) => s.setPerformanceTab);

  return (
    <div className="p-4 border-b border-zinc-800 space-y-3">
      {/* Tab bar */}
      <div className="flex items-center gap-0 -mx-4 -mt-4 px-0 border-b border-zinc-800">
        <button
          onClick={() => setTab("live")}
          className={clsx(
            "flex-1 px-4 py-2 text-xs font-medium uppercase tracking-wider transition-colors",
            tab === "live"
              ? "text-zinc-200 border-b-2 border-emerald-500"
              : "text-zinc-500 hover:text-zinc-400"
          )}
        >
          Live
        </button>
        <button
          onClick={() => setTab("backtest")}
          className={clsx(
            "flex-1 px-4 py-2 text-xs font-medium uppercase tracking-wider transition-colors",
            tab === "backtest"
              ? "text-zinc-200 border-b-2 border-blue-500"
              : "text-zinc-500 hover:text-zinc-400"
          )}
        >
          Backtest
        </button>
      </div>

      {tab === "live" ? <LivePerformance /> : <BacktestPerformance />}
    </div>
  );
}
