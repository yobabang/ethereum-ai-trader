import { useEffect, useRef, useState, useCallback } from "react";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type Time,
  type IPriceLine,
} from "lightweight-charts";
import type { Position } from "../api";

const SYMBOLS = [
  { value: "BTC/USDT:USDT", label: "BTC/USDT" },
  { value: "ETH/USDT:USDT", label: "ETH/USDT" },
];
const TIMEFRAMES = [
  { value: "5m", label: "5m" },
  { value: "15m", label: "15m" },
  { value: "1h", label: "1H" },
  { value: "4h", label: "4H" },
  { value: "1d", label: "1D" },
];

interface Kline {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface KlineChartProps {
  positions?: Position[];
}

export function KlineChart({ positions = [] }: KlineChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const [symbol, setSymbol] = useState("BTC/USDT:USDT");
  const [timeframe, setTimeframe] = useState("1h");
  const [price, setPrice] = useState<number | null>(null);
  const [changePct, setChangePct] = useState<number | null>(null);

  const fetchKlines = useCallback(async (sym: string, tf: string) => {
    try {
      const res = await fetch(
        `/api/v1/market/klines?symbol=${encodeURIComponent(sym)}&timeframe=${tf}&limit=200`
      );
      const json = await res.json();
      if (json.data?.length) {
        return json.data as Kline[];
      }
    } catch {
      console.warn("Kline fetch failed");
    }
    return null;
  }, []);

  const fetchTicker = useCallback(async (sym: string) => {
    try {
      const res = await fetch(`/api/v1/market/ticker?symbol=${encodeURIComponent(sym)}`);
      const json = await res.json();
      if (json.last) {
        setPrice(json.last);
        setChangePct(json.change_pct_24h ?? null);
      }
    } catch {
      /* ignore */
    }
  }, []);

  // Initialize chart once
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: "#111827" },
        textColor: "#9CA3AF",
      },
      grid: {
        vertLines: { color: "#1F2937" },
        horzLines: { color: "#1F2937" },
      },
      crosshair: {
        mode: 0,
        vertLine: { color: "#6B7280", style: 2, width: 1, labelBackgroundColor: "#374151" },
        horzLine: { color: "#6B7280", style: 2, width: 1, labelBackgroundColor: "#374151" },
      },
      rightPriceScale: {
        borderColor: "#374151",
        scaleMargins: { top: 0.1, bottom: 0.2 },
      },
      timeScale: {
        borderColor: "#374151",
        timeVisible: true,
        secondsVisible: false,
      },
      width: containerRef.current.clientWidth,
      height: 480,
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#22C55E",
      downColor: "#EF4444",
      borderDownColor: "#EF4444",
      borderUpColor: "#22C55E",
      wickDownColor: "#EF4444",
      wickUpColor: "#22C55E",
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: "",
      priceFormat: { type: "volume" },
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });

    chartRef.current = chart;
    seriesRef.current = candleSeries;
    volSeriesRef.current = volumeSeries;

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      volSeriesRef.current = null;
      priceLinesRef.current = [];
    };
  }, []);

  // Load candle + volume data on symbol/timeframe change
  const loadData = useCallback(async () => {
    const data = await fetchKlines(symbol, timeframe);
    if (!data || !seriesRef.current) return;

    const candles: CandlestickData[] = data.map((k) => ({
      time: (k.time / 1000) as Time,
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
    }));
    seriesRef.current.setData(candles);

    if (volSeriesRef.current) {
      volSeriesRef.current.setData(
        data.map((k) => ({
          time: (k.time / 1000) as Time,
          value: k.volume,
          color: k.close >= k.open ? "rgba(34,197,94,0.4)" : "rgba(239,68,68,0.4)",
        }))
      );
    }

    chartRef.current?.timeScale().fitContent();
  }, [symbol, timeframe, fetchKlines]);

  useEffect(() => {
    loadData();
    fetchTicker(symbol);
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
  }, [loadData, fetchTicker, symbol]);

  // Draw position price lines (entry/SL/TP) for the current symbol
  useEffect(() => {
    if (!seriesRef.current) return;
    // Clear existing lines
    priceLinesRef.current.forEach((l) => seriesRef.current?.removePriceLine(l));
    priceLinesRef.current = [];

    const matching = positions.filter((p) => p.pair === symbol);
    for (const p of matching) {
      const sideLabel = p.side === "long" ? "多" : "空";
      const lines: { price: number; color: string; title: string }[] = [
        { price: p.entry_price, color: "#3B82F6", title: `开仓 ${sideLabel}` },
        { price: p.sl_price, color: "#EF4444", title: "止损" },
        { price: p.tp_price, color: "#22C55E", title: "止盈" },
      ];
      for (const l of lines) {
        const pl = seriesRef.current.createPriceLine({
          price: l.price,
          color: l.color,
          lineStyle: 2,
          lineWidth: 1,
          axisLabelVisible: true,
          title: l.title,
        });
        priceLinesRef.current.push(pl);
      }
    }
  }, [positions, symbol]);

  const changeColor =
    changePct === null ? "text-gray-400" : changePct >= 0 ? "text-green-400" : "text-red-400";

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
            OKX 实时K线
          </h2>
          {price !== null && (
            <span className="text-lg font-bold text-white">
              ${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          )}
          {changePct !== null && (
            <span className={`text-sm font-medium ${changeColor}`}>
              {changePct >= 0 ? "+" : ""}
              {(changePct * 100).toFixed(2)}%
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          <div className="flex rounded-lg bg-gray-800 p-0.5">
            {SYMBOLS.map((s) => (
              <button
                key={s.value}
                onClick={() => setSymbol(s.value)}
                className={`px-3 py-1 text-xs font-medium rounded-md transition ${
                  symbol === s.value ? "bg-indigo-600 text-white" : "text-gray-400 hover:text-white"
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>

          <div className="flex rounded-lg bg-gray-800 p-0.5">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf.value}
                onClick={() => setTimeframe(tf.value)}
                className={`px-3 py-1 text-xs font-medium rounded-md transition ${
                  timeframe === tf.value ? "bg-indigo-600 text-white" : "text-gray-400 hover:text-white"
                }`}
              >
                {tf.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Chart */}
      <div ref={containerRef} className="w-full" style={{ minHeight: 480 }} />

      <p className="text-xs text-gray-600 mt-2">
        OKX 实时数据 · {timeframe} · 每30秒刷新 · 蓝/红/绿线为持仓开仓/止损/止盈
      </p>
    </div>
  );
}
