export interface Trade {
  id: number;
  symbol: string;
  side: "BUY" | "SELL";
  quantity: number;
  entry_price: number;
  exit_price: number | null;
  entry_time: string;
  exit_time: string | null;
  stop_loss: number;
  profit_target: number;
  pnl: number | null;
  pnl_pct: number | null;
  reason: string;
  exit_reason?: string;
  status: "OPEN" | "CLOSED" | "CANCELLED";
  ib_order_id: string | null;
  signal_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface Position {
  symbol: string;
  quantity: number;
  avg_cost: number;
  current_price: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  market_value: number;
}

export interface CandleData {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface PerformanceMetrics {
  starting_balance: number;
  current_balance: number;
  total_pnl: number;
  total_pnl_pct: number;
  pnl_1d: number;
  pnl_1w: number;
  pnl_1m: number;
  pnl_1y: number;
  pnl_ytd: number;
  win_rate: number;
  long_win_rate: number;
  short_win_rate: number;
  long_pnl: number;
  short_pnl: number;
  total_trades: number;
  open_positions: number;
  largest_win: number;
  largest_loss: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
}

export interface EngineStatus {
  running: boolean;
  started_at: string | null;
  active_tickers: string[];
  uptime_seconds: number;
  timeframe: string;
}

export interface ScannerPreset {
  id: string;
  name: string;
  description: string;
}

export interface ScannerResult {
  symbol: string;
  name: string;
  price: number;
  volume: number;
  change_pct: number;
}

export interface SRLevel {
  price: number;
  type: "support" | "resistance";
  strength: number;
  touches: number;
}

export interface ChannelData {
  direction: "ascending" | "descending" | "horizontal";
  confidence: number;
  upper_slope: number;
  upper_intercept: number;
  lower_slope: number;
  lower_intercept: number;
  start_idx: number;
  end_idx: number;
  start_time: string | null;
  end_time: string | null;
}

export interface PatternData {
  type: string;
  confidence: number;
  direction: "bullish" | "bearish";
  target_price: number;
  invalidation_price: number;
  description: string;
  key_points: { idx: number; price: number; time: string | null }[];
}

export interface AnalysisData {
  symbol: string;
  sr_levels: SRLevel[];
  channels: ChannelData[];
  patterns: PatternData[];
  current_price: number;
}

export interface WSMessage {
  type: string;
  payload: Record<string, unknown>;
  timestamp: string;
}

// Strategy types
export interface Strategy {
  id: string;
  name: string;
  description: string;
  entry_rule: string;
  timeframe: string;
  position_size_pct: number;
  stop_loss_pct: number;
  max_exposure_pct: number;
}

// Replay / Backtest types
export interface ReplayStatus {
  running: boolean;
  paused: boolean;
  progress: number;
  speed: number;
  bar_index: number;
  total_bars: number;
  symbol: string;
  timeframe: string;
}

export interface ReplayResults {
  trades: Trade[];
  equity_curve: { timestamp: string; balance: number; bar_index: number }[];
  total_pnl: number;
  total_pnl_pct: number;
  win_rate: number;
  long_win_rate: number;
  short_win_rate: number;
  long_pnl: number;
  short_pnl: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  max_drawdown: number;
  max_drawdown_pct: number;
  starting_balance: number;
  ending_balance: number;
}
