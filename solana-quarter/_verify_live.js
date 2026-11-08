const fs = require("fs");
const { execSync } = require("child_process");
const files = execSync("grep -r -l -F solana-quarter /app/.next", { encoding: "utf8" })
  .split("\n")
  .map((s) => s.trim())
  .filter(Boolean);
for (const file of files) {
  const text = fs.readFileSync(file, "utf8");
  const i = text.indexOf("solana-quarter");
  console.log(file.split("/").pop(), text.slice(i, i + 180).replace(/\n/g, " "));
}
