import json
import subprocess
import sys
from pathlib import Path

import pytest

from cc_feishu import chunk, notify

REPO = Path(__file__).resolve().parents[1]


def run_cli(
    hook_json: str, *, flag: bool = False, dry_env: bool = True
) -> subprocess.CompletedProcess[str]:
    """DRY_RUN 环境变量或 --dry-run 标志运行 notify 子命令。"""
    env = {
        "PYTHONPATH": str(REPO / "src"),
        "PATH": "/usr/bin:/bin",
        "CC_FEISHU_CONFIG": "/nonexistent/config.toml",
    }
    if dry_env:
        env["DRY_RUN"] = "1"
    cmd = [sys.executable, "-m", "cc_feishu", "notify"]
    if flag:
        cmd.append("--dry-run")
    return subprocess.run(cmd, input=hook_json, capture_output=True, text=True, cwd=REPO, env=env)


def write_transcript(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "t.jsonl"
    p.write_text(
        json.dumps(
            {
                "type": "assistant",
                "isSidechain": False,
                "message": {"content": [{"type": "text", "text": text}]},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return p


def test_dry_run_short_message_t1(tmp_path: Path) -> None:
    tp = write_transcript(tmp_path, "你好，这是一条短消息，含代码：\n\n```python\nprint(1)\n```\n")
    out = run_cli(
        json.dumps({"transcript_path": str(tp), "message": "test notify", "cwd": "/tmp/wd"})
    )
    assert out.returncode == 0
    assert "=== chunk 1/1 " in out.stdout  # T1 单片
    assert "> print(1)" in out.stdout  # T1 内容保留
    assert "已截断" not in out.stdout  # T1 无截断标记
    assert out.stdout.endswith("=== end (1 chunks) ===\n")  # 输出格式收尾


def test_dry_run_flag_equivalent_to_env(tmp_path: Path) -> None:
    tp = write_transcript(tmp_path, "正文")
    hook = json.dumps({"transcript_path": str(tp), "message": "m", "cwd": "/w"})
    by_env = run_cli(hook)  # DRY_RUN=1 环境变量
    by_flag = run_cli(hook, flag=True, dry_env=False)  # --dry-run 标志
    assert by_env.stdout == by_flag.stdout  # 两路等价


def test_basic_notify_without_transcript_t5() -> None:
    out = run_cli(json.dumps({"message": "test notify", "cwd": "/tmp/wd"}))
    assert out.returncode == 0
    assert "=== chunk 1/1 " in out.stdout  # T5 单片基础通知
    assert "from: " in out.stdout  # T5 头部保留（默认 hostname，test_config 已单测）


def test_cli_invalid_stdin_json_degrades() -> None:
    # hook stdin 非法 JSON：降级基础通知，退出 0，不崩（已记录差异 #2）
    out = run_cli("{not json")
    assert out.returncode == 0
    assert "=== chunk 1/1 " in out.stdout
    assert "等待输入" in out.stdout  # message 缺省文案


def test_empty_stdin_basic_notify() -> None:
    out = run_cli("")
    assert out.returncode == 0
    assert "=== chunk 1/1 " in out.stdout


def test_build_notification_format() -> None:
    # 头部格式与旧脚本逐字节一致：# message\n\n- `cwd`\n\nfrom: label
    assert (
        notify.build_notification({"message": "等待输入权限确认", "cwd": "/home/x"}, "", "host1")
        == "# 等待输入权限确认\n\n- `/home/x`\n\nfrom: host1"
    )
    # 无 message → "等待输入"；无 cwd → 空反引号（旧脚本 `or` 语义）
    assert notify.build_notification({}, "", "host1") == "# 等待输入\n\n- ``\n\nfrom: host1"


def test_quote_body() -> None:
    # 正文引用化：首行 "> "，续行 "\n> "（空行成 "> "，旧脚本行为）
    full = notify.build_notification({"message": "m", "cwd": "/w"}, "第一行\n\n第二行", "h")
    assert full.endswith("> 第一行\n> \n> 第二行")


def test_large_body_ok() -> None:
    # 数百 KB 正文：进程内直调不崩、分片正确（argv 上限不复存在）
    big = "汉字内容测试" * 20000  # ~420KB
    full = notify.build_notification({"message": "test", "cwd": "/tmp/wd"}, big, "box")
    chunks = chunk.split_notify_chunks(full, 12000)
    assert len(chunks) > 10
    for c in chunks:
        assert chunk.byte_len(c) <= 12000


def test_send_failure_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 发送失败：返回 0、失败记 stderr、best-effort 不中断
    sent: list[list[str]] = []
    cfg = tmp_path / "config.toml"
    cfg.write_text('lark_uid = "ou_x"\n', encoding="utf-8")
    monkeypatch.setenv("CC_FEISHU_CONFIG", str(cfg))

    def fake_send_chunks(uid: str, chunks: list[str]) -> int:
        sent.append(chunks)
        return 1  # 全部失败

    monkeypatch.setattr(notify.lark, "send_chunks", fake_send_chunks)
    rc = notify.notify(json.dumps({"message": "m", "cwd": "/w"}), dry_run=False)
    assert rc == 0
    assert len(sent) == 1  # 基础通知单片


def test_missing_uid_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_FEISHU_CONFIG", "/nonexistent/config.toml")
    boom = RuntimeError("should not send")
    monkeypatch.setattr(notify.lark, "send_chunks", boom)
    rc = notify.notify(json.dumps({"message": "m", "cwd": "/w"}), dry_run=False)
    assert rc == 0  # 未配置 lark_uid：跳过发送而非崩溃
