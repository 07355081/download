"""绝对安全护栏：水位 / 互斥锁检测 / skip 优先。"""
from __future__ import annotations

import fcntl
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import _paths as P

sys.path.insert(0, str(P.PARENT))
import _watermark as WM  # noqa: E402


@dataclass
class GuardResult:
    ok: bool
    reason: str


def _lock_held(path: Path) -> bool:
    """非阻塞探测 path 是否被其他进程持有排他锁。"""
    if not path.exists():
        return False
    try:
        fd = os.open(str(path), os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        return False


def peer_busy(*, ignore_tradfi: bool = False) -> GuardResult:
    peers: list[tuple[str, Path]] = [
        ("coinglass-daily", P.COINGLASS_DAILY_LOCK),
        ("misc-daily", P.MISC_DAILY_LOCK),
    ]
    if not ignore_tradfi:
        peers.insert(0, ("tradfi-daily", P.TRADFI_DAILY_LOCK))
    for label, path in peers:
        if _lock_held(path):
            return GuardResult(False, f"peer busy: {label} ({path})")
    return GuardResult(True, "ok")


def disk_ok() -> GuardResult:
    st = WM.check()
    if not st.ok:
        return GuardResult(False, st.reason)
    return GuardResult(True, f"disk ok free={st.free_gb:.1f}G")


def preflight(*, ignore_tradfi_lock: bool = False) -> GuardResult:
    """水位 + 对端日更互斥。不获取本模块锁（由调用方 flock）。

    ignore_tradfi_lock=True：链式挂在 run_tradfi_daily 末尾时使用。
    """
    d = disk_ok()
    if not d.ok:
        return d
    p = peer_busy(ignore_tradfi=ignore_tradfi_lock)
    if not p.ok:
        return p
    return GuardResult(True, "ok")


class SpotLock:
    """本模块全局排他锁（snapshot / daily 互斥）。"""

    def __init__(self, path: Path = P.SPOT_LOCK) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        P.ensure_dirs()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "SpotLock":
        return self

    def __exit__(self, *args) -> None:
        self.release()
