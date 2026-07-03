import { useState } from "react";
import { sendControl } from "../api";

export function ControlBar() {
  const [loading, setLoading] = useState(false);

  const handleClick = async (action: "start" | "stop") => {
    setLoading(true);
    try {
      await sendControl(action);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <button
        onClick={() => handleClick("start")}
        disabled={loading}
        className="w-full py-3 rounded-lg font-bold text-sm transition-all bg-green-600 hover:bg-green-700 text-white disabled:opacity-50"
      >
        {loading ? "处理中..." : "▶ 启动交易"}
      </button>

      <div className="space-y-3 text-sm text-gray-400">
        <div className="flex justify-between">
          <span>最大仓位</span>
          <span className="text-white">20%</span>
        </div>
        <div className="flex justify-between">
          <span>最大杠杆</span>
          <span className="text-white">5x</span>
        </div>
        <div className="flex justify-between">
          <span>单笔最大亏损</span>
          <span className="text-yellow-400">8%</span>
        </div>
        <div className="flex justify-between">
          <span>置信度阈值</span>
          <span className="text-white">55%</span>
        </div>
        <div className="flex justify-between">
          <span>交易标的</span>
          <span className="text-white">BTC, ETH</span>
        </div>
        <div className="flex justify-between">
          <span>数据源</span>
          <span className="text-white">OKX 公开行情</span>
        </div>
      </div>

      <div className="pt-3 border-t border-gray-800">
        <p className="text-xs text-gray-600 text-center">
          8 条安全规则保护 · AI 不可越权
        </p>
      </div>
    </div>
  );
}
