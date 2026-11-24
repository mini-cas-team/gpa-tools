from pathlib import Path

import pytest
import yaml

import category_builder
import out_builder
from category_builder import common, definer
from category_builder.definitions import SECTION_HEADER, Definitions

from conftest import FakeCloud, FakeDefiner, common_json


def test_registry_has_jev_and_rejects_unknown_names():
    assert category_builder.names() == ["jev"]
    assert category_builder.get("jev").NAME == "jev"
    with pytest.raises(category_builder.UnknownBuilder, match="known: jev"):
        category_builder.get("llm")  # removed with Ollama


def test_every_registered_builder_has_the_contract():
    for name in category_builder.names():
        module = category_builder.get(name)
        assert callable(module.load_config) and callable(module.run)


def test_active_builder_and_folders():
    cfg = {"work-folder": "/w"}
    assert category_builder.active(cfg) == "jev"
    assert category_builder.active({"active-category-builder": "llm"}) == "jev"  # an older config
    assert category_builder.folder(cfg, "jev").as_posix() == "/w/jev"
    with pytest.raises(KeyError):
        category_builder.folder({}, "jev")


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


def _config(tmp_path, text="# top\na: 1 # keep\ncategory-definitions:\n  STEM: Old.\n  law: Law.\nz: 2\n"):
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    return config


def test_the_folder_is_made_from_config_and_its_texts_are_offered_on_the_first_sync(tmp_path):
    config = _config(tmp_path)
    cloud = FakeCloud({"STEM": "Shared STEM.", "MATH": "Math."})
    defs = Definitions(config, cloud=cloud)
    assert defs.folder == tmp_path / "definitions"
    assert defs.data == {"LAW": "Law.", "STEM": "Old."}  # nothing asked yet
    assert defs.source("STEM") == "local"
    text = config.read_text(encoding="utf-8")
    assert text.startswith("# top\na: 1 # keep\n") and text.endswith("\nz: 2\n")
    assert SECTION_HEADER not in text
    assert yaml.safe_load(text) == {"a": 1, "z": 2, "definitions-folder": str(defs.folder)}
    # the first sync: config.yaml's texts are offered (Add); the server's copy replaces the folder
    assert defs.sync() is True
    assert cloud.created == ["LAW"]  # STEM was on the server: its text wins
    assert defs.data == {"LAW": "Law.", "MATH": "Math.", "STEM": "Shared STEM."}
    assert {c: defs.source(c) for c in defs.data} == {"LAW": "shared", "MATH": "shared", "STEM": "shared"}
    assert (defs.folder / "old-local" / "STEM.json").exists()  # this computer's old text is kept
    assert defs.sync() is False  # nothing changed since
    assert cloud.calls[-1] == "listing"


def test_the_folder_follows_the_server_another_computer_s_changes_included(tmp_path):
    cloud = FakeCloud({"STEM": "Science.", "LAW": "Law."})
    defs = Definitions(_config(tmp_path, "a: 1\n"), cloud=cloud)
    defs.sync()
    # another computer of the account: a private STEM; the owner removed LAW, added MATH
    cloud.private["STEM"] = "Mine."
    del cloud.shared["LAW"]
    cloud.shared["MATH"] = "Math."
    cloud.rev += 1
    assert defs.sync() is True
    assert defs.data == {"MATH": "Math.", "STEM": "Mine."}
    assert defs.source("STEM") == "private" and defs.n == {"limit": 3, "used": 0, "left": 3}
    assert not (defs.folder / "LAW.json").exists()


def test_resolve_takes_the_copy_then_the_server_s_then_an_ai_draft(tmp_path):
    cloud = FakeCloud({"MATH": "Math."})
    drafts = []

    def draft(text):
        return lambda: drafts.append(text) or (text, "gemini")

    defs = Definitions(_config(tmp_path, "a: 1\n"), cloud=cloud)
    assert defs.resolve("MATH", draft("never")) == ("Math.", "cloud")
    assert defs.resolve("MATH", draft("never")) == ("Math.", "repo")
    assert defs.resolve("ART", draft("Art courses.")) == ("Art courses.", "drafted")
    assert cloud.shared["ART"] == "Art courses." and cloud.ai["ART"] == "gemini"
    assert defs.resolve("ART", draft("never")) == ("Art courses.", "repo")
    assert drafts == ["Art courses."]


def test_a_draft_loses_to_another_account_s(tmp_path):
    cloud = FakeCloud()
    defs = Definitions(_config(tmp_path, "a: 1\n"), cloud=cloud)

    def other_account_first():
        cloud.shared["LAW"] = "Their law."  # another account added LAW while this one drafted
        return "My law.", "chatgpt"

    assert defs.resolve("LAW", other_account_first) == ("Their law.", "drafted")
    assert defs.source("LAW") == "shared"


def test_confirm_makes_the_account_s_private_text_and_reset_drops_it(tmp_path):
    from category_builder.definitions import Changed
    from jev_category.client import CloudError

    cloud = FakeCloud({"STEM": "Shared."}, n_limit=1)
    defs = Definitions(_config(tmp_path, "a: 1\n"), cloud=cloud)
    defs.sync()
    entry = defs.confirm("STEM", " Mine. ")
    assert (entry["definition"], entry["source"]) == ("Mine.", "private")
    assert defs.n == {"limit": 1, "used": 1, "left": 0}
    assert cloud.shared == {"STEM": "Shared."}  # the shared text stays
    with pytest.raises(CloudError) as over:
        defs.confirm("STEM", "Mine again.")
    assert over.value.status == 429
    # changed on another computer since this one showed it: the server's text comes back
    cloud.n_limit = None
    cloud.private["STEM"] = "Other computer's."
    with pytest.raises(Changed) as changed:
        defs.confirm("STEM", "Mine again.")
    assert changed.value.entry["definition"] == "Other computer's." and defs.data["STEM"] == "Other computer's."
    assert defs.confirm("STEM", "Mine again.")["definition"] == "Mine again."
    assert defs.reset("STEM")["source"] == "shared" and defs.data["STEM"] == "Shared."


