"""拉取 OKX 储备金证明（Proof of Reserves）最新快照并落盘。

优先顺序：
1. 环境变量 OKX_POR_API_URL（VPS Network 确认后的同源 JSON）
2. 内置候选公开接口（GET）
3. 解析官网详情页 HTML（中/英）

输出：
  output/json/okx_por_latest.json
  output/json/okx-por-history/<YYYY-MM-DD>.json

用法：
  python download.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output" / "json"
LATEST_PATH = OUT_DIR / "okx_por_latest.json"
HISTORY_DIR = OUT_DIR / "okx-por-history"

POR_PAGE_ZH = "https://www.okx.com/zh-hans/proof-of-reserves/detail"
POR_PAGE_EN = "https://www.okx.com/en-us/proof-of-reserves"
FOCUS = ("BTC", "ETH", "USDT", "USDC", "SOL", "XRP", "OKB", "DOGE")
HTTP_TIMEOUT = 60
USER_AGENT = "okx-por/1.0 (+data-download)"

# VPS 上从 DevTools 确认后可覆盖；也可用 OKX_POR_API_URL
API_CANDIDATES = [
    "https://www.okx.com/priapi/v5/asset/por/currency-list",
    "https://www.okx.com/priapi/v5/asset/por/list",
    "https://www.okx.com/v2/asset/balance/por-list",
    "https://www.okx.com/priapi/v5/asset/balance/por-list",
]


def http_get(url: str, *, accept: str = "application/json,text/html,*/*") -> tuple[str, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": POR_PAGE_ZH,
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        body = resp.read().decode("utf-8", "replace")
        return ctype, body


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _num(x: Any) -> float | None:
    if x is None or x == "":
        return None
    if isinstance(x, (int, float)):
        return float(x)
    text = str(x).replace(",", "").replace("%", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _ratio_from_pct(pct: float | None) -> float | None:
    if pct is None:
        return None
    # 页面写 109% → 1.09；若已是 1.09 则不动
    return pct / 100.0 if pct > 5 else pct


def normalize_assets(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    for row in rows:
        coin = str(row.get("coin") or row.get("ccy") or row.get("currency") or "").upper()
        if not coin:
            continue
        user = _num(
            row.get("user")
            or row.get("liability")
            or row.get("accountAssets")
            or row.get("accountAsset")
            or row.get("customerLiability")
        )
        platform = _num(
            row.get("platform")
            or row.get("wallet")
            or row.get("walletAssets")
            or row.get("walletAsset")
            or row.get("totalAssets")
        )
        ratio = _num(row.get("ratio") or row.get("reserveRatio") or row.get("reserve_ratio"))
        ratio_pct = _num(row.get("ratio_pct") or row.get("reserveRatioPct"))
        if ratio is None and ratio_pct is not None:
            ratio = _ratio_from_pct(ratio_pct)
        if ratio is not None and ratio > 5:
            ratio = ratio / 100.0
        if user is None:
            continue
        item: dict[str, Any] = {
            "coin": coin,
            "user": user,
            "platform": platform,
            "ratio": ratio if ratio is not None else None,
            "ratio_pct": round(ratio * 100.0, 6) if ratio is not None else None,
            "exchange_balance": _num(row.get("exchange_balance") or row.get("exchangeBalance")),
            "third_party_custody": _num(
                row.get("third_party_custody") or row.get("thirdPartyCustody") or row.get("custody")
            ),
        }
        assets[coin] = item
    return assets


def try_parse_json_payload(body: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    # 常见包装：{code, data: {...}|[...]}
    data = payload.get("data", payload)
    rows: list[Any] = []
    meta: dict[str, Any] = {}
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        meta = data
        for key in (
            "list",
            "currencyList",
            "currencies",
            "assets",
            "snapshotDataList",
            "porList",
            "details",
        ):
            maybe = data.get(key)
            if isinstance(maybe, list):
                rows = maybe
                break
        if not rows and any(k in data for k in ("BTC", "ETH", "USDT")):
            # map form
            for coin, row in data.items():
                if isinstance(row, dict):
                    row = {**row, "coin": coin}
                    rows.append(row)
    if not rows:
        return None

    assets = normalize_assets([r for r in rows if isinstance(r, dict)])
    if not assets:
        return None

    snap = (
        str(meta.get("snapshotDate") or meta.get("snapshot_date") or meta.get("date") or "")
        or str(payload.get("snapshotDate") or "")
    )
    # 标准化日期
    snap = _normalize_date(snap) or ""
    audit = str(
        meta.get("reportId")
        or meta.get("auditId")
        or meta.get("audit_id")
        or payload.get("reportId")
        or snap
        or ""
    )
    return {
        "audit_id": audit,
        "snapshot_date": snap,
        "snapshot_time": str(meta.get("snapshotTime") or meta.get("verifyDate") or ""),
        "assets": assets,
        "raw_keys": list(meta.keys())[:40],
    }


def _normalize_date(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    # 2026-09-08 / 2026/09/08 / Sep 8, 2026 / 09-08 / 2026年9月8日
    m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(20\d{2})",
        text,
        re.I,
    )
    if m:
        months = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        return f"{int(m.group(3)):04d}-{months[m.group(1)[:3].lower()]:02d}-{int(m.group(2)):02d}"
    return None


def parse_html_detail(html: str) -> dict[str, Any] | None:
    """从详情页 SSR HTML 抽出各币种账户资产与储备率。

    页面结构（SSR）：每个币种卡片含 collateralRatio%、欧易账户资产 / 欧易钱包资产等。
    注意：文案里有「超过账户资产…的 100%」，不能用泛匹配「账户资产」。
    """
    snap = None
    for pat in (
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日",
        r"验证日期[^\d]*(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2})",
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+20\d{2}",
        r"(20\d{2}-\d{2}-\d{2})",
    ):
        m = re.search(pat, html, re.I)
        if m:
            snap = _normalize_date(m.group(0))
            if snap:
                break

    audit = ""
    am = re.search(r'data-e2e-okd-select-value="(\d+)"', html)
    if am:
        audit = am.group(1)

    assets: dict[str, dict[str, Any]] = {}
    # 以储备率节点为锚，向前找币种 icon，向后找余额字段
    ratio_re = re.compile(
        r'index_collateralRatio__[^>]*>(\d+(?:\.\d+)?)\s*%',
        re.I,
    )
    for m in ratio_re.finditer(html):
        ratio_pct = _num(m.group(1))
        start = max(0, m.start() - 500)
        end = min(len(html), m.end() + 1200)
        chunk = html[start:end]
        cm = re.search(r"currency/icon/([a-z]+)", chunk, re.I)
        if not cm:
            continue
        coin = cm.group(1).upper()
        # icon 文件名形如 btc202304... / usdt202508...
        for known in FOCUS:
            if coin.startswith(known):
                coin = known
                break
        else:
            # 去掉尾部数字
            coin = re.sub(r"\d+$", "", coin).upper()
        if coin not in FOCUS and coin not in {"BTC", "ETH", "USDT", "USDC", "SOL", "XRP", "OKB", "DOGE"}:
            continue

        def _label_num(label: str) -> float | None:
            lm = re.search(
                re.escape(label)
                + r"</div><div class=\"index_balanceValue__[^\"]+\">([\d,]+(?:\.\d+)?)</div>",
                chunk,
            )
            return _num(lm.group(1)) if lm else None

        user = _label_num("欧易账户资产")
        platform = _label_num("欧易钱包资产")
        exchange = _label_num("交易所钱包")
        custody = _label_num("第三方托管钱包")
        if user is None or ratio_pct is None:
            continue
        # 过滤误抓：账户资产不可能正好等于「100%」那种占位，且应远大于 100（除极少数小币）
        if user == 100.0 and platform is not None and platform > 1000:
            continue
        ratio = _ratio_from_pct(ratio_pct)
        assets[coin] = {
            "coin": coin,
            "user": user,
            "platform": platform,
            "ratio": ratio,
            "ratio_pct": round(ratio * 100.0, 6) if ratio is not None else None,
            "exchange_balance": exchange,
            "third_party_custody": custody,
        }

    if len(assets) < 2:
        return None
    return {
        "audit_id": audit or (f"OKX-{snap}" if snap else "OKX-latest"),
        "snapshot_date": snap or "",
        "snapshot_time": f"{snap}T00:00:00+08:00" if snap else "",
        "assets": assets,
    }


def seed_history_if_missing() -> None:
    """写入公开报道/官网可见的两期基线，便于首月 MoM（仅当文件不存在）。"""
    seeds = [
        {
            "snapshot_date": "2026-08-11",
            "audit_id": "500375535",
            "note": "Seeded from WuSaid MoM back-calc (user BTC/ETH/USDT) vs 2026-09-08 page; ratios unknown.",
            "assets": {
                "BTC": {"coin": "BTC", "user": 134399.0, "platform": None, "ratio": None, "ratio_pct": None},
                "ETH": {"coin": "ETH", "user": 1725703.0, "platform": None, "ratio": None, "ratio_pct": None},
                "USDT": {
                    "coin": "USDT",
                    "user": 8_117_815_188.0,
                    "platform": None,
                    "ratio": None,
                    "ratio_pct": None,
                    "note": "USDT prior ≈ current − 3.75e8 (约)",
                },
            },
        },
        {
            "snapshot_date": "2026-09-08",
            "audit_id": "502299735",
            "note": "Seeded from OKX PoR detail page (WebFetch 2026-10-15) + download report id.",
            "assets": {
                "BTC": {
                    "coin": "BTC",
                    "user": 139865.0,
                    "platform": 153151.0,
                    "ratio": 1.09,
                    "ratio_pct": 109.0,
                    "exchange_balance": 146359.0,
                    "third_party_custody": 6792.0,
                },
                "ETH": {
                    "coin": "ETH",
                    "user": 1785866.0,
                    "platform": 1796024.0,
                    "ratio": 1.01,
                    "ratio_pct": 101.0,
                    "exchange_balance": 1716056.0,
                    "third_party_custody": 79968.0,
                },
                "USDT": {
                    "coin": "USDT",
                    "user": 8_492_815_188.0,
                    "platform": 8_953_354_829.0,
                    "ratio": 1.05,
                    "ratio_pct": 105.0,
                    "exchange_balance": 8_817_731_267.0,
                    "third_party_custody": 135_623_562.0,
                },
                "USDC": {
                    "coin": "USDC",
                    "user": 1_913_464_602.0,
                    "platform": 1_921_669_881.0,
                    "ratio": 1.00,
                    "ratio_pct": 100.0,
                },
                "SOL": {
                    "coin": "SOL",
                    "user": 7_411_794.0,
                    "platform": 7_762_249.0,
                    "ratio": 1.05,
                    "ratio_pct": 105.0,
                },
                "XRP": {
                    "coin": "XRP",
                    "user": 227_095_152.0,
                    "platform": 244_524_394.0,
                    "ratio": 1.08,
                    "ratio_pct": 108.0,
                },
                "OKB": {
                    "coin": "OKB",
                    "user": 19_715_073.0,
                    "platform": 19_753_384.0,
                    "ratio": 1.00,
                    "ratio_pct": 100.0,
                },
                "DOGE": {
                    "coin": "DOGE",
                    "user": 5_987_132_155.0,
                    "platform": 6_053_490_916.0,
                    "ratio": 1.01,
                    "ratio_pct": 101.0,
                },
            },
        },
    ]
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for seed in seeds:
        path = HISTORY_DIR / f"{seed['snapshot_date']}.json"
        if path.is_file():
            continue
        assets = seed["assets"]
        doc = {
            "source": "okx-por-seed",
            "note": seed["note"],
            "api": "seed",
            "fetched_at": fetched_at,
            "audit_id": seed["audit_id"],
            "snapshot_date": seed["snapshot_date"],
            "snapshot_time": f"{seed['snapshot_date']}T00:00:00+08:00",
            "focus": [c for c in FOCUS if c in assets],
            "assets": assets,
            "asset_count": len(assets),
        }
        atomic_write(path, doc)
        print(f"seeded {path}")


def build_doc(parsed: dict[str, Any], *, fetched_at: str, api: str) -> dict[str, Any]:
    assets = parsed["assets"]
    snap = parsed.get("snapshot_date") or ""
    if not snap:
        # 若页面没日期但资产与种子 Sep 一致，沿用
        sep = HISTORY_DIR / "2026-09-08.json"
        if sep.is_file():
            try:
                old = json.loads(sep.read_text(encoding="utf-8"))
                old_btc = ((old.get("assets") or {}).get("BTC") or {}).get("user")
                new_btc = (assets.get("BTC") or {}).get("user")
                if old_btc is not None and new_btc is not None and abs(float(old_btc) - float(new_btc)) < 1:
                    snap = "2026-09-08"
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
    audit = str(parsed.get("audit_id") or (f"OKX-{snap}" if snap else "OKX-latest"))
    return {
        "source": "okx-por",
        "api": api,
        "fetched_at": fetched_at,
        "audit_id": audit,
        "snapshot_date": snap,
        "snapshot_time": str(parsed.get("snapshot_time") or ""),
        "focus": [c for c in FOCUS if c in assets],
        "assets": assets,
        "asset_count": len(assets),
        "page": POR_PAGE_ZH,
    }


def fetch_live() -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    urls: list[str] = []
    env_url = (os.environ.get("OKX_POR_API_URL") or "").strip()
    if env_url:
        urls.append(env_url)
    urls.extend(API_CANDIDATES)

    errors: list[str] = []
    for url in urls:
        try:
            ctype, body = http_get(url)
            if "json" in ctype or body.lstrip().startswith(("{", "[")):
                parsed = try_parse_json_payload(body)
                if parsed and parsed.get("assets"):
                    return build_doc(parsed, fetched_at=fetched_at, api=url)
            errors.append(f"{url}: no usable json")
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            errors.append(f"{url}: {exc}")

    for page in (POR_PAGE_ZH, POR_PAGE_EN):
        try:
            _, body = http_get(page, accept="text/html,application/xhtml+xml")
            parsed = parse_html_detail(body)
            if parsed and parsed.get("assets"):
                return build_doc(parsed, fetched_at=fetched_at, api=page)
            errors.append(f"{page}: html parse miss")
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            errors.append(f"{page}: {exc}")

    raise RuntimeError("OKX PoR fetch failed: " + " | ".join(errors[:6]))


def ensure_latest_from_seed() -> dict[str, Any] | None:
    """本机连不上 OKX 时，用最新种子充当 latest，便于本地 dry-run。"""
    path = HISTORY_DIR / "2026-09-08.json"
    if not path.is_file():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        return None
    doc = {
        **doc,
        "source": "okx-por-seed-as-latest",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": (doc.get("note") or "")
        + " Offline fallback: okx.com unreachable from this host; replace on VPS live fetch.",
    }
    return doc


def main() -> int:
    seed_history_if_missing()
    try:
        doc = fetch_live()
    except RuntimeError as exc:
        print(f"[WARN] live fetch failed: {exc}", file=sys.stderr)
        doc = ensure_latest_from_seed()
        if not doc:
            print("[ERROR] no seed fallback", file=sys.stderr)
            return 1
        print("[WARN] using seeded 2026-09-08 as latest (offline)", file=sys.stderr)

    atomic_write(LATEST_PATH, doc)
    snap = doc.get("snapshot_date")
    if isinstance(snap, str) and len(snap) == 10:
        # 不覆盖已有非 seed 正式归档；seed 可被 live 覆盖
        hist = HISTORY_DIR / f"{snap}.json"
        if (not hist.is_file()) or str(doc.get("source") or "").startswith("okx-por"):
            if doc.get("source") != "okx-por-seed-as-latest":
                atomic_write(hist, doc)
                print(f"archived {hist}")
    print(
        f"wrote {LATEST_PATH} audit={doc.get('audit_id')} date={snap} "
        f"assets={doc.get('asset_count')} focus={doc.get('focus')} source={doc.get('source')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
