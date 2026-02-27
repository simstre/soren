const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      // no JSON body
    }
    throw new Error(`API error: ${res.status} — ${detail}`);
  }
  return res.json();
}

export const api = {
  // Trades
  getTrades: (params?: { symbol?: string; status?: string }) => {
    const searchParams = new URLSearchParams();
    if (params?.symbol) searchParams.set("symbol", params.symbol);
    if (params?.status) searchParams.set("status", params.status);
    const qs = searchParams.toString();
    return fetchApi<import("./types").Trade[]>(`/api/trades${qs ? `?${qs}` : ""}`);
  },

  // Positions
  getPositions: () => fetchApi<import("./types").Position[]>("/api/positions"),

  // Metrics
  getMetrics: () => fetchApi<import("./types").PerformanceMetrics>("/api/metrics"),

  // Candles
  getCandles: (symbol: string, timeframe = "5m", limit = 100, end?: string) => {
    let url = `/api/candles/${symbol}?timeframe=${timeframe}&limit=${limit}`;
    if (end) url += `&end=${encodeURIComponent(end)}`;
    return fetchApi<import("./types").CandleData[]>(url);
  },

  // Tickers
  getTickerInfo: (symbol: string) =>
    fetchApi<{ symbol: string; name: string }>(`/api/tickers/${symbol}/info`),

  // Strategies
  getStrategies: () =>
    fetchApi<import("./types").Strategy[]>("/api/strategies/"),

  // Analysis
  getAnalysis: (symbol: string, timeframe = "5m") =>
    fetchApi<import("./types").AnalysisData>(`/api/analysis/${symbol}?timeframe=${timeframe}`),

  // Engine
  getEngineStatus: () => fetchApi<import("./types").EngineStatus>("/api/engine/status"),
  startEngine: (strategy: string, tickers?: string[]) =>
    fetchApi<{ status: string }>("/api/engine/start", {
      method: "POST",
      body: JSON.stringify({ strategy, ...(tickers && tickers.length > 0 ? { tickers } : {}) }),
    }),
  stopEngine: () => fetchApi<{ status: string }>("/api/engine/stop", { method: "POST" }),

  // Scanner
  getScannerPresets: () => fetchApi<import("./types").ScannerPreset[]>("/api/scanner/presets"),
  runScanner: (presetId: string) =>
    fetchApi<import("./types").ScannerResult[]>(`/api/scanner/run?preset_id=${presetId}`, {
      method: "POST",
    }),
  scanFitness: (topN = 10, strategy?: string, scanTactic?: string) =>
    fetchApi<{ symbol: string; total_score: number; price: number; avg_volume: number; atr_pct: number }[]>(
      `/api/scanner/fitness?top_n=${topN}${strategy ? `&strategy=${strategy}` : ""}${scanTactic ? `&scan_tactic=${scanTactic}` : ""}`
    ),

  // Replay
  startReplay: (params: {
    symbols: string[];
    timeframe: string;
    start_date: string;
    end_date: string;
    speed: number;
    strategy: string;
  }) =>
    fetchApi<{ status: string; symbols: string[] }>("/api/replay/start", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  stopReplay: () =>
    fetchApi<{ status: string }>("/api/replay/stop", { method: "POST" }),
  pauseReplay: () =>
    fetchApi<{ status: string }>("/api/replay/pause", { method: "POST" }),
  resumeReplay: () =>
    fetchApi<{ status: string }>("/api/replay/resume", { method: "POST" }),
  setReplaySpeed: (speed: number) =>
    fetchApi<{ status: string; speed: number }>("/api/replay/speed", {
      method: "POST",
      body: JSON.stringify({ speed }),
    }),
  getReplayStatus: () =>
    fetchApi<import("./types").ReplayStatus>("/api/replay/status"),
  getReplayResults: () =>
    fetchApi<import("./types").ReplayResults>("/api/replay/results"),

  // Trading settings (read-only diagnostics)
  getTradingSettings: () =>
    fetchApi<{ max_position_pct: number; max_exposure_pct: number; risk_per_trade_pct: number; stop_loss_pct: number }>(
      "/api/settings/trading"
    ),
};
