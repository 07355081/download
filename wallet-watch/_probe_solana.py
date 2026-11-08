#!/usr/bin/env python3
"""One-shot Solana RPC connectivity + Pump fee wallet balances."""
from __future__ import annotations

import json
import urllib.request

RPCS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
    "https://rpc.ankr.com/solana",
]
ADDRS = [
    "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM",
    "62qc2CNXwrYqQScmEdiZFFAnJR262PxWEuNQtxfafNgV",
    "FWsW1xNtWscwNmKv6wVsU1iTzRN6wmmk3MjxRP5tT7hz",
    "7VtfL8fvgNfhz17qKRMjzQEXgbdpnHHHQRh54R9jP2RJ",
    "G5UZAVbAf46s7cKWoyKu8kYTip9DGTpbLZ2qa9Aq69dP",
]


def rpc(url: str, method: str, params: list, timeout: int = 25) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def main() -> None:
    ok = None
    for url in RPCS:
        try:
            r = rpc(url, "getHealth", [])
            print("HEALTH", url, r.get("result"))
            ok = url
            break
        except Exception as exc:  # noqa: BLE001
            print("FAIL", url, type(exc).__name__, str(exc)[:120])
    if not ok:
        raise SystemExit("no rpc")

    for addr in ADDRS:
        try:
            bal = rpc(ok, "getBalance", [addr]).get("result", {}).get("value")
            print(addr, "SOL", None if bal is None else round(bal / 1e9, 4))
            sigs = rpc(ok, "getSignaturesForAddress", [addr, {"limit": 3}]).get("result") or []
            for s in sigs:
                print("  sig", str(s.get("signature") or "")[:20], "t", s.get("blockTime"), "err", s.get("err"))
        except Exception as exc:  # noqa: BLE001
            print(addr, "ERR", exc)


if __name__ == "__main__":
    main()
