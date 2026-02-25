"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { createChart, createTextWatermark, createSeriesMarkers, ColorType, CandlestickSeries, type IChartApi, type ISeriesApi, type LogicalRange, type SeriesMarker, type Time } from "lightweight-charts";
import { useCandles } from "@/hooks/useCandles";
import { useAnalysis } from "@/hooks/useAnalysis";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useAppStore } from "@/stores/appStore";
import { api } from "@/lib/api";
import { ChartToolbar } from "./ChartToolbar";
import { TickerSearch } from "./TickerSearch";
import { applyOverlays, clearOverlays, type OverlayHandles } from "./ChartOverlays";
import type { WSMessage, Trade, CandleData } from "@/lib/types";

/** Get the local timezone abbreviation (e.g. "EST", "PST", "CET"). */
function getTimezoneAbbr(): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", { timeZoneName: "short" }).formatToParts(new Date());
    return parts.find((p) => p.type === "timeZoneName")?.value ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  }
}

/** Local UTC offset in seconds (e.g. EST = -5h = -18000). */
function getLocalUtcOffsetSeconds(): number {
  return -new Date().getTimezoneOffset() * 60;
}

const TZ_ABBR = getTimezoneAbbr();

export function ChartContainer() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const isInitialLoadRef = useRef(true);
  const loadMoreRef = useRef<(() => void) | null>(null);

  const selectedTicker = useAppStore((s) => s.selectedTicker);
  const timeframe = useAppStore((s) => s.timeframe);
  const { candles, loading, loadingMore, loadMore } = useCandles(selectedTicker, timeframe);
  const analysis = useAnalysis(selectedTicker, timeframe);
  const overlayHandlesRef = useRef<OverlayHandles | null>(null);
  const tradeMarkersRef = useRef<SeriesMarker<Time>[]>([]);
  const tradeMarkerHandleRef = useRef<{ setMarkers: (m: SeriesMarker<Time>[]) => void } | null>(null);
  const replayMode = useAppStore((s) => s.replayMode);
  const selectedTradeId = useAppStore((s) => s.selectedTradeId);
  const [companyName, setCompanyName] = useState("");
  const prevCandleCountRef = useRef(0);
  const autoScrollRef = useRef(true);

  // Fetch company name when ticker changes
  useEffect(() => {
    let cancelled = false;
    setCompanyName(""); // eslint-disable-line react-hooks/set-state-in-effect -- reset on dependency change
    api.getTickerInfo(selectedTicker).then((info) => {
      if (!cancelled) setCompanyName(info.name);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [selectedTicker]);

  // Keep loadMore ref up to date (avoids stale closure in the scroll handler)
  useEffect(() => {
    loadMoreRef.current = loadMore;
  }, [loadMore]);

  // Handle visible range change to trigger loading more data
  const onVisibleLogicalRangeChanged = useCallback((logicalRange: LogicalRange | null) => {
    if (!logicalRange || !seriesRef.current) return;

    const barsInfo = seriesRef.current.barsInLogicalRange(logicalRange);
    if (barsInfo !== null && barsInfo.barsBefore < 50) {
      loadMoreRef.current?.();
    }

    // Disable auto-scroll if user panned away from the right edge
    if (barsInfo !== null && barsInfo.barsAfter > 5) {
      autoScrollRef.current = false;
    } else if (barsInfo !== null && barsInfo.barsAfter <= 2) {
      autoScrollRef.current = true;
    }
  }, []);

  // Create chart on mount
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#09090b" },
        textColor: "#a1a1aa",
        fontSize: 12,
      },
      grid: {
        vertLines: { color: "#27272a" },
        horzLines: { color: "#27272a" },
      },
      crosshair: {
        vertLine: { color: "#52525b", width: 1, style: 2 },
        horzLine: { color: "#52525b", width: 1, style: 2 },
      },
      rightPriceScale: {
        borderColor: "#27272a",
      },
      timeScale: {
        borderColor: "#27272a",
        timeVisible: true,
        secondsVisible: false,
      },
      localization: {
        timeFormatter: (time: number) => {
          const d = new Date(time * 1000);
          return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        },
      },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#10b981",
      downColor: "#ef4444",
      borderUpColor: "#10b981",
      borderDownColor: "#ef4444",
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
    });

    chartRef.current = chart;
    seriesRef.current = series;
    isInitialLoadRef.current = true;

    // Subscribe to scroll/pan events for infinite loading
    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleLogicalRangeChanged);

    const resizeObserver = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      chart.applyOptions({ width, height });
    });
    resizeObserver.observe(chartContainerRef.current);

    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleLogicalRangeChanged);
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [onVisibleLogicalRangeChanged]);

  // Update data when candles change — use incremental update() when possible
  useEffect(() => {
    if (!seriesRef.current || candles.length === 0) return;

    const utcOffset = getLocalUtcOffsetSeconds();
    const toBarData = (c: CandleData) => ({
      time: (Math.floor(new Date(c.timestamp).getTime() / 1000) + utcOffset) as unknown as import("lightweight-charts").Time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    });

    const prevCount = prevCandleCountRef.current;
    const isAppend = candles.length > prevCount && prevCount > 0;
    const isInitial = isInitialLoadRef.current || prevCount === 0;

    if (isInitial || !isAppend) {
      // Full reset: initial load, ticker switch, or data prepend
      const seen = new Set<number>();
      const data = candles
        .map(toBarData)
        .filter((d) => {
          const t = d.time as unknown as number;
          if (seen.has(t)) return false;
          seen.add(t);
          return true;
        })
        .sort((a, b) => (a.time as unknown as number) - (b.time as unknown as number));

      seriesRef.current.setData(data);

      if (isInitialLoadRef.current) {
        chartRef.current?.timeScale().fitContent();
        isInitialLoadRef.current = false;
        autoScrollRef.current = true;
      }
    } else {
      // Incremental: just update the newest candle(s) — much faster
      const newCandles = candles.slice(prevCount - 1); // include last bar for update
      for (const c of newCandles) {
        seriesRef.current.update(toBarData(c));
      }

      // Auto-scroll to keep the latest bar visible during replay
      if (autoScrollRef.current && chartRef.current) {
        chartRef.current.timeScale().scrollToRealTime();
      }
    }

    prevCandleCountRef.current = candles.length;
  }, [candles]);

  // Reset initial load flag when ticker/timeframe changes
  useEffect(() => {
    isInitialLoadRef.current = true;
    prevCandleCountRef.current = 0;
    autoScrollRef.current = true;
    // Clear trade markers for the new ticker
    tradeMarkersRef.current = [];
    if (tradeMarkerHandleRef.current) {
      tradeMarkerHandleRef.current.setMarkers([]);
    }
  }, [selectedTicker, timeframe]);

  // Update watermark when ticker or company name changes
  const watermarkRef = useRef<{ applyOptions: (opts: Record<string, unknown>) => void } | null>(null);
  useEffect(() => {
    if (!chartRef.current) return;
    const pane = chartRef.current.panes()[0];
    if (!pane) return;

    const lines = [
      { text: selectedTicker, color: "rgba(255, 255, 255, 0.12)", fontSize: 120, fontFamily: "ui-monospace, SFMono-Regular, monospace", fontStyle: "" },
    ];
    if (companyName) {
      lines.push({ text: companyName, color: "rgba(255, 255, 255, 0.12)", fontSize: 28, fontFamily: "ui-sans-serif, system-ui, sans-serif", fontStyle: "" });
    }

    if (watermarkRef.current) {
      watermarkRef.current.applyOptions({ lines });
    } else {
      watermarkRef.current = createTextWatermark(pane, { lines }) as unknown as { applyOptions: (opts: Record<string, unknown>) => void };
    }
  }, [selectedTicker, companyName]);

  // Apply technical analysis overlays
  useEffect(() => {
    if (!chartRef.current || !seriesRef.current) return;

    // Clear previous overlays
    if (overlayHandlesRef.current) {
      clearOverlays(chartRef.current, seriesRef.current, overlayHandlesRef.current);
      overlayHandlesRef.current = null;
    }

    if (analysis && candles.length > 0) {
      overlayHandlesRef.current = applyOverlays(
        chartRef.current,
        seriesRef.current,
        analysis,
      );
    }
  }, [analysis, candles]);

  // Replay trade arrows — listen for replay_trade and replay_state via WebSocket
  const handleReplayTradeWS = useCallback(
    (msg: WSMessage) => {
      if (!seriesRef.current) return;

      // Clear markers when a new replay starts
      if (msg.type === "replay_state") {
        const state = msg.payload as { running: boolean; bar_index: number };
        if (state.running && state.bar_index <= 1) {
          tradeMarkersRef.current = [];
          if (tradeMarkerHandleRef.current) {
            tradeMarkerHandleRef.current.setMarkers([]);
          }
        }
        return;
      }

      if (msg.type !== "replay_trade") return;

      const trade = msg.payload as unknown as Trade;
      const store = useAppStore.getState();

      // Auto-switch chart to the traded ticker during replay
      if (store.replayMode && trade.symbol !== store.selectedTicker) {
        // Clear markers from previous ticker's chart
        tradeMarkersRef.current = [];
        if (tradeMarkerHandleRef.current) {
          tradeMarkerHandleRef.current.setMarkers([]);
        }
        store.setSelectedTicker(trade.symbol);
        // New candles will load via useCandles; this trade's marker is added below
      }

      if (trade.symbol !== useAppStore.getState().selectedTicker) return;

      // Remove existing markers for this trade ID (dedup on update)
      const entryId = `entry-${trade.id}`;
      const exitId = `exit-${trade.id}`;
      tradeMarkersRef.current = tradeMarkersRef.current.filter(
        (m) => (m as SeriesMarker<Time> & { id?: string }).id !== entryId &&
               (m as SeriesMarker<Time> & { id?: string }).id !== exitId
      );

      const fmt = (n: number) => n.toFixed(2);

      // Add entry marker
      if (trade.entry_time) {
        const isBuy = trade.side === "BUY";
        const entryTime = (Math.floor(new Date(trade.entry_time).getTime() / 1000) + getLocalUtcOffsetSeconds()) as unknown as Time;

        tradeMarkersRef.current.push({
          time: entryTime,
          position: isBuy ? "belowBar" : "aboveBar",
          color: isBuy ? "#10b981" : "#ef4444",
          shape: isBuy ? "arrowUp" : "arrowDown",
          text: isBuy ? `BUY $${fmt(trade.entry_price)}` : `SELL $${fmt(trade.entry_price)}`,
          id: entryId,
        } as SeriesMarker<Time> & { id: string });
      }

      // Add exit marker if trade is closed
      if (trade.status === "CLOSED" && trade.exit_time && trade.exit_price) {
        const exitTime = (Math.floor(new Date(trade.exit_time).getTime() / 1000) + getLocalUtcOffsetSeconds()) as unknown as Time;
        const pnlPositive = (trade.pnl ?? 0) >= 0;

        tradeMarkersRef.current.push({
          time: exitTime,
          position: trade.side === "BUY" ? "aboveBar" : "belowBar",
          color: pnlPositive ? "#10b981" : "#ef4444",
          shape: "circle",
          text: `EXIT $${fmt(trade.exit_price)}`,
          id: exitId,
        } as SeriesMarker<Time> & { id: string });
      }

      // Sort markers by time (required by lightweight-charts)
      const sorted = [...tradeMarkersRef.current].sort(
        (a, b) => (a.time as number) - (b.time as number)
      );

      // Create or update markers
      if (tradeMarkerHandleRef.current) {
        tradeMarkerHandleRef.current.setMarkers(sorted);
      } else {
        tradeMarkerHandleRef.current = createSeriesMarkers(seriesRef.current, sorted);
      }
    },
    []
  );

  useWebSocket(handleReplayTradeWS);

  // Highlight selected trade markers and scroll chart to its entry time
  const HIGHLIGHT_COLOR = "#facc15"; // yellow
  const prevSelectedRef = useRef<number | null>(null);
  const selectedTrade = useAppStore((s) => s.selectedTrade);

  useEffect(() => {
    if (!seriesRef.current) return;

    type MarkerWithId = SeriesMarker<Time> & { id?: string; _origColor?: string };
    const markers = tradeMarkersRef.current as MarkerWithId[];

    // Restore previous selection's colors
    if (prevSelectedRef.current !== null) {
      const prevEntryId = `entry-${prevSelectedRef.current}`;
      const prevExitId = `exit-${prevSelectedRef.current}`;
      for (const m of markers) {
        if (m.id === prevEntryId || m.id === prevExitId) {
          if (m._origColor) {
            m.color = m._origColor;
            delete m._origColor;
          }
        }
      }
    }

    // Highlight new selection
    if (selectedTradeId !== null && selectedTrade) {
      const entryId = `entry-${selectedTradeId}`;
      const exitId = `exit-${selectedTradeId}`;
      const fmt = (n: number) => n.toFixed(2);

      // If markers for this trade don't exist yet (e.g. after ticker switch), create them
      const hasEntry = markers.some((m) => m.id === entryId);
      if (!hasEntry && selectedTrade.entry_time) {
        const isBuy = selectedTrade.side === "BUY";
        const entryTime = (Math.floor(new Date(selectedTrade.entry_time).getTime() / 1000) + getLocalUtcOffsetSeconds()) as unknown as Time;
        markers.push({
          time: entryTime,
          position: isBuy ? "belowBar" : "aboveBar",
          color: isBuy ? "#10b981" : "#ef4444",
          shape: isBuy ? "arrowUp" : "arrowDown",
          text: isBuy ? `BUY $${fmt(selectedTrade.entry_price)}` : `SELL $${fmt(selectedTrade.entry_price)}`,
          id: entryId,
        } as MarkerWithId);
      }

      const hasExit = markers.some((m) => m.id === exitId);
      if (!hasExit && selectedTrade.status === "CLOSED" && selectedTrade.exit_time && selectedTrade.exit_price) {
        const exitTime = (Math.floor(new Date(selectedTrade.exit_time).getTime() / 1000) + getLocalUtcOffsetSeconds()) as unknown as Time;
        const pnlPositive = (selectedTrade.pnl ?? 0) >= 0;
        markers.push({
          time: exitTime,
          position: selectedTrade.side === "BUY" ? "aboveBar" : "belowBar",
          color: pnlPositive ? "#10b981" : "#ef4444",
          shape: "circle",
          text: `EXIT $${fmt(selectedTrade.exit_price)}`,
          id: exitId,
        } as MarkerWithId);
      }

      let scrollTime: Time | null = null;

      for (const m of markers) {
        if (m.id === entryId || m.id === exitId) {
          if (!m._origColor) m._origColor = m.color;
          m.color = HIGHLIGHT_COLOR;
          if (m.id === entryId) scrollTime = m.time;
        }
      }

      // Scroll chart to the trade's entry
      if (scrollTime !== null && chartRef.current) {
        const ts = scrollTime as unknown as number;
        const timeScale = chartRef.current.timeScale();
        const visibleRange = timeScale.getVisibleLogicalRange();
        if (visibleRange) {
          const barsInView = visibleRange.to - visibleRange.from;
          const allData = seriesRef.current?.data() ?? [];
          let logicalIdx = 0;
          for (let i = 0; i < allData.length; i++) {
            if ((allData[i].time as unknown as number) >= ts) {
              logicalIdx = i;
              break;
            }
            logicalIdx = i;
          }
          timeScale.setVisibleLogicalRange({
            from: logicalIdx - barsInView / 3,
            to: logicalIdx + (barsInView * 2) / 3,
          });
        }
      }
    }

    prevSelectedRef.current = selectedTradeId;

    // Update display
    const sorted = [...markers].sort(
      (a, b) => (a.time as number) - (b.time as number)
    );
    if (tradeMarkerHandleRef.current) {
      tradeMarkerHandleRef.current.setMarkers(sorted);
    } else if (seriesRef.current) {
      tradeMarkerHandleRef.current = createSeriesMarkers(seriesRef.current, sorted);
    }
  }, [selectedTradeId, selectedTrade, candles]);

  return (
    <div className="flex flex-col h-full">
      <ChartToolbar symbol={selectedTicker} />
      <div className="relative flex-1">
        <TickerSearch />
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-zinc-950/80 z-10">
            <div className="text-zinc-400 text-sm">Loading chart data...</div>
          </div>
        )}
        {loadingMore && (
          <div className="absolute top-2 left-2 z-10 bg-zinc-800/80 text-zinc-400 text-xs px-2 py-1 rounded">
            Loading history...
          </div>
        )}
        <div ref={chartContainerRef} className="w-full h-full" />
        {/* Timezone indicator */}
        <div className="absolute bottom-1 left-2 text-[10px] text-zinc-600 pointer-events-none select-none">
          {TZ_ABBR}
        </div>
      </div>
    </div>
  );
}
