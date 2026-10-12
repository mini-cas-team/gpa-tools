"""Run one browser leg in its own OS process, with a hard deadline.

A separate process rather than a thread: each leg starts its own Playwright
instance, Playwright's sync API must not share a thread with an event loop
(the web app's request threads, say), and a browser crash or hang then costs
only that attempt -- the parent kills the process at the deadline and moves on.

Copied from llm_compare (pipeline.py): the fan-out to several legs at once
became one leg per call, since the definer tries the sites in turn.
"""

from __future__ import annotations

import multiprocessing as mp
import queue as queue_mod
import time
from pathlib import Path

from .browser_legs import query_chatgpt, query_gemini

LEGS = {
    "chatgpt": query_chatgpt,
    "gemini": query_gemini,
}

# Beyond the leg's own reply timeout: starting the browser, loading the page,
# finding the input box (15 s), closing.
STARTUP_ALLOWANCE = 60.0


class LegError(RuntimeError):
    """A leg failed, crashed or overran; the message says which."""


def _run_leg(leg, question: str, profile_root: str, timeout: float, options: dict,
             results) -> None:
    """Child entry point. Always puts exactly one tuple unless the process dies.

    The leg callable is passed in rather than looked up by name: children are
    spawned, so they re-import this module fresh and would not see a `LEGS`
    substituted in the parent."""
    try:
        results.put((leg(question, Path(profile_root), timeout, **options), None))
    except Exception as e:  # reported to the parent, not swallowed
        results.put((None, f"{type(e).__name__}: {e}"))


def ask(site: str, question: str, profile_root: Path, timeout: float = 120.0,
        legs: dict | None = None, **options) -> str:
    """The site's reply to ``question``; LegError when there is none.

    ``options`` go to the leg: headless, browser."""
    leg = (LEGS if legs is None else legs)[site]
    ctx = mp.get_context("spawn")
    results = ctx.Queue()
    proc = ctx.Process(target=_run_leg, args=(leg, question, str(profile_root), timeout, options, results),
                       name=f"definer-{site}")
    proc.start()

    end = time.monotonic() + timeout + STARTUP_ALLOWANCE
    outcome = None
    while time.monotonic() < end:
        try:
            outcome = results.get(timeout=1.0)
            break
        except queue_mod.Empty:
            # a child can die without putting anything (hard browser crash)
            if not proc.is_alive():
                try:
                    outcome = results.get(timeout=1.0)
                except queue_mod.Empty:
                    pass
                break

    overran = outcome is None and time.monotonic() >= end
    proc.join(timeout=5)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5)
    if outcome is None:
        if overran:
            raise LegError(f"no reply within {timeout + STARTUP_ALLOWANCE:.0f}s; stopped")
        raise LegError(f"no result (process exit code {proc.exitcode})")
    text, error = outcome
    if error is not None:
        raise LegError(error)
    return text
