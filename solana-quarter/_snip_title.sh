#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25 root@47.74.6.207 'docker exec dashboard node -e "
const fs=require(\"fs\");
const files=[
\"/app/.next/static/chunks/app/dashboard/lark-feeds/[slug]/page-c2be57a421dd5795.js\",
\"/app/.next/static/chunks/app/dashboard/lark-feeds/[slug]/page-a1chartonly20261023.js\"
];
for (const f of files) {
  const s=fs.readFileSync(f,\"utf8\");
  for (const key of [\"month-end\",\"月末\",\"label\"]) {
    let i=0, n=0;
    while ((i=s.indexOf(key,i))>=0 && n<3) {
      console.log(f.split(\"/\").pop(), key, s.slice(Math.max(0,i-120), i+160).replace(/\\n/g,\" \"));
      console.log(\"---\");
      i+=key.length; n++;
    }
  }
}
"'
