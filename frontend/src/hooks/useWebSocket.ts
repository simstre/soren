"use client";

import { useEffect } from "react";
import { wsClient } from "@/lib/ws";
import type { WSMessage } from "@/lib/types";

export function useWebSocket(handler?: (msg: WSMessage) => void) {
  useEffect(() => {
    wsClient.connect();

    let unsubscribe: (() => void) | undefined;
    if (handler) {
      unsubscribe = wsClient.subscribe(handler);
    }

    return () => {
      unsubscribe?.();
    };
  }, [handler]);

  return { send: wsClient.send.bind(wsClient) };
}
