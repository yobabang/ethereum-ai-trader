import { useEffect, useState } from "react";
import {
  getTradingControl,
  setTradingControl,
  type TradingControl,
  type TradingMode,
  type TradingControlPayload,
} from "../api";

const MODES: { value: TradingMode; label: string; desc: string }[] = [
  { value: "hybrid", label: "Hybrid", desc: "AI 出方向+策略择时，5m 高频高倍（推荐）" },
  { value: "ai", label: "AI", desc: "纯 AI 4 层流水线，1h 周期，保守" },
  { value: "trend", label: "Trend", desc: "EMA9/100 趋势策略，4h 周期" },
  { value: "breakout", label: "Breakout", desc: "Donchian 通道突破，1h" },
  { value: "rl", label: "RL", desc: "PPO 强化学习，1h" },
];

export function TradingControlPanel() {
  const [state, setState] = useState<TradingControl | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const load = async () => setState(await getTradingControl());
  useEffect(() => {
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const flash = (ok: boolean, msg: string) => {
    setToast({ ok, msg });
    setTimeout(() => setToast(null), 3000);
  };

  const apply = async (payload: TradingControlPayload, label: string) => {
    setBusy(true);
    try {
      const r = await setTradingControl(payload);
      flash(true, `${label} — 将在下轮决策生效`);
      await load();
    } catch (e) {
      flash(false, `${label} 失败: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-4 space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
          交易控制
        </h2>
        <p className="text-xs text-gray-600 mt-0.5">
          运行中切换 AI 决策方式。下轮生效（最多 5m 延迟）。
        </p>
      </div>

      {/* Current state display */}
      {state && (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-gray-500">当前:</span>
          <span className="px-2 py-0.5 rounded bg-indigo-900/50 text-indigo-300 font-medium">
            {state.mode ?? "未设置"}
          </span>
          {state.leverage != null && (
            <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-300">{state.leverage}x</span>
          )}
          {state.paused && (
            <span className="px-2 py-0.5 rounded bg-yellow-900/60 text-yellow-300 font-medium">已暂停</span>
          )}
        </div>
      )}

      {/* Mode selection */}
      <div>
        <label className="block text-xs text-gray-500 mb-1.5">决策模式</label>
        <div className="grid grid-cols-5 gap-1">
          {MODES.map((m) => (
            <button
              key={m.value}
              disabled={busy}
              title={m.desc}
              onClick={() => apply({ mode: m.value }, `模式→${m.label}`)}
              className={`py-1.5 rounded text-xs font-medium transition ${
                state?.mode === m.value
                  ? "bg-indigo-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:text-white"
              } disabled:opacity-40`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <p className="text-[10px] text-gray-600 mt-1">
          hybrid=高频高倍(5m/≤50x)，ai=保守低频(1h/≤5x)。鼠标悬停看各模式说明。
        </p>
      </div>

      {/* Leverage */}
      <div>
        <label className="block text-xs text-gray-500 mb-1">
          杠杆 <span className="text-white font-medium">{state?.leverage ?? "—"}x</span>
        </label>
        <input
          type="range"
          min={1}
          max={50}
          step={1}
          defaultValue={state?.leverage ?? 20}
          onMouseUp={(e) => {
            const v = parseInt((e.target as HTMLInputElement).value);
            apply({ leverage: v }, `杠杆→${v}x`);
          }}
          disabled={busy}
          className="w-full accent-indigo-500"
        />
        <p className="text-[10px] text-gray-600 mt-0.5">
          下单杠杆倍数。越高盈亏越放大，50x 下价格波动 2% 即爆仓。
        </p>
      </div>

      {/* Pause / Resume */}
      <button
        disabled={busy}
        onClick={() => apply({ paused: !state?.paused }, state?.paused ? "恢复" : "暂停")}
        className={`w-full py-2 rounded-lg font-bold text-sm transition ${
          state?.paused
            ? "bg-green-700 hover:bg-green-600 text-white"
            : "bg-yellow-700 hover:bg-yellow-600 text-white"
        } disabled:opacity-40`}
      >
        {state?.paused ? "▶ 恢复交易" : "⏸ 暂停开仓"}
      </button>
      <p className="text-[10px] text-gray-600 -mt-2">
        暂停后 AI 不再开新仓，但现有仓位的止损止盈仍正常执行（不平仓）。
      </p>

      {/* Advanced risk params */}
      <div className="border-t border-gray-800 pt-3">
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1"
        >
          {showAdvanced ? "▼" : "▶"} 高级风控参数
        </button>
        <p className="text-[10px] text-gray-600 mt-0.5">改了立即影响下一笔单。不懂别动。</p>

        {showAdvanced && <AdvancedParams state={state} busy={busy} onApply={apply} />}
      </div>

      {/* Toast */}
      {toast && (
        <div
          className={`fixed bottom-4 right-4 px-4 py-2 rounded-lg text-sm z-50 border ${
            toast.ok
              ? "bg-green-900/80 text-green-300 border-green-700"
              : "bg-red-900/80 text-red-300 border-red-700"
          }`}
        >
          {toast.msg}
        </div>
      )}
    </div>
  );
}

function AdvancedParams({
  state,
  busy,
  onApply,
}: {
  state: TradingControl | null;
  busy: boolean;
  onApply: (p: TradingControlPayload, label: string) => void;
}) {
  const [sl, setSl] = useState(state?.stop_loss_pct ?? 0.008);
  const [tp, setTp] = useState(state?.take_profit_pct ?? 0.015);
  const [conf, setConf] = useState(state?.min_confidence ?? 0.50);
  const [pos, setPos] = useState(state?.position_pct ?? 0.30);

  return (
    <div className="mt-2 space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="block text-[10px] text-gray-500">止损 {(sl * 100).toFixed(1)}%</label>
          <input
            type="number" min={0.001} max={0.10} step={0.001} value={sl}
            onChange={(e) => setSl(Math.min(0.10, Math.max(0, parseFloat(e.target.value) || 0)))}
            className="w-full bg-gray-800 text-white text-xs rounded px-2 py-1 border border-gray-700"
          />
        </div>
        <div>
          <label className="block text-[10px] text-gray-500">止盈 {(tp * 100).toFixed(1)}%</label>
          <input
            type="number" min={0.001} max={0.10} step={0.001} value={tp}
            onChange={(e) => setTp(Math.min(0.10, Math.max(0, parseFloat(e.target.value) || 0)))}
            className="w-full bg-gray-800 text-white text-xs rounded px-2 py-1 border border-gray-700"
          />
        </div>
        <div>
          <label className="block text-[10px] text-gray-500">置信阈值 {(conf * 100).toFixed(0)}%</label>
          <input
            type="number" min={0} max={1} step={0.05} value={conf}
            onChange={(e) => setConf(Math.min(1, Math.max(0, parseFloat(e.target.value) || 0)))}
            className="w-full bg-gray-800 text-white text-xs rounded px-2 py-1 border border-gray-700"
          />
        </div>
        <div>
          <label className="block text-[10px] text-gray-500">仓位 {(pos * 100).toFixed(0)}%</label>
          <input
            type="number" min={0.01} max={0.50} step={0.05} value={pos}
            onChange={(e) => setPos(Math.min(0.50, Math.max(0, parseFloat(e.target.value) || 0)))}
            className="w-full bg-gray-800 text-white text-xs rounded px-2 py-1 border border-gray-700"
          />
        </div>
      </div>
      <button
        disabled={busy}
        onClick={() =>
          onApply(
            { stop_loss_pct: sl, take_profit_pct: tp, min_confidence: conf, position_pct: pos },
            "风控参数"
          )
        }
        className="w-full py-1.5 rounded text-xs bg-gray-700 hover:bg-gray-600 text-white disabled:opacity-40"
      >
        应用风控参数
      </button>
    </div>
  );
}
