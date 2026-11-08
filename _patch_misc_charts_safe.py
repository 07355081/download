#!/usr/bin/env python3
"""安全地在 run_misc 各模块 json 镜像成功后追加 chart.html 拷贝。"""
from __future__ import annotations

from pathlib import Path

SH = Path(__file__).resolve().parent / "run_misc_daily.sh"

# (unique anchor line that already exists, chart insert after that line's statement block)
# We insert AFTER a complete single-line or continued cp that ends with || echo
INSERTS: list[tuple[str, str, str]] = [
    # mod, anchor_substring (must be unique), indent
    (
        "zec-spot",
        '|| echo "[$(ts)]   [WARN] zec-spot json 镜像失败,继续"',
        "    ",
    ),
    (
        "lab-spot",
        '|| echo "[$(ts)]   [WARN] lab-spot json 镜像失败,继续"',
        "    ",
    ),
    (
        "gua-spot",
        '|| echo "[$(ts)]   [WARN] gua-spot json 镜像失败,继续"',
        "    ",
    ),
    (
        "aave-v4-spokes",
        '|| echo "[$(ts)]   [WARN] aave-v4-spokes json 镜像失败,继续"',
        "    ",
    ),
    (
        "l2-token-mcap",
        '|| echo "[$(ts)]   [WARN] l2-token-mcap flash 失败,继续"',
        "    ",
    ),
    (
        "depin-mcap",
        '|| echo "[$(ts)]   [WARN] depin-mcap json 镜像失败,继续"',
        "    ",
    ),
    (
        "falconx-vault",
        '|| echo "[$(ts)]   [WARN] falconx-vault json 镜像失败,继续"',
        "    ",
    ),
    (
        "edgex-oi",
        '|| echo "[$(ts)]   [WARN] edgex-oi json 镜像失败,继续"',
        "    ",
    ),
    (
        "xrp-fees",
        '|| echo "[$(ts)]   [WARN] xrp-fees json 镜像失败,继续"',
        "    ",
    ),
    (
        "collector-crypt",
        '2>/dev/null || echo "[$(ts)]   [WARN] collector-crypt json 镜像失败,继续"',
        "    ",
    ),
    (
        "physical-tcg",
        '2>/dev/null || echo "[$(ts)]   [WARN] physical-tcg monthly json 镜像失败,继续"',
        "  ",
    ),
    (
        "paradex",
        '|| echo "[$(ts)]   [WARN] paradex json 镜像失败,继续"',
        "    ",
    ),
    (
        "spark-savings",
        '|| echo "[$(ts)]   [WARN] spark-savings json 镜像失败,继续"',
        "    ",
    ),
    (
        "near-intents",
        '|| echo "[$(ts)]   [WARN] near-intents json 镜像失败,继续"',
        "    ",
    ),
    (
        "fragment",
        '|| echo "[$(ts)]   [WARN] fragment json 镜像失败,继续"',
        "    ",
    ),
    (
        "cb-stock-dex",
        '|| echo "[$(ts)]   [WARN] cb-stock-dex flash 失败,继续"',
        "    ",
    ),
    (
        "cb-base-stock-dex",
        '|| echo "[$(ts)]   [WARN] cb-base-stock-dex flash 失败,继续"',
        "    ",
    ),
    (
        "chain-fees-dist",
        '|| echo "[$(ts)]   [WARN] chain-fees-dist flash 失败,继续"',
        "    ",
    ),
    (
        "chain-app-revenue",
        '|| echo "[$(ts)]   [WARN] chain-app-revenue flash 失败,继续"',
        "    ",
    ),
    (
        "gsr-core3",
        '$VENV gsr-core3/download.py || echo "[$(ts)]   [WARN] gsr-core3 失败,继续"',
        "  ",
    ),
]


def chart_block(mod: str, indent: str) -> str:
    extra = ""
    if mod == "cb-stock-dex":
        extra = (
            f"{indent}mkdir -p /root/dashboard/public/json/{mod}\n"
            f"{indent}cp -a {mod}/output/json/cb_stock_dex.json \\\n"
            f"{indent}  /root/dashboard/public/json/{mod}/cb_stock_dex.json \\\n"
            f'{indent}  2>/dev/null || echo "[$(ts)]   [WARN] {mod} json 镜像失败,继续"\n'
        )
    elif mod == "cb-base-stock-dex":
        extra = (
            f"{indent}mkdir -p /root/dashboard/public/json/{mod}\n"
            f"{indent}cp -a {mod}/output/json/daily.json \\\n"
            f"{indent}  /root/dashboard/public/json/{mod}/daily.json \\\n"
            f'{indent}  2>/dev/null || echo "[$(ts)]   [WARN] {mod} json 镜像失败,继续"\n'
        )
    elif mod in ("chain-fees-dist", "chain-app-revenue", "gsr-core3", "l2-token-mcap"):
        extra = f"{indent}mkdir -p /root/dashboard/public/json/{mod}\n"
    return (
        extra
        + f"{indent}cp -a {mod}/chart.html \\\n"
        + f"{indent}  /root/dashboard/public/json/{mod}/chart.html \\\n"
        + f'{indent}  || echo "[$(ts)]   [WARN] {mod} chart 镜像失败,继续"\n'
    )


def main() -> None:
    text = SH.read_text(encoding="utf-8")
    # VPS bak already has first batch charts — skip those if present
    n = 0
    for mod, anchor, indent in INSERTS:
        if f"{mod}/chart.html" in text and mod not in ("cb-stock-dex", "cb-base-stock-dex", "chain-fees-dist", "chain-app-revenue", "gsr-core3", "paradex", "fragment", "near-intents", "spark-savings"):
            # already in bak for first batch
            if f"cp -a {mod}/chart.html" in text:
                print("already", mod)
                continue
        if f"cp -a {mod}/chart.html" in text:
            print("already", mod)
            continue
        if anchor not in text:
            print("missing-anchor", mod, anchor[:60])
            continue
        idx = text.index(anchor) + len(anchor)
        # skip trailing newline once
        if idx < len(text) and text[idx] == "\n":
            idx += 1
        block = chart_block(mod, indent)
        text = text[:idx] + block + text[idx:]
        n += 1
        print("ok", mod)
    SH.write_text(text, encoding="utf-8")
    print("added", n)
    # sanity
    assert "cp -a paradex/chart.html" in text
    assert 'echo "[$(ts)]      cp -a' not in text
    print("sanity_ok")


if __name__ == "__main__":
    main()
