# SPEC 补充 — 模拟合约交易平台 v0.2.1

> 对 SPEC v0.2.0 的补充，覆盖缺口与明确化
> 日期：2026-07-03

---

## 一、成功标准（Success Criteria）— SPEC 原文缺失

### 1.1 功能验收标准

| 场景 | 期望结果 |
|------|---------|
| AI 决策 LONG，10x 杠杆，10% 仓位 | sim_broker 1s 内落库，持仓显示正确浮盈亏 |
| 价格触及 SL | 触发平仓，realized_pnl 为负，equity 下降 |
| 价格触及 TP | 触发平仓，realized_pnl 为正，equity 上升 |
| 同一根 K 线同时触及 SL+TP | **SL 优先**（保守原则） |
| 保证金率 < 维持保证金 | 强平，exit_reason='liquidated' |
| 资金费率结算时刻 | 每 8h 按真实费率扣减持仓浮盈亏 |
| 服务重启 | 自动恢复 open 持仓到内存 |
| OKX 行情断连 | SL/TP 暂停检查，日志告警，不虚假触发 |

### 1.2 性能目标

| 指标 | 目标 |
|------|------|
| AI 决策→订单落库延迟 | < 1s |
| SL/TP 检查频率 | 每 5s（高频循环） |
| 前端 K 线刷新延迟 | < 2s |
| 权益曲线更新 | 每 30s |
| SQLite 单文件可持续运行 | ≥ 6 个月无清理 |

### 1.3 拟真度要求

> **"除了资金是假的，其他必须真"** — 这是项目最高原则

- 强平价格使用**动态重算**（非静态近似），随资金费用+浮亏实时调整
- 资金费率使用**真实 OKX 数据**（00:00/08:00/16:00 UTC），持仓不足 8h 按比例扣减
- 滑点模拟：市价单成交价 = ticker.last ± 0.02%（模拟市场冲击）
- 手续费：maker 0.02%、taker 0.05%（按真实 OKX 标准，AI 用市价单算 taker）

---

## 二、边界补充（Boundaries）— SPEC 原文缺失

### Always Do

- ✅ 用真实 OKX 行情（公开 API，无需 Key）
- ✅ 资金费率用真实数据（OKX `/api/v5/public/funding-rate`）
- ✅ 所有资金计算保留 8 位小数，避免浮点误差
- ✅ 订单状态变更必须落库 + 写日志
- ✅ 每个 API 端点有错误处理 + 合理 HTTP 状态码

### Ask First（不要擅自改）

- ⚠️ 新增依赖库
- ⚠️ 修改 AI 决策管道（5 层管道保持原样）
- ⚠️ 数据库 schema 变更

### Never Do

- ❌ **永不接入实盘 OKX API**（代码里硬编码 `SANDBOX=True`）
- ❌ **永不在配置里放真实 API Key**
- ❌ **永不修改 live_trader.py 让其连接实盘**
- ❌ 永不删除失败的测试来"让测试过"
- ❌ 永不硬编码真实账户地址或密钥

---

## 三、测试策略（Testing Strategy）— SPEC 原文缺失

### 3.1 测试分层

| 层级 | 范围 | 框架 | 文件 |
|------|------|------|------|
| 单元测试 | sim_broker 逻辑 | pytest | tests/test_sim_broker.py |
| 单元测试 | database CRUD | pytest | tests/test_database.py |
| 单元测试 | PnL/强平公式 | pytest | tests/test_pnl_math.py |
| 集成测试 | AI 决策→订单→平仓 | pytest | tests/test_sim_e2e.py |
| 烟雾测试 | 全链路启动 | manual | tests/test_smoke.py |

### 3.2 必测场景（覆盖 sim_broker 所有边界）

- [ ] 正常开多/开空 → 触 TP 平仓
- [ ] 正常开多/开空 → 触 SL 平仓
- [ ] 同根 K 线 SL+TP 同时触发 → SL 优先
- [ ] 保证金不足 → 拒绝开仓
- [ ] 保证金率 < 0.5% → 强平
- [ ] 资金费率结算：持仓 4h（应扣半份）
- [ ] 资金费率结算：持仓 8h（完整扣）
- [ ] 最大并发仓位 2，第 3 单拒绝
- [ ] 服务重启后恢复 open 仓位
- [ ] 空余额 + 已用保证金下开新单 → 拒绝

### 3.3 验收门槛

- 单元测试 100% 通过
- 集成测试全场景覆盖
- 烟雾测试：服务启动、收到 AI 决策、完成一次交易循环无报错

---

## 四、sim_broker 关键逻辑明确化

### 4.1 SL/TP 触发规则（填补 SPEC 4.1 歧义）

