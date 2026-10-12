"""category_definition_builder without a browser: reply parsing, the per-leg
process, the client and the chain."""

import time

import pytest

from category_definition_builder import (BrowserClient, DraftFailed, NoDefinition, client,
                                         define, pipeline)
from category_definition_builder.browser_legs import _wait_for_stable_text

DEFINITION = "STEM covers biology, chemistry, physics, computer science and mathematics."


# --- reply parsing ----------------------------------------------------------------

def test_extract_keeps_only_the_marked_definition():
    reply = f"Searched the web\n\n**DEFINITION:** {DEFINITION}\n END\n\nWant more detail?"
    assert client.extract(reply) == DEFINITION
    # no END: up to the end of the reply, on one line
    assert client.extract(f"DEFINITION: \"{DEFINITION}\nstill it.\"") == DEFINITION + " still it."


@pytest.mark.parametrize("reply, match", [
    ("Here is a definition of STEM without the marker.", "no DEFINITION"),
    ("DEFINITION: Science. END", "characters"),
    ("DEFINITION: " + "x" * 2001 + " END", "characters"),
])
def test_extract_rejects_replies_without_a_usable_definition(reply, match):
    with pytest.raises(NoDefinition, match=match):
        client.extract(reply)


def test_the_browser_prompt_is_one_line():
    prompt = client.BROWSER_PROMPT.format(category="STEM")
    assert "\n" not in prompt and '"STEM"' in prompt and "DEFINITION:" in prompt


# --- waiting for a streamed reply ---------------------------------------------------

class FakePage:
    """A page whose last reply shows ``texts`` one poll after another
    (or whatever ``texts`` returns, when it is a function)."""

    def __init__(self, texts):
        self.texts = texts if callable(texts) else list(texts)

    def wait_for_timeout(self, ms):
        pass

    def locator(self, selector):
        page = self

        class Loc:
            last = property(lambda self: self)

            def count(self):
                return 0 if selector == "busy" else 1

            def inner_text(self):
                if callable(page.texts):
                    return page.texts()
                return page.texts.pop(0) if len(page.texts) > 1 else page.texts[0]
        return Loc()


def test_wait_returns_text_once_it_stops_changing():
    page = FakePage(["", "ST", "STEM is", "STEM is", "STEM is"])
    assert _wait_for_stable_text(page, "r", timeout=5, poll_interval=0) == "STEM is"


def test_wait_fails_instead_of_returning_a_cut_off_reply():
    grow = iter(range(1, 10 ** 7))
    page = FakePage(lambda: "x" * next(grow))  # never stops growing
    with pytest.raises(TimeoutError, match="still changing"):
        _wait_for_stable_text(page, "r", timeout=0.05, poll_interval=0)


# --- one leg per process (these spawn real processes) ------------------------------

def answers(question, profile_root, timeout, headless=True):
    return f"DEFINITION: {DEFINITION} END" if headless else "headed"


def fails(question, profile_root, timeout):
    raise TimeoutError("no reply text")


def hangs(question, profile_root, timeout):
    time.sleep(60)


def test_ask_returns_the_reply_or_the_leg_error(tmp_path, monkeypatch):
    legs = {"chatgpt": answers, "gemini": fails}
    assert pipeline.ask("chatgpt", "q", tmp_path, 1, legs=legs) == f"DEFINITION: {DEFINITION} END"
    assert pipeline.ask("chatgpt", "q", tmp_path, 1, legs=legs, headless=False) == "headed"
    with pytest.raises(pipeline.LegError, match="TimeoutError: no reply text"):
        pipeline.ask("gemini", "q", tmp_path, 1, legs=legs)
    monkeypatch.setattr(pipeline, "STARTUP_ALLOWANCE", 1.0)
    started = time.monotonic()
    with pytest.raises(pipeline.LegError, match="no reply within 2s; stopped"):
        pipeline.ask("chatgpt", "q", tmp_path, 1, legs={"chatgpt": hangs})
    assert time.monotonic() - started < 20


def test_browser_client_sends_the_prompt_and_extracts(tmp_path, monkeypatch):
    sent = []

    def ask(site, prompt, profile_root, timeout, **options):
        sent.append((site, prompt, profile_root, timeout, options))
        return f"Sure! DEFINITION: {DEFINITION} END"

    monkeypatch.setattr(pipeline, "ask", ask)
    assert BrowserClient("gemini", tmp_path, 30, headless=False, browser="msedge").define("STEM") \
        == DEFINITION
    assert sent == [("gemini", client.BROWSER_PROMPT.format(category="STEM"), tmp_path, 30,
                     {"headless": False, "browser": "msedge"})]
    with pytest.raises(ValueError, match="claude"):
        BrowserClient("claude", tmp_path)


# --- the chain ------------------------------------------------------------------------

def test_define_stops_at_the_first_definition_and_records_each_try():
    tried = []

    def step(name, result):
        def fn(category):
            tried.append(name)
            if isinstance(result, Exception):
                raise result
            return result
        return fn

    seen = []
    draft = define("STEM", [("chatgpt", 2, step("chatgpt", OSError("down"))),
                            ("gemini", 2, step("gemini", "  ")),  # blank counts as a failure
                            ("ollama", 1, step("ollama", " Def. ")),
                            ("never", 1, step("never", "x"))], on_attempt=seen.append)
    assert (draft.definition, draft.source) == ("Def.", "ollama")
    assert tried == ["chatgpt", "chatgpt", "gemini", "gemini", "ollama"]
    assert [a.error for a in draft.failures] == ["OSError: down", "OSError: down",
                                                 "ValueError: empty definition",
                                                 "ValueError: empty definition"]
    assert seen == draft.attempts

    with pytest.raises(DraftFailed, match="no definition for LAW: chatgpt try 1 .* failed"):
        define("LAW", [("chatgpt", 1, step("chatgpt", OSError("down")))])
