const fs = require("fs");
const { execSync } = require("child_process");
const oldName = "page-2ba04f7908730339.js";
const newName = "page-2ba04f7908730339-solana.js";
const hits = execSync("grep -r -l -F " + oldName + " /app/.next", { encoding: "utf8" })
  .split("\n")
  .map((s) => s.trim())
  .filter(Boolean);
console.log("refs", hits.join("\n"));
const srcDir = "/app/.next/static/chunks/app/dashboard/lark-feeds/[slug]";
const src = srcDir + "/" + oldName;
const dst = srcDir + "/" + newName;
fs.copyFileSync(src, dst);
for (const file of hits) {
  const text = fs.readFileSync(file, "utf8");
  if (!text.includes(oldName)) continue;
  fs.writeFileSync(file, text.split(oldName).join(newName));
  console.log("rewritten", file);
}
console.log("copied", dst);
