#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25 root@47.74.6.207 'docker exec dashboard node - <<"NODE"
const fs = require("fs");
const {execSync} = require("child_process");
const files = execSync("grep -r -l -F defi-tvl /app/.next", {encoding:"utf8"})
  .split("\n").map(s => s.trim()).filter(Boolean);
const needle = "{slug:\"defi-tvl\",titleZh:\"全球/分链 DeFi TVL\",titleEn:\"DeFi TVL\",jsonPath:\"defi-tvl/defi_tvl.json\",section:\"defi\"},";
const insert = needle + "{slug:\"solana-quarter\",titleZh:\"Solana 季度应用收入与 TVL\",titleEn:\"Solana quarter revenue and TVL\",jsonPath:\"solana-quarter/solana_quarter.json\",section:\"chain\"},";
for (const file of files) {
  let text = fs.readFileSync(file, "utf8");
  if (text.includes("solana-quarter")) {
    console.log("already", file);
    continue;
  }
  if (!text.includes(needle)) {
    console.log("no-needle", file);
    continue;
  }
  text = text.split(needle).join(insert);
  fs.writeFileSync(file, text);
  console.log("patched", file);
}
NODE
ls -la /root/dashboard/public/json/solana-quarter/solana_quarter.json 2>/dev/null || echo NO_JSON'