```
每 5 秒独立循环（不依赖 AI 的 15min 循环）:
  1. 拉取 OKX ticker.last (BTC, ETH)
  2. 对每个 open 仓位:
     a. 多仓: 如果 high ≥ tp_price → 平 TP
              如果 low  ≤ sl_price → 平 SL
              (同一根 K 线: SL 优先)
     b. 空仓: 如果 low  ≤ tp_price → 平 TP
              如果 high ≥ sl_price → 平 SL
              (同一根 K 线: SL 优先)
     c. 计算保证金率 → 低于维持保证金 → 强平
  3. 写权益快照
```

### 4.2 独立的 SL/TP 检查循环

```python
# sim_broker.py 内部
async def sl_tp_check_loop():
    """每 5s 检查一次 SL/TP，独立于 AI 15min 循环"""
    while True:
        for pos in db.open_positions():
            ticker = get_ticker(pos.pair)
            check_and_close(pos, ticker)
        await asyncio.sleep(5)
```

### 4.3 动态强平价格（非静态近似）

```python
def calc_liquidation_price(pos, current_funding_accrued):
    """强平价随资金费用和浮亏实时调整"""
    maintenance = pos.size * 0.005  # 维持保证金
    available_margin = pos.margin - current_funding_accrued - pos.realized_pnl
    if pos.side == 'long':
        liq_price = pos.entry_price * (1 - available_margin / pos.size + 0.005)
    else:
        liq_price = pos.entry_price * (1 + available_margin / pos.size - 0.005)
    return liq_price
```

### 4.4 资金费率结算细节

```python
def settle_funding(pos, funding_rate):
    """
    OKX 结算时刻: 00:00 / 08:00 / 16:00 UTC
    持仓不足 8h: 按持仓时长比例扣
    """
    hours_held = (now - pos.entry_time).total_seconds() / 3600
    if hours_held < 8:
        # 按持仓时长比例（至少扣满 1 小时份额）
        ratio = max(hours_held / 8, 1/8)
        funding_charge = pos.size * funding_rate * ratio
    else:
        funding_charge = pos.size * funding_rate

    # 多仓付正费率，空仓收正费率（反之亦然）
    if pos.side == 'long':
        pos.funding_paid += funding_charge  # 多仓付费
    else:
        pos.funding_paid -= funding_charge  # 空仓收费
```

### 4.5 滑点模拟

```python
def fill_price(ticker_last, side, is_taker=True):
    """市价单成交价 = ticker.last ± 滑点"""
    slip = 0.0002  # 0.02%
    if side == 'long':
        return ticker_last * (1 + slip)
    return ticker_last * (1 - slip)
```

### 4.6 手续费

```python
ENTRY_FEE_RATE = 0.0005   # 0.05% taker
EXIT_FEE_RATE = 0.0005    # 0.05% taker

entry_fee = contracts * entry_price * ENTRY_FEE_RATE
exit_fee = contracts * exit_price * EXIT_FEE_RATE
```

---

## 五、数据流修正（SPEC 3.3）

原文数据流未体现独立的 SL/TP 循环。修正为：

```
┌─ OKX 公开行情 ──→ 前端 K 线 (每 30s 轮询 /market/klines)
│                    └→ 前端 ticker (每 3s 轮询 /market/ticker)
│
├─ AI 交易循环 (每 15min)
│     ├→ 特征工程
│     ├→ AI 管道
│     ├→ 决策仲裁
│     └→ sim_broker.open_order() (新增)
│
└─ SL/TP/强平 检查循环 (每 5s) ← 新增独立循环
      ├→ 拉 OKX ticker
      ├→ 检查每个 open 仓位 SL/TP/强平
      ├→ 触发时 sim_broker.close_order()
      └→ 写权益快照

─ 资金费率结算循环 (00:00/08:00/16:00 UTC) ← 新增
      ├→ 拉 OKX 当前 funding rate
      └→ 对每个 open 仓位 settle_funding()

─ 服务重启恢复 (启动时) ← 新增
      └→ 从 SQLite 加载 open 仓位到内存
```

---

## 六、OKX 断连降级（SPEC 原文缺失）

| 场景 | 行为 |
|------|------|
| ticker 拉取失败 1 次 | 用上次价格继续，告警 |
| 连续 3 次失败 | SL/TP 检查暂停，写日志 |
| 行情恢复 | 用**最后已知价格**检查一次 SL/TP，再恢复 5s 循环 |
| 期间发生的价格跳空 | **不模拟**（模拟盘无法捕捉，接受这个损失） |

---

## 七、SQLite 数据清理（SPEC 原文缺失）

- `equity_snapshots` 保留最近 30 天（每 30s 一条，约 86400 条）
- `positions` 保留所有（closed 状态也保留，用于统计）
- `ai_decisions` 保留最近 90 天
- 启动时自动清理过期数据

---

