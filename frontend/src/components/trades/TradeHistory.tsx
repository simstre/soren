"use client";

import { useTrades } from "@/hooks/useTrades";
import { useAppStore } from "@/stores/appStore";
import { formatCurrency, formatPercent, formatDateTime } from "@/lib/formatters";
import type { Trade } from "@/lib/types";
import clsx from "clsx";

/** Extract setup grade (A-E) from reason string like "[A] Support..." */
function parseGrade(reason: string): string | null {
  const match = reason.match(/^\[([A-E])\]/);
  return match ? match[1] : null;
}

const GRADE_COLORS: Record<string, string> = {
  A: "bg-emerald-500/15 text-emerald-400",
  B: "bg-blue-500/15 text-blue-400",
  C: "bg-yellow-500/15 text-yellow-400",
  D: "bg-orange-500/15 text-orange-400",
  E: "bg-red-500/15 text-red-400",
};

function TradeTable({ trades, loading }: { trades: Trade[]; loading: boolean }) {
  const selectedTradeId = useAppStore((s) => s.selectedTradeId);
  const setSelectedTradeId = useAppStore((s) => s.setSelectedTradeId);
  const setSelectedTrade = useAppStore((s) => s.setSelectedTrade);
  const setSelectedTicker = useAppStore((s) => s.setSelectedTicker);

  return (
    <div className="flex-1 overflow-y-auto">
      <table className="w-full text-sm table-fixed">
        {/* Time | Symbol | Grade | Side | Qty | Entry | Exit | Stop | Target | P&L | Reason | Status */}
        <colgroup>
          <col className="w-[140px]" />
          <col className="w-[70px]" />
          <col className="w-[42px]" />
          <col className="w-[52px]" />
          <col className="w-[48px]" />
          <col className="w-[80px]" />
          <col className="w-[80px]" />
          <col className="w-[80px]" />
          <col className="w-[80px]" />
          <col className="w-[120px]" />
          <col />
          <col className="w-[68px]" />
        </colgroup>
        <thead className="sticky top-0 bg-zinc-900">
          <tr className="text-xs text-zinc-500 border-b border-zinc-800">
            <th className="text-left px-3 py-2 font-medium">Time</th>
            <th className="text-left px-2 py-2 font-medium">Symbol</th>
            <th className="text-center px-1 py-2 font-medium">Grade</th>
            <th className="text-left px-2 py-2 font-medium">Side</th>
            <th className="text-right px-2 py-2 font-medium">Qty</th>
            <th className="text-right px-2 py-2 font-medium">Entry</th>
            <th className="text-right px-2 py-2 font-medium">Exit</th>
            <th className="text-right px-2 py-2 font-medium">Stop</th>
            <th className="text-right px-2 py-2 font-medium">Target</th>
            <th className="text-right px-2 py-2 font-medium">P&L</th>
            <th className="text-left px-2 py-2 font-medium">Reason</th>
            <th className="text-center px-2 py-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/50">
          {loading ? (
            <tr>
              <td colSpan={12} className="px-3 py-4 text-zinc-500 text-center">
                Loading trades...
              </td>
            </tr>
          ) : trades.length === 0 ? (
            <tr>
              <td colSpan={12} className="px-3 py-4 text-zinc-600 text-center">
                No trades yet
              </td>
            </tr>
          ) : (
            trades.map((trade) => {
              const pnlPositive = (trade.pnl ?? 0) >= 0;
              const grade = parseGrade(trade.reason);
              return (
                <tr
                  key={trade.id}
                  onClick={() => {
                    const deselecting = selectedTradeId === trade.id;
                    setSelectedTradeId(deselecting ? null : trade.id);
                    setSelectedTrade(deselecting ? null : trade);
                    if (!deselecting) setSelectedTicker(trade.symbol);
                  }}
                  className={clsx(
                    "text-zinc-300 cursor-pointer transition-colors",
                    selectedTradeId === trade.id
                      ? "bg-blue-500/15 hover:bg-blue-500/20"
                      : "hover:bg-zinc-800/30"
                  )}
                >
                  <td className="px-3 py-1.5 font-mono text-xs text-zinc-400 truncate">
                    {formatDateTime(trade.entry_time)}
                  </td>
                  <td className="px-2 py-1.5 font-mono font-medium text-white truncate">
                    {trade.symbol}
                  </td>
                  <td className="px-1 py-1.5 text-center">
                    {grade ? (
                      <span
                        className={clsx(
                          "text-[10px] font-bold px-1.5 py-0.5 rounded",
                          GRADE_COLORS[grade] ?? "bg-zinc-500/10 text-zinc-400"
                        )}
                      >
                        {grade}
                      </span>
                    ) : (
                      <span className="text-zinc-600 text-[10px]">--</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5">
                    <span
                      className={clsx(
                        "text-xs font-medium px-1.5 py-0.5 rounded",
                        trade.side === "BUY"
                          ? "bg-emerald-500/10 text-emerald-400"
                          : "bg-red-500/10 text-red-400"
                      )}
                    >
                      {trade.side}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono">{trade.quantity}</td>
                  <td className="px-2 py-1.5 text-right font-mono">
                    {formatCurrency(trade.entry_price)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono">
                    {trade.exit_price ? formatCurrency(trade.exit_price) : "---"}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-zinc-500">
                    {formatCurrency(trade.stop_loss)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-zinc-500">
                    {formatCurrency(trade.profit_target)}
                  </td>
                  <td className="px-2 py-1.5 text-right whitespace-nowrap">
                    {trade.pnl != null ? (
                      <>
                        <span
                          className={clsx(
                            "font-mono font-medium",
                            pnlPositive ? "text-emerald-400" : "text-red-400"
                          )}
                        >
                          {formatCurrency(trade.pnl)}
                        </span>
                        <span
                          className={clsx(
                            "text-xs font-mono ml-1",
                            pnlPositive ? "text-emerald-400/70" : "text-red-400/70"
                          )}
                        >
                          {formatPercent(trade.pnl_pct ?? 0)}
                        </span>
                      </>
                    ) : (
                      <span className="text-zinc-500">---</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-xs text-zinc-400 truncate">
                    {trade.reason}
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    <span
                      className={clsx(
                        "text-[10px] font-medium px-1.5 py-0.5 rounded uppercase",
                        trade.status === "OPEN"
                          ? "bg-blue-500/10 text-blue-400"
                          : trade.status === "CLOSED"
                          ? "bg-zinc-500/10 text-zinc-400"
                          : "bg-yellow-500/10 text-yellow-400"
                      )}
                    >
                      {trade.status}
                    </span>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

export function TradeHistory() {
  const { liveTrades, replayTrades, loading } = useTrades();
  const tab = useAppStore((s) => s.tradeHistoryTab);
  const setTab = useAppStore((s) => s.setTradeHistoryTab);

  const activeTrades = tab === "live" ? liveTrades : replayTrades;

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex items-center border-b border-zinc-800 bg-zinc-900/50">
        <button
          onClick={() => setTab("live")}
          className={clsx(
            "px-4 py-2 text-xs font-medium uppercase tracking-wider transition-colors",
            tab === "live"
              ? "text-zinc-200 border-b-2 border-emerald-500"
              : "text-zinc-500 hover:text-zinc-400"
          )}
        >
          Live ({liveTrades.length})
        </button>
        <button
          onClick={() => setTab("backtest")}
          className={clsx(
            "px-4 py-2 text-xs font-medium uppercase tracking-wider transition-colors",
            tab === "backtest"
              ? "text-zinc-200 border-b-2 border-blue-500"
              : "text-zinc-500 hover:text-zinc-400"
          )}
        >
          Backtest ({replayTrades.length})
        </button>
      </div>

      <TradeTable trades={activeTrades} loading={loading && tab === "live"} />
    </div>
  );
}
