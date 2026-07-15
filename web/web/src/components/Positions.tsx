import { useState } from "react";
import type { Position } from "../api";
import { closePosition } from "../api";

function fmtDuration(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "—";
  const min = Math.floor(ms / 60000);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h${min % 60}m`;
  const day = Math.floor(hr / 24);
  return `${day}d${hr % 24}h`;
}

interface PositionsProps {
  positions: Position[];
  onClosed?: () => void;
  onRowClick?: (p: Position) => void;
}

export function Positions({ positions, onClosed, onRowClick }: PositionsProps) {
  const [closingId, setClosingId] = useState<number | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null);

  if (!positions.length) {
    return <p className="text-gray-500 text-sm py-4">当前无持仓</p>;
  }

  const flash = (ok: boolean, msg: string) => {
    setToast({ ok, msg });
    setTimeout(() => setToast(null), 3000);
  };

  const handleClose = async (p: Position) => {
    setConfirmId(null);
    setClosingId(p.id);
    try {
      await closePosition(p.id);
      flash(true, `${p.pair} 已平仓`);
      onClosed?.();
    } catch (e) {
      flash(false, `平仓失败: ${(e as Error).message}`);
    } finally {
      setClosingId(null);
    }
  };

  const confirming = positions.find((p) => p.id === confirmId);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-500 border-b border-gray-800">
            <th className="pb-2 font-medium">交易对</th>
            <th className="pb-2 font-medium">方向</th>
            <th className="pb-2 font-medium text-right">开仓价</th>
            <th className="pb-2 font-medium text-right">现价</th>
            <th className="pb-2 font-medium text-right">浮盈/亏</th>
            <th className="pb-2 font-medium text-right">ROE</th>
            <th className="pb-2 font-medium text-right">强平价</th>
            <th className="pb-2 font-medium text-right">止损</th>
            <th className="pb-2 font-medium text-right">止盈</th>
            <th className="pb-2 font-medium text-right">保证金</th>
            <th className="pb-2 font-medium text-right">资金费</th>
            <th className="pb-2 font-medium text-right">时长</th>
            <th className="pb-2 font-medium text-right">杠杆</th>
            <th className="pb-2 font-medium text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr
              key={p.id}
              className="border-b border-gray-800/50 cursor-pointer hover:bg-gray-800/30"
              onClick={() => onRowClick?.(p)}
            >
              <td className="py-2 font-medium">
                {p.pair.replace(":USDT", "")}
                <span className="text-xs text-gray-500 ml-1">
                  {p.mode === "trend" ? "趋势" : p.mode === "manual" ? "手动" : "AI"}
                </span>
              </td>
              <td className="py-2">
                <span
                  className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                    p.side === "long"
                      ? "bg-green-900/50 text-green-400"
                      : "bg-red-900/50 text-red-400"
                  }`}
                >
                  {p.side === "long" ? "多" : "空"}
                </span>
              </td>
              <td className="py-2 text-right">${p.entry_price.toLocaleString()}</td>
              <td className="py-2 text-right">${p.current_price.toLocaleString()}</td>
              <td
                className={`py-2 text-right font-medium ${
                  p.unrealized_pnl >= 0 ? "text-green-400" : "text-red-400"
                }`}
              >
                {p.unrealized_pnl >= 0 ? "+" : ""}${p.unrealized_pnl.toFixed(2)}
              </td>
              <td
                className={`py-2 text-right font-medium ${
                  p.roe_pct >= 0 ? "text-green-400" : "text-red-400"
                }`}
              >
                {p.roe_pct >= 0 ? "+" : ""}
                {p.roe_pct.toFixed(1)}%
              </td>
              <td className="py-2 text-right text-orange-400/80">
                {p.liq_price != null ? `$${p.liq_price.toLocaleString()}` : "—"}
              </td>
              <td className="py-2 text-right text-gray-400">
                ${p.sl_price.toLocaleString()}
              </td>
              <td className="py-2 text-right text-gray-400">
                ${p.tp_price.toLocaleString()}
              </td>
              <td className="py-2 text-right text-gray-300">${p.margin.toFixed(1)}</td>
              <td
                className={`py-2 text-right text-xs ${
                  p.funding_paid >= 0 ? "text-red-400/80" : "text-green-400/80"
                }`}
              >
                {p.funding_paid >= 0 ? "-" : "+"}${Math.abs(p.funding_paid).toFixed(3)}
              </td>
              <td className="py-2 text-right text-xs text-gray-400 whitespace-nowrap">
                {fmtDuration(p.entry_time)}
              </td>
              <td className="py-2 text-right">{p.leverage}x</td>
              <td className="py-2 text-right">
                <button
                  disabled={closingId === p.id}
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmId(p.id);
                  }}
                  className="px-2 py-1 rounded text-xs font-medium bg-red-900/60 text-red-300 hover:bg-red-800 border border-red-700/50 disabled:opacity-40"
                >
                  {closingId === p.id ? "..." : "平仓"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Confirm dialog */}
      {confirming && (
        <div
          className="fixed inset-0 bg-black/60 flex items-center justify-center z-50"
          onClick={() => setConfirmId(null)}
        >
          <div
            className="bg-gray-900 border border-gray-700 rounded-xl p-5 max-w-sm w-full mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-white mb-2">确认平仓</h3>
            <p className="text-sm text-gray-300 mb-1">
              {confirming.pair.replace(":USDT", "")} ·{" "}
              <span className={confirming.side === "long" ? "text-green-400" : "text-red-400"}>
                {confirming.side === "long" ? "多" : "空"} {confirming.leverage}x
              </span>
            </p>
            <p className="text-xs text-gray-500 mb-1">
              开仓价 ${confirming.entry_price.toLocaleString()} · 现价 $
              {confirming.current_price.toLocaleString()}
            </p>
            <p
              className={`text-sm font-medium mb-4 ${
                confirming.unrealized_pnl >= 0 ? "text-green-400" : "text-red-400"
              }`}
            >
              当前浮盈 {confirming.unrealized_pnl >= 0 ? "+" : ""}${confirming.unrealized_pnl.toFixed(2)}
            </p>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setConfirmId(null)}
                className="px-3 py-1.5 rounded text-sm bg-gray-800 text-gray-300 hover:bg-gray-700"
              >
                取消
              </button>
              <button
                onClick={() => handleClose(confirming)}
                className="px-3 py-1.5 rounded text-sm bg-red-700 text-white hover:bg-red-600 font-medium"
              >
                确认平仓
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
