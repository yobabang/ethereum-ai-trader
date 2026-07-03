import type { AccountSummary } from "../api";

function StatCard({
  label,
  value,
  sub,
  color = "text-white",
  isMock = false,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
  isMock?: boolean;
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

export function Dashboard({ account }: { account: AccountSummary | null }) {
  if (!account) return null;

  const equity = account.equity.toLocaleString("zh-CN", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
  });
  const pnl = account.today_pnl;
  const pnlStr =
    pnl >= 0
      ? `+${pnl.toLocaleString("zh-CN", {
          style: "currency",
          currency: "USD",
          minimumFractionDigits: 0,
        })}`
      : pnl.toLocaleString("zh-CN", {
          style: "currency",
          currency: "USD",
          minimumFractionDigits: 0,
        });
  const pnlColor = pnl >= 0 ? "text-green-400" : "text-red-400";
  const isMock = account._source === "mock";

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <StatCard
        label="总权益"
        value={equity}
        sub={`初始 $${account.initial_equity.toLocaleString()}`}
        isMock={isMock}
      />
      <StatCard
        label="今日盈亏"
        value={pnlStr}
        sub={`${(account.today_pnl_pct >= 0 ? "+" : "") + account.today_pnl_pct.toFixed(2)}%`}
        color={pnlColor}
        isMock={isMock}
      />
      <StatCard label="持仓数" value={String(account.open_positions)} isMock={isMock} />
      <StatCard
        label="胜率"
        value={`${(account.win_rate * 100).toFixed(1)}%`}
        sub={`${account.total_trades} 笔交易`}
        isMock={isMock}
      />
    </div>
  );
}
