"""Probe CoinGecko holding_chart auth/days limits (does not modify main pipeline).

Official docs:
  https://docs.coingecko.com/reference/public-treasury-entity-chart

Plan limits (days):
  Demo / Keyless: 7,14,30,90,180,365  (max 1 year)
  Basic:          +730
  Analyst+:       +max

Usage:
  python test_holding_chart_api.py
  python test_holding_chart_api.py --entity strategy --coin bitcoin
"""
from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_DEMO_KEY = "CG-6QESrrBSVRdymKvuhxVXSyEp"
ENTITY = "strategy"
COIN = "bitcoin"


def resolve_key(cli: str | None) -> str | None:
    if cli:
        return cli.strip() or None
    env = (os.getenv("COINGECKO_KEY") or "").strip()
    return env or DEFAULT_DEMO_KEY


def probe(
    *,
    label: str,
    base_url: str,
    entity_id: str,
    coin_id: str,
    days: str,
    api_key: str | None,
    header_name: str | None,
) -> dict[str, Any]:
    path = f"/public_treasury/{entity_id}/{coin_id}/holding_chart"
    params = {"days": days, "include_empty_intervals": "true"}
    url = f"{base_url}{path}?{urlencode(params)}"
    headers = {"accept": "application/json"}
    if api_key and header_name:
        headers[header_name] = api_key

    started = time.time()
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body) if body.strip() else None
            holdings = data.get("holdings", []) if isinstance(data, dict) else []
            return {
                "label": label,
                "ok": True,
                "status": resp.status,
                "url": url,
                "header": header_name,
                "points": len(holdings) if isinstance(holdings, list) else 0,
                "first": holdings[0] if holdings else None,
                "last": holdings[-1] if holdings else None,
                "elapsed_s": round(time.time() - started, 2),
            }
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        return {
            "label": label,
            "ok": False,
            "status": exc.code,
            "url": url,
            "header": header_name,
            "error": f"{exc.code} {exc.reason}",
            "body": detail,
            "elapsed_s": round(time.time() - started, 2),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "label": label,
            "ok": False,
            "status": -1,
            "url": url,
            "header": header_name,
            "error": str(exc)[:300],
            "elapsed_s": round(time.time() - started, 2),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--entity", default=ENTITY)
    parser.add_argument("--coin", default=COIN)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    key = resolve_key(args.api_key)
    cases = [
        ("demo+days=max (current full mode)", "https://api.coingecko.com/api/v3", "max", key, "x-cg-demo-api-key"),
        ("demo+days=365 (dashboard default)", "https://api.coingecko.com/api/v3", "365", key, "x-cg-demo-api-key"),
        ("demo+days=30", "https://api.coingecko.com/api/v3", "30", key, "x-cg-demo-api-key"),
        ("keyless+days=365", "https://api.coingecko.com/api/v3", "365", None, None),
        ("keyless+days=max", "https://api.coingecko.com/api/v3", "max", None, None),
        ("pro+days=max", "https://pro-api.coingecko.com/api/v3", "max", key, "x-cg-pro-api-key"),
        ("pro+days=365", "https://pro-api.coingecko.com/api/v3", "365", key, "x-cg-pro-api-key"),
    ]

    print(f"entity={args.entity} coin={args.coin} key={'set' if key else 'none'}\n")
    results = []
    for label, base, days, k, header in cases:
        r = probe(
            label=label,
            base_url=base,
            entity_id=args.entity,
            coin_id=args.coin,
            days=days,
            api_key=k,
            header_name=header,
        )
        results.append(r)
        status = "OK" if r["ok"] else "FAIL"
        extra = f"points={r.get('points', 0)}" if r["ok"] else f"{r.get('error')} {r.get('body', '')[:120]}"
        print(f"[{status}] {label}: {extra}")
        time.sleep(1.2)

    ok_cases = [r["label"] for r in results if r["ok"]]
    fail_cases = [r["label"] for r in results if not r["ok"]]
    print("\n=== summary ===")
    print("working:", ok_cases or "(none)")
    print("failed:", fail_cases or "(none)")
    if any(r["ok"] for r in results if "365" in r["label"] and "demo" in r["label"]):
        print("\nLikely fix: use days=365 (or 30/90/180) instead of days=max on Demo API key.")
    if any(r["ok"] for r in results if r.get("header") == "x-cg-pro-api-key"):
        print("Pro API key path also works for this key/plan.")


if __name__ == "__main__":
    main()
