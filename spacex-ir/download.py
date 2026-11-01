#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SpaceX 官方 IR 季度财报（免费 PDF）→ JSON。

从 https://ir.spacex.com/ 发现最新 Earnings/Results PDF（优先 8-K Exhibit），
仅在 URL/ETag/大小变化时整包下载；用 pdftotext（或 pdfplumber）抽文本后解析。
不占付费额度；日常只扫首页，季度才拉 PDF。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "spacex_ir.json"
META_PATH = HERE / "output" / "download_meta.json"
PDF_DIR = HERE / "output" / "pdf"
IR_HOME = "https://ir.spacex.com/"
UA = {"User-Agent": "Mozilla/5.0 (compatible; dashboard-data/spacex-ir)"}

QUARTER_WORD = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "q1": 1,
    "q2": 2,
    "q3": 3,
    "q4": 4,
}


def http_get(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def http_head(url: str, timeout: int = 20) -> dict[str, str]:
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {k.lower(): v for k, v in resp.headers.items()}


def load_meta() -> dict[str, Any]:
    if not META_PATH.is_file():
        return {}
    try:
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_meta(meta: dict[str, Any]) -> None:
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    meta["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = META_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(META_PATH)


def discover_pdf_url() -> str:
    html = http_get(IR_HOME, timeout=40).decode("utf-8", "replace")
    rels = sorted(set(re.findall(r"/files/[^\s\"'<>]+\.pdf", html, re.I)))
    scored: list[tuple[tuple[int, int, int], str]] = []
    for rel in rels:
        low = rel.lower()
        m = re.search(r"/(\d{4})/q([1-4])/", low)
        if not m:
            continue
        year, q = int(m.group(1)), int(m.group(2))
        # Earnings / Results 优先于普通 8-K 封面
        bonus = 2 if ("earning" in low or "result" in low) else 0
        if "8-k" in low or "8k" in low:
            bonus += 1
        scored.append(((year, q, bonus), IR_HOME.rstrip("/") + rel))
    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]
    # 兜底：已知 Results 文件名模式（近年）
    now_y = datetime.now(timezone.utc).year
    words = ("Fourth", "Third", "Second", "First")
    for y in range(now_y, now_y - 2, -1):
        for w in words:
            url = (
                f"{IR_HOME.rstrip('/')}/files/content_files/"
                f"SpaceX-Reports-{w}-Quarter-{y}-Results.pdf"
            )
            try:
                http_head(url)
                return url
            except Exception:
                continue
    raise RuntimeError("未在 IR 首页发现财报 PDF")


def extract_text(pdf_path: Path) -> str:
    for bin_name in ("pdftotext", "/usr/bin/pdftotext"):
        try:
            with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
                txt_path = Path(tmp.name)
            try:
                subprocess.run(
                    [bin_name, "-layout", str(pdf_path), str(txt_path)],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                return txt_path.read_text(encoding="utf-8", errors="replace")
            finally:
                txt_path.unlink(missing_ok=True)
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            continue
    try:
        import pdfplumber  # type: ignore
    except ImportError as exc:
        raise RuntimeError("需要 pdftotext 或 pdfplumber") from exc
    parts: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _money_m(s: str) -> float | None:
    s = s.strip().replace(",", "").replace("$", "")
    if not s or s in {"—", "-", "–"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def _pct_from_amounts(cur: float, base: float) -> float | None:
    if base == 0:
        return None
    return round((cur / base - 1.0) * 100.0, 2)


def parse_quarter_label(text: str) -> tuple[int, int, str]:
    m = re.search(
        r"Reports\s+(First|Second|Third|Fourth)\s+Quarter\s+(\d{4})\s+Results",
        text,
        re.I,
    )
    if m:
        q = QUARTER_WORD[m.group(1).lower()]
        y = int(m.group(2))
        return y, q, f"{y}Q{q}"
    m = re.search(r"quarter ended\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", text, re.I)
    if m:
        month = m.group(1).lower()[:3]
        y = int(m.group(3))
        q = {"jan": 1, "mar": 1, "apr": 2, "jun": 2, "jul": 3, "sep": 3, "oct": 4, "dec": 4}.get(
            month, 0
        )
        if q:
            return y, q, f"{y}Q{q}"
    raise RuntimeError("无法识别财报季度")


def parse_highlights(text: str) -> dict[str, Any]:
    # 「$, in millions」分段：取 Three Months 列（当前季 / 上季 / 去年同期）
    block_m = re.search(
        r"Third Quarter Financial Highlights.*?Capex\s*\n(.*?)(?:\n\s*Note:|\n\s*Space Segment)",
        text,
        re.I | re.S,
    )
    # 也兼容 First/Second/Fourth
    if not block_m:
        block_m = re.search(
            r"Quarter Financial Highlights.*?Capex\s*\n(.*?)(?:\n\s*Note:|\n\s*Space Segment)",
            text,
            re.I | re.S,
        )
    if not block_m:
        raise RuntimeError("未找到 Financial Highlights 表")

    chunk = block_m.group(0)

    rev_m = re.search(
        r"Revenue\s*\n\s*Space\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?"
        r".*?Connectivity\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?"
        r".*?SI\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?"
        r".*?Total\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?",
        chunk,
        re.I | re.S,
    )
    if not rev_m:
        raise RuntimeError("Revenue 表解析失败")
    g = [float(x.replace(",", "")) for x in rev_m.groups()]
    rev = {
        "space": (g[0], g[1], g[2]),
        "connectivity": (g[3], g[4], g[5]),
        "si": (g[6], g[7], g[8]),
        "total": (g[9], g[10], g[11]),
    }

    ebitda_m = re.search(
        r"Segment Adjusted EBITDA\s*\n\s*Space\s+\$?\(?([\d,\-]+)\)?\s+\$?\(?([\d,\-]+)\)?\s+\$?\(?([\d,\-]+)\)?"
        r".*?Connectivity\s+\$?\(?([\d,\-]+)\)?\s+\$?\(?([\d,\-]+)\)?\s+\$?\(?([\d,\-]+)\)?"
        r".*?SI\s+\$?\(?([\d,\-]+)\)?\s+\$?\(?([\d,\-]+)\)?\s+\$?\(?([\d,\-]+)\)?"
        r".*?Total\s+\$?\(?([\d,\-]+)\)?\s+\$?\(?([\d,\-]+)\)?\s+\$?\(?([\d,\-]+)\)?",
        chunk,
        re.I | re.S,
    )
    if not ebitda_m:
        raise RuntimeError("Adjusted EBITDA 表解析失败")
    eg = []
    for x in ebitda_m.groups():
        v = _money_m(x)
        if v is None:
            raise RuntimeError(f"EBITDA 金额失败: {x}")
        eg.append(v)
    ebitda = {
        "space": (eg[0], eg[1], eg[2]),
        "connectivity": (eg[3], eg[4], eg[5]),
        "si": (eg[6], eg[7], eg[8]),
        "total": (eg[9], eg[10], eg[11]),
    }

    capex_m = re.search(
        r"Capex\s*\n\s*Space\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?"
        r".*?Connectivity\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?"
        r".*?SI\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?"
        r".*?Total\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?\s+\$?\(?([\d,]+)\)?",
        chunk,
        re.I | re.S,
    )
    if not capex_m:
        raise RuntimeError("Capex 表解析失败")
    cg = [float(x.replace(",", "")) for x in capex_m.groups()]
    capex = {
        "space": (cg[0], cg[1], cg[2]),
        "connectivity": (cg[3], cg[4], cg[5]),
        "si": (cg[6], cg[7], cg[8]),
        "total": (cg[9], cg[10], cg[11]),
    }

    # Net income：优先 GAAP 表精确值（百万美元）
    ni = None
    m = re.search(
        r"Net\s+income\s*\(loss\)\s+\$\s*([\d,]+)\s+\$\s*\(?([\d,\-]+)\)?",
        text,
        re.I,
    )
    if m:
        ni = _money_m(m.group(1))
    if ni is None:
        m = re.search(
            r"Net income of \$([0-9.]+)\s+billion|Achieved.*?net income of \$([0-9.]+)\s+billion",
            text,
            re.I,
        )
        if m:
            raw = m.group(1) or m.group(2)
            ni = round(float(raw) * 1000, 1)

    # launches
    launch_m = re.search(
        r"Total launches\s*\(#\)\s+(\d+)\s+(\d+)\s+(\d+)",
        text,
        re.I,
    )
    launches = None
    if launch_m:
        launches = {
            "current": int(launch_m.group(1)),
            "prior_quarter": int(launch_m.group(2)),
            "prior_year": int(launch_m.group(3)),
        }

    # Digital assets (millions) vs year-end
    dig_m = re.search(
        r"Digital\s*assets[\s.]*?([\d,]+)\s+([\d,]+)",
        text,
        re.I,
    )
    digital = None
    if dig_m:
        cur, ye = float(dig_m.group(1).replace(",", "")), float(dig_m.group(2).replace(",", ""))
        digital = {
            "book_m": cur,
            "year_end_m": ye,
            "change_m": round(cur - ye, 1),
        }
    unreal_m = re.search(
        r"Unrealized\s*\(gain\)\s*loss on digital assets[\s.]*?([\d,]+)",
        text,
        re.I,
    )
    if digital is not None and unreal_m:
        digital["unrealized_loss_m"] = float(unreal_m.group(1).replace(",", ""))

    def pack(triple: tuple[float, float, float]) -> dict[str, float | None]:
        cur, pq, py = triple
        return {
            "current_m": cur,
            "prior_quarter_m": pq,
            "prior_year_m": py,
            "yoy_pct": _pct_from_amounts(cur, py),
            "qoq_pct": _pct_from_amounts(cur, pq),
        }

    return {
        "revenue": {k: pack(v) for k, v in rev.items()},
        "adj_ebitda": {k: pack(v) for k, v in ebitda.items()},
        "capex": {k: pack(v) for k, v in capex.items()},
        "net_income_m": ni,
        "launches": launches,
        "digital_assets": digital,
    }


def ensure_pdf(url: str) -> Path:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w.\-]+", "_", url.rstrip("/").split("/")[-1]) or "latest.pdf"
    path = PDF_DIR / name
    meta = load_meta()
    try:
        head = http_head(url)
    except Exception:
        head = {}
    etag = head.get("etag") or ""
    length = head.get("content-length") or ""
    same = (
        meta.get("url") == url
        and meta.get("etag") == etag
        and meta.get("contentLength") == length
        and path.is_file()
        and path.stat().st_size > 100_000
    )
    if same:
        print(f"[spacex-ir] 缓存命中 {path.name} ({path.stat().st_size} bytes)")
        return path
    print(f"[spacex-ir] 下载 {url}")
    data = http_get(url, timeout=180)
    if len(data) < 50_000 or not data.startswith(b"%PDF"):
        raise RuntimeError(f"PDF 异常 size={len(data)}")
    tmp = path.with_suffix(".pdf.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    save_meta({"url": url, "etag": etag, "contentLength": length or str(len(data)), "file": str(path)})
    print(f"[spacex-ir] 已保存 {path.name} ({len(data)} bytes)")
    return path


def spacex_series(year: int, quarter: int, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    rev = (metrics.get("revenue") or {}).get("total") or {}
    current = rev.get("current_m")
    prior_q = rev.get("prior_quarter_m")
    prior_y = rev.get("prior_year_m")
    if not all(isinstance(v, (int, float)) for v in (current, prior_q, prior_y)):
        return []
    prev_q_year, prev_q = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
    points = [
        {"month": f"{year - 1}-Q{quarter}", "usd": float(prior_y) * 1_000_000},
        {"month": f"{prev_q_year}-Q{prev_q}", "usd": float(prior_q) * 1_000_000},
        {"month": f"{year}-Q{quarter}", "usd": float(current) * 1_000_000},
    ]
    return [{"label": "营收合计", "labelEn": "Revenue", "months": points}]


def spacex_bars(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    rev = metrics.get("revenue") or {}
    labels = {"space": "航天", "connectivity": "连接", "si": "SI"}
    bars = []
    for key, name in labels.items():
        current = (rev.get(key) or {}).get("current_m")
        if isinstance(current, (int, float)):
            bars.append({"name": name, "amount": float(current) * 1_000_000})
    return bars


def main() -> int:
    url = discover_pdf_url()
    print(f"[spacex-ir] latest={url}")
    pdf = ensure_pdf(url)
    text = extract_text(pdf)
    year, q, qid = parse_quarter_label(text)
    metrics = parse_highlights(text)
    doc = {
        "code": 0,
        "msg": "success",
        "data": {
            "quarter_id": qid,
            "year": year,
            "quarter": q,
            "source_url": url,
            "source": "SpaceX Investor Relations (free PDF)",
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "metrics": metrics,
            "unit": "USD millions unless noted",
        },
        "series": spacex_series(year, q, metrics),
        "top": spacex_bars(metrics),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    # 附带文本便于核验
    (HERE / "output" / "latest.txt").write_text(text, encoding="utf-8", errors="replace")
    m = metrics
    print(
        f"[spacex-ir] {qid} rev={m['revenue']['total']['current_m']} "
        f"ebitda={m['adj_ebitda']['total']['current_m']} "
        f"ni={m.get('net_income_m')} launches={(m.get('launches') or {}).get('current')}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[spacex-ir] ERROR {exc}", flush=True)
        raise SystemExit(1)
