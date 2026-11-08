#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25 root@47.74.6.207 "docker ps --filter name=dashboard --format '{{.Status}}'; curl -s -o /dev/null -w 'local:%{http_code}\n' http://127.0.0.1:3000/dashboard/lark-feeds/solana-quarter; curl -s http://127.0.0.1:3000/json/solana-quarter/solana_quarter.json | head -c 400; echo"
