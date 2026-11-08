/**
 * Hot-patch dashboard image: register listed-cex-spot-share if missing.
 */
const fs = require("fs");
const { execSync } = require("child_process");

const marker = "eth-tokenized-treasuries/eth_tokenized_treasuries.json";
const slug = "listed-cex-spot-share";
const entry =
  '{slug:"listed-cex-spot-share",titleZh:"已上市交易所现货周占比",titleEn:"Listed CEX spot weekly share",jsonPath:"listed-cex-spot-share/listed_cex_spot_share.json",section:"cex"}';

function grepFiles(needle) {
  return execSync(`grep -r -l -F ${JSON.stringify(needle)} /app/.next 2>/dev/null || true`, {
    encoding: "utf8",
  })
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

const files = grepFiles(marker);
const patched = [];
for (const file of files) {
  let text = fs.readFileSync(file, "utf8");
  if (text.includes(`slug:"${slug}"`) || text.includes(`"slug":"${slug}"`)) {
    console.log("already", file);
    continue;
  }
  const at = text.indexOf(marker);
  if (at < 0) continue;
  const sectionAt = text.indexOf('section:"spot"', at);
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
  const dst = file.slice(0, -3) + "-listedcex.js";
  fs.copyFileSync(file, dst);
  const name = file.split("/").pop();
  const next = dst.split("/").pop();
  for (const ref of grepFiles(name)) {
    if (ref === dst) continue;
    const body = fs.readFileSync(ref, "utf8");
    if (!body.includes(name)) continue;
    fs.writeFileSync(ref, body.split(name).join(next));
    console.log("rewritten", ref, "->", next);
  }
}
console.log("done", { n: patched.length });
