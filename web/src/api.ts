/** API client for the simulation trading platform (v0.2.0).
 *
 * Talks to our FastAPI backend (engine/api_bridge.py) at /api/v1/trade/*.
 * Falls back to mock data when the backend is unavailable (dev mode).
 *
 * Mock data is ALWAYS tagged with _source: "mock" so the UI can show a badge
 * — never silently present fake numbers as real (trust is core to this platform).
 */

const BASE = "/api/v1";

// ---------------------------------------------------------------------------
// Types — all carry an optional _source so callers can tell real from mock
// ---------------------------------------------------------------------------

export type DataSource = "real" | "mock";

export interface AccountSummary {
  _source?: DataSource;
  initial_equity: number;
  equity: number;
  balance: number;
  unrealized_pnl: number;
  today_pnl: number;
  today_pnl_pct: number;
  open_positions: number;
  total_trades: number;
  win_rate: number;
  max_drawdown: number;
}

export interface Position {
  _source?: DataSource;
  id: number;
  pair: string;
  side: "long" | "short";
  entry_price: number;
  current_price: number;
  contracts: number;
  margin: number;
  leverage: number;
  sl_price: number;
  tp_price: number;
  unrealized_pnl: number;
  roe_pct: number;
  funding_paid: number;
  entry_time: string;
  ai_confidence: number | null;
  ai_reason: string | null;
  mode: string;
}

export interface Order {
  _source?: DataSource;
  id: number;
  pair: string;
  side: string;
  status: string;
  entry_price: number;
  entry_time: string;
  exit_price: number | null;
  exit_time: string | null;
  exit_reason: string | null;
  contracts: number;
  margin: number;
  leverage: number;
  sl_price: number;
  tp_price: number;
  realized_pnl: number;
  funding_paid: number;
  ai_confidence: number | null;
  ai_reason: string | null;
  mode: string;
}

export interface EquityPoint {
  _source?: DataSource;
  timestamp: string;
  equity: number;
  balance: number;
  unrealized_pnl: number;
  open_positions: number;
}

export interface AiDecision {
  _source?: DataSource;
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

// ---------------------------------------------------------------------------
// Fetch helper — tags real data
// ---------------------------------------------------------------------------

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status}: ${res.statusText}`);
  return res.json();
}

function tagReal<T>(data: T): T {
  return { ...data, _source: "real" as const };
}

function tagRealArray<T extends object>(arr: T[]): T[] {
  return arr.map((x) => ({ ...x, _source: "real" as const }));
}

// ---------------------------------------------------------------------------
// API functions (with mock fallback, mock always tagged)
// ---------------------------------------------------------------------------

export async function getAccount(): Promise<AccountSummary> {
  try {
    const data = await fetchJson<AccountSummary>(`${BASE}/trade/account`);
    return tagReal(data);
  } catch {
    return {
      _source: "mock",
      initial_equity: 1000,
      equity: 1047.5,
      balance: 892.3,
      unrealized_pnl: 155.2,
      today_pnl: 47.5,
      today_pnl_pct: 4.75,
      open_positions: 1,
      total_trades: 23,
      win_rate: 0.56,
      max_drawdown: 0.083,
    };
  }
}

export async function getPositions(): Promise<Position[]> {
  try {
    const data = await fetchJson<{ positions: Position[]; count: number }>(
      `${BASE}/trade/positions`
    );
    return tagRealArray(data.positions ?? []);
  } catch {
    return [
      {
        _source: "mock",
        id: 1, pair: "ETH/USDT:USDT", side: "short",
        entry_price: 3450, current_price: 3395, contracts: 1.2,
        margin: 414, leverage: 3, sl_price: 3520, tp_price: 3380,
        unrealized_pnl: 66, roe_pct: 15.9, funding_paid: -0.5,
        entry_time: new Date(Date.now() - 3600000).toISOString(),
        ai_confidence: 0.72, ai_reason: "downtrend", mode: "trend",
      },
    ];
  }
}

export async function getOrders(limit: number = 50): Promise<Order[]> {
  try {
    const data = await fetchJson<{ orders: Order[]; count: number }>(
      `${BASE}/trade/orders?limit=${limit}`
    );
    return tagRealArray(data.orders ?? []);
  } catch {
    return [
      { _source: "mock", id: 3, pair: "ETH/USDT:USDT", side: "short", status: "closed",
        entry_price: 3480, entry_time: new Date(Date.now() - 7200000).toISOString(),
        exit_price: 3525, exit_time: new Date(Date.now() - 6600000).toISOString(),
        exit_reason: "stop_loss", contracts: 1.0, margin: 348, leverage: 3,
        sl_price: 3525, tp_price: 3380, realized_pnl: -45, funding_paid: 0.2,
        ai_confidence: 0.68, ai_reason: "downtrend", mode: "trend" },
      { _source: "mock", id: 2, pair: "BTC/USDT:USDT", side: "long", status: "closed",
        entry_price: 59200, entry_time: new Date(Date.now() - 10800000).toISOString(),
        exit_price: 59580, exit_time: new Date(Date.now() - 9600000).toISOString(),
        exit_reason: "take_profit", contracts: 0.05, margin: 296, leverage: 3,
        sl_price: 58800, tp_price: 59580, realized_pnl: 38, funding_paid: 0.1,
        ai_confidence: 0.75, ai_reason: "uptrend", mode: "ai" },
    ];
  }
}

export async function getEquityHistory(days: number = 7): Promise<EquityPoint[]> {
  try {
    const data = await fetchJson<{ snapshots: EquityPoint[]; count: number }>(
      `${BASE}/trade/equity?days=${days}`
    );
    return tagRealArray(data.snapshots ?? []);
  } catch {
    // Mock 7-day curve
    const points: EquityPoint[] = [];
    let equity = 1000;
    for (let i = 168; i >= 0; i--) {
      const d = new Date(Date.now() - i * 3600000);
      equity += (Math.random() - 0.48) * 8;
      points.push({
        _source: "mock",
        timestamp: d.toISOString(), equity: Math.round(equity * 100) / 100,
        balance: equity - 50, unrealized_pnl: 50, open_positions: 1,
      });
    }
    return points;
  }
}

export async function getAiDecision(): Promise<AiDecision | null> {
  try {
    const data = await fetchJson<AiDecision>(`${BASE}/ai/decision`);
    return tagReal(data);
  } catch {
    return {
      _source: "mock",
      action: "SHORT",
      reason: "trend_filter: downtrend confirmed | ema9/100 | slope=True",
      confidence: 0.72,
      expected_return: -0.015,
      position_size_pct: 0.12,
      stop_loss_pct: 0.02,
      take_profit_pct: 0.04,
      leverage: 3,
      timestamp: new Date().toISOString(),
    };
  }
}

export async function sendControl(action: "start" | "stop"): Promise<void> {
  // Reserved for future manual control; backend endpoint not yet implemented
  console.log(`Control action (stub): ${action}`);
}

// ---------------------------------------------------------------------------
// Helper: detect if any of the fetched datasets is mock
// ---------------------------------------------------------------------------

export function isAnyMock(
  account: AccountSummary | null,
  positions: Position[],
  orders: Order[],
  equity: EquityPoint[],
  decision: AiDecision | null
): boolean {
  if (account?._source === "mock") return true;
  if (positions.some((p) => p._source === "mock")) return true;
  if (orders.some((o) => o._source === "mock")) return true;
  if (equity.some((e) => e._source === "mock")) return true;
  if (decision?._source === "mock") return true;
  return false;
}
