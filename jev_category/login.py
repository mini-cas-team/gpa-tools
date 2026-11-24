"""This computer's gpa-tools cloud key: signed in with the account's email and password.

    python -m jev_category.login                      # asks the email and password, gets this computer's key
    python -m jev_category.login --email you@x.edu    # the email given (the installer passes it); asks the password
    python -m jev_category.login status               # signed-in <email> | expired | blocked | none | unknown
    python -m jev_category.login logout               # this computer signs out (its key is revoked)

The account (one per email) is made on the cloud server's /account page: the email is
confirmed with an emailed code, then the user chooses a password.  Every computer then signs
in here and gets its own key from the server; the server links every key to the email, and
every limit is the email's.  A key stops working after 90 days without use, when it is revoked
(on /account, by the package owner, or by a password reset): sign in again.

Files in ~/.gpa-tools/ (GPA_TOOLS_HOME overrides it), never in the repo or config.yaml:

    client_token    this computer's key (mode 0600); the password is never saved
    account.json    {cloud_url, email, label, id}: whose key it is

GPA_TOOLS_CLOUD_TOKEN (cloud-token-env), when set, wins over the file.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sys
from pathlib import Path

from .client import JevError, _post

CLIENT, ACCOUNT = "client_token", "account.json"
OLD_FILES = ("pending_token", "request.json")  # the old approval flow's (jev_category.enroll)
TIMEOUT = 20


class NoToken(Exception):
    """This computer has no key yet; the message says what to do."""


class SignInError(Exception):
    """Signing in failed; ``status`` is the server's HTTP status (0: not reached)."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


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


def hostname() -> str:
    name = socket.gethostname().split(".")[0]
    return "".join(c if c.isalnum() or c in "._-" else "-" for c in name)[:64] or "pc"


def account() -> dict:
    """{cloud_url, email, label, id} of this computer's key, or {} when it has none."""
    try:
        return json.loads(_read(ACCOUNT) or "{}")
    except ValueError:
        return {}


def account_page(cloud_url: str) -> str:
    return cloud_url.rstrip("/") + "/account"


def _status_of(exc: JevError) -> int:
    text = str(exc)
    return int(text[5:8]) if text.startswith("HTTP ") and text[5:8].isdigit() else 0


def sign_in(cloud_url: str, email: str, password: str, label: str | None = None) -> dict:
    """Get this computer's own key from the server and save it.  Returns {email, label, id}.
    SignInError with the server's reason (wrong password, locked, blocked, not reachable)."""
    url = cloud_url.rstrip("/")
    try:
        reply = _post(f"{url}/v1/keys", {"email": email.strip(), "password": password,
                                         "label": label or hostname()}, None, TIMEOUT, retries=0)
    except JevError as exc:
        status = _status_of(exc)
        detail = str(exc).split(": ", 1)[-1]
        if status == 401:
            detail += f" -- no account yet? Create one at {account_page(url)}"
        raise SignInError(detail, status) from None
    except OSError as exc:
        raise SignInError(f"cannot reach {url}: {getattr(exc, 'reason', exc)}") from None
    _write(CLIENT, reply["key"])
    who = {"cloud_url": url, "email": reply["email"], "label": reply["label"], "id": reply["id"]}
    _write(ACCOUNT, json.dumps(who, indent=1) + "\n")
    for name in OLD_FILES:
        (home() / name).unlink(missing_ok=True)
    return {k: who[k] for k in ("email", "label", "id")}


def sign_out(cloud_url: str | None = None) -> bool:
    """Revoke this computer's key on the server (when it can be reached) and forget it here.
    True when there was a key."""
    key = _read(CLIENT)
    if not key:
        return False
    url = (cloud_url or account().get("cloud_url") or "").rstrip("/")
    if url:
        try:
            _post(f"{url}/v1/keys/self", None, key, TIMEOUT, retries=0, method="DELETE")
        except (JevError, OSError):
            pass  # expired, revoked or offline: forgetting it here is what matters
    for name in (CLIENT, ACCOUNT):
        (home() / name).unlink(missing_ok=True)
    return True


