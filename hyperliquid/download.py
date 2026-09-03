"""Download Hyperliquid metrics from Dune Analytics into local JSON files.

Data source: a set of ASXN-maintained Dune queries (daily, refreshed ~08-10:00;
ASXN has ~1 day lag). We only READ the latest cached execution result via
DuneClient.get_latest_result — this does NOT trigger a re-execution, so credit
cost is low and it is safe to run daily from the VPS.

Outputs (one file per query):
  output/json/<name>.json
    {
      "code": "0",
      "msg": "success",
      "query_id": 8077142,
      "name": "hip3_overview",
      "description": "Hyperliquid HIP-3 整体交易量和持仓量",
      "source_url": "https://dune.com/queries/8077142",
      "updated_at": "2026-07-23T06:00:00Z",   # 本次抓取时间(UTC)
      "row_count": 123,
      "columns": ["day", "volume", "open_interest", ...],
      "data": [ { ...row... }, ... ]           # Dune 结果行(list[dict])
    }

Key handling (与项目其它模块一致):
  优先级: --api-key > 环境变量 DUNE_API_KEY > <repo>/.env 里的 DUNE_API_KEY

Usage:
  python download.py                      # 拉全部 query
  python download.py --only hip4          # 只拉某一个(按 name)
  python download.py --api-key xxxx       # 显式传 key
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT_DIR = HERE.parent                       # /root/data-download
OUTPUT_DIR = HERE / "output"
JSON_DIR = OUTPUT_DIR / "json"

# name -> (query_id, 中文说明)。name 即输出文件名 <name>.json,前端也按此约定读取。
# 若以后增删 query,改这里即可(copy_to_dashboard 的白名单也要同步)。
QUERIES: dict[str, tuple[int, str]] = {
    "hip3_overview":     (8077142, "Hyperliquid HIP-3 整体交易量和持仓量"),
    "hip3_by_category":  (8077405, "Hyperliquid HIP-3 按资产类别分类交易量和持仓量"),
    "hip3_by_market":    (8077374, "Hyperliquid HIP-3 按部署市场分类的交易量和持仓量"),
    "hip3_by_symbol":    (8077453, "Hyperliquid HIP-3 按具体资产分类的交易量和持仓量"),
    "hl_by_category":    (8077484, "Hyperliquid 按资产类别分类交易量"),
    "hip3_and_crypto":   (8077513, "Hyperliquid HIP-3 和 Crypto 分类交易量、持仓量及市场份额"),
    "hip4":              (8077860, "Hyperliquid HIP-4 交易量、交易笔数和活跃市场"),
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env(path: Path) -> None:
    """Minimal .env loader (与 coinglass-history/_common.py 同款,不引第三方依赖)。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def resolve_api_key(arg_key: str | None) -> str:
    if arg_key:
        return arg_key
    load_env(ROOT_DIR / ".env")
    key = (os.environ.get("DUNE_API_KEY") or "").strip()
    if not key:
        sys.exit(
            "ERROR: DUNE_API_KEY not provided. 用 --api-key 传入,或写入 "
            f"{ROOT_DIR / '.env'} (DUNE_API_KEY=xxxx),或 export DUNE_API_KEY。"
        )
    return key


def extract_rows(result) -> list[dict]:
    """从 dune_client 的 ResultsResponse 里取出行(list[dict])。兼容不同版本字段。"""
    # 常见:result.result.rows -> list[dict]
    res = getattr(result, "result", None)
    if res is not None:
        rows = getattr(res, "rows", None)
        if rows is not None:
            return list(rows)
    # 兜底:整体转 dict 再找 rows
    if hasattr(result, "get_rows"):
        try:
            return list(result.get_rows())
        except Exception:
            pass
    raise RuntimeError("无法从 Dune 返回结果中解析出 rows,请检查 dune-client 版本")


def fetch_one(dune, name: str, query_id: int, description: str) -> dict:
    result = dune.get_latest_result(query_id)
    rows = extract_rows(result)
    columns = list(rows[0].keys()) if rows else []
    return {
        "code": "0",
        "msg": "success",
        "query_id": query_id,
        "name": name,
        "description": description,
        "source_url": f"https://dune.com/queries/{query_id}",
        "updated_at": now_utc_iso(),
        "row_count": len(rows),
        "columns": columns,
        "data": rows,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)  # 原子替换,避免半写文件被前端读到


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-key", default=None, help="Dune API key(默认取 env / .env 的 DUNE_API_KEY)")
    parser.add_argument("--only", default=None, choices=sorted(QUERIES.keys()), help="只拉某一个 query(按 name)")
    parser.add_argument("--out", default=str(JSON_DIR), help="输出目录(默认 output/json)")
    parser.add_argument("--sleep", type=float, default=0.5, help="每个 query 之间的间隔秒数")
    args = parser.parse_args()

    api_key = resolve_api_key(args.api_key)

    try:
        from dune_client.client import DuneClient
    except ImportError:
        sys.exit(
            "ERROR: 缺少 dune-client。请在 venv 里安装: "
            f"{ROOT_DIR / '.venv/bin/pip'} install dune-client"
        )

    dune = DuneClient(api_key)
    out_dir = Path(args.out)

    targets = {args.only: QUERIES[args.only]} if args.only else QUERIES
    total = len(targets)
    ok = 0
    failed: list[str] = []

    print(f"[hyperliquid] 拉取 {total} 个 Dune query → {out_dir}")
    for i, (name, (query_id, desc)) in enumerate(targets.items(), 1):
        started = time.time()
        try:
            payload = fetch_one(dune, name, query_id, desc)
            write_json(out_dir / f"{name}.json", payload)
            print(f"  [{i}/{total}] {name:<18} q={query_id} rows={payload['row_count']:<6} "
                  f"{time.time() - started:.1f}s")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{total}] {name:<18} q={query_id} FAILED: {exc}")
            failed.append(name)
        time.sleep(max(0.0, args.sleep))

    print(f"\nDONE. ok={ok}/{total} failed={len(failed)}"
          + (f" ({', '.join(failed)})" if failed else ""))
    print(f"json -> {out_dir}")
    if failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
