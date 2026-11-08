#!/usr/bin/env bash
set -euo pipefail
curl -s https://data.wublock123.com/dashboard/lark-feeds/solana-quarter | grep -o 'page-2ba04f7908730339[^"]*' | head -3
echo
curl -s -o /dev/null -w "chunk:%{http_code}\n" "https://data.wublock123.com/_next/static/chunks/app/dashboard/lark-feeds/%5Bslug%5D/page-2ba04f7908730339-solana.js"
echo "-- titles in chunk --"
curl -s "https://data.wublock123.com/_next/static/chunks/app/dashboard/lark-feeds/%5Bslug%5D/page-2ba04f7908730339-solana.js" | grep -o 'Solana quarterly application revenue and TVL' | head -1
curl -s "https://data.wublock123.com/_next/static/chunks/app/dashboard/lark-feeds/%5Bslug%5D/page-2ba04f7908730339-solana.js" | grep -c '索拉纳' || true
curl -s "https://data.wublock123.com/json/solana-quarter/solana_quarter.json" | grep -F '"label"' | head -8
