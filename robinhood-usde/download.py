"""Robinhood Chain 上的 USDe、USDG 和稳定币总流通量。DefiLlama 免费日序列。

2026-08-27 必须仍是 USDe 320451052、USDG 425627043，对不上就停。
金库把多少流动性分到 Morpho 市场，这条接口没有，不写。

输出：
  output/json/robinhood_usde.json
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "robinhood_usde.json"
API = "https://stablecoins.llama.fi/stablecoincharts/" + urllib.parse.quote("Robinhood Chain")
UA = "dashboard-data/robinhood-usde"
CHECK_DAY = "2026-08-27"
CHECK_USDE = 320_451_052
CHECK_USDG = 425_627_043


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url: str) -> list:
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            if not isinstance(data, list):
                raise RuntimeError(f"不是列表: {url}")
            return data
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < 3:
                time.sleep(2**attempt)
    raise RuntimeError(f"GET {url} failed: {last}")


def dollars(row: dict) -> int:
    circ = row.get("totalCirculatingUSD") or {}
    raw = circ.get("peggedUSD") if isinstance(circ, dict) else None
    if raw is None:
        raise RuntimeError(f"缺少流通量: {row.get('date')}")
    return int(round(float(raw)))


def by_day(rows: list) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        ts = int(row["date"])
        day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        out[day] = dollars(row)
    return out


def main() -> None:
    print("[robinhood-usde] fetch DefiLlama…")
    total = by_day(fetch(API))
    usde = by_day(fetch(API + "?stablecoin=146"))
    usdg = by_day(fetch(API + "?stablecoin=286"))
    days = sorted(set(total) & set(usde) & set(usdg))
    if usde.get(CHECK_DAY) != CHECK_USDE or usdg.get(CHECK_DAY) != CHECK_USDG:
        raise SystemExit(
            f"{CHECK_DAY} USDe={usde.get(CHECK_DAY)} USDG={usdg.get(CHECK_DAY)}，"
            f"不是 {CHECK_USDE} / {CHECK_USDG}"
        )
    history = []
    for day in days:
        tot = total[day]
        share = round(usde[day] / tot * 100, 2) if tot else None
        history.append(
            {
                "time": day,
                "usde": usde[day],
                "usdg": usdg[day],
                "total": tot,
                "share_pct": share,
            }
        )
    if len(history) < 30:
        raise SystemExit(f"天数太少: {len(history)}")
    last = history[-1]
    snap = {
        "name": "robinhood_usde",
        "source": "https://stablecoins.llama.fi/stablecoincharts/Robinhood%20Chain",
        "note_zh": "Robinhood Chain 上 USDe、USDG 和稳定币总流通量。不含金库流向 Morpho 的比例。",
        "updated_at": now_iso(),
        "as_of": last["time"],
        "history": history,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[robinhood-usde] {len(history)} days, as_of={last['time']} "
        f"USDe={last['usde']} share={last['share_pct']} USDG={last['usdg']}"
    )


if __name__ == "__main__":
    main()
