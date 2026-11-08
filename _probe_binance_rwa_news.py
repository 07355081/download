# -*- coding: utf-8 -*-
"""One-shot probe for Binance Research / RWA.xyz tokenized-stocks news coverage."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

ENV_PATHS = [
    Path("/root/dashboard/.env.live"),
    Path("/root/data-download/.env"),
    Path(__file__).resolve().parent.parent / "data-dashboard" / ".env.live",
    Path(__file__).resolve().parent.parent / "data-dashboard" / ".env.local",
]


def load_keys() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in ENV_PATHS:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or "=" not in line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def get(url: str, headers: dict[str, str] | None = None, timeout: int = 45) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() if exc.fp else b""
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc).encode("utf-8", errors="replace")


def main() -> None:
    env = load_keys()
    print("keys", {k: bool(env.get(k)) for k in ("COINGECKO_KEY", "RWA_API_KEY", "RWA_XYZ_API_KEY", "TOKEN_TERMINAL_API_KEY", "DUNE_API_KEY")})

    cg_key = env.get("COINGECKO_KEY") or ""
    if cg_key:
        base = "https://pro-api.coingecko.com/api/v3"
        headers = {"x-cg-pro-api-key": cg_key}
    else:
        base = "https://api.coingecko.com/api/v3"
        headers = {}
    code, body = get(
        f"{base}/coins/markets?vs_currency=usd&category=tokenized-stock&order=market_cap_desc&per_page=250&page=1",
        headers=headers,
    )
    print("cg_markets", code)
    if code == 200:
        data = json.loads(body.decode("utf-8"))
        total = sum(float(x.get("market_cap") or 0) for x in data)
        print("cg_n", len(data), "sum_yi", round(total / 1e8, 2))
        print("cg_top", [(x.get("symbol"), round(float(x.get("market_cap") or 0) / 1e8, 2)) for x in data[:5]])

    for u in (
        "https://api.llama.fi/rwa/stats",
        "https://api.llama.fi/rwa/current",
        "https://api.llama.fi/rwa/chart/chain-breakdown",
        "https://api.rwa.xyz/v4/assets",
        "https://api.rwa.xyz/v4/networks",
    ):
        code, body = get(u, timeout=25)
        print(u, code, body[:100].decode("utf-8", errors="replace").replace("\n", " "))

    for p in (
        Path("/root/data-download/robinhood-chain/output/json/rwa_aum.json"),
        Path("/root/dashboard/public/json/robinhood-chain/rwa_aum.json"),
    ):
        if not p.is_file():
            continue
        doc = json.loads(p.read_text(encoding="utf-8"))
        rows = doc.get("data") if isinstance(doc, dict) else doc
        print("rh_file", p, "rows", len(rows) if isinstance(rows, list) else type(rows))
        if isinstance(rows, list) and rows and isinstance(rows[-1], dict):
            print("rh_last_keys", list(rows[-1].keys())[:25])
            print("rh_last", json.dumps(rows[-1], ensure_ascii=False)[:400])
        break


if __name__ == "__main__":
    main()
