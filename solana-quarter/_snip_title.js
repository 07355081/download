const fs = require("fs");
const { execSync } = require("child_process");
const files = execSync("grep -r -l -F month-end /app/.next/static/chunks/app/dashboard/lark-feeds", {
  encoding: "utf8",
})
  .split("\n")
  .map((s) => s.trim())
  .filter(Boolean);
for (const file of files) {
  const text = fs.readFileSync(file, "utf8");
  const key = "month-end";
  let index = 0;
  let count = 0;
  while ((index = text.indexOf(key, index)) >= 0 && count < 2) {
    console.log(file);
    console.log(text.slice(Math.max(0, index - 220), index + 180));
    console.log("---");
    index += key.length;
    count += 1;
  }
}