def status(cloud_url: str, token_env: str = "GPA_TOOLS_CLOUD_TOKEN") -> tuple[str, str]:
    """(state, detail): signed-in (detail = the email), expired, blocked, none, or unknown
    (the server cannot be reached)."""
    key = os.environ.get(token_env, "").strip() or _read(CLIENT)
    if not key:
        return "none", "not signed in (python -m jev_category.login)"
    try:
        info = _post(cloud_url.rstrip("/") + "/v1/info", None, key, TIMEOUT, retries=0)
    except JevError as exc:
        status_code = _status_of(exc)
        detail = str(exc).split(": ", 1)[-1]
        return ("expired" if status_code == 401 else "blocked" if status_code == 403 else "unknown"), detail
    except OSError as exc:
        return "unknown", f"cannot reach {cloud_url}: {getattr(exc, 'reason', exc)}"
    return "signed-in", str(info.get("email") or info.get("client") or "")


def in_use(body: dict) -> str:
    """The 423 message: which computer has the account, and from when it is free (local time)."""
    info = (body or {}).get("in_use") or {}
    until = info.get("until") or ""
    try:
        from datetime import datetime
        when = datetime.fromisoformat(until.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    except ValueError:
        when = ""
    label = info.get("label") or "another computer"
    after = f" after {when}" if when else " later"
    return (f"This account is in use on {label}. Try again{after} (it is free 30 minutes after "
            f"that computer's last use, or when it signs out).")


def notice(cloud_url: str = "") -> str:
    where = f" (create an account at {account_page(cloud_url)})" if cloud_url else ""
    return ("This computer is not signed in to the gpa-tools cloud: sign in with your account's "
            f"email and password in browser_view (Sign in), or run python -m jev_category.login{where}.")


def token(token_env: str = "GPA_TOOLS_CLOUD_TOKEN", cloud_url: str = "") -> str:
    """This computer's key; NoToken (with the notice to show) when it has none.  Whether the
    server still accepts it shows on the first call (401: sign in again)."""
    env = os.environ.get(token_env, "").strip()
    if env:
        return env
    key = _read(CLIENT)
    if key:
        return key
    raise NoToken(notice(cloud_url))


# ---- the command line ----------------------------------------------------------------

def _cloud_url(config: Path) -> str:
    import yaml

    from . import builder
    try:
        raw = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise SystemExit(f"cannot read {config}: {exc}") from None
    url = builder.jev_settings(raw)["cloud_url"]
    if not url:
        raise SystemExit("cloud-url is not set in config.yaml's category-builder: jev:")
    return url


def _login(url: str, email: str | None, label: str | None, tries: int) -> int:
    if not sys.stdin.isatty() and not email:
        print("Sign-in needs a person at the keyboard: run python -m jev_category.login in a terminal.",
              file=sys.stderr)
        return 1
    email = (email or input("Email: ")).strip()
    print(f"Signing in to {url} as {email} (no account yet? create one at {account_page(url)})")
    for attempt in range(1, tries + 1):
        try:
            password = getpass.getpass("Password: ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        try:
            who = sign_in(url, email, password, label)
        except SignInError as exc:
            print(f"Sign-in failed: {exc}", file=sys.stderr)
            if exc.status != 401 or attempt == tries:
                return 1
            continue
        print(f"Signed in as {who['email']} on {who['label']}; the key is in {home() / CLIENT}")
        return 0
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", nargs="?", default="login", choices=["login", "status", "logout"])
    ap.add_argument("--email", help="the account's email (asked when not given)")
    ap.add_argument("--label", help="this computer's name on the account page (default: its hostname)")
    ap.add_argument("--tries", type=int, default=3, help="password tries (default 3)")
    ap.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = ap.parse_args(argv)
    url = _cloud_url(args.config)
    if args.cmd == "login":
        return _login(url, args.email, args.label, args.tries)
    if args.cmd == "logout":
        print("signed out" if sign_out(url) else "this computer was not signed in")
        return 0
    state, detail = status(url)
    print(f"{state}  {detail}  ({home()})")
    return 0 if state == "signed-in" else 1


if __name__ == "__main__":
    sys.exit(main())
