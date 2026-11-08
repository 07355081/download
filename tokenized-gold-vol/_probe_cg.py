#!/usr/bin/env python3
import json
import urllib.parse
import urllib.request
from pathlib import Path


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in (Path("/root/dashboard/.env.live"), Path("/root/data-download/.env")):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def try_url(url: str, headers: dict[str, str]) -> None:
    req = urllib.request.Request(
        url,
        headers={**headers, "User-Agent": "dash", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
            vols = doc.get("total_volumes") or []
            print("OK", url.split("?")[0][-40:], "n", len(vols), "hdr", sorted(headers))
    except Exception as exc:  # noqa: BLE001
        print("FAIL", getattr(exc, "code", type(exc).__name__), sorted(headers), exc)


def main() -> None:
    env = load_env()
    print("keys", [k for k in env if "COINGECKO" in k.upper()])
    key = (
        env.get("COINGECKO_DEMO_API_KEY")
        or env.get("COINGECKO_KEY")
        or env.get("COINGECKO_API_KEY")
        or ""
    ).strip()
    print("key_len", len(key))
    for days in ("365", "max"):
        qs = urllib.parse.urlencode({"vs_currency": "usd", "days": days})
        path = f"/coins/pax-gold/market_chart?{qs}"
        if key:
            try_url(f"https://api.coingecko.com/api/v3{path}", {"x-cg-demo-api-key": key})
            try_url(f"https://pro-api.coingecko.com/api/v3{path}", {"x-cg-pro-api-key": key})
        try_url(f"https://api.coingecko.com/api/v3{path}", {})


if __name__ == "__main__":
    main()
