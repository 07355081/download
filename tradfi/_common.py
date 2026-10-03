"""TradFi 原生 API 下载管道 —— 共享基础库。

设计要点：
- 代理感知：读取 HTTPS_PROXY / HTTP_PROXY / ALL_PROXY 环境变量，有则走代理，无则直连。
- 备用域名：部分交易所主域名在国内 SSL 失败，内置备用域名自动切换。
- 重试 + 限流：指数退避，简单令牌间隔。
- 增量断点：已有 JSON 时只回补更新的 K 线（读末尾时间戳）。
- 输出对齐 coinglass 的 futures-price-history：行对象 {time(ms), open, high, low, close, volume_usd}。
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "output" / "json"
PRICE_DIR = OUTPUT_DIR / "tradfi-price"
OI_DIR = OUTPUT_DIR / "tradfi-oi"
FUNDING_DIR = OUTPUT_DIR / "tradfi-funding"
SYMBOLS_PATH = OUTPUT_DIR / "tradfi-symbols.json"

# 复用现有 tag 系统的 Stocks 集合（数据源头，非发布版）
TAG_SUMMARY_PATH = ROOT_DIR.parent / "tag" / "output" / "json" / "base_asset_categories_summary.json"

USER_AGENT = "tradfi-native-downloader/1.0"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 4
DEFAULT_MAX_DAILY_ROWS = 5000  # ~13 年日线，作为"尽量回补最长历史"的安全上限

# ───────────────────────── 资产分类 ─────────────────────────
# 只保留 stocks / commodities / indices，排除 forex / crypto。

INDEX_BASES = {
    "SPX", "SPX500", "SP500", "NAS100", "NASDAQ100", "NDX", "US500", "US100", "US30",
    "DJI", "DJ30", "HK50", "HSI", "JP225", "NIKKEI", "UK100", "FTSE", "DE40", "DAX",
    "RUSSELL", "RUT", "QQQ", "SPY", "SQQQ", "TQQQ", "IWM", "DIA",
}
COMMODITY_BASES = {
    "XAU", "XAG", "XPT", "XPD", "XCU", "PAXG", "XAUT", "GOLD", "SILVER", "PLATINUM",
    "PALLADIUM", "COPPER", "NATGAS", "NG", "CL", "BZ", "WTI", "BRENT", "OIL", "USOIL",
    "UKOIL", "WTIOIL", "BRENTOIL",
}
FOREX_BASES = {
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCHF", "USDCAD", "EURJPY",
    "EURGBP", "EUR", "GBP", "JPY", "AUD", "NZD", "CHF", "CAD",
}

SYMBOL_SYNONYMS = {
    "GOLD": "XAU", "SILVER": "XAG", "PLATINUM": "XPT", "PALLADIUM": "XPD",
    "COPPER": "XCU", "SP500": "SPX", "SPX500": "SPX", "NASDAQ100": "NAS100",
    "NDX": "NAS100", "WTI": "CL", "USOIL": "CL", "WTIOIL": "CL",
    "UKOIL": "BZ", "BRENTOIL": "BRENT", "NATGAS": "NG",
}

# 已知与 tradfi 符号表 / tag Stocks 撞名的加密资产：强制判为非 tradfi（None），
# 避免把真加密（QNT=Quant、DIA=DIA 预言机）误当股票/指数而从 coinglass 下线。
# 如发现更多撞名，往这里补即可（单一真源，native 发现 + coinglass 排除 + route 均生效）。
# SPX=SPX6900 meme（≠标普,标普用 SPX500/SPY/US500）；PAXG/XAUT=黄金背书加密代币（≠真金 XAU/XAG）。
# 这几个只在"靠 base 集合识别"的适配器(MEXC/HTX/Kraken)里会误判,原生标记型适配器不受影响。
CRYPTO_OVERRIDE = {"QNT", "DIA", "SPX", "PAXG", "XAUT"}

# 无歧义加密 base：即使适配器传 native_stock=True 也不得进 TradFi。
# 用于拦住 HIP-3 加密盘（hyna:BTC）和由此污染的 HTX 白名单（BTC-USDT 永续）。
# 不要放可能与股票/ETF 撞名的短代码（IP / LIT / GAS）。
CRYPTO_BASES = {
    "BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "BCH", "LTC", "SUI",
    "XMR", "ZEC", "LINK", "DOT", "AVAX", "ATOM", "NEAR", "ARB", "OP",
    "FIL", "UNI", "AAVE", "MKR", "LDO", "PEPE", "SHIB", "WIF", "BONK",
    "FARTCOIN", "ENA", "HYPE", "PUMP", "BASED", "XPL", "LIGHTER", "USDE",
    "TOTAL2", "TRX", "TON", "SEI", "TIA", "INJ", "WLD", "ONDO", "JUP",
    "RENDER", "FET", "TAO", "PENDLE", "IMX", "RUNE",
}


def normalize_base(base: str) -> str:
    b = (base or "").strip().upper()
    return SYMBOL_SYNONYMS.get(b, b)


def strip_stock_suffix(base: str) -> str:
    """去掉交易所自定义的 STOCK / RWA 后缀，还原真实 ticker。"""
    b = (base or "").strip().upper()
    for suf in ("STOCK", "RWA"):
        if b.endswith(suf) and len(b) > len(suf):
            return b[: -len(suf)]
    return b


# tag 里被标为 Stocks 的 base_asset 集合（懒加载，driver 启动时 load_tag_stocks() 注入）
_TAG_STOCKS: set[str] = set()


def load_tag_stocks() -> set[str]:
    """从 tag/base_asset_categories_summary.json 读出所有含 Stocks 标签的 base_asset。"""
    global _TAG_STOCKS
    try:
        raw = json.loads(TAG_SUMMARY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _TAG_STOCKS = set()
        return _TAG_STOCKS
    assets = raw.get("assets") if isinstance(raw, dict) else None
    out: set[str] = set()
    if isinstance(assets, dict):
        for sym, tags in assets.items():
            tag_list = tags if isinstance(tags, list) else str(tags or "").split("|")
            if any("stock" in str(t).lower() for t in tag_list):
                out.add(normalize_base(strip_stock_suffix(sym)))
    _TAG_STOCKS = out
    return out


def is_crypto_base(base_raw: str) -> bool:
    """True 表示该 base 绝不能进 TradFi（含 native_stock=True / 跨所白名单）。"""
    raw = (base_raw or "").strip().upper()
    base = normalize_base(strip_stock_suffix(raw))
    if base in CRYPTO_OVERRIDE or base in CRYPTO_BASES:
        return True
    if raw.startswith("1000") and len(raw) > 4:
        return True
    return False


def classify_sector(base_raw: str, *, native_stock: bool = False) -> str | None:
    """返回 Stocks / Commodities / Indices；forex / 无法判定返回 None。

    个股识别 = 交易所原生标记（native_stock，如 instCategory=3 / symbolType=stock /
    isRwa / *STOCK* 后缀）或 base_asset 命中 tag 的 Stocks 集合。
    大宗 / 指数用固定集合判定。
    加密（CRYPTO_OVERRIDE / CRYPTO_BASES）一律 None，避免 HIP-3/HTX 永续混进 Stocks。
    """
    base = normalize_base(strip_stock_suffix(base_raw))
    if is_crypto_base(base_raw):
        return None
    if base in FOREX_BASES:
        return None
    if base in COMMODITY_BASES:
        return "Commodities"
    if base in INDEX_BASES:
        return "Indices"
    if native_stock or base in _TAG_STOCKS:
        return "Stocks"
    return None


# ───────────────────────── 数据结构 ─────────────────────────


@dataclass
class Instrument:
    exchange: str
    instrument_id: str
    base_asset: str
    sector: str  # Stocks / Commodities / Indices
    form: str = "perp"  # perp / cfd / spot
    quote_asset: str = ""


@dataclass
class HttpResult:
    ok: bool
    data: Any = None
    error: str = ""


# ───────────────────────── HTTP ─────────────────────────

_SSL_CTX = ssl.create_default_context()


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


def _socks_handler(proxy: str) -> urllib.request.BaseHandler:
    """为 socks4/socks5/socks5h 代理（如 ssh -D 建立的本地 SOCKS）构造 urllib handler。

    需要 PySocks（pip install PySocks）。socks5h 表示由代理端解析 DNS（跨境场景必须）。
    """
    try:
        import socks  # type: ignore
        from sockshandler import SocksiPyHandler  # type: ignore
    except ImportError as e:  # noqa: BLE001
        raise RuntimeError(
            "检测到 SOCKS 代理但未安装 PySocks。请先 `pip install PySocks`。"
            f" 代理={proxy}"
        ) from e

    from urllib.parse import urlparse

    parsed = urlparse(proxy)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 1080
    if scheme in ("socks5", "socks5h"):
        ptype = socks.PROXY_TYPE_SOCKS5
    elif scheme in ("socks4", "socks4a"):
        ptype = socks.PROXY_TYPE_SOCKS4
    else:
        ptype = socks.PROXY_TYPE_SOCKS5
    # socks5h / socks4a → rdns=True（远端解析域名）；socks5/socks4 亦默认远端解析更稳。
    rdns = scheme in ("socks5h", "socks4a", "socks5", "socks4")
    return SocksiPyHandler(ptype, host, port, rdns, parsed.username, parsed.password)


def _proxy_handler() -> urllib.request.BaseHandler | None:
    proxy = _current_proxy()
    if not proxy:
        return None
    if proxy.lower().startswith("socks"):
        return _socks_handler(proxy)
    return urllib.request.ProxyHandler({"http": proxy, "https": proxy})


@dataclass
class Http:
    """轻量 HTTP 客户端：代理感知 + 重试 + 限流。"""

    min_interval_s: float = 0.12
    timeout: int = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    _last_call: float = field(default=0.0, repr=False)
    _opener: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        handlers = [urllib.request.HTTPSHandler(context=_SSL_CTX)]
        ph = _proxy_handler()
        if ph is not None:
            handlers.append(ph)
        self._opener = urllib.request.build_opener(*handlers)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        self._last_call = time.monotonic()

    def get_json(self, url: str, *, headers: dict[str, str] | None = None) -> HttpResult:
        last_err = ""
        for attempt in range(self.retries):
            self._throttle()
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": USER_AGENT, **(headers or {})}
                )
                with self._opener.open(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                return HttpResult(ok=True, data=json.loads(raw))
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                # 451/403 是地理封锁，重试无意义
                if e.code in (451, 403, 401):
                    return HttpResult(ok=False, error=last_err)
                time.sleep(1.0 + attempt * 1.5)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                last_err = str(e)[:120]
                time.sleep(1.0 + attempt * 1.5)
        return HttpResult(ok=False, error=last_err)

    def get_json_multi(self, urls: Iterable[str], *, headers: dict[str, str] | None = None) -> HttpResult:
        """依次尝试多个候选域名（主域名 + 备用域名），返回首个成功的。"""
        err = ""
        for url in urls:
            res = self.get_json(url, headers=headers)
            if res.ok:
                return res
            err = res.error
        return HttpResult(ok=False, error=err)

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> HttpResult:
        """POST JSON body（Hyperliquid info API 等）。"""
        last_err = ""
        body = json.dumps(payload).encode("utf-8")
        base_headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json", **(headers or {})}
        for attempt in range(self.retries):
            self._throttle()
            try:
                req = urllib.request.Request(url, data=body, headers=base_headers, method="POST")
                with self._opener.open(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                return HttpResult(ok=True, data=json.loads(raw))
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                if e.code in (451, 403, 401):
                    return HttpResult(ok=False, error=last_err)
                time.sleep(1.0 + attempt * 1.5)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                last_err = str(e)[:120]
                time.sleep(1.0 + attempt * 1.5)
        return HttpResult(ok=False, error=last_err)


# ───────────────────────── 输出 / 增量 ─────────────────────────


def ensure_dirs() -> None:
    for d in (PRICE_DIR, OI_DIR, FUNDING_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _safe_seg(value: str) -> str:
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in value)


def price_file(exchange: str, instrument_id: str, interval: str) -> Path:
    return PRICE_DIR / f"{_safe_seg(exchange)}_{_safe_seg(instrument_id)}_{interval}.json"


def oi_file(exchange: str, instrument_id: str, interval: str) -> Path:
    return OI_DIR / f"{_safe_seg(exchange)}_{_safe_seg(instrument_id)}_{interval}.json"


def funding_file(exchange: str, instrument_id: str) -> Path:
    return FUNDING_DIR / f"{_safe_seg(exchange)}_{_safe_seg(instrument_id)}.json"


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(doc, dict):
        rows = doc.get("data") or doc.get("rows") or []
    else:
        rows = doc
    return rows if isinstance(rows, list) else []


def write_rows(path: Path, rows: list[dict], meta: dict | None = None) -> None:
    payload: dict[str, Any] = {"data": rows}
    if meta:
        payload.update(meta)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def merge_rows(existing: list[dict], fresh: list[dict], time_key: str = "time") -> list[dict]:
    """按时间戳去重合并，fresh 覆盖 existing。"""
    by_time: dict[Any, dict] = {}
    for r in existing:
        t = r.get(time_key)
        if t is not None:
            by_time[t] = r
    for r in fresh:
        t = r.get(time_key)
        if t is not None:
            by_time[t] = r
    return [by_time[t] for t in sorted(by_time)]


def latest_time(rows: list[dict], time_key: str = "time") -> int | None:
    ts = [r.get(time_key) for r in rows if isinstance(r.get(time_key), (int, float))]
    return int(max(ts)) if ts else None


# ───────────────────────── 数值/时间归一 ─────────────────────────


def to_num(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None  # 排除 NaN
    except (TypeError, ValueError):
        return None


def to_ms(v: Any) -> int | None:
    n = to_num(v)
    if n is None:
        return None
    n = int(n)
    if n < 1_000_000_000_000:  # 秒 → 毫秒
        n *= 1000
    return n


INTERVAL_MS = {
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}
