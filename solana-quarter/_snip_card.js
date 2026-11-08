const fs = require("fs");
const { execSync } = require("child_process");
const file = execSync("grep -r -l -F 月末环比 /app/.next/static/chunks/app/dashboard/lark-feeds", { encoding: "utf8" })
  .split("\n")
  .map((s) => s.trim())
  .filter((s) => s.includes("[slug]"))[0];
const text = fs.readFileSync(file, "utf8");
const key = "月末环比";
const index = text.indexOf(key);
console.log(file);
console.log(text.slice(index - 500, index + 200));
