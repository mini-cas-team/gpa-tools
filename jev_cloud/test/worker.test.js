import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, afterEach, beforeEach, describe, test } from "node:test";

import worker from "../src/index.js";
import { courseBody, courseKey, letterBody, parseChoice, parseScores } from "../src/jev.js";
import { r2Store, updateJson } from "../src/store.js";
import { fsStore } from "../local/fs_store.js";
import { createServer, readDevVars, settings } from "../local/server.js";
import { fakeR2 } from "./fake_r2.js";

const fixtures = JSON.parse(readFileSync(new URL("../../jev_core/fixtures.json", import.meta.url), "utf8"));
const ADMIN = "admin-secret";
const TYPESAFE = "https://typesafe.test/v1/systemone";
const realFetch = globalThis.fetch;
const temps = [];
after(() => temps.forEach((d) => rmSync(d, { recursive: true, force: true })));

function tempDir() {
  const d = mkdtempSync(join(tmpdir(), "jev-cloud-"));
  temps.push(d);
  return d;
}

let sent, replies;

// Fake TypeSafe: records each request; answers from `replies` (a function or a queue).
// Anything else (the local server test) goes to the real fetch.
beforeEach(() => {
  sent = [];
  replies = null;
  globalThis.fetch = async (url, init) => {
    if (String(url) !== TYPESAFE) return realFetch(url, init);
    const body = JSON.parse(init.body);
    sent.push({ url, auth: init.headers.Authorization, body });
    const r = typeof replies === "function" ? replies(body) : replies.shift();
    return r instanceof Response ? r : new Response(JSON.stringify(r), { status: 200 });
  };
});
afterEach(() => { globalThis.fetch = realFetch; });

/** Answers every noul question: MATH/PHYS high, the rest low. */
function scoreByCode(body) {
  const answers = {};
  for (const q of Object.keys(body.questions)) {
    const course = body.state[`course_${q.slice(1)}`];
    answers[q] = { type: "noul", noul: /^(MATH|PHYS)/.test(course) ? 0.97 : 0.05 };
  }
  return { answers, usage: { input_tokens: 10 * Object.keys(answers).length, output_tokens: 0 } };
}

const STEM = { category: "STEM", definition: "Science and math." };

test("jev.js builds exactly what jev_core (Python) builds", () => {
  const c = fixtures.course;
  assert.deepEqual(courseBody(c.model, c.category, c.definition, c.courses), c.body);
  assert.deepEqual(letterBody(fixtures.letter.model, fixtures.letter.letter), fixtures.letter.body);
  assert.equal(courseKey(fixtures.course_key.code, fixtures.course_key.title), fixtures.course_key.key);
  assert.deepEqual(parseScores(c.body, { answers: { q01: { noul: 0.9 }, q02: { noul: 0.1 } } }), [0.9, 0.1]);
  assert.throws(() => parseScores(c.body, { answers: { q01: { noul: 0.9 } } }));
  assert.deepEqual(parseChoice({ answers: { rank: { choice: "good", confidence: 0.7 } } }), ["good", 0.7]);
  assert.throws(() => parseChoice({ answers: { rank: { choice: "superb" } } }));
});

// The same behaviour on both stores: the local folder and (faked) R2.
const STORES = {
  "local folder": () => ({ STORE: fsStore(tempDir()) }),
  "R2": () => ({ BUCKET: fakeR2() }),
};

