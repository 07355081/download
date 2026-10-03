"""Dune Analytics 取数公用层：REST 客户端 + "缓存过期就自己触发执行"的逻辑。

被 uni-burn / robinhood-chain 共用。

为什么要能自己触发执行:
  Dune 的 get_latest_result 只读"上一次有人跑过"的缓存快照。如果目标 query 不属于
  本账号、且 owner 没给它挂调度,缓存会永久冻结 —— 线上 UNI Burn 面板就是这样卡在
  2026-08-16、连发六天同一份 677 行数据。所以这里先看缓存执行时间,超过 max_age_hours
  就自己 POST /execute 触发一次真实执行,轮询到完成后按 execution_id 取结果。
  owner 自己有调度且缓存还新的 query 走不到触发分支,不花执行额度。
  ASXN 的 Hyperliquid 系列一旦停调度,同样由这个分支接管。

为什么只用标准库、不用 dune-client:
  判断新鲜度要读 execution_ended_at,取结果要按 execution_id 走 /execution/{id}/results,
  这两处字段/方法在 dune-client 各版本间命名不一致。直接打 REST 更可控,也少一个依赖。

Key 解析优先级(与项目其它模块一致): --api-key > 环境变量 DUNE_API_KEY > <repo>/.env
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

API_BASE = "https://api.dune.com/api/v1"

# 全历史扫描一次要几百 credits。日常不准自动 POST /execute；
# --force-execute 仍可手动跑。窄查询换了新的 query id 之后不受这份名单影响。
AUTO_EXECUTE_BLOCKLIST = frozenset({8024180, 8260046})


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dune_ts(value: str | None) -> datetime | None:
    """Dune 返回形如 2026-08-22T03:29:50.752325346Z,小数位可能超过 6 位。"""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        frac, sign, offset = tail.partition("+")
        text = f"{head}.{frac[:6]}{sign}{offset}" if sign else f"{head}.{frac[:6]}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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


def resolve_api_key(arg_key: str | None, root_dir: Path) -> str:
    if arg_key:
        return arg_key
    load_env(root_dir / ".env")
    key = (os.environ.get("DUNE_API_KEY") or "").strip()
    if not key:
        sys.exit(
            "ERROR: DUNE_API_KEY not provided. 用 --api-key 传入,或写入 "
            f"{root_dir / '.env'} (DUNE_API_KEY=xxxx),或 export DUNE_API_KEY。"
        )
    return key


class DuneCreditsExhausted(RuntimeError):
    """本计费周期 datapoint 额度已用尽。同一轮里不应再 POST /execute。"""


def explain_dune_http(method: str, url: str, code: int, detail: str) -> RuntimeError:
    """把 Dune 的 HTTP 错误收成一行原因。402 与档位错误分开，方便日志判断。"""
    text = detail.lower()
    if code == 402 or "datapoint limit" in text or "billing cycle" in text:
        return DuneCreditsExhausted(f"额度不足: HTTP {code}: {detail}")
    if code == 400 and "performance" in text:
        return RuntimeError(f"档位不被这把 key 接受: HTTP {code}: {detail}")
    return RuntimeError(f"Dune {method} {url} -> HTTP {code}: {detail}")


class DuneApi:
    """只封装需要的几个 Dune REST 接口(results / execute / status)。"""

    def __init__(self, api_key: str, timeout: float = 180.0) -> None:
        self._headers = {"X-Dune-API-Key": api_key, "Content-Type": "application/json"}
        self._timeout = timeout
        self.credits_exhausted = False

    def _request(self, method: str, url: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            err = explain_dune_http(method, url, exc.code, detail)
            if isinstance(err, DuneCreditsExhausted):
                self.credits_exhausted = True
            raise err from exc

    def latest_meta(self, query_id: int) -> dict:
        """只取 1 行,用来判断缓存执行时间,几乎不花额度。"""
        return self._request("GET", f"{API_BASE}/query/{query_id}/results?limit=1")

    def execute(self, query_id: int, performance: str | None) -> str:
        # 档位跟套餐绑定:有的账号不认 medium(会 400 Invalid performance tier)。
        # 不传则让 Dune 按当前 key 的默认引擎选。
        if self.credits_exhausted:
            raise DuneCreditsExhausted("额度不足: 本轮已确认 datapoint 用尽,不再执行")
        body = {"performance": performance} if performance else None
        res = self._request("POST", f"{API_BASE}/query/{query_id}/execute", body)
        execution_id = res.get("execution_id")
        if not execution_id:
            raise RuntimeError(f"execute 未返回 execution_id: {res}")
        return execution_id

    def status(self, execution_id: str) -> dict:
        return self._request("GET", f"{API_BASE}/execution/{execution_id}/status")

    def rows(self, url: str) -> tuple[list[dict], dict]:
        """按 next_uri 翻页取全部行,返回 (rows, 首页响应)。"""
        first = self._request("GET", url)
        rows = list(first.get("result", {}).get("rows", []))
        page = first
        while page.get("next_uri"):
            page = self._request("GET", page["next_uri"])
            rows.extend(page.get("result", {}).get("rows", []))
        return rows, first


def wait_for_execution(api: DuneApi, execution_id: str, timeout_s: float, poll_s: float) -> dict:
    deadline = time.time() + timeout_s
    while True:
        st = api.status(execution_id)
        state = st.get("state", "")
        if st.get("is_execution_finished") or state.endswith(("COMPLETED", "FAILED", "CANCELLED")):
            if not state.endswith("COMPLETED"):
                raise RuntimeError(f"execution {execution_id} 结束但状态为 {state}")
            return st
        if time.time() >= deadline:
            raise RuntimeError(
                f"execution {execution_id} 超过 {timeout_s:.0f}s 仍未完成(state={state})"
            )
        time.sleep(poll_s)


@dataclass
class FetchResult:
    rows: list[dict]
    execution_id: str | None
    execution_ended_at: str | None
    executed_by_us: bool
    unchanged: bool = False


def read_local_execution_id(path: Path) -> str | None:
    """本地 JSON 顶层的 execution_id。文件不存在或坏了就当成没有缓存。"""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    execution_id = payload.get("execution_id") if isinstance(payload, dict) else None
    return execution_id if isinstance(execution_id, str) and execution_id else None


def _unchanged(execution_id: str | None, execution_ended_at: str | None) -> FetchResult:
    return FetchResult(
        rows=[],
        execution_id=execution_id,
        execution_ended_at=execution_ended_at,
        executed_by_us=False,
        unchanged=True,
    )


def fetch_query(
    api: DuneApi,
    query_id: int,
    args: argparse.Namespace,
    indent: str = "    ",
    local_execution_id: str | None = None,
) -> FetchResult:
    """缓存够新就复用;否则自己触发一次执行,再按 execution_id 取结果。

    本地 execution_id 与 Dune 缓存相同,且这次不需要重新执行时,不翻页下载整表。
    整表下载按返回的 datapoint 计费,没变的快照再拉一次只是重复扣额度。
    """
    cached_ended_at: str | None = None
    remote_execution_id: str | None = None
    age_h: float | None = None

    if not args.force_execute:
        meta = api.latest_meta(query_id)
        remote_execution_id = meta.get("execution_id")
        cached_ended_at = meta.get("execution_ended_at")
        ended = parse_dune_ts(cached_ended_at)
        if ended is not None:
            age_h = (datetime.now(timezone.utc) - ended).total_seconds() / 3600.0

    blocked = query_id in AUTO_EXECUTE_BLOCKLIST and not args.force_execute
    stale = args.force_execute or age_h is None or age_h > args.max_age_hours
    need_exec = stale and not args.no_execute and not blocked

    if blocked and local_execution_id and (
        not remote_execution_id or local_execution_id == remote_execution_id
    ):
        print(f"{indent}q={query_id} 禁止自动执行全历史查询,保留本地文件")
        return _unchanged(local_execution_id, cached_ended_at)

    if (
        not need_exec
        and not args.force_execute
        and local_execution_id
        and remote_execution_id
        and local_execution_id == remote_execution_id
    ):
        age_txt = f"age={age_h:.1f}h" if age_h is not None else "age=?"
        print(f"{indent}execution_id 未变({age_txt}),跳过整表下载")
        return _unchanged(remote_execution_id, cached_ended_at)

    if not need_exec:
        age_txt = f"age={age_h:.1f}h" if age_h is not None else "age=?"
        if blocked:
            why = f"{age_txt},禁止自动执行,只读缓存"
        elif stale:
            why = f"{age_txt},--no-execute 兜底"
        else:
            why = f"{age_txt} <= {args.max_age_hours}h"
        rows, first = api.rows(f"{API_BASE}/query/{query_id}/results")
        print(f"{indent}复用缓存执行({why}),未消耗执行额度")
        return FetchResult(
            rows=rows,
            execution_id=first.get("execution_id"),
            execution_ended_at=first.get("execution_ended_at"),
            executed_by_us=False,
        )

    reason = "强制执行" if args.force_execute else (
        f"缓存已 {age_h:.1f}h > {args.max_age_hours}h" if age_h is not None else "拿不到缓存执行时间"
    )
    print(f"{indent}{reason},触发一次执行(performance={args.performance})…")
    execution_id = api.execute(query_id, args.performance)
    st = wait_for_execution(api, execution_id, args.exec_timeout, args.poll_interval)
    credits = st.get("execution_cost_credits")
    print(
        f"{indent}execution {execution_id} 完成"
        + (f",约 {credits:.1f} credits" if isinstance(credits, (int, float)) else "")
    )
    rows, first = api.rows(f"{API_BASE}/execution/{execution_id}/results")
    return FetchResult(
        rows=rows,
        execution_id=first.get("execution_id") or execution_id,
        execution_ended_at=first.get("execution_ended_at") or st.get("execution_ended_at"),
        executed_by_us=True,
    )


def build_payload(
    name: str,
    query_id: int,
    description: str,
    result: FetchResult,
) -> dict:
    """统一的发布结构:前端只读 data,其余字段用于排查数据是哪一次执行来的。"""
    return {
        "code": "0",
        "msg": "success",
        "query_id": query_id,
        "name": name,
        "description": description,
        "source_url": f"https://dune.com/queries/{query_id}",
        "updated_at": now_utc_iso(),
        "execution_id": result.execution_id,
        "execution_ended_at": result.execution_ended_at,
        "executed_by_us": result.executed_by_us,
        "row_count": len(result.rows),
        "columns": list(result.rows[0].keys()) if result.rows else [],
        "data": result.rows,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


def add_common_args(parser: argparse.ArgumentParser, default_max_age_hours: float) -> None:
    """各 Dune 模块共用的 CLI 开关。"""
    parser.add_argument("--api-key", default=None, help="Dune API key(默认取 env / .env 的 DUNE_API_KEY)")
    parser.add_argument("--sleep", type=float, default=0.5, help="每个 query 之间的间隔秒数")
    parser.add_argument(
        "--max-age-hours", type=float, default=default_max_age_hours,
        help=f"缓存执行结果超过这个小时数就自己触发执行(默认 {default_max_age_hours:g})",
    )
    parser.add_argument("--force-execute", action="store_true", help="无视缓存年龄,强制触发执行")
    parser.add_argument("--no-execute", action="store_true", help="只读缓存,永不触发执行")
    parser.add_argument(
        "--performance", default="free", choices=["", "free", "small", "medium", "large"],
        help="执行算力档位;默认 free。当前这把 key 不认 medium/large,不传会走 2 分钟 Small 并超时",
    )
    parser.add_argument("--exec-timeout", type=float, default=900.0, help="等待执行完成的最长秒数")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="轮询执行状态的间隔秒数")
    parser.add_argument("--dry-run", action="store_true", help="只打印缓存新鲜度判断,不写文件、不触发执行")


def row_day(row: dict, date_key: str) -> str:
    value = row.get(date_key)
    return str(value)[:10] if value else ""


def merge_rows_by_date(old_rows: list[dict], new_rows: list[dict], date_key: str) -> list[dict]:
    """新结果里出现的日期整段替换,更早的历史行保留。"""
    new_days = {row_day(row, date_key) for row in new_rows}
    new_days.discard("")
    kept = [row for row in old_rows if row_day(row, date_key) not in new_days]
    merged = kept + list(new_rows)
    merged.sort(key=lambda row: row_day(row, date_key))
    return merged


def print_dry_run(
    api: DuneApi,
    targets: dict[str, tuple[int, str]],
    max_age_hours: float,
    label: str,
    header: bool = True,
) -> None:
    if header:
        print(f"[{label}] DRY-RUN:只检查缓存新鲜度")
    for name, (query_id, _) in targets.items():
        meta = api.latest_meta(query_id)
        ended = parse_dune_ts(meta.get("execution_ended_at"))
        age_h = (datetime.now(timezone.utc) - ended).total_seconds() / 3600.0 if ended else None
        stale = age_h is None or age_h > max_age_hours
        if query_id in AUTO_EXECUTE_BLOCKLIST and stale:
            verdict = "禁止自动执行"
        elif stale:
            verdict = "会触发执行"
        else:
            verdict = "复用缓存"
        print(
            f"  {name:<26} q={query_id} cached_ended={meta.get('execution_ended_at')} "
            f"age={'?' if age_h is None else f'{age_h:.1f}h'} -> {verdict}"
        )


def run_module(
    label: str,
    queries: dict[str, tuple[int, str]],
    args: argparse.Namespace,
    out_dir: Path,
    root_dir: Path,
    merge_on: dict[str, str] | None = None,
    max_age_overrides: dict[str, float] | None = None,
) -> None:
    """各 Dune 模块 download.py 的公共主流程。

    merge_on: name -> 日期列。窄查询只返回最近几天时,用它把新日期叠进本地历史。
    max_age_overrides: name -> 小时。同一模块里需要更勤刷新的查询单独设。
    """
    api = DuneApi(resolve_api_key(args.api_key, root_dir))
    targets = {args.only: queries[args.only]} if getattr(args, "only", None) else queries

    if args.dry_run:
        saved_age = args.max_age_hours
        print(f"[{label}] DRY-RUN:只检查缓存新鲜度")
        for name, (query_id, _) in targets.items():
            args.max_age_hours = (max_age_overrides or {}).get(name, saved_age)
            print_dry_run(api, {name: (query_id, "")}, args.max_age_hours, label, header=False)
        args.max_age_hours = saved_age
        return

    total = len(targets)
    ok = 0
    failed: list[str] = []
    names = list(targets.items())
    saved_age = args.max_age_hours
    print(f"[{label}] 拉取 {total} 个 Dune query → {out_dir}")
    for i, (name, (query_id, desc)) in enumerate(names, 1):
        started = time.time()
        dest = out_dir / f"{name}.json"
        args.max_age_hours = (max_age_overrides or {}).get(name, saved_age)
        try:
            print(f"  [{i}/{total}] {name:<26} q={query_id}")
            result = fetch_query(
                api, query_id, args, local_execution_id=read_local_execution_id(dest)
            )
            if result.unchanged:
                print(f"    未改写 {dest.name} {time.time() - started:.1f}s")
                ok += 1
                continue
            payload = build_payload(name, query_id, desc, result)
            date_key = (merge_on or {}).get(name)
            if date_key and dest.exists():
                try:
                    previous = json.loads(dest.read_text(encoding="utf-8"))
                    old_rows = previous.get("data") if isinstance(previous, dict) else None
                except (OSError, json.JSONDecodeError):
                    old_rows = None
                if isinstance(old_rows, list) and old_rows:
                    payload["data"] = merge_rows_by_date(old_rows, payload["data"], date_key)
                    payload["row_count"] = len(payload["data"])
                    payload["merged_history"] = True
            write_json(dest, payload)
            print(f"    rows={payload['row_count']:<6} {time.time() - started:.1f}s")
            ok += 1
        except DuneCreditsExhausted as exc:
            print(f"    FAILED: {exc}")
            failed.append(name)
            for skipped, _ in names[i:]:
                print(f"    SKIP {skipped}: 额度不足,不再执行")
                failed.append(skipped)
            break
        except Exception as exc:  # noqa: BLE001
            print(f"    FAILED: {exc}")
            failed.append(name)
        time.sleep(max(0.0, args.sleep))
    args.max_age_hours = saved_age

    print(
        f"\nDONE. ok={ok}/{total} failed={len(failed)}"
        + (f" ({', '.join(failed)})" if failed else "")
    )
    print(f"json -> {out_dir}")
    if failed:
        raise SystemExit(2)
