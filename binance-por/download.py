"""拉取 Binance 储备金证明（Proof of Reserves）最新快照并落盘。

接口（与官网 PoR 页同源，需 POST JSON）：
  POST https://www.binance.com/bapi/apex/v1/public/apex/market/query/userReserveAuditProofSnapshot
  body: {}

输出：
  output/json/binance_por_latest.json
  output/json/binance-por-history/<YYYY-MM-DD>.json

用法：
  python download.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output" / "json"
LATEST_PATH = OUT_DIR / "binance_por_latest.json"
HISTORY_DIR = OUT_DIR / "binance-por-history"

API_URL = (
    "https://www.binance.com/bapi/apex/v1/public/apex/market/"
    "query/userReserveAuditProofSnapshot"
)
FOCUS = ("BTC", "ETH", "BNB", "USDT", "USDC")
HTTP_TIMEOUT = 60
USER_AGENT = "binance-por/1.0 (+data-download)"


def http_post_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "clienttype": "web",
            "lang": "en",
            "Origin": "https://www.binance.com",
            "Referer": "https://www.binance.com/en/proof-of-reserves",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_snapshot_date(snapshot_time: str) -> str | None:
    """'01/10/26 00:00:00 UTC' → '2026-10-01'."""
    text = (snapshot_time or "").strip()
    if not text:
        return None
    head = text.split(" UTC")[0].strip()
    for fmt in ("%d/%m/%y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(head, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize(payload: dict[str, Any], *, fetched_at: str) -> dict[str, Any]:
    if str(payload.get("code")) not in ("000000", "0") and payload.get("success") is False:
        raise RuntimeError(f"Binance PoR API error: {payload.get('code')} {payload.get('message')}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Binance PoR API missing data")
    rows = data.get("snapshotDataList")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Binance PoR API missing snapshotDataList")

    assets: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        coin = str(row.get("coin") or "").upper()
        if not coin:
            continue
        try:
            ratio = float(row.get("ratio"))
            user = float(row.get("customerLiability"))
            platform = float(row.get("binanceLiability"))
        except (TypeError, ValueError):
            continue
        item: dict[str, Any] = {
            "coin": coin,
            "ratio": ratio,
            "ratio_pct": round(ratio * 100.0, 6),
            "user": user,
            "platform": platform,
            "exchange_balance": row.get("exchangeBalance"),
            "third_party_custody": row.get("thirdPartyCustody"),
            "user_usdt": row.get("customerLiabilityUsdt"),
        }
        assets[coin] = item

    snapshot_time = str(data.get("snapshotTime") or "")
    snapshot_date = parse_snapshot_date(snapshot_time) or parse_snapshot_date(
        str(data.get("auditDate") or "")
    )
    return {
        "source": "binance-por",
        "api": API_URL,
        "fetched_at": fetched_at,
        "audit_id": str(data.get("auditId") or ""),
        "audit_date": str(data.get("auditDate") or ""),
        "snapshot_time": snapshot_time,
        "snapshot_date": snapshot_date,
        "merkle_root": str(data.get("merkleRootHash") or ""),
        "auditor": str(data.get("auditor") or ""),
        "auditor_link": str(data.get("auditorLink") or ""),
        "focus": [c for c in FOCUS if c in assets],
        "assets": assets,
        "asset_count": len(assets),
    }


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def seed_september_2026_if_missing() -> None:
    """首月 MoM 用公开报道口径种子（仅当本地尚无 2026-09-01 归档时写入）。"""
    path = HISTORY_DIR / "2026-09-01.json"
    if path.is_file():
        return
    # 数字对齐公开 PoR 报道（2026-09-01 快照）；仅作 MoM 基线，后续以官方接口归档为准。
    assets = {
        "BTC": {
            "coin": "BTC",
            "ratio": 1.0016,
            "ratio_pct": 100.16,
            "user": 682355.564,
            "platform": 683448.518,
            "user_usdt": None,
        },
        "ETH": {
            "coin": "ETH",
            "ratio": 1.0000,
            "ratio_pct": 100.00,
            "user": 3982738.635,
            "platform": 3982772.99,
            "user_usdt": None,
        },
        "BNB": {
            "coin": "BNB",
            "ratio": 1.0051,
            "ratio_pct": 100.51,
            "user": 43374148.018,
            "platform": 43593997.879,
            "user_usdt": None,
        },
        "USDT": {
            "coin": "USDT",
            "ratio": 1.0291,
            "ratio_pct": 102.91,
            "user": 32_310_000_000.0,
            "platform": 33_250_000_000.0,
            "user_usdt": 32_310_000_000.0,
        },
    }
    payload = {
        "source": "binance-por-seed",
        "note": "Seeded from public September 2026 PoR figures for first MoM; replace when official archive available.",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "audit_id": "PR01SEP26",
        "audit_date": "01/09/26",
        "snapshot_time": "01/09/26 00:00:00 UTC",
        "snapshot_date": "2026-09-01",
        "merkle_root": "",
        "auditor": "zk-SNARKs",
        "focus": ["BTC", "ETH", "BNB", "USDT"],
        "assets": assets,
        "asset_count": len(assets),
    }
    atomic_write(path, payload)
    print(f"seeded {path}")


def main() -> int:
    seed_september_2026_if_missing()
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        payload = http_post_json(API_URL, {})
        doc = normalize(payload, fetched_at=fetched_at)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    atomic_write(LATEST_PATH, doc)
    snap = doc.get("snapshot_date")
    if isinstance(snap, str) and len(snap) == 10:
        atomic_write(HISTORY_DIR / f"{snap}.json", doc)
        print(f"archived {HISTORY_DIR / (snap + '.json')}")
    print(
        f"wrote {LATEST_PATH} audit={doc.get('audit_id')} date={snap} "
        f"assets={doc.get('asset_count')} focus={doc.get('focus')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
