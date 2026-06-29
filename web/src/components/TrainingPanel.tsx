import { useEffect, useState } from "react";

interface TrainingStatus {
  training_in_progress: boolean;
  training_count: number;
  last_train_time: string;
  hours_until_next: number;
  last_metrics: Record<string, number>;
  last_error: string;
  model_versions: number;
  current_version: string;
}

export function TrainingPanel() {
  const [status, setStatus] = useState<TrainingStatus | null>(null);

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch("/api/v1/ai/training");
        if (res.ok) setStatus(await res.json());
      } catch {}
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 30000);
    return () => clearInterval(interval);
  }, []);

  if (!status) return null;

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
        AI 训练状态
      </h2>

      {/* Training indicator */}
      <div className="flex items-center gap-2 mb-4">
        <span
          className={`w-2 h-2 rounded-full ${
            status.training_in_progress
              ? "bg-yellow-400 animate-pulse"
              : status.last_train_time !== "never"
              ? "bg-green-400"
              : "bg-gray-500"
          }`}
        />
        <span className="text-sm">
          {status.training_in_progress
            ? "训练中..."
            : status.last_train_time !== "never"
            ? `上次训练: ${new Date(status.last_train_time).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`
            : "尚未训练"}
        </span>
      </div>

      {/* Next training */}
      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="bg-gray-800 rounded-lg p-3">
          <p className="text-xs text-gray-500">训练次数</p>
          <p className="text-lg font-bold">{status.training_count}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-3">
          <p className="text-xs text-gray-500">下次训练</p>
          <p className="text-lg font-bold">
            {status.training_in_progress
              ? "进行中"
              : `${status.hours_until_next.toFixed(1)}h`}
          </p>
        </div>
      </div>

      {/* Last metrics */}
      {Object.keys(status.last_metrics).length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-gray-500">最近训练指标</p>
          <div className="grid grid-cols-2 gap-2 text-xs">
            {status.last_metrics.sharpe !== undefined && (
              <div className="flex justify-between bg-gray-800 rounded px-2 py-1">
                <span className="text-gray-400">Sharpe</span>
                <span className={status.last_metrics.sharpe > 0.5 ? "text-green-400" : "text-red-400"}>
                  {status.last_metrics.sharpe.toFixed(2)}
                </span>
              </div>
            )}
            {status.last_metrics.max_drawdown !== undefined && (
              <div className="flex justify-between bg-gray-800 rounded px-2 py-1">
                <span className="text-gray-400">MaxDD</span>
                <span className={status.last_metrics.max_drawdown < 0.15 ? "text-green-400" : "text-red-400"}>
                  {(status.last_metrics.max_drawdown * 100).toFixed(1)}%
                </span>
              </div>
            )}
            {status.last_metrics.win_rate !== undefined && (
              <div className="flex justify-between bg-gray-800 rounded px-2 py-1">
                <span className="text-gray-400">胜率</span>
                <span className={status.last_metrics.win_rate > 0.4 ? "text-green-400" : "text-red-400"}>
                  {(status.last_metrics.win_rate * 100).toFixed(0)}%
                </span>
              </div>
            )}
            {status.last_metrics.profit_factor !== undefined && (
              <div className="flex justify-between bg-gray-800 rounded px-2 py-1">
                <span className="text-gray-400">盈亏比</span>
                <span className={status.last_metrics.profit_factor > 1.5 ? "text-green-400" : "text-red-400"}>
                  {status.last_metrics.profit_factor.toFixed(2)}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Model versions */}
      <div className="mt-3 pt-3 border-t border-gray-800 flex justify-between text-xs">
        <span className="text-gray-500">模型版本</span>
        <span className="text-gray-300">{status.model_versions} 个 ({status.current_version?.slice(0, 15) || "none"})</span>
      </div>

      {/* Error */}
      {status.last_error && (
        <div className="mt-2 p-2 bg-red-900/30 border border-red-800 rounded text-xs text-red-400">
          {status.last_error.slice(0, 100)}
        </div>
      )}
    </div>
  );
}
