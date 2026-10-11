---
name: install-gpa-tools
description: Install gpa-tools on this machine and get it running - Python venv and packages, pdftotext, Ollama, an LLM model sized to the machine's RAM and GPU, the optional TypeSafe jev builder (API key in an environment variable), and config.yaml (folders, model, ollama-url, active builder). Use when the user asks to install, set up or repair gpa-tools or its dependencies, to add or fix the jev builder, when config.yaml is missing, or when Ollama/the model/TypeSafe is not reachable.
---

# Installing gpa-tools

The user has cloned the repo and started Claude Code in its root. Get them to a
working `python -m browser_view`. The scripts in `install/` do the work and
report in plain text or JSON; this file says in what order and what to ask.

## Rules

- **User account only.** Never use sudo, an admin prompt, apt, dnf, brew
  installs that need admin, or system-wide paths. Every step below has a
  per-user route. If something seems to need admin, stop and tell the user.
- **Check before changing.** Run `install/check.py` first and skip whatever is
  already there and working. Run it again at the end.
- **One plan, one confirmation.** After the check, show the user the plan:
  what will be installed, where, and the download sizes (Ollama 1.0–1.6 GB, the
  model 1–5 GB). Ask once before starting. Ask again only for the questions
  marked **Ask** below.
- **Report faithfully.** If a step fails, show its output and stop that step.
  Don't paper over it.
- **Never handle the TypeSafe API key.** Don't create it, ask for it in chat,
  print it, or write it into any file. The user creates it and exports it
  themselves; config.yaml holds only the variable's *name* (`api-key-env`).

## Paths per OS

| | Linux / macOS | Windows (Claude Code runs Git Bash) |
|---|---|---|
| any Python to start with | `python3` | `py` or `python` |
| the venv's Python, `$PY` below | `.venv312/bin/python` | `.venv312/Scripts/python.exe` |

On Windows, `python` can be the Microsoft Store stub, which runs nothing.
`check.py` only lists interpreters that actually report a version.

## Step 0 — check

```bash
python3 install/check.py --text      # summary for you and the user
python3 install/check.py             # JSON, when you need a detail
```

It reports the OS, RAM, GPU, free disk, Python and venv, pdftotext, the
Ollama binary, whether a server answers at
`http://localhost:11434` (and at any `ollama-url` already in config.yaml) with
its models, the config.yaml folders, the llm builder's and the definer's
model, `recommended_model`, and the jev builder: its model, whether the key
variable is set in this shell (never its value) and, if so, whether TypeSafe
accepts the key and the model. If `config.outdated` is not empty, config.yaml
is from before category builders: tell the user which keys are no longer read,
then run configure.py with the model and URL they had there and delete the
old block once the user agrees.

If no Python runs at all, do step 1b first and then run `check.py` with
`uv run --python 3.12 install/check.py --text`.

## Step 1 — Python venv and packages

Skip this step if `venv exists` and `missing: nothing`.

a. **Python 3.12 present** (`python3.12`, or `py -3.12` on Windows):
   ```bash
   python3.12 -m venv .venv312
   $PY -m pip install -r requirements.txt pytest
   ```
   On Debian/Ubuntu, `venv` can fail with "ensurepip is not available".
   That means the `python3.12-venv` apt package is missing, which needs admin.
   Use b instead.

b. **No Python 3.12** (or a failed a): install `uv` for this user, and let it
   fetch Python 3.12 for this user too:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh                                     # Linux/macOS -> ~/.local/bin/uv
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows
   uv venv --python 3.12 .venv312
   uv pip install --python .venv312 -r requirements.txt pytest
   ```
   A fresh `uv` isn't on PATH in the current shell yet. Call it by full
   path: `~/.local/bin/uv`, or `~/.local/bin/uv.exe` on Windows.

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
gets the subfolders `transcripts/` (PDFs), `work/` (converted transcripts,
and one `work/<builder>/` folder per category builder), `out/` (the published
`<cat>_gpa.csv` files other applications read) and `log/`. Also ask
whether they already have a folder of transcript PDFs to use as `pdf-folder`.

```bash
$PY install/configure.py --data ~/gpa-tools-data
$PY install/configure.py --data ~/gpa-tools-data --pdf-folder "/path/to/their/pdfs"
```

Without a config.yaml, this creates one from the template built into `configure.py`. If one
already exists with folders that exist (check.py says so), **Ask** before
changing them. `configure.py` only rewrites the keys it's given and creates the
folders. config.yaml is tracked in git, so mention that the change shows up in `git status`.

## Step 4 — Ollama and its URL

Look at check.py's `ollama` section.

1. **A server answers at `http://localhost:11434`:** use that URL. Go to step 5.
2. **The binary is installed but nothing answers:** start it, wait a few
   seconds, and probe again.
   - Linux with the user service: `systemctl --user start ollama`.
     Otherwise run `~/.local/ollama/bin/ollama serve` (or the binary
     check.py found) in the background.
   - macOS: `open -a Ollama`, or `open ~/Applications/Ollama.app`.
   - Windows: start `"$LOCALAPPDATA/Programs/Ollama/ollama app.exe"`.
   ```bash
   python3 install/ollama_check.py probe http://localhost:11434
   ```
