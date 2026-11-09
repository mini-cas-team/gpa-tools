"""rank_ai: Student Rank's filters from a request typed in plain words.

    from rank_ai import ask
    result = ask(cfg, "<the user's request>", objects)   # summary.ABS objects
    result.params   # Student Rank query params: /view/rank?<urlencode(params)>

The code never looks at what is asked. It sends the LLM

    prompt.build   every filter the objects offer (ABS.filter_specs: fields, their
                   meanings, their known values), their current filters, the request
                   word for word, and the answer format
    providers      ollama, gemini, chatgpt, claude: ask(prompt) -> reply text,
                   tried in config.yaml's rank-ai: providers order
    answer         the JSON object out of the reply, each filter checked against
                   the specs; a problem is told to the same provider, which answers
                   again (up to ``repairs`` times)

and gives each object its filters back (ABS.params) as query params, so the
page shows the result with its usual filters and dialogs.

    rank-ai:
      providers: [ollama]   # tried in turn until one answers
      repairs: 1

Every request, its attempts and its answer go to <log-folder>/rank_ai.log.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from category_definition_builder.chain import Attempt
from summary import ABS, Filter

from . import answer, prompt as prompts
from .providers import DEFAULT_CLAUDE_MODEL, PROVIDERS

LOG_NAME = "rank_ai.log"
DEFAULTS = {"providers": ["ollama"], "repairs": 1, "timeout": 600, "num-ctx": 16384,
            "claude-model": DEFAULT_CLAUDE_MODEL, "claude-api-key-env": "ANTHROPIC_API_KEY"}


def load_config(cfg: dict) -> dict:
    """config.yaml's rank-ai: section over DEFAULTS. The ollama model and URL
    default to the definer's (the larger model); the browser settings to
    category-definer's, with a browser profile folder of their own."""
    from category_builder import definer, llm_models

    own = cfg.get("rank-ai") if isinstance(cfg.get("rank-ai"), dict) else {}
    models = llm_models(cfg)
    definer_cfg = cfg.get("category-definer") if isinstance(cfg.get("category-definer"), dict) else {}
    merged = {**DEFAULTS,
              "model": models["category-definition-model"], "ollama-url": models["definer-ollama-url"],
              "browser-timeout": definer_cfg.get("browser-timeout", definer.DEFAULTS["browser-timeout"]),
              "browser": definer_cfg.get("browser", definer.DEFAULTS["browser"]),
              "profile-folder": definer.profile_folder(cfg) / "rank-ai",
              "log-folder": Path(cfg.get("log-folder") or "log").expanduser(),
              **own}
    order = merged["providers"]
    merged["providers"] = [order] if isinstance(order, str) else list(order or [])
    unknown = [p for p in merged["providers"] if p not in PROVIDERS]
    if unknown or not merged["providers"]:
        raise ValueError(f"rank-ai providers {merged['providers']!r}: each one of {', '.join(PROVIDERS)}")
    merged["repairs"] = max(0, int(merged["repairs"]))
    merged["profile-folder"] = Path(merged["profile-folder"]).expanduser()
    return merged


@dataclass
class Result:
    filters: list[Filter]
    params: list[tuple[str, str]]
    explanation: str
    unsupported: list[str]
    problems: list[str]  # left after the last try: those filters were not applied
    source: str  # the provider that answered
    attempts: list[Attempt] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"filters": [f.to_dict() for f in self.filters], "params": self.params,
                "explanation": self.explanation, "unsupported": self.unsupported,
                "problems": self.problems, "source": self.source,
                "attempts": [a.line() for a in self.attempts]}


class AskFailed(OSError):
    """No provider gave a readable answer."""

    def __init__(self, attempts: list[Attempt]):
        self.attempts = attempts
        tries = "; ".join(a.line() for a in attempts) or "no providers configured"
        super().__init__(f"no answer: {tries}")


def params_for(objects: list[ABS], filters: list[Filter]) -> list[tuple[str, str]]:
    """Each object's filters as its query params, in the objects' order."""
    out: list[tuple[str, str]] = []
    for obj in objects:
        out += obj.params([f for f in filters if f.column == obj.title])
    return out


def ask(cfg: dict, question: str, objects: list[ABS],
        on_attempt: Callable[[Attempt], None] | None = None, today: str | None = None) -> Result:
    """The filters for ``question`` from the first provider that answers.

    A provider that fails (unreachable, timed out, a site change) hands over
    to the next one. One whose answer is unreadable or has problems is asked
    again with the problems (``repairs`` times); after that its usable filters
    are applied and the rest reported in ``problems``."""
    question = (question or "").strip()
    if not question:
        raise ValueError("no request")
    rcfg = load_config(cfg)
    specs = [s for obj in objects for s in obj.filter_specs()]
    current = [f for obj in objects for f in obj.current_filters()]
    descriptions = {obj.title: obj.description for obj in objects}
    base = prompts.build(question, specs, current, descriptions,
                         today if today is not None else time.strftime("%Y-%m-%d"))
    attempts: list[Attempt] = []
    replies: list[str] = []

    def note(attempt: Attempt) -> None:
        attempts.append(attempt)
        if on_attempt:
            on_attempt(attempt)

    for name in rcfg["providers"]:
        text = base
        for n in range(1, rcfg["repairs"] + 2):
            started = time.monotonic()
            took = lambda: round(time.monotonic() - started, 1)  # noqa: E731
            try:
                reply = PROVIDERS[name](rcfg).ask(text)
            except Exception as exc:  # any failure: the next provider
                note(Attempt(name, n, took(), f"{type(exc).__name__}: {exc}"))
                break
            replies.append(reply)
            try:
                checked = answer.check(answer.extract(reply), specs)
            except answer.NoAnswer as exc:
                note(Attempt(name, n, took(), str(exc)))
                text = prompts.repair(base, reply, [str(exc)])
                continue
            if checked.problems and n <= rcfg["repairs"]:
                note(Attempt(name, n, took(), "problems: " + "; ".join(checked.problems)))
                text = prompts.repair(base, reply, checked.problems)
                continue
            note(Attempt(name, n, took()))
            result = Result(checked.filters, params_for(objects, checked.filters),
                            checked.explanation, checked.unsupported, checked.problems,
                            name, attempts)
            record(rcfg, question, attempts, result, replies)
            return result
    record(rcfg, question, attempts, None, replies)
    raise AskFailed(attempts)


def record(rcfg: dict, question: str, attempts: list[Attempt], result: Result | None,
           replies: list[str]) -> None:
    """Append one request to <log-folder>/rank_ai.log."""
    lines = [f"{time.strftime('%Y-%m-%d %H:%M:%S')} request: {json.dumps(question, ensure_ascii=False)}"]
    lines += [f"  {a.line()}" for a in attempts]
    if result is not None:
        lines.append(f"  answer by {result.source}: "
                     + json.dumps({k: v for k, v in result.to_dict().items() if k != "attempts"},
                                  ensure_ascii=False))
    elif replies:
        lines.append(f"  last reply: {json.dumps(replies[-1][:2000], ensure_ascii=False)}")
    try:
        rcfg["log-folder"].mkdir(parents=True, exist_ok=True)
        with open(rcfg["log-folder"] / LOG_NAME, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass  # the log is a convenience; the attempts also travel with the result
