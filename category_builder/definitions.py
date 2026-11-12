"""Category definitions: the local definitions repo, kept in step with the cloud's shared ones.

Every builder uses them.  The repo is a folder, one ``<CAT>.json`` per category:

    {"category": "STEM", "definition": "...", "origin": "cloud", "pending": false,
     "updated_at": "2026-11-10T21:00:00Z"}

    origin   cloud  the text is the cloud's shared definition (taken from it, or this PC's
                    draft that the cloud accepted first), so its Jev scores are shared
             local  this PC's own text: an edit (a local override; its scores are this PC's
                    own) or a draft the cloud has not seen yet
    pending  true   a draft not yet offered to the cloud (it could not be reached); it is
                    offered the next time the category is used

config.yaml's ``definitions-folder`` names the folder (default <work-folder>/definitions).

How a category gets its definition (``resolve``):
  1. the repo has it                         -> use it
  2. the cloud has it                        -> copy it into the repo (origin cloud)
  3. category-definer drafts one             -> offer it to the cloud (create-only, the first
     client's text becomes the shared one); the cloud's answer, ours or an earlier client's,
     is saved into the repo (origin cloud).  Cloud unreachable -> saved as local, pending.

When the folder does not exist yet it is made (``init``): config.yaml's old
``category-definitions:`` section is moved into it (that section is then replaced
by ``definitions-folder:``) and every cloud definition is downloaded.

The cloud is used only when category-builder: jev: is in cloud mode with its token
set (``cloud_for``); otherwise the repo is all there is, and drafts stay pending.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import yaml
from jev_core import JevError

log = logging.getLogger("category_builder.definitions")

FOLDER_KEY = "definitions-folder"
SECTION = "category-definitions"  # where config.yaml kept them before the repo; migrated
SECTION_HEADER = (
    "# --- category definitions (all builders) ------------------------------------\n"
    "# Shared by every category builder; drafted by category-definer, edit freely.\n"
    "# Changing a definition clears the llm builder's cached answers for it.\n"
    "# Delete an entry to have category-definer draft a new one.\n"
)
FOLDER_HEADER = (
    "# --- category definitions (all builders) ------------------------------------\n"
    "# One <CAT>.json per category, kept in step with the cloud's shared definitions\n"
    "# (category_builder/definitions.py). Delete a file to fetch or draft it again.\n"
)


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
    """The gpa-tools cloud client when jev is in cloud mode with its url and token set, else None."""
    from jev_category import builder as jev_builder  # here: jev_category imports this package

    cfg = jev_builder.jev_settings(raw)
    if cfg["mode"] != "cloud" or not cfg["cloud_url"]:
        return None
    try:
        return jev_builder.client(cfg)
    except Exception:  # noqa: BLE001  the token is not set: work without the cloud
        return None


_UNSET = object()


class Definitions:
    """The local repo: ``data`` is {CATEGORY: definition}, ``entries`` the whole files."""

    def __init__(self, config_path: Path, cloud=_UNSET):
        self.config_path = Path(config_path)
        raw = _load_raw(self.config_path)
        self.folder = repo_folder(raw, self.config_path)
        self._cloud = cloud if cloud is not _UNSET else _UNSET
        self._raw = raw
        self.entries: dict[str, dict] = {}
        self.data: dict[str, str] = {}
        if not self.folder.exists():
            self.init()
        self.reload()

    # ---- the cloud, asked only when needed ----------------------------------
    @property
    def cloud(self):
        if self._cloud is _UNSET:
            self._cloud = cloud_for(self._raw)
        return self._cloud

    # ---- reading and writing the repo ---------------------------------------
    def reload(self) -> None:
        self.entries: dict[str, dict] = {}
        for path in sorted(self.folder.glob("*.json")):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("skipping unreadable definition %s: %s", path, exc)
                continue
            if isinstance(entry, dict) and entry.get("category") and entry.get("definition"):
                self.entries[entry["category"]] = entry
        self.data: dict[str, str] = {c: e["definition"].strip() for c, e in self.entries.items()}

    def file(self, category: str) -> Path:
        return self.folder / f"{category}.json"

    def set(self, category: str, definition: str, origin: str = "local", pending: bool = False) -> dict:
        entry = {"category": category, "definition": definition.strip(), "origin": origin,
                 "pending": pending, "updated_at": _now()}
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.file(category)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(entry, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
        self.entries[category] = entry
        self.data[category] = entry["definition"]
        return entry

    def origin(self, category: str) -> str | None:
        entry = self.entries.get(category)
        return entry.get("origin") if entry else None

    # ---- the cloud ----------------------------------------------------------
    def cloud_definition(self, category: str) -> str | None:
        if self.cloud is None:
            return None
        try:
            return self.cloud.definition(category)
        except (JevError, OSError) as exc:  # OSError: URLError, timeouts
            log.info("could not ask the cloud for %s's definition: %s", category, exc)
            return None

    def publish(self, category: str, definition: str) -> dict:
        """Offer this PC's ``definition`` of a new category to the cloud and save the
        result: the cloud's shared text (ours when we were first) as origin cloud, or ours
        as local and pending when the cloud cannot be reached.  The saved entry, plus
        ``shared``: "created" | "adopted" (an earlier client's won) | "pending"."""
        if self.cloud is not None:
            try:
                reply = self.cloud.create_definition(category, definition)
            except (JevError, OSError) as exc:
                log.info("could not offer %s's definition to the cloud: %s", category, exc)
            else:
                entry = self.set(category, reply["definition"], origin="cloud")
                shared = "created" if reply.get("created") else "adopted"
                if shared == "adopted":
                    log.info("the cloud already had a %s definition; using it instead of this "
                             "PC's draft", category)
                return {**entry, "shared": shared}
        return {**self.set(category, definition, origin="local", pending=True), "shared": "pending"}

    def update(self, category: str, definition: str) -> dict:
        """An edit of an existing category: origin cloud when it equals the cloud's
        shared text, else a local override (its scores are this PC's own)."""
        shared = self.cloud_definition(category)
        if shared is not None and shared.strip() == definition.strip():
            return self.set(category, definition, origin="cloud")
        return self.set(category, definition, origin="local")

    def use_cloud(self, category: str) -> dict | None:
        """Replace this PC's text with the cloud's shared one; None when the cloud has none."""
        shared = self.cloud_definition(category)
        return None if shared is None else self.set(category, shared, origin="cloud")

    def resolve(self, category: str, draft) -> tuple[str, str]:
        """(definition, how): how is "repo", "cloud" (copied from it), or "drafted"
        (``draft()`` wrote it; then offered to the cloud, which may answer with an
        earlier client's text).  ``draft`` raises when it cannot draft."""
        entry = self.entries.get(category)
        if entry:
            if entry.get("pending") and self.cloud is not None:
                return self.publish(category, entry["definition"])["definition"], "repo"
            return entry["definition"].strip(), "repo"
        shared = self.cloud_definition(category)
        if shared is not None:
            self.set(category, shared, origin="cloud")
            return shared.strip(), "cloud"
        return self.publish(category, draft())["definition"], "drafted"

    # ---- making the repo ----------------------------------------------------
    def init(self) -> None:
        """Make the folder: move config.yaml's old section in, then download the cloud's."""
        self.folder.mkdir(parents=True, exist_ok=True)
        old = {str(k).strip().upper(): str(v).strip()
               for k, v in (self._raw.get(SECTION) or {}).items() if v and str(v).strip()}
        shared = {}
        if self.cloud is not None:
            try:
                shared = self.cloud.definitions()
            except (JevError, OSError) as exc:
                log.info("could not download the cloud's definitions: %s", exc)
        for category, definition in shared.items():
            if category not in old:
                self.set(category, definition, origin="cloud")
        for category, definition in old.items():
            if category in shared:
                same = shared[category].strip() == definition
                self.set(category, definition, origin="cloud" if same else "local")
            else:
                # This PC's earlier draft or edit: offered to the cloud when next used.
                self.set(category, definition, origin="local", pending=True)
        log.info("made the definitions repo %s: %d from config.yaml, %d from the cloud",
                 self.folder, len(old), len(set(shared) - set(old)))
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
