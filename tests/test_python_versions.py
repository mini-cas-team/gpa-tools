"""gpa-tools runs on Python 3.14 and 3.12 (install/check.py PYTHON_SUPPORTED).

Developed on 3.14, so these catch what would break only on 3.12:
  - syntax newer than 3.12 (``except A, B:`` without parentheses, t-strings, ...)
  - an annotation 3.12 cannot evaluate: 3.14 evaluates annotations lazily, 3.12
    when the function or class is defined, so a forward reference without
    ``from __future__ import annotations`` is a NameError on import there
  - a standard-library module newer than 3.12, imported without a fallback
"""

from __future__ import annotations

import ast
import importlib
import inspect
import sys
from pathlib import Path

import pytest

import check

ROOT = Path(__file__).resolve().parent.parent
# modules newer than 3.12 -> the version that added them
NEW_MODULES = {"annotationlib": "3.14", "compression": "3.14", "string.templatelib": "3.14",
               "concurrent.interpreters": "3.14", "dbm.sqlite3": "3.13"}
# scripts that are not importable modules (run from their own folder, or start a program)
NOT_MODULES = {"upload/common/half_experiment.py"}


def _sources() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*.py")
                  if not any(part.startswith((".", "backup", "__pycache__"))
                             for part in p.relative_to(ROOT).parts))


def _module(path: Path) -> str | None:
    rel = path.relative_to(ROOT)
    if path.name == "__main__.py" or rel.as_posix() in NOT_MODULES:
        return None
    if rel.parts[0] in ("install", "tests"):  # both are on sys.path (conftest.py)
        return rel.stem
    parts = rel.with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def test_the_supported_pythons_are_3_14_and_3_12():
    assert check.PYTHON_SUPPORTED == ((3, 14), (3, 12))
    assert check.supported("3.12.3") and check.supported("3.14.4")
    assert not check.supported("3.11.9") and not check.supported("3.13.1") and not check.supported("")
    assert sys.version_info[:2] in check.PYTHON_SUPPORTED


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_the_code_is_python_3_12_syntax(path):
    ast.parse(path.read_text(encoding="utf-8"), str(path), feature_version=(3, 12))


def test_newer_standard_library_modules_are_imported_only_with_a_fallback():
    bad = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        guarded = {id(n) for t in ast.walk(tree) if isinstance(t, ast.Try)
                   and any(isinstance(h.type, ast.Name) and h.type.id in ("ImportError", "ModuleNotFoundError")
                           for h in t.handlers)
                   for n in ast.walk(t)}
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
            for name in names:
                new = next((m for m in NEW_MODULES if name == m or name.startswith(m + ".")), None)
                if new and id(node) not in guarded:
                    bad.append(f"{path.relative_to(ROOT)}:{node.lineno} imports {name} "
                               f"(Python {NEW_MODULES[new]}+) without an ImportError fallback")
    assert not bad, "\n".join(bad)


def _bound_lines(tree: ast.Module) -> dict[str, int]:
    """Each module-level name -> the line it is first bound on."""
    bound: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [(a.asname or a.name).split(".")[0] for a in node.names]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names = [node.id]
        else:
            continue
        for name in names:
            # a def/class binds its name after its annotations are evaluated
            line = node.end_lineno if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else node.lineno
            bound[name] = min(bound.get(name, line), line)
    return bound


def test_no_annotation_names_something_defined_after_it():
    """Without ``from __future__ import annotations``, 3.12 evaluates a module-
    level def's or class's annotations where it stands: every name they use must
    be bound above (3.14 would wait, so importing on 3.14 cannot catch this)."""
    import builtins
    bad = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
               and any(a.name == "annotations" for a in n.names) for n in tree.body):
            continue
        bound = _bound_lines(tree)
        defs = [(n, n.lineno) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            defs += [(n, n.lineno) for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            defs += [(n.annotation, n.lineno) for n in cls.body if isinstance(n, ast.AnnAssign)]
        defs += [(n.annotation, n.lineno) for n in tree.body if isinstance(n, ast.AnnAssign)]
        for node, line in defs:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                annotations = [x.annotation for x in a.posonlyargs + a.args + a.kwonlyargs
                               + [a.vararg, a.kwarg] if x is not None and x.annotation is not None]
                annotations += [node.returns] if node.returns else []
            else:
                annotations = [node]
            for annotation in annotations:
                for name in (n.id for n in ast.walk(annotation) if isinstance(n, ast.Name)):
                    if hasattr(builtins, name) and name not in bound:
                        continue
                    if bound.get(name, line) >= line:
                        bad.append(f"{path.relative_to(ROOT)}:{line} annotation uses {name!r}, "
                                   "not yet defined there on 3.12 (add `from __future__ import annotations`)")
    assert not bad, "\n".join(bad)


def test_every_annotation_evaluates_as_python_3_12_would():
    """Import every module and evaluate each module-, class- and function-level
    annotation now, as 3.12 does on import. On 3.12 the import alone does it."""
    try:
        import annotationlib
    except ImportError:  # 3.12: evaluated on import, so importing is the test
        annotationlib = None
    bad = []

    def evaluate(obj, where):
        if annotationlib is None:
            return
        try:
            annotationlib.get_annotations(obj, format=annotationlib.Format.VALUE)
        except Exception as e:  # noqa: BLE001 - any failure is a 3.12 import error
            bad.append(f"{where}: {type(e).__name__}: {e}")

    for path in _sources():
        name = _module(path)
        if name is None:
            continue
        module = importlib.import_module(name)
        evaluate(module, name)
        for key, value in vars(module).items():
            if getattr(value, "__module__", None) != module.__name__:
                continue
            if inspect.isfunction(value):
                evaluate(value, f"{name}.{key}")
            elif inspect.isclass(value):
                evaluate(value, f"{name}.{key}")
                for attr, member in vars(value).items():
                    if inspect.isfunction(member):
                        evaluate(member, f"{name}.{key}.{attr}")
    assert not bad, "\n".join(bad)
