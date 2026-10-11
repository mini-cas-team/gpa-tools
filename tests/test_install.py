import os

import pytest
import yaml

import check
import configure


@pytest.mark.parametrize("ram, gpus, model", [
    (11.6, [], "qwen2.5:3b"),
    (64, [], "qwen2.5:3b"),
    (6, [], "qwen2.5:1.5b"),
    (None, [], "qwen2.5:3b"),
    (16, [{"vendor": "nvidia", "name": "RTX", "vram_gb": 12}], "qwen2.5:7b"),
    (16, [{"vendor": "nvidia", "name": "MX", "vram_gb": 2}], "qwen2.5:3b"),
    (16, [{"vendor": "apple", "name": "M2", "vram_gb": 16}], "qwen2.5:7b"),
    (8, [{"vendor": "apple", "name": "M1", "vram_gb": 8}], "qwen2.5:3b"),
    (32, [{"vendor": "other", "name": "Intel UHD", "vram_gb": None}], "qwen2.5:3b"),
])
def test_recommended_model_follows_ram_and_gpu(ram, gpus, model):
    picked = check.recommend_model(ram, gpus)
    assert picked["model"] == model
    assert picked["reason"]


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
    text = configure.set_nested(text, "block", "ollama-url", "http://h:1")
    assert yaml.safe_load(text) == {"block": {"model": "b", "ollama-url": "http://h:1"},
                                    "other": {"model": "z"}}
    assert "model: b # keep" in text


def test_set_nested_adds_a_missing_block():
    assert configure.set_nested("x: 1\n", "block", "model", "m") == \
        "x: 1\nblock:\n  model: m\n"


def test_set_nested_goes_two_levels_deep():
    text = "category-builder:\n  llm:\n    model: a # keep\n  jev:\n    model: j\n\nz: 1\n"
    text = configure.set_nested(text, ["category-builder", "llm"], "model", "b")
    text = configure.set_nested(text, ["category-builder", "llm"], "ollama-url", "http://h:1")
    text = configure.set_nested(text, ["category-builder", "other"], "model", "o")
    assert yaml.safe_load(text) == {"category-builder": {
        "llm": {"model": "b", "ollama-url": "http://h:1"}, "jev": {"model": "j"},
        "other": {"model": "o"}}, "z": 1}
    assert "    model: b # keep" in text and text.endswith("\n\nz: 1\n")
    assert configure.set_nested("", ["a", "b"], "k", "v") == "a:\n  b:\n    k: v\n"


def test_template_is_valid_yaml_with_every_key():
    cfg = yaml.safe_load(configure.TEMPLATE)
    assert set(configure.FOLDERS) <= set(cfg)
    assert cfg["category-builder"]["llm"] == {"model": "qwen2.5:3b",
                                              "ollama-url": "http://localhost:11434"}
    assert cfg["category-builder"]["jev"] == {
        "model": "jev-1.13.0", "api-url": "https://api.typesafe.ai/v1/systemone",
        "api-key-env": "GPA_TOOLS_KEY", "threshold": 0.5, "batch-size": 20}
    assert cfg["active-category-builder"] == "llm"
    assert cfg["category-definer"]["provider"] == "ollama"
    assert cfg["category-definitions"] == {}
    assert cfg["min-category-courses"] == 4
    assert "category" not in cfg


def test_configure_creates_config_from_the_template(tmp_path, capsys):
    config, data = tmp_path / "config.yaml", tmp_path / "d"
    assert configure.main(["--config", str(config), "--data", str(data),
                           "--model", "qwen2.5:7b", "--ollama-url", "http://box:11434",
                           "--definer-model", "qwen2.5:14b"]) == 0
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    for key, sub in configure.FOLDERS.items():
        assert cfg[key] == (data / sub).as_posix()
        assert (data / sub).is_dir()
    assert cfg["category-builder"]["llm"] == {"model": "qwen2.5:7b", "ollama-url": "http://box:11434"}
    assert cfg["category-definer"] == {"provider": "ollama", "model": "qwen2.5:14b",
                                       "ollama-url": "http://box:11434"}
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
                    "--pdf-folder", str(tmp_path / "pdfs"), "--no-mkdir"])
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert cfg["pdf-folder"] == (tmp_path / "pdfs").as_posix()
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


