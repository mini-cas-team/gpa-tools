"""Talk to TypeSafe for the jev builder: is the key accepted, ask one real question.

    python3 install/jev_check.py probe [--key-env NAME] [--api-url URL]
    python3 install/jev_check.py ask   [MODEL] [--key-env NAME] [--api-url URL]

Standard library only. The API key is read from the environment variable
(default GPA_TOOLS_KEY) and is never printed. ``probe`` lists the models the
key can use (free); ``ask`` sends one yes/no question through jev_category's
own client, the same request a real run makes (a fraction of a cent), which
also proves the model name is accepted. Exit status is 0 on success, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(PROJECT_ROOT))

from check import JEV_DEFAULTS, probe_jev  # noqa: E402


def api_key(env: str) -> str | None:
    key = os.environ.get(env, "").strip()
    if not key:
        print(f"error: {env} is not set in this shell. Create a key in the TypeSafe console "
              f"(API Keys), then export {env}=<key> in your shell profile and open a new shell.")
    return key or None


def ask(api_url: str, model: str, key: str) -> int:
    from jev_category.client import JevClient, JevError

    client = JevClient(api_url, model, key)
    started = time.time()
    try:
        scores, usage = client.scores("STEM", "Mathematics and the natural sciences are STEM.",
                                      [("MATH 101", "Calculus I")])
    except (JevError, OSError) as exc:
        print(f"error: {getattr(exc, 'reason', exc)}")
        return 1
    print(json.dumps({"model": model, "question": "Is MATH 101 Calculus I in STEM?",
                      "score": round(scores[0], 3), "input_tokens": usage.get("input_tokens"),
                      "seconds": round(time.time() - started, 1)}))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("probe", "ask"))
    ap.add_argument("model", nargs="?", default=JEV_DEFAULTS["model"])
    ap.add_argument("--key-env", default=JEV_DEFAULTS["api-key-env"])
    ap.add_argument("--api-url", default=JEV_DEFAULTS["api-url"])
    args = ap.parse_args(argv)
    key = api_key(args.key_env)
    if not key:
        return 1
    if args.command == "probe":
        result = probe_jev(args.api_url, key)
        print(json.dumps(result, indent=2))
        return 0 if "models" in result else 1
    return ask(args.api_url, args.model, key)


if __name__ == "__main__":
    sys.exit(main())
