"""category-definer: drafts a category definition as a starting point.

Drafting is separate from building: a draft is one call per category, the
user edits it, and every builder then uses the saved text.  The definer has
its own section in config.yaml, so it can use a different (stronger) model
or, later, a different provider from the llm builder:

    category-definer:
      provider: ollama
      model: qwen2.5:14b
      ollama-url: http://localhost:11434

Keys left out fall back to the llm builder's model and ollama-url.
"""

from __future__ import annotations

from pathlib import Path

from . import settings
from .definitions import Definitions

DEFAULTS = {"provider": "ollama", "model": "qwen2.5:3b", "ollama-url": "http://localhost:11434"}


def _ollama(cfg: dict):
    from llm_category.llm import OllamaClient

    return OllamaClient(cfg["ollama-url"], cfg["model"])


# provider -> client factory; a client has define(category) -> str
PROVIDERS = {"ollama": _ollama}


def load_config(cfg: dict) -> dict:
    llm = {k: v for k, v in settings(cfg, "llm").items() if k in ("model", "ollama-url")}
    merged = {**DEFAULTS, **llm, **(cfg.get("category-definer") or {})}
    if merged["provider"] not in PROVIDERS:
        raise ValueError(f"unknown category-definer provider {merged['provider']!r} "
                         f"(known: {', '.join(PROVIDERS)})")
    return merged


def describe(cfg: dict) -> str:
    """"qwen2.5:3b at http://..." -- for error messages and logs."""
    return f"{cfg['model']} at {cfg.get('ollama-url', cfg['provider'])}"


def client(cfg: dict):
    return PROVIDERS[cfg["provider"]](cfg)


def draft(cfg: dict, category: str) -> str:
    """The definer's proposed definition for ``category``. Nothing is saved."""
    return client(load_config(cfg)).define(category)


def definition_for(config_path: Path, cfg: dict, category: str) -> tuple[str, bool]:
    """The saved definition, drafting and saving one first if there is none.

    Returns (definition, drafted).
    """
    definitions = Definitions(config_path)
    if category in definitions.data:
        return definitions.data[category].strip(), False
    definition = draft(cfg, category)
    definitions.set(category, definition)
    return definition, True
