import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
} from "recharts";
import type { EquityPoint } from "../api";

export function EquityCurve({ data }: { data: EquityPoint[] }) {
  if (!data.length) {
    return <p className="text-gray-500 text-sm py-8 text-center">暂无数据</p>;
  }

  const chartData = data.map((d) => ({
    ...d,
    time: new Date(d.date).toLocaleDateString("zh-CN", {
      month: "short",
      day: "numeric",
    }),
  }));

  const min = Math.min(...data.map((d) => d.equity));
  const max = Math.max(...data.map((d) => d.equity));
  const start = data[0].equity;
  const isUp = (data[data.length - 1].equity ?? start) >= start;

  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={chartData}>
        <defs>
          <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={isUp ? "#22c55e" : "#ef4444"} stopOpacity={0.3} />
            <stop offset="100%" stopColor={isUp ? "#22c55e" : "#ef4444"} stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="time"
          tick={{ fontSize: 11, fill: "#6b7280" }}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          domain={[min * 0.995, max * 1.005]}
          tick={{ fontSize: 11, fill: "#6b7280" }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v: number) => `$${(v / 1000).toFixed(1)}k`}
          width={60}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "#1f2937",
            border: "1px solid #374151",
            borderRadius: "8px",
            fontSize: "12px",
          }}
          formatter={(value: number) => [`$${value.toLocaleString()}`, "权益"]}
        />
        <Area
          type="monotone"
          dataKey="equity"
          stroke={isUp ? "#22c55e" : "#ef4444"}
          strokeWidth={2}
          fill="url(#equityGradient)"
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
