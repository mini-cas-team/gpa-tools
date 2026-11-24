"""Category definitions: this computer's copy of what its account (an email) uses.

Every builder uses them.  The cloud server holds the definitions (see gpa-tools-cloud's
src/definitions.py): an email's text of a category is its **private** one when it has one,
else the **shared** one.  A shared definition is an AI's draft (chatgpt, gemini, ...): Add
offers it, and the first text of a category wins.  A private one is the email's own edit
(Update -> Confirm; Reset drops it).  A new category (Add) and a new private text (Confirm)
each use one of the email's N, counted together (3 by default; the server enforces it).  Jev scores a category only with
the email's current text, so this folder is a copy, refreshed from the server (``sync``) when
the computer connects (browser_view: at start and on the first action after 30 idle
minutes), when the Upload tab lists the categories, and when a build finds its copy stale
(the server's 409).  One ``<CAT>.json`` per category:

    {"category": "STEM", "definition": "...", "source": "shared", "ai": "gemini",
     "updated_at": "2026-11-22T21:00:00Z"}

    source   shared   the shared text      private   this email's own text
             local    no cloud (jev mode direct, development): the folder is all there is
    ai       the AI that drafted the shared text (chatgpt, gemini, legacy, admin, ...)

``.sync.json`` holds {cloud_url, email, rev}: whose copy it is, and the server's revision of it.

config.yaml's ``definitions-folder`` names the folder (default <work-folder>/definitions).
When it does not exist yet it is made (``init``): config.yaml's old ``category-definitions:``
section is moved into it (that section is then replaced by ``definitions-folder:``).

Older copies (before accounts) had ``origin: cloud|local`` and ``pending``; on the first sync a
``local`` one is offered to the server (a pending draft as an Add, an edit as this email's
private text) and kept in ``old-local/``, then the server's copy replaces the folder.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

import yaml
from jev_core import JevError

log = logging.getLogger("category_builder.definitions")

FOLDER_KEY = "definitions-folder"
SECTION = "category-definitions"  # where config.yaml kept them before the repo; migrated
SYNC = ".sync.json"
OLD_LOCAL = "old-local"
SECTION_HEADER = (
    "# --- category definitions (all builders) ------------------------------------\n"
    "# Shared by every category builder; drafted by category-definer, edit freely.\n"
    "# Changing a definition clears the llm builder's cached answers for it.\n"
    "# Delete an entry to have category-definer draft a new one.\n"
)
FOLDER_HEADER = (
    "# --- category definitions (all builders) ------------------------------------\n"
    "# One <CAT>.json per category, this computer's copy of its account's definitions\n"
    "# on the cloud server (category_builder/definitions.py).\n"
)


class Changed(Exception):
    """The server's text of a category is not the one this computer had (changed on another
    computer, or by the package owner); the copy now has the server's, ``entry``."""

    def __init__(self, message: str, entry: dict):
        super().__init__(message)
        self.entry = entry


def _signed_in_email() -> str | None:
    from jev_category import login

    return login.account().get("email") or None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_raw(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}


def repo_folder(raw: dict, config_path: Path) -> Path:
    """config.yaml's definitions-folder, else <work-folder>/definitions, else next to config.yaml."""
    if raw.get(FOLDER_KEY):
        return Path(raw[FOLDER_KEY]).expanduser()
    if raw.get("work-folder"):
        return Path(raw["work-folder"]).expanduser() / "definitions"
    return config_path.parent / "definitions"


def cloud_for(raw: dict):
    """The gpa-tools cloud client when jev is in cloud mode with its url and this computer's key, else None."""
    from jev_category import builder as jev_builder  # here: jev_category imports this package

    cfg = jev_builder.jev_settings(raw)
    if cfg["mode"] != "cloud" or not cfg["cloud_url"]:
        return None
    try:
        return jev_builder.client(cfg)
    except Exception:  # noqa: BLE001  not signed in: work without the cloud
        return None


def cloud_mode(raw: dict) -> bool:
    """jev is in cloud mode: the server holds the definitions (signed in or not)."""
    from jev_category import builder as jev_builder

    cfg = jev_builder.jev_settings(raw)
    return cfg["mode"] == "cloud" and bool(cfg["cloud_url"])


_UNSET = object()


