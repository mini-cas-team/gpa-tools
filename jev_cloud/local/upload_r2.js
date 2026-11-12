// Copy the local data folder into the R2 bucket, so the deployed Worker starts with
// everything the local server collected (scores, definitions, tokens, usage):
//
//   node local/upload_r2.js [data-folder] [bucket]      prints the commands
//   node local/upload_r2.js [data-folder] [bucket] | sh  runs them (needs `npx wrangler login`)
//
// Each file keeps its path as its R2 key, which is what the Worker reads.

import { readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL("..", import.meta.url));
const dir = process.argv[2] || join(here, "data");
const bucket = process.argv[3] || "gpa-tools-jev";

function* files(d) {
  for (const name of readdirSync(d)) {
    const p = join(d, name);
    if (statSync(p).isDirectory()) yield* files(p);
    else if (name.endsWith(".json")) yield p;
  }
}

const quote = (s) => `'${s.replace(/'/g, "'\\''")}'`;
for (const path of files(dir)) {
  const key = relative(dir, path).split(sep).join("/");
  console.log(`npx wrangler r2 object put ${quote(`${bucket}/${key}`)} --file=${quote(path)} --content-type=application/json --remote`);
}
