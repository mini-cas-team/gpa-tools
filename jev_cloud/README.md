# jev_cloud: the gpa-tools cloud server

The only place the TypeSafe API key lives. Clients (`category-builder: jev: mode: cloud`)
send it course and letter data with a client token. The server builds the Jev question
from `../jev_core/prompts.json`, asks TypeSafe, and returns the result.

The same code runs in two places:

| | Run with | Data in |
|---|---|---|
| **Local**, for testing before there's a Cloudflare account | `npm start` (plain Node 22.5+, no npm install) | a folder, `jev_cloud/data/` (git-ignored) |
| **Cloudflare**, the deployed server | `npx wrangler deploy` (a Worker) | an R2 bucket, `gpa-tools-jev` |

The data is JSON files with the same names in both places, so moving from local
to Cloudflare means uploading the folder. No code changes.

## The data files

| File | Holds |
|---|---|
| `scores/<model>/<CAT>/<sha256(definition)[:16]>.json` | `{model, category, definition, scores: {"CODE\|title": 0-1}}`. The scores map has the same format as a client's `<work>/jev/cache_<cat>.json`. |
| `definitions/<CAT>.json` | `{category, definition, created_at, created_by}`: the shared definitions. The first client's text wins and is never replaced. |
| `requests.json` | `{<sha256(token)>: {id, email, hostname, status, requested_at, decided_at}}`: laptops asking for a token. Never holds the token itself. |
| `tokens.json` | `{<sha256(token)>: {name, daily_limit, revoked, created_at}}`. The tokens themselves are never stored. |
| `usage/<YYYY-MM-DD>/<sha256(token)>.json` | `{name, questions}`: Jev questions asked that day (UTC) |

- **Scores are shared.** Every client using the same model and definition gets
  them, so a course is paid for once. Only Jev's answers and the admin seed write
  scores; clients can't.
- **Letters are never stored.** A letter's text passes through to Jev, and only
  its length is logged.
- **Safe concurrent writes.** Two requests changing the same file never lose each
  other's writes: R2 writes only if the file hasn't changed since it was read, and
  retries otherwise. Locally, each file is locked while it's updated.

## Endpoints

| Endpoint | Who | Body / reply |
|---|---|---|
| `GET /v1/info` | client | → `{model, limits, client}` |
| `POST /v1/courses/score` | client | `{category, definition, courses: [[code, title], ...]}` (≤ 50) → `{model, scores, usage}` |
| `POST /v1/letters/rank` | client | `{letter}` → `{model, choice, confidence}` |
| `GET /v1/definitions` | client | → `{definitions: {CAT: definition}}` |
| `GET /v1/definitions/<CAT>` | client | → `{category, definition}` or 404 |
| `POST /v1/definitions/<CAT>` | client | `{definition}` → `{category, definition, created}`. Create-only: when one exists, it comes back with `created: false`. |
| `POST /v1/requests` | anyone | `{email, hostname, token_hash}` → `{id, status: "pending"}`. At most 50 wait at once; a newer request from the same email + hostname replaces its pending one. |
| `GET /v1/requests/<token_hash>` | anyone | → `{id, status, name}`. Only the laptop holding the token knows its hash. |
| `GET /v1/admin/requests` | admin | → `{requests: [...]}` |
| `POST /v1/admin/requests/<id>/approve` | admin | `{daily_limit?}` → the hash becomes the live token `<email>:<hostname>`; that laptop's older token is revoked |
| `POST /v1/admin/requests/<id>/reject` | admin | |
| `POST /v1/admin/tokens` | admin | `{name, daily_limit?}` → `{name, token}` (shown once) |
| `DELETE /v1/admin/tokens/<name>` | admin | revokes it |
| `POST /v1/admin/scores` | admin | `{category, definition, model?, scores: {"CODE\|title": 0-1}}` (≤ 2000) |

Client calls use a client token; admin calls use the `ADMIN_TOKEN` secret.
Errors come back as `{error}` with these statuses:

- 401: bad token
- 413 / 422: bad input
- 429: daily limit reached
- 502: TypeSafe refused the request
- 503: TypeSafe is busy, or the server has no key

## 1. Run it locally (no Cloudflare account)

