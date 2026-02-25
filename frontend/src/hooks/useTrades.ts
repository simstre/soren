"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { useWebSocket } from "./useWebSocket";
import type { Trade, WSMessage } from "@/lib/types";

export function useTrades() {
  const [liveTrades, setLiveTrades] = useState<Trade[]>([]);
  const [replayTrades, setReplayTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);

  // Fetch live trades on mount
  useEffect(() => {
    let cancelled = false;
    api.getTrades().then((data) => {
      if (!cancelled) {
        setLiveTrades(data);
        setLoading(false);
      }
    }).catch(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  const handleWS = useCallback((msg: WSMessage) => {
    // Live trades
    if (msg.type === "trade_opened" || msg.type === "trade_closed") {
      const trade = msg.payload as unknown as Trade;
      setLiveTrades((prev) => {
        const idx = prev.findIndex((t) => t.id === trade.id);
        if (idx >= 0) {
          const updated = [...prev];
          updated[idx] = trade;
          return updated;
        }
        return [trade, ...prev];
      });
    }

    // Replay/backtest trades
    if (msg.type === "replay_trade") {
      const trade = msg.payload as unknown as Trade;
      setReplayTrades((prev) => {
        const idx = prev.findIndex((t) => t.id === trade.id);
        if (idx >= 0) {
          const updated = [...prev];
          updated[idx] = trade;
          return updated;
        }
        return [trade, ...prev];
      });
    }

    // Clear replay trades when a new replay starts
    if (msg.type === "replay_state") {
      const state = msg.payload as { running: boolean; bar_index: number };
      if (state.running && state.bar_index <= 1) {
        setReplayTrades([]);
      }
    }
  }, []);

  useWebSocket(handleWS);

  return { liveTrades, replayTrades, loading };
}
