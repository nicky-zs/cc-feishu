import socket
from pathlib import Path

import pytest

from cc_feishu import config


def write_cfg(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_defaults_when_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_FEISHU_CONFIG", str(tmp_path / "none.toml"))
    assert config.load_config() == config.Config()


def test_load_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = write_cfg(tmp_path, 'lark_uid = "ou_x"\nchunk_bytes = 9000\nfrom_label = "devbox"\n')
    monkeypatch.setenv("CC_FEISHU_CONFIG", str(p))
    cfg = config.load_config()
    assert cfg.lark_uid == "ou_x"
    assert cfg.chunk_bytes == 9000
    assert cfg.from_label == "devbox"
    assert cfg.delivery == "tmux"  # 未写的键取默认
    assert cfg.resume_prefix == "/go"
    assert cfg.routing_ttl_hours == 24


def test_broken_toml_falls_back_to_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = write_cfg(tmp_path, "not [valid toml")
    monkeypatch.setenv("CC_FEISHU_CONFIG", str(p))
    assert config.load_config() == config.Config()


def test_env_chunk_bytes_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_FEISHU_CONFIG", str(tmp_path / "none.toml"))
    monkeypatch.setenv("FEISHU_CHUNK_BYTES", "3000")
    assert config.load_config().chunk_bytes == 3000


def test_env_chunk_bytes_invalid_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_FEISHU_CONFIG", str(tmp_path / "none.toml"))
    monkeypatch.setenv("FEISHU_CHUNK_BYTES", "abc")
    assert config.load_config().chunk_bytes == 12000


def test_chunk_bytes_floor() -> None:
    assert config.effective_chunk_bytes(config.Config(chunk_bytes=500)) == 1000
    assert config.effective_chunk_bytes(config.Config(chunk_bytes=12000)) == 12000


def test_from_label_defaults_to_hostname() -> None:
    assert config.effective_from_label(config.Config()) == socket.gethostname()
    assert config.effective_from_label(config.Config(from_label="box1")) == "box1"


def test_config_path_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_FEISHU_CONFIG", "/x/y.toml")
    assert config.config_path() == Path("/x/y.toml")
