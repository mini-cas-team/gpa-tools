"""python -m jev_category.admin users | user <email> [...] | revoke-key <id> | definitions [...]

The package owner's side of the accounts (the same as the cloud server's /admin page):
every account (one per email) with its computers, its Jev use today, its Updates (N) and the
computer that has it now; an email's own daily limit and N; giving its Updates back; freeing
it for another computer; blocking it (all its computers stop); revoking one computer's key;
the definitions (shared ones and each email's private ones), and setting or deleting a
shared one.

    user <email> --daily-limit N|default --n-limit N|none|default --n-used 0 --release
                 --block | --unblock
    definitions [--set CAT --text "..." | --delete CAT]
Needs the server's admin token in GPA_TOOLS_CLOUD_ADMIN_TOKEN (--admin-token-env);
cloud-url comes from config.yaml.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
from pathlib import Path

from .client import JevError, _post
from .login import _cloud_url


def _limit(value: str):
    if value == "default":
        return None
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError("a whole number >= 0, or default")
    return n


def _n_limit(value: str):
    if value in ("default", "none"):
        return None if value == "default" else "none"
    return _limit(value)


def _row(u: dict) -> list[str]:
    limit = f"{u['used_today']}/{u['daily_limit'] if u['daily_limit'] is not None else '-'}"
    state = "blocked" if u["blocked"] else ("" if u["has_password"] else "no password yet")
    n = u.get("n") or {}
    n_limit = "-" if n.get("limit") is None else n["limit"]
    n_own = " (own)" if u.get("own_n_limit") is not None else ""
    updates = f"  updates {n.get('used', 0)}/{n_limit}{n_own}" if n else ""
    active = u.get("active")
    busy = f"  in use on {active['label']} until {active['until'][11:16]} UTC" if active else ""
    own = " (own limit)" if u["own_daily_limit"] is not None else ""
    lines = [f"{u['email']}  jev today {limit}{own}{updates}{'  ' + state if state else ''}{busy}"]
    for d in u["devices"]:
        flag = " revoked" if d["revoked"] else " expired" if d["expired"] else ""
        lines.append(f"    {d['id']}  {d['label']:<24} last used {d['last_used'][:10]}{flag}")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=Path("config.yaml"))
    ap.add_argument("--admin-token-env", default="GPA_TOOLS_CLOUD_ADMIN_TOKEN")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("users", help="every account, its computers and its Jev use today")
    u = sub.add_parser("user", help="change one email's daily limit, or block / unblock it")
    u.add_argument("email")
    u.add_argument("--daily-limit", type=_limit, default=argparse.SUPPRESS,
                   help="Jev questions a day for this email, or default")
    u.add_argument("--n-limit", type=_n_limit, default=argparse.SUPPRESS,
                   help="Updates (private definitions) for this email: a number, none (no limit), or default")
    u.add_argument("--n-used", type=_limit, default=argparse.SUPPRESS,
                   help="set the Updates used (0 gives them all back)")
    u.add_argument("--release", action="store_const", const=True, default=argparse.SUPPRESS,
                   help="free the account for another computer now")
    blk = u.add_mutually_exclusive_group()
    blk.add_argument("--block", dest="blocked", action="store_const", const=True, default=argparse.SUPPRESS)
    blk.add_argument("--unblock", dest="blocked", action="store_const", const=False, default=argparse.SUPPRESS)
    sub.add_parser("revoke-key", help="stop one computer (its key id, from users)").add_argument("id")
    d = sub.add_parser("definitions", help="the shared definitions and each email's private ones")
    act = d.add_mutually_exclusive_group()
    act.add_argument("--set", metavar="CAT", help="set CAT's shared text (with --text)")
    act.add_argument("--delete", metavar="CAT", help="delete CAT's shared text")
    d.add_argument("--text", help="the shared text for --set")
    args = ap.parse_args(argv)

    url = _cloud_url(args.config).rstrip("/")
    admin = os.environ.get(args.admin_token_env, "").strip()
    if not admin:
        raise SystemExit(f"the admin token is not set: export {args.admin_token_env}=<token>")

    def call(path, body=None, method=None):
        return _post(url + path, body, admin, 15, 1, method=method)

    try:
        if args.cmd == "users":
            data = call("/v1/admin/users")
            default = data["daily_limit"] or "no limit"
            n_default = "no limit" if data.get("n_limit") is None else data["n_limit"]
            print(f"{len(data['users'])} account(s); default daily limit {default}; "
                  f"default Updates {n_default}; keys expire after {data['idle_days']} days without use")
            for row in data["users"]:
                print("\n".join(_row(row)))
        elif args.cmd == "user":
            change = {k: getattr(args, k) for k in ("daily_limit", "blocked", "n_limit", "n_used", "release")
                      if hasattr(args, k)}
            if not change:
                raise SystemExit("nothing to change: give --daily-limit, --n-limit, --n-used, --release, "
                                 "--block or --unblock")
            print("\n".join(_row(call(f"/v1/admin/users/{urllib.parse.quote(args.email)}", change))))
        elif args.cmd == "revoke-key":
            r = call(f"/v1/admin/keys/{urllib.parse.quote(args.id)}", method="DELETE")
            print(f"key {r['id']} {'revoked' if r['revoked'] else 'was already revoked'}")
        elif args.set or args.delete:
            cat = urllib.parse.quote((args.set or args.delete).strip().upper())
            if args.set:
                if not (args.text or "").strip():
                    raise SystemExit("--set needs --text \"the definition\"")
                r = call(f"/v1/admin/definitions/{cat}", {"definition": args.text})
                print(f"{r['category']}: shared text set")
            else:
                r = call(f"/v1/admin/definitions/{cat}", method="DELETE")
                print(f"{r['category']}: shared text deleted")
        else:
            data = call("/v1/admin/definitions")
            print(f"{len(data['shared'])} shared definition(s)")
            for cat, d in sorted(data["shared"].items()):
                print(f"  {cat}  ({d.get('source')}, added by {d.get('created_by') or '?'})\n    {d['definition']}")
            for p in data["private"]:
                print(f"private, {p['email']}:")
                for cat, d in sorted(p["definitions"].items()):
                    print(f"  {cat}\n    {d['definition']}")
    except (JevError, OSError) as exc:
        print(f"{url}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
