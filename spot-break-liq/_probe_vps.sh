#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20)
"${SSH[@]}" "$HOST" 'ls -la /root/data-download/spot-break-liq/ 2>/dev/null || true
ls -la /root/data-download/spot-break-liq/output/ 2>/dev/null || true
ls -la /root/data-download/spot-break-liq/output/json/ 2>/dev/null || true
ls -la /root/dashboard/public/json/spot-break-liq/ 2>/dev/null || true
find /root/data-download/spot-break-liq -name "*.json" 2>/dev/null | head -40
crontab -l 2>/dev/null | grep -i spot-break || true
grep -n spot-break /root/data-download/run_monitor.sh /root/data-download/*/run_monitor.sh 2>/dev/null | head -20 || true'
