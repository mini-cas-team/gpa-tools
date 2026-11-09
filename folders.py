"""Where gpa-tools reads its input and keeps what it makes.

config.yaml:

    input-folder: ~/Templates        # raw input, <input-folder>/<component>/
    work-folder: ~/Templates/work    # what the tools make, <work-folder>/<component>/,
                                     # and each builder's <work-folder>/<builder>/
    components: [transcripts, letters, personal]

    component     input (never written by a run)   work (made from the input)
    transcripts   <name>.pdf                       <name>.txt, <name>.common.json
    letters       <key>_letter<N>.pdf              <key>_letter<N>.txt, letter_ranks.csv
    personal      <name>.profile                   <name>.json

Both folders must be set (KeyError otherwise; there is no default, so nothing
is ever read or written somewhere the config does not name).  Every file in a
work folder can be made again from the input.  ``components``
names the three the code uses; leaving one out turns that component off (its
folders raise KeyError) rather than adding or renaming anything.
"""

from __future__ import annotations

from pathlib import Path

COMPONENTS = ("transcripts", "letters", "personal")


def components(cfg: dict) -> list[str]:
    """config.yaml's components, else all three."""
    listed = cfg.get("components")
    return list(COMPONENTS) if listed is None else [str(c) for c in listed]


def _check(cfg: dict, component: str) -> None:
    if component not in COMPONENTS:
        raise ValueError(f"unknown component {component!r}; one of {', '.join(COMPONENTS)}")
    if component not in components(cfg):
        raise KeyError(f"{component} is not in config.yaml's components")


def input_folder(cfg: dict, component: str) -> Path:
    """<input-folder>/<component>; KeyError when input-folder is not set."""
    _check(cfg, component)
    if not cfg.get("input-folder"):
        raise KeyError("input-folder is not set in config.yaml")
    return Path(cfg["input-folder"]).expanduser() / component


def input_or_none(cfg: dict, component: str) -> Path | None:
    """input_folder(), or None when it is not configured."""
    try:
        return input_folder(cfg, component)
    except KeyError:
        return None


def work_or_none(cfg: dict, component: str) -> Path | None:
    """work_folder(), or None when it is not configured."""
    try:
        return work_folder(cfg, component)
    except KeyError:
        return None


def work_root(cfg: dict) -> Path:
    """config.yaml's work-folder; KeyError when it is not set."""
    if not cfg.get("work-folder"):
        raise KeyError("work-folder is not set in config.yaml")
    return Path(cfg["work-folder"]).expanduser()


def work_folder(cfg: dict, component: str) -> Path:
    """<work-folder>/<component>; KeyError when work-folder is not set."""
    _check(cfg, component)
    return work_root(cfg) / component
