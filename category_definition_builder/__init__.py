"""category_definition_builder: drafts a category definition from its name.

Asks ChatGPT or Gemini through their free web chats, anonymously, in the
installed Chrome or Edge (Gemini headless; ChatGPT only in a visible window).
category_builder.definer picks the steps from config.yaml's mode and falls
back to the local Ollama model.  Started as a copy of llm_compare (~/tmp/browser).

    python -m category_definition_builder STEM     # print a draft; nothing is saved

    browser_legs.py   drive chatgpt.com / gemini.google.com
    pipeline.py       one leg per OS process, with a hard deadline
    client.py         BrowserClient.define(): the prompt and the DEFINITION: ... END reply
    chain.py          try steps in turn, recording every attempt
"""

from .chain import Attempt, Draft, DraftFailed, define
from .client import BrowserClient, NoDefinition

__all__ = ["Attempt", "BrowserClient", "Draft", "DraftFailed", "NoDefinition", "define"]
