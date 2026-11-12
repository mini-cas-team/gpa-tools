# install/

Scripts that set gpa-tools up on a new machine. They install everything in
the user's own account, with no admin rights, on Linux, macOS and Windows.

The easy way: start Claude Code in the project root and type

```
/install-gpa-tools
```

Claude follows `.claude/skills/install-gpa-tools/SKILL.md`. It checks the
machine, shows a plan, asks a few questions (data folder, your email for this
laptop's cloud token, the definer's mode), and runs the scripts below.

## The scripts

| script | does | needs |
|---|---|---|
| `check.py` | Reports OS, RAM, disk, Python/venv, pdftotext, Google Chrome and Edge, config.yaml (including the definer's mode), and the jev builder: the cloud server and where this laptop's token comes from. Changes nothing. | any Python 3.8+ |
| `setup_venv.py` | Builds `.venv` with the best Python found (3.14, else 3.12; else uv fetches 3.14 for this user) and installs `requirements.txt` and pytest. `--dry-run` shows the plan; `--rebuild` replaces a venv of an unsupported Python; `--python X` uses that one. Never installs uv or a Python itself. Exit 2: rebuild needed, 3: no supported Python and no uv. | any Python 3.8+ |
| `get_tools.py` | Installs poppler (pdftotext) into `.tools/` with micromamba. | any Python 3.8+ |
| `jev_check.py` | Direct mode only (development): `probe` (is the TypeSafe key accepted; free), `ask [MODEL]` (one real question). Reads the key from `$GPA_TOOLS_KEY` (`--key-env` for another name) and never prints it. | any Python 3.8+ |
| `configure.py` | Creates config.yaml from its built-in template, or updates it: folders, tool-folders, `category-builder: jev:` (`--jev-mode cloud\|direct`, `--cloud-url`, `--jev-model`, `--jev-key-env`), `--active-builder jev`, and `--definer-mode dev\|prod` (prod on a machine without a desktop). | the venv's Python |

## By hand

`$PY` is `.venv/bin/python`, or `.venv/Scripts/python.exe` on Windows.

```bash
python3 install/check.py --text

python3 install/setup_venv.py                      # .venv with Python 3.14 or 3.12, and the packages

python3 install/get_tools.py                        # only if check.py found no pdftotext
$PY install/configure.py --tool-folder <folder it printed last>

$PY install/configure.py --data ~/gpa-tools-data    # transcripts/ work/ out/ log/; jev in cloud mode

$PY -m jev_category.enroll request --email you@school.edu   # this laptop's token: the owner approves it
$PY -m jev_category.enroll status                           # approved?

$PY -m browser_view                                 # http://127.0.0.1:5001
```

## The category-definer

`mode: dev` tries ChatGPT, then Gemini, in visible browser windows. `mode: prod`
tries Gemini headless. Both use the sites' free web chats (Playwright with the
installed Chrome or Edge; no account, no key). When every step fails, the user
types the definition in the Upload tab. Most categories never need a draft:
the cloud server already shares their definitions. Check a step with
`$PY -m category_definition_builder STEM --only gemini`; on a machine without a
desktop, use `configure.py --definer-mode prod`.

## Where things go

| what | where |
|---|---|
| venv | `.venv/` in the project |
| definer's Chrome profiles | `<work-folder>/browser-profiles/` (`profile-folder:` under `category-definer:` to move them) |
| poppler | `.tools/` in the project (git-ignored), listed under `tool-folders:` in config.yaml |
| this laptop's cloud token | `~/.gpa-tools/` (`client_token`, or `pending_token` until approved) |
| TypeSafe API key (direct mode, development only) | an environment variable (`GPA_TOOLS_KEY`) in the user's shell profile; config.yaml only names it |
| config | `config.yaml` in the project (tracked in git; `configure.py` holds the template for a fresh one) |

Only Linux has been tested end to end. The macOS and Windows steps follow
micromamba's documented per-user install, but haven't been run.
