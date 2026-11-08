from pathlib import Path

path = Path("/root/data-download/run_misc_daily.sh")
text = path.read_text(encoding="utf-8")
changed = False

def insert_after(anchor: str, block: str) -> None:
    global text, changed
    if block.strip().splitlines()[0].strip() in text:
        return
    idx = text.find(anchor)
    if idx < 0:
        raise SystemExit(f"missing anchor: {anchor}")
    line_end = text.find("\n", idx)
    text = text[: line_end] + "\n" + block + text[line_end:]
    changed = True

if "hl_revenue_flash.py" not in text:
    insert_after(
        "hyperliquid/run_all.py -- --no-execute",
        '  echo "[$(ts)] hyperliquid revenue flash → Lark..."\n'
        "  $VENV hyperliquid/hl_revenue_flash.py --push \\\n"
        '    || echo "[$(ts)]   [WARN] hl revenue flash 失败,继续"',
    )
if "hype_flash.py" not in text:
    insert_after(
        "token-burns/download.py",
        '  echo "[$(ts)] hype assistance fund flash → Lark..."\n'
        "  $VENV token-burns/hype_flash.py --push \\\n"
        '    || echo "[$(ts)]   [WARN] hype flash 失败,继续"\n'
        '  echo "[$(ts)] us gov btc holdings flash → Lark..."\n'
        "  $VENV crypto-treasuries/us_gov_btc_flash.py --push \\\n"
        '    || echo "[$(ts)]   [WARN] us gov btc flash 失败,继续"',
    )
if changed:
    path.write_text(text, encoding="utf-8")
    print("hooked")
else:
    print("already hooked")
