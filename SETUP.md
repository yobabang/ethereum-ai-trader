# 前置准备指南

> 在写一行代码之前需要搞清楚的事情

---

## 一、交易所选型

### 候选对比

| 维度 | OKX | Binance | Bybit |
|------|-----|---------|-------|
| 期货 API 稳定性 | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| 大陆用户友好度 | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ |
| Freqtrade 期货支持 | ✅ 完善 | ✅ 完善 | ✅ 完善 |
| BTC/ETH 流动性 | 极高 | 极高 | 高 |
| 费率 | Maker 0.02% / Taker 0.05% | Maker 0.02% / Taker 0.04% | Maker 0.01% / Taker 0.06% |
| KYC 门槛 | 较低 | 高 | 中 |
| ccxt 文档 | 完善 | 完善 | 完善 |

### 推荐：OKX

理由：
- Freqtrade 已有完整的期货实现（`okx.py`，含保证金模式、持仓模式检测）
- 大陆访问相对友好
- API 文档完善，支持 WebSocket 私有频道
- 永续合约 USDT 本位对 `BTC/USDT:USDT` 和 `ETH/USDT:USDT` 流动性极好

---

## 二、交易所账户准备

### 2.1 OKX 准备步骤

```
1. 注册 OKX 账号 → 完成 KYC 认证
2. 进入 API 管理 → 创建 API Key
   权限必须勾选：
     ✅ 交易 (Trade)
     ✅ 读取 (Read)
     ⬜ 提币 (Withdraw) → 不勾！
3. 把 USDT 转入 OKX 资金账户
4. 在 OKX 网页端手动将 USDT 从「资金账户」划转到「交易账户」
   （API 无法执行跨账户划转，这一步必须人工）
5. 开通合约交易（网页端点一下同意协议即可）
```

### 2.2 安全提醒

- **API Key 只勾交易权限，绝对不勾提币权限**
- 初始只用少量资金测试（建议 200-500 USDT）
- 确认设置了 IP 白名单（OKX 支持 API Key 绑定 IP）

---

## 三、合约自动开启机制

### 3.1 Freqtrade 已经自动做了的事

Freqtrade 在每次开仓前**自动完成**以下操作（我们 Fork 后保留）：

```
execute_entry() 调用链:
  1. exchange.set_margin_mode(pair, "isolated")     ← 设置逐仓模式
  2. exchange._set_leverage(leverage, pair)          ← 设置杠杆倍数
  3. exchange.create_order(... leverage=leverage)    ← 带杠杆参数下单
```

这些不需要我们额外实现——freqtrade 的 `exchange.py` 已经在 `create_order` 前自动调用 `set_margin_mode` 和 `_set_leverage`（见 `exchange.py:1408-1411`）。

### 3.2 我们需要额外做的

| 事项 | 原因 | 实现位置 |
|------|------|----------|
| 启动时检测账户模式 | OKX 有 net_mode（全仓）和 双向持仓模式 | `additional_exchange_init()` |
| 启动时验证合约对可用 | 确认 `BTC/USDT:USDT` 和 `ETH/USDT:USDT` 可交易 | `__init__` 后调用 `validate_pairs()` |
| 异常时自动重试 | 网络波动导致设置失败 | `@retrier` 装饰器(已有) |
| 启动时资金划转提醒 | 如果合约账户余额不足，提示用户人工划转 | 新增 `check_futures_balance()` |

### 3.3 我们不用担心的

- ❌ 不需要手动调 API 创建合约账户 — OKX 统一账户自动支持
- ❌ 不需要手动设置持仓模式 — Freqtrade 自动从 API 读取
- ❌ 不需要每次交易重新设杠杆 — Freqtrade 在开仓时自动设

---

## 四、配置文件设计

### 4.1 `config.json`

