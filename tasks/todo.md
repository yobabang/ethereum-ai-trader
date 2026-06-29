# 任务清单

> 更新日期: 2026-06-28 | 当前 Phase: 1 ✅ 完成 → Phase 2 待开始

---

## Phase 1: AI 决策核心 + 引擎改造

### 1.1 特征工程管线
- [x] **T1.1.1** 实现价格特征计算（40 个指标：RSI, MACD, BB, EMA, ATR, ADX, OBV 等）
  - 文件: `freqtrade/ai/features.py`
  - 依赖: TA-Lib, pandas-ta
  - 验证: 对所有 BTC/ETH 历史数据跑一遍，输出无 NaN
- [x] **T1.1.2** 实现订单簿特征计算 ← 同 T1.1.1 提交 (spread/depth/imbalance/ratio)
- [x] **T1.1.3** 实现衍生品特征计算 ← 同 T1.1.1 (funding_rate/signal/oi/ls_ratio)
- [x] **T1.1.4** 实现特征标准化管线 ← 同 T1.1.1 (51列, 零NaN, FreqAI兼容)

### 1.2 Layer 1: 市场状态分类器
- [x] **T1.2.1** 实现训练脚本（LightGBM 分类器，6 类）  ← 已提交 614df79
  - 文件: `freqtrade/ai/regime_classifier.py`
  - 输入: 最近 30 天特征数据
  - 验证: 11/11 测试通过，训练准确率 1.0
- [x] **T1.2.2** 实现推理接口 `classify(ohlcv_df) -> RegimeLabel`  ← 同上
  - 验证: 单次推理 < 100ms
- [x] **T1.2.3** 编写单元测试 ← 同 T1.2.1 (11/11 通过)

### 1.3 Layer 2: 方向预测器
- [x] **T1.3.1** 实现 LightGBM 回归器 ← 提交 64c8597 (XGBoost 延后)
- [x] **T1.3.2** 实现置信度计算 ← 同上 (基于残差标准差校准)
- [x] **T1.3.3** 实现推理接口 ← 同上 (return/confidence/max_drawdown)
- [x] **T1.3.4** 编写单元测试 ← 同上 (8/8 通过)

### 1.4 Layer 3 + Layer 4
- [x] **T1.4.1** 实现风险计算器 ← 提交 b448f59 (RiskCalculator)
- [x] **T1.4.2** 实现决策仲裁器 ← 同上 (8条安全规则, 全部验证)
- [x] **T1.4.3** 编写单元测试 ← 同上 (28/28 通过)

### 1.5 改造 FreqtradeBot
- [x] **T1.5.1** AIStrategy 封装 ← 提交 be108ef (不改 freqtradebot.py)
  - 方案: 实现 IStrategy 接口, 委托给 AI 四层流水线
  - Bot 层面零改动, AI 表现为一个普通策略
- [x] **T1.5.2** 接入 AI 决策核心 ← 同上
  - populate_entry_trend → DecisionArbitrator.decide()
  - custom_exit/custom_stake_amount/leverage 均使用 AI 决策
- [x] **T1.5.3** API 扩展 ← 同上 (AIStrategy 通过 freqtrade 原生 API 可查询)
- [x] **T1.5.4** 编写集成测试 ← 同上 (13/13 通过)

---

## Phase 2: Web 仪表盘

- [ ] **T2.1** 扩展 API
  - `GET /api/v1/status` → { equity, daily_pnl, positions, ai_status }
  - `GET /api/v1/trades` → 交易记录列表（分页）
  - `GET /api/v1/equity` → 权益历史时序数据
  - `POST /api/v1/control` → { action: "start" | "stop" }
  - `WS /ws/live` → 实时推送权益和持仓变化
- [ ] **T2.2** 创建 React 前端项目 (`web/`)
  - `npm create vite@latest web -- --template react-ts`
  - 安装 Tailwind + Recharts
- [ ] **T2.3** 实现仪表盘组件
  - `Dashboard.tsx` — 四卡片（总权益/今日盈亏/持仓数/AI状态）
  - `EquityCurve.tsx` — 7 天权益曲线（Recharts）
  - `Positions.tsx` — 当前持仓表格
  - `TradeHistory.tsx` — 交易记录列表
  - `ControlBar.tsx` — 启动/停止按钮 + 设置弹窗
- [ ] **T2.4** 前端集成 WebSocket 实时更新

---

## Phase 3: 自我优化循环

- [ ] **T3.1** 实现在线训练调度器
  - 文件: `freqtrade/ai/self_optimizer.py`
  - 每 4 小时调度训练任务
  - 模型版本管理（保存/加载/回滚）
  - 新模型 7 天回测验证后自动替换
- [ ] **T3.2** 实现交易反馈闭环
  - 每笔平仓记录: { 入场理由, 出场理由, 盈亏, 持仓时长 }
  - 连续亏损自动调整:
    - 提高置信度阈值
    - 降低单笔仓位
  - 连胜恢复:
    - 逐步恢复仓位
- [ ] **T3.3** 编写测试
  - 文件: `tests/ai/test_self_optimizer.py`

---

## Phase 4: 回测 + 验证

- [ ] **T4.1** AI 回测适配
  - 让 AI 决策核心在历史 K 线上逐根运行
  - 复用 freqtrade `Backtesting` 引擎
  - 输出回测报告（夏普/回撤/胜率/盈亏比）
- [ ] **T4.2** Dry-run 模拟交易
  - 用 OKX/Binance testnet 或 freqtrade dry-run
  - 至少 1 周连续运行
  - 日志记录所有 AI 决策和理由
- [ ] **T4.3** 达标检查
  - 夏普 > 0.5
  - 最大回撤 < 15%
  - 无安全规则被违反

---

## 验收标准（全项目完成）

- [ ] AI 在回测中达到最低指标（夏普>0.5, 回撤<15%）
- [ ] Web 面板可用：启动/停止/查看持仓/权益曲线
- [ ] 所有安全规则有测试覆盖
- [ ] Docker Compose 一键部署
- [ ] 1 周 dry-run 无灾难性事件
