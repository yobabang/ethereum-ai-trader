import { useState } from "react";
import type { BotStatus } from "../api";

export function ControlBar({
  status,
  onControl,
}: {
  status: BotStatus | null;
  onControl: (action: "start" | "stop") => Promise<void>;
}) {
  const [loading, setLoading] = useState(false);
  const isRunning = status?.state === "running";

  const handleClick = async () => {
    setLoading(true);
    try {
      await onControl(isRunning ? "stop" : "start");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <button
        onClick={handleClick}
        disabled={loading}
        className={`w-full py-3 rounded-lg font-bold text-sm transition-all ${
          isRunning
            ? "bg-red-600 hover:bg-red-700 text-white"
            : "bg-green-600 hover:bg-green-700 text-white"
        } disabled:opacity-50`}
      >
        {loading ? "处理中..." : isRunning ? "🟢 停止交易" : "▶ 启动交易"}
      </button>

      <div className="space-y-3 text-sm text-gray-400">
        {status && (
          <div className="flex justify-between">
            <span>市场状态</span>
            <span className={status.allowed_to_trade ? "text-green-400" : "text-red-400"}>
              {status.current_regime?.replace("_", " ") || "--"}
            </span>
          </div>
        )}
        <div className="flex justify-between">
          <span>最大仓位</span>
          <span className="text-white">20%</span>
        </div>
        <div className="flex justify-between">
          <span>最大杠杆</span>
          <span className="text-white">5x</span>
        </div>
        <div className="flex justify-between">
          <span>单笔最大亏损</span>
          <span className="text-yellow-400">{status ? `${(status.per_trade_max_loss * 100).toFixed(0)}%` : "8%"}</span>
        </div>
        <div className="flex justify-between">
          <span>置信度阈值</span>
          <span className={status && status.adaptive_confidence > 0.55 ? "text-orange-400" : "text-white"}>
            {status ? `${(status.adaptive_confidence * 100).toFixed(0)}%` : "55%"}
          </span>
        </div>
        <div className="flex justify-between">
          <span>仓位倍率</span>
          <span className={status && status.adaptive_position_scalar < 1.0 ? "text-orange-400" : "text-white"}>
            {status ? `${(status.adaptive_position_scalar * 100).toFixed(0)}%` : "100%"}
          </span>
        </div>
        <div className="flex justify-between">
          <span>交易标的</span>
          <span className="text-white">BTC, ETH</span>
        </div>
        <div className="flex justify-between">
          <span>交易所</span>
          <span className="text-white">OKX</span>
        </div>
      </div>

      <div className="pt-3 border-t border-gray-800">
        <p className="text-xs text-gray-600 text-center">
          8 条安全规则保护 · AI 不可越权
        </p>
      </div>
    </div>
  );
}
