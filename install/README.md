# install/

Scripts that set gpa-tools up on a new machine. They install everything in
the user's own account, with no admin rights, on Linux, macOS and Windows.

The easy way: start Claude Code in the project root and type

```
/install-gpa-tools
```

Claude follows `.claude/skills/install-gpa-tools/SKILL.md`. It checks the
machine, shows a plan, asks a few questions (data folder, model, Ollama
location), and runs the scripts below.

## The scripts

| script | does | needs |
|---|---|---|
| `check.py` | Reports OS, RAM, GPU, disk, Python/venv, pdftotext, Ollama (binary, server, models), config.yaml, and the recommended model. Changes nothing. | any Python 3.8+ |
| `get_tools.py` | Installs poppler (pdftotext) into `.tools/` with micromamba. | any Python 3.8+ |
| `get_ollama.py` | Installs Ollama for this user and starts it. Does nothing if a server already answers. | the venv's Python |
| `ollama_check.py` | `probe URL`, `pull URL MODEL`, `ask URL MODEL` (one real yes/no question). | any Python 3.8+ |
| `configure.py` | Creates config.yaml from its built-in template, or updates it: folders, model, ollama-url, tool-folders. | the venv's Python |

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

$PY -m browser_view                                 # http://127.0.0.1:5001
```

## Model choice

`check.py` picks a qwen2.5 size from the hardware:

| machine | model |
|---|---|
| NVIDIA or AMD GPU with 8 GB+ memory, or Apple Silicon with 16 GB+ | `qwen2.5:7b` |
| 8 GB+ RAM otherwise (a CPU-only machine stays on 3b: 7b is about 2.5 times slower) | `qwen2.5:3b` |
| under 8 GB RAM | `qwen2.5:1.5b` |

Changing the model later discards the llm builder's cached answers, so the next
run asks every course again.

## Where things go

| what | where |
|---|---|
| venv | `.venv312/` in the project |
| poppler | `.tools/` in the project (git-ignored), listed under `tool-folders:` in config.yaml |
| Ollama | Linux: `~/.local/ollama`, a systemd user service. macOS: `~/Applications/Ollama.app`. Windows: `%LOCALAPPDATA%\Programs\Ollama` |
| models | `~/.ollama/models` |
| config | `config.yaml` in the project (tracked in git; `configure.py` holds the template for a fresh one) |

Only Linux has been tested end to end. The macOS and Windows steps follow
Ollama's and micromamba's documented per-user installs, but haven't been run.
