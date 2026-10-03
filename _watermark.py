"""磁盘 + outbox 高水位护栏(下载中转管道共用:tradfi / coinglass-history)。

背景:VPS 根分区 ~25G、无独立数据盘。管道为 download→JSON(VPS)→to_parquet→outbox→本机拉走即删。
两处会涨盘:①下载时 output/json 累积;②本机离线拉不走时 outbox 的 parquet 堆积。
护栏据此设两道水位,任一触发即"暂停"(下载中止本轮剩余 / 派生停止写更多 / cron 预检直接跳过):
  - min_free_gb   :outbox 所在盘可用空间下限(同时覆盖 output/json,因同一根分区)。
  - max_outbox_gb :outbox 目录体积上限——超过说明本机没拉走,再产出只会撑爆盘。

阈值可用环境变量覆盖(shell CLI 与 python 库共用同一口径):
  WM_MIN_FREE_GB (默认 5.0)   WM_MAX_OUTBOX_GB (默认 15.0)

用法:
  库(download.py / to_parquet.py):
    from _watermark import check, Gate
    st = check()                      # -> Status(ok, free_gb, outbox_gb, reason)
    gate = Gate()                     # 线程安全、带缓存,供 threadpool worker 频繁调用
    if gate.blocked(): ...            # True 表示已触水位,应停止产出
  CLI(shell 预检,exit 0=放行 / 3=触水位):
    python _watermark.py --outbox /root/outbox
"""
from __future__ import annotations

import argparse
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_OUTBOX = Path("/root/outbox")
GB = 1024 ** 3


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except (ValueError, TypeError):
        return default


def min_free_gb() -> float:
    return _env_float("WM_MIN_FREE_GB", 5.0)


def max_outbox_gb() -> float:
    return _env_float("WM_MAX_OUTBOX_GB", 15.0)


def disk_free_gb(path: Path) -> float:
    """path 所在文件系统可用空间(GB)。path 不存在则回退到其存在的父目录。"""
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free / GB
    except OSError:
        return float("inf")  # 取不到就不误伤放行


def dir_size_gb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / GB


@dataclass
class Status:
    ok: bool
    free_gb: float
    outbox_gb: float
    reason: str


def check(
    outbox: Path = DEFAULT_OUTBOX,
    *,
    min_free: float | None = None,
    max_outbox: float | None = None,
) -> Status:
    mf = min_free_gb() if min_free is None else min_free
    mo = max_outbox_gb() if max_outbox is None else max_outbox
    free = disk_free_gb(outbox)
    used = dir_size_gb(outbox)
    if free < mf:
        return Status(False, free, used, f"磁盘可用 {free:.1f}G < 下限 {mf:.1f}G")
    if used > mo:
        return Status(False, free, used, f"outbox 体积 {used:.1f}G > 上限 {mo:.1f}G(本机未拉走?)")
    return Status(True, free, used, "ok")


class Gate:
    """线程安全、带缓存的水位门闩,供 threadpool worker 高频调用。

    一旦触水位即"粘住"(latched),之后 blocked() 恒为 True,避免半停半跑;
    未触水位时每 interval_s 秒最多真正 stat 一次,其余走缓存。

    interval_s 默认 300s 而非更短: check() 会 os.walk 整个 outbox(约 8 万个 parquet),
    间隔太小会在下载期间持续制造 stat 风暴, 本身就成了 IO 峰值的来源。
    """

    def __init__(self, outbox: Path = DEFAULT_OUTBOX, interval_s: float = 300.0) -> None:
        self.outbox = outbox
        self.interval_s = interval_s
        self._lock = threading.Lock()
        self._latched: Status | None = None
        self._last_ts = 0.0
        self._last: Status | None = None

    def status(self) -> Status:
        with self._lock:
            if self._latched is not None:
                return self._latched
            now = time.monotonic()
            if self._last is not None and (now - self._last_ts) < self.interval_s:
                return self._last
            st = check(self.outbox)
            self._last, self._last_ts = st, now
            if not st.ok:
                self._latched = st  # 粘住
            return st

    def blocked(self) -> bool:
        return not self.status().ok


def main() -> None:
    ap = argparse.ArgumentParser(description="磁盘/outbox 高水位预检(exit 3=触水位)")
    ap.add_argument("--outbox", default=str(DEFAULT_OUTBOX))
    ap.add_argument("--min-free-gb", type=float, default=None)
    ap.add_argument("--max-outbox-gb", type=float, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    st = check(Path(args.outbox), min_free=args.min_free_gb, max_outbox=args.max_outbox_gb)
    if not args.quiet:
        tag = "OK" if st.ok else "BLOCK"
        print(f"[watermark {tag}] free={st.free_gb:.1f}G outbox={st.outbox_gb:.1f}G :: {st.reason}")
    raise SystemExit(0 if st.ok else 3)


if __name__ == "__main__":
    main()
