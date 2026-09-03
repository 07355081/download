"""下载 CoinGecko 全部分类，按市值降序排列，输出 CSV。

数据来源（CoinGecko 公开 API）：
  GET /coins/categories?order=market_cap_desc

说明：
  - 一次请求即可拿到全部分类（约 700+），无需分页。
  - CSV 列：rank, id, name, market_cap
  - 有市值数字则填入 market_cap；没有则留空（不填 0、不填 null）。
  - 不下载 content、top_3_coins 等与分类列表无关的字段。

运行：
  python download_coingecko_categories.py

输出：
  output/csv/coingecko_categories_by_market_cap.csv
"""
from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# ── 路径与常量（按需修改）────────────────────────────────────────

# 脚本所在目录，用于定位输出路径
ROOT = Path(__file__).resolve().parent

# CSV 输出路径
OUT_CSV = ROOT / "output" / "csv" / "coingecko_categories_by_market_cap.csv"

# CSV 列名（顺序即文件列顺序）
CSV_COLUMNS = ("rank", "id", "name", "market_cap")

# CoinGecko API 根地址（免费 Demo API，无需 Key；若限流可加大 RETRY_SLEEP）
GECKO_BASE = "https://api.coingecko.com/api/v3"

# HTTP 请求头里的 User-Agent，避免被部分网关拒识
UA = "coingecko-category-downloader/1.0"

# 请求失败时的最大重试次数（含 429 限流）
MAX_RETRIES = 5

# 每次重试前的等待秒数基数（实际等待 = 基数 + 已重试次数）
RETRY_SLEEP_S = 1.5


# ── HTTP 工具 ────────────────────────────────────────────────────


def get_json(url: str, *, params: dict[str, str] | None = None, timeout: int = 120) -> Any:
    """发起 GET 请求并解析 JSON；429/网络错误时自动退避重试。"""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)

    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            # 429 表示请求过快，等待后重试
            if e.code == 429 and attempt + 1 < MAX_RETRIES:
                time.sleep(RETRY_SLEEP_S + attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt + 1 < MAX_RETRIES:
                time.sleep(RETRY_SLEEP_S + attempt)
                continue
            raise
    raise last_err  # type: ignore[misc]


# ── 分类拉取与整理 ───────────────────────────────────────────────


def fetch_all_categories() -> list[dict[str, Any]]:
    """从 CoinGecko 拉取带市值数据的分类列表（API 已按市值降序，本地会再排一次）。"""
    print("正在请求 CoinGecko /coins/categories ...", flush=True)
    rows = get_json(
        f"{GECKO_BASE}/coins/categories",
        params={"order": "market_cap_desc"},
    )
    if not isinstance(rows, list):
        raise RuntimeError(f"API 返回格式异常，期望 list，实际: {type(rows).__name__}")
    return [r for r in rows if isinstance(r, dict)]


def parse_market_cap(raw: Any) -> float | None:
    """把 API 里的 market_cap 转成 float；缺失或非数字则返回 None。"""
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # 0 或负数视为无效，输出时留空
    if value <= 0:
        return None
    return value


def normalize_category(row: dict[str, Any]) -> dict[str, Any] | None:
    """把单条 API 记录整理为中间格式：id、name 必填；market_cap 可选（float）。"""
    cat_id = str(row.get("id") or row.get("category_id") or "").strip()
    name = str(row.get("name") or "").strip()
    if not cat_id or not name:
        return None

    item: dict[str, Any] = {"id": cat_id, "name": name}
    market_cap = parse_market_cap(row.get("market_cap"))
    if market_cap is not None:
        item["market_cap"] = market_cap
    return item


def sort_categories(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 market_cap 降序；无市值的分类排在最后，同类之间按 name 字母序。"""
    with_cap = [c for c in categories if "market_cap" in c]
    without_cap = [c for c in categories if "market_cap" not in c]

    with_cap.sort(key=lambda c: c["market_cap"], reverse=True)
    without_cap.sort(key=lambda c: c["name"].lower())

    return with_cap + without_cap


def to_csv_rows(categories: list[dict[str, Any]]) -> list[dict[str, str]]:
    """把分类列表转为 CSV 行；market_cap 无值时留空字符串。"""
    rows: list[dict[str, str]] = []
    for rank, item in enumerate(categories, start=1):
        rows.append({
            "rank": str(rank),
            "id": item["id"],
            "name": item["name"],
            "market_cap": "" if "market_cap" not in item else str(item["market_cap"]),
        })
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """写入 CSV（utf-8-sig 方便 Excel 打开）；先写 .tmp 再替换。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


# ── 入口 ─────────────────────────────────────────────────────────


def main() -> int:
    raw_rows = fetch_all_categories()
    print(f"  API 返回 {len(raw_rows)} 条原始记录", flush=True)

    categories: list[dict[str, Any]] = []
    skipped = 0
    for row in raw_rows:
        item = normalize_category(row)
        if item is None:
            skipped += 1
            continue
        categories.append(item)

    categories = sort_categories(categories)
    csv_rows = to_csv_rows(categories)

    write_csv(OUT_CSV, csv_rows)

    with_cap_count = sum(1 for c in categories if "market_cap" in c)
    without_cap_count = len(categories) - with_cap_count

    print(
        f"  有效分类: {len(categories)}"
        f"（有市值 {with_cap_count}，无市值 {without_cap_count}）",
        flush=True,
    )
    if skipped:
        print(f"  跳过无效记录: {skipped}", flush=True)
    if categories:
        top = categories[0]
        cap_text = f", market_cap={top['market_cap']:,.0f}" if "market_cap" in top else ""
        print(f"  市值第一: {top['id']} ({top['name']}{cap_text})", flush=True)
    print(f"已写入 -> {OUT_CSV}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
