"""ChatGPT and Gemini legs: drive the consumer web UIs anonymously (no login,
free tier) through a persistent browser profile per site.

The browser is the installed Chrome, else the installed Edge, else
Playwright's own Chromium (``playwright install chromium``) -- see BROWSERS.
Gemini answers in a headless browser; ChatGPT stops a headless one at its
bot check, so it needs headless=False: a visible window on a desktop.

The selectors here are consumer web UI internals with no stability
guarantee; expect to update them when a site changes its layout.

Copied from llm_compare (browser_legs.py) and trimmed: no Claude leg, the
profile folder, browser and headless mode are passed in, and a reply still
growing at the deadline is an error instead of being returned cut off.
"""

from __future__ import annotations

import time
from pathlib import Path

SITES = {
    "chatgpt": "https://chatgpt.com/",
    "gemini": "https://gemini.google.com/app",
}

# The input box, by accessible name.
_PROMPT_BOX = {
    "chatgpt": "Chat with ChatGPT",
    "gemini": "Enter a prompt for Gemini",
}

# Response-container selector to poll for each site's last message.
_RESPONSE_SELECTOR = {
    "chatgpt": "[data-assistant-markdown]",
    "gemini": ".markdown-main-panel",
}

# Present while the site is still answering.  While one matches, text
# stability is ignored -- a pause mid-answer must not be mistaken for the end.
# A miss here is harmless: it degrades to text stability alone.
_BUSY_SELECTOR = {
    "chatgpt": 'button[data-testid="stop-button"]',
}


# browser setting -> Playwright channels tried in turn (None: Playwright's own Chromium)
BROWSERS = {
    "auto": ["chrome", "msedge", None],
    "chrome": ["chrome"],
    "msedge": ["msedge"],
    "chromium": [None],
}
# Playwright's wording when a channel or its own build is not installed
_NOT_INSTALLED = ("is not found", "Executable doesn't exist")


def launch(site: str, profile_root: Path, *, headless: bool, browser: str = "auto"):
    from playwright.sync_api import Error, sync_playwright

    profile = Path(profile_root) / site
    profile.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    missing = []
    try:
        for channel in BROWSERS[browser]:
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(profile),
                    channel=channel,
                    headless=headless,
                    viewport={"width": 1280, "height": 900},
                    chromium_sandbox=True,
                )
                return playwright, context
            except Error as exc:
                if not any(s in str(exc) for s in _NOT_INSTALLED):
                    raise
                missing.append(channel or "playwright chromium")
        raise RuntimeError(f"no usable browser: {', '.join(missing)} not installed")
    except BaseException:
        playwright.stop()
        raise


def _wait_for_stable_text(
    page,
    selector: str,
    timeout: float,
    poll_interval: float = 1.0,
    stable_polls: int = 2,
    busy_selector: str | None = None,
) -> str:
    """Poll a locator's last match until its text stops changing across
    `stable_polls` consecutive polls, i.e. streaming has finished. While
    `busy_selector` matches anything the answer is still being produced, so
    stability is not counted."""
    locator = page.locator(selector).last
    deadline = time.monotonic() + timeout
    last_text = None
    stable_count = 0
    while time.monotonic() < deadline:
        page.wait_for_timeout(int(poll_interval * 1000))
        if busy_selector and page.locator(busy_selector).count() > 0:
            stable_count = 0
            continue
        if locator.count() == 0:
            continue
        text = locator.inner_text()
        if text and text == last_text:
            stable_count += 1
            if stable_count >= stable_polls:
                return text
        else:
            stable_count = 0
        last_text = text
    if last_text:
        raise TimeoutError(f"the reply was still changing after {timeout:.0f}s "
                           f"({len(last_text)} characters so far)")
    raise TimeoutError(f"no reply text for selector {selector!r} within {timeout:.0f}s")


def query(site: str, question: str, profile_root: Path, timeout: float = 120.0,
          headless: bool = True, browser: str = "auto") -> str:
    """Ask ``site`` one question in a fresh chat and return the reply text.

    ``question`` must be one line: Enter sends it."""
    playwright, context = launch(site, profile_root, headless=headless, browser=browser)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(SITES[site])
        box = page.get_by_role("textbox", name=_PROMPT_BOX[site])
        box.wait_for(timeout=15000)
        box.click()
        page.keyboard.type(question)
        page.keyboard.press("Enter")
        return _wait_for_stable_text(page, _RESPONSE_SELECTOR[site], timeout,
                                     busy_selector=_BUSY_SELECTOR.get(site))
    finally:
        context.close()
        playwright.stop()


def query_chatgpt(question: str, profile_root: Path, timeout: float = 120.0,
                  headless: bool = False, browser: str = "auto") -> str:
    return query("chatgpt", question, profile_root, timeout, headless, browser)


def query_gemini(question: str, profile_root: Path, timeout: float = 120.0,
                 headless: bool = True, browser: str = "auto") -> str:
    return query("gemini", question, profile_root, timeout, headless, browser)
