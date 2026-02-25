"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { useWebSocket } from "./useWebSocket";
import { useAppStore } from "@/stores/appStore";
import type { ReplayStatus, ReplayResults, WSMessage } from "@/lib/types";

export function useReplay() {
  const [status, setStatus] = useState<ReplayStatus | null>(null);
  const [results, setResults] = useState<ReplayResults | null>(null);
  const [loading, setLoading] = useState(false);
  const setReplayMode = useAppStore((s) => s.setReplayMode);
  const setTradeHistoryTab = useAppStore((s) => s.setTradeHistoryTab);
  const setPerformanceTab = useAppStore((s) => s.setPerformanceTab);

  // Fetch initial status
  useEffect(() => {
    api.getReplayStatus().then(setStatus).catch(() => {});
  }, []);

  const start = useCallback(
    async (params: {
      symbols: string[];
      timeframe: string;
      start_date: string;
      end_date: string;
      speed: number;
      stop_loss_pct: number;
      position_size_pct: number;
      strategy: string;
    }) => {
      setLoading(true);
      setResults(null);
      try {
        await api.startReplay(params);
        const s = await api.getReplayStatus();
        setStatus(s);
        setReplayMode(true);
        setTradeHistoryTab("backtest");
        setPerformanceTab("backtest");
      } finally {
        setLoading(false);
      }
    },
    [setReplayMode, setTradeHistoryTab, setPerformanceTab]
  );

  const stop = useCallback(async () => {
    setLoading(true);
    try {
      await api.stopReplay();
      const s = await api.getReplayStatus();
      setStatus(s);
      try {
        const r = await api.getReplayResults();
        setResults(r);
      } catch {
        // No results yet
      }
    } finally {
      setLoading(false);
      setReplayMode(false);
    }
  }, [setReplayMode]);

  const pause = useCallback(async () => {
    await api.pauseReplay();
    const s = await api.getReplayStatus();
    setStatus(s);
  }, []);

  const resume = useCallback(async () => {
    await api.resumeReplay();
    const s = await api.getReplayStatus();
    setStatus(s);
  }, []);

  const setSpeed = useCallback(async (speed: number) => {
    await api.setReplaySpeed(speed);
    setStatus((prev) => (prev ? { ...prev, speed } : prev));
  }, []);

  const clearResults = useCallback(() => {
    setResults(null);
  }, []);

  // WebSocket listeners
  const handleWS = useCallback(
    (msg: WSMessage) => {
      if (msg.type === "replay_state") {
        setStatus(msg.payload as unknown as ReplayStatus);
      }
      if (msg.type === "replay_complete") {
        const data = msg.payload as { results: ReplayResults };
        setResults(data.results);
        setReplayMode(false);
      }
    },
    [setReplayMode]
  );

  useWebSocket(handleWS);

  return { status, results, loading, start, stop, pause, resume, setSpeed, clearResults };
}
