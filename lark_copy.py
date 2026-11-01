# -*- coding: utf-8 -*-
"""Turn a Lark payload into copy that can be published as news."""
from __future__ import annotations

import re

_MARKS = {
    "【历史阈值警报】",
    "【实时日常报告】",
    "【特殊警报】",
    "【季度特殊警报】",
}
_DROP = (
    "本条为",
    "请勿当成",
    "不是例行",
    "不是当日流水",
    "不是刚收完",
    "不是当日例行",
    "历史回扫",
    "口径非 Token Terminal",
)


def as_news(text: str) -> str:
    lines: list[str] = []
    for raw in str(text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line in _MARKS:
            continue
        parts = re.split(r"(?<=。)", line)
        kept = [part for part in parts if part and not any(token in part for token in _DROP)]
        merged = "".join(kept).strip()
        if merged:
            lines.append(merged)
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        raise RuntimeError("empty news")
    return cleaned
