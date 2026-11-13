export type Mode = "paper" | "live";
export type Side = "UP" | "DOWN";

export interface BookSnapshot {
  token_id: string;
  market_id: string;
  side: Side;
  best_bid: number | null;
  best_ask: number | null;
  bid_size: number | null;
  ask_size: number | null;
  updated_at_ms: number;
  age_ms: number | null;
  source: string;
}

export interface MarketSnapshot {
  market_id: string;
  slug: string;
  event_slug: string;
  window_start: string;
  window_end: string;
  remaining_seconds: number;
  duration_seconds: number;
  up_token_id: string;
  down_token_id: string;
  status: string;
}

export interface StrategyState {
  name: string;
  in_window: boolean;
  remaining_seconds: number | null;
  up_ask: number | null;
  down_ask: number | null;
  up_armed: boolean;
  down_armed: boolean;
  reason: string;
  trigger_threshold: number;
  final_window_seconds: number;
  stage2_seconds: number;
  stage2_threshold: number;
  stage3_seconds: number;
  stage3_threshold: number;
  stage0_enabled: boolean;
  stage0_start_seconds: number;
  stage0_min_ask: number;
  stage0_max_ask: number;
  stage0_min_price_gap_usd: number;
  stage0_sell_price: number;
  stage0_mid_from_ask: number;
  stage0_high_from_ask: number;
  stage0_sell_price_low: number;
  stage0_sell_price_mid: number;
  stage1_sell_enabled: boolean;
  stage1_sell_min_buy: number;
  stage1_sell_max_buy: number;
  stage1_sell_price: number;
  conflict_policy: string;
  duplicate_policy: string;
}

export interface AccountState {
  mode: Mode;
  balance: number;
  starting_balance: number;
  realized_pnl: number;
  unrealized_pnl: number;
  daily_pnl: number;
  trades_today: number;
  positions_awaiting_settlement?: number;
  pending_redeems?: number;
}

export interface PositionRow {
  id: number;
  market_id: string;
  side: Side;
  size: number;
  avg_price: number;
  cost_basis: number;
  fees: number;
  unrealized_pnl: number;
  realized_pnl: number;
  status: string;
  resolution: string | null;
  awaiting_settlement?: boolean;
  opened_at: string | null;
  closed_at: string | null;
}

export interface OrderRow {
  id: number;
  market_id: string;
  side: Side;
  stage?: number;
  action?: "BUY" | "SELL";
  status: string;
  requested_notional: number;
  requested_price: number;
  submitted_price: number | null;
  filled_size: number;
  avg_fill_price: number | null;
  fee: number;
  exchange_order_id: string | null;
  error?: string | null;
  reason?: string;
  result?: "WIN" | "LOSS" | "OPEN" | null;
  result_pnl?: number | null;
  created_at: string;
}

export interface LastOrder {
  order_id: number | null;
  side: Side;
  status: string;
  reason: string;
  filled_size: number;
  avg_price: number | null;
  exchange_order_id: string | null;
  error: string | null;
  ts?: string;
}

export interface LiveReadiness {
  live_enabled: boolean;
  geo_verified: boolean;
  credentials_present: boolean;
  funder_configured: boolean;
  ready: boolean;
  reasons: string[];
  balance: number | null;
}

export interface BtcState {
  price: number | null;
  price_to_beat: number | null;
  price_to_beat_source: string | null;
  price_source?: string | null;
  diff: number | null;
  age_ms: number | null;
  stale: boolean;
  connected: boolean;
  enabled: boolean;
  last_error: string | null;
  ticks?: number;
  reconnects?: number;
  ws_mode?: number;
  price_to_beat_error?: string | null;
}

export interface Snapshot {
  server_time: string;
  bot: {
    status: "RUNNING" | "STOPPED";
    mode: Mode;
    kill_switch: boolean;
    started_at: string | null;
    risk_halt: string | null;
  };
  market: MarketSnapshot | null;
  books: { up: BookSnapshot | null; down: BookSnapshot | null };
  strategy: StrategyState;
  account: AccountState;
  positions: PositionRow[];
  open_orders: OrderRow[];
  last_order: LastOrder | null;
  market_data: {
    connected: boolean;
    reconnects: number;
    last_message_ms: number | null;
    last_error: string | null;
  };
  btc?: BtcState;
  live: LiveReadiness;
  config: Record<string, number | string | boolean | null>;
}

export interface Analytics {
  mode: Mode;
  total_trades: number;
  closed_trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  total_pnl: number;
  realized_pnl: number;
  unrealized_pnl: number;
  avg_entry_price: number | null;
  fees_paid: number;
  volume_usdc: number;
}

export interface TriggerRow {
  id: number;
  market_id: string;
  side: Side;
  best_ask: number;
  ask_size: number | null;
  remaining_seconds: number;
  threshold: number;
  triggered: boolean;
  decision: string;
  reason: string;
  created_at: string;
}

export interface LogRow {
  id?: number;
  level: string;
  category: string;
  message: string;
  payload?: Record<string, unknown> | null;
  mode?: string | null;
  created_at: string;
}

export interface SettingsState {
  trigger_threshold: number;
  final_window_seconds: number;
  stage2_seconds: number;
  stage2_threshold: number;
  stage3_seconds: number;
  stage3_threshold: number;
  max_execution_price: number;
  trade_amount_usdc: number;
  max_daily_loss: number;
  daily_profit_target: number;
  stale_data_max_age_ms: number;
  duplicate_policy: string;
  conflict_policy: string;
  paper_slippage_bps: number;
  paper_fee_rate: number;
  max_trades_per_day: number;
  max_open_positions: number;
  min_price_gap_usd: number;
  stage2_min_price_gap_usd: number;
  stage3_min_price_gap_usd: number;
  stage0_enabled: boolean;
  stage0_start_seconds: number;
  stage0_min_ask: number;
  stage0_max_ask: number;
  stage0_min_price_gap_usd: number;
  stage0_sell_price: number;
  stage0_mid_from_ask: number;
  stage0_high_from_ask: number;
  stage0_sell_price_low: number;
  stage0_sell_price_mid: number;
  stage1_sell_enabled: boolean;
  stage1_sell_min_buy: number;
  stage1_sell_max_buy: number;
  stage1_sell_price: number;
  paper_fill_ratio: number;
  paper_fee_exponent: number;
  paper_starting_balance: number;
  live_trading_enabled: boolean;
  geo_verified: boolean;
}
