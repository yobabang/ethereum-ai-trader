import { useEffect, useState, useCallback } from "react";
import { Dashboard } from "./components/Dashboard";
import { EquityCurve } from "./components/EquityCurve";
import { Positions } from "./components/Positions";
import { TradeHistory } from "./components/TradeHistory";
import { TrainingPanel } from "./components/TrainingPanel";
import { LaunchCheck } from "./components/LaunchCheck";
import { ControlBar } from "./components/ControlBar";
import { LiveKlineChart } from "./components/LiveKlineChart";
import {
  getAccount,
  getPositions,
  getOrders,
  getEquityHistory,
  getAiDecision,
  isAnyMock,
  type AccountSummary,
  type Position,
  type Order,
  type EquityPoint,
  type AiDecision,
} from "./api";

export default function App() {
  const [account, setAccount] = useState<AccountSummary | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [aiDecision, setAiDecision] = useState<AiDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const [acct, pos, ord, eq, ai] = await Promise.all([
        getAccount(),
        getPositions(),
        getOrders(50),
        getEquityHistory(7),
        getAiDecision(),
      ]);
      setAccount(acct);
      setPositions(pos);
      setOrders(ord);
      setEquity(eq);
      setAiDecision(ai);
      setError(null);
    } catch (err) {
      setRetryCount((c) => c + 1);
      if (retryCount > 5) {
        setError("无法连接到交易引擎。请检查 sim_trader 是否运行中。");
      }
    }
  }, [retryCount]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-4 md:p-6 max-w-7xl mx-auto">
      {/* Error Banner */}
      {error && (
        <div className="mb-4 p-3 bg-red-900/40 border border-red-700 rounded-lg flex items-center justify-between">
          <span className="text-red-300 text-sm">{error}</span>
          <button
            onClick={() => { setError(null); setRetryCount(0); refresh(); }}
            className="px-3 py-1 bg-red-800 hover:bg-red-700 rounded text-xs text-white"
          >
            重连
          </button>
        </div>
      )}

      {/* Mock Data Banner — shown when any dataset is mock (backend unavailable) */}
      {isAnyMock(account, positions, orders, equity, aiDecision) && (
        <div className="mb-4 p-3 bg-yellow-900/40 border border-yellow-700 rounded-lg flex items-center gap-2">
          <span className="text-yellow-300 text-sm">⚠️ 模拟数据</span>
          <span className="text-yellow-200/70 text-xs">
            后端未连接，当前显示的是演示数据，非真实交易结果。请启动
            <code className="mx-1 px-1 bg-yellow-950/50 rounded">python engine/api_bridge.py</code>
            获取真实数据。
          </span>
        </div>
      )}

      {/* Header */}
      <header className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">以太 AI 模拟交易平台</h1>
          <p className="text-gray-400 text-sm">
            真实行情 · 虚拟资金 · $1,000 USDT 模拟
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm font-medium bg-green-900/50 text-green-400 border border-green-700">
            <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
            模拟运行中
          </span>
        </div>
      </header>

      {/* Stat Cards */}
      <Dashboard account={account} />

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-4">
        {/* Live K-line Chart — takes 2 columns */}
        <div className="lg:col-span-2">
          <LiveKlineChart pair="BTC/USDT:USDT" />
        </div>

        {/* AI Decision + Training */}
        <div className="space-y-4">
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
              AI 最新决策
            </h2>
            {aiDecision ? (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <span
                    className={`px-2 py-0.5 rounded text-sm font-bold ${
                      aiDecision.action === "LONG"
                        ? "bg-green-900 text-green-400"
                        : aiDecision.action === "SHORT"
                        ? "bg-red-900 text-red-400"
                        : "bg-gray-800 text-gray-400"
                    }`}
                  >
                    {aiDecision.action}
                  </span>
                  <span className="text-sm text-gray-500">
                    置信度 {(aiDecision.confidence * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="text-xs text-gray-400 space-y-1">
                  <p>预期收益: {(aiDecision.expected_return * 100).toFixed(2)}%</p>
                  <p>仓位: {(aiDecision.position_size_pct * 100).toFixed(0)}%</p>
                  <p>止损: {(aiDecision.stop_loss_pct * 100).toFixed(1)}%</p>
                  <p>止盈: {(aiDecision.take_profit_pct * 100).toFixed(1)}%</p>
                  <p>杠杆: {aiDecision.leverage}x</p>
                </div>
                <p className="text-xs text-gray-500 mt-2 italic">
                  {aiDecision.reason}
                </p>
              </div>
            ) : (
              <p className="text-gray-500 text-sm">等待 AI 决策...</p>
            )}
          </div>
          <TrainingPanel />
        </div>
      </div>

      {/* Positions */}
      <div className="mt-4 bg-gray-900 rounded-xl border border-gray-800 p-4">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
          当前持仓
        </h2>
        <Positions positions={positions} />
      </div>

      {/* Trade History + Control Bar */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-4">
        <div className="lg:col-span-2 bg-gray-900 rounded-xl border border-gray-800 p-4">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
            交易记录
          </h2>
          <TradeHistory orders={orders} />
        </div>
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
            系统状态
          </h2>
          <LaunchCheck />
        </div>
      </div>

      {/* Equity Curve */}
      <div className="mt-4 bg-gray-900 rounded-xl border border-gray-800 p-4">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
          权益曲线 (7天)
        </h2>
        <EquityCurve data={equity} />
      </div>
    </div>
  );
}
