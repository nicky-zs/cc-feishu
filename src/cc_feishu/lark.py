"""lark-cli 封装：发送 markdown 消息。lark-cli 继续作为飞书 OpenAPI 执行层。"""

import subprocess
import sys
import time

SEND_CMD = ["lark-cli", "im", "+messages-send", "--as", "bot"]
ERR_TRUNCATE = 500
CHUNK_INTERVAL = 0.3  # 片间限速，避免触发消息发送频率限制
SEND_TIMEOUT = 30.0  # 单片发送超时：lark-cli 挂起不得无限阻塞 Notification hook


def send_markdown(uid: str, markdown: str) -> tuple[bool, str]:
    """发一条 markdown 消息给 uid。返回 (成功, 错误信息截断)。"""
    cmd = [*SEND_CMD, "--user-id", uid, "--markdown", markdown]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=SEND_TIMEOUT)
    except FileNotFoundError:
        return False, "lark-cli not found"
    except subprocess.TimeoutExpired:
        return False, f"lark-cli 发送超时（>{int(SEND_TIMEOUT)}s）"
    if p.returncode == 0:
        return True, ""
    return False, (p.stderr or p.stdout or "").strip()[:ERR_TRUNCATE]


def send_chunks(uid: str, chunks: list[str]) -> int:
    """逐片发送，返回失败片数。单片失败只记 stderr，不中断其余片。"""
    failed = 0
    for i, c in enumerate(chunks):
        ok, err = send_markdown(uid, c)
        if not ok:
            failed += 1
            sys.stderr.write(f"cc-feishu notify: 第 {i + 1}/{len(chunks)} 片发送失败: {err}\n")
        if i < len(chunks) - 1:
            time.sleep(CHUNK_INTERVAL)
    return failed
