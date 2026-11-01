# -*- coding: utf-8 -*-
"""Hyperliquid 大额持仓：只在穿过历史阈值时推 Lark。

第一次只把当前快照记下来，不发。之后：
- 单地址、单币种名义持仓从阈值下方穿到阈值上方，发一条
- 大额动作里新出现的平仓，名义达到阈值，发一条

不合并地址，不写姓名。消息第一行是程序标记【历史阈值警报】，新闻标题仍是正文第一句。

用法：
  python hyperliquid/whale_threshold.py
  python hyperliquid/whale_threshold.py --push
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE_PATH = HERE / "output" / "whale_threshold_state.json"
LOG_PATH = HERE / "whale_threshold.log"
ENV_FILES = [
    ROOT / ".env",
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
]
POSITION_URL = "https://open-api-v4.coinglass.com/api/hyperliquid/whale-position"
ALERT_URL = "https://open-api-v4.coinglass.com/api/hyperliquid/whale-alert"
DASHBOARD_URL = "https://data.wublock123.com/dashboard"
MARK = "【历史阈值警报】"
TZ_CN = timezone(timedelta(hours=8))
DEFAULT_POSITION_USD = 200_000_000.0
DEFAULT_CLOSE_USD = 50_000_000.0
MAX_SENDS = 3
NAMES = {"BTC": "比特币", "ETH": "以太坊", "SOL": "Solana"}


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


def load_env() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in ENV_FILES:
        merged.update(load_env_file(path))
    for key in ("COINGLASS_API_KEY", "LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET", "HL_WHALE_POSITION_USD", "HL_WHALE_CLOSE_USD"):
        if os.environ.get(key):
            merged[key] = os.environ[key]
    if not merged.get("COINGLASS_API_KEY"):
        common = ROOT / "coinglass-history" / "_common.py"
        if common.is_file():
            for line in common.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("COINGLASS_API_KEY"):
                    merged["COINGLASS_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return merged


def env_float(env: dict[str, str], key: str, default: float) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def fetch_rows(url: str, api_key: str) -> list[dict[str, Any]]:
    req = urllib.request.Request(
        url,
        headers={"CG-API-KEY": api_key, "accept": "application/json", "User-Agent": "wu-hl-whale/1.0"},
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    code = str(payload.get("code", "0"))
    if code not in ("0", "200"):
        raise RuntimeError(f"CoinGlass {url.rsplit('/', 1)[-1]} code={code} msg={payload.get('msg')}")
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError(f"CoinGlass {url.rsplit('/', 1)[-1]} 没有列表")
    return [row for row in data if isinstance(row, dict)]


def num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def pos_key(row: dict[str, Any]) -> str | None:
    user = str(row.get("user") or "").strip().lower()
    symbol = str(row.get("symbol") or "").strip().upper()
    if not user or not symbol:
        return None
    return f"{user}|{symbol}"


def short_addr(user: str) -> str:
    text = user.strip()
    if len(text) <= 12:
        return text
    return f"{text[:6]}…{text[-4:]}"


def coin_name(symbol: str) -> str:
    return NAMES.get(symbol, symbol)


def side_name(size: float) -> str:
    return "多单" if size > 0 else "空单"


def fmt_usd(usd: float) -> str:
    return f"{usd / 1e8:.2f} 亿美元"


def fmt_coins(size: float) -> str:
    amount = abs(size)
    if amount >= 100:
        return f"{amount:,.0f}"
    return f"{amount:,.2f}"


def fmt_px(value: float | None) -> str:
    if value is None or value <= 0:
        return ""
    return f"{value:,.2f}"


def stamp_now() -> str:
    now = datetime.now(TZ_CN)
    return f"{now.month}月{now.day}日{now.hour:02d}:{now.minute:02d}"


def position_text(row: dict[str, Any], usd: float) -> str:
    symbol = str(row.get("symbol") or "").upper()
    size = num(row.get("position_size")) or 0.0
    liq = fmt_px(num(row.get("liq_price")))
    liq_bit = f"清算价约 {liq} 美元" if liq else "清算价暂缺"
    body = (
        f"据吴说数据中心统计，截至{stamp_now()}，Hyperliquid 上地址 {short_addr(str(row.get('user') or ''))} "
        f"的{coin_name(symbol)}{side_name(size)}名义持仓达到 {fmt_usd(usd)}，约 {fmt_coins(size)} 枚，{liq_bit}。"
        f"该地址此前提仓未达到这一规模。名义金额按标记价格计算，清算价会随保证金变化。"
    )
    return f"{MARK}\n{body}\n{DASHBOARD_URL}"


def close_text(row: dict[str, Any], usd: float) -> str:
    symbol = str(row.get("symbol") or "").upper()
    size = num(row.get("position_size")) or 0.0
    entry = fmt_px(num(row.get("entry_price")))
    entry_bit = f"入场价约 {entry} 美元。" if entry else ""
    body = (
        f"据吴说数据中心统计，截至{stamp_now()}，Hyperliquid 上地址 {short_addr(str(row.get('user') or ''))} "
        f"平掉{coin_name(symbol)}{side_name(size)}约 {fmt_coins(size)} 枚，名义约 {fmt_usd(usd)}。{entry_bit}"
        f"此为持仓动作记录中的平仓，不能据此认定已经爆仓。"
    )
    return f"{MARK}\n{body}\n{DASHBOARD_URL}"


def alert_id(row: dict[str, Any]) -> str | None:
    key = pos_key(row)
    created = row.get("create_time")
    if not key or created is None:
        return None
    return f"{key}|{created}|{row.get('position_action')}"


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"bootstrapped": False, "positions": {}, "seen_alerts": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"bootstrapped": False, "positions": {}, "seen_alerts": []}
    if not isinstance(doc, dict):
        return {"bootstrapped": False, "positions": {}, "seen_alerts": []}
    positions = doc.get("positions") if isinstance(doc.get("positions"), dict) else {}
    seen = doc.get("seen_alerts") if isinstance(doc.get("seen_alerts"), list) else []
    return {"bootstrapped": bool(doc.get("bootstrapped")), "positions": positions, "seen_alerts": seen}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(datetime.now().timestamp()))
        digest = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(digest).decode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", "replace")
    if not body:
        return
    parsed = json.loads(body)
    code = parsed.get("code", parsed.get("StatusCode"))
    if isinstance(code, int) and code != 0:
        raise RuntimeError(f"Lark rejected: {body[:200]}")


def log_line(text: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(text.rstrip() + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    env = load_env()
    api_key = (env.get("COINGLASS_API_KEY") or "").strip()
    if not api_key:
        print("[ERROR] 未配置 COINGLASS_API_KEY", file=sys.stderr)
        return 1
    position_usd = env_float(env, "HL_WHALE_POSITION_USD", DEFAULT_POSITION_USD)
    close_usd = env_float(env, "HL_WHALE_CLOSE_USD", DEFAULT_CLOSE_USD)

    try:
        positions = fetch_rows(POSITION_URL, api_key)
        alerts = fetch_rows(ALERT_URL, api_key)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[skip] 持仓接口不可用，不发：{exc}", file=sys.stderr)
        return 1

    state = load_state()
    previous: dict[str, float] = {}
    for key, item in state["positions"].items():
        if isinstance(item, dict):
            value = num(item.get("usd"))
        else:
            value = num(item)
        if value is not None:
            previous[str(key)] = value
    seen = {str(item) for item in state["seen_alerts"]}

    current: dict[str, float] = {}
    current_rows: dict[str, dict[str, Any]] = {}
    for row in positions:
        key = pos_key(row)
        usd = num(row.get("position_value_usd"))
        if not key or usd is None:
            continue
        usd = abs(usd)
        current[key] = usd
        current_rows[key] = row

    crossings: list[tuple[str, dict[str, Any], float]] = []
    if state["bootstrapped"]:
        for key, usd in current.items():
            if usd >= position_usd and previous.get(key, 0.0) < position_usd:
                crossings.append((key, current_rows[key], usd))

    closes: list[tuple[str, dict[str, Any], float]] = []
    new_seen: list[str] = []
    for row in alerts:
        ident = alert_id(row)
        if not ident or ident in seen:
            continue
        new_seen.append(ident)
        action = row.get("position_action")
        usd = num(row.get("position_value_usd"))
        if not state["bootstrapped"] or action != 2 or usd is None:
            continue
        usd = abs(usd)
        if usd >= close_usd:
            closes.append((ident, row, usd))

    if not state["bootstrapped"]:
        save_state({
            "bootstrapped": True,
            "positions": {key: {"usd": usd} for key, usd in current.items()},
            "seen_alerts": list(seen | set(new_seen))[-2000:],
        })
        print(f"[bootstrap] positions={len(current)} alerts={len(new_seen)} 不发")
        log_line(f"{datetime.now(TZ_CN).isoformat(timespec='seconds')} bootstrap positions={len(current)}")
        return 0

    if len(crossings) > MAX_SENDS or len(closes) > MAX_SENDS:
        print(f"[skip] 单轮穿过 {len(crossings)} 条、平仓 {len(closes)} 条，超过 {MAX_SENDS}，整轮不发")
        log_line(
            f"{datetime.now(TZ_CN).isoformat(timespec='seconds')} skip burst "
            f"cross={len(crossings)} close={len(closes)}"
        )
        return 0

    texts = [position_text(row, usd) for _key, row, usd in crossings]
    texts.extend(close_text(row, usd) for _ident, row, usd in closes)
    for text in texts:
        print(text)
        print("---")

    sent = 0
    if args.push and texts:
        webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
        secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
        if not webhook:
            print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
            return 1
        for text in texts:
            send_lark(webhook, secret, text)
            sent += 1

    save_state({
        "bootstrapped": True,
        "positions": {key: {"usd": usd} for key, usd in current.items()},
        "seen_alerts": list(seen | set(new_seen))[-2000:],
    })
    log_line(
        f"{datetime.now(TZ_CN).isoformat(timespec='seconds')} cross={len(crossings)} "
        f"close={len(closes)} sent={sent}"
    )
    print(f"[done] cross={len(crossings)} close={len(closes)} sent={sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
