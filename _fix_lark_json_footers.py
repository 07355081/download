#!/usr/bin/env python3
"""把 Lark 文末 raw JSON 链接改成看板页或静态看图页。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "https://data.wublock123.com"

# 模块目录名 / 相对脚本 → 已有看板（优先）
DASHBOARD_MAP: dict[str, str] = {
    "binance-spcx-rank": f"{BASE}/dashboard/tradfi",
    "btc-etf-volume": f"{BASE}/dashboard/etf/btc",
    "btc-hashrate-puell": f"{BASE}/dashboard/mining_shutdown",
    "etf-halfyear": f"{BASE}/dashboard/etf/btc",
    "financing-early-stage": f"{BASE}/dashboard/financing-news",
    "kr-cex-volume": f"{BASE}/dashboard/exchange_volume",
    "rlusd-chain": f"{BASE}/dashboard/stablecoin-marketcap",
    "strive-btc": f"{BASE}/dashboard/treasuries",
    "spacex-perp": f"{BASE}/dashboard/tradfi",
    "spacex-liquidity": f"{BASE}/dashboard/tradfi",
    "spcx-liq": f"{BASE}/dashboard/tradfi",
    "hyperliquid/hl_quarter_rwa_flash.py": f"{BASE}/dashboard/hyperliquid",
    "hyperliquid/tradexyz_oi_flash.py": f"{BASE}/dashboard/hyperliquid",
    "pm-weekly": f"{BASE}/dashboard/prediction-markets",
    "hype-spot": f"{BASE}/dashboard/hyperliquid",
    "bnb-spot": f"{BASE}/dashboard/exchange_volume",
    "bianrensheng-spot": f"{BASE}/dashboard/meme-markets",
    "rh-meme-mcap": f"{BASE}/dashboard/robinhood-chain",
    "tokenized-treasuries": f"{BASE}/dashboard/treasuries",
    "strc-apxusd": f"{BASE}/dashboard/treasuries",
    "btc-weekly-move": f"{BASE}/dashboard/btc-indicators",
    "eth-quarter": f"{BASE}/dashboard/eth-indicators",
    "perp-dex-share": f"{BASE}/dashboard/hyperliquid",
    "perp-volume/hl_cex_share_flash.py": f"{BASE}/dashboard/hyperliquid",
    "prop-amm": f"{BASE}/dashboard/dex-fees",
    "cb-premium": f"{BASE}/dashboard/btc-indicators",
}

# 没有合适看板的：生成 chart.html
CHART_MODULES = [
    "aave-v4-spokes",
    "gua-spot",
    "lab-spot",
    "zec-spot",
    "xrp-fees",
    "edgex-oi",
    "depin-mcap",
    "falconx-vault",
    "physical-tcg",
    "collector-crypt",
    "l2-token-mcap",
]

CHART_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js"></script>
  <style>
    body {{ margin: 0; font-family: system-ui, sans-serif; background: #f8fafc; color: #0f172a; }}
    main {{ max-width: 1100px; margin: 0 auto; padding: 24px 16px 40px; }}
    h1 {{ font-size: 1.35rem; margin: 0 0 8px; }}
    .sub {{ color: #64748b; font-size: 0.9rem; margin-bottom: 16px; }}
    #chart {{ width: 100%; height: 440px; background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; }}
    .err {{ color: #b91c1c; }}
    pre {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 12px; overflow: auto; max-height: 320px; font-size: 12px; }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <p class="sub">数据文件：<a href="./{json_name}">{json_name}</a>（Lark 文末看图页）</p>
    <div id="chart"></div>
    <p class="sub" id="note"></p>
    <pre id="meta"></pre>
  </main>
  <script>
    const JSON_NAME = "{json_name}";
    function pickSeries(doc) {{
      if (Array.isArray(doc.data)) return doc.data;
      if (Array.isArray(doc.series)) return doc.series;
      if (Array.isArray(doc.months)) return doc.months;
      if (Array.isArray(doc.history)) return doc.history;
      if (doc.latest_day && Array.isArray(doc.data)) return doc.data;
      return [];
    }}
    function dayOf(row) {{
      return row.day || row.date || row.month || row.asof_day || row.time || "";
    }}
    function valueOf(row) {{
      for (const k of ["transactions","tx_count","fees_usd","volume_usd","tvl_usd","deposits_usd","oi_usd","price","value","usd","total_usd","mcap_usd","chg_24h_pct"]) {{
        if (typeof row[k] === "number" && Number.isFinite(row[k])) return row[k];
      }}
      return null;
    }}
    fetch("./" + JSON_NAME, {{ cache: "no-store" }})
      .then((r) => {{ if (!r.ok) throw new Error(r.status); return r.json(); }})
      .then((doc) => {{
        const series = pickSeries(doc).filter((r) => r && dayOf(r) && valueOf(r) != null);
        document.getElementById("meta").textContent = JSON.stringify(doc.meta || doc.latest || doc.latest_day || {{ keys: Object.keys(doc).slice(0,12) }}, null, 2);
        if (!series.length) {{
          document.getElementById("note").textContent = "未识别到可画图的时间序列，上方展示元数据；完整数据见 JSON。";
          return;
        }}
        const start = Math.max(0, 100 - (180 / Math.max(series.length, 1)) * 100);
        const chart = echarts.init(document.getElementById("chart"));
        chart.setOption({{
          animation: false,
          tooltip: {{ trigger: "axis" }},
          grid: {{ left: "3%", right: "4%", bottom: "12%", top: "8%", containLabel: true }},
          dataZoom: [{{ type: "inside", start, end: 100 }}, {{ type: "slider", start, end: 100, height: 18, bottom: 8 }}],
          xAxis: {{ type: "category", data: series.map(dayOf), boundaryGap: false }},
          yAxis: {{ type: "value", splitLine: {{ lineStyle: {{ color: "#f0f0f0" }} }} }},
          series: [{{
            type: "line", smooth: true, symbol: "none",
            lineStyle: {{ width: 2, color: "#4f46e5" }},
            areaStyle: {{ color: "rgba(79,70,229,0.10)" }},
            data: series.map(valueOf)
          }}]
        }});
        window.addEventListener("resize", () => chart.resize());
        document.getElementById("note").textContent = "共 " + series.length + " 个点；可缩放查看。";
      }})
      .catch((err) => {{
        document.getElementById("note").className = "err";
        document.getElementById("note").textContent = "加载失败: " + err;
      }});
  </script>
</body>
</html>
"""


