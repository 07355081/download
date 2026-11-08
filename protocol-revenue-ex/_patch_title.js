const fs = require("fs");
const { execSync } = require("child_process");

const from = '"crypto_earnings"===String(e.name||"")?';
const to =
  '"protocol_revenue_ex"===String(e.name||"")?{titleZh:"近一年协议收入（不含稳定币发行商与公链）",titleEn:"Trailing 1y revenue ex stablecoin issuers and chains",bars:l}:' +
  from;

const files = execSync(
  'grep -r -l -F ' + JSON.stringify(from) + ' /app/.next/static/chunks/app/dashboard/lark-feeds || true',
  { encoding: "utf8" },
)
  .split("\n")
  .map((s) => s.trim())
  .filter(Boolean);

let n = 0;
for (const file of files) {
  const text = fs.readFileSync(file, "utf8");
  if (text.includes('"protocol_revenue_ex"===String(e.name||"")')) {
    console.log("already", file);
    continue;
  }
  if (!text.includes(from)) continue;
  fs.writeFileSync(file, text.split(from).join(to));
  n += 1;
  console.log("patched", file);
}
console.log("done", n);
