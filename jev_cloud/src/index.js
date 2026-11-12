// gpa-tools cloud server: the only place the TypeSafe key lives.  Runs as a Cloudflare Worker
// (data in R2) or, for testing, on plain Node (local/server.js, data in a folder); the code is
// the same, only env.STORE differs.
//
// Client endpoints (Authorization: Bearer <client token>):
//   GET  /v1/info                  {model, limits, client}             proves the token works
//   POST /v1/courses/score         {category, definition, courses:[[code, title], ...]}
//                                  -> {model, scores:[0-1, ...], usage:{cached, asked, input_tokens, output_tokens}}
//   POST /v1/letters/rank          {letter} -> {model, choice, confidence}    (the letter is never stored)
//   GET  /v1/definitions           {definitions: {CAT: definition, ...}}   every shared definition
//   GET  /v1/definitions/<CAT>     {category, definition}  or 404
//   POST /v1/definitions/<CAT>     {definition} -> {category, definition, created}
//                                  create-only: the first client's definition becomes the shared
//                                  one and is never replaced; a later POST gets the existing one
//                                  back with created: false
// Enrollment (no token: the laptop has none yet; see "Getting a client token" below):
//   POST /v1/requests              {email, hostname, token_hash} -> {id, status: "pending"}
//   GET  /v1/requests/<token_hash> {id, status: pending|approved|rejected, name}
// Admin endpoints (Authorization: Bearer <ADMIN_TOKEN>):
//   GET    /v1/admin/requests      {requests: [{id, email, hostname, status, requested_at, ...}]}
//   POST   /v1/admin/requests/<id>/approve  {daily_limit?} -> the token <email>:<hostname> is live
//   POST   /v1/admin/requests/<id>/reject
//   POST   /v1/admin/tokens        {name, daily_limit?} -> {name, token}   (the token is shown once)
//   DELETE /v1/admin/tokens/<name> revokes it
//   POST   /v1/admin/scores        {category, definition, model?, scores:{"CODE|title": 0-1}}   seeds scores
//
// Files (keys in R2, paths under the local data folder):
//   scores/<model>/<CAT>/<sha256(definition)[:16]>.json  {model, category, definition, scores:{"CODE|title": 0-1}}
//                                  the same scores map as a client's <work>/jev/cache_<cat>.json
//   definitions/<CAT>.json         {category, definition, created_at, created_by}
//   tokens.json                    {<sha256(token)>: {name, daily_limit, revoked, created_at}}
//   requests.json                  {<sha256(token)>: {id, email, hostname, status, requested_at, decided_at}}
//   usage/<YYYY-MM-DD>/<sha256(token)>.json   {name, questions}
//
// Course scores are shared by every client with the same model and definition: a course one
// client paid for is free for the others.  Only Jev's answers (and the admin seed) are written;
// a client never writes a score.  Request bodies are never logged.
//
// Getting a client token: the laptop makes its own token at install, keeps it, and sends only
// its sha256 with the user's email and hostname.  An admin (a person) approves the request,
// which puts that hash into tokens.json; the laptop then sees "approved" and starts using
// its token.  The token itself never reaches the server or any list, and only the laptop
// holding it can ask about its request (it is looked up by the hash, not by email).

import { courseBody, courseKey, JevError, LIMITS, letterBody, parseChoice, parseScores } from "./jev.js";
import { r2Store, readJson, updateJson } from "./store.js";

const MAX_SEED = 2000;
const RETRY_STATUSES = [429, 529];
const CATEGORY_RE = /^[A-Za-z0-9][A-Za-z0-9 _&+-]*$/;
const NAME_RE = /^[A-Za-z0-9._@:+-]+$/;
const EMAIL_RE = /^[^\s@:/]+@[^\s@:/]+\.[^\s@:/]+$/;
const HOST_RE = /^[A-Za-z0-9._-]{1,64}$/;
const HASH_RE = /^[0-9a-f]{64}$/;
const MAX_PENDING = 50;

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export default {
  async fetch(request, env) {
    try {
      const store = env.STORE || r2Store(env.BUCKET);
      return await route(request, env, store);
    } catch (err) {
      if (err instanceof HttpError) return json({ error: err.message }, err.status);
      if (err instanceof JevError) return json({ error: `TypeSafe: ${err.message}` }, 502);
      console.error("internal error", err && err.stack ? err.stack : String(err));
      return json({ error: "internal error" }, 500);
    }
  },
};

