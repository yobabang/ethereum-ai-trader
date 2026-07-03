# 以太 AI 模拟交易平台 — 项目手册 V3.0

> **版本**: 3.0.0 | **日期**: 2026-07-03
>
> 核心规格详见 [SPEC.md](SPEC.md) + [SPEC_SUPPLEMENT.md](SPEC_SUPPLEMENT.md)

---

## 一、项目简介

一个**模拟合约交易平台**：
- **真实行情**驱动：从 OKX 公开 API 拉取实时行情（无需 API Key）
- **虚拟资金**交易：$1,000 USDT 模拟盘，AI 自动决策
- **数据驱动迭代**：积累交易数据后，触发 AI 分析 + walk-forward 验证 + 参数应用

**最高原则**：除了钱是假的，其他必须真。

---

## 二、架构总览

```
┌──────────────────────────────────────────────────────────┐
│                    前端 (React + Vite :3000)               │
│  ┌─────────┐ ┌──────────┐ ┌────────┐ ┌───────────────┐  │
│  │ 实时K线  │ │ AI决策    │ │ 持仓   │ │ 权益曲线       │  │
│  │(WebSocket)│ │ (面板)   │ │ 列表   │ │ + 交易记录    │  │
│  └─────────┘ └──────────┘ └────────┘ └───────────────┘  │
└──────────────────────┬───────────────────────────────────┘
                       │ REST API (:8090)
┌──────────────────────┴───────────────────────────────────┐
│                  后端 (FastAPI :8090)                       │
│  ┌──────────┐ ┌─────────────┐ ┌────────────────────────┐ │
│  │ /market  │ │ /trade      │ │ AI 交易循环              │ │
│  │ /ws      │ │ /account    │ │ engine/live_trader.py   │ │
│  │ /klines  │ │ /positions  │ │ --mode ai | trend       │ │
│  └──────────┘ └──────┬──────┘ └──────────┬─────────────┘ │
│                      │                   │               │
│              ┌───────┴───────────────────┴──────┐        │
│              │   SQLite (sim_trader.db)          │        │
│              │   positions / equity / decisions  │        │
│              └──────────────┬───────────────────┘        │
│                             │                             │
│              ┌──────────────┴───────────────────┐        │
│              │   迭代监控系统                      │        │
│              │   iteration_monitor → snapshots   │        │
│              │   → analyze → walk-forward 验证   │        │
│              └──────────────────────────────────┘        │
└──────────────────────────────────────────────────────────┘
```

### 关键组件

| 组件 | 文件 | 说明 |
|------|------|------|
| AI 决策引擎 | `engine/live_trader.py` | 主循环，支持 `--mode ai`（1h AI 管道）或 `--mode trend`（4h 趋势策略） |
| 模拟订单引擎 | `engine/sim_broker.py` | 开仓/平仓/SL/TP/强平/资金费率结算 |
| 趋势策略 | `engine/trend_strategy.py` | EMA 趋势跟踪 + trend_filter + slope_confirm |
| 突破策略 | `engine/breakout_strategy.py` | Donchian 通道突破 |
| AI 模型 | `engine/direction_predictor.py` + `engine/regime_classifier.py` | LightGBM 方向预测 + 市场状态分类 |
| 数据库 | `engine/database.py` | SQLite CRUD |
| API 服务 | `engine/api_bridge.py` | FastAPI + WebSocket 推送 |
| 迭代监控 | `engine/iteration_monitor.py` | 数据触发 + 快照生成 |
| AI 分析 | `scripts/analyze_iteration.py` | 读快照 → AI 建议 → walk-forward 验证 |

---

## 三、快速启动

### 3.1 安装依赖

```bash
pip install fastapi uvicorn websockets aiohttp requests pandas numpy lightgbm scikit-learn joblib
cd web && npm install
```

### 3.2 启动三件套

**终端 1：模拟交易引擎**

```bash
cd ethereum-ai-trader
python -m engine.live_trader                      # AI 模式 (默认)
python -m engine.live_trader --mode trend         # 趋势策略
python -m engine.live_trader --aggressive         # 激进模式
python -m engine.live_trader --mode trend --aggressive  # 趋势+激进
```

