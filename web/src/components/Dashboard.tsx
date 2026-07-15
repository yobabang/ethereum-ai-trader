import type { AccountSummary } from "../api";

function StatCard({
  label, value, sub, color = "text-white", isMock = false,
}: {
  label: string; value: string; sub?: string; color?: string; isMock?: boolean;
}) {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-4 relative">
      {isMock && (
        <span className="absolute top-2 right-2 px-1.5 py-0.5 rounded text-[10px] bg-yellow-900/60 text-yellow-400 border border-yellow-700">
          模拟
        </span>
      )}
      <p className="text-xs text-gray-500 uppercase tracking-wider">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${color}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
    </div>
  );
}

function fmtUSD(n: number): string {
  return n.toLocaleString("zh-CN", { style: "currency", currency: "USD", minimumFractionDigits: 0 });
}

export function Dashboard({ account }: { account: AccountSummary | null }) {
  if (!account) return null;

  const isMock = account._source === "mock";
  const eq = fmtUSD(account.equity);
  const realized = account.realized_pnl_today ?? 0;
  const unrealized = account.unrealized_pnl ?? 0;
  const totalPnl = realized + unrealized;
  const rColor = realized >= 0 ? "text-green-400" : "text-red-400";
  const uColor = unrealized >= 0 ? "text-green-400" : "text-red-400";
  const tColor = totalPnl >= 0 ? "text-green-400" : "text-red-400";

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
      <StatCard
        label="总权益"
        value={eq}
        sub={`初始 ${fmtUSD(account.initial_equity)}`}
        isMock={isMock}
      />
      <StatCard
        label="今日已实现"
        value={fmtUSD(realized)}
        sub={realized !== 0 ? `${realized >= 0 ? "+" : ""}${(realized / account.initial_equity * 100).toFixed(2)}%` : "无平仓"}
        color={rColor}
        isMock={isMock}
      />
      <StatCard
        label="未实现浮亏"
        value={fmtUSD(unrealized)}
        sub={account.open_positions > 0 ? `${account.open_positions} 个持仓` : "无持仓"}
        color={uColor}
        isMock={isMock}
      />
      <StatCard
        label="今日总盈亏"
        value={fmtUSD(totalPnl)}
        sub={`${(totalPnl / account.initial_equity * 100).toFixed(2)}%`}
        color={tColor}
        isMock={isMock}
      />
      <StatCard
        label="胜率"
        value={`${(account.win_rate * 100).toFixed(1)}%`}
        sub={`${account.total_trades} 笔交易`}
        isMock={isMock}
      />
    </div>
  );
}
