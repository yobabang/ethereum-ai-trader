import type { Order } from "../api";

function fmtDuration(entryIso: string, exitIso: string | null): string {
  if (!exitIso) return "持仓中";
  const ms = new Date(exitIso).getTime() - new Date(entryIso).getTime();
  if (ms < 0) return "—";
  const min = Math.floor(ms / 60000);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h${min % 60}m`;
  const day = Math.floor(hr / 24);
  return `${day}d${hr % 24}h`;
}

export function TradeHistory({ orders }: { orders: Order[] }) {
  if (!orders.length) {
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
            <th className="pb-2 font-medium text-right">入场</th>
            <th className="pb-2 font-medium text-right">出场</th>
            <th className="pb-2 font-medium text-right">盈亏</th>
            <th className="pb-2 font-medium text-right">时长</th>
            <th className="pb-2 font-medium">理由</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.id} className="border-b border-gray-800/50">
              <td className="py-2 text-gray-400 text-xs whitespace-nowrap">
                {new Date(o.entry_time).toLocaleString("zh-CN", {
                  month: "2-digit",
                  day: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </td>
              <td className="py-2 font-medium text-xs">
                {o.pair.replace(":USDT", "")}
              </td>
              <td className="py-2">
                <span
                  className={`px-1.5 py-0.5 rounded text-xs ${
                    o.side === "long"
                      ? "bg-green-900/50 text-green-400"
                      : "bg-red-900/50 text-red-400"
                  }`}
                >
                  {o.side === "long" ? "多" : "空"}
                </span>
              </td>
              <td className="py-2 text-right text-xs">
                ${o.entry_price.toLocaleString()}
              </td>
              <td className="py-2 text-right text-xs">
                {o.exit_price !== null
                  ? `$${o.exit_price.toLocaleString()}`
                  : <span className="text-yellow-500">持仓中</span>}
              </td>
              <td
                className={`py-2 text-right font-medium text-xs ${
                  o.realized_pnl > 0
                    ? "text-green-400"
                    : o.realized_pnl < 0
                    ? "text-red-400"
                    : "text-gray-500"
                }`}
              >
                {o.realized_pnl !== 0
                  ? `${o.realized_pnl > 0 ? "+" : ""}$${o.realized_pnl.toFixed(2)}`
                  : "—"}
              </td>
              <td className="py-2 text-right text-xs text-gray-400 whitespace-nowrap">
                {fmtDuration(o.entry_time, o.exit_time)}
              </td>
              <td className="py-2 text-xs text-gray-500 max-w-[120px] truncate">
                {o.exit_reason === "take_profit" ? (
                  <span className="text-green-500">止盈</span>
                ) : o.exit_reason === "stop_loss" ? (
                  <span className="text-red-500">止损</span>
                ) : o.exit_reason === "liquidated" ? (
                  <span className="text-red-600 font-bold">强平</span>
                ) : o.exit_reason === "manual" ? (
                  <span className="text-yellow-500">手动</span>
                ) : o.status === "open" ? (
                  <span className="text-yellow-500">持仓中</span>
                ) : (
                  o.exit_reason ?? "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
