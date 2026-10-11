import csv
import io
import json
import urllib.error

import pytest

import category_builder
import jev_category.builder as jev_run
from category_builder.common import RunError
from jev_category.client import JevClient, JevError
from jev_category.store import ScoreCache

from conftest import common_json


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class FakeJev:
    """Stands in for JevClient: MATH/PHYS courses score high, the rest low."""

    def __init__(self, url="http://fake", model="jev-test", api_key="k", scores=None):
        self.url, self.model = url, model
        self.score_for = scores or {"MATH": 0.97, "PHYS": 0.9, "ECON": 0.4}
        self.batches: list[list[str]] = []

    def scores(self, category, definition, courses):
        self.batches.append([code for code, _ in courses])
        return ([next((v for k, v in self.score_for.items() if code.startswith(k)), 0.05)
                 for code, _ in courses], {"input_tokens": 100 * len(courses), "output_tokens": 0})


@pytest.fixture
def cfg(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(f"work-folder: {tmp_path / 'work'}\nout-folder: {tmp_path / 'out'}\n"
                      f"log-folder: {tmp_path / 'log'}\ncategory-builder:\n  jev:\n"
                      f"    api-key-env: TEST_JEV_KEY\n    batch-size: 2\n"
                      f"category-definitions:\n  STEM: STEM means science.\n", encoding="utf-8")
    return jev_run.load_config(config)


@pytest.fixture
def fake_jev(monkeypatch):
    jev = FakeJev()
    monkeypatch.setattr(jev_run, "JevClient", lambda url, model, key: jev)
    monkeypatch.setenv("TEST_JEV_KEY", "secret")
    return jev


def test_jev_is_a_registered_builder():
    assert "jev" in category_builder.names()
    assert category_builder.get("jev") is jev_run
    assert category_builder.active({"active-category-builder": "jev"}) == "jev"


def test_load_config_defaults_and_overrides(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("work-folder: /w\n", encoding="utf-8")
    cfg = jev_run.load_config(config)
    assert cfg["folder"] == cfg["in"] / "jev"
    assert (cfg["model"], cfg["url"], cfg["key_env"], cfg["threshold"], cfg["batch_size"]) == (
        "jev-1.13.0", "https://api.typesafe.ai/v1/systemone", "GPA_TOOLS_KEY", 0.5, 20)
    config.write_text("work-folder: /w\ncategory-builder:\n  jev:\n    model: jev-latest\n"
                      "    api-url: http://h/v1\n    threshold: 0.7\n    batch-size: 5\n",
                      encoding="utf-8")
    cfg = jev_run.load_config(config)
    assert (cfg["model"], cfg["url"], cfg["threshold"], cfg["batch_size"]) == (
        "jev-latest", "http://h/v1", 0.7, 5)


def test_client_sends_one_noul_per_course_and_reads_scores(monkeypatch):
    sent = []

    def urlopen(req, timeout):
        sent.append((req, json.loads(req.data)))
        return io.BytesIO(json.dumps({"model": "jev-1.13.0", "answers": {
            "q01": {"type": "noul", "noul": 0.98}, "q02": {"type": "noul", "noul": 0.04}},
            "usage": {"input_tokens": 500, "output_tokens": 20}}).encode())

    monkeypatch.setattr("jev_category.client.urllib.request.urlopen", urlopen)
    client = JevClient("http://h/v1/systemone", "jev-1.13.0", "secret")
    scores, usage = client.scores("STEM", "Def.", [("MATH 1", "Calc"), ("FREN 1", "French")])
    assert scores == [0.98, 0.04] and usage["input_tokens"] == 500
    req, body = sent[0]
    assert req.full_url == "http://h/v1/systemone"
    assert req.get_header("Authorization") == "Bearer secret"
    assert body["model"] == "jev-1.13.0"
    assert body["state"] == {"category": "STEM", "definition": "Def.",
                             "course_01": "MATH 1 - Calc", "course_02": "FREN 1 - French"}
    assert body["questions"]["q02"]["type"] == "noul"
    assert "`course_02`" in body["questions"]["q02"]["instructions"]


def http_error(code, body, headers=None):
    return urllib.error.HTTPError("http://h", code, "err", headers or {}, io.BytesIO(body.encode()))


def test_client_retries_rate_limits_and_reports_errors(monkeypatch):
    replies = [http_error(429, "{}", {"retry-after": "0"}),
               http_error(422, json.dumps({"detail": {"message": "Unknown model: jev"}}))]

    def urlopen(req, timeout):
        raise replies.pop(0)

    monkeypatch.setattr("jev_category.client.urllib.request.urlopen", urlopen)
    with pytest.raises(JevError, match="HTTP 422: Unknown model: jev"):
        JevClient("http://h", "jev", "k").scores("STEM", "Def.", [("MATH 1", "Calc")])
    assert replies == []  # the 429 was retried


def test_score_cache_keeps_scores_and_resets_on_model_change(tmp_path):
    path = tmp_path / "cache.json"
    cache = ScoreCache(path, "def", "jev-1.13.0")
    cache.put_many({("math 1", "Calc"): 0.97, ("ENGL 1", "Essay"): 0.03}, seconds=0.4)
    again = ScoreCache(path, "def", "jev-1.13.0")
    assert again.get("MATH 1", "calc") == 0.97 and again.timing == {"count": 2, "seconds": 0.4}
    assert ScoreCache(path, "def", "jev-latest").reset


def test_classify_batches_distinct_courses_and_applies_threshold(cfg, fake_jev):
    common_json(cfg["in"], "ann", [("MATH 1", "Calc", "A", 3, 4.0), ("ENGL 1", "Essay", "B", 3, 3.0),
                                   ("ECON 1", "Micro", "B", 3, 3.0)])
    common_json(cfg["in"], "bob", [("math 1", "calc", "B", 3, 3.0)])
    students = jev_run.load_students(cfg["in"])
    cache = ScoreCache(cfg["folder"] / "cache.json", "def", "jev-test")
    progress = []
    jev_run.classify(students, "STEM", "def", cache, fake_jev, threshold=0.5, batch_size=2,
                     progress=lambda d, t: progress.append((d, t)))
    assert fake_jev.batches == [["MATH 1", "ENGL 1"], ["ECON 1"]]
    assert progress == [(0, 3), (2, 3), (3, 3)]
    assert [c.in_category for s in students for c in s.courses] == [True, False, False, True]
    jev_run.classify(students, "STEM", "def", cache, fake_jev, threshold=0.3)
    assert len(fake_jev.batches) == 2  # all from the cache; only the threshold changed
    assert students[0].courses[2].in_category is True  # ECON 0.4 > 0.3


def test_run_writes_csvs_with_scores_into_work_jev(cfg, fake_jev):
    common_json(cfg["in"], "ann", [("MATH 1", "Calc", "A", 3, 4.0), ("ENGL 1", "Essay", "C", 3, 2.0)])
    gpa_csv = jev_run.run(cfg, "STEM")
    assert gpa_csv == cfg["folder"] / "stem_gpa.csv"
    assert read_csv(gpa_csv)[0]["STEM_GPA"] == "4.0"
    courses = read_csv(cfg["folder"] / "stem_courses.csv")
    assert [(c["code"], c["is_stem"], c["stem_score"]) for c in courses] == [
        ("MATH 1", "yes", "0.97"), ("ENGL 1", "no", "0.05")]


def test_run_without_api_key_is_a_clear_error(cfg, fake_jev, monkeypatch):
    common_json(cfg["in"], "ann", [("MATH 1", "Calc", "A", 3, 4.0)])
    monkeypatch.delenv("TEST_JEV_KEY")
    with pytest.raises(RunError, match="TEST_JEV_KEY"):
        jev_run.run(cfg, "STEM")


def test_run_turns_api_errors_into_run_errors(cfg, fake_jev, monkeypatch):
    common_json(cfg["in"], "ann", [("MATH 1", "Calc", "A", 3, 4.0)])

    def refuse(*args):
        raise JevError("HTTP 401: invalid key")

    monkeypatch.setattr(fake_jev, "scores", refuse)
    with pytest.raises(RunError, match="401"):
        jev_run.run(cfg, "STEM")


def test_estimate_counts_uncached_courses(cfg):
    common_json(cfg["in"], "ann", [("MATH 1", "Calc", "A", 3, 4.0), ("ENGL 1", "Essay", "B", 3, 3.0)])
    est = jev_run.estimate(cfg, "STEM", "STEM means science.")
    assert (est["courses"], est["pending"]) == (2, 2)
