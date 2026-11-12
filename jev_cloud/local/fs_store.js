// The store (see src/store.js) as a folder on disk, for running the server on plain Node.
// The key "scores/jev-1.14.0/STEM/ab12.json" is the file <dir>/scores/jev-1.14.0/STEM/ab12.json,
// the same name it gets in R2, so the folder can be uploaded to R2 as it is.

import { createHash } from "node:crypto";
import { mkdir, readdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, join, resolve, sep } from "node:path";

export function fsStore(dir) {
  const root = resolve(dir);
  let queue = Promise.resolve(); // one put at a time: this process is the only writer

  function file(key) {
    const path = resolve(join(root, ...key.split("/")));
    if (!path.startsWith(root + sep) || key.split("/").some((p) => p === "" || p === "." || p === "..")) {
      throw new Error(`bad key: ${key}`);
    }
    return path;
  }

  async function get(key) {
    try {
      const text = await readFile(file(key), "utf8");
      return { text, etag: createHash("sha256").update(text).digest("hex") };
    } catch (err) {
      if (err.code === "ENOENT") return null;
      throw err;
    }
  }

  function put(key, text, { etag = null } = {}) {
    const run = queue.then(async () => {
      const now = await get(key);
      if ((now ? now.etag : null) !== etag) return false;
      const path = file(key);
      await mkdir(dirname(path), { recursive: true });
      const tmp = `${path}.${process.pid}.tmp`;
      await writeFile(tmp, text, "utf8");
      await rename(tmp, path); // a reader never sees half a file
      return true;
    });
    queue = run.catch(() => {});
    return run;
  }

  // Every .json key under `prefix` ("definitions/"), sorted.
  async function list(prefix) {
    const keys = [];
    async function walk(rel) {
      let entries;
      try {
        entries = await readdir(join(root, ...rel.split("/").filter(Boolean)), { withFileTypes: true });
      } catch (err) {
        if (err.code === "ENOENT") return;
        throw err;
      }
      for (const e of entries) {
        if (e.isDirectory()) await walk(`${rel}${e.name}/`);
        else if (e.name.endsWith(".json")) keys.push(`${rel}${e.name}`);
      }
    }
    const dir = prefix.endsWith("/") ? prefix : prefix.slice(0, prefix.lastIndexOf("/") + 1);
    await walk(dir);
    return keys.filter((k) => k.startsWith(prefix)).sort();
  }

  // This process is the folder's only writer, so a read-change-write of one file can simply
  // wait for the one before it (updateJson uses this instead of retrying).
  const locks = new Map();
  function locked(key, fn) {
    const prev = locks.get(key) || Promise.resolve();
    const run = prev.then(fn, fn);
    const tail = run.catch(() => {});
    locks.set(key, tail);
    tail.then(() => { if (locks.get(key) === tail) locks.delete(key); });
    return run;
  }

  return { root, get, put, list, locked };
}
