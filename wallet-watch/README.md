# wallet-watch（非 Dune 地址监控）

监控链上地址异动，经 Etherscan / Tronscan / mempool.space 取数，**不消耗 Dune credits**。命中金额阈值或 Tornado / Binance 等对手方时推飞书。

名单：

- `watchlist.json` — Ledger / CryptoBilis
- `watchlist_usg_bitfinex.json` — 美国政府 Bitfinex 没收资金（Arkham usg1 标签参考）
- `watchlist_pump_sol.json` — Pump.fun 官方手续费收款地址 SOL 大额转出（`monitor_pump_sol.py`）

## 配置

在 `data-download/.env` 增加（强烈建议配 Etherscan，否则 ETH 只能每小时轮询 4 个地址）：

```bash
ETHERSCAN_API_KEY=your_key          # https://etherscan.io/myapikey
# 飞书也可读自 /root/dashboard/.env.live
LARK_ALERT_WEBHOOK=https://open.larksuite.com/open-apis/bot/v2/hook/...
LARK_ALERT_SECRET=                  # 机器人开了签名校验再填
```

可编辑：各 watchlist、`counterparties.json`。

## 用法

```bash
# 列出地址
python wallet-watch/monitor.py --dry-run
python wallet-watch/monitor.py --watchlist watchlist_usg_bitfinex.json --dry-run

# Ledger 首次上线：只记游标
python wallet-watch/monitor.py --bootstrap

# USG 首次：历史大额用【历史大额】抬头推送，并写入游标
python wallet-watch/monitor.py --watchlist watchlist_usg_bitfinex.json --history-alert --push

# 之后定期：有新告警才推飞书
python wallet-watch/monitor.py --push
python wallet-watch/monitor.py --watchlist watchlist_usg_bitfinex.json --push

# Pump.fun SOL 转出（Solana）
python wallet-watch/monitor_pump_sol.py --dry-run
python wallet-watch/monitor_pump_sol.py --bootstrap
python wallet-watch/monitor_pump_sol.py --push

# YZi Labs 标注地址 → 币安 ENA（日合计≥500万枚；bootstrap 不补历史）
python wallet-watch/monitor_yzi_ena.py --bootstrap
python wallet-watch/monitor_yzi_ena.py --push
```

阈值默认：ETH/TRX ≥ 1，稳定币 ≥ 10000，BTC ≥ 0.1。  
USG 名单 defaults：日常 BTC ≥ 100；历史回扫 ≥ 1000。超过约 6 小时的新检出日常也会改用【历史大额】抬头。

Pump SOL：日常转出 ≥ 10000 SOL → `【实时日常报告】`；≥ 6 小时前检出、历史回扫 ≥ 50000、或累计里程碑 → `【历史阈值警报】`。  
可选环境变量：`SOLANA_RPC_URL` 或 `HELIUS_API_KEY`（不配则用公共 RPC）。

## 定时（VPS）

```cron
10 * * * * /root/data-download/wallet-watch/run_hourly.sh
```

日志：`wallet-watch/_hourly.log`；状态：`output/state.json`、`output/state_watchlist_usg_bitfinex.json`、`output/state_watchlist_pump_sol.json`、`output/pump_sol_ledger.json`。
