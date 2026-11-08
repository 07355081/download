#!/usr/bin/env bash
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" bash -s <<'EOF'
echo '=== env key names ==='
grep -E '^(LLAMA|DEFILLAMA|PRO_API|TOKEN_TERMINAL)' /root/dashboard/.env.live /root/data-download/.env 2>/dev/null | sed 's/=.*/=***/' || echo none
echo '=== try free rwa again ==='
curl -sS -o /tmp/rwa_cur.txt -w '%{http_code}' 'https://api.llama.fi/rwa/current' || true
echo
head -c 120 /tmp/rwa_cur.txt; echo
curl -sS -o /tmp/rwa_bd.txt -w '%{http_code}' 'https://api.llama.fi/rwa/chart/chain-breakdown' || true
echo
head -c 120 /tmp/rwa_bd.txt; echo
EOF
