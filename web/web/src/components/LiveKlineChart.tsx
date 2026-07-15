import { useEffect, useRef, useState } from "react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
} from "recharts";

interface KlineData {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface TickerData {
  pair: string;
  timestamp: string;
  status: "ok" | "degraded";
  source: string | null;
  ticker: {
    instId: string;
    last: string;
    open24h: string;
    high24h: string;
    low24h: string;
    vol24h: string;
    change24h?: string;
  } | null;
  candles: string[][] | []; // [ts, open, high, low, close, vol, ...]
}

interface LiveKlineChartProps {
  pair?: string;
}

export function LiveKlineChart({ pair = "BTC/USDT:USDT" }: LiveKlineChartProps) {
  const [klines, setKlines] = useState<KlineData[]>([]);
  const [ticker, setTicker] = useState<TickerData["ticker"]>(null);
  const [connected, setConnected] = useState(false);
  const [degraded, setDegraded] = useState(false);
  const [selectedPair, setSelectedPair] = useState(pair);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const wsUrl = `ws://${window.location.hostname}:3000/ws/klines?pair=${encodeURIComponent(selectedPair)}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log(`[WS] Connected for ${selectedPair}`);
      setConnected(true);
      setDegraded(false);
    };

    ws.onmessage = (event) => {
      try {
        const data: TickerData = JSON.parse(event.data);
        setDegraded(data.status === "degraded");
        if (data.ticker) {
          setTicker(data.ticker);
        }
        if (data.candles && data.candles.length > 0) {
          // Parse candles (reverse chronological → oldest first)
          const parsed: KlineData[] = data.candles
            .reverse()
            .map((c) => ({
              timestamp: new Date(Number(c[0])).toLocaleTimeString("zh-CN", {
                hour: "2-digit",
                minute: "2-digit",
              }),
              open: parseFloat(c[1]),
              high: parseFloat(c[2]),
              low: parseFloat(c[3]),
              close: parseFloat(c[4]),
              volume: parseFloat(c[5]),
            }));
          // Keep last 10 candles for display
          setKlines((prev) => {
            const merged = [...prev, ...parsed];
            // Deduplicate by timestamp
            const seen = new Set();
            const unique = merged.filter((k) => {
              if (seen.has(k.timestamp)) return false;
              seen.add(k.timestamp);
              return true;
            });
            return unique.slice(-10);
          });
        }
      } catch (err) {
        console.error("[WS] Parse error:", err);
      }
    };

    ws.onclose = () => {
      console.log(`[WS] Disconnected for ${selectedPair}`);
      setConnected(false);
    };

    ws.onerror = (err) => {
      console.error("[WS] Error:", err);
      setConnected(false);
    };

    return () => {
      ws.close();
    };
  }, [selectedPair]);

  const lastPrice = ticker?.last ? parseFloat(ticker.last) : null;
  const change24h = ticker?.change24h
    ? parseFloat(ticker.change24h)
    : ticker?.open24h
    ? ((parseFloat(ticker.last || "0") - parseFloat(ticker.open24h)) /
        parseFloat(ticker.open24h)) *
      100
    : 0;
  const isUp = (change24h || 0) >= 0;

  const chartData = klines.map((k) => ({
    time: k.timestamp,
    price: k.close,
    open: k.open,
    high: k.high,
    low: k.low,
    close: k.close,
  }));

  const minPrice = Math.min(...klines.map((k) => k.low));
  const maxPrice = Math.max(...klines.map((k) => k.high));
  const range = maxPrice - minPrice;

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
            实时行情
          </h2>
          <div className="flex items-center gap-2 mt-1">
            <select
              value={selectedPair}
              onChange={(e) => setSelectedPair(e.target.value)}
              className="bg-gray-800 text-white text-sm rounded px-2 py-1 border border-gray-700"
            >
              <option value="BTC/USDT:USDT">BTC/USDT</option>
              <option value="ETH/USDT:USDT">ETH/USDT</option>
            </select>
            <span
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs ${
                !connected
                  ? "bg-red-900/50 text-red-400"
                  : degraded
                  ? "bg-yellow-900/50 text-yellow-400"
                  : "bg-green-900/50 text-green-400"
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  !connected
                    ? "bg-red-400"
                    : degraded
                    ? "bg-yellow-400"
                    : "bg-green-400 animate-pulse"
                }`}
              />
              {!connected ? "断连" : degraded ? "行情离线" : "实时"}
            </span>
          </div>
        </div>
        {ticker && (
          <div className="text-right">
            <div
              className={`text-2xl font-bold ${
                isUp ? "text-green-400" : "text-red-400"
              }`}
            >
              ${lastPrice?.toLocaleString() ?? "--"}
            </div>
            <div
              className={`text-xs ${
                isUp ? "text-green-400" : "text-red-400"
              }`}
            >
              {isUp ? "+" : ""}
              {change24h.toFixed(2)}% (24h)
            </div>
          </div>
        )}
      </div>

      {/* Price Chart */}
      <div className="h-48 mb-4">
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData}>
              <defs>
                <linearGradient id="priceGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop
                    offset="0%"
                    stopColor={isUp ? "#22c55e" : "#ef4444"}
                    stopOpacity={0.3}
                  />
                  <stop
                    offset="100%"
                    stopColor={isUp ? "#22c55e" : "#ef4444"}
                    stopOpacity={0}
                  />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="time"
                tick={{ fontSize: 10, fill: "#6b7280" }}
                tickLine={false}
                axisLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                domain={[minPrice - range * 0.1, maxPrice + range * 0.1]}
                tick={{ fontSize: 10, fill: "#6b7280" }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v: number) => `$${v.toFixed(0)}`}
                width={70}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#1f2937",
                  border: "1px solid #374151",
                  borderRadius: "8px",
                  fontSize: "11px",
                }}
                formatter={(value: number) => [`$${value.toLocaleString()}`, "价格"]}
              />
              <Area
                type="monotone"
                dataKey="price"
                stroke={isUp ? "#22c55e" : "#ef4444"}
                strokeWidth={2}
                fill="url(#priceGradient)"
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full flex items-center justify-center text-gray-500 text-sm">
            等待行情数据...
          </div>
        )}
      </div>

      {/* Recent K-lines Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500 border-b border-gray-800">
              <th className="pb-1 text-left">时间</th>
              <th className="pb-1 text-right">开盘</th>
              <th className="pb-1 text-right">最高</th>
              <th className="pb-1 text-right">最低</th>
              <th className="pb-1 text-right">收盘</th>
              <th className="pb-1 text-right">成交量</th>
            </tr>
          </thead>
          <tbody>
            {klines.slice(-5).map((k, i) => (
              <tr key={i} className="border-b border-gray-800/50">
                <td className="py-1 text-gray-400">{k.timestamp}</td>
                <td className="py-1 text-right">${k.open.toLocaleString()}</td>
                <td className="py-1 text-right text-green-400">
                  ${k.high.toLocaleString()}
                </td>
                <td className="py-1 text-right text-red-400">
                  ${k.low.toLocaleString()}
                </td>
                <td
                  className={`py-1 text-right font-medium ${
                    k.close >= k.open ? "text-green-400" : "text-red-400"
                  }`}
                >
                  ${k.close.toLocaleString()}
                </td>
                <td className="py-1 text-right text-gray-500">
                  {k.volume.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
