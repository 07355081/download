"""Pump.fun 手续费相关地址 — 大额 SOL 转出监控（Solana，不占 Dune）。

地址取自 Pump 官方 Global::fee_recipient / fee_recipients。
只对「转出 SOL」过线告警；累计账本用于里程碑 / 历史阈值抬头。

Usage:
  python monitor_pump_sol.py --dry-run
  python monitor_pump_sol.py --bootstrap
  python monitor_pump_sol.py                 # 拉增量、写 state，不推
  python monitor_pump_sol.py --push
  python monitor_pump_sol.py --history-alert --push
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_DIR = HERE / "output"
WATCHLIST_PATH = HERE / "watchlist_pump_sol.json"
STATE_PATH = OUT_DIR / "state_watchlist_pump_sol.json"
LEDGER_PATH = OUT_DIR / "pump_sol_ledger.json"

DEFAULT_RPCS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
]
HTTP_TIMEOUT = 45.0
USER_AGENT = "wallet-watch-pump-sol/1.0"
TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/wallet-watch"
LAMPORTS = 1_000_000_000
MARK_DAILY = "【实时日常报告】"
MARK_HIST = "【历史阈值警报】"
COINGECKO_SOL = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"


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

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def merge_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in (
        Path("/root/dashboard/.env.live"),
        ROOT.parent / "data-dashboard" / ".env.live",
        ROOT / ".env",
    ):
        env.update(load_env(path))
    for k in ("SOLANA_RPC_URL", "HELIUS_API_KEY", "LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def short_addr(addr: str) -> str:
    if len(addr) <= 14:
        return addr
    return f"{addr[:8]}…{addr[-6:]}"


def rpc_urls(env: dict[str, str]) -> list[str]:
    urls: list[str] = []
    custom = (env.get("SOLANA_RPC_URL") or "").strip()
    if custom:
        urls.append(custom)
    helius = (env.get("HELIUS_API_KEY") or "").strip()
    if helius:
        urls.append(f"https://mainnet.helius-rpc.com/?api-key={helius}")
    urls.extend(DEFAULT_RPCS)
    # de-dupe keep order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def rpc_call(urls: list[str], method: str, params: list[Any], *, sleep_s: float = 0.15) -> Any:
    last_err: Exception | None = None
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for url in urls:
        for attempt in range(3):
            time.sleep(sleep_s if attempt == 0 else min(8.0, 1.5 * (attempt + 1)))
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            )
            try:
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"])[:200])
                return payload.get("result")
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
    assert last_err is not None
    raise last_err


def fetch_sol_usd() -> float | None:
    try:
        req = urllib.request.Request(COINGECKO_SOL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        val = data.get("solana", {}).get("usd")
        return float(val) if val is not None else None
    except Exception:  # noqa: BLE001
        return None


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(datetime.now().timestamp()))
        payload["timestamp"] = ts
        digest = hmac.new(f"{ts}\n{secret}".encode("utf-8"), b"", hashlib.sha256).digest()
        payload["sign"] = base64.b64encode(digest).decode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", "replace")
    if not body:
        return
    parsed = json.loads(body)
    code = parsed.get("code", parsed.get("StatusCode"))
    if isinstance(code, int) and code != 0:
        raise RuntimeError(f"Lark rejected: {body[:200]}")


def account_index(keys: list[Any], address: str) -> int | None:
    for i, key in enumerate(keys):
        pk = key.get("pubkey") if isinstance(key, dict) else key
        if pk == address:
            return i
    return None


def parse_outflow(tx: dict[Any, Any], address: str) -> dict[str, Any] | None:
    if not tx:
        return None
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return None
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = message.get("accountKeys") or []
    idx = account_index(keys, address)
    if idx is None:
        return None
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if idx >= len(pre) or idx >= len(post):
        return None
    delta_lamports = int(post[idx]) - int(pre[idx])
    if delta_lamports >= 0:
        return None  # only outs
    amount = abs(delta_lamports) / LAMPORTS
    # best-effort counterpart: largest positive balance delta among others
    counterpart = ""
    best = 0
    for i, key in enumerate(keys):
        if i == idx or i >= len(pre) or i >= len(post):
            continue
        gain = int(post[i]) - int(pre[i])
        if gain > best:
            best = gain
            counterpart = key.get("pubkey") if isinstance(key, dict) else str(key)
    return {
        "amount": amount,
        "counterpart": counterpart,
        "fee_sol": float(meta.get("fee") or 0) / LAMPORTS,
    }


def fetch_signatures(urls: list[str], address: str, *, limit: int = 20) -> list[dict[str, Any]]:
    sigs = rpc_call(urls, "getSignaturesForAddress", [address, {"limit": limit}]) or []
    out: list[dict[str, Any]] = []
    for row in sigs:
        if not isinstance(row, dict):
            continue
        sig = str(row.get("signature") or "")
        if not sig:
            continue
        out.append(
            {
                "tx": sig,
                "ts": int(row.get("blockTime") or 0),
                "err": row.get("err"),
            }
        )
    return out


def fetch_outflows(
    urls: list[str],
    address: str,
    *,
    limit: int = 20,
    parse_tx: bool = True,
) -> list[dict[str, Any]]:
    """If parse_tx is False (bootstrap), only return signature shells for cursor seeding."""
    sigs = fetch_signatures(urls, address, limit=limit)
    if not parse_tx:
        return [
            {
                "chain": "solana",
                "watch": address,
                "tx": row["tx"],
                "ts": row["ts"],
                "direction": "out",
                "counterpart": "",
                "asset": "SOL",
                "amount": 0.0,
                "kind": "cursor",
            }
            for row in sigs
        ]

    events: list[dict[str, Any]] = []
    for row in sigs:
        if row.get("err") is not None:
            continue
        sig = str(row["tx"])
        ts = int(row["ts"] or 0)
        try:
            tx = rpc_call(
                urls,
                "getTransaction",
                [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                sleep_s=0.12,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  tx fail {sig[:16]}: {exc}")
            continue
        parsed = parse_outflow(tx or {}, address)
        if not parsed:
            continue
        events.append(
            {
                "chain": "solana",
                "watch": address,
                "tx": sig,
                "ts": ts,
                "direction": "out",
                "counterpart": parsed["counterpart"],
                "asset": "SOL",
                "amount": parsed["amount"],
                "kind": "native",
            }
        )
    return events


def when_cn(ts: int) -> str:
    if not ts:
        return "未知时间"
    return datetime.fromtimestamp(ts, tz=TZ_CN).strftime("%Y-%m-%d %H:%M")


def fmt_sol(n: float) -> str:
    if n >= 1000:
        return f"{n:,.0f}"
    return f"{n:,.2f}"


def fmt_usd(n: float | None) -> str:
    if n is None:
        return ""
    if n >= 1e8:
        return f"（约合 {n / 1e8:.2f} 亿美元）"
    if n >= 1e4:
        return f"（约合 {n / 1e4:.2f} 万美元）"
    return f"（约合 {n:,.0f} 美元）"


def format_alert(
    ev: dict[str, Any],
    *,
    label: str,
    history: bool,
    sol_usd: float | None,
    ledger: dict[str, Any],
) -> str:
    amount = float(ev.get("amount") or 0)
    usd = amount * sol_usd if sol_usd else None
    cum = float(ledger.get("cum_sol") or 0)
    cum_usd = float(ledger.get("cum_usd") or 0) or None
    avg = (cum_usd / cum) if cum > 0 and cum_usd else None
    mark = MARK_HIST if history else MARK_DAILY
    title = f"{mark}\nPump.fun 相关地址转出 {fmt_sol(amount)} 枚 SOL"
    body = (
        f"据吴说数据中心链上监测，标记为 Pump.fun 协议手续费收款的地址"
        f"于 {when_cn(int(ev.get('ts') or 0))} 转出 {fmt_sol(amount)} 枚 SOL"
        f"{fmt_usd(usd)}。"
    )
    if label:
        body += f"监控标签：{label}。"
    if ev.get("counterpart"):
        body += f"主要对手方地址 {short_addr(str(ev['counterpart']))}。"
    body += (
        f"自本监控账本记录以来，累计转出约 {fmt_sol(cum)} 枚 SOL"
        f"{fmt_usd(cum_usd)}"
    )
    if avg:
        body += f"，账本均价约 {avg:,.0f} 美元"
    body += "。"
    if history:
        body += (
            f"本条为历史回扫/里程碑警报（发现时间 "
            f"{datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M')} UTC+8），请勿当成刚刚发生。"
        )
    body += (
        "说明：地址来自 Pump 官方 Global 手续费收款配置；链上仅能确认转出，"
        "不能确认是否在交易所成交。与 Lookonchain 累计口径可能不一致。"
    )
    explorer = f"https://solscan.io/tx/{ev.get('tx')}"
    src = "https://arkm.com/explorer/entity/pump-fun"
    return "\n".join([PAGE, title, "", body, "", f"Arkham 实体参考：{src}", explorer])


def update_ledger(ledger: dict[str, Any], ev: dict[str, Any], sol_usd: float | None) -> dict[str, Any]:
    amount = float(ev.get("amount") or 0)
    usd_piece = amount * sol_usd if sol_usd else 0.0
    ledger["cum_sol"] = float(ledger.get("cum_sol") or 0) + amount
    ledger["cum_usd"] = float(ledger.get("cum_usd") or 0) + usd_piece
    ledger["tx_count"] = int(ledger.get("tx_count") or 0) + 1
    ledger["updated_at"] = now_utc_iso()
    last_milestone = float(ledger.get("last_milestone_sol") or 0)
    return ledger | {"_prev_milestone": last_milestone}


def crossed_milestone(ledger: dict[str, Any], step: float) -> bool:
    if step <= 0:
        return False
    cum = float(ledger.get("cum_sol") or 0)
    prev = float(ledger.get("_prev_milestone") or ledger.get("last_milestone_sol") or 0)
    new_level = int(cum // step) * step
    return new_level > prev and new_level > 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--bootstrap", action="store_true", help="只记游标，不告警")
    parser.add_argument("--history-alert", action="store_true", help="回扫大额转出，用历史抬头")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--watchlist", default=str(WATCHLIST_PATH))
    parser.add_argument("--limit", type=int, default=15, help="每地址回看签名条数")
    parser.add_argument("--max-alerts", type=int, default=10)
    args = parser.parse_args()

    env = merge_env()
    watch_path = Path(args.watchlist)
    if not watch_path.is_file():
        watch_path = HERE / args.watchlist
    if not watch_path.is_file():
        raise SystemExit(f"找不到 watchlist: {args.watchlist}")

    doc = load_json(watch_path, {})
    defaults = doc.get("defaults") if isinstance(doc.get("defaults"), dict) else {}
    min_sol = float(defaults.get("min_sol") or 10_000)
    history_min = float(defaults.get("history_min_sol") or 50_000)
    milestone = float(defaults.get("milestone_sol") or 100_000)
    case_title = str(doc.get("case_title") or "Pump.fun SOL 转出")
    addresses = [
        a
        for a in (doc.get("addresses") or [])
        if isinstance(a, dict) and a.get("enabled", True) and a.get("address") and a.get("chain") == "solana"
    ]
    print(f"[pump-sol] {case_title} addresses={len(addresses)} min_sol={min_sol}")
    if args.dry_run:
        for a in addresses:
            print(f"  {a.get('address')}  ({a.get('label')})")
        return 0

    urls = rpc_urls(env)
    state = load_json(STATE_PATH, {"seen": {}, "updated_at": None})
    seen: dict[str, bool] = dict(state.get("seen") or {})
    ledger = load_json(LEDGER_PATH, {"cum_sol": 0.0, "cum_usd": 0.0, "tx_count": 0, "last_milestone_sol": 0.0})
    sol_usd = fetch_sol_usd()
    print(f"[pump-sol] SOL/USD={sol_usd} rpc0={urls[0]}")

    alerts: list[tuple[dict[str, Any], str, bool]] = []
    label_of = {str(a["address"]): str(a.get("label") or "") for a in addresses}

    for item in addresses:
        address = str(item["address"])
        prefix = f"solana|{address}|"
        seeded = any(k.startswith(prefix) for k in seen)
        seed_only = (args.bootstrap or not seeded) and not args.history_alert
        try:
            batch = fetch_outflows(
                urls,
                address,
                limit=args.limit,
                parse_tx=not seed_only,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED {short_addr(address)}: {exc}")
            continue
        fresh = 0
        for ev in batch:
            key = f"solana|{address}|{ev['tx']}"
            if key in seen:
                continue
            seen[key] = True
            fresh += 1
            amount = float(ev.get("amount") or 0)
            if seed_only:
                continue
            if args.history_alert:
                if amount < history_min:
                    continue
                history = True
            else:
                if amount < min_sol:
                    continue
                # stale tx -> historical mark
                age_h = (time.time() - int(ev.get("ts") or time.time())) / 3600.0
                history = age_h >= 6.0
            ledger = update_ledger(ledger, ev, sol_usd)
            if crossed_milestone(ledger, milestone):
                history = True
                ledger["last_milestone_sol"] = int(float(ledger["cum_sol"]) // milestone) * milestone
            alerts.append((ev, label_of.get(address, ""), history))
        print(f"  {short_addr(address)} new={fresh} batch={len(batch)} seed_only={seed_only}")

    state["seen"] = seen
    state["updated_at"] = now_utc_iso()
    save_json(STATE_PATH, state)
    # strip helper
    ledger.pop("_prev_milestone", None)
    save_json(LEDGER_PATH, ledger)

    if not alerts:
        print("[pump-sol] 无过线告警")
        return 0

    alerts = alerts[: args.max_alerts]
    texts = [
        format_alert(ev, label=label, history=hist, sol_usd=sol_usd, ledger=ledger)
        for ev, label, hist in alerts
    ]
    for text in texts:
        print(text)
        print("---")

    if not args.push:
        print(f"[pump-sol] dry/no-push candidates={len(texts)}")
        return 0

    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    if not webhook:
        raise SystemExit("未配置 LARK_ALERT_WEBHOOK")
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    for text in texts:
        send_lark_text(webhook, secret, text)
        time.sleep(0.4)
    print(f"[pump-sol] pushed={len(texts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
