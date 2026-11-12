"""This laptop's gpa-tools cloud token: asked for at install, picked up once approved.

    python -m jev_category.enroll request --email jane@school.edu   # at install
    python -m jev_category.enroll status                            # what the server says

The token lives in ~/.gpa-tools/ (GPA_TOOLS_HOME overrides it), never in the repo or
config.yaml:

    client_token    the live token; when it is there (not empty) it is used, nothing else
    pending_token   this laptop's own token, made at install, waiting for approval
    request.json    {cloud_url, email, hostname, id}: the request that was sent

How it works: ``request`` makes a random token here, keeps it in pending_token, and sends
the server only its sha256, with the email and hostname.  The package owner approves the
request (``python -m jev_category.admin``), which makes that hash a live token on the server.
On every start ``token()`` asks the server about the request (by the hash, so only this
laptop can) and, once it is approved, moves pending_token to client_token.  The token never
leaves this laptop.  GPA_TOOLS_CLOUD_TOKEN (cloud-token-env), when set, wins over the files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import sys
from pathlib import Path

from .client import JevError, _post

CLIENT, PENDING, REQUEST = "client_token", "pending_token", "request.json"


class NoToken(Exception):
    """This laptop has no usable token yet; the message says why and what to do."""


def home() -> Path:
    return Path(os.environ.get("GPA_TOOLS_HOME") or Path.home() / ".gpa-tools").expanduser()


def _read(name: str) -> str:
    try:
        return (home() / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write(name: str, text: str) -> None:
    folder = home()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    path = folder / name
    tmp = path.with_name(name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    tmp.replace(path)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hostname() -> str:
    name = socket.gethostname().split(".")[0]
    return "".join(c if c.isalnum() or c in "._-" else "-" for c in name)[:64] or "pc"


def _call(url: str, path: str, body: dict | None = None, timeout: float = 15) -> dict:
    return _post(url.rstrip("/") + path, body, None, timeout, retries=1)


def _send(req: dict, token: str) -> dict:
    return _call(req["cloud_url"], "/v1/requests",
                 {"email": req["email"], "hostname": req["hostname"], "token_hash": _hash(token)})


def request(cloud_url: str, email: str, host: str | None = None, new: bool = False) -> str:
    """Ask the server for a token for this laptop.  Returns what happened, for the installer.
    Keeps a live client_token; the request is saved first, so a failed send is retried on start."""
    if _read(CLIENT):
        if not new:
            return f"this laptop already has a client token ({home() / CLIENT}); --new replaces it"
        (home() / CLIENT).unlink()  # revoked: ask again with a fresh token
    req = {"cloud_url": cloud_url.rstrip("/"), "email": email.strip().lower(),
           "hostname": host or hostname()}
    token = "gpat_" + secrets.token_hex(32)
    _write(PENDING, token)
    try:
        reply = _send(req, token)
    except (JevError, OSError) as exc:
        _write(REQUEST, json.dumps(req, indent=1) + "\n")
        if isinstance(exc, JevError) and "HTTP 422" in str(exc):
            raise NoToken(f"the server refused the request: {exc}") from None
        return f"could not reach {cloud_url} ({exc}); the request is sent the next time gpa-tools starts"
    req["id"] = reply.get("id")
    _write(REQUEST, json.dumps(req, indent=1) + "\n")
    return (f"asked for a token as {req['email']}:{req['hostname']} (request {req['id']}); "
            f"ask the package owner to approve it")


def _notice(why: str) -> NoToken:
    req = _saved_request()
    who = f" {req['email']}:{req['hostname']}" if req else ""
    return NoToken(f"No gpa-tools cloud token on this laptop: {why}. "
                   f"Contact the package owner to approve{who}.")


def _saved_request() -> dict | None:
    try:
        return json.loads(_read(REQUEST) or "null")
    except ValueError:
        return None


def status() -> str:
    """pending | approved | rejected | none (no request) | unknown (the server cannot be reached)."""
    req, token = _saved_request(), _read(PENDING)
    if _read(CLIENT):
        return "approved"
    if not (req and token):
        return "none"
    try:
        reply = _call(req["cloud_url"], f"/v1/requests/{_hash(token)}")
    except JevError as exc:
        if not str(exc).startswith("HTTP 404"):
            return "unknown"
        try:  # never arrived (the server was down at install): send it now
            _send(req, token)
        except (JevError, OSError):
            return "unknown"
        return "pending"
    except OSError:
        return "unknown"
    if reply.get("status") == "approved":
        _write(CLIENT, token)
        (home() / PENDING).unlink(missing_ok=True)
    return str(reply.get("status") or "unknown")


def token(token_env: str = "GPA_TOOLS_CLOUD_TOKEN") -> str:
    """This laptop's client token; NoToken (with the notice to show) when there is none yet."""
    env = os.environ.get(token_env, "").strip()
    if env:
        return env
    live = _read(CLIENT)
    if live:
        return live
    state = status()
    if state == "approved":
        return _read(CLIENT)
    raise _notice({
        "pending": "its request is waiting for approval",
        "rejected": "its request was rejected",
        "unknown": "the cloud server cannot be reached to check its request",
        "none": f"none was requested (python -m jev_category.enroll request --email <you>) "
                f"and {token_env} is not set",
    }[state] if state in ("pending", "rejected", "unknown", "none") else f"request status {state}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("request", help="ask the server for this laptop's token")
    r.add_argument("--email", required=True)
    r.add_argument("--hostname")
    r.add_argument("--new", action="store_true", help="replace this laptop's token (it was revoked)")
    r.add_argument("--config", type=Path, default=Path("config.yaml"))
    sub.add_parser("status", help="ask the server about this laptop's request")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "request":
            from . import builder
            cfg = builder.load_config(args.config)
            if not cfg["cloud_url"]:
                raise SystemExit("cloud-url is not set in config.yaml's category-builder: jev:")
            print(request(cfg["cloud_url"], args.email, args.hostname, args.new))
        else:
            state = status()
            print(f"{state}  ({home()})")
            return 0 if state == "approved" else 1
    except NoToken as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