def test_a_new_config_loads_in_llm_category_and_browser_view(tmp_path):
    import llm_category.builder as llm_run
    from browser_view.app import create_app
    from category_builder.definitions import Definitions

    config = tmp_path / "config.yaml"
    configure.main(["--config", str(config), "--data", str(tmp_path / "d")])
    cfg = llm_run.load_config(config)
    assert cfg["model"] == "qwen2.5:3b" and cfg["in"] == tmp_path / "d" / "work"
    assert cfg["folder"] == tmp_path / "d" / "work" / "llm"
    assert Definitions(config).data == {}
    assert create_app(config).test_client().get("/categories").get_json()["names"] == []


def test_tool_folders_go_first_on_path(monkeypatch, tmp_path):
    from upload.convert import add_tool_folders

    monkeypatch.setenv("PATH", "/usr/bin")
    add_tool_folders([str(tmp_path)])
    add_tool_folders(str(tmp_path))  # a bare string, and no duplicate
    add_tool_folders(None)
    assert os.environ["PATH"].split(os.pathsep) == [str(tmp_path), "/usr/bin"]


def test_check_reports_builder_definer_and_outdated_keys(monkeypatch):
    new = {"active-category-builder": "llm",
           "category-builder": {"llm": {"model": "small", "ollama-url": "http://a:1"}},
           "category-definer": {"model": "big"}}
    info = check.config_info(new, "present")
    assert (info["model"], info["ollama-url"], info["active-category-builder"]) == \
        ("small", "http://a:1", "llm")
    assert info["definer"] == {"provider": "ollama", "model": "big", "ollama-url": "http://a:1"}
    assert info["outdated"] == {}
    old = check.config_info({"llm-category": {"model": "m"}}, "present")
    assert old["model"] is None and "llm-category" in old["outdated"]

    monkeypatch.setattr(check, "ollama_binary", lambda: None)
    monkeypatch.setattr(check, "probe_ollama", lambda url: {"url": url})
    new["category-definer"]["ollama-url"] = "http://b:2"
    assert [s["url"] for s in check.ollama_info(new)["servers"]] == \
        [check.DEFAULT_OLLAMA_URL, "http://a:1", "http://b:2"]


def test_configure_points_out_an_outdated_llm_category_block(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("llm-category:\n  model: old\n", encoding="utf-8")
    assert configure.main(["--config", str(config), "--model", "new", "--no-mkdir"]) == 0
    assert "'llm-category:' is from an older config.yaml" in capsys.readouterr().out
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert cfg["category-builder"]["llm"]["model"] == "new" and cfg["llm-category"] == {"model": "old"}


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


def test_check_reports_jev_without_revealing_the_key(monkeypatch):
    monkeypatch.delenv("GPA_TOOLS_KEY", raising=False)
    info = check.jev_info({})
    assert info == {"model": "jev-1.13.0", "api-url": "https://api.typesafe.ai/v1/systemone",
                    "api-key-env": "GPA_TOOLS_KEY", "key_set": False}

    probed = []
    monkeypatch.setenv("K", "secret-value")
    monkeypatch.setattr(check, "probe_jev", lambda url, key: probed.append(key) or
                        {"reachable": True, "authorized": True, "models": ["jev-latest"]})
    info = check.jev_info({"category-builder": {"jev": {"model": "jev", "api-key-env": "K"}}})
    assert probed == ["secret-value"] and info["key_set"] and info["model_ok"] is False
    assert "secret-value" not in str(info)
    assert check.jev_model_ok("jev-1.13.0", []) and check.jev_model_ok("jev-latest", ["jev-latest"])
    assert check.jev_models_url("https://api.typesafe.ai/v1/systemone") == \
        "https://api.typesafe.ai/v1/models"


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
    assert jev_check.main(["ask", "jev-1.13.0"]) == 0
    out = capsys.readouterr().out
    assert '"score": 0.97' in out and "secret-value" not in out
