"""This laptop's cloud token (jev_category/enroll.py): requested at install, picked up once approved."""

import hashlib
import stat

import pytest

from jev_category import enroll
from jev_category.client import JevError


class FakeServer:
    """The enrollment endpoints of the cloud server: requests by token hash, decided by hand."""

    def __init__(self):
        self.requests: dict[str, dict] = {}
        self.down = False

    def __call__(self, url, path, body=None, timeout=15):
        if self.down:
            raise OSError("connection refused")
        if path == "/v1/requests":
            self.requests.setdefault(body["token_hash"], {**body, "status": "pending"})
            return {"id": body["token_hash"][:12], "status": "pending"}
        req = self.requests.get(path.rsplit("/", 1)[1])
        if not req:
            raise JevError("HTTP 404: no such request")
        return {"status": req["status"]}

    def decide(self, status):
        for r in self.requests.values():
            r["status"] = status


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(enroll, "_call", fake)
    monkeypatch.delenv("GPA_TOOLS_CLOUD_TOKEN", raising=False)
    return fake


def test_request_keeps_the_token_here_and_sends_only_its_hash(server, gpa_tools_home):
    said = enroll.request("https://c.test/", "Jane@School.edu", "hp-envy")
    assert "jane@school.edu:hp-envy" in said
    token = (gpa_tools_home / "pending_token").read_text()
    assert token.startswith("gpat_") and len(token) == 69
    (sent,) = server.requests.values()
    assert sent["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(server.requests)
    assert stat.S_IMODE(gpa_tools_home.stat().st_mode) == 0o700
    assert stat.S_IMODE((gpa_tools_home / "pending_token").stat().st_mode) == 0o600


def test_start_waits_then_picks_up_the_approved_token(server, gpa_tools_home):
    enroll.request("https://c.test", "jane@school.edu", "hp-envy")
    token = (gpa_tools_home / "pending_token").read_text()
    with pytest.raises(enroll.NoToken, match="waiting for approval.*jane@school.edu:hp-envy"):
        enroll.token()
    server.decide("approved")
    assert enroll.token() == token
    assert (gpa_tools_home / "client_token").read_text() == token
    assert not (gpa_tools_home / "pending_token").exists()
    server.down = True  # from now on the file is enough
    assert enroll.token() == token
    assert enroll.request("https://c.test", "jane@school.edu").startswith("this laptop already has")
    server.down = False  # revoked later: --new asks again with a fresh token
    enroll.request("https://c.test", "jane@school.edu", "hp-envy", new=True)
    assert not (gpa_tools_home / "client_token").exists() and len(server.requests) == 2


def test_rejected_unreachable_and_never_requested_each_say_so(server, gpa_tools_home, monkeypatch):
    with pytest.raises(enroll.NoToken, match="none was requested.*GPA_TOOLS_CLOUD_TOKEN"):
        enroll.token()
    enroll.request("https://c.test", "a@b.co", "pc1")
    server.down = True
    with pytest.raises(enroll.NoToken, match="cannot be reached"):
        enroll.token()
    server.down = False
    server.decide("rejected")
    with pytest.raises(enroll.NoToken, match="rejected.*package owner"):
        enroll.token()
    monkeypatch.setenv("GPA_TOOLS_CLOUD_TOKEN", "gpat_env")
    assert enroll.token() == "gpat_env"  # the variable wins over the files


def test_a_request_sent_while_the_server_was_down_is_sent_on_start(server, gpa_tools_home):
    server.down = True
    assert "next time" in enroll.request("https://c.test", "a@b.co", "pc1")
    assert server.requests == {}
    server.down = False
    with pytest.raises(enroll.NoToken, match="waiting"):
        enroll.token()
    assert len(server.requests) == 1
    server.decide("approved")
    assert enroll.token().startswith("gpat_")
