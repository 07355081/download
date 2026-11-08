# -*- coding: utf-8 -*-
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# aave-llama
p = ROOT / "aave-llama" / "flash.py"
t = p.read_text(encoding="utf-8")
t2 = t.replace("return text, reasons", 'return f"{text}\\n{PAGE}", reasons', 1)
p.write_text(t2, encoding="utf-8")
print("aave-llama", t != t2)

# protocol-revenue-monthly
p = ROOT / "protocol-revenue-monthly" / "flash.py"
t = p.read_text(encoding="utf-8")
t2 = t.replace(
    'return f"协议月收入：{label}\\n{body}"',
    'return f"协议月收入：{label}\\n{body}\\n{PAGE}"',
)
t2 = t2.replace(
    'return f"协议月收入异动：{label}\\n{body}"',
    'return f"协议月收入异动：{label}\\n{body}\\n{PAGE}"',
)
p.write_text(t2, encoding="utf-8")
print("protocol-revenue-monthly", t != t2)

# lookonchain-week: append PAGE to build returns that end with body
p = ROOT / "lookonchain-week" / "flash.py"
t = p.read_text(encoding="utf-8")
if "{PAGE}" not in t:
    t2 = t.replace('return f"{title}\\n{body}"', 'return f"{title}\\n{body}\\n{PAGE}"')
    if t2 == t:
        # try return body alone in news builders
        import re

        def add(m):
            block = m.group(0)
            if "{PAGE}" in block:
                return block
            return block.replace("return body", 'return f"{body}\\n{PAGE}"').replace(
                "return text", 'return f"{text}\\n{PAGE}"'
            )

        t2 = re.sub(r"def build_[\s\S]{0,2000}?(?=\ndef )", add, t)
    p.write_text(t2, encoding="utf-8")
    print("lookonchain", t != t2, "{PAGE}" in t2)
else:
    print("lookonchain already")

# book_return
p = ROOT / "crypto-treasuries" / "book_return_flash.py"
t = p.read_text(encoding="utf-8")
if "{PAGE}" not in t:
    t2 = t.replace("return text", 'return f"{text}\\n{PAGE}"')
    t2 = t2.replace("return body", 'return f"{body}\\n{PAGE}"')
    # also f-string returns wrapping paragraphs
    if "{PAGE}" not in t2:
        import re

        t2 = re.sub(
            r'(return \(\s*\n\s*f?".*?")\s*\n(\s*\))',
            r'\1\n        f"\\n{PAGE}"\n\2',
            t,
            count=3,
            flags=re.S,
        )
    p.write_text(t2, encoding="utf-8")
    print("book_return", "{PAGE}" in Path(p).read_text(encoding="utf-8"))
else:
    print("book_return already")

# rh_base_tx / stock_compare
for rel in ["robinhood-chain/rh_base_tx_flash.py", "robinhood-chain/stock_compare_flash.py"]:
    p = ROOT / rel
    t = p.read_text(encoding="utf-8")
    if "{PAGE}" in t or "{DASHBOARD_URL}" in t:
        print(rel, "already")
        continue
    t2 = t.replace('return f"{title}\\n{body}"', 'return f"{title}\\n{body}\\n{PAGE}"')
    t2 = t2.replace("return text", 'return f"{text}\\n{PAGE}"')
    t2 = t2.replace("return body", 'return f"{body}\\n{PAGE}"')
    p.write_text(t2, encoding="utf-8")
    print(rel, "{PAGE}" in t2)

# wallet-watch monitor
p = ROOT / "wallet-watch" / "monitor.py"
t = p.read_text(encoding="utf-8")
t2 = t.replace(
    "lines.append(explorer)\n    lines.append(PAGE)",
    'lines.append(f"交易哈希 {tx}")\n    lines.append(PAGE)',
)
old = 'return "\\n".join([title, "", body, "", f"Arkham 实体：{ARKHAM_USG1}", explorer])'
new = 'return "\\n".join([title, "", body, "", f"交易哈希 {tx}", PAGE])'
if old in t2:
    t2 = t2.replace(old, new)
p.write_text(t2, encoding="utf-8")
print("wallet-watch", t != t2)

# yzi / pump
for name in ["monitor_yzi_ena.py", "monitor_pump_sol.py"]:
    p = ROOT / "wallet-watch" / name
    if not p.is_file():
        continue
    t = p.read_text(encoding="utf-8")
    # replace trailing explorer URLs in joins with PAGE only if PAGE present
    if "PAGE =" in t and "{PAGE}" not in t and "PAGE)" not in t:
        import re

        # append PAGE to return "\n".join([...])
        t2 = t
        if 'return "\\n".join(' in t2 and "PAGE" not in t2.split('return "\\n".join(')[-1][:200]:
            t2 = t2.replace(
                'return "\\n".join([',
                'return "\\n".join([PAGE, ',
                1,
            )
        # also strip arkham/etherscan as sole footers — keep hash
        t2 = re.sub(
            r'f"https://(?:etherscan|arkm|solscan)[^"]+"',
            lambda m: f'f"交易哈希 {{tx}}"' if "tx" in t2[max(0, t2.find(m.group(0)) - 200) : t2.find(m.group(0))] else m.group(0),
            t2,
        )
        p.write_text(t2, encoding="utf-8")
        print(name, "patched")
    else:
        print(name, "skip")
