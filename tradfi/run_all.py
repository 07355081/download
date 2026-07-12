"""一键跑通 TradFi 原生 API 管道（阶段一：10 家永续 price+OI+funding）。

等价于：python download.py --intervals 1d
可加环境变量 HTTPS_PROXY 走代理（国内直连 Binance/Bybit 会被封）。
"""
from __future__ import annotations

import sys

import download

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv += ["--intervals", "1d"]
    download.main()
