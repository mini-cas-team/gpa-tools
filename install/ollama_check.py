"""Talk to an Ollama server: is it there, pull a model, ask one real question.

    python3 install/ollama_check.py probe [URL]
    python3 install/ollama_check.py pull  URL MODEL
    python3 install/ollama_check.py ask   URL MODEL

Standard library only. ``pull`` goes through the server's API, so it also
works for an Ollama on another computer. ``ask`` sends one yes/no question
through llm_category's own client, the same request a real run makes.
Exit status is 0 on success, 1 otherwise.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(PROJECT_ROOT))

from check import DEFAULT_OLLAMA_URL, probe_ollama  # noqa: E402


def pull(url: str, model: str) -> int:
    req = urllib.request.Request(f"{url.rstrip('/')}/api/pull",
                                 data=json.dumps({"model": model, "stream": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    shown, last = None, 0.0
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            for line in resp:
                event = json.loads(line)
                if "error" in event:
                    print(f"error: {event['error']}")
                    return 1
                status = event.get("status", "")
                if event.get("total"):
                    pct = int(100 * event.get("completed", 0) / event["total"])
                    # One line per 10% or per 30 s, so a log stays readable.
                    if (status, pct // 10) != shown or time.time() - last > 30:
                        print(f"{status} {pct}%", flush=True)
                        shown, last = (status, pct // 10), time.time()
                elif status != (shown or ("",))[0]:
                    print(status, flush=True)
                    shown = (status, None)
    except OSError as exc:
        print(f"error: {getattr(exc, 'reason', exc)}")
        return 1
    print(f"{model} is ready at {url}")
    return 0


def ask(url: str, model: str) -> int:
    from llm_category.llm import OllamaClient

    client = OllamaClient(url, model)
    started = time.time()
    try:
        yes = client.is_member("STEM", "Mathematics and the natural sciences are STEM.",
                               "MATH 101", "Calculus I")
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {getattr(exc, 'reason', exc)}")
        return 1
    seconds = time.time() - started
    print(json.dumps({"model": model, "question": "Is MATH 101 Calculus I in STEM?",
                      "answer": "yes" if yes else "no", "seconds": round(seconds, 1)}))
    # The first answer includes loading the model; later ones are faster.
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("probe", "pull", "ask"):
        print(__doc__)
        return 2
    command, rest = args[0], args[1:]
    if command == "probe":
        result = probe_ollama(rest[0] if rest else DEFAULT_OLLAMA_URL)
        print(json.dumps(result, indent=2))
        return 0 if result["reachable"] else 1
    if len(rest) != 2:
        print(f"usage: ollama_check.py {command} URL MODEL")
        return 2
    return pull(*rest) if command == "pull" else ask(*rest)


if __name__ == "__main__":
    sys.exit(main())
