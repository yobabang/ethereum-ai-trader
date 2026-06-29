# 以太 AI Trader

纯 AI 驱动的加密货币永续合约自动交易系统，基于 Freqtrade 深度改造。

## 快速启动

```bash
# Windows
start.bat

# Linux/Mac
bash start.sh

# 实盘模式
bash start.sh --live
```

启动后访问:
- **仪表盘**: http://localhost:3000
- **Bot API**: http://localhost:8080
- **AI Bridge**: http://localhost:8081

## 项目结构

```
ethereum-ai-trader/
├── config.json          # 交易配置 (OKX API + 风险参数)
├── api.txt              # API 密钥 (已 gitignore)
├── models/              # 训练好的 AI 模型
├── SPEC.md              # 完整规格说明
├── SETUP.md             # 前置准备指南
├── KNOWLEDGE_BASE.md    # 项目知识库 (对话重启恢复)
├── plan.md              # 实施计划
├── tasks/todo.md        # 任务清单
├── test_report.docx     # 真实数据测试报告
├── start.sh / start.bat # 一键启动脚本
└── README.md            # 本文件

../freqtrade/
├── freqtrade/ai/        # AI 决策核心 (12 个模块)
│   ├── features.py          # 特征工程 (51列)
│   ├── regime_classifier.py # 市场状态分类 (Layer 1)
│   ├── direction_predictor.py # 方向预测 (Layer 2)
│   ├── decision_arbitrator.py # 风险+仲裁 (Layer 3+4)
│   ├── self_optimizer.py    # 自适应优化
│   ├── scheduler.py         # 自动训练调度
│   ├── training_pipeline.py # 训练管道
│   ├── backtest_adapter.py  # 回测引擎
│   ├── ai_strategy.py       # IStrategy 桥接
│   ├── api_bridge.py        # AI API 端点
│   ├── trainer.py           # 离线训练
│   ├── validate.py          # 部署前验证
│   └── launch_check.py      # 启动检查清单
├── web/                 # React 仪表盘
└── tests/ai/            # 测试 (111+ tests)
```

## AI 架构

```
市场数据 → Layer 1: 市场状态分类 → Layer 2: 方向预测
              ↓                        ↓
         Layer 3: 风险计算 → Layer 4: 决策仲裁 → 下单
                             ↑
                    8 条安全规则 (不可越权)
```

## 使用流程

```bash
# 1. 配置
编辑 config.json → 填入 OKX API 密钥

# 2. 下载数据
cd ../freqtrade
python -m freqtrade download-data -c ../ethereum-ai-trader/config.json \
  --pairs BTC/USDT:USDT ETH/USDT:USDT --timeframes 4h --trading-mode futures

# 3. 训练模型
python -m freqtrade.ai.trainer --config ../ethereum-ai-trader/config.json

# 4. 验证
python -m freqtrade.ai.validate --config ../ethereum-ai-trader/config.json

# 5. 一键启动
cd ../ethereum-ai-trader && bash start.sh
```

## 安全规则 (8 条)

| # | 规则 |
|---|------|
| 1 | HIGH_VOLATILITY → 禁止开新仓 |
| 2 | 置信度 < 55% → 不动 |
| 3 | 预期回撤 > 5% 权益 → 不动 |
| 4 | 已有亏损仓位 → 不开同方向新仓 |
| 5 | 极端资金费率 → 方向限制 |
| 6 | 连续 3 笔亏损 → 停机 12 小时 |
| 7 | 单笔最大仓位 20% |
| 8 | 最大杠杆 5x |

## 风险警告

本系统仅用于教育目的。加密货币交易存在重大亏损风险。永远不要投入无法承受损失的资金。
