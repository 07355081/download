const fs = require("fs");
const { execSync } = require("child_process");

const marker = "hl-net-revenue/hl_net_revenue.json";
const slug = "hl-hip3-markets";
const entry =
  '{slug:"hl-hip3-markets",titleZh:"Hyperliquid HIP-3 可交易市场数",titleEn:"Hyperliquid HIP-3 market count",jsonPath:"hl-hip3-markets/hl_hip3_markets.json",section:"chain"}';

const files = execSync(`grep -r -l -F ${marker} /app/.next`, { encoding: "utf8" })
  .split("\n")
  .map((line) => line.trim())
  .filter(Boolean);

const patched = [];
for (const file of files) {
  let text = fs.readFileSync(file, "utf8");
  if (text.includes(slug)) {
    console.log("already", file);
    continue;
  }
  const at = text.indexOf(marker);
  if (at < 0) {
    console.log("no-marker", file);
    continue;
  }
  const sectionAt = text.indexOf('section:"chain"', at);
  if (sectionAt < 0) {
    console.log("no-section", file);
    continue;
  }
  const end = text.indexOf("}", sectionAt);
  if (end < 0) {
    console.log("no-end", file);
    continue;
  }
  text = text.slice(0, end + 1) + "," + entry + text.slice(end + 1);
  fs.writeFileSync(file, text);
  patched.push(file);
  console.log("patched", file);
}

for (const file of patched) {
  if (!file.includes("/static/chunks/") || !file.endsWith(".js")) continue;
  const base = file.slice(0, -3);
  const dst = `${base}-hip3m.js`;
  fs.copyFileSync(file, dst);
  const name = file.split("/").pop();
  const next = dst.split("/").pop();
  const refs = execSync(`grep -r -l -F ${name} /app/.next`, { encoding: "utf8" })
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  for (const ref of refs) {
    if (ref === dst) continue;
    const body = fs.readFileSync(ref, "utf8");
    if (!body.includes(name)) continue;
    fs.writeFileSync(ref, body.split(name).join(next));
    console.log("rewritten", ref, "->", next);
  }
}

// Chart title for count series (best-effort string patch).
const titleNeedle = 'slug==="okx-events"?zh?"公开系列数":"Public series count"';
const titleInsert =
  titleNeedle +
  ':slug==="hl-hip3-markets"?zh?"HIP-3 市场数":"HIP-3 markets"';
const moneyNeedle = 'slug!=="wallet-watch"&&slug!=="okx-events"';
const moneyInsert = 'slug!=="wallet-watch"&&slug!=="okx-events"&&slug!=="hl-hip3-markets"';
const titleFiles = execSync('grep -r -l -F \'slug==="okx-events"\' /app/.next/static /app/.next/server || true', {
  encoding: "utf8",
})
  .split("\n")
  .map((line) => line.trim())
  .filter(Boolean);
for (const file of titleFiles) {
  let text = fs.readFileSync(file, "utf8");
  let changed = false;
  if (text.includes(titleNeedle) && !text.includes("hl-hip3-markets")) {
    text = text.split(titleNeedle).join(titleInsert);
    changed = true;
  }
  if (text.includes(moneyNeedle) && !text.includes('slug!=="hl-hip3-markets"')) {
    text = text.split(moneyNeedle).join(moneyInsert);
    changed = true;
  }
  if (changed) {
    fs.writeFileSync(file, text);
    console.log("title-patched", file);
  }
}
