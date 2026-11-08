#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25 root@47.74.6.207 'docker exec dashboard node -e "
const fs=require(\"fs\");
const files=[
\"/app/.next/static/chunks/app/dashboard/lark-feeds/[slug]/page-c2be57a421dd5795.js\",
\"/app/.next/static/chunks/app/dashboard/lark-feeds/[slug]/page-a1chartonly20261023.js\",
\"/app/.next/server/app/dashboard/lark-feeds/[slug]/page.js\"
];
for (const f of files) {
  const s=fs.readFileSync(f,\"utf8\");
  const i=s.indexOf(\"defi-tvl\");
  console.log(\"FILE\", f, \"len\", s.length, \"idx\", i);
  if (i>=0) console.log(s.slice(Math.max(0,i-180), i+220));
  console.log(\"---\");
}
"'
