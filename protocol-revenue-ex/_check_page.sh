#!/bin/bash
set -eu
html=$(curl -s https://data.wublock123.com/dashboard/lark-feeds/protocol-revenue-ex)
echo "$html" | grep -o 'static/chunks/app/dashboard/lark-feeds/[^"]*' | head -20
echo "--- slug in those ---"
echo "$html" | grep -o 'static/chunks/[^"]*lark-feeds[^"]*' | while read -r p; do
  code=$(curl -s "https://data.wublock123.com/$p" | grep -c 'protocol-revenue-ex' || true)
  echo "$code $p"
done
echo "--- container files ---"
docker exec dashboard sh -c 'grep -l protocol-revenue-ex /app/.next/static/chunks/app/dashboard/lark-feeds/*.js /app/.next/server/app/dashboard/lark-feeds/*.js 2>/dev/null | head'
