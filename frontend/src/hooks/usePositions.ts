"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { useWebSocket } from "./useWebSocket";
import { useAppStore } from "@/stores/appStore";
import type { Position, WSMessage } from "@/lib/types";

export function usePositions() {
  const [livePositions, setLivePositions] = useState<Position[]>([]);
  const [replayPositions, setReplayPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const replayMode = useAppStore((s) => s.replayMode);

  useEffect(() => {
    let cancelled = false;
    api.getPositions().then((data) => {
      if (!cancelled) {
        setLivePositions(data);
        setLoading(false);
      }
    }).catch(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  const handleWS = useCallback((msg: WSMessage) => {
    // Live position updates
    if (msg.type === "position_update") {
      const position = msg.payload as unknown as Position;
      setLivePositions((prev) => {
        const idx = prev.findIndex((p) => p.symbol === position.symbol);
        if (idx >= 0) {
          const updated = [...prev];
          updated[idx] = position;
          return updated;
        }
        return [...prev, position];
      });
    }

    // Replay position updates
    if (msg.type === "replay_positions") {
      const positions = msg.payload as unknown as { positions: Position[] };
      setReplayPositions(positions.positions);
    }

    // Clear replay positions when a new replay starts
    if (msg.type === "replay_state") {
      const state = msg.payload as { running: boolean; bar_index: number };
      if (state.running && state.bar_index <= 1) {
        setReplayPositions([]);
      }
    }

    // Clear replay positions when replay completes
    if (msg.type === "replay_complete") {
      setReplayPositions([]);
    }
  }, []);

  useWebSocket(handleWS);

  const positions = replayMode ? replayPositions : livePositions;

  return { positions, loading: loading && !replayMode };
}
