"""拉取 GMGN 多链 Meme 24h 成交榜并落盘。

数据源：官方 OpenAPI `GET https://openapi.gmgn.ai/v1/market/rank`
覆盖链：sol / robinhood / bsc / arc / eth（与吴说「主要 Meme 市场」口径对齐）。

口径说明：
  - 链成交量 = 该链 24h volume 榜 Top N（默认 100，接口上限）代币成交额合计
  - 这是「核心榜单合计」，不是链上全部 meme 全量；与吴说前端展示可能有偏差
  - 日环比 = 与上一份已落库快照（按 Asia/Shanghai 自然日）的链合计对比

鉴权：
  - 环境变量 GMGN_API_KEY（推荐申请 https://gmgn.ai/ai）
  - 未配置时可用公开只读测试 key（限频、不稳定，勿作生产依赖）
  - 每次请求需 timestamp（±5s）+ 新鲜 client_id（UUID）
  - 仅支持 IPv4；本机若被墙，设 HTTPS_PROXY / ALL_PROXY

用法：
  python download.py
  python download.py --limit 50
  python download.py --env-file ../.env
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT_DIR = HERE.parent
JSON_DIR = HERE / "output" / "json"
HISTORY_DIR = JSON_DIR / "history"
LATEST_NAME = "meme_markets_24h.json"
HISTORY_SERIES_NAME = "meme_markets_history.json"

API_BASE = "https://openapi.gmgn.ai"
# 公开只读演示 key（GMGN 文档/skills 中用于连通性测试）；生产务必换成自有 key。
DEMO_API_KEY = "gmgn_solbscbaseethmonadtron"

CHAINS: list[tuple[str, str]] = [
    ("sol", "Solana"),
    ("robinhood", "Robinhood"),
    ("bsc", "BNB"),
    ("arc", "Arc"),
    ("eth", "以太坊"),
]

TZ_CN = timezone(timedelta(hours=8))
USER_AGENT = "dashboard-data/gmgn-meme"
HTTP_TIMEOUT = 30

# 强制走 IPv4：GMGN 文档写明不支持 IPv6，本机 AAAA 记录常导向不可达地址。
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_getaddrinfo  # type: ignore[assignment]

_SSL_CTX = ssl.create_default_context()


def now_cn() -> datetime:
    return datetime.now(TZ_CN)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cn_day(dt: datetime | None = None) -> str:
    return (dt or now_cn()).date().isoformat()


def cn_stamp(dt: datetime | None = None) -> str:
    d = dt or now_cn()
    return f"{d.month} 月 {d.day} 日 {d.hour:02d}:{d.minute:02d}"


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def api_key() -> str:
    return (os.environ.get("GMGN_API_KEY") or "").strip() or DEMO_API_KEY


def using_demo_key() -> bool:
    return api_key() == DEMO_API_KEY


def _current_proxy() -> str:
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("all_proxy")
        or ""
    )


def _build_opener() -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.HTTPSHandler(context=_SSL_CTX)
    ]
    proxy = _current_proxy()
    if proxy:
        if proxy.lower().startswith("socks"):
            try:
                import socks  # type: ignore
                from sockshandler import SocksiPyHandler  # type: ignore
                from urllib.parse import urlparse

                parsed = urlparse(proxy)
                scheme = (parsed.scheme or "").lower()
                ptype = (
                    socks.PROXY_TYPE_SOCKS4
                    if scheme.startswith("socks4")
                    else socks.PROXY_TYPE_SOCKS5
                )
                handlers.append(
                    SocksiPyHandler(
                        ptype,
                        parsed.hostname or "127.0.0.1",
                        parsed.port or 1080,
                        True,
                        parsed.username,
                        parsed.password,
                    )
                )
            except ImportError as e:
                raise RuntimeError(
                    f"检测到 SOCKS 代理但未安装 PySocks: {proxy}"
                ) from e
        else:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def http_get_json(url: str, headers: dict[str, str], *, attempts: int = 5) -> dict[str, Any]:
    opener = _build_opener()
    last_err: Exception | None = None
    for i in range(attempts):
        # timestamp 窗口 ±5s，每次重试重新签发
        parsed = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        q["timestamp"] = [str(int(time.time()))]
        q["client_id"] = [str(uuid.uuid4())]
        query = urllib.parse.urlencode({k: v[0] for k, v in q.items()})
        fresh = urllib.parse.urlunparse(parsed._replace(query=query))
        req = urllib.request.Request(
            fresh,
            headers={**headers, "User-Agent": USER_AGENT, "Accept": "application/json"},
            method="GET",
        )
        try:
            with opener.open(req, timeout=HTTP_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
        except urllib.error.HTTPError as e:
            last_err = e
            # 演示 Key / 限频常见 429；指数退避后再试
            wait = 8.0 * (i + 1) if e.code == 429 else 1.5 * (i + 1)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET failed after {attempts} attempts: {url} ({last_err})")


def _num(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def normalize_token(item: dict[str, Any], chain: str) -> dict[str, Any]:
    symbol = (
        item.get("symbol")
        or item.get("token_symbol")
        or item.get("name")
        or "?"
    )
    return {
        "chain": chain,
        "address": item.get("address") or item.get("token_address") or "",
        "symbol": str(symbol),
        "name": item.get("name") or item.get("token_name") or symbol,
        "volume": _num(item.get("volume")),
        "swaps": int(_num(item.get("swaps"))),
        "liquidity": _num(item.get("liquidity")),
        "market_cap": _num(item.get("market_cap") or item.get("marketcap")),
        "price": _num(item.get("price")),
    }


def fetch_chain_rank(chain: str, *, limit: int, interval: str) -> list[dict[str, Any]]:
    params = {
        "chain": chain,
        "interval": interval,
        "limit": str(limit),
        "order_by": "volume",
        "direction": "desc",
        "timestamp": str(int(time.time())),
        "client_id": str(uuid.uuid4()),
    }
    url = f"{API_BASE}/v1/market/rank?{urllib.parse.urlencode(params)}"
    payload = http_get_json(url, {"X-APIKEY": api_key()})
    # OpenAPI 偶发双层 {"code":0,"data":{"code":0,"data":{"rank":[...]}}}
    node: Any = payload
    for _ in range(4):
        if not isinstance(node, dict):
            break
        code = node.get("code")
        if code not in (0, "0", None):
            raise RuntimeError(
                f"{chain} API code={code} msg={node.get('message') or node.get('msg')}"
            )
        inner = node.get("data")
        if isinstance(inner, dict) and ("rank" in inner or "data" in inner or "code" in inner):
            node = inner
            continue
        if isinstance(inner, list):
            node = inner
            break
        break
    rank = None
    if isinstance(node, dict):
        rank = node.get("rank")
        if rank is None and isinstance(node.get("data"), list):
            rank = node.get("data")
    elif isinstance(node, list):
        rank = node
    if not isinstance(rank, list):
        keys = list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
        raise RuntimeError(f"{chain} unexpected payload keys: {keys}")
    return [normalize_token(x, chain) for x in rank if isinstance(x, dict)]


def build_snapshot(*, limit: int, interval: str) -> dict[str, Any]:
    fetched_at = now_cn()
    chains_out: list[dict[str, Any]] = []
    all_tokens: list[dict[str, Any]] = []

    for chain_id, label in CHAINS:
        tokens = fetch_chain_rank(chain_id, limit=limit, interval=interval)
        vol = sum(t["volume"] for t in tokens)
        chains_out.append(
            {
                "chain": chain_id,
                "label": label,
                "volume": vol,
                "token_count": len(tokens),
                "top": tokens[:10],
            }
        )
        all_tokens.extend(tokens)
        # 演示 Key 很容易 429，链间多歇一会
        time.sleep(2.5 if using_demo_key() else 0.5)

    total = sum(c["volume"] for c in chains_out)
    for c in chains_out:
        c["share"] = (c["volume"] / total * 100.0) if total else 0.0

    chains_out.sort(key=lambda c: -c["volume"])
    top_global = sorted(all_tokens, key=lambda t: -t["volume"])[:3]

    return {
        "source": "gmgn_openapi",
        "interval": interval,
        "limit_per_chain": limit,
        "methodology": (
            "各链成交量=GMGN 24h volume 榜 TopN 代币成交额合计；"
            "占比=该链合计/纳入链合计；日环比依赖历史快照。"
        ),
        "day": cn_day(fetched_at),
        "fetched_at_cn": fetched_at.strftime("%Y-%m-%d %H:%M:%S"),
        "fetched_at_cn_text": cn_stamp(fetched_at),
        "generated_at": now_utc_iso(),
        "using_demo_key": using_demo_key(),
        "total_volume": total,
        "chains": chains_out,
        "top_tokens": top_global,
    }


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def attach_dod(snapshot: dict[str, Any]) -> dict[str, Any]:
    """挂载日环比：优先找上一自然日 history，否则找最近一份非当日快照。"""
    day = snapshot["day"]
    prev_path = HISTORY_DIR / f"{(datetime.fromisoformat(day) - timedelta(days=1)).date().isoformat()}.json"
    prev = load_json(prev_path)
    if prev is None:
        candidates = sorted(HISTORY_DIR.glob("*.json")) if HISTORY_DIR.is_dir() else []
        for path in reversed(candidates):
            if path.stem >= day:
                continue
            prev = load_json(path)
            if prev:
                break

    prev_total = float((prev or {}).get("total_volume") or 0)
    total = float(snapshot["total_volume"] or 0)
    snapshot["prev_day"] = (prev or {}).get("day")
    snapshot["prev_total_volume"] = prev_total or None
    snapshot["dod_pct"] = ((total / prev_total - 1.0) * 100.0) if prev_total else None

    prev_by_chain = {
        c["chain"]: float(c.get("volume") or 0)
        for c in (prev or {}).get("chains") or []
        if isinstance(c, dict)
    }
    for c in snapshot["chains"]:
        pv = prev_by_chain.get(c["chain"])
        c["prev_volume"] = pv
        c["dod_pct"] = ((c["volume"] / pv - 1.0) * 100.0) if pv else None
    return snapshot


def write_snapshot(snapshot: dict[str, Any]) -> Path:
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    latest = JSON_DIR / LATEST_NAME
    latest.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    hist = HISTORY_DIR / f"{snapshot['day']}.json"
    hist.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_history_series()
    return latest


def _series_point(snap: dict[str, Any], fallback_day: str) -> dict[str, Any] | None:
    day = str(snap.get("day") or fallback_day or "").strip()
    if not day:
        return None
    chains = []
    for row in snap.get("chains") or []:
        if not isinstance(row, dict) or not row.get("chain"):
            continue
        chains.append(
            {
                "chain": row.get("chain"),
                "label": row.get("label"),
                "volume": float(row.get("volume") or 0),
            }
        )
    return {
        "day": day,
        "total_volume": float(snap.get("total_volume") or 0),
        "chains": chains,
    }


def write_history_series() -> Path:
    """按自然日合并全史。已有走势里的旧日期保留，同一天被新快照覆盖。"""
    by_day: dict[str, dict[str, Any]] = {}
    existing = load_json(JSON_DIR / HISTORY_SERIES_NAME) or {}
    for point in existing.get("points") or []:
        if isinstance(point, dict) and point.get("day"):
            by_day[str(point["day"])] = point
    if HISTORY_DIR.is_dir():
        for path in sorted(HISTORY_DIR.glob("*.json")):
            snap = load_json(path)
            if not snap:
                continue
            point = _series_point(snap, path.stem)
            if point:
                by_day[point["day"]] = point
    points = [by_day[day] for day in sorted(by_day)]
    payload = {
        "source": "gmgn_openapi",
        "generated_at": now_utc_iso(),
        "points": points,
    }
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    path = JSON_DIR / HISTORY_SERIES_NAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--series-only", action="store_true", help="只根据已有 history/ 重写走势 JSON，不请求接口")
    parser.add_argument("--limit", type=int, default=100, help="每链 Top N（默认 100，接口上限）")
    parser.add_argument("--interval", default="24h", choices=("1m", "5m", "1h", "6h", "24h"))
    parser.add_argument(
        "--env-file",
        default=str(ROOT_DIR / ".env"),
        help="读取 GMGN_API_KEY / 代理变量（默认 data-download/.env）",
    )
    args = parser.parse_args()

    load_env_file(Path(args.env_file))
    if args.series_only:
        path = write_history_series()
        payload = load_json(path) or {}
        print(f"wrote {path} points={len(payload.get('points') or [])}")
        return 0

    limit = max(1, min(100, int(args.limit)))

    print(f"GMGN OpenAPI → {', '.join(c for c, _ in CHAINS)} interval={args.interval} limit={limit}")
    print(f"API key: {'DEMO' if using_demo_key() else 'custom'} | proxy={_current_proxy() or 'none'}")

    try:
        snap = build_snapshot(limit=limit, interval=args.interval)
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] fetch failed: {e}", file=sys.stderr)
        print(
            "提示: GMGN 仅 IPv4；若 TcpTest 失败请设 HTTPS_PROXY，或在可访问 openapi.gmgn.ai 的服务器上跑。",
            file=sys.stderr,
        )
        return 1

    snap = attach_dod(snap)
    path = write_snapshot(snap)
    print(f"wrote {path}")
    print(
        f"total=${snap['total_volume']:,.0f} day={snap['day']} "
        f"dod={snap['dod_pct'] if snap['dod_pct'] is not None else 'n/a'}"
    )
    for c in snap["chains"]:
        print(
            f"  {c['label']}: ${c['volume']:,.0f} ({c['share']:.1f}%) "
            f"dod={c['dod_pct'] if c['dod_pct'] is not None else 'n/a'} top={c['top'][0]['symbol'] if c['top'] else '-'}"
        )
    if snap["using_demo_key"]:
        print("[WARN] 正在使用公开演示 API Key，请尽快配置自有 GMGN_API_KEY", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
