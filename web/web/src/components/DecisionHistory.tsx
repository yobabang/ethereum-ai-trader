import { useEffect, useState } from "react";
import { getDecisionHistory, type AiDecisionRecord } from "../api";

export function DecisionHistory() {
  const [decisions, setDecisions] = useState<AiDecisionRecord[]>([]);

  useEffect(() => {
    const load = async () => setDecisions(await getDecisionHistory(50));
    load();
    const interval = setInterval(load, 15000);
    return () => clearInterval(interval);
  }, []);

  if (!decisions.length) {
    return <p className="text-gray-500 text-sm py-4">暂无 AI 决策记录</p>;
  }

  return (
    <div className="overflow-x-auto max-h-72 overflow-y-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-gray-500 border-b border-gray-800 sticky top-0 bg-gray-900">
            <th className="pb-2 font-medium">时间</th>
            <th className="pb-2 font-medium">交易对</th>
            <th className="pb-2 font-medium">动作</th>
            <th className="pb-2 font-medium text-right">置信度</th>
            <th className="pb-2 font-medium text-right">预期</th>
            <th className="pb-2 font-medium text-right">仓位</th>
            <th className="pb-2 font-medium">执行</th>
          </tr>
        </thead>
        <tbody>
          {decisions.map((d) => (
            <tr key={d.id} className="border-b border-gray-800/40">
              <td className="py-1.5 text-gray-400 whitespace-nowrap">
                {new Date(d.timestamp).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}
              </td>
              <td className="py-1.5 font-medium">{d.pair.replace(":USDT", "")}</td>
              <td className="py-1.5">
                <span
                  className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                    d.action === "LONG"
                      ? "bg-green-900/50 text-green-400"
                      : d.action === "SHORT"
                      ? "bg-red-900/50 text-red-400"
                      : "bg-gray-800 text-gray-400"
                  }`}
                >
                  {d.action}
                </span>
              </td>
              <td className="py-1.5 text-right text-gray-300">
                {d.confidence != null ? `${(d.confidence * 100).toFixed(0)}%` : "—"}
              </td>
              <td
                className={`py-1.5 text-right ${
                  (d.expected_return ?? 0) >= 0 ? "text-green-400" : "text-red-400"
                }`}
              >
                {d.expected_return != null ? `${(d.expected_return * 100).toFixed(2)}%` : "—"}
              </td>
              <td className="py-1.5 text-right text-gray-300">
                {d.position_size_pct != null ? `${(d.position_size_pct * 100).toFixed(0)}%` : "—"}
              </td>
              <td className="py-1.5">
                {d.executed ? (
                  <span className="text-green-500">✓</span>
                ) : (
                  <span className="text-gray-600">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