def module_key(rel: str) -> str:
    parts = Path(rel).parts
    if parts[0] == "hyperliquid" or parts[0] == "perp-volume":
        return rel.replace("\\", "/")
    return parts[0]


def find_targets() -> list[Path]:
    out: list[Path] = []
    for path in list(ROOT.rglob("flash.py")) + list(ROOT.rglob("monitor.py")) + list(ROOT.rglob("*_flash.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "data.wublock123.com/json/" in text and text.rstrip().endswith(".json") is False:
            if re.search(r"https://data\.wublock123\.com/json/[^\"'\s]+\.json", text):
                out.append(path)
    return sorted(set(out))


def strip_json_from_footer(text: str) -> str:
    # \n{PAGE}\n{JSON_URL} / \n{DASHBOARD_URL}\n{JSON_URL} / \n\n{PAGE}\n{JSON_PAGE}
    text = re.sub(r"\\n\{PAGE\}\\n\{JSON_URL\}", r"\\n{PAGE}", text)
    text = re.sub(r"\\n\{DASHBOARD_URL\}\\n\{JSON_URL\}", r"\\n{DASHBOARD_URL}", text)
    text = re.sub(r"\\n\\n\{PAGE\}\\n\{JSON_PAGE\}", r"\\n\\n{PAGE}", text)
    text = re.sub(r"\\n\\n\{ETF_PAGE\}\\n\{PAGE\}", r"\\n\\n{ETF_PAGE}", text)
    text = re.sub(r"\\n\{PAGE\}\\n\{CG_PAGE\}", r"\\n{PAGE}\\n{CG_PAGE}", text)  # keep cg
    # cb-premium still has PAGE as json - handled separately
    return text


def set_const(text: str, name: str, value: str) -> str:
    pat = re.compile(rf'^({name}\s*=\s*)(["\']).*\2', re.M)
    if pat.search(text):
        return pat.sub(rf'\1"{value}"', text, count=1)
    return text


def ensure_page_const(text: str, page: str) -> str:
    if re.search(r"^(PAGE|DASHBOARD_URL|NEWS_URL)\s*=", text, re.M):
        for name in ("PAGE", "DASHBOARD_URL", "NEWS_URL"):
            if re.search(rf"^{name}\s*=", text, re.M):
                text = set_const(text, name, page)
                break
    else:
        # insert after JSON_URL if present
        m = re.search(r'^(JSON_URL\s*=\s*".*"\s*)$', text, re.M)
        if m:
            text = text[: m.end()] + f'\nPAGE = "{page}"' + text[m.end() :]
        else:
            text = f'PAGE = "{page}"\n' + text
    # point JSON_URL / JSON_PAGE / NEWS_URL used alone in footer to PAGE
    if "JSON_URL" in text:
        text = set_const(text, "JSON_URL", page)
    if "JSON_PAGE" in text:
        text = set_const(text, "JSON_PAGE", page)
    if "NEWS_URL" in text:
        text = set_const(text, "NEWS_URL", page)
    return text


def replace_footer_json_only(text: str) -> str:
    # if footer still uses only JSON_URL, leave as is (JSON_URL now equals PAGE)
    text = re.sub(r"\\n\{JSON_URL\}", r"\\n{PAGE}", text)
    text = re.sub(r"\\n\{JSON_PAGE\}", r"\\n{PAGE}", text)
    text = re.sub(r"\\n\{NEWS_URL\}", r"\\n{PAGE}", text)
    # if PAGE missing but we rewrote to {PAGE}, ensure PAGE exists
    if "{PAGE}" in text and not re.search(r"^PAGE\s*=", text, re.M):
        if re.search(r"^DASHBOARD_URL\s*=", text, re.M):
            text = text.replace("{PAGE}", "{DASHBOARD_URL}")
        elif re.search(r"^NEWS_URL\s*=", text, re.M):
            text = text.replace("{PAGE}", "{NEWS_URL}")
    return text


def json_name_for_module(mod: str) -> str | None:
    folder = ROOT / mod
    if not folder.is_dir():
        return None
    # prefer known
    candidates = list((folder / "output" / "json").glob("*.json")) if (folder / "output" / "json").is_dir() else []
    if not candidates:
        candidates = list(folder.glob("output/json/*.json"))
    if candidates:
        # pick non-tmp, prefer * without lark state
        cands = [p for p in candidates if "lark" not in p.name and not p.name.endswith(".tmp")]
        if cands:
            return sorted(cands, key=lambda p: p.stat().st_mtime, reverse=True)[0].name
    # fallback from common patterns
    return None


def write_chart(mod: str) -> str | None:
    jname = json_name_for_module(mod)
    if not jname:
        # hardcode some
        defaults = {
            "aave-v4-spokes": "aave_v4_spokes.json",
            "gua-spot": "gua_spot.json",
            "lab-spot": "lab_spot.json",
            "zec-spot": "zec_spot.json",
            "xrp-fees": "xrp_fees.json",
            "edgex-oi": "edgex_oi.json",
            "depin-mcap": "depin_mcap.json",
            "falconx-vault": "falconx_vault.json",
            "physical-tcg": "physical_tcg.json",
            "collector-crypt": "collector_crypt_monthly.json",
            "l2-token-mcap": "l2_token_mcap.json",
        }
        jname = defaults.get(mod)
    if not jname:
        return None
    title = f"{mod} 数据"
    html = CHART_HTML.format(title=title, json_name=jname)
    path = ROOT / mod / "chart.html"
    path.write_text(html, encoding="utf-8")
    return f"{BASE}/json/{mod}/chart.html"


def main() -> int:
    changed: list[str] = []
    charted: list[str] = []
    for path in find_targets():
        rel = path.relative_to(ROOT).as_posix()
        key = module_key(rel)
        text0 = path.read_text(encoding="utf-8")
        text = text0

        # special: btc-etf-volume PAGE is json — use ETF_PAGE only
        if key == "btc-etf-volume":
            text = set_const(text, "PAGE", f"{BASE}/dashboard/etf/btc")
            text = strip_json_from_footer(text)
            text = text.replace(r"\n\n{ETF_PAGE}\n{PAGE}", r"\n\n{ETF_PAGE}")
            text = text.replace(r"\n\n{ETF_PAGE}\n{PAGE}", r"\n\n{ETF_PAGE}")

        page = DASHBOARD_MAP.get(key)
        if page is None and key in CHART_MODULES:
            page = write_chart(key)
            if page:
                charted.append(key)

        if page:
            text = ensure_page_const(text, page)
            # if DASHBOARD_URL used as generic homepage, upgrade
            if re.search(r'DASHBOARD_URL\s*=\s*"https://data\.wublock123\.com/dashboard"', text):
                text = set_const(text, "DASHBOARD_URL", page)
            text = strip_json_from_footer(text)
            text = replace_footer_json_only(text)
            # cb-premium: keep CG_PAGE
            if key == "cb-premium":
                text = set_const(text, "PAGE", page)

        else:
            # still strip dual footers if page-like const already points to dashboard
            text = strip_json_from_footer(text)
            # if JSON_URL is the only footer and PAGE missing, try module map by folder
            folder = Path(rel).parts[0]
            if folder in DASHBOARD_MAP:
                page = DASHBOARD_MAP[folder]
                text = ensure_page_const(text, page)
                text = replace_footer_json_only(text)
            elif folder in CHART_MODULES:
                page = write_chart(folder)
                if page:
                    charted.append(folder)
                    text = ensure_page_const(text, page)
                    text = replace_footer_json_only(text)

        # physical-tcg monthly shares chart with physical-tcg
        if key == "physical-tcg" and "monthly_flash" in rel:
            page = f"{BASE}/json/physical-tcg/chart.html"
            text = ensure_page_const(text, page)
            text = strip_json_from_footer(text)
            text = replace_footer_json_only(text)

        if text != text0:
            path.write_text(text, encoding="utf-8")
            changed.append(rel)

    # second pass: any remaining json footers with homepage-only dashboard
    for path in find_targets():
        rel = path.relative_to(ROOT).as_posix()
        text0 = path.read_text(encoding="utf-8")
        text = text0
        if re.search(r'data\.wublock123\.com/json/[^"\']+\.json', text) and re.search(
            r"\\n\{JSON_URL\}|\\n\{NEWS_URL\}|\\n\{JSON_PAGE\}", text
        ):
            folder = Path(rel).parts[0]
            if folder in CHART_MODULES or folder in DASHBOARD_MAP:
                continue
            # create chart for leftover modules that still append JSON
            page = write_chart(folder)
            if not page:
                continue
            charted.append(folder)
            text = ensure_page_const(text, page)
            text = strip_json_from_footer(text)
            text = replace_footer_json_only(text)
            if text != text0:
                path.write_text(text, encoding="utf-8")
                changed.append(rel)

    print("changed", len(changed))
    for c in sorted(set(changed)):
        print(" ", c)
    print("charts", sorted(set(charted)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
