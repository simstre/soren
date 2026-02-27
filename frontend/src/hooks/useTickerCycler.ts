"use client";

import { useEffect, useRef, useCallback } from "react";
import { useWebSocket } from "./useWebSocket";
import { useAppStore } from "@/stores/appStore";
import type { WSMessage, Trade } from "@/lib/types";

const CYCLE_INTERVAL_MS = 15_000;

/**
 * Auto-cycles through active tickers while the engine is running.
 * Snaps immediately to a ticker when a trade_opened message arrives,
 * then resumes cycling after 15s.
 *
 * Returns a callback for the ChartContainer to handle trade_opened markers.
 */
export function useTickerCycler(onTradeOpened?: (trade: Trade) => void) {
  const engineRunning = useAppStore((s) => s.engineRunning);
  const activeTickers = useAppStore((s) => s.activeTickers);
  const setSelectedTicker = useAppStore((s) => s.setSelectedTicker);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearCycleInterval = useCallback(() => {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const startCycleInterval = useCallback(() => {
    clearCycleInterval();
    intervalRef.current = setInterval(() => {
      const { activeTickers: tickers, selectedTicker } = useAppStore.getState();
      if (tickers.length === 0) return;
      const idx = tickers.indexOf(selectedTicker);
      const next = tickers[(idx + 1) % tickers.length];
      setSelectedTicker(next);
    }, CYCLE_INTERVAL_MS);
  }, [clearCycleInterval, setSelectedTicker]);

  // Start/stop cycling based on engine state
  useEffect(() => {
    if (engineRunning && activeTickers.length > 0) {
      startCycleInterval();
    } else {
      clearCycleInterval();
    }
    return clearCycleInterval;
  }, [engineRunning, activeTickers, startCycleInterval, clearCycleInterval]);

  // Listen for trade_opened WS messages — snap to that ticker and reset timer
  const handleWS = useCallback(
    (msg: WSMessage) => {
      if (msg.type !== "trade_opened") return;
      const trade = msg.payload as unknown as Trade;

      // Snap chart to the traded ticker
      setSelectedTicker(trade.symbol);

      // Reset cycle timer so user gets a full 15s to view the trade
      const { engineRunning: running, activeTickers: tickers } = useAppStore.getState();
      if (running && tickers.length > 0) {
        startCycleInterval();
      }

      // Forward to ChartContainer for marker rendering
      onTradeOpened?.(trade);
    },
    [setSelectedTicker, startCycleInterval, onTradeOpened]
  );

  useWebSocket(handleWS);
}
