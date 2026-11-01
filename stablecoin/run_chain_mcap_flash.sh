#!/bin/bash
# 分链稳定币市值日常 / 阈值 Lark。建议排在 misc 日更稳定币刷新之后，或由 misc 内联调用。
# 例：40 6 * * * /root/data-download/stablecoin/run_chain_mcap_flash.sh
set -o pipefail
cd /root/data-download || exit 1
VENV=./.venv/bin/python
LOG=/root/data-download/stablecoin/_chain_mcap_flash.log
ts() { date '+%Y-%m-%d %H:%M:%S'; }

{
  echo "======== [$(ts)] chain-mcap-flash ========"
  $VENV stablecoin/chain_mcap_flash.py --push --history-scan
  echo "[$(ts)] done"
} >>"$LOG" 2>&1
