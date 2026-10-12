"""category-definer: drafts a category definition as a starting point.

Drafting is separate from building: a draft is one call per category, the
user edits it, and every builder then uses the saved text.  ``mode`` picks
the steps, tried in turn until one gives a definition (MODES):

    dev   chatgpt (visible window) -> gemini (visible window) -> ollama
    prod  gemini (headless) -> ollama

chatgpt and gemini are their free web chats, used anonymously through
category_definition_builder in the installed Chrome or Edge (``browser``);
they get ``retries`` extra tries each.  ollama is the local model named by
category-builder: llm: category-definition-model, at that section's ollama-url.

    category-definer:
      mode: dev
      retries: 1
      browser-timeout: 120
      browser: auto

Every draft's attempts, failures included, are appended to
<log-folder>/category_definer.log.
"""

from __future__ import annotations

import time
from pathlib import Path

from category_definition_builder import Draft, DraftFailed, define

from . import llm_models
from .definitions import Definitions

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_NAME = "category_definer.log"
DEFAULTS = {"mode": "dev", "retries": 1, "browser-timeout": 120, "browser": "auto"}
# mode -> (step, headless) in order; headless is None for ollama
MODES = {
    "dev": [("chatgpt", False), ("gemini", False), ("ollama", None)],
    "prod": [("gemini", True), ("ollama", None)],
}
BROWSER_STEPS = ("chatgpt", "gemini")  # the ones retried
OLLAMA_TIMEOUT = 900  # seconds; category-definition-model is picked as large as fits
BROWSERS = ("auto", "chrome", "msedge", "chromium")


def _ollama(cfg: dict):
    from llm_category.llm import OllamaClient

    # A big definition model can take minutes to load and answer on first use.
    return OllamaClient(cfg["ollama-url"], cfg["model"], timeout=OLLAMA_TIMEOUT)


def _browser(site: str):
    def make(cfg: dict):
        from category_definition_builder import BrowserClient

        return BrowserClient(site, cfg["profile-folder"], cfg["browser-timeout"],
                             headless=cfg["headless"][site], browser=cfg["browser"])
    return make


# step -> client factory; a client has define(category) -> str
PROVIDERS = {"chatgpt": _browser("chatgpt"), "gemini": _browser("gemini"), "ollama": _ollama}


def profile_folder(cfg: dict) -> Path:
    """Where the browser steps keep their browser profiles."""
    own = cfg.get("category-definer") or {}
    if own.get("profile-folder"):
        return Path(own["profile-folder"]).expanduser()
    if cfg.get("work-folder"):
        return Path(cfg["work-folder"]).expanduser() / "browser-profiles"
    return PROJECT_ROOT / ".browser-profiles"


def load_config(cfg: dict) -> dict:
    models = llm_models(cfg)
    own = cfg.get("category-definer") or {}
    merged = {**DEFAULTS, **own, "model": models["category-definition-model"],
              "ollama-url": models["definer-ollama-url"], "profile-folder": profile_folder(cfg),
              "log-folder": Path(cfg.get("log-folder") or "log").expanduser()}
    merged.pop("provider", None)  # older config.yaml: provider: ollama, now mode:
    if merged["mode"] not in MODES:
        raise ValueError(f"category-definer mode {merged['mode']!r} is unknown "
                         f"(known: {', '.join(MODES)})")
    if merged["browser"] not in BROWSERS:
        raise ValueError(f"category-definer browser {merged['browser']!r} is unknown "
                         f"(known: {', '.join(BROWSERS)})")
    merged["order"] = [step for step, _ in MODES[merged["mode"]]]
    merged["headless"] = {step: headless for step, headless in MODES[merged["mode"]]
                          if headless is not None}
    merged["retries"] = max(0, int(merged["retries"]))
    return merged


def step_names(cfg: dict) -> list[str]:
    """["chatgpt (window)", "gemini (window)", "ollama"] -- what each step is."""
    return [f"{s} ({'headless' if cfg['headless'][s] else 'window'})" if s in cfg["headless"]
            else s for s in cfg["order"]]


def describe(cfg: dict) -> str:
    """"dev: chatgpt (window) -> gemini (window) -> ollama qwen2.5:3b at http://..."."""
    names = [f"ollama {cfg['model']} at {cfg['ollama-url']}" if n == "ollama" else n
             for n in step_names(cfg)]
    return f"{cfg['mode']}: " + " -> ".join(names)


def steps(cfg: dict) -> list:
    """(step, tries, define) in order. A client is made only when its step is reached."""
    def lazy(name):
        return lambda category: PROVIDERS[name](cfg).define(category)
    return [(name, 1 + cfg["retries"] if name in BROWSER_STEPS else 1, lazy(name))
            for name in cfg["order"]]


def draft(cfg: dict, category: str, only: str | None = None) -> Draft:
    """The definer's proposed definition for ``category``, with every attempt.

    ``only`` keeps just that step of the mode (to check one site by hand).
    Nothing is saved to config.yaml; the attempts go to category_definer.log.
    DraftFailed (an OSError) when every step fails."""
    definer_cfg = load_config(cfg)
    if only:
        if only not in definer_cfg["order"]:
            raise ValueError(f"{only} is not a step of mode {definer_cfg['mode']} "
                             f"({', '.join(definer_cfg['order'])})")
        definer_cfg["order"] = [only]
    try:
        result = define(category, steps(definer_cfg))
    except DraftFailed as exc:
        record(definer_cfg, category, exc.attempts, None)
        raise
    record(definer_cfg, category, result.attempts, result.source)
    return result


def record(cfg: dict, category: str, attempts, source: str | None) -> None:
    """Append one draft's attempts to <log-folder>/category_definer.log."""
    failures = sum(a.error is not None for a in attempts)
    head = (f"drafted by {source}" if source else "every step failed") + \
        f" ({failures} failure(s))"
    lines = [f"{time.strftime('%Y-%m-%d %H:%M:%S')} {category}: {head}"]
    lines += [f"  {a.line()}" for a in attempts]
    try:
        cfg["log-folder"].mkdir(parents=True, exist_ok=True)
        with open(cfg["log-folder"] / LOG_NAME, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass  # the log is a convenience; the attempts also travel with the result


def definition_for(config_path: Path, cfg: dict, category: str) -> tuple[str, bool]:
    """The saved definition, drafting and saving one first if there is none.

    Returns (definition, drafted).
    """
    definitions = Definitions(config_path)
    if category in definitions.data:
        return definitions.data[category].strip(), False
    definition = draft(cfg, category).definition
    definitions.set(category, definition)
    return definition, True
