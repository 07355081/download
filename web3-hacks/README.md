# web3-hacks（DefiLlama Web3 安全事件月损）

免费公开接口 `https://api.llama.fi/hacks`，不占 Dune / CoinGlass。

## 口径

- 按 **UTC 自然月**汇总事件次数与 `amount`（美元损失）
- **不是** GoPlus 中文社区「重大事件」统计；次数/金额常对不上，写稿须注明 DefiLlama
- 完整月：当前 UTC 日期已进入下一自然月后，该月标为 `complete`

## 产出

- `output/json/web3_hacks.json` → 可同步到看板 `public/json/web3-hacks/`
- Lark：`flash.py --push`
  - 【实时日常报告】每个刚闭合自然月一条
  - 【历史阈值警报】月损创新高、|月环比|≥100%（可调）、或单笔最大≥1 亿美元

## 用法

```bash
python download.py
python flash.py                 # dry-run
python flash.py --push
python flash.py --push --history-scan
```

环境变量（可选）：

- `WEB3_HACKS_MOM_PCT` 默认 `100`
- `WEB3_HACKS_SINGLE_USD` 默认 `100000000`
- `LARK_ALERT_WEBHOOK` / `LARK_ALERT_SECRET`

misc 日更约 05:00 会拉取并尝试推送（同月已发则跳过）。
