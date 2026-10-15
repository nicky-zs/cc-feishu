import subprocess

import pytest

from cc_feishu import lark


class FakeRun:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.sleeps: list[float] = []
        self.fail_on = fail_on

    def run(self, cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if self.fail_on is not None and self.fail_on in (cmd[-1] if cmd else ""):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)


def test_send_markdown_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun()
    monkeypatch.setattr(lark.subprocess, "run", fake.run)
    ok, err = lark.send_markdown("ou_x", "# hello")
    assert ok
    assert err == ""
    assert fake.calls == [
        [
            "lark-cli",
            "im",
            "+messages-send",
            "--as",
            "bot",
            "--user-id",
            "ou_x",
            "--markdown",
            "# hello",
        ]
    ]


def test_send_markdown_failure_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun(fail_on="文本")
    monkeypatch.setattr(lark.subprocess, "run", fake.run)
    ok, err = lark.send_markdown("ou_x", "文本")
    assert not ok
    assert err == "boom"


def test_send_markdown_cli_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_fn(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("lark-cli")

    monkeypatch.setattr(lark.subprocess, "run", raise_fn)
    ok, err = lark.send_markdown("ou_x", "文本")
    assert not ok
    assert "lark-cli not found" in err


def test_send_markdown_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # lark-cli 挂起不得无限阻塞 Notification hook：超时按失败处理
    def raise_timeout(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(lark.subprocess, "run", raise_timeout)
    ok, err = lark.send_markdown("ou_x", "文本")
    assert not ok
    assert "超时" in err


def test_send_chunks_throttle_and_isolated_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun(fail_on="第二片")
    monkeypatch.setattr(lark.subprocess, "run", fake.run)
    monkeypatch.setattr(lark.time, "sleep", fake.sleep)
    failed = lark.send_chunks("ou_x", ["第一片", "第二片", "第三片"])
    assert failed == 1
    assert len(fake.calls) == 3  # 失败片不中断其余片
    assert fake.sleeps == [0.3, 0.3]  # 片间限速


def test_send_chunks_single_no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun()
    monkeypatch.setattr(lark.subprocess, "run", fake.run)
    monkeypatch.setattr(lark.time, "sleep", fake.sleep)
    assert lark.send_chunks("ou_x", ["唯一片"]) == 0
    assert fake.sleeps == []