async function route(request, env, store) {
  const path = new URL(request.url).pathname.replace(/\/+$/, "");
  const method = request.method;
  const ctx = { env, store };
  let m;
  if (path === "/v1/info" && method === "GET") return info(await client(request, ctx), ctx);
  if (path === "/v1/courses/score" && method === "POST") return scoreCourses(await client(request, ctx), await body(request), ctx);
  if (path === "/v1/letters/rank" && method === "POST") return rankLetter(await client(request, ctx), await body(request), ctx);
  if (path === "/v1/definitions" && method === "GET") {
    await client(request, ctx);
    return listDefinitions(ctx);
  }
  if ((m = path.match(/^\/v1\/definitions\/([^/]+)$/))) {
    const category = normCategory(decodeURIComponent(m[1]));
    if (method === "GET") {
      await client(request, ctx);
      return getDefinition(category, ctx);
    }
    if (method === "POST") return createDefinition(await client(request, ctx), category, await body(request), ctx);
  }
  if (path === "/v1/requests" && method === "POST") return createRequest(await body(request), ctx);
  if ((m = path.match(/^\/v1\/requests\/([0-9a-f]{64})$/)) && method === "GET") return requestStatus(m[1], ctx);
  if (path === "/v1/admin/requests" && method === "GET") {
    await admin(request, ctx);
    return listRequests(ctx);
  }
  if ((m = path.match(/^\/v1\/admin\/requests\/([0-9a-f]+)\/(approve|reject)$/)) && method === "POST") {
    await admin(request, ctx);
    return decideRequest(m[1], m[2], await body(request), ctx);
  }
  if (path === "/v1/admin/tokens" && method === "POST") {
    await admin(request, ctx);
    return createToken(await body(request), ctx);
  }
  if ((m = path.match(/^\/v1\/admin\/tokens\/([^/]+)$/)) && method === "DELETE") {
    await admin(request, ctx);
    return revokeToken(decodeURIComponent(m[1]), ctx);
  }
  if (path === "/v1/admin/scores" && method === "POST") {
    await admin(request, ctx);
    return seedScores(await body(request), ctx);
  }
  throw new HttpError(404, `no such endpoint: ${method} ${path || "/"}`);
}

// ---- auth --------------------------------------------------------------------

function bearer(request) {
  const header = request.headers.get("Authorization") || "";
  const token = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  if (!token) throw new HttpError(401, "missing bearer token");
  return token;
}

/** The client token {hash, name, daily_limit}; 401 unless it exists and is not revoked. */
async function client(request, { store }) {
  const hash = await sha256(bearer(request));
  const { data } = await readJson(store, "tokens.json");
  const tok = data && data[hash];
  if (!tok || tok.revoked) throw new HttpError(401, "unknown or revoked token");
  return { hash, ...tok };
}

async function admin(request, { env }) {
  if (!env.ADMIN_TOKEN) throw new HttpError(403, "admin endpoints are off: ADMIN_TOKEN is not set");
  // compare digests, not the strings, so the time taken says nothing about the token
  if ((await sha256(bearer(request))) !== (await sha256(env.ADMIN_TOKEN))) {
    throw new HttpError(401, "not the admin token");
  }
}

// ---- client endpoints --------------------------------------------------------

function info(tok, { env }) {
  return json({ model: model(env), limits: LIMITS, client: tok.name });
}

async function scoresKey(mdl, cat, definition) {
  return `scores/${encodeURIComponent(mdl)}/${encodeURIComponent(cat)}/${(await sha256(definition)).slice(0, 16)}.json`;
}

