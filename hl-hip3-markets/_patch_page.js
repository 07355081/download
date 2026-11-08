const fs = require("fs");
const { execSync } = require("child_process");

// Best-effort: ensure series_count cards recognize unique_count label text if old bundle.
const files = execSync(
  'grep -r -l -F "公开系列" /app/.next/static/chunks/app/dashboard/lark-feeds /app/.next/server/app/dashboard/lark-feeds || true',
  { encoding: "utf8" },
)
  .split("\n")
  .map((line) => line.trim())
  .filter(Boolean);

for (const file of files) {
  let text = fs.readFileSync(file, "utf8");
  if (text.includes("HIP-3 市场") && text.includes("unique_count")) {
    console.log("already-cards", file);
    continue;
  }
  // Replace the compact okxEventCards series label branch if present in minified form.
  const old =
    'typeof r.series_count!="number"?[]:[{label:t?"公开系列":"Series",value:String(r.series_count)}]';
  const neu =
    'typeof r.series_count!="number"?[]:((()=>{const i=typeof r.unique_count=="number";const a=[{label:i?t?"HIP-3 市场":"HIP-3 markets":t?"公开系列":"Series",value:String(r.series_count)}];if(typeof r.day=="string")a.unshift({label:t?"数据日":"As of",value:r.day});if(i)a.push({label:t?"去重标的":"Unique underlyings",value:String(r.unique_count)});const b=Array.isArray(r.top_builders)?r.top_builders:Array.isArray(r.builders)?r.builders:[];for(const e of b.slice(0,4)){if(!e||typeof e!="object")continue;const n=String(e.name||""),c=typeof e.markets=="number"?e.markets:null;if(n&&c!=null)a.push({label:n,value:String(c)})}return a})())';
  if (text.includes(old)) {
    text = text.split(old).join(neu);
    fs.writeFileSync(file, text);
    console.log("cards-patched", file);
  } else {
    console.log("cards-skip", file);
  }
}
