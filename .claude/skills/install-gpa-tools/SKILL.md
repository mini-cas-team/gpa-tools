---
name: install-gpa-tools
description: Install gpa-tools on this machine and get it running - Python venv and packages, pdftotext, Ollama, an LLM model sized to the machine's RAM and GPU, and config.yaml (folders, model, ollama-url). Use when the user asks to install, set up or repair gpa-tools or its dependencies, when config.yaml is missing, or when Ollama/the model is not reachable.
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
its models, the config.yaml folders, and `recommended_model`.

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
gets the subfolders `transcripts/`, `work/`, `out/` and `log/`. Also ask
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

Run `pull` in the background with a long timeout, and tell the user it's
downloading.

## Step 6 — verify and hand over

```bash
python3 install/check.py --text
$PY -m pytest tests -q       # needs neither Ollama nor pdftotext
```

If the transcripts folder already has PDFs, offer to convert them:
`$PY -m upload`.

Then tell the user:
- what was installed and where, and what was skipped because it was already there
- the model and the ollama-url saved in config.yaml
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

## Troubleshooting

| symptom | cause and fix |
|---|---|
| `pdftotext not found on PATH or in config.yaml's tool-folders` | Step 2 not done, or `tool-folders` points at the wrong folder. Re-run `get_tools.py` and use its last line. |
| `Connection refused` at the URL | The server isn't running; see step 4.2. On Linux, `systemctl --user status ollama` and `journalctl --user -u ollama`. |
| `model "…" not found` | Not pulled on *that* server: run `ollama_check.py pull <url> <model>`. |
| The first `ask` takes a minute | Loading the model. Later answers take a few seconds with a GPU, 10–20 s on a CPU. |
| `bind: address already in use` from `ollama serve` | Another Ollama (maybe a system install) already holds port 11434. Use that one. |
