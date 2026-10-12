# install/

Scripts that set gpa-tools up on a new machine. They install everything in
the user's own account, with no admin rights, on Linux, macOS and Windows.

The easy way: start Claude Code in the project root and type

```
/install-gpa-tools
```

Claude follows `.claude/skills/install-gpa-tools/SKILL.md`. It checks the
machine, shows a plan, asks a few questions (data folder, model, Ollama
location, whether to add the TypeSafe jev builder), and runs the scripts below.

## The scripts

| script | does | needs |
|---|---|---|
| `check.py` | Reports OS, RAM, GPU, disk, Python/venv, pdftotext, Google Chrome and Edge, Ollama (binary, server, models), config.yaml (including the definer's mode), and the recommended model. Changes nothing. | any Python 3.8+ |
| `get_tools.py` | Installs poppler (pdftotext) into `.tools/` with micromamba. | any Python 3.8+ |
| `get_ollama.py` | Installs Ollama for this user and starts it. Does nothing if a server already answers. | the venv's Python |
| `ollama_check.py` | `probe URL`, `pull URL MODEL`, `ask URL MODEL` (one real yes/no question). | any Python 3.8+ |
| `jev_check.py` | `probe` (is the TypeSafe key accepted; free), `ask [MODEL]` (one real question). Reads the key from `$GPA_TOOLS_KEY` (`--key-env` for another name) and never prints it. | any Python 3.8+ |
| `configure.py` | Creates config.yaml from its built-in template, or updates it: folders, tool-folders, the two Ollama models of `category-builder: llm:` (`--model` sets `category-build-model` and `category-definition-model`; `--definer-model` gives the definition model its own) and its `--ollama-url`, `category-builder: jev:` (`--jev-model`, `--jev-key-env`), `--active-builder llm\|jev`, and `--definer-mode dev\|prod` (prod on a machine without a desktop). | the venv's Python |

## By hand

`$PY` is `.venv312/bin/python`, or `.venv312/Scripts/python.exe` on Windows.

```bash
python3 install/check.py --text

python3.12 -m venv .venv312                         # or: uv venv --python 3.12 .venv312
$PY -m pip install -r requirements.txt pytest

python3 install/get_tools.py                        # only if check.py found no pdftotext
$PY install/configure.py --tool-folder <folder it printed last>

$PY install/configure.py --data ~/gpa-tools-data    # transcripts/ work/ out/ log/

$PY install/get_ollama.py --dry-run                 # only if no Ollama answers
$PY install/get_ollama.py

python3 install/ollama_check.py pull http://localhost:11434 qwen2.5:3b
python3 install/ollama_check.py ask  http://localhost:11434 qwen2.5:3b
$PY install/configure.py --model qwen2.5:3b --ollama-url http://localhost:11434

# optional: the jev builder. Create a key in https://console.typesafe.ai (API Keys),
# put `export GPA_TOOLS_KEY="<key>"` in ~/.bashrc (Windows: setx), open a new shell.
python3 install/jev_check.py probe
python3 install/jev_check.py ask jev-1.13.0
$PY install/configure.py --jev-model jev-1.13.0 --active-builder jev

$PY -m browser_view                                 # http://127.0.0.1:5001
```

## Model choice

`check.py` picks two qwen2.5 sizes from the hardware (`recommended_models`):

- **build** (`category-build-model`) answers ~400 questions a run, so it is the
  fast one: the biggest that fits entirely in GPU memory (up to 14b), or 3b
  without a GPU (1.5b under 8 GB RAM).
- **definition** (`category-definition-model`) is asked once per category, so it
  is as large as fits: GPU memory plus 70% of RAM, up to 32b (72b on a 48 GB+
  GPU), or up to 14b without a GPU.

| machine | build | definition |
|---|---|---|
| no GPU, 12 GB RAM | 3b | 7b |
| no GPU, 32 GB+ RAM | 3b | 14b |
| GPU 8 GB, 16 GB RAM | 7b | 14b |
| GPU 12–24 GB | 14b | 32b |

Save them with `configure.py --model <build> --definer-model <definition>`
(pull both). Changing the build model later discards the llm builder's cached
answers, so the next run asks every course again; the definition model clears
nothing.
Ollama is the definer's last step. Before it, `mode: dev` tries ChatGPT and
Gemini in visible browser windows, and `mode: prod` tries Gemini headless, through
their free web chats (Playwright with the installed Chrome or Edge; no account,
no key). Check a step with `$PY -m category_definition_builder STEM --only gemini`;
on a machine without a desktop, use `configure.py --definer-mode prod`.

## Where things go

| what | where |
|---|---|
| venv | `.venv312/` in the project |
| definer's Chrome profiles | `<work-folder>/browser-profiles/` (`profile-folder:` under `category-definer:` to move them) |
| poppler | `.tools/` in the project (git-ignored), listed under `tool-folders:` in config.yaml |
| Ollama | Linux: `~/.local/ollama`, a systemd user service. macOS: `~/Applications/Ollama.app`. Windows: `%LOCALAPPDATA%\Programs\Ollama` |
| models | `~/.ollama/models` |
| TypeSafe API key | an environment variable (`GPA_TOOLS_KEY`) in the user's shell profile; config.yaml only names it |
| config | `config.yaml` in the project (tracked in git; `configure.py` holds the template for a fresh one) |

Only Linux has been tested end to end. The macOS and Windows steps follow
Ollama's and micromamba's documented per-user installs, but haven't been run.