```bash
cd jev_cloud
printf 'TYPESAFE_API_KEY=%s\nADMIN_TOKEN=%s\n' "$GPA_TOOLS_KEY" "$(python3 -c 'import secrets;print(secrets.token_hex(16))')" > .dev.vars
npm start                      # http://127.0.0.1:8787, data in jev_cloud/data/
```

`.dev.vars` is git-ignored, and `wrangler dev` reads the same file. Environment
variables override it. Other settings: `JEV_DATA` (the folder), `HOST`, `PORT`,
`JEV_MODEL`, `TYPESAFE_URL`, `DAILY_LIMIT`.

Laptops normally get their token by asking and being approved (`python -m jev_category.enroll`
and `python -m jev_category.admin`, in the top README's "This laptop's token"). For a quick
test, make one by hand, then point the client at the server:

```bash
ADMIN=$(sed -n 's/^ADMIN_TOKEN=//p' .dev.vars)
curl -s -X POST http://127.0.0.1:8787/v1/admin/tokens -H "Authorization: Bearer $ADMIN" \
  -H "Content-Type: application/json" -d '{"name": "my-pc"}'
```

```yaml
# config.yaml
category-builder:
  jev:
    mode: cloud
    cloud-url: http://127.0.0.1:8787
```

```bash
export GPA_TOOLS_CLOUD_TOKEN=gpat_...        # the token from the curl above
.venv/bin/python install/check.py            # "server accepts the token"
GPA_TOOLS_CLOUD_ADMIN_TOKEN=$ADMIN .venv/bin/python -m jev_category.seed --category STEM --definition
                                             # optional: upload this machine's scores
```

## 2. Deploy to Cloudflare later

Run these from `jev_cloud/`. `npx wrangler` downloads Wrangler on first use.

Before you start, do these in the dashboard: enable R2 (Storage & databases → R2, free plan;
a payment method is required), and open Compute → Workers & Pages once. Opening that page
creates the account's `<subdomain>.workers.dev`.

```bash
npx wrangler login                           # browser page: Authorize
npx wrangler dev --port 8788                 # optional: run under Wrangler first, simulated R2
npx wrangler r2 bucket create gpa-tools-jev
sed -n 's/^TYPESAFE_API_KEY=//p' .dev.vars | tr -d '\n' | npx wrangler secret put TYPESAFE_API_KEY
python3 -c 'import secrets;print(secrets.token_hex(24))' | tr -d '\n' | npx wrangler secret put ADMIN_TOKEN
npx wrangler deploy                          # prints https://gpa-tools-jev.<subdomain>.workers.dev
node local/upload_r2.js | sh                 # copy jev_cloud/data/ into the bucket (tokens, scores, ...)
```

- **Pipe the secrets in.** If `secret put` can't prompt (a non-interactive shell, or
  Claude Code's `!`), it saves an **empty** secret without complaining. The server then
  answers 503 "the server has no TypeSafe key". To fix it, run the command again with
  the value piped in. You don't need to deploy again.
- **Keep the admin token.** Save the `ADMIN_TOKEN` value somewhere private before you pipe it.
- **New subdomains are slow to start.** A brand-new workers.dev subdomain can take about a
  minute to get its TLS certificate. Until then, curl fails with exit 35.

Deployed now: `https://gpa-tools-jev.johnprofessional132.workers.dev`.

Then change each client's `cloud-url` to the workers.dev URL. Tokens made
locally keep working, because `tokens.json` was uploaded with everything else.

`wrangler.toml` `[vars]`:

- `JEV_MODEL`: the model used for every client. Changing it starts new score
  files, and each client's local cache is cleared on its next run.
- `TYPESAFE_URL`: the TypeSafe endpoint.
- `DAILY_LIMIT`: the default number of Jev questions per token per UTC day.
  `"0"` means no limit. A token's own `daily_limit` overrides it.

## Tests

```bash
cd jev_cloud && npm test
```

Every endpoint is tested on both stores: a temporary folder, and R2 faked in
memory with its conditional-write rules. TypeSafe is faked. The tests also check
`jev_core/fixtures.json`, as the Python tests do, so the JavaScript and Python
sides build the same TypeSafe request.