**终端 2：API 服务**

```bash
python -m engine.api_bridge                       # 默认端口 8090
python -m engine.api_bridge --port 8091           # 自定义端口
```

**终端 3：前端**

```bash
cd web && npm run dev                             # Vite :3000
```

打开 http://localhost:3000

### 3.3 端口冲突处理

默认 API 端口 8090。如果被占用（如 McAfee 等），启动时会提示：

```
ERROR: port 8090 is already in use by PID 1234
Options:
  1. Use a different port: python -m engine.api_bridge --port 8091
  2. Free the port (stop the occupying process)
```

同时更新 `web/vite.config.ts` 代理指向新端口。

---

## 四、迭代系统使用

### 4.1 触发条件（任一满足即触发）

| 条件 | 阈值 | 说明 |
|------|------|------|
| 交易数量 | ≥50 笔 | 最近 50 笔新交易（自上次分析以来） |
| 最大回撤 | ≥15% | 权益曲线 peak-to-trough |
| 连续亏损 | ≥5 笔 | 连续止损 |
| 时间安全网 | ≥168 小时 | 至少每周分析一次 |

### 4.2 使用方式

```bash
# 单次检查
python -m engine.iteration_monitor --once

# 后台循环（每 5 分钟检查）
python -m engine.iteration_monitor --interval 300

# 指定数据库
python -m engine.iteration_monitor --db custom.db --once
```

触发后：
1. 生成快照 `data/snapshots/snapshot_<时间戳>.json`
2. 快照自动包含 `db_path` 字段（analyze 自动读取）
3. 运行 `python scripts/analyze_iteration.py` 读快照
4. AI 分析数据，给出参数建议
5. 建议写入快照 `_ai_recommendations` 字段
6. walk-forward 验证 → 达标才应用

### 4.3 CronCreate 自动化

Claude Code 会话内配 CronCreate（每 5 分钟）：
```
*/5 * * * * → 检查 NEW.flag → 读反馈 → 优化 → 写 fix_*.md
```

**限制**：会话级隔离，Claude 退出失效，7 天过期。长期运行建议用 `--interval` 模式。

---

## 五、测试说明

### 5.1 运行测试

```bash
# 单元测试
python -m pytest tests/test_sim_broker.py tests/test_smoke.py -v

# 策略测试
python -m pytest tests/test_trend_strategy.py tests/test_breakout_strategy.py -v

# 全量
python -m pytest tests/ \
  --ignore=tests/test_ai_strategy.py \
  -q
```

### 5.2 端到端验证

```bash
# 填充测试数据 + 触发 + 分析 + 验证
python - <<'PY'
from engine.database import Database
from datetime import datetime, timezone, timedelta
db = Database("test_e2e.db")
now = datetime.now(timezone.utc)
for i in range(60):
    db.open_position({
        "pair": "BTC/USDT:USDT", "side": "long", "entry_price": 50000.0,
        "entry_time": (now - timedelta(hours=60-i)).isoformat(),
        "contracts": 0.01, "margin": 100.0, "leverage": 3,
        "sl_price": 49000.0, "tp_price": 52000.0,
        "ai_confidence": 0.72, "ai_reason": "test", "mode": "ai",
    })
    pid = db.get_recent_positions(1)[0]["id"]
    db.close_position(pid, 51000.0, (now - timedelta(hours=59-i)).isoformat(),
                      "take_profit", 5.0, 0.1)
PY

python -m engine.iteration_monitor --db test_e2e.db --once
python scripts/analyze_iteration.py --db test_e2e.db
python -m engine.iteration_monitor --db test_e2e.db --once  # 验证重置
rm -f test_e2e.db
```

---

## 六、安全护栏

| 规则 | 说明 |
|------|------|
| ❌ 永不接入实盘交易 | `sim_broker.py` 无任何 ccxt 导入 |
| ❌ 永不加载真实 API Key | 无 OKX_API_KEY 环境变量 |
| ❌ 永不调用下单 API | `api_bridge.py` 仅有 GET 端点 |
| ✅ 只读 OKX 公开数据 | ticker + K 线 + 资金费率 |
| ✅ 虚拟资金 | 初始 $1,000 USDT，固定不可修改 |

