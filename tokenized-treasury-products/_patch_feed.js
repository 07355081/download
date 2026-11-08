const fs = require("fs");
const { execSync } = require("child_process");

const marker = "non-usd-stables/non_usd_stables.json";
const slug = "tokenized-treasury-products";
const entry =
  '{slug:"tokenized-treasury-products",titleZh:"代币化国债产品市值",titleEn:"Tokenized treasury products",jsonPath:"tokenized-treasury-products/tokenized_treasury_products.json",section:"other"}';

const files = execSync(`grep -r -l -F ${marker} /app/.next || true`, { encoding: "utf8" })
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
  if (at < 0) continue;
  const sectionAt = text.indexOf('section:"other"', at);
  if (sectionAt < 0) {
    console.log("no-section", file);
    continue;
  }
  const end = text.indexOf("}", sectionAt);
  if (end < 0) continue;
  text = text.slice(0, end + 1) + "," + entry + text.slice(end + 1);
  fs.writeFileSync(file, text);
  patched.push(file);
  console.log("patched", file);
}

for (const file of patched) {
  if (!file.includes("/static/chunks/") || !file.endsWith(".js")) continue;
  const base = file.slice(0, -3);
  const dst = `${base}-ttprod.js`;
  fs.copyFileSync(file, dst);
  const name = file.split("/").pop();
  const next = dst.split("/").pop();
  const refs = execSync(`grep -r -l -F ${name} /app/.next || true`, { encoding: "utf8" })
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
