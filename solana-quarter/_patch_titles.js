const fs = require("fs");
const { execSync } = require("child_process");

const files = execSync("grep -r -l -F lark-feeds /app/.next/server/app/dashboard/lark-feeds /app/.next/static/chunks/app/dashboard/lark-feeds", {
  encoding: "utf8",
})
  .split("\n")
  .map((s) => s.trim())
  .filter(Boolean);

const pairs = [
  [
    'titleZh:"索拉纳季度应用收入与锁仓",titleEn:"Solana quarterly application revenue and locked value"',
    'titleZh:"Solana 季度应用收入与 TVL",titleEn:"Solana quarterly application revenue and TVL"',
  ],
  [
    'let i=String(o.label||o.slug||"").trim();i&&t.push({name:i,',
    'let i=String(o.label||o.slug||"").trim();let iEn=String(o.labelEn||i).trim();i&&t.push({name:i,nameEn:iEn,',
  ],
  [
    'let n=t.map(e=>e.name).join(o?"、":", "),s=t.some(e=>e.points.some(e=>e.day.length>=10))?n:o?"".concat(n," 月末"):"".concat(n," month-end")',
    't=t.map(e=>({...e,name:o?e.name:(e.nameEn||e.name)}));let n=t.map(e=>e.name).join(o?"、":", "),s=t.some(e=>e.points.some(e=>e.day.length>=10||/Q/.test(e.day)))?n:o?"".concat(n," 月末"):"".concat(n," month-end")',
  ],
  [
    'className:"text-xs text-slate-500",children:e.name',
    'className:"text-xs text-slate-500",children:o?e.name:(e.nameEn||e.name)',
  ],
];

for (const file of files) {
  let text = fs.readFileSync(file, "utf8");
  let changed = false;
  for (const [from, to] of pairs) {
    const count = text.split(from).length - 1;
    if (!count) continue;
    text = text.split(from).join(to);
    changed = true;
    console.log("replaced", count, file.split("/").pop());
  }
  if (changed) fs.writeFileSync(file, text);
}
