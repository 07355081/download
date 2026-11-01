#!/usr/bin/env python3
"""Paymentscan 首页加密卡「按卡商」月成交（公开 HTML，无 Dune）。

页面嵌入两套 projects 序列；取 RedotPay 累计更接近官网榜单（约 69.7 亿）的那套，
对应 spends 口径（含部分自报/链下），不是 obchakevich Dune。

输出：output/json/crypto_card_programs.json
"""
from __future__ import annotations

import json
import re
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "crypto_card_programs.json"
PAGE_URL = "https://paymentscan.xyz/"
UA = "Mozilla/5.0 dashboard-data/crypto-card-programs"
ROW_RE = re.compile(
    r'\{date:"(\d{4}-\d{2}-\d{2})",project:"([^"]+)",volumes:([0-9.]+),txs:([0-9.]+),users:([0-9.]+)'
)
PROJECTS_RE = re.compile(r"projects:\$R\[\d+\]=\[")
MIN_MONTHS = 12
# 官网榜单 RedotPay all-time 约 6.966B；用累计接近度选 spends 数组
REDOT_CUM_TARGET = 6.966e9


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_page() -> str:
    req = urllib.request.Request(PAGE_URL, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        if resp.status != 200:
            raise RuntimeError(f"page status {resp.status}")
        return resp.read().decode("utf-8", "replace")


def parse_chunks(html: str) -> list[dict[str, dict[str, float]]]:
    starts = [m.start() for m in PROJECTS_RE.finditer(html)]
    if len(starts) < 1:
        raise RuntimeError("页面未找到 projects 数组")
    chunks: list[dict[str, dict[str, float]]] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else min(len(html), start + 400_000)
        rows = ROW_RE.findall(html[start:end])
        bucket: dict[str, dict[str, float]] = defaultdict(dict)
        for day, project, volume, _txs, _users in rows:
            # 同月同名取后者（数组内偶发重复）
            bucket[day[:7]][project] = float(volume)
        if len(bucket) >= MIN_MONTHS and any("RedotPay" in m for m in bucket.values()):
            chunks.append(dict(bucket))
    if not chunks:
        raise RuntimeError("未解析到含 RedotPay 的月表")
    return chunks


def pick_spends(chunks: list[dict[str, dict[str, float]]]) -> dict[str, dict[str, float]]:
    def redot_cum(bucket: dict[str, dict[str, float]]) -> float:
        return sum(month.get("RedotPay", 0.0) for month in bucket.values())

    return min(chunks, key=lambda b: abs(redot_cum(b) - REDOT_CUM_TARGET))


def build(bucket: dict[str, dict[str, float]]) -> dict[str, Any]:
    months_out: list[dict[str, Any]] = []
    total_series: list[dict[str, Any]] = []
    redot_series: list[dict[str, Any]] = []
    cum_rp = 0.0
    cum_tot = 0.0
    for month in sorted(bucket):
        programs = bucket[month]
        total = sum(programs.values())
        rp = float(programs.get("RedotPay", 0.0))
        cum_rp += rp
        cum_tot += total
        top = sorted(programs.items(), key=lambda item: -item[1])[:8]
        row = {
            "month": month,
            "total_usd": round(total, 2),
            "redotpay_usd": round(rp, 2),
            "redotpay_share_pct": round(100.0 * rp / total, 2) if total > 0 else None,
            "redotpay_cum_usd": round(cum_rp, 2),
            "redotpay_cum_share_pct": round(100.0 * cum_rp / cum_tot, 2) if cum_tot > 0 else None,
            "program_count": len(programs),
            "top": [{"name": name, "volume_usd": round(vol, 2)} for name, vol in top],
        }
        months_out.append(row)
        total_series.append({"month": month, "usd": round(total, 2)})
        redot_series.append({"month": month, "usd": round(rp, 2)})

    return {
        "code": "0",
        "msg": "success",
        "name": "crypto_card_programs",
        "description": "Paymentscan 公开首页按卡商月成交（spends 近似）。非 Dune/obchakevich。",
        "source": "paymentscan-public-home",
        "source_url": PAGE_URL,
        "updated_at": now_iso(),
        "month_count": len(months_out),
        "months": months_out,
        "series": [
            {"label": "合计", "labelEn": "Total", "months": total_series},
            {"label": "RedotPay", "labelEn": "RedotPay", "months": redot_series},
        ],
    }


def main() -> int:
    print("GET", PAGE_URL)
    html = fetch_page()
    bucket = pick_spends(parse_chunks(html))
    payload = build(bucket)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    latest = payload["months"][-1]
    print(
        f"wrote {OUT_PATH} months={payload['month_count']} "
        f"latest={latest['month']} total={latest['total_usd']/1e8:.2f}亿 "
        f"redot={latest['redotpay_usd']/1e8:.2f}亿"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
