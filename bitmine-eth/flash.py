# -*- coding: utf-8 -*-
"""Bitmine 周持仓 → Lark。

- 新的一周：最新持仓和当周增减
- 最新一份 8-K 对得上同一持仓时，补质押、现金、ORBS、总资产
- 当周增持高于此前每一周再加一条阈值
- 质押数较上一份已记录的 8-K 增加时另发一条。第一次只记基线，不发已披露的数。
- 不补发旧周。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "bitmine_eth.json",
    Path("/root/dashboard/public/json/bitmine-eth/bitmine_eth.json"),
]
STATE_PATH = HERE / "output" / "bitmine_eth_lark.json"
LOG_PATH = HERE / "bitmine_eth_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/bitmine-eth"
MIN_PRIOR = 6


def _ensure_lark_copy() -> None:
    import sys as _sys
    from pathlib import Path as _Path
    folder = _Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in _sys.path:
        _sys.path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news

def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def merge_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        env.update(load_env_file(path))
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def load_doc(explicit: str) -> tuple[Path, dict[str, Any]]:
    paths = [Path(explicit)] if explicit else JSON_CANDIDATES
    for path in paths:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("history"), list):
                return path, doc
    raise FileNotFoundError("找不到 bitmine_eth.json")


def comma(n: int) -> str:
    return f"{n:,}"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{y} 年 {int(m)} 月 {int(d)} 日"


def usd_zh(amount: int) -> str:
    if amount >= 100_000_000:
        value = amount / 100_000_000
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return f"{text} 亿美元"
    value = amount / 10_000
    text = f"{value:.0f}" if abs(value - round(value)) < 0.05 else f"{value:.1f}"
    return f"{text} 万美元"


def pack_of(doc: dict[str, Any], row: dict[str, Any]) -> dict[str, Any] | None:
    pack = doc.get("latest_pack")
    if not isinstance(pack, dict):
        return None
    try:
        if int(pack["eth"]) != int(row["eth"]):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return pack


def core(row: dict[str, Any], pack: dict[str, Any] | None = None) -> str:
    eth = int(row["eth"])
    add = int(row.get("week_add") or (pack or {}).get("week_add") or 0)
    verb = "增加" if add >= 0 else "减少"
    day = str((pack or {}).get("as_of") or row["time"])
    bits = [
        f"据吴说数据中心统计，截至 {cn_day(day)}，"
        f"Bitmine（BMNR）持有 {comma(eth)} 枚 ETH，"
        f"较上一披露周{verb} {comma(abs(add))} 枚"
    ]
    if pack and isinstance(pack.get("supply_pct"), (int, float)):
        pct = float(pack["supply_pct"])
        bits.append(f"约占 ETH 总供应量的 {pct:g}%")
        gap = 5.0 - pct
        if 0 < gap <= 1.0:
            bits.append(f"距离持股占总供应 5% 约差 {gap:.2f} 个百分点")
        elif abs(gap) < 0.005:
            bits.append("已接近持股占总供应 5%")
    text = "，".join(bits) + "。"
    if pack:
        extra: list[str] = []
        staked = pack.get("staked")
        if isinstance(staked, int) and staked > 0:
            if eth > 0:
                extra.append(
                    f"已质押 {comma(staked)} 枚，约占其 ETH 持仓的 {staked / eth * 100:.1f}%"
                )
            else:
                extra.append(f"已质押 {comma(staked)} 枚")
        if isinstance(pack.get("staking_rev_usd"), int):
            extra.append(f"预计年化质押收入约 {usd_zh(int(pack['staking_rev_usd']))}")
        if extra:
            text += "，".join(extra) + "。"
        bag: list[str] = []
        if isinstance(pack.get("total_usd"), int):
            bag.append(f"公司加密资产、现金及 moonshots 合计约 {usd_zh(int(pack['total_usd']))}")
        inner: list[str] = []
        if isinstance(pack.get("cash_usd"), int):
            inner.append(f"{usd_zh(int(pack['cash_usd']))}现金")
        if isinstance(pack.get("orbs_usd"), int):
            inner.append(f"约 {usd_zh(int(pack['orbs_usd']))} Eightco（ORBS）持仓")
        if bag and inner:
            text += bag[0] + "，其中包括 " + "及".join(inner) + "。"
        elif bag:
            text += bag[0] + "。"
        elif inner:
            text += "其中包括 " + "及".join(inner) + "。"
    return f"{text}\n{DASHBOARD_URL}"


def is_record(row: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    add = row.get("week_add")
    if not isinstance(add, int):
        return False
    prior = [
        int(r["week_add"])
        for r in rows
        if str(r.get("time")) < str(row["time"]) and isinstance(r.get("week_add"), int)
    ]
    if len(prior) < MIN_PRIOR:
        return False
    return add > max(prior)


def gen_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    marks = {LARK_MARK_DAILY, LARK_MARK_THRESHOLD, LARK_MARK_SPECIAL}
    heads: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s in marks:
            heads.append(s)
        elif not s:
            continue
        else:
            break
    body = as_news(text)
    if heads:
        body = "\n".join(heads) + "\n" + body
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": body}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_sign(secret, ts)
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if str(result.get("code", "0")) != "0":
        raise RuntimeError(result)


def load_state() -> dict[str, Any]:
    base: dict[str, Any] = {"sentIds": [], "stakedBaselined": False}
    if not STATE_PATH.is_file():
        return base
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return base
    if not isinstance(doc, dict):
        return base
    ids = doc.get("sentIds")
    base["sentIds"] = [str(x) for x in ids] if isinstance(ids, list) else []
    base["stakedBaselined"] = bool(doc.get("stakedBaselined"))
    if isinstance(doc.get("stakedBaseline"), int):
        base["stakedBaseline"] = int(doc["stakedBaseline"])
    if isinstance(doc.get("stakedBaselineDay"), str):
        base["stakedBaselineDay"] = doc["stakedBaselineDay"]
    return base


def stake_increase_text(pack: dict[str, Any], previous: int) -> tuple[str, str] | None:
    staked = pack.get("staked")
    if not isinstance(staked, int) or staked <= previous:
        return None
    day = str(pack.get("as_of") or pack.get("filing_date") or "")
    if len(day) != 10:
        return None
    delta = staked - previous
    text = (
        f"吴说获悉，据吴说数据中心统计，截至 {cn_day(day)}，"
        f"Bitmine（BMNR）披露已质押 ETH {comma(staked)} 枚，"
        f"较上一份披露增加 {comma(delta)} 枚。"
        f"口径为公司最新 SEC 8-K，不是小时级链上流水。"
        f"\n{DASHBOARD_URL}"
    )
    return f"bitmine-eth-staked-{day}-{staked}", text


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    try:
        path, doc = load_doc(args.json)
        rows = [r for r in doc["history"] if isinstance(r, dict) and r.get("time")]
        rows.sort(key=lambda r: str(r["time"]))
        row = rows[-1]
    except (OSError, json.JSONDecodeError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    day = str(row["time"])
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    pack = pack_of(doc, row)
    print(f"[bitmine-eth] json={path} as_of={day} age={age}d pack={bool(pack)}")
    if age > 14:
        print("[ERROR] 最新持仓超过 14 天，不发", file=sys.stderr)
        return 1

    briefs = []
    body = core(row, pack)
    if is_record(row, rows):
        text = (
            f"Bitmine 当周增持 {int(row['week_add']):,} 枚 ETH，高于此前每一周。"
            + body
        )
        briefs.append(
            (
                "threshold",
                f"bitmine-eth-threshold-{day}",
                f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}\n{text}",
            )
        )
    # 有 8-K 包时同样作周更日常（含质押/占比/年化收入）；无包则只发持仓增减
    daily_id = f"bitmine-eth-pack-{day}" if pack else f"bitmine-eth-weekly-{day}"
    briefs.append(("daily", daily_id, f"{LARK_MARK_DAILY}\n{body}"))

    state = load_state()
    stake_boot = False
    if pack and isinstance(pack.get("staked"), int):
        staked_now = int(pack["staked"])
        stake_day = str(pack.get("as_of") or pack.get("filing_date") or day)
        if not state.get("stakedBaselined"):
            state["stakedBaselined"] = True
            state["stakedBaseline"] = staked_now
            state["stakedBaselineDay"] = stake_day
            stake_boot = True
            print(f"[bootstrap] staked={staked_now} day={stake_day} 不发已披露质押")
        else:
            previous = state.get("stakedBaseline")
            if isinstance(previous, int):
                hit = stake_increase_text(pack, previous)
                if hit:
                    msg_id, text = hit
                    briefs.append(("staked", msg_id, text))
    sent = set(state["sentIds"])
    for kind, msg_id, text in briefs:
        print("---")
        print(f"id={msg_id} kind={kind} already={msg_id in sent}")
        print(text)

    if not args.push:
        if stake_boot:
            print("[bootstrap] 未写基线（dry-run）")
        print("\n[dry-run] 未推送")
        return 0
    if stake_boot:
        save_state(state)

    env = merge_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    sent_any = False
    for _kind, msg_id, text in briefs:
        if msg_id in state["sentIds"] and not args.force:
            print(f"skip {msg_id}")
            continue
        try:
            send_lark(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {msg_id}: {exc}", file=sys.stderr)
            return 1
        print(f"  → Lark OK {msg_id}")
        state["sentIds"].append(msg_id)
        if _kind == "staked" and pack and isinstance(pack.get("staked"), int):
            state["stakedBaseline"] = int(pack["staked"])
            state["stakedBaselineDay"] = str(pack.get("as_of") or pack.get("filing_date") or day)
            state["stakedBaselined"] = True
        sent_any = True
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"=== {stamp} id={msg_id}\n{text}\n\n")
        time.sleep(0.4)
    if sent_any:
        state["sentIds"] = state["sentIds"][-80:]
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
