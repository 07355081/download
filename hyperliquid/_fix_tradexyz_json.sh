#!/bin/bash
set -euo pipefail
echo "=== find json ==="
find /root /app -name 'tradexyz_session.json' 2>/dev/null | head -20
echo "=== public dir ==="
ls -la /root/dashboard/public/json/hyperliquid/ 2>/dev/null || echo "no hyperliquid dir"
echo "=== docker mount ==="
docker inspect dashboard --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ro={{.RW}}{{println}}{{end}}' 2>/dev/null | head -30
echo "=== inside container ==="
docker exec dashboard sh -c 'ls -la /app/public/json/hyperliquid/tradexyz_session.json 2>/dev/null || echo missing-in-container'