for (const [storeName, makeStore] of Object.entries(STORES)) {
  describe(`with the ${storeName} store`, () => {
    let env;
    beforeEach(() => {
      env = { ...makeStore(), TYPESAFE_API_KEY: "ts-key", TYPESAFE_URL: TYPESAFE, ADMIN_TOKEN: ADMIN,
              JEV_MODEL: "jev-test", DAILY_LIMIT: "0" };
    });
    const store = () => env.STORE || r2Store(env.BUCKET);
    const file = async (key) => {
      const o = await store().get(key);
      return o && JSON.parse(o.text);
    };

    async function call(method, path, { token, body } = {}) {
      const headers = { "Content-Type": "application/json" };
      if (token) headers.Authorization = `Bearer ${token}`;
      const resp = await worker.fetch(new Request(`https://jev.test${path}`, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      }), env);
      return { status: resp.status, json: await resp.json() };
    }

    async function newToken(name = "school-a", extra = {}) {
      const r = await call("POST", "/v1/admin/tokens", { token: ADMIN, body: { name, ...extra } });
      assert.equal(r.status, 201);
      return r.json.token;
    }

    test("client endpoints need a live token; admin endpoints need the admin token", async () => {
      assert.equal((await call("GET", "/v1/info")).status, 401);
      assert.equal((await call("GET", "/v1/info", { token: "nope" })).status, 401);
      assert.equal((await call("POST", "/v1/admin/tokens", { token: "nope", body: { name: "x" } })).status, 401);

      const token = await newToken();
      assert.match(token, /^gpat_[0-9a-f]{64}$/);
      const info = await call("GET", "/v1/info", { token });
      assert.equal(info.status, 200);
      assert.deepEqual([info.json.model, info.json.client, info.json.limits.max_courses], ["jev-test", "school-a", 50]);
      assert.equal((await call("POST", "/v1/admin/tokens", { token, body: { name: "y" } })).status, 401);
      assert.ok(!JSON.stringify(await file("tokens.json")).includes(token)); // only its hash is kept

      assert.equal((await call("POST", "/v1/admin/tokens", { token: ADMIN, body: { name: "school-a" } })).status, 409);
      assert.equal((await call("DELETE", "/v1/admin/tokens/school-a", { token: ADMIN })).status, 200);
      assert.equal((await call("DELETE", "/v1/admin/tokens/school-a", { token: ADMIN })).status, 404);
      assert.equal((await call("GET", "/v1/info", { token })).status, 401);

      delete env.ADMIN_TOKEN;
      assert.equal((await call("POST", "/v1/admin/tokens", { token: "x", body: { name: "a" } })).status, 403);
    });

    test("scores are asked once, saved as a file, and shared with every client", async () => {
      const a = await newToken("school-a");
      const b = await newToken("school-b");
      replies = scoreByCode;

      const first = await call("POST", "/v1/courses/score", {
        token: a, body: { ...STEM, courses: [["MATH 1", "Calc"], ["ENGL 1", "Essay"], ["math 1", "calc"]] } });
      assert.equal(first.status, 200);
      assert.deepEqual(first.json.scores, [0.97, 0.05, 0.97]);
      assert.deepEqual(first.json.usage, { cached: 1, asked: 2, input_tokens: 20, output_tokens: 0 });
      assert.equal(sent.length, 1);
      assert.equal(sent[0].auth, "Bearer ts-key");
      assert.deepEqual(sent[0].body, courseBody("jev-test", "STEM", "Science and math.", [["MATH 1", "Calc"], ["ENGL 1", "Essay"]]));

      // the file: the same scores map as a client's cache_<cat>.json
      const { createHash } = await import("node:crypto");
      const hash = createHash("sha256").update("Science and math.").digest("hex").slice(0, 16);
      assert.deepEqual(await file(`scores/jev-test/STEM/${hash}.json`), {
        model: "jev-test", category: "STEM", definition: "Science and math.",
        scores: { "MATH 1|calc": 0.97, "ENGL 1|essay": 0.05 } });

      // another client, same definition: nothing new is asked
      const second = await call("POST", "/v1/courses/score", {
        token: b, body: { ...STEM, category: "stem", courses: [["ENGL 1", "essay"], ["MATH 1", "Calc"]] } });
      assert.deepEqual(second.json.scores, [0.05, 0.97]);
      assert.equal(second.json.usage.asked, 0);
      assert.equal(sent.length, 1);

      // a different definition is a different question
      await call("POST", "/v1/courses/score", {
        token: b, body: { category: "STEM", definition: "Only math.", courses: [["MATH 1", "Calc"]] } });
      assert.equal(sent.length, 2);
    });

    test("two clients adding scores at once both keep theirs", async () => {
      const t = await newToken();
      replies = scoreByCode;
      const ask = (code) => call("POST", "/v1/courses/score", { token: t, body: { ...STEM, courses: [[code, "x"]] } });
      const all = await Promise.all(["MATH 1", "MATH 2", "ENGL 3", "PHYS 4", "ENGL 5"].map(ask));
      assert.ok(all.every((r) => r.status === 200));
      const again = await call("POST", "/v1/courses/score", {
        token: t, body: { ...STEM, courses: [["MATH 1", "x"], ["MATH 2", "x"], ["ENGL 3", "x"], ["PHYS 4", "x"], ["ENGL 5", "x"]] } });
      assert.equal(again.json.usage.asked, 0);
      assert.equal((await file(`usage/${new Date().toISOString().slice(0, 10)}/${await hashOf(t)}.json`)).questions, 5);
    });

    test("course requests are checked before anything is asked", async () => {
      const t = await newToken();
      replies = scoreByCode;
      const bad = [
        [{ ...STEM, courses: [] }, 422],
        [{ ...STEM, courses: [["MATH 1"]] }, 422],
        [{ ...STEM, courses: [[1, 2]] }, 422],
        [{ ...STEM, courses: Array.from({ length: 51 }, (_, i) => [`C ${i}`, "t"]) }, 413],
        [{ category: "", definition: "d", courses: [["A", "b"]] }, 422],
        [{ category: "../etc", definition: "d", courses: [["A", "b"]] }, 422],
        [{ category: "STEM", definition: "x".repeat(4001), courses: [["A", "b"]] }, 413],
      ];
      for (const [body, status] of bad) assert.equal((await call("POST", "/v1/courses/score", { token: t, body })).status, status);
      const notJson = await worker.fetch(new Request("https://jev.test/v1/courses/score", {
        method: "POST", headers: { Authorization: `Bearer ${t}` }, body: "nope" }), env);
      assert.equal(notJson.status, 400);
      assert.equal(sent.length, 0);
    });

    test("TypeSafe failures come back as clear gateway errors and save nothing", async () => {
      const t = await newToken();
      const ask = () => call("POST", "/v1/courses/score", { token: t, body: { ...STEM, courses: [["MATH 1", "Calc"]] } });
      replies = [new Response(JSON.stringify({ detail: { message: "Unknown model: jev-test" } }), { status: 422 })];
      let r = await ask();
      assert.equal(r.status, 502);
      assert.match(r.json.error, /TypeSafe HTTP 422: Unknown model/);
      replies = [{ answers: {} }];
      assert.equal((await ask()).status, 502);
      replies = scoreByCode;
      assert.equal((await ask()).json.usage.asked, 1); // nothing was saved by the failures
      delete env.TYPESAFE_API_KEY;
      replies = [];
      r = await call("POST", "/v1/courses/score", { token: t, body: { ...STEM, courses: [["PHYS 9", "x"]] } });
      assert.equal(r.status, 503);
    });

    test("the daily limit counts Jev questions, not cached courses", async () => {
      const t = await newToken("small", { daily_limit: 2 });
      replies = scoreByCode;
      const ask = (courses) => call("POST", "/v1/courses/score", { token: t, body: { ...STEM, courses } });
      assert.equal((await ask([["MATH 1", "a"], ["MATH 2", "b"]])).status, 200);
      assert.equal((await ask([["MATH 1", "a"], ["MATH 2", "b"]])).status, 200); // all cached: free
      const over = await ask([["MATH 3", "c"]]);
      assert.equal(over.status, 429);
      assert.match(over.json.error, /2 of 2/);
    });

    test("a letter is ranked and never stored", async () => {
      const t = await newToken();
      replies = [{ answers: { rank: { type: "choice", choice: "excellent", confidence: 0.82 } } }];
      const r = await call("POST", "/v1/letters/rank", { token: t, body: { letter: "Dear committee, a private letter" } });
      assert.deepEqual(r.json, { model: "jev-test", choice: "excellent", confidence: 0.82 });
      assert.deepEqual(sent[0].body, letterBody("jev-test", "Dear committee, a private letter"));
      const everything = env.STORE ? JSON.stringify(await file("tokens.json")) : JSON.stringify([...env.BUCKET.objects]);
      assert.ok(!everything.includes("private letter"));
      assert.equal((await call("POST", "/v1/letters/rank", { token: t, body: { letter: "" } })).status, 422);
    });

    test("the first client's definition is the shared one, never replaced", async () => {
      const t = await newToken();
      const u = await newToken("school-b");
      assert.equal((await call("GET", "/v1/definitions/STEM", { token: t })).status, 404);
      assert.equal((await call("POST", "/v1/definitions/STEM", { body: STEM })).status, 401);
      const first = await call("POST", "/v1/definitions/stem", { token: t, body: STEM });
      assert.equal(first.status, 201);
      assert.deepEqual(first.json, { category: "STEM", definition: "Science and math.", created: true });
      const second = await call("POST", "/v1/definitions/STEM", { token: u, body: { definition: "Other." } });
      assert.equal(second.status, 200);
      assert.deepEqual(second.json, { category: "STEM", definition: "Science and math.", created: false });
      assert.equal((await call("PUT", "/v1/definitions/STEM", { token: ADMIN, body: STEM })).status, 404);
      const r = await call("GET", "/v1/definitions/Stem", { token: u });
      assert.deepEqual(r.json, { category: "STEM", definition: "Science and math." });
      assert.equal((await call("POST", "/v1/definitions/STEM", { token: t, body: { definition: " " } })).status, 422);
    });

    test("two clients creating one category at once get one definition", async () => {
      const t = await newToken();
      const rs = await Promise.all([1, 2, 3, 4, 5].map((i) =>
        call("POST", "/v1/definitions/LAW", { token: t, body: { definition: `Law ${i}.` } })));
      assert.equal(rs.filter((r) => r.json.created).length, 1);
      assert.equal(new Set(rs.map((r) => r.json.definition)).size, 1);
    });

    test("every definition is listed", async () => {
      const t = await newToken();
      assert.deepEqual((await call("GET", "/v1/definitions", { token: t })).json, { definitions: {} });
      for (const [cat, d] of [["STEM", "S."], ["LAW", "L."], ["MATH", "M."], ["ART & DESIGN", "A."]]) {
        await call("POST", `/v1/definitions/${encodeURIComponent(cat)}`, { token: t, body: { definition: d } });
      }
      assert.deepEqual((await call("GET", "/v1/definitions", { token: t })).json,
        { definitions: { "ART & DESIGN": "A.", LAW: "L.", MATH: "M.", STEM: "S." } });
      assert.equal((await call("GET", "/v1/definitions")).status, 401);
    });

    test("seeded scores are served without asking Jev", async () => {
      const t = await newToken();
      const seed = await call("POST", "/v1/admin/scores", {
        token: ADMIN, body: { ...STEM, scores: { "MATH 1|calc": 0.91, "ENGL 1|essay": 0.02 } } });
      assert.deepEqual(seed.json, { model: "jev-test", category: "STEM", stored: 2 });
      replies = [];
      const r = await call("POST", "/v1/courses/score", { token: t, body: { ...STEM, courses: [["math 1", "Calc"], ["ENGL 1", "Essay"]] } });
      assert.deepEqual(r.json.scores, [0.91, 0.02]);
      assert.equal(sent.length, 0);
      assert.equal((await call("POST", "/v1/admin/scores", { token: ADMIN, body: { ...STEM, scores: { "bad": 1 } } })).status, 422);
      assert.equal((await call("POST", "/v1/admin/scores", { token: ADMIN, body: { ...STEM, scores: { "A|b": 2 } } })).status, 422);
    });

    test("a laptop asks for a token, the admin approves, the laptop's own token goes live", async () => {
      const token = "gpat_" + "ab".repeat(32);
      const hash = await hashOf(token);
      const ask = { email: "Jane@School.edu", hostname: "hp-envy", token_hash: hash };
      const sent1 = await call("POST", "/v1/requests", { body: ask });
      assert.equal(sent1.status, 201);
      assert.deepEqual(sent1.json, { id: hash.slice(0, 12), status: "pending" });
      assert.equal((await call("POST", "/v1/requests", { body: ask })).status, 201); // resent: same request
      assert.deepEqual((await call("GET", `/v1/requests/${hash}`)).json,
        { id: hash.slice(0, 12), status: "pending", name: "jane@school.edu:hp-envy" });
      assert.equal((await call("GET", "/v1/info", { token })).status, 401); // not yet
      assert.equal((await call("GET", "/v1/requests/" + "0".repeat(64))).status, 404);

      // only the admin lists and decides
      assert.equal((await call("GET", "/v1/admin/requests", { token })).status, 401);
      const list = await call("GET", "/v1/admin/requests", { token: ADMIN });
      assert.deepEqual(list.json.requests.map((r) => [r.id, r.email, r.status]), [[hash.slice(0, 12), "jane@school.edu", "pending"]]);
      assert.ok(!JSON.stringify(await file("requests.json")).includes(token));

      const ok = await call("POST", `/v1/admin/requests/${hash.slice(0, 12)}/approve`, { token: ADMIN, body: {} });
      assert.deepEqual(ok.json, { id: hash.slice(0, 12), status: "approved", name: "jane@school.edu:hp-envy", replaced: false });
      assert.equal((await call("GET", `/v1/requests/${hash}`)).json.status, "approved");
      assert.equal((await call("GET", "/v1/info", { token })).json.client, "jane@school.edu:hp-envy");
      assert.equal((await call("POST", `/v1/admin/requests/${hash.slice(0, 12)}/reject`, { token: ADMIN, body: {} })).status, 409);
      assert.equal((await call("POST", "/v1/requests", { body: ask })).status, 409); // already a token
      assert.ok(!JSON.stringify(await file("tokens.json")).includes(token));

      // a reinstall of the same laptop: its new token replaces the old one once approved
      const token2 = "gpat_" + "cd".repeat(32);
      const hash2 = await hashOf(token2);
      await call("POST", "/v1/requests", { body: { ...ask, token_hash: hash2 } });
      const again = await call("POST", `/v1/admin/requests/${hash2.slice(0, 12)}/approve`, { token: ADMIN, body: { daily_limit: 9 } });
      assert.equal(again.json.replaced, true);
      assert.equal((await call("GET", "/v1/info", { token })).status, 401);
      assert.equal((await call("GET", "/v1/info", { token: token2 })).status, 200);
      assert.equal((await call("DELETE", "/v1/admin/tokens/jane@school.edu:hp-envy", { token: ADMIN })).status, 200);
    });

    test("token requests are checked, rejected ones stay unusable, and pending ones are capped", async () => {
      const h = async (i) => hashOf(`gpat_${i}`);
      const good = { email: "a@b.co", hostname: "pc1", token_hash: await h(0) };
      for (const bad of [{ ...good, email: "nope" }, { ...good, hostname: "a/b" }, { ...good, token_hash: "xyz" }, { ...good, email: "" }]) {
        assert.equal((await call("POST", "/v1/requests", { body: bad })).status, 422);
      }
      await call("POST", "/v1/requests", { body: good });
      const rej = await call("POST", `/v1/admin/requests/${good.token_hash.slice(0, 12)}/reject`, { token: ADMIN, body: {} });
      assert.equal(rej.json.status, "rejected");
      assert.equal((await call("GET", `/v1/requests/${good.token_hash}`)).json.status, "rejected");
      assert.equal((await call("GET", "/v1/info", { token: "gpat_0" })).status, 401);
      assert.equal((await call("POST", "/v1/admin/requests/ffffffffffff/approve", { token: ADMIN, body: {} })).status, 404);

      // the same laptop asking again replaces its pending request
      await call("POST", "/v1/requests", { body: { ...good, hostname: "pc2", token_hash: await h(1) } });
      await call("POST", "/v1/requests", { body: { ...good, hostname: "pc2", token_hash: await h(2) } });
      const pending = (await call("GET", "/v1/admin/requests", { token: ADMIN })).json.requests.filter((r) => r.status === "pending");
      assert.deepEqual(pending.map((r) => r.id), [(await h(2)).slice(0, 12)]);

      for (let i = 3; i < 52; i++) {
        assert.equal((await call("POST", "/v1/requests", { body: { ...good, hostname: `pc${i}`, token_hash: await h(i) } })).status, 201);
      }
      assert.equal((await call("POST", "/v1/requests", { body: { ...good, hostname: "one-more", token_hash: await h(99) } })).status, 429);
    });

    test("unknown endpoints are 404", async () => {
      assert.equal((await call("GET", "/")).status, 404);
      assert.equal((await call("GET", "/v1/courses/score")).status, 404);
    });
  });
}