async function scoreCourses(tok, req, ctx) {
  const category = text(req.category, "category", LIMITS.max_category_chars);
  const definition = text(req.definition, "definition", LIMITS.max_definition_chars);
  const courses = req.courses;
  if (!Array.isArray(courses) || courses.length === 0) throw new HttpError(422, "courses must be a non-empty list");
  if (courses.length > LIMITS.max_courses) {
    throw new HttpError(413, `at most ${LIMITS.max_courses} courses per request, got ${courses.length}`);
  }
  for (const c of courses) {
    if (!Array.isArray(c) || c.length !== 2 || typeof c[0] !== "string" || typeof c[1] !== "string"
        || c[0].length + c[1].length > LIMITS.max_course_chars) {
      throw new HttpError(422, "each course must be [code, title] (strings)");
    }
  }
  const mdl = model(ctx.env);
  const cat = normCategory(category);
  const key = await scoresKey(mdl, cat, definition);
  const keys = courses.map(([code, title]) => courseKey(code, title));

  const { data } = await readJson(ctx.store, key);
  const known = new Map(Object.entries((data && data.scores) || {}));
  const missing = new Map(); // course key -> [code, title], once each
  courses.forEach((c, i) => { if (!known.has(keys[i]) && !missing.has(keys[i])) missing.set(keys[i], c); });

  const usage = { cached: courses.length - missing.size, asked: missing.size, input_tokens: 0, output_tokens: 0 };
  if (missing.size) {
    await spend(tok, missing.size, ctx);
    const jevBody = courseBody(mdl, category, definition, [...missing.values()]);
    const reply = await typesafe(jevBody, ctx.env);
    const fresh = {};
    parseScores(jevBody, reply).forEach((s, i) => { fresh[[...missing.keys()][i]] = s; });
    const saved = await updateJson(ctx.store, key, (file) => {
      const now = file || { model: mdl, category: cat, definition, scores: {} };
      return { ...now, scores: { ...fresh, ...now.scores } }; // a score already saved wins
    });
    for (const [k, s] of Object.entries(saved.scores)) known.set(k, s);
    usage.input_tokens = (reply.usage && reply.usage.input_tokens) || 0;
    usage.output_tokens = (reply.usage && reply.usage.output_tokens) || 0;
  }
  console.log(`score ${tok.name} ${cat}: ${usage.cached} cached, ${usage.asked} asked, ${usage.input_tokens} tokens`);
  return json({ model: mdl, scores: keys.map((k) => known.get(k)), usage });
}

async function rankLetter(tok, req, ctx) {
  const letter = text(req.letter, "letter", LIMITS.max_letter_chars);
  await spend(tok, 1, ctx);
  const reply = await typesafe(letterBody(model(ctx.env), letter), ctx.env);
  const [choice, confidence] = parseChoice(reply);
  console.log(`letter ${tok.name}: ${letter.length} chars`); // never the text
  return json({ model: model(ctx.env), choice, confidence });
}

const definitionKey = (category) => `definitions/${encodeURIComponent(category)}.json`;

async function getDefinition(category, { store }) {
  const { data } = await readJson(store, definitionKey(category));
  if (!data) throw new HttpError(404, `no definition for ${category}`);
  return json({ category: data.category, definition: data.definition });
}

async function listDefinitions({ store }) {
  const definitions = {};
  for (const key of await store.list("definitions/")) {
    const { data } = await readJson(store, key);
    if (data && data.category && data.definition) definitions[data.category] = data.definition;
  }
  return json({ definitions });
}

// The first definition of a category is the shared one: written only if the file does not
// exist yet (etag null), so two clients drafting the same new category at once end up with
// one text -- the loser gets the winner's back (created: false) and uses it.
async function createDefinition(tok, category, req, { store }) {
  const definition = text(req.definition, "definition", LIMITS.max_definition_chars).trim();
  const created_at = new Date().toISOString();
  const file = { category, definition, created_at, created_by: tok.name };
  if (await store.put(definitionKey(category), JSON.stringify(file, null, 1), { etag: null })) {
    console.log(`definition ${category} created by ${tok.name}`);
    return json({ category, definition, created: true }, 201);
  }
  const { data } = await readJson(store, definitionKey(category));
  return json({ category, definition: data.definition, created: false });
}

// ---- admin endpoints ---------------------------------------------------------