```json
{
  "exchange": {
    "name": "okx",
    "key": "从 OKX API 管理页面获取",
    "secret": "从 OKX API 管理页面获取",
    "password": "OKX API 的 passphrase",
    "ccxt_config": {
      "enableRateLimit": true
    }
  },
  "trading_mode": "futures",
  "margin_mode": "isolated",
  "pair_whitelist": [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT"
  ],
  "stake_currency": "USDT",
  "stake_amount": "unlimited",
  "max_open_trades": 3,
  "dry_run": false,
  "ai": {
    "max_leverage": 5,
    "max_position_pct": 0.20,
    "max_drawdown_pct": 0.15,
    "daily_loss_limit_pct": 0.05,
    "min_confidence": 0.55,
    "train_interval_hours": 4,
    "train_period_days": 60,
    "regime_train_days": 30,
    "backtest_days": 7
  },
  "api_server": {
    "enabled": true,
    "listen_ip_address": "127.0.0.1",
    "listen_port": 8080,
    "jwt_secret_key": "随机生成一个长字符串"
  },
  "datadir": "./user_data/data",
  "db_url": "postgresql+psycopg2://trader:password@localhost:5432/ethereum_trader"
}
```

### 4.2 环境变量覆盖（更安全）

敏感信息不要写在 config.json 里，用环境变量：

```bash
export FREQTRADE__EXCHANGE__KEY="your-api-key"
export FREQTRADE__EXCHANGE__SECRET="your-api-secret"
export FREQTRADE__EXCHANGE__PASSWORD="your-passphrase"
export DATABASE_URL="postgresql://trader:password@localhost:5432/ethereum_trader"
```

---

## 五、数据准备

### 5.1 下载历史数据（模型训练用）

```bash
# 下载 BTC 和 ETH 的 4小时 K线（够 60 天训练）
freqtrade download-data \
  --exchange okx \
  --pairs BTC/USDT:USDT ETH/USDT:USDT \
  --timeframes 4h 1h 15m \
  --timerange 20260101- \
  --trading-mode futures

# 也下载订单簿数据（如果策略要用）
freqtrade download-data \
  --exchange okx \
  --pairs BTC/USDT:USDT ETH/USDT:USDT \
  --timeframes 4h \
  --timerange 20260101- \
  --trading-mode futures \
  --dl-type orderbook
```

### 5.2 实时数据

Freqtrade 启动后自动通过 WebSocket 订阅实时 K 线，不需要额外配置。

---

## 六、Docker 开发环境

```yaml
# docker-compose.yml
version: '3.8'
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: trader
      POSTGRES_PASSWORD: password
      POSTGRES_DB: ethereum_trader
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  bot:
    build: .
    depends_on:
      - db
    environment:
      - DATABASE_URL=postgresql://trader:password@db:5432/ethereum_trader
      - FREQTRADE__EXCHANGE__KEY=${OKX_API_KEY}
      - FREQTRADE__EXCHANGE__SECRET=${OKX_API_SECRET}
      - FREQTRADE__EXCHANGE__PASSWORD=${OKX_API_PASSWORD}
    ports:
      - "8080:8080"
    volumes:
      - ./user_data:/freqtrade/user_data
      - ./models:/freqtrade/models

  web:
    build: ./web
    ports:
      - "3000:3000"
    environment:
      - VITE_API_URL=http://localhost:8080

volumes:
  pgdata:
```

---

## 七、启动检查清单

在启动实盘交易前，逐项确认：

| # | 检查项 | 通过标准 |
|---|--------|----------|
| 1 | API Key 只读+交易权限 | 无提币权限 |
| 2 | 合约账户有 USDT 余额 | ≥ 200 USDT |
| 3 | dry_run=true 先跑一遍 | 无错误，AI 产出决策日志 |
| 4 | 回测达标 | 夏普>0.5, 回撤<15% |
| 5 | 安全规则单元测试通过 | 8 条规则全部覆盖 |
| 6 | Docker Compose 启动成功 | bot + db + web 全部 running |
| 7 | Web 面板可访问 | localhost:3000 看到仪表盘 |

---

## 八、不做的（再次明确）

- ❌ 不做现货 → 只做合约
- ❌ 不做其他币种 → 只做 BTC/ETH
- ❌ 不做法币入金/出金 → 纯 USDT 本位
- ❌ 不做跨交易所 → 只用 OKX
