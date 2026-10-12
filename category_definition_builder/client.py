"""BrowserClient: a category definition from ChatGPT or Gemini.

Chat sites answer in free text, often with a preamble ("Searched the web")
or a closing offer, so the prompt asks for the definition between
``DEFINITION:`` and ``END`` and only that part is kept.  A reply without it,
or one too short or too long to be a definition, is an error (NoDefinition),
so the definer goes on to its next try instead of saving chatter.
"""

from __future__ import annotations

import re
from pathlib import Path

from llm_category.llm import DEFINE_PROMPT

from . import pipeline

# One line on purpose: the legs type it and Enter sends it.
BROWSER_PROMPT = DEFINE_PROMPT + (
    " Answer from general knowledge; do not search the web. Reply with only the definition "
    "as plain text on one line, starting with DEFINITION: and ending with END."
)
MIN_CHARS, MAX_CHARS = 40, 2000

_MARKED = re.compile(r"DEFINITION:\s*(?P<text>.*?)\s*(?:\bEND\b|$)", re.S)


class NoDefinition(ValueError):
    """The reply did not contain a usable definition."""


def extract(reply: str) -> str:
    """The definition in a ``DEFINITION: ... END`` reply, as one clean line."""
    match = _MARKED.search(reply.replace("**", ""))
    if not match:
        raise NoDefinition(f"no DEFINITION: in the reply: {_clip(reply)!r}")
    text = " ".join(match.group("text").split()).strip(" \"'`*")
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        raise NoDefinition(f"the definition is {len(text)} characters "
                           f"(expected {MIN_CHARS}-{MAX_CHARS}): {_clip(text)!r}")
    return text


def _clip(text: str, width: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width - 1] + "…"


class BrowserClient:
    """define(category) through one site's web UI (see browser_legs)."""

    def __init__(self, site: str, profile_root: Path, timeout: float = 120.0,
                 headless: bool = True, browser: str = "auto"):
        if site not in pipeline.LEGS:
            raise ValueError(f"unknown site {site!r} (known: {', '.join(pipeline.LEGS)})")
        self.site, self.profile_root, self.timeout = site, Path(profile_root), timeout
        self.headless, self.browser = headless, browser

    def define(self, category: str) -> str:
        prompt = BROWSER_PROMPT.format(category=category)
        return extract(pipeline.ask(self.site, prompt, self.profile_root, self.timeout,
                                    headless=self.headless, browser=self.browser))
