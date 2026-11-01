"""Robinhood Chain 自己的链手续费，对照 Solana、Ethereum、Base。

用 DefiLlama summary/fees 的 dailyFees。这是链自己收到的手续费，
不是链上各项目手续费加总。

2026-09-01 的 Robinhood Chain 必须仍是 3751220 美元，并且高于另外三条链合计。

输出：
  output/json/rh_chain_fees.json
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "rh_chain_fees.json"
API = "https://api.llama.fi/summary/fees/"
UA = "dashboard-data/rh-chain-fees"
CHECK_DAY = "2026-09-01"
CHECK_RH = 3_751_220
CHAINS = (
    ("Robinhood Chain", "robinhood"),
    ("solana", "solana"),
    ("ethereum", "ethereum"),
    ("base", "base"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(slug: str) -> dict[str, int]:
    url = API + urllib.parse.quote(slug) + "?dataType=dailyFees"
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as resp:
                doc = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < 3:
                time.sleep(2**attempt)
    else:
        raise RuntimeError(f"GET {url} failed: {last}")
    out: dict[str, int] = {}
    for ts, val in doc.get("totalDataChart") or []:
        if not isinstance(val, (int, float)):
            continue
        day = datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")
        out[day] = int(round(float(val)))
    if len(out) < 30:
        raise RuntimeError(f"{slug} 只有 {len(out)} 天")
    return out


def main() -> None:
    series: dict[str, dict[str, int]] = {}
    for slug, key in CHAINS:
        print(f"[rh-chain-fees] fetch {slug}…")
        series[key] = fetch(slug)
        time.sleep(0.3)
    rh = series["robinhood"]
    if rh.get(CHECK_DAY) != CHECK_RH:
        raise SystemExit(f"{CHECK_DAY} Robinhood Chain 是 {rh.get(CHECK_DAY)}，不是 {CHECK_RH}")
    peers = series["solana"][CHECK_DAY] + series["ethereum"][CHECK_DAY] + series["base"][CHECK_DAY]
    if rh[CHECK_DAY] <= peers:
        raise SystemExit(f"{CHECK_DAY} 没有高于另外三条链")
    days = sorted(set.intersection(*(set(s) for s in series.values())))
    history = []
    for day in days:
        peer = series["solana"][day] + series["ethereum"][day] + series["base"][day]
        history.append(
            {
                "time": day,
                "robinhood": series["robinhood"][day],
                "solana": series["solana"][day],
                "ethereum": series["ethereum"][day],
                "base": series["base"][day],
                "peers": peer,
                "above": series["robinhood"][day] > peer,
            }
        )
    last = history[-1]
    snap = {
        "name": "rh_chain_fees",
        "source": "defillama/summary/fees dailyFees",
        "note_zh": "链自己的手续费。不是链上各项目手续费加总。对照 Solana、Ethereum、Base。",
        "updated_at": now_iso(),
        "as_of": last["time"],
        "history": history,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[rh-chain-fees] {len(history)} days as_of={last['time']} "
        f"rh={last['robinhood']} peers={last['peers']} above={last['above']}"
    )


if __name__ == "__main__":
    main()
