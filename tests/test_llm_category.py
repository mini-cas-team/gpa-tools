import csv
import io
import json

import pytest
import yaml

import llm_category.builder as llm_run
from category_builder import definer
from category_builder.common import RunError
from category_builder.definitions import Definitions
from llm_category.llm import OllamaClient
from llm_category.store import AnswerCache

from conftest import FakeLLM, common_json


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture
def cfg(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(f"work-folder: {tmp_path / 'work'}\nout-folder: {tmp_path / 'out'}\n"
                      f"log-folder: {tmp_path / 'log'}\n", encoding="utf-8")
    return llm_run.load_config(config)


@pytest.fixture
def fake_llm(monkeypatch):
    """One FakeLLM for both the builder's questions and the definer's drafts."""
    llm = FakeLLM()
    monkeypatch.setattr(llm_run, "OllamaClient", lambda url, model: llm)
    monkeypatch.setitem(definer.PROVIDERS, "ollama", lambda c: llm)
    return llm


def test_load_config_reads_category_builder_llm(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("work-folder: ~/w\n", encoding="utf-8")
    cfg = llm_run.load_config(config)
    assert cfg["in"].is_absolute() and cfg["in"].name == "w"
    assert cfg["folder"] == cfg["in"] / "llm"
    assert (cfg["model"], cfg["url"]) == ("qwen2.5:3b", "http://localhost:11434")
    config.write_text("work-folder: /w\ncategory-builder:\n  llm:\n    model: big\n"
                      "    ollama-url: http://h:1\n", encoding="utf-8")
    cfg = llm_run.load_config(config)
    assert (cfg["model"], cfg["url"]) == ("big", "http://h:1")  # an older config.yaml's key
    config.write_text("work-folder: /w\ncategory-builder:\n  llm:\n    model: old\n"
                      "    category-build-model: build\n    category-definition-model: define\n",
                      encoding="utf-8")
    assert llm_run.load_config(config)["model"] == "build"


def test_ollama_client_posts_a_schema_and_reads_the_answer(monkeypatch):
    sent = []

    def urlopen(req, timeout):
        sent.append(json.loads(req.data))
        answer = {"answer": "yes"} if "yes or no" in sent[-1]["prompt"] else {"definition": " Def. "}
        return io.BytesIO(json.dumps({"response": json.dumps(answer)}).encode())

    monkeypatch.setattr("llm_category.llm.urllib.request.urlopen", urlopen)
    client = OllamaClient("http://h:1/", "m")
    assert client.define("STEM") == "Def."
    assert client.is_member("STEM", "Def.", "MATH 1", "Calc") is True
    assert [b["model"] for b in sent] == ["m", "m"]
    assert sent[1]["format"]["properties"]["answer"]["enum"] == ["yes", "no"]
    assert "MATH 1 - Calc" in sent[1]["prompt"] and sent[1]["options"]["temperature"] == 0


def test_answer_cache_keys_persist_and_reset(tmp_path):
    path = tmp_path / "cache.json"
    cache = AnswerCache(path, "def", "m")
    cache.put(" math 1 ", "Calculus ", True, seconds=2.0)
    assert AnswerCache.key("MATH 1", "calculus") == "MATH 1|calculus"
    again = AnswerCache(path, "def", "m")
    assert again.get("MATH 1", "calculus") is True and not again.reset
    assert again.timing == {"count": 1, "seconds": 2.0}
    changed = AnswerCache(path, "new def", "m")
    assert changed.reset and changed.get("MATH 1", "calculus") is None


def test_classify_asks_each_distinct_course_once(cfg, fake_llm):
    common_json(cfg["in"], "ann", [("MATH 1", "Calc", "A", 3, 4.0), ("ENGL 1", "Essay", "B", 3, 3.0)])
    common_json(cfg["in"], "bob", [("math 1", "calc", "B", 3, 3.0)])
    students = llm_run.load_students(cfg["in"])
    cache = AnswerCache(cfg["folder"] / "cache.json", "def", "m")
    progress = []
    llm_run.classify(students, "STEM", "def", cache, fake_llm, lambda d, t: progress.append((d, t)))
    assert fake_llm.asked == ["MATH 1", "ENGL 1"]
    assert progress == [(0, 2), (1, 2), (2, 2)]
    assert [c.in_category for s in students for c in s.courses] == [True, False, True]
    llm_run.classify(students, "STEM", "def", cache, fake_llm)
    assert len(fake_llm.asked) == 2  # all answered from the cache


def test_run_writes_both_csvs_into_work_llm(cfg, fake_llm):
    common_json(cfg["in"], "ann", [("MATH 1", "Calc", "A", 3, 4.0), ("PHYS 1", "Mech", "B", 3, 3.0),
                                   ("ENGL 1", "Essay", "C", 3, 2.0)])
    common_json(cfg["in"], "cat", [("MATH 1", "Calc", "A", 4, 4.0), ("PHYS 2", "E&M", "A", 4, 4.0)])
    common_json(cfg["in"], "bob", [("HIST 1", "Rome", "A", 3, 4.0)])

    gpa_csv = llm_run.run(cfg, "STEM")
    assert gpa_csv == cfg["folder"] / "stem_gpa.csv"
    assert (cfg["folder"] / "cache_stem.json").is_file()
    rows = read_csv(gpa_csv)
    # Data, not a ranking: no rank or gate column; highest GPA first, no-GPA last.
    assert "rank" not in rows[0] and "gate" not in rows[0]
    assert [(r["student"], r["STEM_GPA"], r["stem_courses"]) for r in rows] == [
        ("Cat", "4.0", "2"), ("Ann", "3.5", "2"), ("Bob", "", "0")]
    assert rows[1]["all_courses_GPA"] == "3.0" and rows[1]["stem_all_pct"] == "66.67"
    courses = read_csv(cfg["folder"] / "stem_courses.csv")
    assert [(c["code"], c["is_stem"]) for c in courses if c["student"] == "Ann"] == [
        ("MATH 1", "yes"), ("PHYS 1", "yes"), ("ENGL 1", "no")]
    # No definition existed, so the definer drafted one and it was saved.
    assert Definitions(cfg["config"]).data == {"STEM": "STEM means science."}
    assert fake_llm.defined == ["STEM"]


def test_estimate_uses_cached_timing(cfg):
    common_json(cfg["in"], "ann", [("MATH 1", "Calc", "A", 3, 4.0), ("ENGL 1", "Essay", "B", 3, 3.0)])
    first = llm_run.estimate(cfg, "STEM", "def")
    assert first["pending"] == 2 and first["seconds_per_answer"] == llm_run.FALLBACK_SECONDS_PER_ANSWER
    cache = AnswerCache(cfg["folder"] / "cache_stem.json", "def", cfg["model"])
    cache.put("MATH 1", "Calc", True, seconds=4.0)
    later = llm_run.estimate(cfg, "STEM", "def")
    assert (later["courses"], later["pending"], later["seconds"]) == (2, 1, 4)


def test_run_without_transcripts_or_ollama_is_a_run_error(cfg, monkeypatch):
    with pytest.raises(RunError, match="No \\*.common.json"):
        llm_run.run(cfg, "STEM")

    class Down(FakeLLM):
        def define(self, category):
            raise OSError("connection refused")

        def is_member(self, category, definition, code, title):
            raise OSError("connection refused")

    common_json(cfg["in"], "ann", [("MATH 1", "Calc", "A", 3, 4.0)])
    monkeypatch.setattr(llm_run, "OllamaClient", lambda url, model: Down())
    monkeypatch.setitem(definer.PROVIDERS, "ollama", lambda c: Down())
    # No definition, and every definer step fails: each try is in the message.
    with pytest.raises(RunError, match="No definition for STEM .* gemini try 2 .*"
                                       "ollama try 1 .* connection refused"):
        llm_run.run(cfg, "STEM")
    with cfg["config"].open("a", encoding="utf-8") as fh:
        fh.write("category-definitions:\n  STEM: Science.\n")
    with pytest.raises(RunError, match="Cannot reach Ollama"):
        llm_run.run(cfg, "STEM")


def test_cli_runs_the_llm_builder_and_publishes(cfg, fake_llm, capsys):
    import llm_category.__main__ as cli

    common_json(cfg["in"], "ann", [("MATH 1", "Calc", "A", 3, 4.0)])
    assert cli.main(["--config", str(cfg["config"]), "--category", "stem"]) == 0
    out = cfg["config"].parent / "out"
    assert (cfg["folder"] / "stem_gpa.csv").is_file() and not out.exists()
    assert cli.main(["--config", str(cfg["config"]), "--category", "stem", "--publish"]) == 0
    assert (out / "stem_gpa.csv").read_text() == (cfg["folder"] / "stem_gpa.csv").read_text()
    assert "published" in capsys.readouterr().out


def test_each_model_does_its_own_work(tmp_path, monkeypatch):
    """category-definition-model only drafts; category-build-model only answers."""
    calls = []

    class Recorder:
        def __init__(self, url, model, timeout=300):
            self.model = model

        def define(self, category):
            calls.append((self.model, "define"))
            return f"{category} means maths."

        def is_member(self, category, definition, code, title):
            calls.append((self.model, "is_member"))
            return True

    monkeypatch.setattr("llm_category.llm.OllamaClient", Recorder)  # the definer's import
    monkeypatch.setattr(llm_run, "OllamaClient", Recorder)  # the builder's
    monkeypatch.setitem(definer.PROVIDERS, "ollama", definer._ollama)  # undo conftest's no_browser
    config = tmp_path / "config.yaml"
    config.write_text(f"work-folder: {tmp_path / 'work'}\nlog-folder: {tmp_path / 'log'}\n"
                      "category-builder:\n  llm:\n    category-build-model: small\n"
                      "    category-definition-model: big\n", encoding="utf-8")
    common_json(tmp_path / "work", "ann", [("MATH 1", "Calc", "A", 3, 4.0),
                                           ("PHYS 1", "Mechanics", "B", 3, 3.0)])
    llm_run.run(llm_run.load_config(config), "STEM")  # no STEM definition yet: drafts one first
    assert calls == [("big", "define"), ("small", "is_member"), ("small", "is_member")]
