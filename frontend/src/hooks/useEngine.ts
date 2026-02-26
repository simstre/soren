"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { useWebSocket } from "./useWebSocket";
import { useAppStore } from "@/stores/appStore";
import type { EngineStatus, WSMessage } from "@/lib/types";

export function useEngine() {
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const setEngineRunning = useAppStore((s) => s.setEngineRunning);

  const fetchStatus = useCallback(() => {
    return api.getEngineStatus().then((s) => {
      setStatus(s);
      setEngineRunning(s.running);
    });
  }, [setEngineRunning]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleWS = useCallback(
    (msg: WSMessage) => {
      if (msg.type === "engine_state") {
        const s = msg.payload as unknown as EngineStatus;
        setStatus(s);
        setEngineRunning(s.running);
      }
    },
    [setEngineRunning]
  );

  useWebSocket(handleWS);

  const start = useCallback(async (strategy: string, tickers?: string[]) => {
    setLoading(true);
    try {
      await api.startEngine(strategy, tickers);
      await fetchStatus();
    } finally {
      setLoading(false);
    }
  }, [fetchStatus]);

  const stop = useCallback(async () => {
    setLoading(true);
    try {
      await api.stopEngine();
      await fetchStatus();
    } finally {
      setLoading(false);
    }
  }, [fetchStatus]);

  return { status, loading, start, stop };
}
