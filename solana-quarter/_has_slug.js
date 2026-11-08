const { execSync } = require("child_process");
const fs = require("fs");
const files = execSync("grep -r -l -F solana-quarter /app/.next || true", { encoding: "utf8" })
  .split("\n")
  .map((s) => s.trim())
  .filter(Boolean);
console.log(files.join("\n") || "MISSING");
const chunk = execSync("grep -r -l -F month-end /app/.next/static/chunks/app/dashboard/lark-feeds", {
  encoding: "utf8",
})
  .split("\n")
  .map((s) => s.trim())
  .filter((s) => s.includes("[slug]"));
for (const file of chunk) {
  const text = fs.readFileSync(file, "utf8");
  const key = ".label";
  let index = 0;
  let count = 0;
  while ((index = text.indexOf(key, index)) >= 0 && count < 4) {
    console.log("LABEL", file.split("/").pop(), text.slice(index - 80, index + 120));
    index += key.length;
    count += 1;
  }
}
