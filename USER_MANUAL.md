# 以太 AI Trader — 系统说明书

> 版本：v2.2（高频高倍版） ｜ 更新：2026-07-15
> 纯 AI 驱动的 BTC/ETH 永续合约**模拟**交易系统 · 真实行情 · 虚拟资金

---

## 目录

1. [系统概述](#1-系统概述)
2. [快速开始](#2-快速开始)
3. [交易模式](#3-交易模式)
4. [AI 决策引擎](#4-ai-决策引擎)
5. [前端操作界面](#5-前端操作界面)
6. [手动覆盖操作](#6-手动覆盖操作)
7. [配置说明](#7-配置说明)
8. [测试](#8-测试)
9. [架构与目录](#9-架构与目录)
10. [故障排查](#10-故障排查)
11. [风险声明](#11-风险声明)

---

## 1. 系统概述

### 1.1 这是什么

一个**模拟合约交易平台**，核心特点：

- **真实行情**：OKX 公开 API 实时拉取 BTC/ETH 永续合约 K 线、盘口、资金费率（无需 API Key）
- **虚拟资金**：所有交易在 SimBroker（模拟撮合引擎）中执行，零真实资金风险
- **AI 决策**：4 层 AI 流水线自动判断市场状态、预测方向、计算风控、仲裁下单
- **两种模式**：低倍保守（≤5x / 4h）与高频高倍（≤50x / 5m 混合）
- **完整前端**：K 线图、持仓、交易记录、AI 决策、统计仪表盘 + 手动覆盖操作

### 1.2 设计原则

| 原则 | 说明 |
|------|------|
| **AI 为主** | AI 自动看盘、决策、下单，无需人工干预 |
| **手动仅覆盖** | 前端提供平仓/手动开仓作为紧急覆盖手段，不作为主流程 |
| **安全第一** | 8 条硬编码安全规则 AI 不可越权 |
| **模拟优先** | 永不连接实盘交易，dry_run 始终为 true |

### 1.3 服务端口

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端 Dashboard | http://localhost:3000 | React + Vite 开发服务器 |
| AI API Bridge | http://127.0.0.1:8090 | FastAPI 后端，供前端数据 + 手动操作 |
| WebSocket 行情 | ws://localhost:3000/ws/klines | 实时 K 线推送 |

---

## 2. 快速开始

### 2.1 环境要求

- Python 3.11+
- Node.js 18+
- 网络可访问 OKX 公开 API（`www.okx.com`）

### 2.2 后端安装

```bash
cd ethereum-ai-trader

# 安装依赖
pip install -r requirements.txt
pip install -r requirements-dev.txt   # 测试用（pytest, httpx）
```

### 2.3 前端安装

```bash
cd web
npm install
```

### 2.4 启动（高频高倍模式）

**终端 1 — 后端交易引擎：**
```bash
cd ethereum-ai-trader
python -m engine.live_trader --mode hybrid --leverage 20 --equity 1000
```

**终端 2 — API Bridge（供前端）：**
```bash
cd ethereum-ai-trader
python -m engine.api_bridge --port 8090
```

**终端 3 — 前端：**
```bash
cd web
npm run dev
```

访问 http://localhost:3000 即可看到交易仪表盘。

> 后端未启动时，前端会显示黄色"模拟数据"横幅，展示的是演示数据。

---

## 3. 交易模式

系统支持 5 种决策模式，通过 `--mode` 选择：

### 3.1 模式对比

| 模式 | 周期 | 杠杆 | 决策逻辑 | 适用场景 |
|------|------|------|----------|----------|
| `ai` | 1h | ≤5x | 纯 AI 4 层流水线 | 保守低频 |
| `trend` | 4h | ≤5x | EMA9/100 趋势策略 | 趋势跟踪 |
| `breakout` | 1h | ≤5x | Donchian 通道突破 | 突破捕捉 |
| `rl` | 1h | ≤5x | PPO 强化学习 | RL 实验 |
| **`hybrid`** | **5m** | **≤50x** | **AI 方向 + 策略择时混合** | **高频高倍（推荐）** |

### 3.2 高频高倍模式（hybrid）

**核心思路**：AI 模型是按 4h 训练的，直接喂 5m 数据幅度预测不准——但**方向**（涨/跌）仍然可信。所以：

1. **AI 出方向**：L2 模型预测 `expected_return`，取符号 → long / short / none
2. **策略出择时**：trend 策略（EMA9/50）在同一 5m 数据上算信号 → long / short / hold
3. **两者一致才开仓**，不一致 → HOLD（理由"AI/策略分歧"）

这种融合绕开了模型频率不匹配问题，方向比幅度稳健。

**默认参数（HYBRID 预设）：**
```python
leverage: 20x        # 可 --leverage 覆盖到 50
position_pct: 30%    # 仓位占比
stop_loss_pct: 0.8%  # 紧止损（50x 下爆仓距离约 2%）
take_profit_pct: 1.5% # 快止盈
min_confidence: 0.50
interval: 300s       # 5 分钟决策一次
```

### 3.3 启动命令示例

```bash
# 高频高倍混合（默认 20x）
python -m engine.live_trader --mode hybrid --leverage 20 --equity 1000

# 最激进（50x + aggressive 阈值）
python -m engine.live_trader --mode hybrid --leverage 50 --aggressive

# 保守低频（原模式）
python -m engine.live_trader --mode ai --leverage 5

# 1m 秒级 scalp（旧预设）
python -m engine.live_trader --scalp
```

### 3.4 CLI 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--mode` | ai | ai/trend/breakout/rl/hybrid |
| `--leverage` | 模式默认 | 固定杠杆覆盖（上限 50） |
| `--aggressive` | off | 激进参数（低阈值、高杠杆） |
| `--equity` | 1000 | 初始权益（USDT） |
| `--db` | sim_trader.db | SQLite 路径 |
| `--highfreq` | off | hybrid 别名（5m/300s） |
| `--scalp` | off | 1m/60s scalp 预设 |

---

## 4. AI 决策引擎

### 4.1 四层流水线

```
市场数据 → L1 市场状态分类 → L2 方向预测
              ↓                    ↓
         L3 风险计算 → L4 决策仲裁 → 下单
                          ↑
                 8 条安全规则（不可越权）
```

**Layer 1 — 市场状态分类**（`regime_classifier.py`）
LightGBM 分类器，6 类市场状态：
- `TRENDING_STRONG` 强趋势（适合顺势）
- `TRENDING_WEAK` 弱趋势（降仓位）
- `RANGING_TIGHT` / `RANGING_WIDE` 震荡（禁止交易）
- `HIGH_VOLATILITY` 高波动（禁止交易）
- `LOW_VOLATILITY` 低波动（等待）

**Layer 2 — 方向预测**（`direction_predictor.py`）
LightGBM + XGBoost 回归集成，120+ 维特征，输出：预期收益率、置信度、预期最大回撤。

**Layer 3 — 风险计算**（`decision_arbitrator.py` RiskCalculator）
纯规则，计算：最大仓位%、ATR 止损、动态止盈、杠杆倍数。

**Layer 4 — 决策仲裁**（DecisionArbitrator）
综合前三层，应用安全规则，输出最终 `{action, position_size, leverage, SL, TP}`。

### 4.2 八条安全规则（硬编码，AI 不可修改）

| # | 规则 | 说明 |
|---|------|------|
| 1 | 高波动/震荡市 | 禁止开新仓 |
| 2 | 置信度 < 阈值 | 不动（默认 55%，hybrid 50%） |
| 3 | 预期回撤 > 权益阈值 | 不动 |
| 4 | 已有亏损仓位 | 不开同方向 |
| 5 | 极端资金费率 | 方向限制 |
| 6 | 连续 3 笔亏损 | 停机 12 小时 |
| 7 | 单笔最大仓位 | 20% 权益 |
| 8 | 最大杠杆 | 50x |

### 4.3 自我优化循环

- **每 4 小时**自动重训练 L1/L2（用最近 30/60 天数据）
- 新模型 vs 旧模型回测对比：夏普>0.5、回撤<15%、胜率>40%、盈亏比>1.5 才替换
- 每笔平仓后反馈：连亏自动抬升置信阈值 + 收缩仓位（自适应参数）
- 模型版本管理（保留最近 30 版）

### 4.4 模拟撮合（SimBroker）

- 开仓/平仓/止损止盈/爆仓/资金费结算全模拟
- **intrabar SL/TP 检测**：每 5s 用 high/low 检测，捕捉瞬时价格尖峰
- SL 优先于 TP（同一根 K 线同时命中时保守处理）
- OKX → Binance → 本地 feather 三级行情 fallback
- 强平价动态计算（含累计资金费）

---

## 5. 前端操作界面

访问 http://localhost:3000，主要区域：

### 5.1 顶部状态栏
- 平台标题 + 模式标识（"高频高倍 · ≤50x · 5m"）
- 运行状态灯（绿色脉冲 = 后端已连接）
- 错误横幅 + 模拟数据横幅

### 5.2 统计卡片（7 张）
| 卡片 | 说明 |
|------|------|
| 总权益 | 当前账户总值 + 初始权益 |
| 今日已实现 | 今日平仓盈亏 |
| 未实现浮亏 | 当前持仓浮动盈亏 |
| 今日总盈亏 | 已实现 + 未实现 |
| 最大回撤 | 历史峰值到谷底（真实计算） |
| Sharpe | 最佳模型夏普比率 |
| 胜率/连续 | 胜率 + 连盈/连亏计数 |

### 5.3 K 线图
- **candlestick 蜡烛图 + 成交量**（lightweight-charts）
- 交易对切换：BTC / ETH
- 周期切换：**5m** / 15m / 1h / 4h / 1d
- **持仓价位线**：开仓价（蓝）/ 止损（红）/ 止盈（绿）叠加在图上
- 每 30 秒自动刷新

### 5.4 当前持仓表
列：交易对+模式 / 方向 / 开仓价 / 现价 / 浮盈 / ROE / **强平价** / 止损 / 止盈 / 保证金 / 资金费 / **持仓时长** / 杠杆 / **平仓按钮**

- 点击行 → 持仓详情弹窗（含 AI 决策理由）
- 平仓按钮 → 确认弹窗 → 平仓

### 5.5 底部信息区
- **AI 决策历史**：时间线，每条决策的方向/置信度/是否执行
- **行情盘口**：买一/卖一/价差/24h 高低量
- **AI 统计**：最佳 Sharpe/胜率/连亏连盈/自适应参数/模型版本

### 5.6 交易记录 + 权益曲线
- 交易记录：历史单的入场/出场/盈亏/平仓理由/持仓时长
- 权益曲线：7 天权益历史折线

---

## 6. 手动覆盖操作

**定位：AI 自动交易为主，手动仅覆盖。** 在右侧"覆盖操作"面板：

### 6.1 一键平仓所有
- 显示当前持仓数
- 点击 → 二次确认 → 批量平掉全部持仓（市价）
- 用于紧急止损 / AI 失控时的人工刹车

### 6.2 手动开仓（覆盖 AI 决策）
表单字段：
- 交易对：BTC / ETH
- 方向：做多 / 做空
- 仓位滑块：1%–20%
- 杠杆滑块：1x–**50x**
- 止损输入：0–5%（默认 0.8%）
- 止盈输入：0–5%（默认 1.5%）

点击"开仓（覆盖）"→ 调 `POST /trade/manual` 下单。

> 手动单经过 SimBroker 的风控检查（保证金、杠杆上限、重复同对拒绝），不是无条件放行。后端 Pydantic 强制约束：杠杆≤50、仓位≤20%、SL/TP≤10%。

### 6.3 操作反馈
所有操作有 toast 提示：
- ✅ 绿色：成功
- ❌ 红色：失败（含 broker 拒绝原因）

### 6.4 交易控制面板（运行时切换）

**位置**：前端右侧"交易记录"上方，"交易控制"卡片。

**作用**：运行中切换 AI 决策方式，无需重启 live_trader。通过 `trading_state.json` 跨进程传递（api_bridge 写，live_trader 每轮读），**下轮决策生效**（最多 5m 延迟）。

可控制 5 项，每项都有说明文案：

| 控件 | 说明 |
|------|------|
| **决策模式** | 5 选 1：hybrid(高频高倍5m/≤50x,推荐) / ai(保守1h/≤5x) / trend(4h趋势) / breakout(1h突破) / rl(1h强化学习)。鼠标悬停看各模式详情。 |
| **杠杆** | 滑块 1-50x。"越高盈亏越放大，50x 下价格波动 2% 即爆仓。" |
| **暂停/恢复** | "暂停后 AI 不再开新仓，但现有仓位的止损止盈仍正常执行（不平仓）。" |
| **止损 %** | 高级参数，0-10% |
| **止盈 %** | 高级参数，0-10% |
| **置信度阈值 %** | 高级参数，0-100% |
| **仓位 %** | 高级参数，0-50% |

**关键语义**：
- **下轮生效**：切换后不会立即中断当前循环，下一个决策周期（hybrid 5m / ai 1h）才用新参数。
- **暂停 ≠ 平仓**：暂停只阻止开新仓，`check_positions` 照常跑，现有仓位的 SL/TP/强平仍会执行——保证暂停不会让现有仓位失控。
- **partial update**：只改传入的字段，其它字段保留。比如只切模式不会清空杠杆设置。
- **header 联动**：顶部模式标识实时显示当前 mode/leverage/暂停状态。

**API**：
```
GET  /api/v1/trade/control   → 读当前状态
POST /api/v1/trade/control   → 写 partial 更新（Pydantic 约束：杠杆≤50、模式枚举、SL/TP≤10%）
```

---


## 7. 配置说明

### 7.1 config.json

```json
{
  "exchange": { "name": "okx", ... },        // OKX 配置（仅公开行情，无需 Key）
  "trading_mode": "futures",
  "margin_mode": "isolated",
  "pair_whitelist": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
  "dry_run": true,                            // 始终模拟
  "simulation_only": true,
  "ai": {
    "max_leverage": 5,                        // 保守模式上限
    "max_position_pct": 0.20,
    "max_drawdown_pct": 0.15,
    "min_confidence": 0.55,
    "highfreq": {                             // 高频高倍配置块
      "mode": "hybrid",
      "timeframe": "5m",
      "interval_seconds": 300,
      "max_leverage": 50,
      "default_leverage": 20,
      "position_pct": 0.30,
      "stop_loss_pct": 0.008,
      "take_profit_pct": 0.015,
      "circuit_breaker": false
    }
  },
  "api_server": { "listen_port": 8080 },
  "datadir": "user_data/data"
}
```

### 7.2 风控参数速查

| 参数 | 保守模式 | 高频模式 | 位置 |
|------|----------|----------|------|
| 最大杠杆 | 5x | 50x | `SimConfig.max_leverage` |
| 单笔仓位 | 20% | 30% | HYBRID 预设 |
| 止损 | 2% | 0.8% | HYBRID 预设 |
| 止盈 | 4% | 1.5% | HYBRID 预设 |
| 单笔亏损上限 | 3% | 3% | `PER_TRADE_MAX_LOSS_PCT` |
| 断路器 | 关 | 关 | `circuit_breaker_liquidations=0` |
| 置信度阈值 | 55% | 50% | HYBRID 预设 |

---

## 8. 测试

### 8.1 后端测试

```bash
cd ethereum-ai-trader
python -m pytest              # 全量（127 passed）
python -m pytest tests/test_hybrid_pipeline.py -v   # hybrid 逻辑
python -m pytest tests/test_api_bridge.py -v        # API 端点
python -m pytest tests/test_decision_arbitrator.py  # 风控规则
```

**覆盖范围：**
- `test_decision_arbitrator.py` — 8 条安全规则逐条覆盖
- `test_sim_broker.py` — 开仓/SL/TP/爆仓/恢复全生命周期
- `test_hybrid_pipeline.py` — 混合决策一致/分歧场景
- `test_api_bridge.py` — account/平仓/开仓/强平价/入参校验/CORS

### 8.2 前端测试

```bash
cd web
npm test                     # 22 passed
npm run test:watch           # 监听模式
```

**覆盖范围：**
- `ControlBar.test.tsx` — 滑块 clamp、开仓默认值、一键全平、开仓调用
- `Positions.test.tsx` — 平仓弹窗、强平价显示、行点击
- `PositionDetail.test.tsx` — 强平价公式（computeLiq）+ 渲染

### 8.3 测试统计

| 层 | 测试数 | 状态 |
|----|--------|------|
| 后端 engine | 112 | ✅ |
| 后端 api_bridge | 9 | ✅ |
| 后端 hybrid | 6 | ✅ |
| 前端组件 | 22 | ✅ |
| **合计** | **149** | 全绿 |

### 8.4 构建验证

```bash
cd web
npx tsc --noEmit    # 类型检查零错误
npm run build       # 生产构建
```

---

## 9. 架构与目录

```
ethereum-ai-trader/
├── engine/                    # AI 决策核心（Python）
│   ├── live_trader.py         # 主交易循环（5 种模式）
│   ├── sim_broker.py          # 模拟撮合引擎
│   ├── decision_arbitrator.py # L3 风控 + L4 仲裁（8 条规则）
│   ├── regime_classifier.py   # L1 市场状态分类
│   ├── direction_predictor.py # L2 方向预测
│   ├── features.py            # 特征工程（120+ 维）
│   ├── self_optimizer.py      # 在线学习
│   ├── scheduler.py           # 训练调度
│   ├── api_bridge.py          # FastAPI 后端
│   ├── ai_operator.py         # AI 运维 CLI
│   ├── trend_strategy.py      # 趋势策略
│   ├── breakout_strategy.py   # 突破策略
│   ├── rl_signal.py           # RL 信号
│   ├── database.py            # SQLite 持久层
│   └── ...
├── web/                       # React 前端
│   ├── src/
│   │   ├── App.tsx            # 主布局
│   │   ├── api.ts             # API 客户端
│   │   └── components/
│   │       ├── KlineChart.tsx     # 蜡烛图 + 持仓价位线
│   │       ├── Positions.tsx      # 持仓表 + 平仓
│   │       ├── ControlBar.tsx     # 覆盖操作栏
│   │       ├── PositionDetail.tsx # 持仓详情弹窗
│   │       ├── Dashboard.tsx      # 统计卡片
│   │       ├── DecisionHistory.tsx
│   │       ├── AiStats.tsx
│   │       ├── MarketTicker.tsx
│   │       └── ...
│   └── vitest.config.ts
├── models/                    # 训练好的模型（.pkl/.zip）
├── tests/                     # 测试（149 个）
├── config.json                # 配置
├── pytest.ini                 # pytest 配置
└── requirements*.txt
```

### 9.1 数据流

```
OKX 公开 API → fetch_ohlcv (5m K线)
    → FeatureEngineer (120+ 特征)
    → L1 RegimeClassifier (市场状态)
    → L2 DirectionPredictor (方向+置信度)
    → [hybrid] TrendStrategy (择时)
    → L3 RiskCalculator (仓位/SL/TP/杠杆)
    → L4 DecisionArbitrator (8 条规则仲裁)
    → SimBroker.open_order (虚拟下单)
    → SQLite (持仓/权益/决策记录)
    → api_bridge → 前端仪表盘
```

---

## 10. 故障排查

### 10.1 前端显示"模拟数据"横幅
**原因**：后端 API Bridge 未启动或不可达。
**解决**：启动 `python -m engine.api_bridge --port 8090`，确认 http://127.0.0.1:8090/api/v1/ai/health 返回 `{"status":"ok"}`。

### 10.2 K 线图不显示
**原因**：OKX 公开 API 不可达（网络/代理）。
**解决**：检查网络；config.json 的 `proxies` 配置是否正确；系统会自动 fallback 到 Binance。

### 10.3 平仓按钮无响应
**原因**：CORS 未放行 DELETE（旧版本）或 HTTPException 未导入。
**解决**：确认 api_bridge.py 已 `from fastapi import HTTPException` 且 `allow_methods` 含 `"DELETE"`。

### 10.4 端口被占用
```bash
# 8090 被 McAfee 等占用
python -m engine.api_bridge --port 8091
# 前端 vite.config.ts 同步修改 proxy target
```

### 10.5 杠杆被拒绝
后端强制约束：手动开仓杠杆≤50、仓位≤20%、SL/TP≤10%。超限返回 422。

### 10.6 AI 模型加载失败
**原因**：`models/*.pkl` 不存在或版本不匹配。
**解决**：运行 `python -m engine.trainer --config config.json` 重新训练；或 hybrid 模式下 AI 只用方向，模型旧也能跑。

### 10.7 测试 collection error
**原因**：脚本式文件依赖 4h feather 数据。
**解决**：pytest.ini 已 `--ignore` 这些脚本，正常 `pytest` 不会 collect 它们。

---

## 11. 风险声明

### ⚠️ 重要风险提示

1. **本系统仅用于教育与研究目的**
2. **所有交易均为虚拟资金模拟**，dry_run 始终为 true，永不连接实盘
3. **高频高倍模式（50x）会快速归零**——这是预期行为。50x 杠杆下，价格波动 2% 即爆仓。前端"一键平仓所有"和强平价显示是最后的人工刹车
4. **加密货币交易存在重大亏损风险**，永远不要投入无法承受损失的资金
5. **AI 决策不保证盈利**，历史回测表现不代表未来收益

### 11.1 已知限制

- AI 模型按 4h 训练，hybrid 模式只取方向符号（幅度不可信）
- self_optimizer 的在线学习闭环未完全接入交易路径（自适应参数部分生效）
- 无实盘交易能力（设计如此）
- 无多用户系统（单用户设计）

### 11.2 不做的事

- ❌ 不做 BTC/ETH 以外的币种
- ❌ 不做现货（只做永续合约）
- ❌ 不做实盘交易
- ❌ 不做策略编辑器（纯 AI 决策）
- ❌ 不做移动端 App

---

## 附录：常用命令速查

```bash
# 启动
python -m engine.live_trader --mode hybrid --leverage 20 --equity 1000
python -m engine.api_bridge --port 8090
cd web && npm run dev

# 测试
python -m pytest -q                    # 后端全量
cd web && npm test                     # 前端全量

# AI 运维
python -m engine.ai_operator status    # 系统状态
python -m engine.ai_operator trades --last 10  # 最近交易
python -m engine.ai_operator check     # 异常检测

# 模型
python -m engine.trainer --config config.json   # 训练
python -m engine.validate --config config.json  # 验证
```

---

*说明书版本 v2.2 ｜ 如有问题提交 [GitHub Issues](https://github.com/peterfei/ai-agent-team/issues)*
