"""python -m jev_category.admin requests | approve <id> [--daily-limit N] | reject <id> | revoke <name>

The package owner's side of laptop tokens (see enroll.py): list the laptops waiting for
a token, approve or reject them, revoke a laptop's token.  Needs the server's admin token
in GPA_TOOLS_CLOUD_ADMIN_TOKEN (--admin-token-env); cloud-url comes from config.yaml.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import builder
from .client import JevError, _post


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=Path("config.yaml"))
    ap.add_argument("--admin-token-env", default="GPA_TOOLS_CLOUD_ADMIN_TOKEN")
    ap.add_argument("--all", action="store_true", help="requests: also the decided ones")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("requests", help="list the requests waiting for approval")
    a = sub.add_parser("approve")
    a.add_argument("id")
    a.add_argument("--daily-limit", type=int)
    sub.add_parser("reject").add_argument("id")
    sub.add_parser("revoke").add_argument("name", help="the token name, <email>:<hostname>")
    args = ap.parse_args(argv)

    url = builder.load_config(args.config)["cloud_url"].rstrip("/")
    if not url:
        raise SystemExit("cloud-url is not set in config.yaml's category-builder: jev:")
    admin = os.environ.get(args.admin_token_env, "").strip()
    if not admin:
        raise SystemExit(f"the admin token is not set: export {args.admin_token_env}=<token>")

    def call(path, body=None, method=None):
        if method == "DELETE":
            req = urllib.request.Request(url + path, method="DELETE",
                                         headers={"Authorization": f"Bearer {admin}", "User-Agent": "gpa-tools"})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                raise JevError(f"HTTP {exc.code}") from None
        return _post(url + path, body, admin, 15, 1)

    try:
        if args.cmd == "requests":
            rows = call("/v1/admin/requests")["requests"]
            rows = [r for r in rows if args.all or r["status"] == "pending"]
            if not rows:
                print("no requests waiting" if not args.all else "no requests")
            for r in rows:
                print(f"{r['id']}  {r['status']:<8}  {r['requested_at'][:16]}  {r['email']}  {r['hostname']}")
        elif args.cmd in ("approve", "reject"):
            body = {"daily_limit": args.daily_limit} if getattr(args, "daily_limit", None) is not None else {}
            r = call(f"/v1/admin/requests/{args.id}/{args.cmd}", body)
            extra = " (its earlier token is revoked)" if r.get("replaced") else ""
            print(f"{r['id']} {r['status']}: {r['name']}{extra}")
        else:
            call(f"/v1/admin/tokens/{urllib.parse.quote(args.name)}", method="DELETE")
            print(f"revoked {args.name}")
    except (JevError, OSError) as exc:
        print(f"{url}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
