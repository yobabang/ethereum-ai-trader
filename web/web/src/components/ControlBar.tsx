import { useState } from "react";
import {
  closeAllPositions,
  placeManualOrder,
  type Position,
  type ManualOrderPayload,
} from "../api";

interface ControlBarProps {
  positions: Position[];
  onAction?: () => void;
}

const PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT"];

export function ControlBar({ positions, onAction }: ControlBarProps) {
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null);
  const [confirmCloseAll, setConfirmCloseAll] = useState(false);

  // Manual order form state — defaults aligned with high-freq hybrid preset
  const [pair, setPair] = useState(PAIRS[0]);
  const [side, setSide] = useState<"long" | "short">("long");
  const [sizePct, setSizePct] = useState(0.10);
  const [leverage, setLeverage] = useState(10);
  const [slPct, setSlPct] = useState(0.008);
  const [tpPct, setTpPct] = useState(0.015);

  const flash = (ok: boolean, msg: string) => {
    setToast({ ok, msg });
    setTimeout(() => setToast(null), 3500);
  };

  const handleCloseAll = async () => {
    setConfirmCloseAll(false);
    setBusy(true);
    try {
      const { closed, failed } = await closeAllPositions(positions);
      flash(failed === 0, `已平仓 ${closed} 笔${failed ? `，失败 ${failed} 笔` : ""}`);
      onAction?.();
    } catch (e) {
      flash(false, `一键平仓失败: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleManualOpen = async () => {
    setBusy(true);
    const payload: ManualOrderPayload = {
      pair,
      side,
      position_size_pct: sizePct,
      leverage,
      stop_loss_pct: slPct,
      take_profit_pct: tpPct,
    };
    try {
      const r = await placeManualOrder(payload);
      if (r.success) {
        flash(true, `${pair.replace(":USDT", "")} ${side === "long" ? "多" : "空"} 开仓成功`);
        onAction?.();
      } else {
        flash(false, `拒绝: ${r.reason || "未知原因"}`);
      }
    } catch (e) {
      flash(false, `开仓失败: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
        覆盖操作
      </h2>
      <p className="text-xs text-gray-600 -mt-2">AI 自动交易为主，以下为人工覆盖入口</p>

      {/* Emergency: close all */}
      <button
        onClick={() => setConfirmCloseAll(true)}
        disabled={busy || positions.length === 0}
        className="w-full py-2.5 rounded-lg font-bold text-sm transition-all bg-red-700 hover:bg-red-600 text-white disabled:opacity-40 disabled:cursor-not-allowed"
      >
        ⏹ 一键平仓所有 ({positions.length})
      </button>

      <div className="border-t border-gray-800 pt-4">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
          手动开仓（覆盖 AI 决策）
        </h3>

        {/* Pair + Side */}
        <div className="grid grid-cols-2 gap-2 mb-2">
          <select
            value={pair}
            onChange={(e) => setPair(e.target.value)}
            className="bg-gray-800 text-white text-sm rounded px-2 py-1.5 border border-gray-700"
          >
            {PAIRS.map((p) => (
              <option key={p} value={p}>
                {p.replace(":USDT", "")}
              </option>
            ))}
          </select>
          <div className="grid grid-cols-2 gap-1">
            <button
              onClick={() => setSide("long")}
              className={`py-1.5 rounded text-sm font-medium ${
                side === "long"
                  ? "bg-green-700 text-white"
                  : "bg-gray-800 text-gray-400"
              }`}
            >
              做多
            </button>
            <button
              onClick={() => setSide("short")}
              className={`py-1.5 rounded text-sm font-medium ${
                side === "short"
                  ? "bg-red-700 text-white"
                  : "bg-gray-800 text-gray-400"
              }`}
            >
              做空
            </button>
          </div>
        </div>

        {/* Sliders */}
        <label className="block text-xs text-gray-500 mb-1">
          仓位 {(sizePct * 100).toFixed(0)}% (≤20%)
        </label>
        <input
          type="range"
          min={0.01}
          max={0.20}
          step={0.01}
          value={sizePct}
          onChange={(e) => setSizePct(parseFloat(e.target.value))}
          className="w-full mb-2 accent-blue-500"
        />

        <label className="block text-xs text-gray-500 mb-1">杠杆 {leverage}x (≤50x)</label>
        <input
          type="range"
          min={1}
          max={50}
          step={1}
          value={leverage}
          onChange={(e) => setLeverage(parseInt(e.target.value))}
          className="w-full mb-2 accent-blue-500"
        />

        <div className="grid grid-cols-2 gap-2 mb-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1">止损 {(slPct * 100).toFixed(1)}%</label>
            <input
              type="number"
              min={0.001}
              max={0.05}
              step={0.001}
              value={slPct}
              onChange={(e) => setSlPct(Math.min(0.05, Math.max(0, parseFloat(e.target.value) || 0)))}
              className="w-full bg-gray-800 text-white text-sm rounded px-2 py-1 border border-gray-700"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">止盈 {(tpPct * 100).toFixed(1)}%</label>
            <input
              type="number"
              min={0.001}
              max={0.05}
              step={0.001}
              value={tpPct}
              onChange={(e) => setTpPct(Math.min(0.05, Math.max(0, parseFloat(e.target.value) || 0)))}
              className="w-full bg-gray-800 text-white text-sm rounded px-2 py-1 border border-gray-700"
            />
          </div>
        </div>

        <button
          onClick={handleManualOpen}
          disabled={busy}
          className="w-full py-2 rounded-lg font-bold text-sm bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
        >
          {busy ? "处理中..." : "开仓（覆盖）"}
        </button>
      </div>

      <div className="pt-3 border-t border-gray-800 space-y-1 text-xs text-gray-500">
        <div className="flex justify-between"><span>最大仓位</span><span className="text-white">20%</span></div>
        <div className="flex justify-between"><span>最大杠杆</span><span className="text-white">50x</span></div>
        <div className="flex justify-between"><span>置信度阈值</span><span className="text-white">55%</span></div>
        <p className="text-gray-600 text-center pt-1">8 条安全规则保护 · AI 不可越权</p>
      </div>

      {/* Confirm close-all */}
      {confirmCloseAll && (
        <div
          className="fixed inset-0 bg-black/60 flex items-center justify-center z-50"
          onClick={() => setConfirmCloseAll(false)}
        >
          <div
            className="bg-gray-900 border border-red-700/50 rounded-xl p-5 max-w-sm w-full mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-white mb-2">确认一键平仓</h3>
            <p className="text-sm text-gray-300 mb-4">
              将以市价平掉全部 {positions.length} 笔持仓，不可撤销。
            </p>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setConfirmCloseAll(false)}
                className="px-3 py-1.5 rounded text-sm bg-gray-800 text-gray-300 hover:bg-gray-700"
              >
                取消
              </button>
              <button
                onClick={handleCloseAll}
                className="px-3 py-1.5 rounded text-sm bg-red-700 text-white hover:bg-red-600 font-medium"
              >
                确认全平
              </button>
            </div>
          </div>
        </div>
      )}

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
