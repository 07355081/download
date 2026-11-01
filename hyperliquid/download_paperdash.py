"""Fetch Papertrade (PaperDash) stats — no Dune credits.

Source: https://paperdash.xyz/api/v1/stats (public)
UI:     https://paperdash.xyz/stats

Papertrade is a synthetic perp protocol on HyperEVM. Volume figures are
leverage-amplified notional, not cash deployed.

Outputs:
  output/json/papertrade_stats.json

Local prior snapshots (margin / open positions are point-in-time only on the
API) are kept inside the same file under `priors` so 24h deltas stay available
across runs.

Usage:
  python download_paperdash.py
  python download_paperdash.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
OUT_NAME = "papertrade_stats.json"
API_URL = "https://paperdash.xyz/api/v1/stats"
SOURCE_URL = "https://paperdash.xyz/stats"
USER_AGENT = "dashboard-data/paperdash"
REQUEST_TIMEOUT = 60
PRIOR_RETENTION_HOURS = 72
PRIOR_BUCKET_HOURS = 1


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def money(node: Any) -> float | None:
    if node is None:
        return None
    if isinstance(node, (int, float)):
        return float(node)
    if isinstance(node, dict):
        value = node.get("value")
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    try:
        return float(node)
    except (TypeError, ValueError):
        return None


def section(payload: dict, key: str) -> dict:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("api payload missing data")
    block = data.get(key)
    if not isinstance(block, dict):
        raise ValueError(f"api payload missing section {key!r}")
    if block.get("status") not in (None, "available"):
        raise ValueError(f"section {key!r} status={block.get('status')!r}")
    inner = block.get("data")
    if not isinstance(inner, dict):
        raise ValueError(f"section {key!r} has no data object")
    return inner


def fetch_api() -> dict:
    req = urllib.request.Request(
        API_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_existing(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    # Support both envelope {code,data} and legacy flat body.
    data = raw.get("data")
    if isinstance(data, dict) and ("snapshot" in data or "priors" in data):
        return data
    return raw


def parse_iso_ms(iso: str) -> float | None:
    text = iso.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp() * 1000
    except ValueError:
        return None


def hour_bucket(iso: str) -> str:
    ms = parse_iso_ms(iso)
    if ms is None:
        return iso[:13]
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    floored = dt.replace(minute=0, second=0, microsecond=0)
    # Keep PRIOR_BUCKET_HOURS granularity (default 1h).
    hour = (floored.hour // PRIOR_BUCKET_HOURS) * PRIOR_BUCKET_HOURS
    floored = floored.replace(hour=hour)
    return floored.strftime("%Y-%m-%dT%H:%M:%SZ")


def merge_priors(existing: dict | None, snapshot: dict, fetched_at: str) -> list[dict]:
    priors: list[dict] = []
    if existing:
        prev = existing.get("priors")
        if isinstance(prev, list):
            for row in prev:
                if isinstance(row, dict) and row.get("fetched_at"):
                    priors.append(row)

    bucket = hour_bucket(fetched_at)
    entry = {
        "fetched_at": fetched_at,
        "bucket": bucket,
        "tvl_usd": snapshot.get("tvl_usd"),
        "lp_usd": snapshot.get("lp_usd"),
        "volume_usd": snapshot.get("volume_usd"),
        "volume_24h_usd": snapshot.get("volume_24h_usd"),
        "open_positions": snapshot.get("open_positions"),
        "margin_usd": snapshot.get("margin_usd"),
        "accounts": snapshot.get("accounts"),
    }
    # Replace same hour bucket so frequent cron runs don't bloat the file.
    priors = [row for row in priors if row.get("bucket") != bucket]
    priors.append(entry)

    cutoff = parse_iso_ms(fetched_at)
    if cutoff is not None:
        min_ms = cutoff - PRIOR_RETENTION_HOURS * 3600_000
        kept: list[dict] = []
        for row in priors:
            ms = parse_iso_ms(str(row.get("fetched_at") or ""))
            if ms is None or ms >= min_ms:
                kept.append(row)
        priors = kept

    priors.sort(key=lambda row: str(row.get("fetched_at") or ""))
    return priors


def find_prior_near(priors: list[dict], target_iso: str, *, hours: float = 24) -> dict | None:
    target = parse_iso_ms(target_iso)
    if target is None or not priors:
        return None
    want = target - hours * 3600_000
    best: dict | None = None
    best_dist: float | None = None
    for row in priors:
        ms = parse_iso_ms(str(row.get("fetched_at") or ""))
        if ms is None:
            continue
        # Prefer points at least ~20h old so we don't compare against a recent run.
        if ms > target - 20 * 3600_000:
            continue
        dist = abs(ms - want)
        if best_dist is None or dist < best_dist:
            best = row
            best_dist = dist
    if best is None or best_dist is None:
        return None
    # Reject if farther than 6h from the 24h mark.
    if best_dist > 6 * 3600_000:
        return None
    return best


def build_history(points: Any) -> list[dict]:
    rows: list[dict] = []
    if not isinstance(points, list):
        return rows
    for point in points:
        if not isinstance(point, dict):
            continue
        ts = point.get("timestamp")
        if not ts:
            continue
        rows.append(
            {
                "ts": str(ts),
                "tvl_usd": money(point.get("balance")),
                "accounts": int(point["accounts"]) if point.get("accounts") is not None else None,
                "volume_usd": money(point.get("volume")),
                "volume_btc_usd": money(point.get("volumeBtc")),
                "volume_eth_usd": money(point.get("volumeEth")),
                "trader_pnl_usd": money(point.get("traderPnl")),
                "liquidation_usd": money(point.get("liquidation")),
                "trades": int(point["trades"]) if point.get("trades") is not None else None,
                "paper_supply": money(point.get("paperSupply")),
                "paper_staked": money(point.get("paperStaked")),
                "staking_rewards_usd": money(point.get("stakingRewards")),
            }
        )
    rows.sort(key=lambda r: r["ts"])
    return rows


def build_daily(points: Any) -> list[dict]:
    rows: list[dict] = []
    if not isinstance(points, list):
        return rows
    for point in points:
        if not isinstance(point, dict):
            continue
        ts = str(point.get("timestamp") or "")
        day = ts[:10]
        if len(day) != 10:
            continue
        rows.append(
            {
                "day": day,
                "tvl_usd": money(point.get("balance")),
                "accounts": int(point["accounts"]) if point.get("accounts") is not None else None,
                "volume_usd": money(point.get("volume")),
                "volume_btc_usd": money(point.get("volumeBtc")),
                "volume_eth_usd": money(point.get("volumeEth")),
                "trader_pnl_usd": money(point.get("traderPnl")),
                "liquidation_usd": money(point.get("liquidation")),
                "trades": int(point["trades"]) if point.get("trades") is not None else None,
                "paper_supply": money(point.get("paperSupply")),
                "paper_staked": money(point.get("paperStaked")),
                "staking_rewards_usd": money(point.get("stakingRewards")),
            }
        )
    rows.sort(key=lambda r: r["day"])
    return rows


def build_markets(markets_inner: dict) -> tuple[list[dict], float | None]:
    out: list[dict] = []
    raw_markets = markets_inner.get("markets")
    if isinstance(raw_markets, list):
        for row in raw_markets:
            if not isinstance(row, dict):
                continue
            market = row.get("market")
            if not market:
                continue
            out.append({"market": str(market), "volume_usd": money(row.get("volume"))})
    total = money(markets_inner.get("total"))
    return out, total


def build_payload(api: dict, existing: dict | None) -> dict:
    capital = section(api, "capital")
    accounts = section(api, "accounts")
    history = section(api, "history")
    daily = section(api, "daily")
    markets_inner = section(api, "markets")
    try:
        dev_fees = section(api, "devFees")
    except ValueError:
        dev_fees = {}

    markets, volume_24h = build_markets(markets_inner)
    fetched_at = now_utc_iso()
    as_of = None
    for key in ("capital", "history", "daily"):
        block = api.get("data", {}).get(key) if isinstance(api.get("data"), dict) else None
        if isinstance(block, dict) and block.get("sourceAsOf"):
            as_of = str(block["sourceAsOf"])
            break
    if not as_of:
        as_of = fetched_at

    snapshot = {
        "tvl_usd": money(capital.get("balance")),
        "lp_usd": money(capital.get("lp")),
        "volume_usd": money(capital.get("volume")),
        "volume_24h_usd": volume_24h,
        "open_positions": int(capital["openPositions"]) if capital.get("openPositions") is not None else None,
        "margin_usd": money(capital.get("margin")),
        "reserve_usd": money(capital.get("reserve")),
        "accounts": int(accounts["count"]) if accounts.get("count") is not None else None,
        "top10_share_bps": int(accounts["top10ShareBps"]) if accounts.get("top10ShareBps") is not None else None,
        "paper_supply": money(capital.get("paperSupply")),
        "paper_staked": money(capital.get("paperStaked")),
        "staker_rewards_usd": money(capital.get("stakerRewards")),
        "reward_reserve_usd": money(capital.get("rewardReserve")),
        "dev_fees_usd": money(dev_fees.get("total")),
        "dev_fees_pending_usd": money(dev_fees.get("pending")),
        "source_block": str(capital["sourceBlock"]) if capital.get("sourceBlock") is not None else None,
    }

    priors = merge_priors(existing, snapshot, fetched_at)
    prior_24h = find_prior_near(priors, fetched_at, hours=24)

    history_rows = build_history(history.get("points"))
    # Derive ~24h TVL from API history when local priors are still thin.
    tvl_24h_ago = None
    if history_rows:
        target = parse_iso_ms(as_of) or parse_iso_ms(fetched_at)
        if target is not None:
            want = target - 24 * 3600_000
            best = None
            best_dist = None
            for row in history_rows:
                ms = parse_iso_ms(str(row["ts"]))
                if ms is None:
                    continue
                dist = abs(ms - want)
                if best_dist is None or dist < best_dist:
                    best = row
                    best_dist = dist
            if best is not None and best_dist is not None and best_dist <= 6 * 3600_000:
                tvl_24h_ago = best.get("tvl_usd")

    changes_24h = {
        "tvl_usd": tvl_24h_ago,
        "margin_usd": prior_24h.get("margin_usd") if prior_24h else None,
        "open_positions": prior_24h.get("open_positions") if prior_24h else None,
        "accounts": prior_24h.get("accounts") if prior_24h else None,
        "prior_fetched_at": prior_24h.get("fetched_at") if prior_24h else None,
    }

    body = {
        "meta": {
            "name": "papertrade_stats",
            "description": "Papertrade (PaperDash) TVL / notional volume / positions on HyperEVM",
            "source": API_URL,
            "source_url": SOURCE_URL,
            "note": "Trading volume is leverage-amplified notional, not cash deployed. Max leverage up to 1000x.",
            "fetched_at": fetched_at,
        },
        "as_of": as_of,
        "snapshot": snapshot,
        "changes_24h": changes_24h,
        "markets_24h": markets,
        "history": history_rows,
        "daily": build_daily(daily.get("points")),
        "priors": priors,
    }
    # Match hyperliquid Dune payloads so /api/hyperliquid/* + useHlData work unchanged.
    return {"code": "0", "msg": "success", "data": body}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(JSON_DIR / OUT_NAME))
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print summary without writing")
    args = parser.parse_args()
    dest = Path(args.out)

    print(f"[paperdash] GET {API_URL}", flush=True)
    try:
        api = fetch_api()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit(f"paperdash fetch failed: {exc}") from exc

    existing = load_existing(dest)
    payload = build_payload(api, existing)
    body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    snap = body.get("snapshot") if isinstance(body, dict) else {}
    print(
        f"[paperdash] tvl={snap.get('tvl_usd')} vol_cum={snap.get('volume_usd')} "
        f"vol_24h={snap.get('volume_24h_usd')} positions={snap.get('open_positions')} "
        f"margin={snap.get('margin_usd')} accounts={snap.get('accounts')}",
        flush=True,
    )

    if args.dry_run:
        print("[paperdash] dry-run, not writing")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    hist_n = len(body.get("history") or []) if isinstance(body, dict) else 0
    daily_n = len(body.get("daily") or []) if isinstance(body, dict) else 0
    print(f"[paperdash] wrote {dest} history={hist_n} daily={daily_n}")


if __name__ == "__main__":
    main()
