import { useEffect, useState } from "react";
import { getPositionDetail, type Position, type PositionDetail } from "../api";

interface Props {
  position: Position | null;
  onClose: () => void;
}

const MAINTENANCE = 0.005;

function computeLiq(p: PositionDetail | Position): number | null {
  if ("contracts" in p && (p.contracts ?? 0) <= 0) return null;
  const c = (p as Position).contracts ?? 0;
  if (c <= 0) return null;
  const entry = p.entry_price;
  const margin = p.margin;
  const funding = p.funding_paid ?? 0;
  if (p.side === "long") {
    const eff = margin - Math.max(funding, 0);
    return (entry - eff / c) / (1 - MAINTENANCE);
  }
  const eff = margin - Math.max(-funding, 0);
  return (entry + eff / c) / (1 + MAINTENANCE);
}

export function PositionDetail({ position, onClose }: Props) {
  const [detail, setDetail] = useState<PositionDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!position) return;
    getPositionDetail(position.id)
      .then(setDetail)
      .catch((e) => setErr((e as Error).message));
  }, [position]);

  if (!position) return null;

  const liq = detail ? computeLiq(detail) : position.liq_price ?? computeLiq(position);
  const entryTime = detail?.entry_time ?? position.entry_time;
  const aiConf = detail?.ai_confidence ?? position.ai_confidence;
  const aiReason = detail?.ai_reason ?? position.ai_reason;
  const mode = detail?.mode ?? position.mode;
  const status = detail?.status ?? "open";

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-gray-900 border border-gray-700 rounded-xl p-5 max-w-md w-full mx-4 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-white">
            {position.pair.replace(":USDT", "")} ·{" "}
            <span className={position.side === "long" ? "text-green-400" : "text-red-400"}>
              {position.side === "long" ? "多" : "空"} {position.leverage}x
            </span>
          </h3>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-white text-xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="space-y-2 text-sm">
          <Row label="状态" value={status} color={status === "open" ? "text-yellow-400" : "text-gray-400"} />
          <Row label="模式" value={mode === "trend" ? "趋势" : mode === "manual" ? "手动覆盖" : "AI"} />
          <Row label="开仓价" value={`$${position.entry_price.toLocaleString()}`} />
          <Row label="现价" value={`$${position.current_price.toLocaleString()}`} />
          <Row label="数量 (contracts)" value={position.contracts.toFixed(4)} />
          <Row label="保证金" value={`$${position.margin.toFixed(2)}`} />
          <Row label="强平价" value={liq != null ? `$${liq.toLocaleString()}` : "—"} color="text-orange-400" />
          <Row label="止损价" value={`$${position.sl_price.toLocaleString()}`} />
          <Row label="止盈价" value={`$${position.tp_price.toLocaleString()}`} />
          <Row
            label="未实现盈亏"
            value={`${position.unrealized_pnl >= 0 ? "+" : ""}$${position.unrealized_pnl.toFixed(2)}`}
            color={position.unrealized_pnl >= 0 ? "text-green-400" : "text-red-400"}
          />
          <Row label="ROE" value={`${position.roe_pct >= 0 ? "+" : ""}${position.roe_pct.toFixed(2)}%`}
            color={position.roe_pct >= 0 ? "text-green-400" : "text-red-400"} />
          <Row
            label="累计资金费"
            value={`${position.funding_paid >= 0 ? "-" : "+"}$${Math.abs(position.funding_paid).toFixed(3)}`}
            color={position.funding_paid >= 0 ? "text-red-400/80" : "text-green-400/80"}
          />
          <Row label="开仓时间" value={entryTime ? new Date(entryTime).toLocaleString("zh-CN") : "—"} />
          <Row label="AI 置信度" value={aiConf != null ? `${(aiConf * 100).toFixed(0)}%` : "—"} />

          {aiReason && (
            <div className="pt-2 border-t border-gray-800">
              <p className="text-xs text-gray-500 mb-1">AI 决策理由</p>
              <p className="text-xs text-gray-300 whitespace-pre-wrap break-words">{aiReason}</p>
            </div>
          )}

          {err && (
            <div className="pt-2 text-xs text-red-400">加载详情失败: {err}</div>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ label, value, color = "text-white" }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between border-b border-gray-800/40 py-1">
      <span className="text-gray-500 text-xs">{label}</span>
      <span className={`font-medium text-xs ${color}`}>{value}</span>
    </div>
  );
}
