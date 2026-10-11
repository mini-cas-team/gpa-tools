import pytest
import yaml

import category_builder
import out_builder
from category_builder import common, definer
from category_builder.definitions import SECTION_HEADER, Definitions

from conftest import FakeLLM, common_json


def test_registry_has_llm_and_rejects_unknown_names():
    assert category_builder.names() == ["llm"]
    assert category_builder.get("llm").NAME == "llm"
    with pytest.raises(category_builder.UnknownBuilder, match="known: llm"):
        category_builder.get("jev")


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
    cfg = {"category-builder": {"llm": {"model": "small", "ollama-url": "http://h:1"}}}
    assert definer.load_config(cfg) == {"provider": "ollama", "model": "small",
                                        "ollama-url": "http://h:1"}
    cfg["category-definer"] = {"model": "big"}
    assert definer.load_config(cfg)["model"] == "big"
    with pytest.raises(ValueError, match="provider"):
        definer.load_config({"category-definer": {"provider": "nope"}})


def test_definition_for_drafts_once(tmp_path, monkeypatch):
    llm = FakeLLM()
    monkeypatch.setitem(definer.PROVIDERS, "ollama", lambda c: llm)
    config = tmp_path / "config.yaml"
    config.write_text("a: 1\n", encoding="utf-8")
    assert definer.definition_for(config, {}, "STEM") == ("STEM means science.", True)
    assert definer.definition_for(config, {}, "STEM") == ("STEM means science.", False)
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
        out_builder.publish(cfg, "jev", "STEM")


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