## 八、重启恢复（SPEC 原文缺失）

服务启动时（`sim_broker.start()`）：
1. 从 SQLite 加载 status='open' 的所有仓位到内存
2. 立即拉一次 OKX ticker 计算浮盈亏
3. 启动 SL/TP 循环

---

## 九、OKX 公开数据降级策略

SPEC 说"OKX 数据拿不到可以从其他地方拿"。具体：

| 数据类型 | 主源 | 备源 |
|---------|------|------|
| ticker.last | OKX `/api/v5/market/ticker` | Binance `/api/v3/ticker/price` |
| funding rate | OKX `/api/v5/public/funding-rate` | Binance `/fapi/v1/fundingRate` |
| K 线 | OKX `/api/v5/market/candles` | Binance `/api/v3/klines` |

切换逻辑：主源失败 3 次自动切备源，记录日志。

---

## 十、激进目标配置（SPEC 二原文未体现）

> "除了钱是假的，其他必须真" + "不用保守，看 $1000 短时间能做到什么上限"

### 10.1 AI 管道行为

SPEC 3.1 说 AI 管道"保持不变"——但原 AI 模型已验证 dir_acc 0.502。为了测试"上限"，建议：
- **允许用户选择 AI 模式**：保守（原模型）vs 激进（放宽置信度/止损阈值）
- SPEC 当前"AI 决策保持不变"，但 4h 策略的 trend_filter 等改进是有效的——建议把 4h 趋势策略集成进去替代原 AI 模型，或者让用户配置

### 10.2 杠杆

SPEC 写"杠杆 AI 决策，倍数不限"——这可能导致单次爆仓。建议：
- AI 管道输出杠杆建议（当前 5x 上限）
- 模拟盘**允许突破此上限**测试激进策略，但记录并警告

### 10.3 激进模式开关

建议新增 `--aggressive` 标志：
- 放大 AI 置信度阈值（从 0.55 降到 0.45）
- 取消 5x 杠杆上限
- 取消 20% 仓位上限
- 保留 1000 USDT 初始资金固定

这样 SPEC 的"游戏"目标得以最大化测试，同时保留默认保守模式。

---

## 十一、Phase 顺序调整

基于补充，建议实施顺序：

1. **Phase 1** — database.py + sim_broker.py 核心（含 SL/TP/强平逻辑 + 单元测试）
2. **Phase 2** — API 扩展 + SL/TP 检查循环 + 资金费率循环
3. **Phase 3** — 前端重构（保留现有 web/ 结构）
4. **Phase 4** — 集成测试 + 端到端
5. **Phase 5** — 激进模式 + 重启恢复 + OKX 断连处理

---

## 十二、验收流程

每 Phase 完成后必须：
1. 跑对应层级测试全过
2. 写简短验证报告（命令+输出截图/日志）
3. 用户 review 后才能进入下一 Phase

---

## 十三、确认的架构决策（2026-07-03）

### 决策 1：AI 模型 — 两套并存可切换

`live_trader.py` 新增 `--mode` 参数：
- `--mode ai`（默认）：原 1h AI 管道（direction_predictor + regime_classifier + RL 双信号），保持 SPEC "AI 决策保持不变"
- `--mode trend`：4h 趋势策略（trend_strategy + trend_filter + slope_confirm），经验证有边际

两套都接入 sim_broker，可对比"原 AI" vs "4h 趋势"在模拟盘的真实表现。

### 决策 2：激进模式开关

`live_trader.py` 新增 `--aggressive` 标志：

| 参数 | 默认（保守） | --aggressive |
|------|------------|-------------|
| 置信度阈值 | 0.55 | 0.45 |
| 杠杆上限 | 5x | 无上限（AI 决定） |
| 仓位上限 | 20% | 无上限 |
| 止损下限 | 0.5% | 0.3% |
| regime 阻断 | 开 | 关（震荡市也开仓） |

默认保守模式跑稳定基线，`--aggressive` 测"$1000 能短时间做到什么上限"。

### 决策 3：架构选型 — 方案 A（自写 sim_broker）

已确认：自写 sim_broker + SQLite，不用 OKX 官方模拟盘。理由：
- 完全可控、可定制强平/资金费率逻辑
- 不依赖 OKX 模拟盘 API（可能限流或变更）
- 数据公开，从 OKX/Binance 拉真实行情喂给 sim_broker

---

## 十四、周期性迭代系统（新增）

### 14.1 目标

让模拟盘不只是"跑着看"，而是**周期性自评估 + 迭代**：
- 每周自动生成周期报告（收益/Sharpe/回撤/胜率/PF，按策略/币种/regime 分维度）
- 迭代参数：walk-forward 重寻优，达标则热替换
- 迭代模型：累积足够新数据后，用模拟盘真实交易数据重训

