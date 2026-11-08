const fs = require("fs");
const slugMan = "/app/.next/server/app/dashboard/lark-feeds/[slug]/page_client-reference-manifest.js";
const hubMan = "/app/.next/server/app/dashboard/lark-feeds/page_client-reference-manifest.js";
for (const file of [slugMan, hubMan]) {
  const text = fs.readFileSync(file, "utf8");
  console.log("\nFILE", file, "len", text.length);
  for (const token of ["4108d635dbe7ba19", "33d922acfb95cfc6", "hl-net-revenue"]) {
    let i = 0;
    let n = 0;
    while ((i = text.indexOf(token, i)) >= 0 && n < 3) {
      console.log(token, text.slice(Math.max(0, i - 80), i + token.length + 40).replace(/\n/g, " "));
      i += token.length;
      n += 1;
    }
  }
}
const chunk = "/app/.next/static/chunks/app/dashboard/lark-feeds/[slug]/page-4108d635dbe7ba19-hlrev.js";
const body = fs.readFileSync(chunk, "utf8");
console.log("chunk has slug", body.includes("hl-net-revenue"), "len", body.length);
console.log("old chunk exists", fs.existsSync(chunk.replace("-hlrev", "")));
