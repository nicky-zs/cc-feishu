import json
from pathlib import Path

import pytest

from cc_feishu import install


def write_settings(tmp_path: Path, data: object) -> Path:
    p = tmp_path / "settings.json"
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(json.dumps(data), encoding="utf-8")
    return p


def notification_commands(p: Path) -> list[str]:
    data = json.loads(p.read_text(encoding="utf-8"))
    return [h["command"] for m in data["hooks"]["Notification"] for h in m["hooks"]]


def test_extract_old_lark_uid(tmp_path: Path) -> None:
    s = tmp_path / "notify_feishu.sh"
    s.write_text("#!/bin/sh\nLARK_UID=ou_abc123\nCHUNK_BYTES=12000\n", encoding="utf-8")
    assert install.extract_old_lark_uid(s) == "ou_abc123"
    assert install.extract_old_lark_uid(tmp_path / "none.sh") is None
    s.write_text("#!/bin/sh\n", encoding="utf-8")
    assert install.extract_old_lark_uid(s) is None


def test_default_config_text() -> None:
    text = install.default_config_text("ou_x")
    assert 'lark_uid = "ou_x"' in text
    assert "chunk_bytes = 12000" in text


def test_patch_settings_replaces_old_hook(tmp_path: Path) -> None:
    p = write_settings(
        tmp_path,
        {
            "hooks": {
                "Notification": [{"hooks": [{"type": "command", "command": "notify_feishu.sh"}]}]
            }
        },
    )
    assert install.patch_settings(p) is True
    assert notification_commands(p) == ["cc-feishu notify"]


def test_patch_settings_idempotent(tmp_path: Path) -> None:
    p = write_settings(
        tmp_path,
        {
            "hooks": {
                "Notification": [{"hooks": [{"type": "command", "command": "cc-feishu notify"}]}]
            }
        },
    )
    assert install.patch_settings(p) is False  # 已是目标状态 → 无改动


def test_patch_settings_idempotent_with_args(tmp_path: Path) -> None:
    # 命令带额外参数（如手工加的 --dry-run）也算已安装，不得重复注册
    p = write_settings(
        tmp_path,
        {
            "hooks": {
                "Notification": [
                    {"hooks": [{"type": "command", "command": "cc-feishu notify --dry-run"}]}
                ]
            }
        },
    )
    assert install.patch_settings(p) is False
    assert notification_commands(p) == ["cc-feishu notify --dry-run"]


def test_patch_settings_appends_when_absent(tmp_path: Path) -> None:
    p = write_settings(tmp_path, {"model": "x"})
    assert install.patch_settings(p) is True
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["model"] == "x"  # 不丢已有配置
    assert notification_commands(p) == ["cc-feishu notify"]


def test_patch_settings_creates_missing(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"  # 文件不存在
    assert install.patch_settings(p) is True
    assert notification_commands(p) == ["cc-feishu notify"]


def test_patch_settings_malformed(tmp_path: Path) -> None:
    # 损坏的 settings.json：重建可用的最小配置，但原件先备份（Review Focus #6：不丢数据）
    p = write_settings(tmp_path, "{broken json")
    assert install.patch_settings(p) is True
    assert notification_commands(p) == ["cc-feishu notify"]
    backups = sorted(tmp_path.glob("settings.json.broken-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{broken json"


def test_patch_settings_preserves_other_hooks(tmp_path: Path) -> None:
    p = write_settings(
        tmp_path,
        {
            "hooks": {
                "Notification": [
                    {"hooks": [{"type": "command", "command": "notify_feishu.sh"}]},
                    {"hooks": [{"type": "command", "command": "other-notify.sh"}]},
                ],
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {"type": "command", "command": "codegraph prompt-hook", "timeout": 300}
                        ]
                    }
                ],
            }
        },
    )
    assert install.patch_settings(p) is True
    data = json.loads(p.read_text(encoding="utf-8"))
    notif = data["hooks"]["Notification"]
    assert notif[0]["hooks"][0]["command"] == "cc-feishu notify"
    assert notif[1]["hooks"][0]["command"] == "other-notify.sh"  # 其他通知 hook 保留
    assert data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "codegraph prompt-hook"


def test_run_install_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / ".local" / "bin" / "notify_feishu.sh"
    old.parent.mkdir(parents=True)
    old.write_text("#!/bin/sh\nLARK_UID=ou_from_old\n", encoding="utf-8")
    cfg = tmp_path / "config" / "cc-feishu" / "config.toml"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CC_FEISHU_CONFIG", str(cfg))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    assert install.run_install() == 0
    assert 'lark_uid = "ou_from_old"' in cfg.read_text(encoding="utf-8")  # uid 迁移自旧脚本
    sp = tmp_path / "claude" / "settings.json"
    assert notification_commands(sp) == ["cc-feishu notify"]
    # 幂等：改过 uid 后再装，配置不被覆盖
    cfg.write_text('lark_uid = "ou_custom"\n', encoding="utf-8")
    assert install.run_install() == 0
    assert "ou_custom" in cfg.read_text(encoding="utf-8")
