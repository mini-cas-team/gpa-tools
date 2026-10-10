import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))

import check  # noqa: E402
import configure  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("ram, gpus, model", [
    (11.6, [], "qwen2.5:3b"),                                           # this laptop: CPU only
    (64, [], "qwen2.5:3b"),                                             # lots of RAM, still CPU
    (16, [{"vendor": "nvidia", "name": "RTX", "vram_gb": 12}], "qwen2.5:7b"),
    (16, [{"vendor": "nvidia", "name": "MX", "vram_gb": 2}], "qwen2.5:3b"),
    (16, [{"vendor": "apple", "name": "M2", "vram_gb": 16}], "qwen2.5:7b"),
    (8, [{"vendor": "apple", "name": "M1", "vram_gb": 8}], "qwen2.5:3b"),
    (6, [], "qwen2.5:1.5b"),
    (None, [], "qwen2.5:3b"),
    (32, [{"vendor": "other", "name": "Intel UHD", "vram_gb": None}], "qwen2.5:3b"),
])
def test_model_follows_ram_and_gpu(ram, gpus, model):
    picked = check.recommend_model(ram, gpus)
    assert picked["model"] == model and picked["reason"]


def test_scalar_quotes_only_when_needed():
    assert configure.scalar("/home/ann/data") == "/home/ann/data"
    assert configure.scalar("C:/Users/Ann/data") == "C:/Users/Ann/data"
    for odd in ("/a #b", "x: y", "yes", "123", "[1]"):
        assert yaml.safe_load(f"k: {configure.scalar(odd)}") == {"k": odd}


def test_set_top_keeps_comments_and_uncomments():
    text = "a: 1 # one\n# tool-folders: []\nb: 2\n"
    text = configure.set_top(text, "a", "9")
    text = configure.set_top(text, "tool-folders", "[/t]")
    text = configure.set_top(text, "new", "x")
    assert text == "a: 9 # one\ntool-folders: [/t]\nb: 2\nnew: x\n"


def test_set_nested_edits_inside_the_block_only():
    text = "llm-category:\n  model: a # keep\n\nother:\n  model: z\n"
    text = configure.set_nested(text, "llm-category", "model", "b")
    text = configure.set_nested(text, "llm-category", "ollama-url", "http://h:1")
    assert yaml.safe_load(text) == {"llm-category": {"model": "b", "ollama-url": "http://h:1"},
                                    "other": {"model": "z"}}
    assert "model: b # keep" in text
    assert configure.set_nested("x: 1\n", "llm-category", "model", "m").endswith(
        "llm-category:\n  model: m\n")


def test_configure_creates_from_the_example_and_updates(tmp_path):
    config = tmp_path / "config.yaml"
    assert configure.main(["--config", str(config), "--data", str(tmp_path / "d"),
                           "--model", "qwen2.5:7b", "--ollama-url", "http://box:11434"]) == 0
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert cfg["pdf-folder"] == (tmp_path / "d" / "transcripts").as_posix()
    assert cfg["llm-category"] == {"model": "qwen2.5:7b", "ollama-url": "http://box:11434"}
    assert cfg["category"] == ["STEM"] and cfg["category-definitions"] == {}
    assert all((tmp_path / "d" / sub).is_dir() for sub in ("transcripts", "work", "out", "log"))

    # A second run touches only what it is given, and adding a folder twice is a no-op.
    before = config.read_text(encoding="utf-8")
    for _ in range(2):
        configure.main(["--config", str(config), "--tool-folder", str(tmp_path / "t")])
    after = config.read_text(encoding="utf-8")
    assert yaml.safe_load(after)["tool-folders"] == [(tmp_path / "t").as_posix()]
    assert after.replace(f"tool-folders: [{(tmp_path / 't').as_posix()}]", "# tool-folders: []") == before


def test_example_config_loads_in_every_program(tmp_path):
    import llm_category.__main__ as llm_run
    from gpa_ana.config import Config
    from llm_category.store import Definitions

    config = tmp_path / "config.yaml"
    configure.main(["--config", str(config), "--data", str(tmp_path / "d")])
    assert llm_run.load_config(config)["model"] == "qwen2.5:3b"
    assert Definitions(config).data == {}
    assert Config.load(config).categories == ["STEM"]


def test_tool_folders_go_first_on_path(monkeypatch, tmp_path):
    from upload.convert import add_tool_folders

    monkeypatch.setenv("PATH", "/usr/bin")
    add_tool_folders([str(tmp_path)])
    add_tool_folders(str(tmp_path))  # a bare string, and no duplicate
    assert check.os.environ["PATH"].split(check.os.pathsep) == [str(tmp_path), "/usr/bin"]