3. **No Ollama:** **Ask** whether to install it on this machine, or use an
   Ollama already running on another computer.
   - Install:
     ```bash
     $PY install/get_ollama.py --dry-run     # show the user the steps
     $PY install/get_ollama.py               # Linux with an AMD GPU: add --rocm
     ```
     - **Linux:** it unpacks the official archive into `~/.local/ollama` and
       runs a systemd *user* service. Without the `zstd` command, run
       `$PY -m pip install zstandard` first. A user service stops at logout
       unless lingering is on; **Ask** before running
       `loginctl enable-linger "$USER"`, which needs no admin on most distros.
     - **macOS:** it installs into `~/Applications`. If Ollama offers to
       install its command line tool with the admin password, the user can
       decline.
     - **Windows:** the official installer, per-user, silent.
   - Another computer: **Ask** for its URL, for example `http://192.168.1.20:11434`.
     ```bash
     python3 install/ollama_check.py probe <url>
     ```
     If it doesn't answer, show the error and **Ask** again. A server on
     another machine must listen beyond localhost (`OLLAMA_HOST=0.0.0.0` set
     on that machine). Keep asking until it answers or the user stops.

The URL that answered is the one to save.

## Step 5 — the model

Use check.py's `recommended_model`. These are qwen2.5 sizes, so answers stay
comparable with earlier runs:

| machine | model |
|---|---|
| NVIDIA/AMD GPU with 8 GB+ memory, or Apple Silicon with 16 GB+ | `qwen2.5:7b` |
| 8 GB+ RAM otherwise (CPU-only machines included: 7b is ~2.5x slower on CPU) | `qwen2.5:3b` |
| under 8 GB RAM | `qwen2.5:1.5b` |

**Ask** the user to accept it or pick another, and show the reason check.py
gives. If config.yaml already names a different model, say that switching
discards the llm builder's cached answers, so the next run asks every course
again (hours on a CPU). `--model` sets both `category-builder.llm` and
`category-definer`; add `--definer-model <bigger>` only if the user wants
drafts from a stronger model than the classifier.

```bash
python3 install/ollama_check.py pull <url> <model>   # skip if the server already lists it; minutes
python3 install/ollama_check.py ask  <url> <model>   # one real yes/no question; the first loads the model
$PY install/configure.py --model <model> --ollama-url <url>
```

If the user wants the definer on a bigger model, `pull` that one too and add
`--definer-model <it>`; otherwise the definer uses the same model.

Run `pull` in the background with a long timeout, and tell the user it's
downloading.

## Step 6 — the jev builder (TypeSafe), optional

Two category builders decide which courses are in a category:

- **llm** — the local Ollama model from steps 4–5. Free and offline, but slow
  on a CPU (1.5–2 hours for ~400 courses) and a small model misses obvious
  courses (Data Structures, College Algebra).
- **jev** — TypeSafe's hosted Jev model. Needs internet, a TypeSafe account
  and an API key. ~400 courses take a few seconds and cost well under a cent
  ($0.042 per million input tokens, output free). It gives each course a 0–1
  score, kept in `<cat>_courses.csv`.

**Ask** whether to set up jev, and if so whether it should be the active
builder (the one the Upload and View tabs start with). The default is llm.
If check.py already shows jev with the key set and the model accepted, skip
to the `ask` below.

