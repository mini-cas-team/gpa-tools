from pathlib import Path

import pytest
import yaml

import category_builder
import out_builder
from category_builder import common, definer
from category_builder.definitions import SECTION_HEADER, Definitions

from conftest import FakeLLM, common_json


def test_registry_has_llm_and_rejects_unknown_names():
    assert category_builder.names() == ["llm", "jev"]
    assert category_builder.get("llm").NAME == "llm"
    with pytest.raises(category_builder.UnknownBuilder, match="known: llm"):
        category_builder.get("nope")


def test_every_registered_builder_has_the_contract():
    for name in category_builder.names():
        module = category_builder.get(name)
        assert callable(module.load_config) and callable(module.run)


def test_active_builder_and_folders():
    cfg = {"work-folder": "/w"}
    assert category_builder.active(cfg) == "llm"
    assert category_builder.active({"active-category-builder": "nope"}) == "llm"
    assert category_builder.folder(cfg, "llm").as_posix() == "/w/llm"
    with pytest.raises(KeyError):
        category_builder.folder({}, "llm")


def test_load_student_keeps_only_gpa_courses(tmp_path):
    path = common_json(tmp_path, "ann", [("MATH 1", "Calc", "A", 3, 4.0),
                                         ("ART 1", "Draw", "P", 2, None),
                                         ("PHYS 1", "Lab", "A", 0, 4.0)])
    student = common.load_student(path)
    assert (student.name, student.institution, student.program, student.file) == \
        ("Ann", "TEST UNIVERSITY", "Physics", "ann.pdf")
    assert [c.code for c in student.courses] == ["MATH 1"]


def test_weighted_gpa():
    C = common.Course
    assert common.weighted_gpa([C("t", "a", "", "A", 3, 4.0), C("t", "b", "", "C", 1, 2.0)]) == 3.5
    assert common.weighted_gpa([]) is None


