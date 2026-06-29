import type { Trade } from "../api";

export function TradeHistory({ trades }: { trades: Trade[] }) {
  if (!trades.length) {
    return <p className="text-gray-500 text-sm py-4">暂无交易记录</p>;
  }

  return (
    <div className="overflow-x-auto max-h-64 overflow-y-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-500 border-b border-gray-800 sticky top-0 bg-gray-900">
            <th className="pb-2 font-medium">时间</th>
            <th className="pb-2 font-medium">交易对</th>
            <th className="pb-2 font-medium">方向</th>
            <th className="pb-2 font-medium text-right">盈亏</th>
            <th className="pb-2 font-medium">理由</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="border-b border-gray-800/50">
              <td className="py-2 text-gray-400 text-xs">
                {new Date(t.entry_date).toLocaleTimeString("zh-CN", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </td>
              <td className="py-2 font-medium text-xs">{t.pair.replace(":USDT", "")}</td>
              <td className="py-2">
                <span
                  className={`px-1.5 py-0.5 rounded text-xs ${
                    t.side === "long"
                      ? "bg-green-900/50 text-green-400"
                      : "bg-red-900/50 text-red-400"
                  }`}
                >
                  {t.side === "long" ? "多" : "空"}
                </span>
              </td>
              <td
                className={`py-2 text-right font-medium text-xs ${
                  t.profit !== null
                    ? t.profit >= 0
                      ? "text-green-400"
                      : "text-red-400"
                    : "text-gray-500"
                }`}
              >
                {t.profit !== null
                  ? `${t.profit >= 0 ? "+" : ""}$${t.profit} (${t.profit_pct?.toFixed(1)}%)`
                  : "持仓中"}
              </td>
              <td className="py-2 text-xs text-gray-500 max-w-[120px] truncate">
                {t.exit_reason ?? "--"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
