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


class FakeCloud:
    """The cloud's definition calls: create-only, like jev_cloud."""

    def __init__(self, shared=None, down=False):
        self.shared = dict(shared or {})
        self.down = down
        self.created = []

    def _check(self):
        if self.down:
            raise OSError("cloud unreachable")

    def definitions(self):
        self._check()
        return dict(self.shared)

    def definition(self, category):
        self._check()
        return self.shared.get(category)

    def create_definition(self, category, definition):
        self._check()
        if category in self.shared:
            return {"category": category, "definition": self.shared[category], "created": False}
        self.shared[category] = definition
        self.created.append(category)
        return {"category": category, "definition": definition, "created": True}


def _config(tmp_path, text="# top\na: 1 # keep\ncategory-definitions:\n  STEM: Old.\n  law: Law.\nz: 2\n"):
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    return config


def test_the_repo_is_made_from_config_and_the_cloud(tmp_path):
    config = _config(tmp_path)
    cloud = FakeCloud({"STEM": "Shared STEM.", "MATH": "Math."})
    defs = Definitions(config, cloud=cloud)
    assert defs.folder == tmp_path / "definitions"
    assert defs.data == {"LAW": "Law.", "MATH": "Math.", "STEM": "Old."}
    # STEM differs from the cloud's: this PC's own. LAW is new to the cloud: offered later.
    assert {c: (e["origin"], e["pending"]) for c, e in defs.entries.items()} == {
        "LAW": ("local", True), "MATH": ("cloud", False), "STEM": ("local", False)}
    text = config.read_text(encoding="utf-8")
    assert text.startswith("# top\na: 1 # keep\n") and text.endswith("\nz: 2\n")
    assert SECTION_HEADER not in text
    assert yaml.safe_load(text) == {"a": 1, "z": 2, "definitions-folder": str(defs.folder)}
    # Made once: a later read takes the folder as it is, even with the cloud down.
    assert Definitions(config, cloud=FakeCloud(down=True)).data == defs.data


def test_resolve_takes_the_cloud_s_then_drafts_and_shares(tmp_path):
    config = _config(tmp_path, "a: 1\n")
    cloud = FakeCloud({"MATH": "Math."})
    drafts = []

    def draft(text):
        return lambda: drafts.append(text) or text

    defs = Definitions(config, cloud=cloud)
    assert defs.data == {"MATH": "Math."}
    cloud.shared["BIO"] = "Biology."  # added after this PC's repo was made
    assert defs.resolve("BIO", draft("never")) == ("Biology.", "cloud")
    assert defs.resolve("ART", draft("Art courses.")) == ("Art courses.", "drafted")
    assert cloud.shared["ART"] == "Art courses." and defs.entries["ART"]["origin"] == "cloud"
    assert defs.resolve("ART", draft("never")) == ("Art courses.", "repo")
    assert drafts == ["Art courses."]


def test_a_draft_loses_to_an_earlier_client_s(tmp_path):
    cloud = FakeCloud()
    defs = Definitions(_config(tmp_path, "a: 1\n"), cloud=cloud)

    def other_client_first():
        cloud.shared["LAW"] = "Their law."  # another client shared LAW while this one drafted
        return "My law."

    assert defs.resolve("LAW", other_client_first) == ("Their law.", "drafted")
    assert defs.entries["LAW"]["origin"] == "cloud"


def test_a_draft_made_offline_is_shared_when_next_used(tmp_path):
    config = _config(tmp_path, "a: 1\n")
    defs = Definitions(config, cloud=FakeCloud(down=True))
    assert defs.resolve("ART", lambda: "Art.") == ("Art.", "drafted")
    assert (defs.entries["ART"]["origin"], defs.entries["ART"]["pending"]) == ("local", True)
    cloud = FakeCloud()
    assert Definitions(config, cloud=cloud).resolve("ART", lambda: "never") == ("Art.", "repo")
    assert cloud.created == ["ART"]
    entry = Definitions(config, cloud=cloud).entries["ART"]
    assert (entry["origin"], entry["pending"]) == ("cloud", False)


def test_an_edit_is_a_local_override_until_use_cloud(tmp_path):
    cloud = FakeCloud({"STEM": "Shared."})
    defs = Definitions(_config(tmp_path, "a: 1\n"), cloud=cloud)
    assert defs.update("STEM", "Mine.")["origin"] == "local"
    assert defs.update("STEM", " Shared. ")["origin"] == "cloud"
    defs.update("STEM", "Mine.")
    assert defs.use_cloud("STEM")["definition"] == "Shared." and defs.origin("STEM") == "cloud"
    assert cloud.shared == {"STEM": "Shared."}  # an edit never reaches the cloud
    assert defs.use_cloud("NONE") is None


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
    assert definer.definition_for(config, cfg, "STEM") == ("STEM means science.", "drafted")
    assert definer.definition_for(config, cfg, "STEM") == ("STEM means science.", "repo")
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
