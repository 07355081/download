#!/usr/bin/env python3
"""审计所有会推 Lark 的脚本：文末链接是否自有看板/看图。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DASH = ROOT.parent / "data-dashboard"
OWN = ("data.wublock123.com", "data.wublockchain.xyz")
THIRD_HOST = re.compile(
    r"https?://(?!(?:[\w.-]*\.)?(?:wublock123\.com|wublockchain\.xyz)/)[\w.-]+",
    re.I,
)
SKIP_PARTS = {"_fix", "_scan", "_note", "_tmp", "_deploy", "_patch", "_audit", "__pycache__", ".venv"}

# 消息文末常见变量
CONST = re.compile(
    r"^(?P<var>PAGE|PAGE_URL|JSON_URL|DASHBOARD_URL|DASHBOARD|CHART_URL|NEWS_URL|CG_PAGE|SOURCE_URL)\s*=\s*[\"'](?P<url>[^\"']+)[\"']",
    re.M,
)


def is_lark_script(path: Path, text: str) -> bool:
    if path.suffix != ".py":
        return False
    name = path.name
    if not (
        "flash" in name
        or name == "monitor.py"
        or name.endswith("_flash.py")
        or "lark" in name.lower()
    ):
        return False
    return any(
        k in text
        for k in ("as_news", "LARK_ALERT", "send_lark", "sendLark", "webhook", "LARK_WEBHOOK")
    )


def classify(url: str) -> str:
    u = url.lower().strip()
    if not u.startswith("http"):
        return "not_url"
    if any(o in u for o in OWN):
        if u.endswith(".json") or re.search(r"/json/[^?]+\.json$", u):
            return "own_json"
        if "chart.html" in u:
            return "own_chart"
        if "/dashboard/" in u:
            return "own_dashboard"
        return "own_other"
    return "third"


def footer_urls_in_text(text: str) -> list[tuple[str, str]]:
    """Return (var_or_lit, url) used as message footers."""
    assigns = {m.group("var"): m.group("url") for m in CONST.finditer(text)}
    used_vars: set[str] = set()
    for var in assigns:
        # used in f-string / append
        if re.search(rf"\{{{var}\}}", text) or re.search(rf"append\(\s*{var}\s*\)", text):
            used_vars.add(var)
    out: list[tuple[str, str]] = []
    for var in sorted(used_vars):
        out.append((var, assigns[var]))
    # hardcoded https in build/news near end patterns
    for m in re.finditer(
        r"""(?:\\n|"\n'|'\n|f["'][^"']{0,40})(https://[^"'\s]+)""",
        text,
    ):
        url = m.group(1).rstrip("\\")
        # skip API endpoints
        if any(
            x in url
            for x in (
                "api.",
                "openapi",
                "/bapi/",
                "coinglass.com/api",
                "llama.fi",
                "coingecko.com/api",
            )
        ):
            continue
        if url.endswith(".json") and "wublock" not in url:
            continue
        # only if looks like footer (in string with newline before or after as_news builders)
        out.append(("literal", url))
    # dedupe by url
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for k, u in out:
        if u in seen:
            continue
        seen.add(u)
        uniq.append((k, u))
    return uniq


def main() -> None:
    # nav hrefs
    nav_text = (DASH / "src" / "config" / "dashboard-nav.ts").read_text(encoding="utf-8")
    nav_hrefs = set(re.findall(r'href:\s*"([^"]+)"', nav_text))

    rows: list[dict] = []
    for path in sorted(ROOT.rglob("*.py")):
        if any(s in path.parts for s in SKIP_PARTS) or path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not is_lark_script(path, text):
            continue
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        urls = footer_urls_in_text(text)
        kinds = [classify(u) for _, u in urls]
        if not urls:
            status = "NO_LINK"
        elif any(k == "third" for k in kinds):
            status = "THIRD"
        elif any(k == "own_json" for k in kinds):
            status = "OWN_JSON"
        elif any(k == "own_chart" for k in kinds) and not any(k == "own_dashboard" for k in kinds):
            status = "CHART_ONLY"  # 有图但未必在目录
        elif any(k in ("own_dashboard", "own_other", "own_chart") for k in kinds):
            status = "OK"
        else:
            status = "CHECK"
        # 吴说获悉 + third
        has_wushuo = "吴说获悉" in text or "据吴说" in text
        in_nav = False
        dash_paths = []
        for _, u in urls:
            if "/dashboard/" in u and any(o in u for o in OWN):
                path_part = "/" + u.split(".com/", 1)[-1].split("?", 1)[0]
                if not path_part.startswith("/"):
                    path_part = "/dashboard/" + path_part.split("dashboard/", 1)[-1]
                dash_paths.append(path_part)
                if path_part in nav_hrefs or any(path_part.rstrip("/") == h.rstrip("/") for h in nav_hrefs):
                    in_nav = True
        if status == "OK" and dash_paths and not in_nav:
            status = "DASH_NOT_IN_NAV"
        if status == "CHART_ONLY":
            # chart never in nav
            pass
        rows.append(
            {
                "rel": rel,
                "status": status,
                "urls": urls,
                "has_wushuo": has_wushuo,
                "in_nav": in_nav,
            }
        )

    # also scan data-dashboard lark ts
    for path in sorted((DASH / "src").rglob("*lark*.ts")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "sendLark" not in text and "Lark" not in text:
            continue
        rel = "data-dashboard/" + str(path.relative_to(DASH)).replace("\\", "/")
        urls = re.findall(r"https://[^\"'`\s]+", text)
        urls += re.findall(r"`\$\{[^}]+\}(/dashboard/[^`]+)`", text)
        # getAlertPublicBaseUrl patterns
        link_paths = re.findall(r"getAlertPublicBaseUrl\(\)[^;]{0,80}?(/dashboard/[^\"'`\s]+)", text)
        status = "OK"
        if not urls and not link_paths and "textZh" in text:
            # may build link elsewhere
            if "/dashboard/" not in text and "chart.html" not in text:
                status = "NO_LINK"
        rows.append(
            {
                "rel": rel,
                "status": status,
                "urls": [("ts", u) for u in urls[:5]] + [("path", p) for p in link_paths],
                "has_wushuo": "吴说" in text,
                "in_nav": False,
            }
        )

    by = {}
    for r in rows:
        by.setdefault(r["status"], []).append(r)
    for st in ("THIRD", "OWN_JSON", "NO_LINK", "CHART_ONLY", "DASH_NOT_IN_NAV", "CHECK", "OK"):
        items = by.get(st, [])
        print(f"\n=== {st} ({len(items)}) ===")
        for r in items:
            url_s = "; ".join(f"{a}={b}" for a, b in r["urls"][:3]) or "-"
            print(f"{r['rel']}: {url_s}")


if __name__ == "__main__":
    main()
