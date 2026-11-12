"""python -m jev_category.seed --category STEM [--definition] [--config config.yaml]

Uploads this machine's Jev scores for a category (<work-folder>/jev/cache_<cat>.json)
to the gpa-tools cloud server's shared score store, so no client pays for those
courses again.  --definition also offers this PC's definition to the cloud as the
shared one (create-only: it is refused when the cloud already has one).  Needs the
server's admin token in GPA_TOOLS_CLOUD_ADMIN_TOKEN (--admin-token-env), and for
--definition the client token too; cloud-url comes from config.yaml's category-builder: jev:.

The scores are uploaded only when the cache was made with this PC's current
definition (the local definitions repo) and model; they are filed under that model.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from category_builder.definitions import Definitions

from . import builder
from .client import JevError, _post
from .store import ScoreCache

CHUNK = 400  # the server's per-request limit for seeding


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--category", required=True)
    ap.add_argument("--definition", action="store_true", help="also publish the definition")
    ap.add_argument("--config", type=Path, default=Path("config.yaml"))
    ap.add_argument("--admin-token-env", default="GPA_TOOLS_CLOUD_ADMIN_TOKEN")
    args = ap.parse_args(argv)

    cfg = builder.load_config(args.config)
    if not cfg["cloud_url"]:
        raise SystemExit("cloud-url is not set in config.yaml's category-builder: jev:")
    token = os.environ.get(args.admin_token_env, "").strip()
    if not token:
        raise SystemExit(f"the admin token is not set: export {args.admin_token_env}=<token>")
    category = args.category
    definition = (Definitions(args.config).data.get(category) or "").strip()
    if not definition:
        raise SystemExit(f"the definitions repo has no definition for {category}")
    path = cfg["folder"] / f"cache_{category.lower()}.json"
    cache = ScoreCache(path, definition, cfg["model"])
    if cache.reset:
        raise SystemExit(f"{path} was made with another definition or model than config.yaml's "
                         f"({cfg['model']}); not uploading it")
    scores = cache.answers
    url = cfg["cloud_url"].rstrip("/")
    items = list(scores.items())
    try:
        if args.definition:
            reply = builder.client(cfg).create_definition(category, definition)
            if reply.get("created"):
                print(f"published the {category} definition")
            elif reply["definition"].strip() != definition:
                print(f"the cloud already has another {category} definition; not uploading "
                      f"scores made with this PC's", file=sys.stderr)
                return 1
        for start in range(0, len(items), CHUNK):
            reply = _post(f"{url}/v1/admin/scores",
                          {"category": category, "definition": definition, "model": cfg["model"],
                           "scores": dict(items[start:start + CHUNK])}, token, timeout=60, retries=2)
            print(f"stored {start + reply.get('stored', 0)} of {len(items)} scores ({reply.get('model')})")
    except (JevError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not items:
        print(f"{path} has no scores")
    return 0


if __name__ == "__main__":
    sys.exit(main())