async function createToken(req, { store }) {
  const name = text(req.name, "name", 64);
  if (!NAME_RE.test(name)) throw new HttpError(422, "name: letters, digits, . _ @ : + - only");
  const limit = req.daily_limit == null ? null : Number(req.daily_limit);
  if (limit != null && !(Number.isInteger(limit) && limit >= 0)) throw new HttpError(422, "daily_limit: a whole number");
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  const token = "gpat_" + [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  const hash = await sha256(token);
  const created_at = new Date().toISOString();
  await updateJson(store, "tokens.json", (tokens) => {
    const all = tokens || {};
    if (Object.values(all).some((t) => t.name === name && !t.revoked)) {
      throw new HttpError(409, `a token named ${name} already exists; revoke it first`);
    }
    return { ...all, [hash]: { name, daily_limit: limit, revoked: false, created_at } };
  });
  return json({ name, token, daily_limit: limit }, 201);
}

async function revokeToken(name, { store }) {
  let found = false;
  await updateJson(store, "tokens.json", (tokens) => {
    const all = { ...(tokens || {}) };
    found = false;
    for (const [hash, t] of Object.entries(all)) {
      if (t.name === name && !t.revoked) {
        all[hash] = { ...t, revoked: true };
        found = true;
      }
    }
    return found ? all : undefined;
  });
  if (!found) throw new HttpError(404, `no active token named ${name}`);
  return json({ name, revoked: true });
}

// ---- enrollment --------------------------------------------------------------

// One request per laptop (email + hostname): a new one from the same laptop (a reinstall
// that lost its pending token) replaces its pending one.  At most MAX_PENDING wait at once,
// so an open endpoint cannot fill the store.
async function createRequest(req, { store }) {
  const email = text(req.email, "email", 254).trim().toLowerCase();
  if (!EMAIL_RE.test(email)) throw new HttpError(422, "email: not an email address");
  const hostname = text(req.hostname, "hostname", 64).trim();
  if (!HOST_RE.test(hostname)) throw new HttpError(422, "hostname: letters, digits, . _ - only");
  const hash = String(req.token_hash || "");
  if (!HASH_RE.test(hash)) throw new HttpError(422, "token_hash: the sha256 of the token, 64 hex digits");
  const { data: tokens } = await readJson(store, "tokens.json");
  if (tokens && tokens[hash]) throw new HttpError(409, "that token is already in use");
  const id = hash.slice(0, 12);
  await updateJson(store, "requests.json", (requests) => {
    const all = { ...(requests || {}) };
    if (all[hash]) return undefined; // sent twice: nothing to change
    for (const [h, r] of Object.entries(all)) {
      if (r.status === "pending" && r.email === email && r.hostname === hostname) delete all[h];
    }
    if (Object.values(all).filter((r) => r.status === "pending").length >= MAX_PENDING) {
      throw new HttpError(429, "too many requests are waiting for approval; try again later");
    }
    all[hash] = { id, email, hostname, status: "pending", requested_at: new Date().toISOString() };
    return all;
  });
  console.log(`token request ${id} from ${email} on ${hostname}`);
  return json({ id, status: "pending" }, 201);
}

async function requestStatus(hash, { store }) {
  const { data } = await readJson(store, "requests.json");
  const r = data && data[hash];
  if (!r) throw new HttpError(404, "no such request");
  return json({ id: r.id, status: r.status, name: tokenName(r) });
}

const tokenName = (r) => `${r.email}:${r.hostname}`;

async function listRequests({ store }) {
  const { data } = await readJson(store, "requests.json");
  const requests = Object.values(data || {}).sort((a, b) => a.requested_at.localeCompare(b.requested_at));
  return json({ requests });
}

// Approving puts the request's hash into tokens.json as <email>:<hostname>; an older live token
// of that name (the laptop's previous install) is revoked, so one laptop has one token.
async function decideRequest(id, action, req, { store }) {
  const limit = req.daily_limit == null ? null : Number(req.daily_limit);
  if (limit != null && !(Number.isInteger(limit) && limit >= 0)) throw new HttpError(422, "daily_limit: a whole number");
  const { data } = await readJson(store, "requests.json");
  const matches = Object.entries(data || {}).filter(([, r]) => r.id === id);
  if (matches.length !== 1) throw new HttpError(404, `no request ${id}`);
  const [hash, r] = matches[0];
  if (r.status !== "pending") throw new HttpError(409, `request ${id} is already ${r.status}`);
  const name = tokenName(r);
  let replaced = false;
  if (action === "approve") {
    await updateJson(store, "tokens.json", (tokens) => {
      const all = { ...(tokens || {}) };
      replaced = false;
      for (const [h, t] of Object.entries(all)) {
        if (t.name === name && !t.revoked) { all[h] = { ...t, revoked: true }; replaced = true; }
      }
      all[hash] = { name, daily_limit: limit, revoked: false, created_at: new Date().toISOString() };
      return all;
    });
  }
  const status = action === "approve" ? "approved" : "rejected";
  await updateJson(store, "requests.json", (requests) => {
    const all = { ...(requests || {}) };
    if (all[hash]) all[hash] = { ...all[hash], status, decided_at: new Date().toISOString() };
    return all;
  });
  console.log(`token request ${id} ${status}: ${name}`);
  return json({ id, status, name, replaced });
}

async function seedScores(req, { env, store }) {
  const category = text(req.category, "category", LIMITS.max_category_chars);
  const definition = text(req.definition, "definition", LIMITS.max_definition_chars);
  const scores = req.scores;
  if (!scores || typeof scores !== "object" || Array.isArray(scores)) throw new HttpError(422, "scores must be {\"CODE|title\": score}");
  const entries = Object.entries(scores);
  if (entries.length > MAX_SEED) throw new HttpError(413, `at most ${MAX_SEED} scores per request`);
  const mdl = req.model ? text(req.model, "model", 64) : model(env);
  const cat = normCategory(category);
  const add = {};
  for (const [key, score] of entries) {
    const [code, title] = String(key).split("|");
    const s = Number(score);
    if (title === undefined || !Number.isFinite(s) || s < 0 || s > 1) throw new HttpError(422, `bad score entry: ${key}`);
    add[courseKey(code, title)] = s;
  }
  await updateJson(store, await scoresKey(mdl, cat, definition), (file) => {
    const now = file || { model: mdl, category: cat, definition, scores: {} };
    return { ...now, scores: { ...now.scores, ...add } }; // the seed wins
  });
  return json({ model: mdl, category: cat, stored: entries.length });
}

// ---- helpers -----------------------------------------------------------------

function model(env) {
  return env.JEV_MODEL || "jev-1.14.0";
}

function normCategory(category) {
  const cat = String(category).trim().toUpperCase();
  if (!CATEGORY_RE.test(cat) || cat.length > LIMITS.max_category_chars) {
    throw new HttpError(422, "category: letters, digits, space and _ & + - only");
  }
  return cat;
}

function text(value, name, max) {
  if (typeof value !== "string" || !value.trim()) throw new HttpError(422, `${name} must be a non-empty string`);
  if (value.length > max) throw new HttpError(413, `${name} is longer than ${max} characters`);
  return value;
}

async function body(request) {
  try {
    const parsed = await request.json();
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
  } catch (_) { /* fall through */ }
  throw new HttpError(400, "the body must be a JSON object");
}

/** Count `questions` against the token's daily limit (DAILY_LIMIT when the token has none). */
async function spend(tok, questions, { env, store }) {
  const day = new Date().toISOString().slice(0, 10);
  const limit = tok.daily_limit != null ? tok.daily_limit : Number(env.DAILY_LIMIT || 0) || null;
  await updateJson(store, `usage/${day}/${tok.hash}.json`, (file) => {
    const used = file ? file.questions : 0;
    if (limit != null && used + questions > limit) {
      throw new HttpError(429, `daily limit reached for ${tok.name}: ${used} of ${limit} Jev questions used today (UTC)`);
    }
    return { name: tok.name, questions: used + questions };
  });
}

/** POST to TypeSafe with the key; a couple of short retries on 429/529 (the client retries longer). */
async function typesafe(jevBody, env) {
  if (!env.TYPESAFE_API_KEY) throw new HttpError(503, "the server has no TypeSafe key (TYPESAFE_API_KEY)");
  const url = env.TYPESAFE_URL || "https://api.typesafe.ai/v1/systemone";
  for (let attempt = 0; ; attempt++) {
    let resp;
    try {
      resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "User-Agent": "gpa-tools-cloud",
                   Authorization: `Bearer ${env.TYPESAFE_API_KEY}` },
        body: JSON.stringify(jevBody),
      });
    } catch (err) {
      throw new HttpError(502, `cannot reach TypeSafe: ${err.message || err}`);
    }
    if (resp.ok) {
      try {
        return await resp.json();
      } catch (_) {
        throw new HttpError(502, "TypeSafe answered with something that is not JSON");
      }
    }
    if (RETRY_STATUSES.includes(resp.status) && attempt < 2) {
      const wait = Math.min(Number(resp.headers.get("retry-after")) || 2 ** attempt, 2);
      await new Promise((r) => setTimeout(r, wait * 1000));
      continue;
    }
    const detail = await errorDetail(resp);
    // the client's own retries take over for rate limits; everything else is a bad gateway
    throw new HttpError(RETRY_STATUSES.includes(resp.status) ? 503 : 502, `TypeSafe HTTP ${resp.status}: ${detail}`);
  }
}

async function errorDetail(resp) {
  try {
    const parsed = await resp.json();
    const d = parsed && parsed.detail;
    if (typeof d === "string") return d;
    if (d && d.message) return d.message;
    return JSON.stringify(d === undefined ? parsed : d).slice(0, 300);
  } catch (_) {
    return resp.statusText || "error";
  }
}

async function sha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}
