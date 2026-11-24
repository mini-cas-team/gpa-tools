"""This computer's cloud key (jev_category/login.py) and browser_view's Sign in, faked server."""

import json

import pytest

from browser_view.app import create_app
from jev_category import login
from jev_category.client import JevError

PASSWORD = "correct horse 1"


class FakeCloud:
    """The account endpoints of the cloud server: one account, a key per sign-in."""

    def __init__(self):
        self.keys, self.calls, self.down = {}, [], False

    def __call__(self, url, body, token, timeout, retries, method=None):
        self.calls.append((url, body, token, method))
        if self.down:
            raise OSError("connection refused")
        path = url.split("https://c.test", 1)[1]
        if path == "/v1/keys":
            if body["email"].lower() != "ann@school.edu":
                raise JevError("HTTP 401: wrong email or password")
            if body["password"] != PASSWORD:
                raise JevError("HTTP 401: wrong email or password")
            key = f"gpak_{len(self.keys)}"
            self.keys[key] = body["label"]
            return {"key": key, "id": f"id{len(self.keys)}", "email": "ann@school.edu", "label": body["label"],
                    "idle_days": 90}
        if path == "/v1/keys/self":
            self.keys.pop(token, None)
            return {"revoked": True}
        if path == "/v1/info":
            if token not in self.keys:
                raise JevError("HTTP 401: this key expired after 90 days without use: sign in again")
            return {"model": "jev-1", "email": "ann@school.edu", "label": self.keys[token]}
        raise AssertionError(path)


@pytest.fixture
def cloud(monkeypatch):
    fake = FakeCloud()
    monkeypatch.setattr(login, "_post", fake)
    monkeypatch.delenv("GPA_TOOLS_CLOUD_TOKEN", raising=False)
    return fake


def test_sign_in_saves_only_the_key(cloud, gpa_tools_home):
    with pytest.raises(login.NoToken, match="not signed in.*jev_category.login"):
        login.token()
    who = login.sign_in("https://c.test/", "Ann@School.edu", PASSWORD, "hp-envy")
    assert who == {"email": "ann@school.edu", "label": "hp-envy", "id": "id1"}
    assert login.token() == "gpak_0"
    assert (gpa_tools_home / "client_token").stat().st_mode & 0o777 == 0o600
    assert json.loads((gpa_tools_home / "account.json").read_text()) == \
        {"cloud_url": "https://c.test", "email": "ann@school.edu", "label": "hp-envy", "id": "id1"}
    everything = "".join(p.read_text() for p in gpa_tools_home.iterdir())
    assert PASSWORD not in everything, "the password is never saved"
    assert login.status("https://c.test") == ("signed-in", "ann@school.edu")


def test_wrong_password_and_no_server_say_why(cloud, gpa_tools_home):
    with pytest.raises(login.SignInError, match=r"wrong email or password -- no account yet\? .*https://c\.test/account") as e:
        login.sign_in("https://c.test", "ann@school.edu", "nope")
    assert e.value.status == 401
    cloud.down = True
    with pytest.raises(login.SignInError, match="cannot reach https://c.test") as e:
        login.sign_in("https://c.test", "ann@school.edu", PASSWORD)
    assert e.value.status == 0
    assert not (gpa_tools_home / "client_token").exists()
    assert login.status("https://c.test")[0] == "none"


def test_an_expired_key_shows_in_status_and_sign_out_forgets_it(cloud, gpa_tools_home):
    login.sign_in("https://c.test", "ann@school.edu", PASSWORD)
    cloud.keys.clear()  # the server no longer takes it
    state, detail = login.status("https://c.test")
    assert state == "expired" and "90 days" in detail
    cloud.down = True
    assert login.status("https://c.test")[0] == "unknown"
    cloud.down = False
    login.sign_in("https://c.test", "ann@school.edu", PASSWORD)
    assert login.sign_out() is True
    assert cloud.calls[-1][3] == "DELETE" and cloud.calls[-1][0].endswith("/v1/keys/self")
    assert not (gpa_tools_home / "client_token").exists() and not (gpa_tools_home / "account.json").exists()
    assert login.sign_out() is False


def test_the_variable_wins_and_old_files_go(cloud, gpa_tools_home, monkeypatch):
    gpa_tools_home.mkdir()
    (gpa_tools_home / "pending_token").write_text("gpat_waiting")  # left by the old approval flow
    (gpa_tools_home / "request.json").write_text("{}")
    login.sign_in("https://c.test", "ann@school.edu", PASSWORD)
    assert not (gpa_tools_home / "pending_token").exists() and not (gpa_tools_home / "request.json").exists()
    monkeypatch.setenv("GPA_TOOLS_CLOUD_TOKEN", "gpak_env")
    assert login.token() == "gpak_env"


