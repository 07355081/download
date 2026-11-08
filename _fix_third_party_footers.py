#!/usr/bin/env python3
"""把 Lark 文末第三方链接改成自有 chart.html，并生成看图页。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "https://data.wublock123.com"

# mod -> (json_name_for_chart_fetch, const_name)
# json_name: path relative to chart.html location under public/json/<mod>/
FIXES: dict[str, dict[str, str]] = {
    "paradex": {
        "json": "paradex.json",
        "fetch": "./paradex.json",
        "const": "PAGE",
        "title": "Paradex 锁仓与费用",
    },
    "fragment": {
        "json": "fragment_fees.json",
        "fetch": "./fragment_fees.json",
        "const": "PAGE",
        "title": "Fragment 协议费用",
    },
    "near-intents": {
        "json": "near_intents.json",
        "fetch": "./near_intents.json",
        "const": "DASHBOARD_URL",
        "title": "NEAR Intents",
    },
    "spark-savings": {
        "json": "spark_savings.json",
        "fetch": "./spark_savings.json",
        "const": "PAGE",
        "title": "Spark 储蓄存款",
    },
    "cb-stock-dex": {
        "json": "cb_stock_dex.json",
        "fetch": "./cb_stock_dex.json",
        "const": "PAGE",
        "title": "Coinbase Base 股票代币 DEX",
    },
    "cb-base-stock-dex": {
        "json": "daily.json",
        "fetch": "./daily.json",
        "const": "PAGE",
        "title": "Coinbase 代币化股票 DEX",
    },
    "chain-fees-dist": {
        "json": "chain_fees_dist_latest.json",
        "fetch": "../chain_fees_dist_latest.json",
        "const": "DASHBOARD_URL",
        "title": "公链 24h 手续费分布",
    },
    "chain-app-revenue": {
        "json": "chain_app_revenue_latest.json",
        "fetch": "../chain_app_revenue_latest.json",
        "const": "DASHBOARD_URL",
        "title": "各链 24h 应用收入",
    },
    "gsr-core3": {
        "json": "gsr_core3.json",
        "fetch": "../gsr_core3.json",
        "const": "",
        "title": "GSR Core3 组合权重",
    },
}

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
    <p class="sub">数据文件：<a href="{fetch}">{json_name}</a>（Lark 文末看图页）</p>
    <div id="chart"></div>
    <p class="sub" id="note"></p>
    <pre id="meta"></pre>
  </main>
  <script>
    const JSON_URL = "{fetch}";
    function pickSeries(doc) {{
      if (Array.isArray(doc.data)) return doc.data;
      if (Array.isArray(doc.series)) return doc.series;
      if (Array.isArray(doc.months)) return doc.months;
      if (Array.isArray(doc.history)) return doc.history;
      if (Array.isArray(doc.weeks)) return doc.weeks;
      if (Array.isArray(doc.days)) return doc.days;
      if (doc.latest && typeof doc.latest === "object") {{
        if (Array.isArray(doc.rows)) return doc.rows;
      }}
      return [];
    }}
    function dayOf(row) {{
      return row.day || row.date || row.month || row.asof_day || row.time || row.week || "";
    }}
    function valueOf(row) {{
      for (const k of ["transactions","tx_count","fees_usd","fees","volume_usd","volume","tvl_usd","tvl","deposits_usd","oi_usd","price","value","usd","total_usd","mcap_usd","chg_24h_pct","btc","weight_btc"]) {{
        if (typeof row[k] === "number" && Number.isFinite(row[k])) return row[k];
      }}
      if (row.weights && typeof row.weights.btc === "number") return row.weights.btc;
      return null;
    }}
    fetch(JSON_URL, {{ cache: "no-store" }})
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


def set_const(text: str, name: str, value: str) -> str:
    pat = re.compile(rf'^({name}\s*=\s*)(["\']).*\2', re.M)
    if not pat.search(text):
        raise SystemExit(f"missing const {name}")
    return pat.sub(rf'\1"{value}"', text, count=1)


def main() -> None:
    changed: list[str] = []
    for mod, meta in FIXES.items():
        folder = ROOT / mod
        folder.mkdir(parents=True, exist_ok=True)
        page = f"{BASE}/json/{mod}/chart.html"
        chart = CHART_HTML.format(
            title=meta["title"],
            fetch=meta["fetch"],
            json_name=meta["json"],
        )
        (folder / "chart.html").write_text(chart, encoding="utf-8")
        print("chart", mod)

        const = meta["const"]
        if not const:
            continue
        flash = folder / "flash.py"
        if not flash.is_file():
            print("no-flash", mod)
            continue
        text = flash.read_text(encoding="utf-8")
        text2 = set_const(text, const, page)
        if text2 != text:
            flash.write_text(text2, encoding="utf-8")
            changed.append(str(flash.relative_to(ROOT)))
            print("flash", mod, "->", page)
        else:
            print("unchanged-flash", mod)

    print("changed", len(changed))
    for c in changed:
        print(" ", c)


if __name__ == "__main__":
    main()
