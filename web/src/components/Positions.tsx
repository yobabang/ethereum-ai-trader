import type { Position } from "../api";

export function Positions({ positions }: { positions: Position[] }) {
  if (!positions.length) {
    return <p className="text-gray-500 text-sm py-4">当前无持仓</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-500 border-b border-gray-800">
            <th className="pb-2 font-medium">交易对</th>
            <th className="pb-2 font-medium">方向</th>
            <th className="pb-2 font-medium text-right">数量</th>
            <th className="pb-2 font-medium text-right">开仓价</th>
            <th className="pb-2 font-medium text-right">现价</th>
            <th className="pb-2 font-medium text-right">浮盈</th>
            <th className="pb-2 font-medium text-right">杠杆</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p, i) => (
            <tr key={i} className="border-b border-gray-800/50">
              <td className="py-2 font-medium">{p.pair}</td>
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
              <td className="py-2 text-right">{p.amount}</td>
              <td className="py-2 text-right">${p.entry_price.toLocaleString()}</td>
              <td className="py-2 text-right">${p.current_price.toLocaleString()}</td>
              <td
                className={`py-2 text-right font-medium ${
                  p.pnl >= 0 ? "text-green-400" : "text-red-400"
                }`}
              >
                {p.pnl >= 0 ? "+" : ""}${p.pnl.toLocaleString()}
                <span className="text-xs ml-1">
                  ({p.pnl >= 0 ? "+" : ""}
                  {p.pnl_pct.toFixed(2)}%)
                </span>
              </td>
              <td className="py-2 text-right">{p.leverage}x</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