def test_without_a_cloud_the_folder_is_all_there_is(tmp_path):
    defs = Definitions(_config(tmp_path, "a: 1\n"), cloud=None)
    assert defs.resolve("ART", lambda: ("Art.", "gemini")) == ("Art.", "drafted")
    assert defs.source("ART") == "local"
    assert defs.confirm("ART", "My art.")["definition"] == "My art."
    assert defs.sync() is False
    with pytest.raises(ValueError, match="needs the cloud"):
        defs.reset("ART")


def test_definer_settings_and_older_ollama_keys():
    cfg = {"work-folder": "/w", "log-folder": "/l"}
    assert definer.load_config(cfg) == {
        "mode": "dev", "retries": 1, "browser-timeout": 120, "browser": "auto",
        "profile-folder": Path("/w/browser-profiles"), "log-folder": Path("/l"),
        "order": ["chatgpt", "gemini"], "headless": {"chatgpt": False, "gemini": False}}
    # an older config.yaml's provider:, model: and ollama-url: are ignored
    cfg["category-definer"] = {"model": "big", "ollama-url": "http://h:1", "provider": "ollama",
                               "mode": "prod", "profile-folder": "/p"}
    got = definer.load_config(cfg)
    assert (got["order"], got["headless"], got["profile-folder"]) == (["gemini"], {"gemini": True}, Path("/p"))
    assert not {"provider", "model", "ollama-url"} & set(got)
    assert definer.describe(got) == "prod: gemini (headless)"
    with pytest.raises(ValueError, match="'test'"):
        definer.load_config({"category-definer": {"mode": "test"}})
    with pytest.raises(ValueError, match="'firefox'"):
        definer.load_config({"category-definer": {"browser": "firefox"}})


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
    cfg = {"category-definer": {"retries": 0, "browser": "msedge", "browser-timeout": 9}}
    with pytest.raises(definer.DraftFailed):  # no step left after the sites
        definer.draft(cfg, "LAW")
    assert made == [("chatgpt", 9, False, "msedge"), ("gemini", 9, False, "msedge")]
    made.clear()
    cfg["category-definer"]["mode"] = "prod"
    with pytest.raises(definer.DraftFailed):
        definer.draft(cfg, "LAW")
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

    for step in ("chatgpt", "gemini"):
        monkeypatch.setitem(definer.PROVIDERS, step, lambda c, s=step: Flaky(s))
    cfg = {"log-folder": str(tmp_path / "log")}
    draft = definer.draft(cfg, "LAW")
    assert (draft.definition, draft.source) == ("LAW from gemini.", "gemini")
    assert calls == ["chatgpt", "chatgpt", "gemini", "gemini"]
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


def test_definition_for_drafts_once_in_either_run_mode(tmp_path, monkeypatch):
    gemini = FakeDefiner()
    monkeypatch.setitem(definer.PROVIDERS, "gemini", lambda c: gemini)
    config = tmp_path / "config.yaml"
    config.write_text("a: 1\n", encoding="utf-8")
    cfg = {"log-folder": str(tmp_path / "log"), "run_mode": "dev"}
    assert definer.definition_for(config, cfg, "STEM") == ("STEM means science.", "drafted")
    assert definer.definition_for(config, cfg, "STEM") == ("STEM means science.", "repo")
    # run_mode prod (the default) drafts too, with headless gemini
    assert definer.definition_for(config, {"log-folder": str(tmp_path / "log")}, "LAW") == \
        ("LAW means science.", "drafted")
    assert gemini.defined == ["STEM", "LAW"]


def test_run_mode_is_prod_unless_it_says_dev():
    assert [category_builder.run_mode(c) for c in ({}, {"run_mode": "prod"}, {"run_mode": "Dev"},
                                                   {"run_mode": "test"})] == ["prod", "prod", "dev", "prod"]


def test_out_builder_copies_only_the_gpa_csv(tmp_path):
    cfg = {"work-folder": str(tmp_path / "work"), "out-folder": str(tmp_path / "out")}
    built = tmp_path / "work" / "jev"
    built.mkdir(parents=True)
    (built / "stem_gpa.csv").write_text("student\nAnn\n")
    (built / "stem_courses.csv").write_text("x\n")
    target = out_builder.publish(cfg, "jev", "STEM")
    assert target == tmp_path / "out" / "stem_gpa.csv" and target.read_text() == "student\nAnn\n"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["stem_gpa.csv"]
    with pytest.raises(FileNotFoundError, match="no law_gpa.csv from the jev builder"):
        out_builder.publish(cfg, "jev", "LAW")
    with pytest.raises(category_builder.UnknownBuilder):
        out_builder.publish(cfg, "nope", "STEM")


def test_out_builder_cli(tmp_path, capsys):
    from out_builder.__main__ import main

    config = tmp_path / "config.yaml"
    config.write_text(f"work-folder: {tmp_path / 'work'}\nout-folder: {tmp_path / 'out'}\n")
    assert main(["--config", str(config), "--category", "stem"]) == 1
    assert "no stem_gpa.csv" in capsys.readouterr().err
    (tmp_path / "work" / "jev").mkdir(parents=True)
    (tmp_path / "work" / "jev" / "stem_gpa.csv").write_text("a\n")
    assert main(["--config", str(config), "--category", "stem"]) == 0
    assert (tmp_path / "out" / "stem_gpa.csv").read_text() == "a\n"
