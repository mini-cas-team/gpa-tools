"""The client side of the cloud split: jev_core's shared format, CloudClient, and mode."""

import io
import json
import urllib.error
from pathlib import Path

import pytest

import jev_category.builder as jev_run
import jev_core
from category_builder.common import RunError
from jev_category.client import CloudClient, CloudError, JevClient, JevError
from letter_builder import rank_letters

from conftest import FakeCloud, common_json

FIXTURES = json.loads((Path(jev_core.__file__).with_name("fixtures.json")).read_text(encoding="utf-8"))


def test_jev_core_builds_the_shared_fixtures():
    """gpa-tools-cloud's test/worker.test.js checks the JavaScript side against the same file."""
    c = FIXTURES["course"]
    assert jev_core.course_body(c["model"], c["category"], c["definition"],
                                [tuple(x) for x in c["courses"]]) == c["body"]
    letter = FIXTURES["letter"]
    assert jev_core.letter_body(letter["model"], letter["letter"]) == letter["body"]
    k = FIXTURES["course_key"]
    assert jev_core.course_key(k["code"], k["title"]) == k["key"]


class FakeServer:
    """Stands in for urlopen against the cloud server."""

    def __init__(self, replies):
        self.replies, self.sent = replies, []

    def __call__(self, req, timeout):
        self.sent.append((req.get_method(), req.full_url, req.get_header("Authorization"),
                          json.loads(req.data) if req.data else None))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())


def http_error(code, body):
    return urllib.error.HTTPError("http://c", code, "err", {"retry-after": "0"}, io.BytesIO(body.encode()))


def test_cloud_client_sends_only_data_and_reads_scores(monkeypatch):
    server = FakeServer([{"model": "jev-9", "limits": {}},
                         {"model": "jev-9", "scores": [0.9, 0.1], "usage": {"cached": 1, "asked": 1}},
                         {"model": "jev-9", "choice": "good", "confidence": 0.7}])
    monkeypatch.setattr("jev_category.client.urllib.request.urlopen", server)
    client = CloudClient("https://cloud.test/", "gpat_x")
    assert client.model == "jev-9" and client.model == "jev-9"  # /v1/info asked once
    scores, usage = client.scores("STEM", "Def.", [("MATH 1", "Calc"), ("FREN 1", "French")])
    assert scores == [0.9, 0.1] and usage["cached"] == 1
    assert client.rank_letter("Dear ...") == ("good", 0.7)
    assert [s[:3] for s in server.sent] == [
        ("GET", "https://cloud.test/v1/info", "Bearer gpat_x"),
        ("POST", "https://cloud.test/v1/courses/score", "Bearer gpat_x"),
        ("POST", "https://cloud.test/v1/letters/rank", "Bearer gpat_x")]
    assert server.sent[1][3] == {"category": "STEM", "definition": "Def.",
                                 "courses": [["MATH 1", "Calc"], ["FREN 1", "French"]]}
    assert "questions" not in json.dumps(server.sent)  # the prompts stay on the server


def test_cloud_client_retries_busy_and_reports_errors(monkeypatch):
    server = FakeServer([http_error(503, "{}"),
                         http_error(429, json.dumps({"error": "daily limit reached for a: 2 of 2"}))])
    monkeypatch.setattr("jev_category.client.urllib.request.urlopen", server)
    with pytest.raises(JevError, match="HTTP 429: daily limit reached"):
        CloudClient("https://c", "t", retries=1).scores("STEM", "d", [("A", "b")])
    assert len(server.sent) == 2  # the 503 was retried

    server = FakeServer([{"scores": [0.5]}, {"choice": "superb"}])
    monkeypatch.setattr("jev_category.client.urllib.request.urlopen", server)
    with pytest.raises(JevError, match="got 1 score"):
        CloudClient("https://c", "t").scores("STEM", "d", [("A", "b"), ("C", "d")])
    with pytest.raises(JevError, match="unexpected answer"):
        CloudClient("https://c", "t").rank_letter("x")


def test_direct_client_ranks_a_letter_with_the_shared_question(monkeypatch):
    server = FakeServer([{"answers": {"rank": {"type": "choice", "choice": "excellent", "confidence": 0.8}}}])
    monkeypatch.setattr("jev_category.client.urllib.request.urlopen", server)
    assert JevClient("http://ts", "jev-1", "key").rank_letter("Dear ...") == ("excellent", 0.8)
    assert server.sent[0][3] == jev_core.letter_body("jev-1", "Dear ...")
    assert server.sent[0][2] == "Bearer key"


def settings(**jev):
    return jev_run.jev_settings({"category-builder": {"jev": jev}})