### 14.2 数据收集（已在 SPEC 三表设计内）

模拟盘每笔交易和决策已落库：
- `positions` 表：每笔交易明细（入场/出场/PnL/SL/TP/ai_confidence/regime）
- `equity_snapshots` 表：每 30s 权益快照（用于算 Sharpe/回撤）
- `ai_decisions` 表：每次 AI 决策（含未执行的 HOLD）

这三表数据足够支撑周期分析，无需新增表。

### 14.3 周期报告脚本

新增 `scripts/periodic_review.py`：

```python
def generate_weekly_report(db_path, output_md):
    """从 SQLite 读数据，生成周报"""
    # 1. 本周交易统计（收益/Sharpe/回撤/胜率/PF）
    # 2. 按 --mode 分（ai vs trend）对比
    # 3. 按 --aggressive 分（保守 vs 激进）对比
    # 4. 按币种分（BTC vs ETH）
    # 5. 按 regime 分（趋势 vs 震荡时段表现）
    # 6. 回测可信度验证：实际表现 vs walk-forward 预测差异
    # 7. 输出 MD 报告 + 关键指标 JSON
```

### 14.4 迭代逻辑（分阶段）

**阶段 1：仅参数迭代（前 1-2 个月）**

模拟盘数据不足以重训模型（需 ~1000 笔交易），只做参数迭代：
- 每周用累积的模拟盘数据 + 历史 OHLCV 跑 walk-forward 重寻优
- 新参数 vs 当前参数对比，Sharpe 提升 ≥ 10% 才替换
- 替换到 `models_real/`，下次 live_trader 启动自动加载

**阶段 2：模型迭代（累积 ≥ 1000 笔交易后）**

```python
def maybe_retrain(db_path, min_trades=1000):
    """累积足够数据后重训模型"""
    trades = count_trades(db_path)
    if trades < min_trades:
        return "数据不足，跳过重训"
    # 1. 用模拟盘真实交易数据 + 历史 OHLCV 合并训练
    # 2. walk-forward 严格验证新模型
    # 3. 新模型 Sharpe 提升 ≥ 5% 且达标率 ≥ 旧模型 → 替换
    # 4. 否则保留旧模型，记录原因
```

### 14.5 触发方式：Cron

用 Claude Code 的 CronCreate，每周固定时间触发：

```
每周一 09:00 触发：
  python scripts/periodic_review.py --week
  python scripts/iterate_params.py  # 阶段 1
  python scripts/maybe_retrain.py    # 阶段 2（数据不足时自动跳过）
```

注意：Cron 仅在 Claude 会话开着时触发。需要长期运行的话，未来可改成 Windows Task Scheduler。

### 14.6 诚实预期

基于前期验证：
- 方向预测器 dir_acc 0.502，迭代参数/模型上限有限
- 真正有价值的"迭代产出"是：
  1. **回测可信度验证**：模拟盘实际表现 vs walk-forward 预测差异，若差异大说明回测不可信
  2. **策略退化预警**：4h 趋势在 W2/W3 失效，模拟盘能验证实时是否也退化
  3. **持续市场认知**：每周报告让你看清"哪种行情下策略有效"
- **不期待"迭代出印钞机"**：信息论上限摆在那，迭代是优化边际不是突破瓶颈

### 14.7 新增文件

| 文件 | 职责 |
|------|------|
| `scripts/periodic_review.py` | 周期报告生成 |
| `scripts/iterate_params.py` | 参数迭代（walk-forward 重寻优） |
| `scripts/maybe_retrain.py` | 模型迭代（数据不足时跳过） |

---

## 总结：本次 SPEC 补充覆盖的缺口

| 原缺口 | 本补充覆盖 |
|--------|----------|
| 无 Success Criteria | ✓ 第 1 节 |
| 无 Boundaries | ✓ 第 2 节 |
| 无 Testing Strategy | ✓ 第 3 节 |
| SL/TP 触发规则歧义 | ✓ 第 4.1 节 |
| 无独立 SL/TP 循环 | ✓ 第 4.2 节 |
| 强平价静态近似 | ✓ 第 4.3 节（动态重算） |
| 资金费率结算不精确 | ✓ 第 4.4 节 |
| 无滑点模拟 | ✓ 第 4.5 节 |
| 无手续费 | ✓ 第 4.6 节 |
| 数据流缺失 SL/TP 循环 | ✓ 第 5 节 |
| 无 OKX 断连处理 | ✓ 第 6 节 |
| 无数据清理 | ✓ 第 7 节 |
| 无重启恢复 | ✓ 第 8 节 |
| 无 OKX 降级策略 | ✓ 第 9 节 |
| 激进目标未体现 | ✓ 第 10 节 |

所有缺口已覆盖。SPEC v0.2.1 可执行。
