import os

import pytest
import yaml

import check
import configure


@pytest.mark.parametrize("value", ["/home/ann/data", "C:/Users/Ann/data", "qwen2.5:3b"])
def test_scalar_leaves_plain_values_alone(value):
    assert configure.scalar(value) == value


@pytest.mark.parametrize("value", ["/a #b", "x: y", "yes", "123", "[1]", 'say "hi"', "back\\slash"])
def test_scalar_quotes_values_that_would_change(value):
    assert yaml.safe_load(f"k: {configure.scalar(value)}") == {"k": value}


def test_path_value_is_absolute_with_forward_slashes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert configure.path_value("sub") == (tmp_path / "sub").as_posix()
    assert configure.path_value("~") == os.path.expanduser("~").replace(os.sep, "/")


def test_set_top_replaces_uncomments_and_appends():
    text = "a: 1 # one\n# tool-folders: []\nb: 2\n"
    text = configure.set_top(text, "a", "9")
    text = configure.set_top(text, "tool-folders", "[/t]")
    text = configure.set_top(text, "new", "x")
    assert text == "a: 9 # one\ntool-folders: [/t]\nb: 2\nnew: x\n"


def test_set_top_prefers_a_live_line_over_a_commented_one():
    text = "# jobs: 0\njobs: 2\n"
    assert configure.set_top(text, "jobs", "4") == "# jobs: 0\njobs: 4\n"


def test_set_nested_stays_inside_its_block():
    text = "block:\n  model: a # keep\n\nother:\n  model: z\n"
    text = configure.set_nested(text, "block", "model", "b")
    text = configure.set_nested(text, "block", "url", "http://h:1")
    assert yaml.safe_load(text) == {"block": {"model": "b", "url": "http://h:1"},
                                    "other": {"model": "z"}}
    assert "model: b # keep" in text


def test_set_nested_adds_a_missing_block():
    assert configure.set_nested("x: 1\n", "block", "model", "m") == \
        "x: 1\nblock:\n  model: m\n"


def test_set_nested_goes_two_levels_deep():
    text = "category-builder:\n  one:\n    model: a # keep\n  jev:\n    model: j\n\nz: 1\n"
    text = configure.set_nested(text, ["category-builder", "one"], "model", "b")
    text = configure.set_nested(text, ["category-builder", "one"], "url", "http://h:1")
    text = configure.set_nested(text, ["category-builder", "other"], "model", "o")
    assert yaml.safe_load(text) == {"category-builder": {
        "one": {"model": "b", "url": "http://h:1"}, "jev": {"model": "j"},
        "other": {"model": "o"}}, "z": 1}
    assert "    model: b # keep" in text and text.endswith("\n\nz: 1\n")
    assert configure.set_nested("", ["a", "b"], "k", "v") == "a:\n  b:\n    k: v\n"


def test_template_is_valid_yaml_with_every_key():
    cfg = yaml.safe_load(configure.TEMPLATE)
    assert set(configure.FOLDERS) <= set(cfg)
    assert list(cfg["category-builder"]) == ["jev"]
    assert "model" not in cfg["category-definer"] and "ollama" not in configure.TEMPLATE.lower()
    assert cfg["category-builder"]["jev"] == {
        "mode": "cloud", "cloud-url": "https://gpa-tools-jev.johnprofessional132.workers.dev", "cloud-token-env": "GPA_TOOLS_CLOUD_TOKEN",
        "model": "jev-1.14.0", "api-url": "https://api.typesafe.ai/v1/systemone",
        "api-key-env": "GPA_TOOLS_KEY", "threshold": 0.5, "batch-size": 20}
    assert cfg["active-category-builder"] == "jev" and cfg["run_mode"] == "prod"
    assert cfg["category-definer"]["mode"] == "dev"
    assert cfg["category-definer"]["retries"] == 1
    assert cfg["category-definitions"] == {}
    assert cfg["min-category-courses"] == 4
    assert "category" not in cfg


def test_configure_creates_config_from_the_template(tmp_path, capsys):
    config, data = tmp_path / "config.yaml", tmp_path / "d"
    assert configure.main(["--config", str(config), "--data", str(data)]) == 0
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    for key, sub in configure.FOLDERS.items():
        assert cfg[key] == (data / sub).as_posix()
        assert (data / sub).is_dir()
    for component in ("transcripts", "letters", "personal"):  # input and work, one each
        assert (data / component).is_dir() and (data / "work" / component).is_dir()
    assert cfg["category-builder"]["jev"]["mode"] == "cloud"
    assert cfg["category-definer"] == {"mode": "dev", "retries": 1, "browser-timeout": 120,
                                       "browser": "auto"}
    assert config.read_text(encoding="utf-8").startswith("# gpa-tools configuration")
    assert "created" in capsys.readouterr().out
    assert not config.with_name("config.yaml.tmp").exists()