def test_mode_picks_the_client_and_needs_its_own_secret(monkeypatch):
    monkeypatch.delenv("GPA_TOOLS_CLOUD_TOKEN", raising=False)
    monkeypatch.setenv("GPA_TOOLS_KEY", "ts-key")
    assert isinstance(jev_run.client(settings()), JevClient)  # direct is the default
    cloud = settings(mode="Cloud", **{"cloud-url": "https://c.test"})
    with pytest.raises(RunError, match=r"not signed in.*jev_category\.login.*https://c\.test/account"):
        jev_run.client(cloud)
    monkeypatch.setenv("GPA_TOOLS_CLOUD_TOKEN", "gpat_x")
    monkeypatch.delenv("GPA_TOOLS_KEY")
    made = jev_run.client(cloud)  # the TypeSafe key is not needed in cloud mode
    assert isinstance(made, CloudClient) and made.url == "https://c.test"
    with pytest.raises(RunError, match="cloud-url is not set"):
        jev_run.client(settings(mode="cloud"))
    with pytest.raises(RunError, match="mode must be one of"):
        jev_run.client(settings(mode="prod"))
    assert isinstance(rank_letters.jev_client({"category-builder": {"jev": {
        "mode": "cloud", "cloud-url": "https://c.test"}}}), CloudClient)


