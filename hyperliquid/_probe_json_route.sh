#!/bin/bash
set -eu
echo "=== host curl public ==="
curl -sI https://data.wublock123.com/json/hyperliquid/tradexyz_session.json | head -20
echo "=== host curl listed ==="
curl -sI https://data.wublock123.com/json/listed-cex-spot-share/listed_cex_spot_share.json | head -8
echo "=== inside curl ==="
docker exec dashboard sh -c 'wget -S -O /dev/null http://127.0.0.1:3000/json/hyperliquid/tradexyz_session.json 2>&1 | head -20'
echo "=== nginx ==="
grep -R "json" /etc/nginx/sites-enabled /etc/nginx/conf.d 2>/dev/null | head -30 || true
echo "=== process ==="
docker exec dashboard sh -c 'ls -la /app/public/json/hyperliquid; pwd; ls /app/public/json | head'
