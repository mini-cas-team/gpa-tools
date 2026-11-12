---
name: install-from-clone
description: Install gpa-tools' Python code from a fresh GitHub clone - find which Pythons the machine has, build the project's .venv with the best supported one (3.14, else 3.12; else uv fetches 3.14 for this user), install requirements.txt and pytest, and prove it with the test suite. Use when the user has just cloned the repo (https://github.com/mini-cas-team/gpa-tools) and asks to install it, set up the venv or Python, or check which Python the project can use, or when .venv is missing, broken, or built with an unsupported Python. For the rest of the machine (pdftotext, config.yaml, the jev builder and this laptop's cloud token, the definer) hand over to /install-gpa-tools.
---

# Installing gpa-tools from a clone

The user cloned the repo and started Claude Code in its root. This skill gets
the **code** running: a `.venv` with a supported Python and every package,
checked by the tests. `install/setup_venv.py` does the work and decides
everything below; this file says what to run, what to ask and how to report.

## Rules

- **Only the project folder.** The script writes only `.venv/` (git-ignored).
  Never use sudo, apt, brew or an admin prompt. Installing `uv` (step 3) writes
  to the user's home folder: **Ask** first.
- **Show the plan, ask once.** The dry run (step 1) changes nothing. Show it,
  with the download (about 60 MB of packages from PyPI, Playwright being most of
  it), and ask once before the real run.
- **Report faithfully.** If a command fails, show its output and stop. Don't
  retry with a different Python unless the plan below says to.

## Supported Pythons

gpa-tools runs on **Python 3.14 or 3.12**, 3.14 preferred
(`install/check.py` `PYTHON_SUPPORTED`). 3.13, 3.11 and older are not
supported: a dependency (pyee) needs 3.12+, and only 3.14 and 3.12 are tested.
The code is the same on both; `tests/test_python_versions.py` keeps it so.

## Step 0 — is this the clone?

The working folder must hold `requirements.txt`, `install/setup_venv.py` and
`browser_view/`. If it doesn't, tell the user to clone and start here:

```bash
git clone https://github.com/mini-cas-team/gpa-tools.git
cd gpa-tools
claude
```

Any Python 3.8+ can run the script. Try `python3`, then `python`, then `py` (Windows);
on Windows `python` can be the Microsoft Store stub, which runs nothing. If no
Python runs at all, go to step 3 (uv).

## Step 1 — see what the machine has

```bash
python3 install/setup_venv.py --dry-run
```

The first line lists every Python found (`python3.14`, `python3.12`, `python3`,
`python`, and on Windows `py -3.14` / `py -3.12`) with its version, marks the
unsupported ones and says whether `uv` is there. The second line is the plan:

| plan | meaning | next |
|---|---|---|
| `ok` | `.venv` is a supported Python with every package | step 4 |
| `install` | `.venv` is fine, packages are missing | step 2 |
| `create` | no `.venv`; builds one with the named Python | step 2 |
| `uv` | no supported Python; uv will fetch 3.14 for this user | step 2 |
| `rebuild-needed` (exit 2) | `.venv` is an unsupported Python (e.g. 3.13) | **Ask** to replace it: `--rebuild` deletes `.venv` and builds a new one |
| `no-python` (exit 3) | no supported Python and no uv | step 3 |

If the user wants a particular interpreter (say 3.12, to match a server), use
`--python <path or name>`, for example `--python python3.12`. A `.venv` of
another version then needs `--rebuild`.

## Step 2 — build it

Show the plan, **Ask** once, then run the same command without `--dry-run`
(plus `--rebuild` or `--python` if the user chose them):

```bash
python3 install/setup_venv.py
```

It ends with `.venv ready: Python 3.x.y` (exit 0). Run it in the background
with a long timeout if the network is slow.

If `venv` fails with "ensurepip is not available" (Debian/Ubuntu without the
`python3.X-venv` package, which needs admin), the script removes the half-made
`.venv` and switches to uv if uv is there. Otherwise it exits 1: go to step 3.

## Step 3 — no usable Python: uv

**Ask** before installing uv. It goes into the user's home folder (no admin)
and then downloads Python 3.14 for this user:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh                                     # Linux/macOS -> ~/.local/bin/uv
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows
```

The script finds uv in `~/.local/bin` even before it is on PATH. Then go back
to step 1. If no Python ran at all in step 0, run the script with uv's own
Python: `~/.local/bin/uv run --python 3.14 install/setup_venv.py --dry-run`.

## Step 4 — prove it

`$PY` is `.venv/bin/python`, or `.venv/Scripts/python.exe` on Windows.

```bash
$PY -m pytest tests -q        # needs no pdftotext, browser, API key or cloud server
```

All must pass. A failure only on 3.12 (or only on 3.14) is a compatibility bug
in the code: show it to the user. Don't work around it by switching Pythons.

## Step 5 — hand over

Tell the user:
- which Pythons were found, which one `.venv` uses and why (3.14 preferred;
  3.12 used when it's the only supported one; uv when neither was there)
- what was installed, and the test result
- what's still missing for a working app. On the `prod` branch: run
  `/install-gpa-tools` next for
  pdftotext, `config.yaml` folders, the jev builder and this laptop's cloud
  token (it skips the venv, which is now done). On `master` (a developer): make
  config.yaml with `$PY install/configure.py --data <folder>` and set
  `run_mode: dev` in it
- the app starts with `$PY -m browser_view`, then http://127.0.0.1:5001

## Troubleshooting

| symptom | cause and fix |
|---|---|
| `rebuild-needed: .venv is Python (broken)` | `.venv` points at a Python that was removed or upgraded. **Ask**, then `--rebuild`. |
| `pip` can't reach PyPI | Offline or behind a proxy: the user sets `HTTPS_PROXY`, or installs later. |
| `No matching distribution found for pyee` | That interpreter is older than 3.12: use a supported one (`--python`). |
| Only `python3` found and it's 3.13 | Not supported. Install 3.14 or 3.12 the OS's way (admin), or let uv fetch 3.14 (step 3). |
| On Windows `python` opens the Store | The Store stub. Use `py -3.14` / `py -3.12`, which the script already tries, or uv. |