async function hashOf(token) {
  const { createHash } = await import("node:crypto");
  return createHash("sha256").update(token).digest("hex");
}

// ---- the local folder store and server -------------------------------------------

test("the folder store writes only from the etag it read, and stays inside its folder", async () => {
  const s = fsStore(tempDir());
  assert.equal(await s.get("a/b.json"), null);
  assert.equal(await s.put("a/b.json", "1", { etag: null }), true);
  assert.equal(await s.put("a/b.json", "2", { etag: null }), false); // exists now
  const { etag } = await s.get("a/b.json");
  assert.equal(await s.put("a/b.json", "2", { etag }), true);
  assert.equal(await s.put("a/b.json", "3", { etag }), false); // stale
  assert.equal((await s.get("a/b.json")).text, "2");
  await assert.rejects(s.get("../x.json"));
  await assert.rejects(s.get("a//b.json"));
  const n = await Promise.all(Array.from({ length: 20 }, () =>
    updateJson(s, "count.json", (d) => ({ n: (d ? d.n : 0) + 1 }))));
  assert.equal(n.length, 20);
  assert.equal(JSON.parse((await s.get("count.json")).text).n, 20);
});

test("on R2, concurrent updates of one file retry until all land", async () => {
  const s = r2Store(fakeR2());
  await Promise.all(Array.from({ length: 8 }, () => updateJson(s, "count.json", (d) => ({ n: (d ? d.n : 0) + 1 }))));
  assert.equal(JSON.parse((await s.get("count.json")).text).n, 8);
});

