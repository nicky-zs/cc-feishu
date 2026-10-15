"""Notification hook 编排：hook JSON → 提取 → 分片 → 发送。best-effort，恒成功返回。"""

import json
import sys
from pathlib import Path
from typing import Any

from cc_feishu import chunk, config, lark, transcript


def _session_hint(hook: dict[str, Any]) -> str:
    """会话短 ID：优先 hook stdin 的 session_id，回退 transcript 文件名（M2 路由同源）。"""
    sid = hook.get("session_id")
    if isinstance(sid, str) and sid:
        return sid[:8]
    tp = hook.get("transcript_path")
    if isinstance(tp, str) and tp:
        return Path(tp).stem[:8]
    return ""


def build_notification(hook: dict[str, Any], last_msg: str, from_label: str) -> str:
    """通知头 + 引用化正文。from 行 = 机器标签（缺省 hostname）+ 会话短 ID。"""
    message = hook.get("message")
    cwd = hook.get("cwd")
    header = "# " + (message if isinstance(message, str) and message else "等待输入")
    header += "\n\n- `" + (cwd if isinstance(cwd, str) else "") + "`"
    header += "\n\nfrom: " + from_label
    hint = _session_hint(hook)
    if hint:
        header += " (" + hint + ")"
    if last_msg:
        return header + "\n\n" + "> " + "\n> ".join(last_msg.split("\n"))
    return header


def notify(hook_json: str, dry_run: bool) -> int:
    """Notification hook 入口。返回退出码（恒 0：通知是 best-effort）。"""
    hook: dict[str, Any] = {}
    if hook_json.strip():
        try:
            parsed = json.loads(hook_json)
            if isinstance(parsed, dict):
                hook = parsed
        except ValueError:
            pass  # 非法 JSON → 基础通知（已记录差异 #2）
    cfg = config.load_config()
    last_msg = ""
    tp = hook.get("transcript_path")
    if isinstance(tp, str) and tp:
        last_msg = transcript.extract_last_assistant(tp)
    full = build_notification(hook, last_msg, config.effective_from_label(cfg))
    chunks = chunk.split_notify_chunks(full, config.effective_chunk_bytes(cfg))
    if dry_run:
        n = len(chunks)
        for i, c in enumerate(chunks):
            print(f"=== chunk {i + 1}/{n} (bytes={chunk.byte_len(c)}) ===")
            print(c)
        print(f"=== end ({n} chunks) ===")
        return 0
    if not cfg.lark_uid:
        sys.stderr.write(
            "cc-feishu notify: 配置缺 lark_uid，跳过发送（先运行 cc-feishu install）\n"
        )
        return 0
    failed = lark.send_chunks(cfg.lark_uid, chunks)
    if failed:
        sys.stderr.write(f"cc-feishu notify: {failed}/{len(chunks)} 片发送失败（不中断会话）\n")
    return 0
