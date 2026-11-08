const fs = require("fs");
const { execSync } = require("child_process");
const file = execSync("grep -r -l -F nameEn /app/.next/static/chunks/app/dashboard/lark-feeds", { encoding: "utf8" })
  .split("\n")
  .map((s) => s.trim())
  .filter((s) => s.includes("[slug]"))[0];
const text = fs.readFileSync(file, "utf8");
console.log("file", file);
for (const key of ["亿美元", "valueFormatter", "吴说", "labelEn", "nameEn"]) {
  const i = text.indexOf(key);
  console.log("\n==", key, i);
  if (i >= 0) console.log(text.slice(Math.max(0, i - 160), i + 220));
}
