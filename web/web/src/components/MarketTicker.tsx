import { useEffect, useState } from "react";
import { getMarketTicker, type MarketTicker as Ticker } from "../api";

const PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT"];

export function MarketTicker({ symbol: symbolProp }: { symbol?: string }) {
  const [symbol, setSymbol] = useState(symbolProp ?? "BTC/USDT:USDT");
  const [ticker, setTicker] = useState<Ticker | null>(null);

  useEffect(() => {
    const load = async () => setTicker(await getMarketTicker(symbol));
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [symbol]);

  const spread = ticker && ticker.bid && ticker.ask ? ticker.ask - ticker.bid : null;
  const isUp = (ticker?.change_pct_24h ?? 0) >= 0;

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">盘口</h2>
        <select
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          className="bg-gray-800 text-white text-xs rounded px-2 py-1 border border-gray-700"
        >
          {PAIRS.map((p) => (
            <option key={p} value={p}>
              {p.replace(":USDT", "")}
            </option>
          ))}
        </select>
      </div>

      {ticker ? (
        <div className="space-y-3">
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-white">
              ${ticker.last.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            </span>
            <span className={`text-sm font-medium ${isUp ? "text-green-400" : "text-red-400"}`}>
              {isUp ? "+" : ""}
              {(ticker.change_pct_24h * 100).toFixed(2)}%
            </span>
          </div>

          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="bg-gray-800 rounded px-2 py-1.5 flex justify-between">
              <span className="text-green-500">买一</span>
              <span className="text-green-400">${ticker.bid.toLocaleString()}</span>
            </div>
            <div className="bg-gray-800 rounded px-2 py-1.5 flex justify-between">
              <span className="text-red-500">卖一</span>
              <span className="text-red-400">${ticker.ask.toLocaleString()}</span>
            </div>
          </div>

          {spread != null && (
            <p className="text-xs text-gray-500">价差 ${spread.toFixed(2)}</p>
          )}

          <div className="grid grid-cols-3 gap-2 text-xs">
            <div className="text-center">
              <p className="text-gray-500">24h 高</p>
              <p className="text-green-400">${ticker.high_24h.toLocaleString()}</p>
            </div>
            <div className="text-center">
              <p className="text-gray-500">24h 低</p>
              <p className="text-red-400">${ticker.low_24h.toLocaleString()}</p>
            </div>
            <div className="text-center">
              <p className="text-gray-500">24h 量</p>
              <p className="text-gray-300">{(ticker.volume_24h / 1000).toFixed(1)}K</p>
            </div>
          </div>
        </div>
      ) : (
        <p className="text-gray-500 text-sm">等待行情...</p>
      )}
    </div>
  );
}