验证：
```bash
grep -rn "create_order\|OKX_API_KEY\|apiKey\|secret" engine/live_trader.py engine/sim_broker.py
# 期望：零命中
```

---

## 七、文件索引

### 核心代码
| 文件 | 说明 |
|------|------|
| `engine/live_trader.py` | 主交易循环，支持 `--mode ai/trend` + `--aggressive` |
| `engine/sim_broker.py` | 模拟订单引擎（SL/TP/强平/资金费率） |
| `engine/trend_strategy.py` | 趋势策略（EMA + trend_filter + slope_confirm） |
| `engine/breakout_strategy.py` | 突破策略（Donchian 通道） |
| `engine/direction_predictor.py` | AI 方向预测（LightGBM 回归） |
| `engine/regime_classifier.py` | 市场状态分类（LightGBM 6 分类） |
| `engine/features.py` | 特征工程（40+ 技术指标） |
| `engine/database.py` | SQLite CRUD |
| `engine/api_bridge.py` | FastAPI + WebSocket |
| `engine/iteration_monitor.py` | 迭代触发监控 |

### 脚本
| 文件 | 说明 |
|------|------|
| `scripts/analyze_iteration.py` | AI 分析入口（读快照 + walk-forward 验证） |
| `scripts/trend_walkforward.py` | 趋势策略 walk-forward 验证 |
| `scripts/walkforward_verify.py` | AI 模型 walk-forward 验证 |
| `scripts/combo_walkforward.py` | 多策略组合验证 |

### 文档
| 文件 | 说明 |
|------|------|
| `SPEC.md` | 核心规格 |
| `SPEC_SUPPLEMENT.md` | 补充规格（缺口填补） |
| `PROJECT_MANUAL_V3.md` | 本手册 |
| `README.md` | 项目简介 |
| `SETUP.md` | 部署说明 |
| `KNOWLEDGE_BASE.md` | 知识库 |

### 数据
| 路径 | 说明 |
|------|------|
| `sim_trader.db` | SQLite 数据库（positions/equity_snapshots/ai_decisions） |
| `data/snapshots/` | 迭代快照（自动清理，保留最近 50 个） |
| `data/iteration_state.json` | 迭代状态（自动派生自 DB 路径） |

### 测试
| 文件 | 说明 |
|------|------|
| `tests/test_sim_broker.py` | 订单引擎单元测试（21 tests） |
| `tests/test_smoke.py` | 端到端烟雾测试（11 tests） |
| `tests/test_trend_strategy.py` | 趋势策略测试（11 tests） |
| `tests/test_breakout_strategy.py` | 突破策略测试（7 tests） |

---

## 八、已知问题

### 高优先级
- **walk-forward 验证接入**：`analyze_iteration.py` 的 `validate_and_apply()` 仍是框架，实际调用 `trend_walkforward.py` 待开发

### 中优先级
- **CronCreate 会话限制**：Claude 退出即失效。长期运行建议用 `python -m engine.iteration_monitor --interval`
- **快照文件不清理**：`_cleanup_old_snapshots()` 保留最近 50 个，但长期运行仍会累积

### 低优先级
- **first_analysis bootstrap**：首次运行会触发一次分析（即使 trades < 50），是有意行为
- **exit code 约定**：代码 `0=正常 / 1=错误`，对 cron 有用

---

## 九、常见问题

### Q: 端口被占用怎么办？

启动时会提示。用 `--port` 覆盖：
```bash
python -m engine.api_bridge --port 8091
```
同时更新 `web/vite.config.ts` 代理。

### Q: OKX 拉不到行情？

`sim_broker.py` 有三级降级：OKX → Binance → 缓存。如果都失败，用最后已知价格继续。日志会记 WARNING。

### Q: 如何切换 AI 模式？

```bash
python -m engine.live_trader --mode ai        # 1h AI 管道
python -m engine.live_trader --mode trend     # 4h 趋势策略
```

### Q: 迭代系统怎么手动触发？

```bash
python -m engine.iteration_monitor --once
```

---

**手册结束**

如有问题，参考 [SPEC.md](SPEC.md) + [SPEC_SUPPLEMENT.md](SPEC_SUPPLEMENT.md)。
