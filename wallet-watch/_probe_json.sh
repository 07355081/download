#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=20 "$HOST" bash -s <<'REMOTE'
set -euo pipefail
echo '=== output ==='
ls -la /root/data-download/wallet-watch/output 2>/dev/null | head -40 || echo none
echo '=== public ==='
ls -la /root/dashboard/public/json/wallet-watch 2>/dev/null || echo none
echo '=== json files ==='
find /root/data-download/wallet-watch -maxdepth 3 -name '*.json' 2>/dev/null | head -40
echo '=== state head ==='
python3 - <<'PY'
from pathlib import Path
import json
out=Path('/root/data-download/wallet-watch/output')
for p in sorted(out.glob('*.json'))[:12]:
    try:
        doc=json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        print(p.name, 'ERR', e); continue
    if isinstance(doc, dict):
        print(p.name, 'keys', list(doc.keys())[:12], 'size', p.stat().st_size)
    else:
        print(p.name, type(doc), 'size', p.stat().st_size)
PY
REMOTE
