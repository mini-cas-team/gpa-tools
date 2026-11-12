// Where the server keeps its data: JSON files, by key ("scores/jev-1.14.0/STEM/<hash>.json").
//
// A store is {get(key) -> {text, etag} | null, put(key, text, {etag}) -> bool,
// list(prefix) -> [key, ...]}: put writes only
// when the file is still at `etag` (null = only when it does not exist yet), so two requests
// changing the same file at once never lose each other's writes; updateJson retries (or, when
// the store offers locked(key, fn), as the local folder does, runs fn under a per-file lock).
//   r2Store(bucket)        Cloudflare R2 (the Worker's BUCKET binding)  -- deployed
//   fsStore(dir)           a folder on disk (local/fs_store.js)         -- local Node server

export function r2Store(bucket) {
  return {
    async get(key) {
      const obj = await bucket.get(key);
      return obj ? { text: await obj.text(), etag: obj.etag } : null;
    },
    async put(key, text, { etag = null } = {}) {
      const onlyIf = etag ? { etagMatches: etag } : new Headers({ "If-None-Match": "*" });
      const done = await bucket.put(key, text, { onlyIf, httpMetadata: { contentType: "application/json" } });
      return done !== null; // R2 returns null when the condition failed
    },
    async list(prefix) {
      const keys = [];
      let cursor;
      do {
        const page = await bucket.list({ prefix, cursor });
        keys.push(...page.objects.map((o) => o.key));
        cursor = page.truncated ? page.cursor : undefined;
      } while (cursor);
      return keys.sort();
    },
  };
}

export async function readJson(store, key) {
  const obj = await store.get(key);
  return obj ? { data: JSON.parse(obj.text), etag: obj.etag } : { data: null, etag: null };
}

/**
 * Read `key`, let `change(data | null)` return the new data (or undefined: no write), write it
 * if nobody else wrote in between, else start over.  `change` may run more than once.
 */
export async function updateJson(store, key, change, tries = 10) {
  const once = async () => {
    const { data, etag } = await readJson(store, key);
    const next = change(data);
    if (next === undefined) return { done: true, value: data };
    if (await store.put(key, JSON.stringify(next, null, 1), { etag })) return { done: true, value: next };
    return { done: false };
  };
  if (store.locked) return (await store.locked(key, once)).value; // the only writer: never conflicts
  for (let i = 0; i < tries; i++) {
    const r = await once();
    if (r.done) return r.value;
    await new Promise((ok) => setTimeout(ok, Math.random() * 20 * (i + 1))); // spread the writers out
  }
  throw new Error(`too many concurrent writes to ${key}`);
}
