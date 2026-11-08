from pathlib import Path

p = Path("/root/data-download/run_misc_daily.sh")
text = p.read_text(encoding="utf-8")
if "activity_flash.py" in text:
    print("already hooked")
else:
    anchor = "robinhood-chain/fees_flash.py --push --history-scan \\\n"
    idx = text.find("fees_flash.py --push --history-scan")
    if idx < 0:
        raise SystemExit("anchor not found")
    # insert after the fees flash failure line that follows the anchor
    line_end = text.find("\n", idx)
    next_end = text.find("\n", line_end + 1)
    if next_end < 0:
        raise SystemExit("no following line")
    block = (
        "\n    echo \"[$(ts)] robinhood-chain activity wow flash → Lark...\""
        "\n    $VENV robinhood-chain/activity_flash.py --push \\"
        "\n      || echo \"[$(ts)]   [WARN] robinhood-chain activity flash 失败,继续\""
    )
    text = text[: next_end] + block + text[next_end:]
    p.write_text(text, encoding="utf-8")
    print("hooked")
