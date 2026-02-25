"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AnalysisData } from "@/lib/types";

export function useAnalysis(symbol: string, timeframe: string) {
  const [analysis, setAnalysis] = useState<AnalysisData | null>(null);

  useEffect(() => {
    let cancelled = false;
    setAnalysis(null); // eslint-disable-line react-hooks/set-state-in-effect -- reset on dependency change
    api
      .getAnalysis(symbol, timeframe)
      .then((data) => {
        if (!cancelled) setAnalysis(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe]);

  return analysis;
}