def test_definitions_rewrite_only_their_section(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("# top\na: 1 # keep\ncategory-definitions:\n  STEM: Old.\nz: 2\n",
                      encoding="utf-8")
    defs = Definitions(config)
    assert defs.data == {"STEM": "Old."}
    defs.set("LAW", " Law courses. ")
    defs.set("STEM", "New.")
    text = config.read_text(encoding="utf-8")
    assert text.startswith("# top\na: 1 # keep\n") and text.endswith("\nz: 2\n")
    assert text.count(SECTION_HEADER) == 1
    assert yaml.safe_load(text) == {"a": 1, "z": 2,
                                    "category-definitions": {"LAW": "Law courses.", "STEM": "New."}}


def test_definer_falls_back_to_the_llm_builder_settings():
    cfg = {"category-builder": {"llm": {"model": "small", "ollama-url": "http://h:1"}},
           "work-folder": "/w", "log-folder": "/l"}
    assert definer.load_config(cfg) == {
        "mode": "dev", "retries": 1, "browser-timeout": 120, "browser": "auto",
        "model": "small", "ollama-url": "http://h:1",
        "profile-folder": Path("/w/browser-profiles"), "log-folder": Path("/l"),
        "order": ["chatgpt", "gemini", "ollama"], "headless": {"chatgpt": False, "gemini": False}}
    # an older config.yaml's provider: ollama is ignored
    cfg["category-definer"] = {"model": "big", "provider": "ollama", "mode": "prod",
                               "profile-folder": "/p"}
    got = definer.load_config(cfg)
    assert (got["model"], got["order"], got["headless"], got["profile-folder"]) == (
        "big", ["gemini", "ollama"], {"gemini": True}, Path("/p"))
    assert "provider" not in got
    assert definer.describe(got) == "prod: gemini (headless) -> ollama big at http://h:1"
    with pytest.raises(ValueError, match="'test'"):
        definer.load_config({"category-definer": {"mode": "test"}})
    with pytest.raises(ValueError, match="'firefox'"):
        definer.load_config({"category-definer": {"browser": "firefox"}})


def test_llm_models_reads_the_two_models_and_older_keys():
    assert category_builder.llm_models({}) == {
        "category-build-model": "qwen2.5:3b", "category-definition-model": "qwen2.5:3b",
        "ollama-url": "http://localhost:11434", "definer-ollama-url": "http://localhost:11434"}
    cfg = {"category-builder": {"llm": {"category-build-model": "b", "category-definition-model": "d",
                                        "ollama-url": "http://h:1", "model": "old"}},
           "category-definer": {"model": "older"}}
    models = category_builder.llm_models(cfg)
    assert (models["category-build-model"], models["category-definition-model"]) == ("b", "d")
    assert definer.load_config(cfg)["model"] == "d"
    del cfg["category-builder"]["llm"]["category-definition-model"]
    assert definer.load_config(cfg)["model"] == "older"


def test_definer_modes_pass_headless_and_browser_to_the_sites(monkeypatch):
    made = []

    class Client:
        def __init__(self, site, profile_root, timeout, headless, browser):
            made.append((site, timeout, headless, browser))

        def define(self, category):
            raise OSError("down")

    monkeypatch.setattr("category_definition_builder.BrowserClient", Client)
    for step in definer.BROWSER_STEPS:  # undo conftest's no_browser for this test
        monkeypatch.setitem(definer.PROVIDERS, step, definer._browser(step))
    monkeypatch.setitem(definer.PROVIDERS, "ollama", lambda c: FakeLLM())
    cfg = {"category-definer": {"retries": 0, "browser": "msedge", "browser-timeout": 9}}
    assert definer.draft(cfg, "LAW").source == "ollama"
    assert made == [("chatgpt", 9, False, "msedge"), ("gemini", 9, False, "msedge")]
    made.clear()
    cfg["category-definer"]["mode"] = "prod"
    assert definer.draft(cfg, "LAW").source == "ollama"
    assert made == [("gemini", 9, True, "msedge")]
    with pytest.raises(ValueError, match="chatgpt is not a step of mode prod"):
        definer.draft(cfg, "LAW", only="chatgpt")


def test_definer_tries_steps_in_order_and_logs_every_attempt(tmp_path, monkeypatch):
    calls = []

    class Flaky:
        """chatgpt fails both tries, gemini's first try fails, its retry works."""

        def __init__(self, step):
            self.step = step

        def define(self, category):
            calls.append(self.step)
            if self.step == "chatgpt" or calls.count("gemini") == 1:
                raise OSError(f"{self.step} down")
            return f"{category} from {self.step}."

    for step in ("chatgpt", "gemini", "ollama"):
        monkeypatch.setitem(definer.PROVIDERS, step, lambda c, s=step: Flaky(s))
    cfg = {"log-folder": str(tmp_path / "log")}
    draft = definer.draft(cfg, "LAW")
    assert (draft.definition, draft.source) == ("LAW from gemini.", "gemini")
    assert calls == ["chatgpt", "chatgpt", "gemini", "gemini"]  # ollama never asked
    assert [(a.step, a.attempt, a.error) for a in draft.attempts] == [
        ("chatgpt", 1, "OSError: chatgpt down"), ("chatgpt", 2, "OSError: chatgpt down"),
        ("gemini", 1, "OSError: gemini down"), ("gemini", 2, None)]
    log = (tmp_path / "log" / definer.LOG_NAME).read_text()
    assert "LAW: drafted by gemini (3 failure(s))" in log
    assert "  chatgpt try 2 (0.0s) failed: OSError: chatgpt down" in log

    cfg["category-definer"] = {"retries": 0}
    with pytest.raises(definer.DraftFailed) as failed:
        definer.draft(cfg, "ART", only="chatgpt")
    assert [a.step for a in failed.value.attempts] == ["chatgpt"]
    assert isinstance(failed.value, OSError)
    assert "ART: every step failed (1 failure(s))" in (tmp_path / "log" / definer.LOG_NAME).read_text()


def test_definition_for_drafts_once(tmp_path, monkeypatch):
    llm = FakeLLM()
    monkeypatch.setitem(definer.PROVIDERS, "ollama", lambda c: llm)
    config = tmp_path / "config.yaml"
    config.write_text("a: 1\n", encoding="utf-8")
    cfg = {"log-folder": str(tmp_path / "log")}
    assert definer.definition_for(config, cfg, "STEM") == ("STEM means science.", True)
    assert definer.definition_for(config, cfg, "STEM") == ("STEM means science.", False)
    assert llm.defined == ["STEM"]


def test_out_builder_copies_only_the_gpa_csv(tmp_path):
    cfg = {"work-folder": str(tmp_path / "work"), "out-folder": str(tmp_path / "out")}
    built = tmp_path / "work" / "llm"
    built.mkdir(parents=True)
    (built / "stem_gpa.csv").write_text("student\nAnn\n")
    (built / "stem_courses.csv").write_text("x\n")
    target = out_builder.publish(cfg, "llm", "STEM")
    assert target == tmp_path / "out" / "stem_gpa.csv" and target.read_text() == "student\nAnn\n"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["stem_gpa.csv"]
    with pytest.raises(FileNotFoundError, match="no law_gpa.csv from the llm builder"):
        out_builder.publish(cfg, "llm", "LAW")
    with pytest.raises(category_builder.UnknownBuilder):
        out_builder.publish(cfg, "nope", "STEM")


def test_out_builder_cli(tmp_path, capsys):
    from out_builder.__main__ import main

    config = tmp_path / "config.yaml"
    config.write_text(f"work-folder: {tmp_path / 'work'}\nout-folder: {tmp_path / 'out'}\n")
    assert main(["--config", str(config), "--category", "stem"]) == 1
    assert "no stem_gpa.csv" in capsys.readouterr().err
    (tmp_path / "work" / "llm").mkdir(parents=True)
    (tmp_path / "work" / "llm" / "stem_gpa.csv").write_text("a\n")
    assert main(["--config", str(config), "--category", "stem"]) == 0
    assert (tmp_path / "out" / "stem_gpa.csv").read_text() == "a\n"
