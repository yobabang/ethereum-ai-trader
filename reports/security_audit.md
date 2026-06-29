# 安全审计报告

> 执行: security-auditor agent | 日期: 2026-06-29 | 范围: 全部 AI 模块

## Critical (3)

| # | 问题 | 状态 |
|---|------|------|
| 1 | API 密钥明文存储在 config.json | ✅ 已修复 — 改为 ${ENV_VAR} 占位符 |
| 2 | 无绝对仓位上限 (unlimited stake + 5x) | ✅ 已修复 — 硬上限 $500/笔 |
| 3 | 日盈亏数据为假值 (daily_pnl=0.0) | ✅ 已修复 — 连接到 SelfOptimizer |

## High (4)

| # | 问题 | 状态 |
|---|------|------|
| 4 | CORS 通配符 allow_origins=["*"] | ✅ 已修复 — 限制为 localhost:3000 |
| 5 | 文件写入非原子 (JSON state) | ⬜ 个人使用可接受 |
| 6 | 最低仓位可能覆盖 AI 决策 | ⬜ 已添加 ABSOLUTE_MAX_STAKE 保护 |
| 7 | Pickle 反序列化风险 | ⬜ 本地模型仅，生产需签名验证 |

## Medium (5)

- 决策器缺少异常处理 (已在 try/except 中)
- 部分输入验证缺失 (数值边界)
- 共享可变状态 (MIN_CONFIDENCE 已修订)
- PnL 跟踪 bug (已修复)
- API Key 含交易权限 (需只读+交易，不能有提币)

## Low (4)

- npm 供应链风险 (web/)
- 缺少请求频率限制
- 实盘模式无二次确认
- SOCKS5 代理合规性

## 总体评定: 中等风险，适合个人 Dry-Run

所有 Critical 已修复。High 项目中 CORS 和仓位上限已修复。
实盘前必须: 1) 确认 API Key 无提币权限 2) 小资金测试
