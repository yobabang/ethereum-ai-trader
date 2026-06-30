# 衍生品数据拉取 — 运行说明书

> 本说明书面向**另一台能联网的设备**。在那台设备上跑通脚本，把产出的 feather 文件拷回交易机即可。
> 全程**只读公开行情、绝不下单、无需 API Key、无需登录**。

---

## 一、这台设备要做什么（对方设备）

### 1. 准备环境（Python 3.10+）

```bash
pip install ccxt pandas pyarrow
```

如果 OKX 在你所在地区需要代理（如 v2rayN），记下代理地址（例如 `socks5h://127.0.0.1:10808`）。

### 2. 拿到脚本

从本仓库取 `scripts/pull_derivatives_data.py` 这一个文件即可（单文件，无其他依赖）。

> **重要**：默认用 `binance` 交易所（v2 脚本默认值）。之前用 OKX 拉，OI 和多空比几乎全空（OKX rubik 历史接口保留期限制）。Binance 的 fapi/data 端点对 OI/多空比/taker 历史保留更完整。

### 3. 运行

```bash
# 标准运行（v2 默认 binance + 22000h，与 OHLCV 2.5年对齐）
python pull_derivatives_data.py

# 如需代理：
python pull_derivatives_data.py --proxy socks5h://127.0.0.1:10808

# 拉不到全部历史可缩短（见下方"保留期"说明）：
python pull_derivatives_data.py --hours 6000

# 仍想试 OKX：
python pull_derivatives_data.py --exchange okx
```

### 4. ⚠️ 关于历史保留期（必读）

**Binance 和 OKX 对衍生品历史数据都有保留期限制**，通常只能拉到**最近 6 个月**左右（funding rate 可能更久，OI/多空比/taker 通常 6 个月）。所以即使 `--hours 22000`（2.5 年），实际可能只拉到最近几个月。这是交易所的硬限制，无法绕过。

**脚本会诚实报告实际拉到的行数和覆盖范围**。如果只能拉到几个月：
- 仍然有用——最近几个月的数据对 walk-forward 的最新窗口验证有效
- 只是训练样本会少，有效性结论的范围受限

**不要慌**：拉到多少用多少，脚本和下游管线都做了缺失容错。

### 5. 产物

脚本运行后，在当前目录下生成：

```
user_data/data/okx/BTC_USDT_USDT-derivatives-1h-futures.feather
user_data/data/okx/ETH_USDT_USDT-derivatives-1h-futures.feather
```

**把这两个 feather 文件拷回交易机**（走企业微信/U盘/内网共享均可）。

---

## 二、数据规格（供核对）

每个 feather 文件，每行 1 根 1h K 线，列如下：

| 列名 | 类型 | 说明 |
|------|------|------|
| `date` | int (ms epoch, 13位) | 时间戳，**必须与 OHLCV feather 的 date 对齐**（同一时刻、同一时区） |
| `funding_rate` | float | 8h 资金费率（如 0.0001 = 0.01%），已前向填充到 1h |
| `funding_rate_next` | float | 下一次预期费率（可选，缺失则同 funding_rate） |
| `open_interest` | float | USDT 计价持仓量 |
| `open_interest_change_1h` | float | 1h 变化率（脚本自动算） |
| `long_short_ratio` | float | 多空账户比（如 1.2 = 多头略多） |
| `taker_buy_sell_ratio` | float | 主动买/卖量比（可选，缺失自动填 NaN） |

**关键**：`date` 列必须是**毫秒整数**（如 `1704038400000`），和现有 OHLCV feather 一致。脚本已自动处理，无需手动调整。

### 哪些数据是必须的、哪些可选

- **必须**：`funding_rate`（资金费率）—— 这是核心领先信号
- **强烈建议**：`open_interest`、`long_short_ratio`
- **可选**：`taker_buy_sell_ratio`、`funding_rate_next`

脚本对每类数据**独立容错**：某类拉不到不影响其他类，缺失列自动填 NaN，下游照常运行。

---

## 三、拷回交易机后的处理

### 1. 放到正确位置

把两个 feather 放到交易机的：

```
d:\claudeProject\ethereum-ai-trader\user_data\data\okx\
    BTC_USDT_USDT-derivatives-1h-futures.feather
    ETH_USDT_USDT-derivatives-1h-futures.feather
```

（和现有的 `BTC_USDT_USDT-1h-futures.feather` 同目录）

### 2. 验证数据对齐（交易机上跑）