1. The user creates a key: TypeSafe console (https://console.typesafe.ai) →
   **API Keys**, then copies it. The account needs a balance; the console
   shows it, and runs fail with "out of funds" at $0.00.
2. The user stores it in an environment variable, `GPA_TOOLS_KEY` by default,
   **themselves** — tell them not to paste it here:
   - Linux/macOS: `export GPA_TOOLS_KEY="<key>"` in `~/.bashrc` or `~/.zshrc`,
     then a new terminal.
   - Windows: `setx GPA_TOOLS_KEY "<key>"` in a terminal, then a new terminal.

   If they picked another name, use it as `--jev-key-env` / `--key-env` below.
3. Check it. Claude Code's shell may not read the profile, so on Linux/macOS
   run these through `bash -ic '…'` (or `zsh -ic`) if the variable shows as
   not set:
   ```bash
   python3 install/jev_check.py probe                 # free: is the key accepted
   python3 install/jev_check.py ask jev-1.13.0        # one real question, ~$0.00002
   ```
   Use a versioned model (`jev-1.13.0`) so cached scores don't change under
   the user when TypeSafe ships a new release; `jev-latest` follows releases.
   Plain `jev` is not a model name: the API answers "Unknown model: jev".
4. Save it:
   ```bash
   $PY install/configure.py --jev-model jev-1.13.0                        # keep llm active
   $PY install/configure.py --jev-model jev-1.13.0 --active-builder jev   # make jev active
   ```

The definer that drafts missing category definitions still uses Ollama, so
keep steps 4–5 even with jev active, unless the user will write every
definition by hand (`llm_category/archive/definitions.yaml` has STEM).

## Step 7 — verify and hand over

```bash
python3 install/check.py --text
$PY -m pytest tests -q       # needs neither Ollama nor pdftotext
```

If the transcripts folder already has PDFs, offer to convert them:
`$PY -m upload`.

Then tell the user:
- what was installed and where, and what was skipped because it was already there
- the model and the ollama-url saved in config.yaml (builder `llm`, and the
  definer's model if different)
- if jev was set up: its model, the key variable's name, and which builder is
  active. The app reads the key at run time, so it must start from a shell
  where the variable is set (not from a launcher that skips the profile)
- how to start the app: `$PY -m browser_view`, then open http://127.0.0.1:5001
  (in VS Code, the "browser-view: web UI" launch entry)
- the first category: on the Upload tab, Add, then a name such as STEM.
  `llm_category/archive/definitions.yaml` has a ready-made STEM definition.

## Removing it again

- Delete `.venv312/` and `.tools/` from the project.
- **Linux Ollama:** run `systemctl --user disable --now ollama`, then delete
  `~/.config/systemd/user/ollama.service`, `~/.local/ollama` and
  `~/.local/bin/ollama`.
- **macOS:** delete `~/Applications/Ollama.app`.
- **Windows:** use Settings → Apps → Ollama.
- Models are in `~/.ollama/models`.
- **jev:** delete the key in the TypeSafe console and remove the `export` (or, on Windows, `setx GPA_TOOLS_KEY ""` / Settings → environment variables).

## Troubleshooting

| symptom | cause and fix |
|---|---|
| `pdftotext not found on PATH or in config.yaml's tool-folders` | Step 2 not done, or `tool-folders` points at the wrong folder. Re-run `get_tools.py` and use its last line. |
| `Connection refused` at the URL | The server isn't running; see step 4.2. On Linux, `systemctl --user status ollama` and `journalctl --user -u ollama`. |
| `model "…" not found` | Not pulled on *that* server: run `ollama_check.py pull <url> <model>`. |
| The first `ask` takes a minute | Loading the model. Later answers take a few seconds with a GPU, 10–20 s on a CPU. |
| `bind: address already in use` from `ollama serve` | Another Ollama (maybe a system install) already holds port 11434. Use that one. |
| `GPA_TOOLS_KEY is not set` / `The TypeSafe API key is not set` | The shell or app didn't load the profile. Open a new terminal, or on Linux/macOS run through `bash -ic`. On Windows, `setx` only affects new terminals. |
| `HTTP 401` from TypeSafe | Wrong or revoked key: create a new one in the console and re-export it. |
| `Unknown model: …` | Not a TypeSafe model name: `jev_check.py probe` lists aliases; versioned IDs like `jev-1.13.0` work too. |
| `out of funds` | The TypeSafe balance is $0.00; the user adds funds in the console. |
