# 数据中心周报 Lark

拼稳定币市值周变动、DEX 现货周环比、CEX 合约周环比、协议收入周环比、Bitmine 当周 ETH。

不是 Lookonchain 同源。日更脚本里每天跑一次，同一周只发一次。

```bash
python lookonchain-week/flash.py
python lookonchain-week/flash.py --push --history-scan
```

阈值默认：稳定币 |Δ|≥5000 万美元；DEX/永续 |WoW|≥15%；协议收入 |WoW|≥10%；Bitmine |ΔETH|≥2 万。
过线另发【历史阈值警报】+【特殊警报】。