```bash
cd d:\claudeProject\ethereum-ai-trader
python -c "import pandas as pd; from engine.features import _to_datetime; \
o=pd.read_feather('user_data/data/okx/BTC_USDT_USDT-1h-futures.feather'); \
d=pd.read_feather('user_data/data/okx/BTC_USDT_USDT-derivatives-1h-futures.feather'); \
print('OHLCV rows:', len(o), 'deriv rows:', len(d)); \
print('aligned:', (_to_datetime(o['date']).iloc[0]==_to_datetime(d['date']).iloc[0]))"
```

期望输出：`aligned: True`。若 `False`，说明时间戳没对齐，需要检查（见下方 FAQ）。

### 3. 用衍生品数据重训模型

```bash
python -m engine.trainer --datadir user_data/data \
    --pairs BTC/USDT:USDT ETH/USDT:USDT \
    --model-dir ./models_real_deriv \
    --timeframe 1h --derivatives
```

日志应出现 `Features (OHLCV+deriv) computed ... 70 cols` 和 `Loaded N derivatives rows`。

### 4. 严格样本外验证（核心）

```bash
# 对比：纯 OHLCV vs OHLCV+衍生品
python scripts/walkforward_verify.py --windows 3 --compare
```

会输出每个时间窗口的方向准确率、Sharpe、回撤、胜率、盈亏比，以及"达标窗口占比"。**这才是判断模型是否真正有效的依据**（单次回测会过拟合，walk-forward 不会骗人）。

---

## 四、常见问题（FAQ）

**Q1：脚本跑到一半报 `rate limit` / 超时？**
A：正常。脚本内置重试 + 退避。若频繁失败，加大间隔重跑，或换 `--exchange binance`。

**Q2：某类数据（如 long_short_ratio）拉到 0 行？**
A：OKX 对部分历史数据有保留期限制。脚本会跳过该类、继续拉其他类，不影响整体。Binance 的历史保留通常更久。

**Q3：拷回后 `aligned: False`？**
A：两台设备时区不同导致。检查 OHLCV feather 的 `date` 实际时刻——衍生品必须用相同时区。脚本默认用交易所返回的 UTC 毫秒时间戳，与 OHLCV 一致。若不一致，在交易机上重新拉 OHLCV 或调整衍生品时区。

**Q4：脚本会不会下单 / 用我的 API Key？**
A：**绝对不会**。脚本顶部硬声明只读，只用公开行情端点（`fetch_funding_rate_history` 等），不传 API Key、不调任何下单/账户接口。可放心运行。

**Q5：拉多少数据合适？**
A：与现有 OHLCV 对齐最好（约 22000 小时 = 2.5 年）。至少 4000 小时（5 个月）才能训练出有意义的时序特征（z-score 需要足够窗口）。

---

## 五、本次改造已完成的代码（供交易机端参考）

以下改动**已经在交易机的代码里完成并自测通过**，对方设备只需拉数据、不需要改代码：

| 文件 | 改动 |
|------|------|
| `engine/features.py` | 新增 `compute_derivatives_series()`（时序衍生品特征：z-score/变化率/极值）+ `compute_all_features()`（OHLCV+衍生品合并）+ `_to_datetime()`（robust 时间对齐） |
| `engine/trainer.py` | 新增 `load_derivatives_data()` + `--derivatives` 开关 + `use_derivatives` 参数 |
| `engine/backtest_adapter.py` | `run()` 接收 `derivatives`，合并特征，**传 `funding_signal` 给安全规则**（复活规则5：极端费率拒单），funding 费用改用真实费率 |
| `engine/direction_predictor.py` / `regime_classifier.py` | `predict()` 修复特征数不匹配崩溃（缺失列补 NaN） |
| `scripts/walkforward_verify.py` | 新建：严格滚动训练+验证，支持 `--compare` 对比有无衍生品 |
| `scripts/pull_derivatives_data.py` | 新建：对方设备运行的拉取脚本 |

自测已修复 1 个真实 bug（衍生品模型 + 无衍生品数据时 LightGBM 特征数崩溃），单测 50 passed。

---

## 六、诚实预期（必读）

- 衍生品数据预计把方向准确率从 ~0.50 提升到 **0.54-0.58**（不是质变，从"随机"到"微弱优势"）。
- walk-forward 是**诚实的裁判**：若达标窗口占比 < 50%，说明模型仍不可靠，不建议上线——这不是失败，是避免用真金白银试错。
- 1h 周期手续费摩擦高，即使准确率提升也未必盈利。若走通，下一步建议拉长到 4h。