def test_configure_changes_only_what_it_is_given(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("# mine\npdf-folder: /keep # comment\nwork-folder: /w\n", encoding="utf-8")
    assert configure.main(["--config", str(config), "--work-folder", str(tmp_path / "w2"),
                           "--no-mkdir"]) == 0
    assert config.read_text(encoding="utf-8") == \
        f"# mine\npdf-folder: /keep # comment\nwork-folder: {(tmp_path / 'w2').as_posix()}\n"
    assert not (tmp_path / "w2").exists()


def test_a_folder_option_overrides_data(tmp_path):
    config = tmp_path / "config.yaml"
    configure.main(["--config", str(config), "--data", str(tmp_path / "d"),
                    "--input-folder", str(tmp_path / "in"), "--no-mkdir"])
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert cfg["input-folder"] == (tmp_path / "in").as_posix()
    assert cfg["components"] == ["transcripts", "letters", "personal"]
    assert cfg["work-folder"] == (tmp_path / "d" / "work").as_posix()


def test_tool_folders_are_added_once(tmp_path):
    config = tmp_path / "config.yaml"
    configure.main(["--config", str(config), "--no-mkdir"])
    before = config.read_text(encoding="utf-8")
    for _ in range(2):
        configure.main(["--config", str(config), "--tool-folder", str(tmp_path / "t"), "--no-mkdir"])
    after = config.read_text(encoding="utf-8")
    folder = (tmp_path / "t").as_posix()
    assert yaml.safe_load(after)["tool-folders"] == [folder]
    assert after.replace(f"tool-folders: [{folder}]", "# tool-folders: []") == before


def test_a_new_config_loads_in_the_jev_builder_and_browser_view(tmp_path):
    import jev_category.builder as jev_run
    from browser_view.app import create_app
    from category_builder.definitions import Definitions

    config = tmp_path / "config.yaml"
    configure.main(["--config", str(config), "--data", str(tmp_path / "d")])
    cfg = jev_run.load_config(config)
    assert cfg["mode"] == "cloud" and cfg["in"] == tmp_path / "d" / "work" / "transcripts"
    assert cfg["folder"] == tmp_path / "d" / "work" / "jev"
    assert Definitions(config, cloud=None).data == {}
    assert create_app(config).test_client().get("/categories").get_json()["names"] == []


def test_tool_folders_go_first_on_path(monkeypatch, tmp_path):
    from upload.convert import add_tool_folders

    monkeypatch.setenv("PATH", "/usr/bin")
    add_tool_folders([str(tmp_path)])
    add_tool_folders(str(tmp_path))  # a bare string, and no duplicate
    add_tool_folders(None)
    assert os.environ["PATH"].split(os.pathsep) == [str(tmp_path), "/usr/bin"]


def test_check_reports_builder_definer_and_outdated_keys():
    new = {"active-category-builder": "jev", "category-builder": {"jev": {}}, "category-definer": {}}
    info = check.config_info(new, "present")
    assert info["active-category-builder"] == "jev"
    assert info["definer"] == {"mode": "dev", "retries": 1, "browser": "auto"}
    assert info["outdated"] == {}
    older = check.config_info({"category-definer": {"provider": "ollama"}}, "present")
    assert "category-definer: provider" in older["outdated"]
    assert "llm-category" in check.config_info({"llm-category": {"model": "m"}}, "present")["outdated"]
    # the removed llm builder and Ollama: every key of theirs is pointed out
    gone = check.config_info({"active-category-builder": "llm",
                              "category-builder": {"llm": {"category-build-model": "small"}},
                              "category-definer": {"model": "big", "ollama-url": "http://a:1"}}, "present")
    assert {"category-builder: llm", "active-category-builder: llm", "category-definer: model",
            "category-definer: ollama-url"} <= set(gone["outdated"])


def test_configure_points_out_an_outdated_llm_category_block(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("llm-category:\n  model: old\n", encoding="utf-8")
    assert configure.main(["--config", str(config), "--active-builder", "jev", "--no-mkdir"]) == 0
    assert "'llm-category:' is from an older config.yaml" in capsys.readouterr().out
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["llm-category"] == {"model": "old"}


def test_configure_sets_jev_and_the_active_builder(tmp_path, capsys):
    import jev_category.builder as jev_run

    config = tmp_path / "config.yaml"
    assert configure.main(["--config", str(config), "--data", str(tmp_path / "d"), "--no-mkdir",
                           "--jev-model", "jev-latest", "--jev-key-env", "MY_KEY",
                           "--active-builder", "jev"]) == 0
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert cfg["active-category-builder"] == "jev"
    assert cfg["category-builder"]["jev"]["model"] == "jev-latest"
    assert cfg["category-builder"]["jev"]["api-key-env"] == "MY_KEY"
    assert "active-category-builder: jev # the default" in config.read_text(encoding="utf-8")
    assert "category-builder.jev.model: jev-latest" in capsys.readouterr().out
    loaded = jev_run.load_config(config)
    assert (loaded["model"], loaded["key_env"]) == ("jev-latest", "MY_KEY")
    assert loaded["mode"] == "cloud" and loaded["cloud_url"].startswith("https://")  # the template's default
    assert configure.main(["--config", str(config), "--no-mkdir", "--jev-mode", "direct",
                           "--cloud-url", "http://127.0.0.1:8787"]) == 0
    loaded = jev_run.load_config(config)
    assert (loaded["mode"], loaded["cloud_url"]) == ("direct", "http://127.0.0.1:8787")


def test_check_reports_jev_without_revealing_the_key(monkeypatch):
    monkeypatch.delenv("GPA_TOOLS_KEY", raising=False)
    info = check.jev_info({})
    assert info == {"model": "jev-1.14.0", "api-url": "https://api.typesafe.ai/v1/systemone",
                    "api-key-env": "GPA_TOOLS_KEY", "key_set": False}

    probed = []
    monkeypatch.setenv("K", "secret-value")
    monkeypatch.setattr(check, "probe_jev", lambda url, key: probed.append(key) or
                        {"reachable": True, "authorized": True, "models": ["jev-latest"]})
    info = check.jev_info({"category-builder": {"jev": {"model": "jev", "api-key-env": "K"}}})
    assert probed == ["secret-value"] and info["key_set"] and info["model_ok"] is False
    assert "secret-value" not in str(info)
    assert check.jev_model_ok("jev-1.14.0", []) and check.jev_model_ok("jev-latest", ["jev-latest"])
    assert check.jev_models_url("https://api.typesafe.ai/v1/systemone") == \
        "https://api.typesafe.ai/v1/models"


def test_check_reports_cloud_mode_without_revealing_the_token(monkeypatch, gpa_tools_home):
    cfg = {"category-builder": {"jev": {"mode": "cloud", "cloud-url": "https://c.test",
                                        "cloud-token-env": "TOK"}}}
    monkeypatch.delenv("TOK", raising=False)
    info = check.jev_info(cfg)
    assert {k: info[k] for k in ("mode", "cloud-url", "cloud-token-env", "token_set")} == \
        {"mode": "cloud", "cloud-url": "https://c.test", "cloud-token-env": "TOK", "token_set": False}
    assert info["token_from"].startswith("none")
    gpa_tools_home.mkdir()
    (gpa_tools_home / "pending_token").write_text("gpat_waiting")
    assert check.jev_info(cfg)["token_from"].startswith("requested")
    (gpa_tools_home / "client_token").write_text("gpat_fromfile\n")
    seen = []
    monkeypatch.setattr(check, "probe_cloud", lambda url, token: seen.append(token) or {"model": "m"})
    info = check.jev_info(cfg)
    assert seen == ["gpat_fromfile"] and info["token_from"].endswith("client_token")
    assert "gpat_fromfile" not in str(info)
    probed = []
    monkeypatch.setenv("TOK", "gpat_secret")
    monkeypatch.setattr(check, "probe_cloud", lambda url, token: probed.append((url, token)) or
                        {"reachable": True, "authorized": True, "model": "jev-1.14.0"})
    info = check.jev_info(cfg)
    assert probed == [("https://c.test", "gpat_secret")] and info["cloud"]["model"] == "jev-1.14.0"
    assert "gpat_secret" not in str(info)


def test_jev_check_needs_the_key_and_asks_one_question(monkeypatch, capsys):
    import jev_check

    monkeypatch.delenv("GPA_TOOLS_KEY", raising=False)
    assert jev_check.main(["ask"]) == 1
    assert "GPA_TOOLS_KEY is not set" in capsys.readouterr().out

    class FakeClient:
        def __init__(self, url, model, key):
            self.model = model

        def scores(self, category, definition, courses):
            return [0.97], {"input_tokens": 384}

    monkeypatch.setenv("GPA_TOOLS_KEY", "secret-value")
    monkeypatch.setattr("jev_category.client.JevClient", FakeClient)
    assert jev_check.main(["ask", "jev-1.14.0"]) == 0
    out = capsys.readouterr().out
    assert '"score": 0.97' in out and "secret-value" not in out


def test_configure_sets_the_definer_mode(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("category-definer:\n  provider: ollama\n  model: m\n", encoding="utf-8")
    assert configure.main(["--config", str(config), "--no-mkdir", "--definer-mode", "prod"]) == 0
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert cfg["category-definer"] == {"mode": "prod", "provider": "ollama", "model": "m"}
    out = capsys.readouterr().out
    assert "category-definer.mode: prod" in out and "no longer read" in out
    with pytest.raises(SystemExit):
        configure.main(["--config", str(config), "--definer-mode", "test"])


def test_configure_points_out_the_removed_ollama_keys(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("active-category-builder: llm\ncategory-builder:\n  llm:\n    model: a\n"
                      "category-definer:\n  model: b\n  ollama-url: http://h:1\n", encoding="utf-8")
    assert configure.main(["--config", str(config), "--no-mkdir"]) == 0
    out = capsys.readouterr().out
    for key in ("category-builder.llm", "category-definer.model", "category-definer.ollama-url"):
        assert f"{key} is from an older config.yaml" in out
    assert "active-category-builder llm was removed" in out
    with pytest.raises(SystemExit):
        configure.main(["--config", str(config), "--active-builder", "llm"])


# ---- setup_venv: which Python makes .venv ------------------------------------

import setup_venv  # noqa: E402


def _py(version, path=None):
    return {"path": path or f"/usr/bin/python{version[:4]}", "version": version,
            "supported": check.supported(version)}


def _info(candidates=None, venv=None, uv=None):
    return {"candidates": candidates or {}, "uv": uv,
            "venv": venv or {"exists": False}}


def test_setup_venv_prefers_3_14_then_3_12():
    both = {"python3": _py("3.13.1"), "python3.12": _py("3.12.3"), "python3.14": _py("3.14.4")}
    p = setup_venv.plan(_info(both))
    assert p["action"] == "create" and p["python"] == "3.14.4"
    assert p["commands"][0] == ["/usr/bin/python3.14", "-m", "venv", str(check.VENV)]
    assert p["commands"][1][1:4] == ["-m", "pip", "install"] and "pytest" in p["commands"][1]
    only_312 = {"python3": _py("3.12.3", "/usr/bin/python3")}
    assert setup_venv.plan(_info(only_312))["commands"][0][0] == "/usr/bin/python3"


def test_setup_venv_runs_the_windows_launcher_with_its_version_flag():
    p = setup_venv.plan(_info({"py -3.12": _py("3.12.8", "C:/Windows/py.exe")}))
    assert p["commands"][0][:2] == ["C:/Windows/py.exe", "-3.12"]


def test_setup_venv_falls_back_to_uv_then_stops():
    unsupported = {"python3": _py("3.11.9")}
    p = setup_venv.plan(_info(unsupported, uv="/home/u/.local/bin/uv"))
    assert p["action"] == "uv"
    assert p["commands"][0] == ["/home/u/.local/bin/uv", "venv", "--python", "3.14", str(check.VENV)]
    p = setup_venv.plan(_info(unsupported))
    assert p["action"] == "no-python" and "astral.sh/uv" in p["why"] and not p["commands"]


def test_setup_venv_keeps_or_completes_a_supported_venv():
    ok = {"exists": True, "version": "3.12.3", "supported": True, "missing_packages": []}
    assert setup_venv.plan(_info({"python3.14": _py("3.14.4")}, ok))["action"] == "ok"
    missing = {**ok, "missing_packages": ["flask"]}
    p = setup_venv.plan(_info({}, missing))
    assert p["action"] == "install" and len(p["commands"]) == 1 and "flask" in p["why"]


def test_setup_venv_replaces_an_unsupported_venv_only_with_rebuild():
    old = {"exists": True, "version": "3.13.1", "supported": False, "missing_packages": []}
    found = {"python3.12": _py("3.12.3")}
    assert setup_venv.plan(_info(found, old))["action"] == "rebuild-needed"
    p = setup_venv.plan(_info(found, old), rebuild=True)
    assert p["action"] == "create" and p["remove"] and p["python"] == "3.12.3"


def test_setup_venv_uses_the_python_it_is_given():
    found = {"python3.14": _py("3.14.4")}
    p = setup_venv.plan(_info(found), python=_py("3.12.3", "/opt/py312/bin/python3"))
    assert p["commands"][0][0] == "/opt/py312/bin/python3" and p["python"] == "3.12.3"
    assert setup_venv.plan(_info(found), python=_py("3.11.2"))["action"] == "no-python"
    assert setup_venv.plan(_info(found), python={"path": "/x", "version": "", "supported": False})["action"] == "no-python"
    venv312 = {"exists": True, "version": "3.12.3", "supported": True, "missing_packages": []}
    assert setup_venv.plan(_info(found, venv312), python=_py("3.12.3"))["action"] == "ok"
    assert setup_venv.plan(_info(found, venv312), python=_py("3.14.4"))["action"] == "rebuild-needed"
    assert setup_venv.plan(_info(found, venv312), rebuild=True, python=_py("3.14.4"))["remove"]
