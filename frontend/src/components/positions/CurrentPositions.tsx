"use client";

import { usePositions } from "@/hooks/usePositions";
import { formatCurrency, formatPercent } from "@/lib/formatters";
import clsx from "clsx";

export function CurrentPositions() {
  const { positions, loading } = usePositions();

  return (
    <div className="flex-1 flex flex-col min-h-0 border-b border-zinc-800">
      <div className="px-4 py-2 text-xs text-zinc-500 uppercase tracking-wider border-b border-zinc-800">
        Positions ({positions.length})
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="p-4 text-zinc-500 text-sm">Loading...</div>
        ) : positions.length === 0 ? (
          <div className="p-4 text-zinc-600 text-sm">No open positions</div>
        ) : (
          <div className="divide-y divide-zinc-800/50">
            {positions.map((pos) => {
              const positive = pos.unrealized_pnl >= 0;
              return (
                <div key={pos.symbol} className="px-4 py-2.5 hover:bg-zinc-800/30">
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="text-white font-mono text-sm font-medium">
                        {pos.symbol}
                      </span>
                      <span className="text-zinc-500 text-xs ml-2">
                        {pos.quantity} shares @ {formatCurrency(pos.avg_cost)}
                      </span>
                    </div>
                    <div className="text-right">
                      <div
                        className={clsx(
                          "text-sm font-mono font-medium",
                          positive ? "text-emerald-400" : "text-red-400"
                        )}
                      >
                        {formatCurrency(pos.unrealized_pnl)}
                      </div>
                      <div
                        className={clsx(
                          "text-xs font-mono",
                          positive ? "text-emerald-400" : "text-red-400"
                        )}
                      >
                        {formatPercent(pos.unrealized_pnl_pct)}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center justify-between mt-1">
                    <span className="text-xs text-zinc-500">
                      Current: {formatCurrency(pos.current_price)}
                    </span>
                    <span className="text-xs text-zinc-500">
                      Value: {formatCurrency(pos.market_value)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
