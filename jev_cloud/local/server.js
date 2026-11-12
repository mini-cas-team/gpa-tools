// Run the cloud server on plain Node, with its data in a folder, before there is a
// Cloudflare account:   node local/server.js        (or: npm start)
//
// The Worker code (src/index.js) runs unchanged; only its store differs (a folder here, R2 on
// Cloudflare).  Settings come from the environment, else from .dev.vars (KEY=value lines,
// git-ignored), the same file `wrangler dev` reads:
//   TYPESAFE_API_KEY   the TypeSafe key (needed to ask Jev)
//   ADMIN_TOKEN        turns on the admin endpoints
//   JEV_MODEL, TYPESAFE_URL, DAILY_LIMIT   as in wrangler.toml [vars]
//   JEV_DATA           the data folder (default jev_cloud/data, git-ignored)
//   HOST, PORT         default 127.0.0.1:8787

import { existsSync, readFileSync } from "node:fs";
import http from "node:http";
import { fileURLToPath } from "node:url";

import worker from "../src/index.js";
import { fsStore } from "./fs_store.js";

const HERE = fileURLToPath(new URL("..", import.meta.url));
const SETTINGS = ["TYPESAFE_API_KEY", "ADMIN_TOKEN", "JEV_MODEL", "TYPESAFE_URL", "DAILY_LIMIT", "JEV_DATA", "HOST", "PORT"];
const DEFAULTS = { JEV_MODEL: "jev-1.14.0", TYPESAFE_URL: "https://api.typesafe.ai/v1/systemone", DAILY_LIMIT: "5000",
                   JEV_DATA: `${HERE}data`, HOST: "127.0.0.1", PORT: "8787" };

export function readDevVars(path) {
  if (!existsSync(path)) return {};
  const vars = {};
  for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (m) vars[m[1]] = m[2].replace(/^(["'])(.*)\1$/, "$2");
  }
  return vars;
}

export function settings(env = process.env, devVars = readDevVars(`${HERE}.dev.vars`)) {
  const out = { ...DEFAULTS };
  for (const k of SETTINGS) {
    if (devVars[k]) out[k] = devVars[k];
    if (env[k]) out[k] = env[k];
  }
  return out;
}

/** An http.Server that hands each request to the Worker with a folder store. */
export function createServer(cfg) {
  const env = { ...cfg, STORE: fsStore(cfg.JEV_DATA) };
  return http.createServer(async (req, res) => {
    try {
      const chunks = [];
      for await (const c of req) chunks.push(c);
      const body = chunks.length ? Buffer.concat(chunks) : undefined;
      const request = new Request(`http://${req.headers.host || "localhost"}${req.url}`, {
        method: req.method, headers: req.headers, body: ["GET", "HEAD"].includes(req.method) ? undefined : body,
      });
      const resp = await worker.fetch(request, env);
      res.writeHead(resp.status, Object.fromEntries(resp.headers));
      res.end(Buffer.from(await resp.arrayBuffer()));
    } catch (err) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: String(err) }));
    }
  });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const cfg = settings();
  createServer(cfg).listen(Number(cfg.PORT), cfg.HOST, () => {
    console.log(`gpa-tools cloud server (local) on http://${cfg.HOST}:${cfg.PORT}, data in ${cfg.JEV_DATA}`);
    console.log(`TypeSafe key: ${cfg.TYPESAFE_API_KEY ? "set" : "NOT SET (Jev questions will fail)"}; `
      + `admin endpoints: ${cfg.ADMIN_TOKEN ? "on" : "off (no ADMIN_TOKEN)"}`);
  });
}
