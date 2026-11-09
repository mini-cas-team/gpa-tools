"""The LLMs rank_ai can ask. Each provider is a class with ask(prompt) -> the
reply text; what the reply means is answer.py's job, the same for all of them.

    ollama    the local model (default category-builder: llm: category-definition-model),
              its output held to answer.SCHEMA
    gemini    gemini.google.com's free chat, anonymously, in the installed Chrome/Edge
    chatgpt   chatgpt.com's free chat, the same way (a visible window only)
    claude    the Anthropic Messages API; the key is in the environment variable
              claude-api-key-env names, never in config.yaml

To add one: a class with ask(), and a line in PROVIDERS.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from . import answer

DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
# The browser steps: ChatGPT stops a headless browser at its bot check.
HEADLESS = {"gemini": True, "chatgpt": False}


def _post(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:  # the body says why (bad model, bad key, ...)
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise OSError(f"{url}: HTTP {exc.code}: {detail}") from exc


class Ollama:
    def __init__(self, cfg: dict):
        self.url, self.model = cfg["ollama-url"].rstrip("/"), cfg["model"]
        self.timeout, self.num_ctx = cfg["timeout"], cfg["num-ctx"]

    def ask(self, prompt: str) -> str:
        body = {"model": self.model, "prompt": prompt, "stream": False, "format": answer.SCHEMA,
                # the prompt lists every known value, longer than Ollama's default context
                "options": {"temperature": 0, "num_ctx": self.num_ctx}, "keep_alive": "30m"}
        return _post(f"{self.url}/api/generate", body, {}, self.timeout)["response"]


class Browser:
    def __init__(self, cfg: dict, site: str):
        self.site, self.cfg = site, cfg

    def ask(self, prompt: str) -> str:
        from category_definition_builder import pipeline

        return pipeline.ask(self.site, prompt, Path(self.cfg["profile-folder"]),
                            self.cfg["browser-timeout"], headless=HEADLESS[self.site],
                            browser=self.cfg["browser"])


class Claude:
    def __init__(self, cfg: dict):
        self.model, self.env, self.timeout = cfg["claude-model"], cfg["claude-api-key-env"], cfg["timeout"]

    def ask(self, prompt: str) -> str:
        key = os.environ.get(self.env)
        if not key:
            raise OSError(f"no API key: set the environment variable {self.env}")
        body = {"model": self.model, "max_tokens": 2048,
                "messages": [{"role": "user", "content": prompt}]}
        reply = _post(CLAUDE_URL, body, {"x-api-key": key, "anthropic-version": "2023-06-01"},
                      self.timeout)
        return "".join(part.get("text", "") for part in reply.get("content", [])
                       if part.get("type") == "text")


# name -> provider factory (rank_ai's merged config) -> an object with ask(prompt)
PROVIDERS = {
    "ollama": Ollama,
    "gemini": lambda cfg: Browser(cfg, "gemini"),
    "chatgpt": lambda cfg: Browser(cfg, "chatgpt"),
    "claude": Claude,
}
