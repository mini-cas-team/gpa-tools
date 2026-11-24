"""browser_view's connection to the cloud server: one computer per account at a time.

The server lets one computer of an email work at a time: the computer that makes a request
holds the account until 30 minutes after its last request (ACTIVE_MINUTES on the server),
and another computer's requests meanwhile get 423 "in use on <label>".

browser_view connects (POST /v1/connect: takes the account and downloads this account's
definitions into the local copy) when it starts serving and on the operator's first action
after being idle: a page or a click, not the pages' own status polls.  While the operator
works it renews the hold at most every RENEW seconds.  After IDLE seconds without an action
(and no build or letter run going) it counts as disconnected: the server has freed the
account by then, and the next action connects again (no password: the key stays).  When
browser_view stops it disconnects at once (``disconnect``).

States: off (jev not in cloud mode), signed-out, connected, idle, busy (another computer has
the account; ``detail`` says which and until when), expired (sign in again), blocked,
offline (the server cannot be reached), error.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

log = logging.getLogger("browser_view")

RENEW = 5 * 60     # renew the hold at most this often while the operator works
IDLE = 30 * 60     # no action for this long: disconnected (the server frees the account too)
RETRY = 30         # in use elsewhere / offline: ask again at most this often


class CloudSession:
    def __init__(self, config_path: Path, working=lambda: False):
        """``working()``: True while a build or letter run is going (it keeps the account)."""
        self.config_path = Path(config_path)
        self.working = working
        self.lock = threading.Lock()
        self.state, self.detail = "off", ""
        self.label = self.email = ""
        self.n: dict | None = None
        self.last_activity = 0.0
        self.last_connect = 0.0
        self.last_try = 0.0

    # ---- what the side bar and the Upload tab show (no server call) ------------------
    def snapshot(self) -> dict:
        with self.lock:
            state = self.state
            if state == "connected" and self._idle():
                state = "idle"
            return {"state": state, "detail": self.detail if state != "idle" else
                    "idle for 30 minutes: connects again on your next action",
                    "email": self.email, "label": self.label, "n": self.n}

    def _idle(self) -> bool:
        return time.time() - self.last_activity > IDLE and not self.working()

    # ---- the operator did something -------------------------------------------------
    def touch(self) -> None:
        """An action: connect when not connected (or idle), renew when RENEW is up."""
        now = time.time()
        with self.lock:
            idle = self.state == "connected" and self._idle()
            self.last_activity = now
            if self.state == "connected" and not idle and now - self.last_connect < RENEW:
                return
            if self.state in ("busy", "offline", "error") and now - self.last_try < RETRY:
                return
        self.connect()

    def connect(self) -> str:
        """Take the account and refresh the definitions; the new state."""
        from category_builder.definitions import Definitions, _load_raw, cloud_for, cloud_mode
        from jev_category import login
        from jev_category.client import CloudError, JevError

        raw = _load_raw(self.config_path)
        now = time.time()
        if not cloud_mode(raw):
            return self._set("off", "")
        cloud = cloud_for(raw)
        if cloud is None:
            return self._set("signed-out", "not signed in to the cloud")
        with self.lock:
            self.last_try = now
        try:
            defs = Definitions(self.config_path, cloud=cloud)
            reply = cloud.connect(defs.rev(login.account().get("email")))
            defs.apply(reply)
        except CloudError as exc:
            state = {423: "busy", 401: "expired", 403: "blocked"}.get(exc.status, "error")
            detail = login.in_use(exc.body) if exc.status == 423 else str(exc).split(": ", 1)[-1]
            return self._set(state, detail)
        except JevError as exc:
            return self._set("error", str(exc))
        except OSError as exc:
            return self._set("offline", f"cannot reach the cloud server: {getattr(exc, 'reason', exc)}")
        with self.lock:
            first = self.state != "connected"
            self.state, self.detail = "connected", ""
            self.email, self.label = reply.get("email", ""), reply.get("label", "")
            self.n = reply.get("n") or defs.n
            self.last_connect = now
        if first:
            log.info("connected to the cloud as %s on %s", self.email, self.label)
        return "connected"

    def _set(self, state: str, detail: str) -> str:
        with self.lock:
            changed = (self.state, self.detail) != (state, detail)
            self.state, self.detail = state, detail
        if changed and state not in ("off", "signed-out"):
            log.info("cloud: %s (%s)", state, detail)
        return state

    def update_n(self, n: dict | None) -> None:
        if n:
            with self.lock:
                self.n = n

    def disconnect(self) -> None:
        """Free the account now (browser_view is stopping, or the computer signs out)."""
        from category_builder.definitions import _load_raw, cloud_for

        with self.lock:
            was = self.state
            self.state, self.detail = "idle", ""
        if was != "connected":
            return
        cloud = cloud_for(_load_raw(self.config_path))
        if cloud is None:
            return
        try:
            cloud.disconnect()
            log.info("disconnected from the cloud")
        except Exception as exc:  # noqa: BLE001  offline or expired: the server frees it anyway
            log.info("could not disconnect from the cloud: %s", exc)
