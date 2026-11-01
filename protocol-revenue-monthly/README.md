# 协议自然月收入 Top15

DefiLlama `dailyRevenue`，同一 `parentProtocol` 合并后按 UTC 月排名。含 Tether、Circle。

```bash
python protocol-revenue-monthly/download.py
python protocol-revenue-monthly/flash.py
python protocol-revenue-monthly/flash.py --push --history-scan
```

阈值：前 15 任一协议环比增收 ≥ 1000 万美元，或前 15 中环比增长家数 ≥ 8。过线另发【历史阈值警报】+【特殊警报】。
