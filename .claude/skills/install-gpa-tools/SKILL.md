---
name: install-gpa-tools
description: Install gpa-tools on this machine and get it running - Python venv and packages, pdftotext, config.yaml (folders, the cloud server, definer mode), the jev builder through the gpa-tools cloud server (this laptop asks for its own client token with the user's email, the package owner approves it; direct TypeSafe with an API key for development only), and the category-definer (dev: ChatGPT, then Gemini in browser windows; prod: Gemini headless). No local LLM, no GPU, no model downloads. Use when the user asks to install, set up or repair gpa-tools or its dependencies, to add or fix the jev builder or this laptop's cloud token, when config.yaml is missing, or when the cloud server is not reachable.
---

# Installing gpa-tools

The user has cloned the repo and started Claude Code in its root. Get them to a
working `python -m browser_view`. The scripts in `install/` do the work and
report in plain text or JSON; this file says in what order and what to ask.

gpa-tools has no local model: course scoring (Jev) runs in the gpa-tools cloud
server, and definitions come from the cloud or the free ChatGPT/Gemini web
chats. The install is light: a venv, pdftotext, config.yaml and a token.

## Rules

- **User account only.** Never use sudo, an admin prompt, apt, dnf, brew
  installs that need admin, or system-wide paths. Every step below has a
  per-user route. If something seems to need admin, stop and tell the user.
- **Check before changing.** Run `install/check.py` first and skip whatever is
  already there and working. Run it again at the end.
- **One plan, one confirmation.** After the check, show the user the plan:
  what will be installed and where (the venv's packages, about 200 MB with
  Playwright; poppler about 100 MB if pdftotext is missing). Ask once before
  starting. Ask again only for the questions marked **Ask** below.
- **Report faithfully.** If a step fails, show its output and stop that step.
  Don't paper over it.
- **Never handle secrets.** That covers the TypeSafe API key, the cloud server's
  admin token, and this laptop's client token.
  - Don't ask for them in chat, print them, or write them into any file.
  - Never `cat` the files in `~/.gpa-tools/`. `ls -la` is fine.
  - config.yaml holds only variable *names* (`api-key-env`, `cloud-token-env`).
  - The user's email is not a secret, and the token request needs it.

## Paths per OS

| | Linux / macOS | Windows (Claude Code runs Git Bash) |
|---|---|---|
| any Python to start with | `python3` | `py` or `python` |
| the venv's Python, `$PY` below | `.venv/bin/python` | `.venv/Scripts/python.exe` |

On Windows, `python` can be the Microsoft Store stub, which runs nothing.
`check.py` only lists interpreters that actually report a version.

## Step 0 — check

```bash
python3 install/check.py --text      # summary for you and the user
python3 install/check.py             # JSON, when you need a detail
```

It reports the OS, RAM, free disk, Python and venv, pdftotext, Chrome and Edge,
the config.yaml folders, the definer's mode, and the jev builder. In cloud mode
it shows the cloud-url, where this laptop's token comes from
(`~/.gpa-tools/client_token`, a request waiting for approval, or none), and
whether the server accepts it. In direct mode it shows whether the key variable
is set (never its value) and whether TypeSafe accepts it.

If `config.outdated` is not empty, config.yaml is from an older version. Tell the
user which keys are outdated. Settings of the removed llm builder and Ollama
(`category-builder: llm:`, `active-category-builder: llm`, the definer's `model`
and `ollama-url`) are no longer read: **Ask** before deleting them by hand, and
set `$PY install/configure.py --active-builder jev` when the active builder was
llm.

If no Python runs at all, install uv first (step 1) and then run `check.py` with
`~/.local/bin/uv run --python 3.14 install/check.py --text`.

## Step 1 — Python venv and packages

Skip this step if `venv exists` and `missing: nothing` (and the venv isn't
`unsupported`). Otherwise follow the `/install-from-clone` skill
(`.claude/skills/install-from-clone/SKILL.md`), which runs
`install/setup_venv.py`. That script picks the Python: gpa-tools runs on 3.14 or
3.12 (`check.py` `PYTHON_SUPPORTED`), 3.14 preferred, else uv fetches 3.14 for
this user. In short:

```bash
python3 install/setup_venv.py --dry-run     # the plan; changes nothing
python3 install/setup_venv.py               # build .venv and install the packages
```

Exit 2 (`rebuild-needed`: the venv is an unsupported Python): **Ask**, then add
`--rebuild`. Exit 3 (`no-python`): **Ask** before installing uv for this user
(`curl -LsSf https://astral.sh/uv/install.sh | sh`, or on Windows
`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`),
then run it again. A fresh uv isn't on PATH yet, but the script finds it in
`~/.local/bin`. Show this plan as part of the step 0 plan and confirmation; don't
ask again.

## Step 2 — pdftotext

`pdftotext` is required: upload reads every PDF with it.

- `pdftotext` already found: skip.
- Otherwise:
  ```bash
  python3 install/get_tools.py            # poppler: a minute or so
  ```
  This downloads micromamba into `.tools/` and builds a conda-forge env in
  `.tools/env`, all inside the project and git-ignored. The last line printed
  is the tools folder. Record it:
  ```bash
  $PY install/configure.py --tool-folder <that folder>
  ```
  The programs read `tool-folders` from config.yaml, so there's no need to
  change PATH.

## Step 3 — folders and config.yaml

**Ask** where the data should live. Offer the default `~/gpa-tools-data`, which
becomes `input-folder` with one subfolder per component for the raw input,
`transcripts/` (PDFs), `letters/` (letter PDFs) and `personal/` (`.json`
files), plus `work/` (everything made from the input, again one subfolder per
component, and `work/jev/` for the builder), `out/` (the published
`<cat>_gpa.csv` files other applications read) and `log/`. Also ask whether they
already have a folder holding `transcripts/`, `letters/` and `personal/` to use
as `input-folder`.

```bash
$PY install/configure.py --data ~/gpa-tools-data
$PY install/configure.py --data ~/gpa-tools-data --input-folder "/path/to/their/input"
```

Without a config.yaml, this creates one from the template built into
`configure.py`, already in cloud mode with the deployed `cloud-url` and jev as
the active builder. If one already exists with folders that exist (check.py says
so), **Ask** before changing them. `configure.py` only rewrites the keys it's
given and creates the folders. config.yaml is tracked in git, so mention that
the change shows up in `git status`.

## Step 4 — the jev builder: this laptop's cloud token

Jev (TypeSafe's model) decides which courses are in a category, through the
**gpa-tools cloud server** (`cloud-url`). The server holds the TypeSafe key and
shares course scores between all laptops. This laptop needs only its own client
token. ~400 courses take a few seconds; courses another laptop already scored are
free. Each course gets a 0–1 score, kept in `<cat>_courses.csv`.

If check.py already shows `token: …/client_token; server accepts the token`,
skip this step.

1. **Point config.yaml at the server.** A new config.yaml already has it. For an
   older one:
   ```bash
   $PY install/configure.py --jev-mode cloud --cloud-url https://gpa-tools-jev.johnprofessional132.workers.dev --active-builder jev
   ```
2. **Ask** for the user's email. It is required: the package owner sees it in the
   request list, and the token is named `<email>:<hostname>`. Then request the token:
   ```bash
   $PY -m jev_category.enroll request --email <email>
   ```
   - It creates `~/.gpa-tools/` (`chmod 700`) with `pending_token`, a random token
     made on this laptop (`600`), and `request.json`.
   - It sends the server only the token's sha256, the email and the hostname.
   - It prints the request id. If the server can't be reached, the request is
     sent on the next start: say so and go on.
   - If check.py showed an old token that the server now refuses (401, revoked),
     add `--new`.
3. **Approval is done by a person, the package owner.** Tell the user:
   - the request id and the name `<email>:<hostname>`;
   - to ask the package owner to approve it. The owner runs
     `python -m jev_category.admin requests`, then `approve <id>`, with the admin
     token in `GPA_TOOLS_CLOUD_ADMIN_TOKEN`.
   - If the user *is* the owner, they run those commands **in their own terminal**.
     Never ask for the admin token here.
   - Until the request is approved, the install can finish. The web UI prints a
     NOTICE at start and builds stop with "No gpa-tools cloud token on this
     laptop … Contact the package owner".
4. **Once approved:**
   ```bash
   $PY -m jev_category.enroll status     # approved: pending_token becomes client_token
   python3 install/check.py --text       # token: ~/.gpa-tools/client_token; server accepts the token
   ```
   Every start does the same pickup by itself, so this check is optional.

`GPA_TOOLS_CLOUD_TOKEN` (the variable `cloud-token-env` names), when set, wins
over `~/.gpa-tools/`. It's for testing only. Don't set it in a profile.

### Direct mode (development only)

`mode: direct` calls TypeSafe itself and needs the TypeSafe API key on this
machine. Set it up only when the user asks for it. Production laptops use cloud
mode.

1. The user creates a key in the TypeSafe console (https://console.typesafe.ai →
   **API Keys**). The account needs a balance.
2. They store it **themselves** in `GPA_TOOLS_KEY`: `export` it in
   `~/.bashrc`/`~/.zshrc`, or `setx` it on Windows, then open a new terminal.
   Tell them not to paste it here.
3. Check it, through `bash -ic '…'` if the variable shows as not set:
   `python3 install/jev_check.py probe`, then `python3 install/jev_check.py ask jev-1.14.0`
   (one question, ~$0.00002). Use a versioned model; plain `jev` is not a model name.
4. `$PY install/configure.py --jev-mode direct --jev-model jev-1.14.0`

## Step 5 — the category-definer

Most categories never need it: the cloud server shares every category's
definition, and a new laptop downloads them on first use. When a category is new
everywhere, the definer drafts a definition for the user to edit. Its `mode`
picks the steps, with one retry for each site:

- **dev** (the default): ChatGPT → Gemini. Each site opens a visible browser
  window for about a minute, so it needs a desktop session.
- **prod**: Gemini headless (no window). Use this on a server or any machine
  without a desktop. ChatGPT isn't in prod because it blocks headless browsers.

When every step fails, the user types the definition in the Upload tab's Add box.

The sites are used through their free web chats, with no account and no key.
`category_definition_builder` has Playwright (from requirements.txt) drive the
installed Chrome, else Edge, else Playwright's own Chromium. No extension is
needed.

**Ask** which mode fits this machine. Then set it:
```bash
$PY install/configure.py --definer-mode prod    # dev is already the default
```

- If check.py shows neither `chrome` nor `msedge`, **Ask** before
  downloading Playwright's Chromium (about 150 MB, no admin needed):
  `$PY -m playwright install chromium`. Without any browser, drafting always
  fails and the user types new definitions.
- **Ask** before a test draft. It sends "define STEM" to the site, and in
  dev it opens a window. Nothing is saved:
  ```bash
  $PY -m category_definition_builder STEM --only gemini
  ```
  If a site fails, say so and go on: the other site, or typing, still works.

## Step 6 — verify and hand over

```bash
python3 install/check.py --text
$PY -m pytest tests -q       # needs neither pdftotext, a browser nor the cloud server
```

If the transcripts folder already has PDFs, offer to convert them:
`$PY -m upload`.

Then tell the user:
- what was installed and where, and what was skipped because it was already there
- the cloud-url, the token request's id and name, and whether it is approved
  yet; until then, the package owner must approve it
  (`jev_category.admin approve <id>`). The token is in
  `~/.gpa-tools/client_token`, so the app can start from any shell or launcher
- the definer's mode (dev: chatgpt → gemini in windows; prod: gemini headless),
  that each try is logged to `<log-folder>/category_definer.log`, and that a
  failed draft means typing the definition
- how to start the app: `$PY -m browser_view`, then open http://127.0.0.1:5001
  (in VS Code, the "browser-view: web UI" launch entry)
- the first category: on the Upload tab, Add, then a name such as STEM. A
  category the cloud already has comes with its shared definition.

## Removing it again

- Delete `.venv/` and `.tools/` from the project.
- **jev (cloud):** delete `~/.gpa-tools/`, and ask the package owner to revoke `<email>:<hostname>`
  (`python -m jev_category.admin revoke <email>:<hostname>`).
- **jev (direct):** delete the key in the TypeSafe console and remove the `export`
  (on Windows, `setx GPA_TOOLS_KEY ""`, or Settings → environment variables).
- An Ollama installed for an older gpa-tools is no longer used. To remove it:
  Linux `systemctl --user disable --now ollama`, then delete
  `~/.config/systemd/user/ollama.service`, `~/.local/ollama`, `~/.local/bin/ollama`
  and the models in `~/.ollama`; macOS `~/Applications/Ollama.app`; Windows
  Settings → Apps → Ollama. **Ask** first: other programs may use it.

## Troubleshooting

| symptom | cause and fix |
|---|---|
| `pdftotext not found on PATH or in config.yaml's tool-folders` | Step 2 not done, or `tool-folders` points at the wrong folder. Re-run `get_tools.py` and use its last line. |
| `No gpa-tools cloud token on this laptop: its request is waiting for approval` | The package owner hasn't approved it yet. `$PY -m jev_category.enroll status` re-checks. |
| `… was rejected` / `none was requested` | Request again: `$PY -m jev_category.enroll request --email <email>`. |
| `HTTP 401 … token was revoked or is unknown` (cloud) | The owner revoked it. Run `$PY -m jev_category.enroll request --new --email <email>`, and the owner approves again. |
| `the cloud server cannot be reached` / `Cannot reach the gpa-tools cloud` | No internet, or a wrong `cloud-url`. `curl <cloud-url>/v1/info` should answer 401 (up, no token sent). |
| `GPA_TOOLS_KEY is not set` / `The TypeSafe API key is not set` (direct) | The shell or app didn't load the profile. Open a new terminal, or on Linux/macOS run through `bash -ic`. On Windows, `setx` only affects new terminals. |
| `HTTP 401` from TypeSafe (direct) | Wrong or revoked key: create a new one in the console and re-export it. |
| `Unknown model: …` (direct) | Not a TypeSafe model name: `jev_check.py probe` lists aliases; versioned IDs like `jev-1.14.0` work too. |
| `out of funds` (direct) | The TypeSafe balance is $0.00; the user adds funds in the console. |
| `unknown category builder 'llm'` / OUTDATED `category-builder: llm` | config.yaml is from before Ollama was removed: `$PY install/configure.py --active-builder jev`, and delete the llm section by hand. |
| `no usable browser: chrome, msedge, playwright chromium not installed` | No Chrome or Edge where Playwright looks. Run `$PY -m playwright install chromium`, or leave it: the user types new definitions. |
| `Missing X server or $DISPLAY` (dev mode) | No desktop session (SSH, a server). Use `--definer-mode prod`. |
| `no DEFINITION: in the reply`, or timeouts for one site every time | The site changed its page layout or wording. The chain falls back to the next step, then to typing. Fixing it means updating the selectors in `category_definition_builder/browser_legs.py`. |
| `playwright` in check.py's `missing` | Re-run `$PY -m pip install -r requirements.txt`. |
