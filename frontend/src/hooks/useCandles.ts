"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { useWebSocket } from "./useWebSocket";
import type { CandleData, WSMessage } from "@/lib/types";

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
        setCandles((prev) => {
          if (prev.length > 2000) {
            return [...prev.slice(-1500), candle];
          }
          return [...prev, candle];
        });
      }
    },
    [symbol]
  );

  useWebSocket(handleWS);

  return { candles, loading, loadingMore, loadMore, setCandles };
}
