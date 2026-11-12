// Just enough of an R2 bucket for tests: get(key) / put(key, text, {onlyIf}) with
// R2's conditional-write rules (etagMatches, or If-None-Match: * for "only if new").

let n = 0;

export function fakeR2() {
  const objects = new Map(); // key -> {text, etag}
  return {
    objects,
    async get(key) {
      const o = objects.get(key);
      return o ? { etag: o.etag, text: async () => o.text } : null;
    },
    async put(key, text, { onlyIf } = {}) {
      const now = objects.get(key);
      if (onlyIf instanceof Headers) {
        if (onlyIf.get("If-None-Match") === "*" && now) return null;
      } else if (onlyIf && onlyIf.etagMatches !== undefined) {
        if (!now || now.etag !== onlyIf.etagMatches) return null;
      }
      const etag = `e${++n}`;
      objects.set(key, { text, etag });
      return { key, etag };
    },
    // Two keys per page, so the tests exercise the cursor.
    async list({ prefix = "", cursor } = {}) {
      const keys = [...objects.keys()].filter((k) => k.startsWith(prefix)).sort();
      const start = cursor ? Number(cursor) : 0;
      const page = keys.slice(start, start + 2);
      const truncated = start + 2 < keys.length;
      return { objects: page.map((key) => ({ key })), truncated, cursor: truncated ? String(start + 2) : undefined };
    },
  };
}
