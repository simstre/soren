"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { useWebSocket } from "./useWebSocket";
import type { PerformanceMetrics, WSMessage } from "@/lib/types";

export function useMetrics() {
  const [metrics, setMetrics] = useState<PerformanceMetrics | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api.getMetrics().then((data) => {
      if (!cancelled) {
        setMetrics(data);
        setLoading(false);
      }
    }).catch(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  const handleWS = useCallback((msg: WSMessage) => {
    if (msg.type === "metrics_update") {
      setMetrics(msg.payload as unknown as PerformanceMetrics);
    }
  }, []);

  useWebSocket(handleWS);

  return { metrics, loading };
}