class Definitions:
    """This computer's copy: ``data`` is {CATEGORY: definition}, ``entries`` the whole files,
    ``n`` the email's {limit, used, left} of Updates from the last sync (None before one)."""

    def __init__(self, config_path: Path, cloud=_UNSET):
        self.config_path = Path(config_path)
        raw = _load_raw(self.config_path)
        self.folder = repo_folder(raw, self.config_path)
        self._cloud = cloud
        self._raw = raw
        self.n: dict | None = None
        self.entries: dict[str, dict] = {}
        self.data: dict[str, str] = {}
        if not self.folder.exists():
            self.init()
        self.reload()

    @property
    def cloud(self):
        if self._cloud is _UNSET:
            self._cloud = cloud_for(self._raw)
        return self._cloud

    # ---- reading and writing the copy -----------------------------------------
    def reload(self) -> None:
        self.entries = {}
        for path in sorted(self.folder.glob("*.json")):
            if path.name.startswith("."):
                continue
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("skipping unreadable definition %s: %s", path, exc)
                continue
            if isinstance(entry, dict) and entry.get("category") and entry.get("definition"):
                self.entries[entry["category"]] = entry
        self.data = {c: e["definition"].strip() for c, e in self.entries.items()}

    def file(self, category: str) -> Path:
        return self.folder / f"{category}.json"

    def set(self, category: str, definition: str, source: str = "local", ai: str | None = None) -> dict:
        entry = {"category": category, "definition": definition.strip(), "source": source,
                 "ai": ai, "updated_at": _now()}
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.file(category)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(entry, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
        self.entries[category] = entry
        self.data[category] = entry["definition"]
        return entry

    def _remove(self, category: str) -> None:
        self.file(category).unlink(missing_ok=True)
        self.entries.pop(category, None)
        self.data.pop(category, None)

    def source(self, category: str) -> str | None:
        """shared, private or local; None when this computer has no copy of it."""
        entry = self.entries.get(category)
        if not entry:
            return None
        if "origin" in entry:  # an older copy, not synced yet
            return "local" if entry.get("origin") == "local" else "shared"
        return entry.get("source")

    def store(self, category: str, server: dict) -> dict:
        """Keep the server's {definition, source, ai} of ``category`` (a 409's body, a reply)."""
        if not (server.get("definition") or "").strip():
            self._remove(category)
            return {}
        return self.set(category, server["definition"], server.get("source") or "shared", server.get("ai"))

    # ---- in step with the server -----------------------------------------------
    def _meta(self) -> dict:
        try:
            return json.loads((self.folder / SYNC).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def rev(self, email: str | None = None) -> str | None:
        """The server revision this copy is at; None when it is another email's or server's."""
        meta = self._meta()
        url = getattr(self.cloud, "url", None)
        if not meta.get("rev") or (url and meta.get("cloud_url") != url):
            return None
        email = email or _signed_in_email()
        if email and meta.get("email") != email:
            return None
        return meta["rev"]

    def sync(self) -> bool:
        """Refresh the copy from the server.  True when it changed; False without a cloud or
        when nothing changed.  JevError / OSError when the server cannot be asked."""
        if self.cloud is None:
            return False
        return self.apply(self.cloud.listing(self.rev()))

    def apply(self, reply: dict) -> bool:
        """Take the server's listing (GET /v1/definitions or POST /v1/connect's reply)."""
        self.n = reply.get("n") or self.n
        if self._migrate(reply):
            reply = self.cloud.listing(None)
            self.n = reply.get("n") or self.n
        if reply.get("unchanged"):
            return False
        entries = reply.get("entries") or {c: {"definition": d, "source": "shared"}
                                           for c, d in (reply.get("definitions") or {}).items()}
        changed = False
        for category, e in entries.items():
            mine = self.entries.get(category) or {}
            if (mine.get("definition"), self.source(category), mine.get("ai")) != \
                    (e["definition"].strip(), e.get("source"), e.get("ai")):
                self.store(category, e)
                changed = True
        for category in set(self.entries) - set(entries):
            self._remove(category)
            changed = True
        meta = {"cloud_url": getattr(self.cloud, "url", None), "rev": reply.get("rev"),
                "email": reply.get("email") or _signed_in_email(), "synced_at": _now()}
        (self.folder / SYNC).write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
        if changed:
            log.info("definitions in %s now match the server (rev %s)", self.folder, meta["rev"])
        return changed

    def _migrate(self, reply: dict) -> bool:
        """Offer an older copy's own texts (origin local) to the server, once; True when any."""
        old = {c: e for c, e in self.entries.items() if e.get("origin") == "local"}
        if not old or self.cloud is None:
            return False
        keep = self.folder / OLD_LOCAL
        keep.mkdir(exist_ok=True)
        known = set((reply.get("entries") or reply.get("definitions") or {}))
        for category, e in old.items():
            shutil.copy2(self.file(category), keep / self.file(category).name)
            try:
                if category in known and not e.get("pending"):
                    self.cloud.set_private(category, e["definition"], None)
                    log.info("this computer's own %s definition is now the account's private one", category)
                else:
                    self.cloud.create_definition(category, e["definition"], "legacy")
                    log.info("offered this computer's %s definition to the server", category)
            except (JevError, OSError) as exc:
                log.warning("could not offer this computer's %s definition to the server (%s); "
                            "it is kept in %s", category, exc, keep)
            self._remove(category)
        return True

    # ---- Add, Update -> Confirm, Reset -------------------------------------------
    def add(self, category: str, definition: str, ai: str) -> dict:
        """Add: an AI's (``ai``) draft of a new category, offered as the shared one (one N when
        it is new); the server's answer (ours, or the text it already had) is kept.  The entry plus
        ``created``.  Without a cloud: saved here (source local)."""
        if self.cloud is None:
            return {**self.set(category, definition, "local", ai), "created": True}
        reply = self.cloud.create_definition(category, definition, ai)
        self.n = reply.get("n") or self.n
        entry = self.store(category, reply)
        if not reply.get("created"):
            log.info("the server already had a %s definition; using it instead of this draft", category)
        return {**entry, "created": bool(reply.get("created"))}

    def confirm(self, category: str, definition: str) -> dict:
        """Update -> Confirm: this email's private text of ``category`` (one N).  Changed
        when the server's text is no longer the one this computer showed; CloudError 429
        (none left) and the like pass through."""
        if self.cloud is None:
            return self.set(category, definition, "local", (self.entries.get(category) or {}).get("ai"))
        from jev_category.client import CloudError

        try:
            reply = self.cloud.set_private(category, definition, self.data.get(category))
        except CloudError as exc:
            if exc.status == 409 and "definition" in exc.body:
                entry = self.store(category, exc.body)
                raise Changed(str(exc).split(": ", 1)[-1], entry) from None
            raise
        self.n = reply.get("n") or self.n
        return self.store(category, reply)

    def reset(self, category: str) -> dict:
        """Reset: drop this email's private text; the shared one again."""
        if self.cloud is None:
            raise ValueError("Reset needs the cloud server (jev mode cloud)")
        reply = self.cloud.reset_private(category)
        self.n = reply.get("n") or self.n
        return self.store(category, reply)

    def resolve(self, category: str, draft) -> tuple[str, str]:
        """(definition, how): how is "repo" (this copy), "cloud" (the server had it; the copy
        was refreshed) or "drafted" (``draft()`` gave (text, ai), which was Added; the server
        may answer with a text it already had).  ``draft`` raises when it cannot draft."""
        if category in self.data:
            return self.data[category], "repo"
        if self.cloud is not None:
            try:
                self.sync()
            except (JevError, OSError) as exc:
                log.info("could not refresh the definitions from the server: %s", exc)
            if category in self.data:
                return self.data[category], "cloud"
        text, ai = draft()
        return self.add(category, text, ai)["definition"], "drafted"

    # ---- making the folder -----------------------------------------------------
    def init(self) -> None:
        """Make the folder; config.yaml's old section moves in (offered to the server on the
        first sync, like any older own text)."""
        self.folder.mkdir(parents=True, exist_ok=True)
        old = {str(k).strip().upper(): str(v).strip()
               for k, v in (self._raw.get(SECTION) or {}).items() if v and str(v).strip()}
        for category, definition in old.items():
            path = self.file(category)
            path.write_text(json.dumps({"category": category, "definition": definition, "origin": "local",
                                        "pending": True, "updated_at": _now()}, indent=1) + "\n",
                            encoding="utf-8")
        log.info("made the definitions folder %s: %d from config.yaml", self.folder, len(old))
        if SECTION in self._raw or FOLDER_KEY not in self._raw:
            self._point_config_at_folder()

    def _point_config_at_folder(self) -> None:
        """Replace config.yaml's category-definitions section (and its header) with
        ``definitions-folder: <folder>``; the rest of the file is left byte for byte."""
        if not self.config_path.exists():
            return
        text = self.config_path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        block = FOLDER_HEADER + yaml.safe_dump({FOLDER_KEY: str(self.folder)}, width=200)
        start = next((i for i, line in enumerate(lines)
                      if line.startswith(f"{SECTION}:") or line.startswith(f"{FOLDER_KEY}:")), None)
        if start is None:
            new_text = text.rstrip("\n") + "\n\n" + block if text.strip() else block
        else:
            end = next((i for i in range(start + 1, len(lines))
                        if lines[i].strip() and not lines[i][0].isspace()
                        and not lines[i].startswith("#")), len(lines))
            for header in (SECTION_HEADER, FOLDER_HEADER):
                head = header.splitlines(keepends=True)
                if lines[max(0, start - len(head)):start] == head:
                    start -= len(head)
                    break
            tail = "".join(lines[end:])
            new_text = "".join(lines[:start]) + block + ("\n" + tail if tail else "")
        reread = yaml.safe_load(new_text) or {}
        if reread.get(FOLDER_KEY) != str(self.folder) or SECTION in reread:
            raise ValueError(f"could not update {self.config_path} safely")
        tmp = self.config_path.with_name(self.config_path.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(self.config_path)
        self._raw = reread