def test_the_command_asks_the_password_and_tries_again(cloud, gpa_tools_home, monkeypatch, tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("category-builder:\n  jev:\n    mode: cloud\n    cloud-url: https://c.test\n")
    answers = iter(["wrong", PASSWORD])
    monkeypatch.setattr(login.getpass, "getpass", lambda prompt: next(answers))
    monkeypatch.setattr(login.sys.stdin, "isatty", lambda: True)
    assert login.main(["--config", str(config), "--email", "ann@school.edu", "--label", "pc1"]) == 0
    out = capsys.readouterr()
    assert "Sign-in failed: wrong email or password" in out.err
    assert "Signed in as ann@school.edu on pc1" in out.out
    assert login.main(["status", "--config", str(config)]) == 0
    assert login.main(["logout", "--config", str(config)]) == 0
    assert login.main(["status", "--config", str(config)]) == 1


def test_three_wrong_passwords_stop(cloud, gpa_tools_home, monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("category-builder:\n  jev:\n    mode: cloud\n    cloud-url: https://c.test\n")
    monkeypatch.setattr(login.getpass, "getpass", lambda prompt: "wrong")
    monkeypatch.setattr(login.sys.stdin, "isatty", lambda: True)
    assert login.main(["--config", str(config), "--email", "ann@school.edu"]) == 1
    assert len([c for c in cloud.calls if c[0].endswith("/v1/keys")]) == 3


# ---- browser_view: the side bar, the Sign in dialog, and the request guard --------------

@pytest.fixture
def app_client(tmp_path, cloud, gpa_tools_home):
    config = tmp_path / "config.yaml"
    config.write_text(f"input-folder: {tmp_path / 'in'}\nwork-folder: {tmp_path / 'work'}\n"
                      "run_mode: prod\ncategory-builder:\n  jev:\n    mode: cloud\n    cloud-url: https://c.test\n")
    return create_app(config).test_client()


def test_the_side_bar_offers_sign_in_then_shows_the_account(app_client, cloud):
    page = app_client.get("/").get_data(as_text=True)
    assert "Not signed in to the cloud" in page and 'id="sign-in-dialog"' in page
    bad = app_client.post("/account/sign-in", data={"email": "ann@school.edu", "password": "nope"})
    assert bad.status_code == 401 and "wrong email or password" in bad.get_json()["error"]
    ok = app_client.post("/account/sign-in", data={"email": "ann@school.edu", "password": PASSWORD})
    assert ok.status_code == 200 and ok.get_json()["email"] == "ann@school.edu"
    page = app_client.get("/").get_data(as_text=True)
    assert "ann@school.edu" in page and "https://c.test/account" in page and 'id="cloud-sign-out"' in page
    assert app_client.get("/account/status").get_json()["state"] == "signed-in"
    assert app_client.post("/account/sign-out").get_json() == {"signed_out": True}
    assert "Not signed in to the cloud" in app_client.get("/").get_data(as_text=True)


def test_no_side_bar_account_without_cloud_mode(tmp_path, gpa_tools_home):
    config = tmp_path / "config.yaml"
    config.write_text(f"input-folder: {tmp_path / 'in'}\nwork-folder: {tmp_path / 'work'}\n")
    page = create_app(config).test_client().get("/").get_data(as_text=True)
    assert "sign-in-dialog" not in page and "Not signed in" not in page


def test_other_sites_cannot_use_the_app(app_client):
    sign_in = {"email": "ann@school.edu", "password": PASSWORD}
    # a page on another site, in the same browser, posting to the app
    assert app_client.post("/account/sign-in", data=sign_in,
                           headers={"Origin": "https://evil.example"}).status_code == 403
    assert app_client.post("/account/sign-in", data=sign_in,
                           headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert app_client.post("/account/sign-in", data=sign_in, headers={"Origin": "null"}).status_code == 403
    # a name pointed at 127.0.0.1 (DNS rebinding): refused even for reading
    assert app_client.get("/", headers={"Host": "evil.example:5001"}).status_code == 403
    # the app's own pages
    assert app_client.post("/account/sign-in", data=sign_in, headers={
        "Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert app_client.get("/", headers={"Host": "127.0.0.1:5001"}).status_code == 200
    assert app_client.get("/", headers={"Host": "[::1]:5001"}).status_code == 200
