import type { BotStatus } from "../api";

function StatCard({
  label,
  value,
  sub,
  color = "text-white",
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
      <p className="text-xs text-gray-500 uppercase tracking-wider">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${color}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
    </div>
  );
}

export function Dashboard({ status }: { status: BotStatus | null }) {
  if (!status) return null;

  const equity = status.equity.toLocaleString("zh-CN", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
  });
  const pnl = status.daily_pnl;
  const pnlStr =
    pnl >= 0
      ? `+${status.daily_pnl.toLocaleString("zh-CN", { style: "currency", currency: "USD", minimumFractionDigits: 0 })}`
      : status.daily_pnl.toLocaleString("zh-CN", {
          style: "currency",
          currency: "USD",
          minimumFractionDigits: 0,
        });
  const pnlColor = pnl >= 0 ? "text-green-400" : "text-red-400";

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <StatCard label="总权益" value={equity} />
      <StatCard
        label="今日盈亏"
        value={pnlStr}
        sub={`${(status.daily_pnl_pct >= 0 ? "+" : "") + status.daily_pnl_pct.toFixed(2)}%`}
        color={pnlColor}
      />
      <StatCard label="持仓数" value={String(status.open_positions)} />
      <StatCard label="AI 状态" value={status.ai_status} sub={status.ai_last_train} />
    </div>
  );
}
