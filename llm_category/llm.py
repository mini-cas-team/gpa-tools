"""Minimal Ollama client: one yes/no question, one free-text definition."""

from __future__ import annotations

import json
import urllib.request

YES_NO_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "enum": ["yes", "no"]}},
    "required": ["answer"],
}
DEFINITION_SCHEMA = {
    "type": "object",
    "properties": {"definition": {"type": "string"}},
    "required": ["definition"],
}

DEFINE_PROMPT = (
    "You are helping classify university courses by subject area. "
    "Write a definition of the course category \"{category}\" in two or three sentences. "
    "State which academic subjects are included, and name the borderline subjects "
    "that are NOT included."
)

CLASSIFY_PROMPT = (
    "Category definition: {definition}\n\n"
    "Using only that definition, is this university course in the category {category}?\n"
    "Course: {code} - {title}\n"
    "Answer yes or no."
)


class OllamaClient:
    def __init__(self, url: str, model: str, timeout: float = 300):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _generate(self, prompt: str, schema: dict) -> dict:
        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
            "keep_alive": "30m",  # keep the model loaded between questions
        }
        req = urllib.request.Request(
            f"{self.url}/api/generate",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(json.loads(resp.read())["response"])

    def define(self, category: str) -> str:
        reply = self._generate(DEFINE_PROMPT.format(category=category), DEFINITION_SCHEMA)
        return reply["definition"].strip()

    def is_member(self, category: str, definition: str, code: str, title: str) -> bool:
        prompt = CLASSIFY_PROMPT.format(
            category=category, definition=definition, code=code, title=title
        )
        return self._generate(prompt, YES_NO_SCHEMA)["answer"] == "yes"
