"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { useWebSocket } from "./useWebSocket";
import type { CandleData, WSMessage } from "@/lib/types";

/** Merge a candle into the array: update-in-place if same timestamp, else append. */
function mergeCandle(prev: CandleData[], candle: CandleData): CandleData[] {
  if (prev.length === 0) return [candle];

  const last = prev[prev.length - 1];
  if (last.timestamp === candle.timestamp) {
    // Update the current bar in-place
    const updated = [...prev];
    updated[updated.length - 1] = candle;
    return updated;
  }

  // New bar — append (with cap)
  if (prev.length > 2000) {
    return [...prev.slice(-1500), candle];
  }
  return [...prev, candle];
}

/** Merge multiple candles (e.g. from a poll) into the existing array. */
function mergeCandles(prev: CandleData[], incoming: CandleData[]): CandleData[] {
  if (incoming.length === 0) return prev;

  const existingTimes = new Set(prev.map((c) => c.timestamp));
  let result = prev;

  for (const candle of incoming) {
    const last = result[result.length - 1];
    if (last && last.timestamp === candle.timestamp) {
      // Update current bar
      result = [...result.slice(0, -1), candle];
    } else if (!existingTimes.has(candle.timestamp)) {
      // Genuinely new bar
      result = [...result, candle];
    }
  }

  if (result.length > 2000) {
    return result.slice(-1500);
  }
  return result;
}

const POLL_INTERVAL_MS = 5_000; // poll every 5s for live chart updates

export function useCandles(symbol: string, timeframe = "5m") {
  const [candles, setCandles] = useState<CandleData[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const noMoreDataRef = useRef(false);
  const loadingMoreRef = useRef(false);
  const candlesRef = useRef(candles);
  const prevSymbolRef = useRef<string>("");
  const prevTimeframeRef = useRef<string>("");

  // Keep refs in sync via effects
  useEffect(() => {
    candlesRef.current = candles;
  }, [candles]);

  // Reset when symbol/timeframe changes — skip re-fetch if same symbol+timeframe with data
  useEffect(() => {
    if (
      prevSymbolRef.current === symbol &&
      prevTimeframeRef.current === timeframe &&
      candlesRef.current.length > 0
    ) {
      setLoading(false);
      return;
    }
    prevSymbolRef.current = symbol;
    prevTimeframeRef.current = timeframe;

    let cancelled = false;
    noMoreDataRef.current = false;

    api
      .getCandles(symbol, timeframe)
      .then((data) => {
        if (!cancelled) {
          setCandles(data);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe]);

  // Poll for latest candles so the chart stays live for any ticker
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        // Fetch the latest 2 candles to get current + potentially new bar
        const latest = await api.getCandles(symbol, timeframe, 2, undefined, true);
        if (latest.length > 0) {
          setCandles((prev) => mergeCandles(prev, latest));
        }
      } catch {
        // Silently ignore poll failures
      }
    }, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [symbol, timeframe]);

  // Load older candles — stable callback using refs
  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || noMoreDataRef.current) return;

    const earliest = candlesRef.current[0]?.timestamp;
    if (!earliest) return;

    loadingMoreRef.current = true;
    setLoadingMore(true);
    try {
      const olderCandles = await api.getCandles(symbol, timeframe, 200, earliest);

      if (olderCandles.length === 0) {
        noMoreDataRef.current = true;
        return;
      }

      setCandles((prev) => {
        const existingTimes = new Set(prev.map((c) => c.timestamp));
        const newCandles = olderCandles.filter((c) => !existingTimes.has(c.timestamp));

        if (newCandles.length === 0) {
          noMoreDataRef.current = true;
          return prev;
        }

        return [...newCandles, ...prev];
      });
    } finally {
      loadingMoreRef.current = false;
      setLoadingMore(false);
    }
  }, [symbol, timeframe]);

  const handleWS = useCallback(
    (msg: WSMessage) => {
      if (msg.type === "candle_update" && msg.payload.symbol === symbol) {
        const candle = msg.payload as unknown as CandleData;
        setCandles((prev) => mergeCandle(prev, candle));
      }

      // Replay candle updates — append to chart during backtest
      if (msg.type === "replay_candle" && msg.payload.symbol === symbol) {
        const candle = msg.payload as unknown as CandleData;
        setCandles((prev) => mergeCandle(prev, candle));
      }

      // Clear chart when a new replay starts
      if (msg.type === "replay_state") {
        const state = msg.payload as { running: boolean; bar_index: number };
        if (state.running && state.bar_index <= 1) {
          setCandles([]);
        }
      }
    },
    [symbol]
  );

  useWebSocket(handleWS);

  return { candles, loading, loadingMore, loadMore, setCandles };
}