test("local/server.js serves the Worker over HTTP with its data in a folder", async () => {
  const data = tempDir();
  const server = createServer(settings({ ADMIN_TOKEN: ADMIN, TYPESAFE_API_KEY: "k", TYPESAFE_URL: TYPESAFE,
                                         JEV_DATA: data, JEV_MODEL: "jev-test" }, {}));
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const made = await (await realFetch(`${base}/v1/admin/tokens`, {
      method: "POST", headers: { Authorization: `Bearer ${ADMIN}`, "Content-Type": "application/json" },
      body: JSON.stringify({ name: "pc-1" }) })).json();
    replies = scoreByCode;
    const r = await realFetch(`${base}/v1/courses/score`, {
      method: "POST", headers: { Authorization: `Bearer ${made.token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ ...STEM, courses: [["MATH 1", "Calc"]] }) });
    assert.equal(r.status, 200);
    assert.deepEqual((await r.json()).scores, [0.97]);
    assert.ok(existsSync(join(data, "tokens.json")));
    assert.ok(existsSync(join(data, "scores", "jev-test", "STEM")));
  } finally {
    server.close();
  }
});

test("settings: environment over .dev.vars over defaults", () => {
  const vars = join(tempDir(), ".dev.vars");
  writeFileSync(vars, "TYPESAFE_API_KEY=\"from-file\"\nPORT=9000\n# a comment\n");
  const fromFile = readDevVars(vars);
  assert.deepEqual(fromFile, { TYPESAFE_API_KEY: "from-file", PORT: "9000" });
  const cfg = settings({ PORT: "9100" }, fromFile);
  assert.deepEqual([cfg.TYPESAFE_API_KEY, cfg.PORT, cfg.HOST, cfg.JEV_MODEL], ["from-file", "9100", "127.0.0.1", "jev-1.14.0"]);
  assert.deepEqual(readDevVars(join(tempDir(), "none")), {});
});
