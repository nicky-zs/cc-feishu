"""toml 配置加载（tomllib）+ 环境变量覆盖。"""

import os
import socket
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

DEFAULT_CHUNK_BYTES = 12000
MIN_CHUNK_BYTES = 1000


@dataclass(frozen=True)
class Config:
    lark_uid: str = ""
    chunk_bytes: int = DEFAULT_CHUNK_BYTES
    from_label: str = ""  # 通知头 from 行标签；空 → 用 hostname（替代旧脚本硬编码 IP）
    delivery: str = "tmux"  # 默认交付后端：tmux | queue（M2 生效）
    resume_prefix: str = "/go"  # resume 显式触发前缀（M3 生效）
    routing_ttl_hours: int = 24  # 路由表条目过期时间（M2 生效）


def config_path() -> Path:
    override = os.environ.get("CC_FEISHU_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "cc-feishu" / "config.toml"


def _get_str(data: dict[str, Any], key: str, default: str) -> str:
    v = data.get(key)
    return v if isinstance(v, str) else default


def _get_int(data: dict[str, Any], key: str, default: int) -> int:
    v = data.get(key)
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    return default


def load_config() -> Config:
    data: dict[str, Any] = {}
    try:
        with config_path().open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    cfg = Config(
        lark_uid=_get_str(data, "lark_uid", ""),
        chunk_bytes=_get_int(data, "chunk_bytes", DEFAULT_CHUNK_BYTES),
        from_label=_get_str(data, "from_label", ""),
        delivery=_get_str(data, "delivery", "tmux"),
        resume_prefix=_get_str(data, "resume_prefix", "/go"),
        routing_ttl_hours=_get_int(data, "routing_ttl_hours", 24),
    )
    # FEISHU_CHUNK_BYTES 环境变量覆盖单片预算（与旧脚本同名同义；非整数忽略）
    env_bytes = os.environ.get("FEISHU_CHUNK_BYTES")
    if env_bytes is not None:
        try:
            cfg = replace(cfg, chunk_bytes=int(env_bytes))
        except ValueError:
            pass
    return cfg


def effective_chunk_bytes(cfg: Config) -> int:
    return max(cfg.chunk_bytes, MIN_CHUNK_BYTES)


def effective_from_label(cfg: Config) -> str:
    return cfg.from_label or socket.gethostname()
