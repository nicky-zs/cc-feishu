"""cc-feishu install：注册 Notification hook + 生成默认配置（幂等）。

M2 上线 listen daemon 时再补装 systemd user unit；M1 只做 hook 与配置。
"""

import json
import os
import re
from pathlib import Path
from typing import Any

from cc_feishu import config

NOTIFY_COMMAND = "cc-feishu notify"
_UID_RE = re.compile(r"^LARK_UID=(\S+)", re.MULTILINE)


def old_script_path() -> Path:
    return Path.home() / ".local" / "bin" / "notify_feishu.sh"


def extract_old_lark_uid(old_script: Path) -> str | None:
    """从旧脚本提取 LARK_UID（M1 迁移期一次性使用）。"""
    try:
        text = old_script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _UID_RE.search(text)
    return m.group(1) if m else None


def default_config_text(lark_uid: str) -> str:
    return f"""# cc-feishu 配置（cc-feishu install 生成）
lark_uid = "{lark_uid}"      # 通知接收人
chunk_bytes = 12000        # 单片字节预算
from_label = ""            # 通知头 from 行标签，缺省用 hostname
delivery = "tmux"          # 默认交付后端：tmux | queue（M2 生效）
resume_prefix = "/go"      # resume 显式触发前缀（M3 生效）
routing_ttl_hours = 24     # 路由表条目过期时间（M2 生效）
"""


def settings_path() -> Path:
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(cfg_dir) if cfg_dir else Path.home() / ".claude"
    return base / "settings.json"


def _atomic_write(path: Path, data: str) -> None:
    """临时文件 + rename，写配置不可中途损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def patch_settings(path: Path) -> bool:
    """把 Notification hooks 里的 notify_feishu.sh 替换为 cc-feishu notify；没有则新增。

    幂等：已是目标状态返回 False。其他 hook 一律保留。
    """
    data: dict[str, Any] = {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        data = parsed
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    notif = hooks.get("Notification")
    if not isinstance(notif, list):
        notif = []
        hooks["Notification"] = notif

    changed = False
    installed = False
    for matcher in notif:
        if not isinstance(matcher, dict):
            continue
        entries = matcher.get("hooks")
        if not isinstance(entries, list):
            continue
        for h in entries:
            if not isinstance(h, dict) or not isinstance(h.get("command"), str):
                continue
            cmd = h["command"]
            if "notify_feishu.sh" in cmd:
                if cmd != NOTIFY_COMMAND:
                    h["command"] = NOTIFY_COMMAND
                    changed = True
                installed = True
            elif cmd.strip() == NOTIFY_COMMAND:
                installed = True
    if not installed:
        notif.append({"hooks": [{"type": "command", "command": NOTIFY_COMMAND}]})
        changed = True
    if changed:
        _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return changed


def run_install() -> int:
    # 1) 默认配置：不存在才生成（绝不覆盖用户已改的配置）
    cpath = config.config_path()
    if not cpath.exists():
        uid = extract_old_lark_uid(old_script_path()) or ""
        _atomic_write(cpath, default_config_text(uid))
        if uid:
            print(f"已生成默认配置 {cpath}（lark_uid 迁移自旧脚本）")
        else:
            print(f"已生成默认配置 {cpath}（未找到旧脚本，请手工填写 lark_uid）")
    else:
        print(f"配置已存在，跳过 {cpath}")

    # 2) patch Notification hook（幂等）
    spath = settings_path()
    if patch_settings(spath):
        print(f"settings.json: Notification hook 已切换为 cc-feishu notify ({spath})")
    else:
        print(f"settings.json: 已是 cc-feishu notify，无需改动 ({spath})")
    return 0
