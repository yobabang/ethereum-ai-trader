/** API client for freqtrade REST API + AI endpoints. */

const BASE = "/api/v1";

export interface BotStatus {
  state: string;
  equity: number;
  daily_pnl: number;
  daily_pnl_pct: number;
  open_positions: number;
  ai_status: string;
  ai_last_train: string;
  adaptive_confidence: number;
  adaptive_position_scalar: number;
  per_trade_max_loss: number;
  current_regime: string;
  allowed_to_trade: boolean;
}

export interface Position {
  pair: string;
  side: "long" | "short";
  amount: number;
  entry_price: number;
  current_price: number;
  pnl: number;
  pnl_pct: number;
  stop_loss: number;
  leverage: number;
}

export interface Trade {
  id: number;
  pair: string;
  side: string;
  entry_date: string;
  exit_date: string | null;
  entry_price: number;
  exit_price: number | null;
  amount: number;
  profit: number | null;
  profit_pct: number | null;
  exit_reason: string | null;
}

export interface EquityPoint {
  date: string;
  equity: number;
}

export interface AiDecision {
  action: string;
  reason: string;
  confidence: number;
  expected_return: number;
  position_size_pct: number;
  stop_loss_pct: number;
  take_profit_pct: number;
  leverage: number;
  timestamp: string;
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status}: ${res.statusText}`);
  return res.json();
}

export async function getStatus(): Promise<BotStatus> {
  // Freqtrade status endpoint + AI extensions
  try {
    const data = await fetchJson<any>(`${BASE}/status`);
    const equity = data?.account_balance ?? 0;
    const state = data?.state ?? "stopped";

    return {
      state,
      equity,
      daily_pnl: 0,
      daily_pnl_pct: 0,
      open_positions: data?.open_trades ?? 0,
      ai_status: "ready",
      ai_last_train: "--",
      adaptive_confidence: data?.adaptive_confidence_threshold ?? 0.55,
      adaptive_position_scalar: data?.adaptive_position_scalar ?? 1.0,
      per_trade_max_loss: 0.08,
      current_regime: data?.current_regime ?? "TRENDING_WEAK",
      allowed_to_trade: data?.allowed_to_trade ?? true,
    };
  } catch {
    // Return mock data when backend is unavailable (dev mode)
    return {
      state: "running",
      equity: 52380,
      daily_pnl: 1240,
      daily_pnl_pct: 2.43,
      open_positions: 2,
      ai_status: "学习完成",
      ai_last_train: "4h前",
      adaptive_confidence: 0.55,
      adaptive_position_scalar: 1.0,
      per_trade_max_loss: 0.08,
      current_regime: "TRENDING_WEAK",
      allowed_to_trade: true,
    };
  }
}

export async function getPositions(): Promise<Position[]> {
  try {
    const data = await fetchJson<any[]>(`${BASE}/positions`);
    return data.map((p: any) => ({
      pair: p.pair,
      side: p.is_short ? "short" : "long",
      amount: p.amount,
      entry_price: p.open_rate,
      current_price: p.current_rate ?? p.open_rate,
      pnl: p.profit_ratio ?? 0,
      pnl_pct: (p.profit_ratio ?? 0) * 100,
      stop_loss: p.stop_loss ?? 0,
      leverage: p.leverage ?? 1,
    }));
  } catch {
    return [
      { pair: "BTC/USDT:USDT", side: "long", amount: 0.08, entry_price: 4120, current_price: 4440, pnl: 320, pnl_pct: 3.2, stop_loss: 4020, leverage: 3 },
      { pair: "ETH/USDT:USDT", side: "short", amount: 1.5, entry_price: 3800, current_price: 3715, pnl: -85, pnl_pct: -0.85, stop_loss: 3920, leverage: 3 },
    ];
  }
}

export async function getTrades(): Promise<Trade[]> {
  try {
    const data = await fetchJson<any>(`${BASE}/trades?limit=20`);
    return data.trades ?? [];
  } catch {
    return [
      { id: 1, pair: "ETH/USDT:USDT", side: "short", entry_date: "2026-06-28T14:32:00", exit_date: null, entry_price: 3800, exit_price: null, amount: 1.5, profit: null, profit_pct: null, exit_reason: null },
      { id: 2, pair: "BTC/USDT:USDT", side: "long", entry_date: "2026-06-28T10:15:00", exit_date: "2026-06-28T12:30:00", entry_price: 60200, exit_price: 60880, amount: 0.05, profit: 680, profit_pct: 4.8, exit_reason: "ai_reversal" },
      { id: 3, pair: "BTC/USDT:USDT", side: "long", entry_date: "2026-06-28T06:48:00", exit_date: "2026-06-28T09:20:00", entry_price: 59800, exit_price: 60200, amount: 0.05, profit: 400, profit_pct: 3.2, exit_reason: "ai_take_profit" },
    ];
  }
}

export async function getEquityHistory(): Promise<EquityPoint[]> {
  try {
    return await fetchJson<EquityPoint[]>(`${BASE}/equity`);
  } catch {
    // Generate mock 7-day equity curve
    const points: EquityPoint[] = [];
    let equity = 50000;
    for (let i = 168; i >= 0; i--) {
      const d = new Date(Date.now() - i * 3600000);
      equity += (Math.random() - 0.48) * 300;
      points.push({ date: d.toISOString(), equity: Math.round(equity) });
    }
    return points;
  }
}

export async function getAiDecision(): Promise<AiDecision | null> {
  try {
    return await fetchJson<AiDecision>(`${BASE}/ai/decision`);
  } catch {
    return {
      action: "LONG",
      reason: "TRENDING_STRONG | expected_return=0.0150 | confidence=0.75 | size=15% | SL=1.5% | TP=3.8% | lev=3x",
      confidence: 0.75,
      expected_return: 0.015,
      position_size_pct: 0.15,
      stop_loss_pct: 0.015,
      take_profit_pct: 0.038,
      leverage: 3,
      timestamp: new Date().toISOString(),
    };
  }
}

export async function sendControl(action: "start" | "stop"): Promise<void> {
  await fetch(`${BASE}/control`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
}
