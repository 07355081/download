#!/bin/bash
set -euo pipefail
ssh -o BatchMode=yes -o ConnectTimeout=20 root@47.74.6.207 'bash -s' << 'REMOTE'
set +e
date
echo "===== RERUN ====="
tail -n 8 /root/data-download/_manual_rerun.log
echo "===== TRADFI ====="
tail -n 12 /root/data-download/tradfi/_daily.log
echo "===== DF ====="
df -h / | tail -n 1
echo "===== PROCS ====="
ps -eo etime,cmd | grep -E "tradfi/download|run_tradfi|run_coinglass|run_misc" | grep -v grep
REMOTE
