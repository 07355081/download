#!/usr/bin/env python3
"""对已有 PAGE/DASHBOARD 常量但正文未引用的 flash，把文末补上。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

TARGETS = [
    "aave-llama/flash.py",
    "lookonchain-week/flash.py",
    "protocol-revenue-monthly/flash.py",
    "crypto-treasuries/book_return_flash.py",
    "robinhood-chain/rh_base_tx_flash.py",
    "robinhood-chain/stock_compare_flash.py",
    "spot-break-liq/monitor.py",
]


def pick_var(text: str) -> str | None:
    for name in ("PAGE", "DASHBOARD_URL", "DASHBOARD", "POR_PAGE", "JSON_URL"):
        if re.search(rf"^{name}\s*=", text, re.M):
            return name
    return None


def fix(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    var = pick_var(text)
    if not var:
        print("no-var", path)
        return False
    if "{" + var + "}" in text or f"append({var})" in text:
        print("has", path.name)
        return False
    orig = text
    # aave-llama: return text, reasons
    if "aave-llama" in str(path):
        text = text.replace(
            "return text, reasons",
            f'return f"{{text}}\\n{{{var}}}", reasons',
        )
    # protocol-revenue-monthly style
    text = text.replace(
        'return f"协议月收入：{label}\\n{body}"',
        f'return f"协议月收入：{{label}}\\n{{body}}\\n{{{var}}}"',
    )
    text = text.replace(
        'return f"协议月收入异动：{label}\\n{body}"',
        f'return f"协议月收入异动：{{label}}\\n{{body}}\\n{{{var}}}"',
    )
    # generic: return f"...{body}" without page — add once per occurrence of title/body pattern
    def add_page(m: re.Match[str]) -> str:
        s = m.group(0)
        if "{" + var + "}" in s:
            return s
        # insert before closing quote
        return s[:-1] + f"\\n{{{var}}}" + s[-1]

    text = re.sub(
        r'return f"(?:(?!\\n\{' + var + r'\}).)*\{body\}"',
        add_page,
        text,
    )
    # return "\n".join([...]) patterns — append var
    if "{" + var + "}" not in text and 'return "\\n".join' in text:
        text = text.replace(
            'return "\\n".join(',
            f'return "\\n".join(list(',
        )
        # too risky — skip
        text = orig
        # book_return etc: find last return of news
        for pat in [
            r'(return \(\s*\n(?:.*\n){1,12}?\s*\))',
        ]:
            pass
        # simpler manual markers
        if "book_return" in str(path):
            text = path.read_text(encoding="utf-8")
            text = re.sub(
                r'(return \(\s*f?".*?"\s*\))',
                lambda m: m.group(1) if "{" + var + "}" in m.group(1) else m.group(1)[:-1] + f' + f"\\n{{{var}}}")',
                text,
                count=4,
                flags=re.S,
            )
        if "lookonchain" in str(path):
            text = path.read_text(encoding="utf-8") if text == orig else text
            # find build_news returns
            text = re.sub(
                r'(def build_[a-z_]+\([\s\S]*?return )(f?"[^"]+"|\'[^\']+\'|\([^)]+\))',
                lambda m: m.group(0)
                if "{" + var + "}" in m.group(0)
                else m.group(1) + f'f"{{{m.group(2).strip()}}}\\n{{{var}}}"'
                if False
                else m.group(0),
                text,
            )
            # append after body assembly
            if "{" + var + "}" not in text:
                text = text.replace(
                    "return text",
                    f'return f"{{text}}\\n{{{var}}}"',
                )
                text = text.replace(
                    "return body",
                    f'return f"{{body}}\\n{{{var}}}"',
                )

    if text == orig or "{" + var + "}" not in text:
        # last resort: after as_news( calls wrap
        print("manual-needed", path)
        return False
    path.write_text(text, encoding="utf-8")
    print("ok", path)
    return True


def main() -> None:
    for rel in TARGETS:
        fix(ROOT / rel)


if __name__ == "__main__":
    main()
