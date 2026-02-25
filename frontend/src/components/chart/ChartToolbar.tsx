"use client";

import { useAppStore } from "@/stores/appStore";
import clsx from "clsx";

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1D", "1W"];

interface ChartToolbarProps {
  symbol: string;
}

export function ChartToolbar({ symbol }: ChartToolbarProps) {
  const timeframe = useAppStore((s) => s.timeframe);
  const setTimeframe = useAppStore((s) => s.setTimeframe);

  return (
    <div className="flex items-center justify-between px-4 py-2 border-b border-zinc-800 bg-zinc-900/50">
      <div className="flex items-center gap-3">
        <span className="text-white font-mono font-bold text-lg">{symbol}</span>
      </div>
      <div className="flex items-center gap-1">
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            onClick={() => setTimeframe(tf)}
            className={clsx(
              "px-3 py-1 text-xs font-medium rounded transition-colors",
              timeframe === tf
                ? "bg-emerald-600 text-white"
                : "text-zinc-400 hover:text-white hover:bg-zinc-800"
            )}
          >
            {tf}
          </button>
        ))}
      </div>
    </div>
  );
}
