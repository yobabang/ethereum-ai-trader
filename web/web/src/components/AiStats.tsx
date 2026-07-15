import { useEffect, useState } from "react";
import { getAiStats, type AiStats as AiStatsData } from "../api";

function Metric({ label, value, color = "text-white", sub }: { label: string; value: string; color?: string; sub?: string }) {
  return (
    <div className="bg-gray-800 rounded-lg p-3">
      <p className="text-xs text-gray-500">{label}</p>
      <p className={`text-lg font-bold ${color}`}>{value}</p>
      {sub && <p className="text-[10px] text-gray-600 mt-0.5">{sub}</p>}
    </div>
  );
}

export function AiStatsPanel() {
  const [stats, setStats] = useState<AiStatsData | null>(null);

  useEffect(() => {
    const load = async () => setStats(await getAiStats());
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, []);

  if (!stats) return null;

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
        AI 统计
      </h2>
      <div className="grid grid-cols-2 gap-3">
        <Metric
          label="最佳 Sharpe"
          value={stats.best_sharpe.toFixed(2)}
          color={stats.best_sharpe > 0.5 ? "text-green-400" : "text-red-400"}
        />
        <Metric
          label="最佳胜率"
          value={`${(stats.best_win_rate * 100).toFixed(0)}%`}
          color={stats.best_win_rate > 0.4 ? "text-green-400" : "text-red-400"}
        />
        <Metric
          label="连亏"
          value={`${stats.consecutive_losses}`}
          color={stats.consecutive_losses >= 3 ? "text-red-400" : "text-gray-300"}
        />
        <Metric
          label="连盈"
          value={`${stats.consecutive_wins}`}
          color={stats.consecutive_wins > 0 ? "text-green-400" : "text-gray-300"}
        />
        <Metric
          label="自适应置信阈值"
          value={`${(stats.current_confidence_threshold * 100).toFixed(0)}%`}
          sub="连亏会自动抬升"
        />
        <Metric
          label="自适应仓位系数"
          value={stats.current_position_scalar.toFixed(2)}
          sub="连亏会自动收缩"
        />
      </div>
      <div className="mt-3 pt-3 border-t border-gray-800 flex justify-between text-xs">
        <span className="text-gray-500">模型版本</span>
        <span className="text-gray-300">
          {stats.version_count} 个 ({stats.current_version?.slice(0, 15) || "none"})
        </span>
      </div>
    </div>
  );
}