def test_cloud_run_stamps_the_cache_with_the_servers_model(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(f"work-folder: {tmp_path / 'work'}\nlog-folder: {tmp_path / 'log'}\n"
                      "category-builder:\n  jev:\n    mode: cloud\n    cloud-url: https://c.test\n"
                      "category-definitions:\n  STEM: STEM means science.\n", encoding="utf-8")
    cfg = jev_run.load_config(config)
    common_json(cfg["in"], "ann", [("MATH 1", "Calc", "A", 3, 4.0)])

    cloud = FakeCloud({"STEM": "STEM means science."})
    monkeypatch.setattr(jev_run, "client", lambda cfg: cloud)
    jev_run.run(cfg, "STEM")
    cache = jev_run.ScoreCache(cfg["folder"] / "cache_stem.json", "STEM means science.", "jev-server")
    assert not cache.reset and cache.get("MATH 1", "Calc") == 0.9


def test_seed_uploads_a_matching_cache_in_chunks(tmp_path, monkeypatch, capsys):
    from jev_category import seed

    config = tmp_path / "config.yaml"
    config.write_text(f"work-folder: {tmp_path / 'work'}\ncategory-builder:\n  jev:\n"
                      "    cloud-url: https://c.test\ncategory-definitions:\n  STEM: Science.\n",
                      encoding="utf-8")
    cache = jev_run.ScoreCache(tmp_path / "work" / "jev" / "cache_stem.json", "Science.", "jev-1.14.0")
    (tmp_path / "work" / "jev").mkdir(parents=True)
    cache.put_many({(f"MATH {i}", "t"): 0.9 for i in range(450)}, seconds=1)
    posts = []
    monkeypatch.setattr(seed, "_post", lambda url, body, token, **kw: posts.append((url, body, token))
                        or {"stored": len(body["scores"]), "model": body["model"]})
    monkeypatch.delenv("GPA_TOOLS_CLOUD_ADMIN_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="admin token"):
        seed.main(["--category", "STEM", "--config", str(config)])
    monkeypatch.setenv("GPA_TOOLS_CLOUD_ADMIN_TOKEN", "adm")
    assert seed.main(["--category", "STEM", "--config", str(config)]) == 0
    assert [len(b["scores"]) for _, b, _ in posts] == [400, 50]
    assert posts[0][0] == "https://c.test/v1/admin/scores" and posts[0][2] == "adm"
    assert posts[0][1]["definition"] == "Science." and posts[0][1]["model"] == "jev-1.14.0"
    assert "stored 450 of 450" in capsys.readouterr().out

    repo = tmp_path / "work" / "definitions" / "STEM.json"
    repo.write_text(repo.read_text(encoding="utf-8").replace("Science.", "Other."), encoding="utf-8")
    with pytest.raises(SystemExit, match="another definition"):
        seed.main(["--category", "STEM", "--config", str(config)])


def _cloud_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(f"work-folder: {tmp_path / 'work'}\nlog-folder: {tmp_path / 'log'}\n"
                      "category-builder:\n  jev:\n    mode: cloud\n    cloud-url: https://c.test\n",
                      encoding="utf-8")
    cfg = jev_run.load_config(config)
    common_json(cfg["in"], "ann", [("MATH 1", "Calc", "A", 3, 4.0)])
    return cfg


def test_cloud_run_takes_the_clouds_definition_when_the_copy_has_none(tmp_path, monkeypatch):
    cfg = _cloud_config(tmp_path)
    cloud = FakeCloud({"STEM": "Shared STEM."})
    monkeypatch.setattr(jev_run, "client", lambda cfg: cloud)
    monkeypatch.setattr(jev_run.definer, "draft", lambda *a, **k: pytest.fail("must not draft"))
    jev_run.run(cfg, "STEM")
    assert cloud.asked == ["Shared STEM."]
    saved = json.loads((tmp_path / "work" / "definitions" / "STEM.json").read_text(encoding="utf-8"))
    assert (saved["definition"], saved["source"]) == ("Shared STEM.", "shared")


def test_a_build_with_a_stale_copy_uses_the_server_s_text(tmp_path, monkeypatch, caplog):
    """Another computer of the account changed STEM: the server's 409 brings the new text."""
    caplog.set_level("INFO", "jev_category")
    cfg = _cloud_config(tmp_path)
    cloud = FakeCloud({"STEM": "Shared STEM."})
    monkeypatch.setattr(jev_run, "client", lambda cfg: cloud)
    jev_run.run(cfg, "STEM")
    cloud.private["STEM"] = "My STEM."  # Confirmed on the other computer
    cloud.rev += 1
    jev_run.run(cfg, "STEM")  # the build refreshes the copy first
    assert cloud.asked == ["Shared STEM.", "My STEM."]
    saved = json.loads((tmp_path / "work" / "definitions" / "STEM.json").read_text(encoding="utf-8"))
    assert (saved["definition"], saved["source"]) == ("My STEM.", "private")
    # a copy that is wrong anyway (edited by hand): the server's 409 brings its text
    path = tmp_path / "work" / "definitions" / "STEM.json"
    path.write_text(json.dumps({**saved, "definition": "Hand edit."}), encoding="utf-8")
    jev_run.run(cfg, "STEM")
    assert json.loads(path.read_text(encoding="utf-8"))["definition"] == "My STEM."
    assert "changed on the server" in caplog.text


def test_a_build_while_another_computer_has_the_account_says_so(tmp_path, monkeypatch):
    cfg = _cloud_config(tmp_path)
    cloud = FakeCloud({"STEM": "Shared STEM."})
    monkeypatch.setattr(jev_run, "client", lambda cfg: cloud)
    jev_run.run(cfg, "STEM")
    cloud.in_use = True
    with pytest.raises(RunError, match=r"in use on pc2\. Try again after \d\d:\d\d"):
        jev_run.run(cfg, "STEM")


def test_cloud_client_connects_and_keeps_the_error_body(monkeypatch):
    busy = {"error": "in use on pc2", "in_use": {"label": "pc2", "until": "2026-11-22T11:12:00Z"}}
    server = FakeServer([{"email": "a@b.c", "rev": "1.0", "definitions": {}, "entries": {}, "n": {}},
                         {"released": True},
                         http_error(423, json.dumps(busy)),
                         http_error(429, json.dumps({"error": "no Update left", "n": {"left": 0}})),
                         http_error(429, json.dumps({"error": "daily limit reached for a@b.c"}))])
    monkeypatch.setattr("jev_category.client.urllib.request.urlopen", server)
    c = CloudClient("https://c", "t")
    assert c.connect(since="1.0")["email"] == "a@b.c"
    assert server.sent[0][:2] == ("POST", "https://c/v1/connect?since=1.0")
    assert c.disconnect() is True and server.sent[1][0] == "DELETE"
    with pytest.raises(CloudError) as e:
        c.listing()
    assert (e.value.status, e.value.body["in_use"]["label"]) == (423, "pc2")
    with pytest.raises(CloudError) as e:
        c.set_private("STEM", "Mine.", "Shared.")
    assert (e.value.status, e.value.body["n"]) == (429, {"left": 0})
    assert server.sent[3][:2] == ("PUT", "https://c/v1/definitions/STEM/private")
    assert server.sent[3][3] == {"definition": "Mine.", "base": "Shared."}
    with pytest.raises(CloudError, match="daily limit"):  # not retried: waiting does not help
        c.scores("STEM", "Def.", [("MATH 1", "Calc")])
    assert len(server.sent) == 5


def test_cloud_client_definition_is_none_on_404(monkeypatch):
    server = FakeServer([{"category": "STEM", "definition": " Science. "},
                         http_error(404, json.dumps({"error": "no definition for LAW"}))])
    monkeypatch.setattr("jev_category.client.urllib.request.urlopen", server)
    c = CloudClient("https://c", "t")
    assert c.definition("stem") == "Science." and c.definition("law") is None
    assert server.sent[0][1] == "https://c/v1/definitions/STEM"


def test_cloud_client_lists_and_creates_definitions(monkeypatch):
    server = FakeServer([{"definitions": {"STEM": "S.", "LAW": "L."}},
                         {"category": "ART", "definition": "Art.", "created": True},
                         {"category": "ART", "definition": "", "created": False}])
    monkeypatch.setattr("jev_category.client.urllib.request.urlopen", server)
    c = CloudClient("https://c", "t")
    assert c.definitions() == {"STEM": "S.", "LAW": "L."}
    assert c.create_definition("art", "Art.", "gemini") == {"category": "ART", "definition": "Art.", "created": True}
    assert server.sent[1][1] == "https://c/v1/definitions/ART"
    assert server.sent[1][3] == {"definition": "Art.", "source": "gemini"}
    with pytest.raises(JevError, match="unexpected answer"):
        c.create_definition("art", "Art.")
