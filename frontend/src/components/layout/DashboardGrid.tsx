"use client";

import { ChartContainer } from "@/components/chart/ChartContainer";
import { TradingControls } from "@/components/controls/TradingControls";
import { ReplayPanel } from "@/components/controls/ReplayPanel";
import { InfoPanel } from "@/components/metrics/InfoPanel";
import { CurrentPositions } from "@/components/positions/CurrentPositions";
import { TradeHistory } from "@/components/trades/TradeHistory";
export function DashboardGrid() {
  return (
    <div className="flex-1 min-w-0 overflow-hidden flex">
      {/* Center column: chart on top, trade history below — this is the only part that resizes */}
      <div className="flex-1 min-w-0 grid grid-rows-[3fr_2fr] min-h-0">
        {/* Chart */}
        <div className="min-h-0">
          <ChartContainer />
        </div>

        {/* Trade history — full chart width */}
        <div className="border-t border-zinc-800 overflow-hidden min-h-0">
          <TradeHistory />
        </div>
      </div>

      {/* Right column: fixed width, never resizes */}
      <div className="w-[380px] shrink-0 border-l border-zinc-800 flex flex-col min-h-0 overflow-y-auto">
        <TradingControls />
        <ReplayPanel />
        <InfoPanel />
        <CurrentPositions />
      </div>
    </div>
  );
}
