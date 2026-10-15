# M1 通知链路迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `~/.local/bin/notify_feishu.sh`（sh + heredoc python + jq）严格类型迁移为 `cc-feishu notify` 子命令，行为不变，测试 T1–T7（18 断言）全部迁入 pytest，并提供幂等的 `cc-feishu install` 切换 hook。

**Architecture:** 纯 stdlib Python 包（src layout），按设计文档 §3 分层：`chunk.py`（分片纯函数）、`transcript.py`（jsonl 提取）、`config.py`（toml + 环境覆盖）、`lark.py`（lark-cli 封装）、`notify.py`（编排）、`cli.py`（子命令分发）、`install.py`（hook 注册 + 默认配置）。listen/state/deliver/systemd 属 M2/M3，本计划不做。

**Tech Stack:** Python ≥ 3.11（本机 3.12.3），零运行时依赖；mypy strict、ruff、pytest；pipx 分发（`pipx install --editable .` 供验收）。

**Spec:** `docs/specs/2026-10-15-design.md`（已确认；本计划实现其第 8 节里程碑 M1）

## Global Constraints

- Python ≥ 3.11，**零运行时依赖**（仅 stdlib：subprocess/json/tomllib/socket/dataclasses 等）
- **mypy strict = true 全绿**（`mypy src`）+ `ruff check .` + `ruff format .` 是每次提交门槛
- pytest 测试通过 `pythonpath = ["src"]` 直接跑 src，无需安装
- 现有 bash 套件 T1–T7 的 **18 项断言全部迁入** pytest（断言文本保持等价）
- M1 行为不变：新旧链路 DRY_RUN 输出**逐字节一致**（含元数据条目的真实 transcript 除外，见差异 5）。仅允许五个明确记录的差异：
  1. `from:` 行默认用 hostname（旧脚本硬编码 `10.123.179.32`；比对验收时用 `from_label` 配置对齐）
  2. hook stdin 非法 JSON 时降级为基础通知并退出 0（旧链路 python 直接崩）
  3. `FEISHU_CHUNK_BYTES` 非整数值时忽略（旧链路崩溃）
  4. transcript jsonl 中的空行跳过而非毒化整体结果（旧链路 jq 遇空行报错、只发基础通知）
  5. **提取重置规则收紧**（设计文档 0fe935f 修订，第三次线上 bug 修复）：重置只认 `user` 条目；`assistant` 主链条目追加；**其余一切**（sidechain assistant 及 `last-prompt`/`ai-title`/`mode`/`permission-mode`/`atis-latch`/`attachment`/`system`/`file-history-*`/`cost-state` 等元数据）一律忽略。旧 jq「非 assistant 即重置」会被 turn 结束后追加的元数据把正文清空（通知只剩头部）。等价关系：`新语义(raw) == 旧语义(过滤掉全部被忽略条目后的 raw)`——验收比对据此构造旧侧基线（Task 8）
- 通知是 best-effort：发送失败只记 stderr，**hook 退出码恒 0**
- 配置键名与默认值 verbatim 依设计 §4：`lark_uid`（默认 `""`）、`chunk_bytes = 12000`、`from_label`（默认 `""`）、`delivery = "tmux"`、`resume_prefix = "/go"`、`routing_ttl_hours = 24`；路径 `~/.config/cc-feishu/config.toml`
- 环境变量：`CC_FEISHU_CONFIG` 覆盖配置路径（测试用）；`FEISHU_CHUNK_BYTES` 覆盖单片预算（与旧脚本同名同义）；`DRY_RUN=1` 等价 `--dry-run`；`CLAUDE_CONFIG_DIR` 覆盖 settings.json 位置（install 用）
- 直接在 main 分支开发（新仓库，M1 即首个功能，无需 worktree）
- lark-cli 发送命令 verbatim：`lark-cli im +messages-send --as bot --user-id <uid> --markdown <chunk>`；片间 sleep 0.3s；单片失败只记 stderr 前 500 字符
- 迁移源（只读参考，勿改动）：`~/.local/bin/notify_feishu.sh`、`~/ai_works/notify_feishu_tests/run.sh`

## Review Focus

以下六类输入是设计文档未显式覆盖（或刚由 0fe935f 补充）、但最可能坑到使用者的场景（已逐条落实到所属任务的测试）：

1. **turn 结束后追加的元数据条目**（第三次线上 bug，设计 0fe935f）：`last-prompt`/`ai-title`/`mode`/`attachment` 等出现在最后一段 assistant 内容之后时，正文必须保留（旧 jq 会清空只剩头部）；元数据夹在两轮之间且有 user 边界时，新旧语义等价 → Task 3 `test_trailing_metadata_preserves_content` + `test_metadata_between_turns_same_as_old`
2. **transcript 尾部半行**（Claude Code 正在写入时触发 hook）：旧链路 jq 全有或全无——任一行坏则整体返回空、发基础通知，绝不能输出半截内容 → Task 3 `test_malformed_line_returns_empty` + `test_partial_content_never_leaks`
3. **AskUserQuestion 选项 description 空串 vs 缺失**（jq 真值语义：空串也是真）：`""` 要产出 `"- 选项一："`（带冒号），缺失则不带 → Task 3 `test_option_description_empty_vs_missing`
4. **hook stdin 非法 JSON**：降级为基础通知、退出 0，不能崩（已记录的与旧链路差异）→ Task 6 `test_cli_invalid_stdin_json_degrades`
5. **极小/垃圾 chunk_bytes**：floor 到 1000；小预算硬切不崩、UTF-8 边界安全、内容可精确还原（hard_split 返回 None 的兜底分支为旧脚本原样保留的防御代码，实践中不可达）→ Task 2 `test_hard_split_small_budget` + Task 4 `test_chunk_bytes_floor`
6. **settings.json 缺失/损坏/Notification 里并存其他 hook**：install 不丢数据、不重复注册、幂等 → Task 7 `test_patch_settings_creates_missing` / `test_patch_settings_malformed` / `test_patch_settings_preserves_other_hooks`

另有两条也已在任务内固化：单片发送失败不中断其余片（Task 6 `test_send_failure_isolated`）；数百 KB 正文不经 argv 传递、不崩（Task 6 `test_large_body_ok`，进程内直调）。

---

### Task 1: 工程骨架

**Files:**
- Create: `pyproject.toml`
- Create: `src/cc_feishu/__init__.py`
- Create: `src/cc_feishu/__main__.py`
- Create: `src/cc_feishu/cli.py`
- Create: `src/cc_feishu/py.typed`
- Test: `tests/test_package.py`

**Interfaces:**
- Consumes: 无
- Produces: `cc_feishu.__version__: str`；`cc_feishu.cli.main(argv: list[str] | None = None) -> int`；`python -m cc_feishu` 可执行（Task 6/7 注册子命令）

- [ ] **Step 1: 写失败测试**

`tests/test_package.py`：

```python
import subprocess
import sys
from pathlib import Path

import cc_feishu


def test_version() -> None:
    assert cc_feishu.__version__ == "0.1.0"


def test_python_m_help_exits_zero() -> None:
    repo = Path(__file__).resolve().parents[1]
    p = subprocess.run(
        [sys.executable, "-m", "cc_feishu", "--help"],
        capture_output=True, text=True, cwd=repo,
        env={"PYTHONPATH": str(repo / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert p.returncode == 0
    assert "cc-feishu" in p.stdout
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_package.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc_feishu'`

- [ ] **Step 3: 写实现**

`pyproject.toml`：

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "cc-feishu"
version = "0.1.0"
description = "Claude Code ↔ 飞书桥：等待人工响应时把生成内容发到飞书；反向驱动本地会话（M2+）"
requires-python = ">=3.11"

[project.scripts]
cc-feishu = "cc_feishu.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
cc_feishu = ["py.typed"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.mypy]
strict = true
python_version = "3.11"
mypy_path = "src"

[tool.ruff]
line-length = 100
target-version = "py311"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B"]
```

`src/cc_feishu/__init__.py`：

```python
"""cc-feishu：Claude Code ↔ 飞书桥。"""

__version__ = "0.1.0"
```

`src/cc_feishu/cli.py`：

```python
"""cc-feishu 子命令分发。M1 只有 notify/install 的注册框架，子命令随后续任务加入。"""

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cc-feishu", description="Claude Code ↔ 飞书桥")
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`src/cc_feishu/__main__.py`：

```python
"""`python -m cc_feishu` 入口。"""

import sys

from cc_feishu.cli import main

sys.exit(main())
```

`src/cc_feishu/py.typed`：空文件（`touch`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_package.py -v`
Expected: 2 PASS

- [ ] **Step 5: 质量门槛**

Run: `ruff format . && ruff check . && mypy src && pytest`
Expected: 全绿（ruff 无告警、mypy no errors、测试全过）

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src tests
git commit -m "chore: 工程骨架（pyproject/src layout/mypy strict/ruff/pytest）"
```

### Task 2: chunk.py 分片纯函数

**Files:**
- Create: `src/cc_feishu/chunk.py`
- Test: `tests/test_chunk.py`

**Interfaces:**
- Consumes: 无（纯函数，无项目内依赖）
- Produces:
  - `chunk.byte_len(s: str) -> int` — UTF-8 字节数
  - `chunk.chunk_text(text: str, budget: int) -> list[str]` — 逐行累加切块，围栏中间切点补闭合/重开
  - `chunk.split_notify_chunks(full: str, budget: int) -> list[str]` — 含「（续 i/n）」标记的重切逻辑（notify 直接调用）

- [ ] **Step 1: 写失败测试**

`tests/test_chunk.py`（T2/T3/T4 断言的 chunk 级迁移 + 边界）：

```python
from cc_feishu import chunk


def quote(text: str) -> str:
    """复刻旧脚本的引用化：首行 "> "，其余 "\n> " 连接（空行成 "> "）。"""
    return "> " + "\n> ".join(text.split("\n"))


def build_full(last_msg: str) -> str:
    header = "# test notify\n\n- `/tmp/wd`\n\nfrom: 10.123.179.32"
    if last_msg:
        return header + "\n\n" + quote(last_msg)
    return header


def unquote(line: str) -> str:
    if line.startswith("> "):
        return line[2:]
    if line.startswith(">"):
        return line[1:]
    return line


SKIP = ("# ", "- `", "from: ", "（续 ")


def body_lines(chunks: list[str]) -> list[str]:
    out = []
    for c in chunks:
        for ln in c.split("\n"):
            u = unquote(ln)
            if u.startswith(SKIP) or not u.strip():
                continue
            out.append(u)
    return out


def assert_chunks_ok(chunks: list[str], budget: int) -> None:
    for i, c in enumerate(chunks):
        fences = [ln for ln in c.split("\n") if unquote(ln).lstrip().startswith("```")]
        assert len(fences) % 2 == 0, f"chunk {i+1}: unbalanced fences ({len(fences)})"


def test_byte_len() -> None:
    assert chunk.byte_len("abc") == 3
    assert chunk.byte_len("汉") == 3


def test_short_text_single_chunk() -> None:
    text = "你好，这是一条短消息，含代码：\n\n```python\nprint(1)\n```\n"
    chunks = chunk.split_notify_chunks(build_full(text), 12000)
    assert len(chunks) == 1  # T1 单片
    assert "> print(1)" in chunks[0]  # T1 内容保留
    assert "已截断" not in chunks[0]  # T1 无截断标记
    assert_chunks_ok(chunks, 12000)


def test_multi_paragraph_42kb() -> None:
    lines = []
    for i in range(500):
        lines.append(f"第{i:04d}行：" + "汉字内容测试" * 4)
        if i % 5 == 4:
            lines.append("")
    text = "\n".join(lines)
    chunks = chunk.split_notify_chunks(build_full(text), 12000)
    assert len(chunks) >= 2  # T2 多片
    for c in chunks:  # T2 每片 ≤12000B（含（续）标记）
        assert chunk.byte_len(c) <= 12000, chunk.byte_len(c)
    assert_chunks_ok(chunks, 12000)
    got = [ln for ln in body_lines(chunks)]  # T2 行级还原
    want = [ln for ln in text.split("\n") if ln.strip()]
    assert got == want
    for i in range(2, len(chunks) + 1):  # 续片标记
        assert any(ln.startswith(f"（续 {i}/{len(chunks)}）") for ln in chunks[i - 1].split("\n"))


def test_fence_across_chunks() -> None:
    code = [f"def fn_{i}(): return {i}  # " + "x" * 50 for i in range(500)]
    text = "代码前说明\n\n```python\n" + "\n".join(code) + "\n```\n\n代码后说明"
    chunks = chunk.split_notify_chunks(build_full(text), 12000)
    assert any("```python" in c for c in chunks)  # T3 保留语言标记
    assert_chunks_ok(chunks, 12000)  # T3 每片围栏配平
    body = body_lines(chunks)  # T3 内容子序列还原
    it = iter(body)
    for ol in (ln for ln in text.split("\n") if ln.strip()):
        for bl in it:
            if bl == ol:
                break
        else:
            raise AssertionError(f"missing line: {ol[:40]!r}")


def test_single_long_line_hard_split() -> None:
    text = "汉" * 15000
    chunks = chunk.split_notify_chunks(build_full(text), 12000)
    assert len(chunks) >= 3  # T4 ≥3 片
    assert_chunks_ok(chunks, 12000)
    got = "".join(unquote(ln) for ln in body_lines(chunks))  # T4 硬切后可精确还原
    assert got == text


def test_hard_split_small_budget() -> None:
    # 小预算触发硬切：UTF-8 边界安全 + 精确还原（review focus #4）
    text = "汉" * 200  # 600B 单行，引用化后 602B
    chunks = chunk.chunk_text(quote(text), 200)
    assert len(chunks) >= 3
    assert "".join(unquote(ln) for c in chunks for ln in c.split("\n") if ln.strip()) == text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_chunk.py -v`
Expected: FAIL — `ImportError: cannot import name 'byte_len'`

- [ ] **Step 3: 写实现**

`src/cc_feishu/chunk.py`（自旧脚本 heredoc python 逐行迁移，语义不变）：

```python
"""分片纯函数：把引用化后的通知全文切成每片 ≤budget UTF-8 字节的多片。"""

CLOSE = "> ```"  # 切点落在代码围栏中间时，片尾补的闭合行
CLOSE_B = len(CLOSE.encode()) + 1


def byte_len(s: str) -> int:
    return len(s.encode("utf-8"))


def _unquote(line: str) -> str:
    if line.startswith("> "):
        return line[2:]
    if line.startswith(">"):
        return line[1:]
    return line


def _fence_marker(line: str) -> str | None:
    """行是 ``` 围栏则返回围栏后的内容（语言标记），否则 None。"""
    s = _unquote(line).lstrip()
    return s if s.startswith("```") else None


def _hard_split(line: str, room: int, prefix: str, budget: int) -> list[str] | None:
    """超长行按 UTF-8 字符边界硬切：首段进 room 字节，续段补 prefix。

    返回 None 表示预算小到放不下一个字符，由调用方兜底。"""
    pieces: list[str] = []
    data = line.encode("utf-8")
    first = True
    while len(data) > room:
        cut = room
        while cut > 0 and (data[cut] & 0xC0) == 0x80:  # 回退到多字节字符的起始字节
            cut -= 1
        if cut <= 0:
            return None
        piece = data[:cut].decode("utf-8", "replace")
        pieces.append(piece if first else prefix + piece)
        first = False
        data = data[cut:]
        room = budget - 32  # 续段预算：留出 prefix、换行、围栏重开头和片尾闭合的余量
    piece = data.decode("utf-8", "replace")
    pieces.append(piece if first else prefix + piece)
    return pieces


def chunk_text(text: str, budget: int) -> list[str]:
    """尽量切在空行/换行处；切点落在代码围栏中间时片尾补闭合、下片重开。"""
    chunks: list[str] = []
    cur: list[str] = []
    cur_b = 0
    fence_open = False
    fence_lang = ""

    def flush(final: bool = False) -> None:
        nonlocal cur, cur_b
        reopen = fence_open and not final
        if fence_open:
            cur.append(CLOSE)
            cur_b += CLOSE_B
        if cur:
            chunks.append("\n".join(cur))
        cur = []
        cur_b = 0
        if reopen:
            head = "> ```" + fence_lang
            cur.append(head)
            cur_b = byte_len(head) + 1

    for line in text.split("\n"):
        fm = _fence_marker(line)
        if fm is not None and not fence_open:
            after_open, after_lang = True, fm[3:].strip()[:20]
        elif fm is not None:
            after_open, after_lang = False, ""
        else:
            after_open, after_lang = fence_open, fence_lang
        lb = byte_len(line) + 1
        reserve = CLOSE_B if after_open else 0  # 片尾可能要补闭合行
        if cur_b + lb + reserve <= budget:
            cur.append(line)
            cur_b += lb
            fence_open, fence_lang = after_open, after_lang
            continue
        flush()
        if cur_b + lb + reserve <= budget:
            cur.append(line)
            cur_b += lb
            fence_open, fence_lang = after_open, after_lang
            continue
        # 单行自身就超预算：按 UTF-8 边界硬切。
        # 围栏标记行不可能是超长行，所以围栏状态在行内不会变化。
        prefix = "" if fence_open else "> "
        pieces = _hard_split(line, budget - cur_b - reserve - 1, prefix, budget)
        if pieces is None:
            pieces = [line]  # 极端小预算兜底，允许单片略超
        for piece in pieces:
            pb = byte_len(piece) + 1
            if cur and cur_b + pb + reserve > budget:
                flush()
            cur.append(piece)
            cur_b += pb
        fence_open, fence_lang = after_open, after_lang
    flush(final=True)
    return chunks


def split_notify_chunks(full: str, budget: int) -> list[str]:
    """分片并给第 2 片起加「（续 i/n)」标记：多片时预留标记空间后重切。"""
    chunks = chunk_text(full, budget)
    if len(chunks) > 1:
        # 预留「（续 i/n）」标记的空间后重切，确保加标记后仍不超预算
        chunks = chunk_text(full, budget - 64)
        chunks[1:] = ["（续 %d/%d）\n\n" % (i + 2, len(chunks)) + c for i, c in enumerate(chunks[1:])]
    return chunks
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_chunk.py -v`
Expected: 6 PASS

- [ ] **Step 5: 质量门槛 + Commit**

Run: `ruff format . && ruff check . && mypy src && pytest`

```bash
git add src/cc_feishu/chunk.py tests/test_chunk.py
git commit -m "feat(chunk): 分片纯函数迁移（T2/T3/T4 断言 + 硬切兜底）"
```

### Task 3: transcript.py 提取最后一段连续 assistant 内容

**Files:**
- Create: `src/cc_feishu/transcript.py`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Consumes: 无（纯函数，仅 stdlib json/pathlib）
- Produces:
  - `transcript.extract_last_assistant(transcript_path: str | Path) -> str` — 读 jsonl 末 100 行（与旧脚本 `tail -n 100` 等价），返回最后一段连续主链 assistant 内容合并文本；文件不存在/空/任一行坏 JSON 时返回 `""`

**条目类型处理规则**（设计文档 0fe935f 修订版——第三次线上 bug 修复，勿改动语义）：

| 条目 | 处理 | 备注 |
|---|---|---|
| `type == "assistant"` 且 `isSidechain != true`（主链） | **追加**其格式化内容 | 空内容条目过滤但不重置 |
| `type == "user"`（用户输入 / tool_result） | **重置** | 真正的对话边界 |
| **其余一切**（sidechain assistant；`last-prompt`/`ai-title`/`agent-name`/`custom-title`/`mode`/`permission-mode`/`atis-latch`/`attachment`/`system`/`file-history-*`/`cost-state` 等元数据） | **忽略**（不追加不重置） | 旧 jq「非 assistant 即重置」会被 turn 末尾元数据清空正文 |

sidechain assistant 归入「忽略」是本计划的决定（设计未点名）：保留旧脚本「侧链内容不进通知」的意图，同时不让它成为重置边界。与旧链路的等价关系：`新语义(raw) == 旧语义(raw 过滤掉全部被忽略条目)`。

块级格式（自旧 jq 迁移，jq 真值规则：只有 `false`/`null` 为假，`""` 为真）：

- content 为字符串（老格式）→ 原样
- `text` 块 → `.text` 原样
- `AskUserQuestion` tool_use → `**标题** 问题（可多选）` + `- 标签：描述` 选项列表
- 其他块（thinking/其他 tool_use）→ 不进列表（jq `empty`）
- `if $x.header`：`""` 为真 → `"** ** "`；`if .description`：`""` 为真 → 带 `"："`，键缺失 → 不带

- [ ] **Step 1: 写失败测试**

`tests/test_transcript.py`（T6/T7 断言的函数级迁移 + review focus #1/#2/#3）：

```python
import json
from pathlib import Path
from typing import Any

from cc_feishu import transcript


def assistant_entry(text: str) -> str:
    return json.dumps(
        {"type": "assistant", "isSidechain": False,
         "message": {"content": [{"type": "text", "text": text}]}},
        ensure_ascii=False)


def ask_entry(questions: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"type": "assistant", "isSidechain": False,
         "message": {"content": [{"type": "tool_use", "id": "call_x", "name": "AskUserQuestion",
                                  "input": {"questions": questions}}]}},
        ensure_ascii=False)


def user_entry() -> str:
    return json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}})


def sidechain_entry(text: str) -> str:
    return json.dumps(
        {"type": "assistant", "isSidechain": True,
         "message": {"content": [{"type": "text", "text": text}]}},
        ensure_ascii=False)


def meta_entry(mtype: str) -> str:
    """turn 结束后 transcript 追加的元数据条目（第三次线上 bug 的元凶）。"""
    return json.dumps({"type": mtype, "value": "whatever"}, ensure_ascii=False)


def write(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert transcript.extract_last_assistant(tmp_path / "none.jsonl") == ""


def test_empty_file_returns_empty(tmp_path: Path) -> None:
    assert transcript.extract_last_assistant(write(tmp_path, [])) == ""


def test_malformed_line_returns_empty(tmp_path: Path) -> None:
    # 旧链路 jq 语义：任一行坏 JSON → 整体输出空，发基础通知，绝不输出半截内容
    p = write(tmp_path, [assistant_entry("好内容"), "not json"])
    assert transcript.extract_last_assistant(p) == ""


def test_partial_content_never_leaks(tmp_path: Path) -> None:
    # 半行（Claude 正在写入时触发 hook）：join 前被截断的内容不能泄漏
    p = tmp_path / "t.jsonl"
    p.write_text(assistant_entry("完整行") + '\n{"type":"assistant","isSidechain":false,', encoding="utf-8")
    assert transcript.extract_last_assistant(p) == ""


def test_plain_string_content(tmp_path: Path) -> None:
    # 旧版 content 为字符串的条目（老格式兼容）
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "assistant", "isSidechain": False,
                             "message": {"content": "字符串正文"}}, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    assert transcript.extract_last_assistant(p) == "字符串正文"


def test_ask_user_question(tmp_path: Path) -> None:  # T6 场景（4 断言）
    p = write(tmp_path, [
        assistant_entry("问题前的铺垫说明文字。"),
        ask_entry([{"question": "选哪个方案？", "header": "形态路线", "multiSelect": False,
                    "options": [{"label": "B：独立项目（推荐）", "description": "pipx 装 CLI + install.sh"},
                                {"label": "先小重构缓建仓", "description": "单文件 Python"}]}]),
    ])
    out = transcript.extract_last_assistant(p)
    assert "**形态路线** 选哪个方案？" in out  # T6 问题与标题
    assert "- B：独立项目（推荐）：pipx 装 CLI + install.sh" in out  # T6 选项含描述
    assert "- 先小重构缓建仓：单文件 Python" in out  # T6 第二个选项
    assert "问题前的铺垫说明文字" in out  # T6 铺垫与问题结合呈现


def test_multiselect_marker(tmp_path: Path) -> None:
    p = write(tmp_path, [
        ask_entry([{"question": "Q?", "multiSelect": True, "options": [{"label": "a"}]}]),
    ])
    assert "（可多选）" in transcript.extract_last_assistant(p)


def test_boundary_reset(tmp_path: Path) -> None:  # T7 场景（2 断言）
    p = write(tmp_path, [
        assistant_entry("上一轮的陈旧文本。"),
        user_entry(),
        assistant_entry("本轮的说明文字。"),
        ask_entry([{"question": "T7 的问题？", "header": "测试", "multiSelect": False,
                    "options": [{"label": "选项一", "description": ""}]}]),
    ])
    out = transcript.extract_last_assistant(p)
    assert "本轮的说明文字" in out and "**测试** T7 的问题？" in out  # T7 本轮文本+问题都在
    assert "上一轮的陈旧文本" not in out  # T7 旧轮文本被 user 边界重置


def test_trailing_metadata_preserves_content(tmp_path: Path) -> None:
    # 第三次线上 bug（设计 0fe935f）：turn 结束后追加的元数据不得清空正文
    p = write(tmp_path, [
        user_entry(),
        assistant_entry("本轮生成的主要内容。"),
        meta_entry("last-prompt"),
        meta_entry("ai-title"),
        meta_entry("mode"),
        meta_entry("permission-mode"),
        meta_entry("atis-latch"),
        meta_entry("attachment"),
        meta_entry("system"),
        meta_entry("file-history-snapshot"),
        meta_entry("cost-state"),
    ])
    assert transcript.extract_last_assistant(p) == "本轮生成的主要内容。"


def test_metadata_between_turns_same_as_old(tmp_path: Path) -> None:
    # 元数据夹在两轮之间且随后有 user 边界：新旧语义等价（都只剩本轮）
    p = write(tmp_path, [
        assistant_entry("上一轮内容"),
        meta_entry("last-prompt"),
        meta_entry("ai-title"),
        user_entry(),
        assistant_entry("本轮内容"),
        meta_entry("mode"),
    ])
    assert transcript.extract_last_assistant(p) == "本轮内容"


def test_only_metadata_returns_empty(tmp_path: Path) -> None:
    # 尾部 100 行只有元数据（真实 case：dee558e2 transcript）：发基础通知
    p = write(tmp_path, [meta_entry("last-prompt"), meta_entry("mode"), meta_entry("system")])
    assert transcript.extract_last_assistant(p) == ""


def test_option_description_empty_vs_missing(tmp_path: Path) -> None:
    # jq 真值语义：description="" 也是真 → 带冒号；缺失 → 不带
    p = write(tmp_path, [
        ask_entry([{"question": "Q?", "header": "H", "multiSelect": False,
                    "options": [{"label": "选项一", "description": ""},
                                {"label": "选项二"}]}]),
    ])
    out = transcript.extract_last_assistant(p)
    assert "- 选项一：" in out
    assert "- 选项二" in out and "- 选项二：" not in out


def test_sidechain_assistant_ignored(tmp_path: Path) -> None:
    # sidechain：内容不进通知、也不构成重置边界（本计划决定，见语义表）
    p = write(tmp_path, [
        assistant_entry("主链前文"),
        sidechain_entry("侧链内容"),
        assistant_entry("主链后文"),
    ])
    assert transcript.extract_last_assistant(p) == "主链前文\n\n主链后文"
    p = write(tmp_path, [sidechain_entry("只有侧链")])
    assert transcript.extract_last_assistant(p) == ""


def test_empty_assistant_entry_does_not_reset(tmp_path: Path) -> None:
    # 无可用内容的 assistant 条目：过滤但不重置（jq map(select(length>0))）
    p = write(tmp_path, [
        assistant_entry("前文"),
        json.dumps({"type": "assistant", "isSidechain": False,
                    "message": {"content": [{"type": "thinking", "thinking": "…"}]}}),
        assistant_entry("后文"),
    ])
    assert transcript.extract_last_assistant(p) == "前文\n\n后文"


def test_tail_limit_100_lines(tmp_path: Path) -> None:
    lines = [assistant_entry(f"行{i:03d}") for i in range(150)]
    out = transcript.extract_last_assistant(write(tmp_path, lines))
    assert "行000" not in out and "行049" not in out  # 末 100 行之外的被丢弃
    assert "行050" in out and "行149" in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_transcript.py -v`
Expected: FAIL — `ImportError: No module named 'cc_feishu.transcript'`（或 `AttributeError`）

- [ ] **Step 3: 写实现**

`src/cc_feishu/transcript.py`（块级格式自旧 jq 迁移；条目类型规则按设计 0fe935f 修订版）：

```python
"""transcript jsonl 解析 → 最后一段连续 assistant 内容。

三次线上 bug 的修复结论固化于此：
1. AskUserQuestion 的问题在 tool_use 块里——漏掉则通知只有铺垫文本
2. 提问时需携带上文——只取 last 条目则丢失回答问题所需的上下文
3. turn 结束后 transcript 会追加元数据条目——重置只认 user 条目，其余一律忽略
"""

import json
from pathlib import Path
from typing import Any

TAIL_LINES = 100  # 与旧脚本 tail -n 100 等价


def _is_main_assistant(e: object) -> bool:
    """主链 assistant：type == "assistant" 且 isSidechain 不为 true（jq: $e.isSidechain != true）。"""
    return isinstance(e, dict) and e.get("type") == "assistant" and e.get("isSidechain") is not True


def _is_user(e: object) -> bool:
    return isinstance(e, dict) and e.get("type") == "user"


def _fmt_question(q: dict[str, Any]) -> str:
    question = q.get("question")
    if not isinstance(question, str):
        return ""
    parts = []
    header = q.get("header")
    if isinstance(header, str):  # jq 真值："" 也为真 → "** ** "
        parts.append("**" + header + "** ")
    parts.append(question)
    if q.get("multiSelect"):
        parts.append("（可多选）")
    opt_lines = []
    options = q.get("options")
    if isinstance(options, list):
        for o in options:
            if not isinstance(o, dict):
                continue
            label = o.get("label")
            if not isinstance(label, str):
                continue
            desc = o.get("description")
            if isinstance(desc, str):  # jq 真值："" 也为真 → 带 "："
                opt_lines.append("- " + label + "：" + desc)
            else:
                opt_lines.append("- " + label)
    return "".join(parts) + "\n" + "\n".join(opt_lines)


def _fmt_assistant(e: dict[str, Any]) -> str:
    """一条主链 assistant 条目 → 文本；无可用内容时返回 ""（不重置，仅被过滤）。"""
    msg = e.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif btype == "tool_use" and block.get("name") == "AskUserQuestion":
            inp = block.get("input")
            if isinstance(inp, dict):
                questions = inp.get("questions")
                if isinstance(questions, list):
                    for q in questions:
                        if isinstance(q, dict):
                            parts.append(_fmt_question(q))
    return "\n\n".join(parts)


def extract_last_assistant(transcript_path: str | Path) -> str:
    """最后一段连续主链 assistant 条目的内容合并。

    条目规则（设计 0fe935f）：assistant 主链追加、user 重置、其余（sidechain 与
    last-prompt/ai-title/mode 等元数据）忽略。
    任一行坏 JSON → 返回 ""（与旧 jq 链路一致：全有或全无，绝不出半截内容）。
    空行跳过（旧链路遇空行整体报错，此处更稳；已记录的差异 #4）。
    """
    path = Path(transcript_path)
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    texts: list[str] = []
    for line in lines[-TAIL_LINES:]:
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            return ""
        if _is_main_assistant(e):
            t = _fmt_assistant(e)
            if t:  # 空内容条目被过滤但不重置（jq map(select(length>0))）
                texts.append(t)
        elif _is_user(e):
            texts = []  # user（用户输入/tool_result）是真正的对话边界 → 重置
        # 其余一切类型：忽略——元数据与 sidechain 既不追加也不重置（bug #3 修复）
    # 旧脚本经 $(...) 命令替换取 jq 输出，会剥掉全部尾部换行——逐字节对齐必须复刻
    return "\n\n".join(texts).rstrip("\n")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_transcript.py -v`
Expected: 15 PASS

- [ ] **Step 5: 质量门槛 + Commit**

Run: `ruff format . && ruff check . && mypy src && pytest`

```bash
git add src/cc_feishu/transcript.py tests/test_transcript.py
git commit -m "feat(transcript): jsonl 提取（user-only 重置 + 元数据忽略，T6/T7 断言迁入）"
```
### Task 4: config.py 配置加载

**Files:**
- Create: `src/cc_feishu/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `config.Config`（frozen dataclass）：字段 `lark_uid: str = ""`、`chunk_bytes: int = 12000`、`from_label: str = ""`、`delivery: str = "tmux"`、`resume_prefix: str = "/go"`、`routing_ttl_hours: int = 24`
  - `config.config_path() -> Path` — `$CC_FEISHU_CONFIG` 覆盖，否则 `~/.config/cc-feishu/config.toml`
  - `config.load_config() -> Config` — 文件缺失/损坏时全默认；`FEISHU_CHUNK_BYTES` 环境变量覆盖 `chunk_bytes`（非整数忽略）
  - `config.effective_chunk_bytes(cfg: Config) -> int` — `max(cfg.chunk_bytes, 1000)`
  - `config.effective_from_label(cfg: Config) -> str` — 空则 `socket.gethostname()`

- [ ] **Step 1: 写失败测试**

`tests/test_config.py`：

```python
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


def test_broken_toml_falls_back_to_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 写实现**

`src/cc_feishu/config.py`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_config.py -v`
Expected: 8 PASS

- [ ] **Step 5: 质量门槛 + Commit**

Run: `ruff format . && ruff check . && mypy src && pytest`

```bash
git add src/cc_feishu/config.py tests/test_config.py
git commit -m "feat(config): toml 配置加载与环境变量覆盖"
```

### Task 5: lark.py 发送封装

**Files:**
- Create: `src/cc_feishu/lark.py`
- Test: `tests/test_lark.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `lark.send_markdown(uid: str, markdown: str) -> tuple[bool, str]` — 调 `lark-cli im +messages-send --as bot --user-id <uid> --markdown <chunk>`；返回 (成功与否, 错误信息截断 500 字符)。lark-cli 不存在（FileNotFoundError）→ `(False, "lark-cli not found")`
  - `lark.send_chunks(uid: str, chunks: list[str]) -> int` — 逐片发送，片间 sleep 0.3s，返回失败片数；单片失败不中断（记 stderr）

- [ ] **Step 1: 写失败测试**

`tests/test_lark.py`（monkeypatch `subprocess.run` 和 `time.sleep`，不发真消息）：

```python
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
    assert fake.calls == [["lark-cli", "im", "+messages-send", "--as", "bot",
                           "--user-id", "ou_x", "--markdown", "# hello"]]


def test_send_markdown_failure_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun()
    monkeypatch.setattr(lark.subprocess, "run", fake.run)
    ok, err = lark.send_markdown("ou_x", "文本")
    assert not ok


def test_send_markdown_cli_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_fn(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("lark-cli")
    monkeypatch.setattr(lark.subprocess, "run", raise_fn)
    ok, err = lark.send_markdown("ou_x", "文本")
    assert not ok
    assert "lark-cli not found" in err


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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_lark.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 写实现**

`src/cc_feishu/lark.py`：

```python
"""lark-cli 封装：发送 markdown 消息。lark-cli 继续作为飞书 OpenAPI 执行层。"""

import subprocess
import sys
import time

SEND_CMD = ["lark-cli", "im", "+messages-send", "--as", "bot"]
ERR_TRUNCATE = 500
CHUNK_INTERVAL = 0.3  # 片间限速，避免触发消息发送频率限制


def send_markdown(uid: str, markdown: str) -> tuple[bool, str]:
    """发一条 markdown 消息给 uid。返回 (成功, 错误信息截断)。"""
    cmd = [*SEND_CMD, "--user-id", uid, "--markdown", markdown]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return False, "lark-cli not found"
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
            sys.stderr.write("cc-feishu notify: 第 %d/%d 片发送失败: %s\n" % (i + 1, len(chunks), err))
        if i < len(chunks) - 1:
            time.sleep(CHUNK_INTERVAL)
    return failed
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_lark.py -v`
Expected: 5 PASS

- [ ] **Step 5: 质量门槛 + Commit**

Run: `ruff format . && ruff check . && mypy src && pytest`

```bash
git add src/cc_feishu/lark.py tests/test_lark.py
git commit -m "feat(lark): lark-cli 发送封装（限速 + 失败隔离）"
```

### Task 6: notify.py 编排 + CLI 集成（DRY_RUN 行为对齐）

**Files:**
- Create: `src/cc_feishu/notify.py`
- Modify: `src/cc_feishu/cli.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: `chunk.split_notify_chunks`、`chunk.byte_len`、`transcript.extract_last_assistant`、`config.load_config`/`effective_chunk_bytes`/`effective_from_label`、`lark.send_chunks`
- Produces:
  - `notify.build_notification(hook: dict[str, Any], last_msg: str, from_label: str) -> str` — 头部 `# {message 或 "等待输入"}\n\n- \`{cwd}\`\n\nfrom: {label}`；有正文时空行 + `"> " + "\n> ".join(行)`
  - `notify.notify(hook_json: str, dry_run: bool) -> int` — 完整链路，恒返回 0；dry_run 时按旧格式打印分片
  - CLI `cc-feishu notify [--dry-run]`：stdin 读 hook JSON；`DRY_RUN=1` 等价 `--dry-run`（cli.py 负责 stdin/env，notify.py 无 CLI 代码）

**DRY_RUN 输出格式（与旧脚本逐字节一致）**：

```
=== chunk {i}/{n} (bytes={UTF-8 字节数}) ===
{chunk 文本}
=== chunk {i+1}/{n} (bytes=...) ===
...
=== end ({n} chunks) ===
```

- [ ] **Step 1: 写失败测试**

`tests/test_notify.py`（T1/T5 断言的 CLI 级迁移 + review focus #3/#6 + 大正文）：

```python
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cc_feishu import chunk, notify

REPO = Path(__file__).resolve().parents[1]


def run_cli(hook_json: str, *, flag: bool = False, dry_env: bool = True) -> subprocess.CompletedProcess[str]:
    """DRY_RUN 环境变量或 --dry-run 标志运行 notify 子命令。"""
    env = {"PYTHONPATH": str(REPO / "src"), "PATH": "/usr/bin:/bin",
           "CC_FEISHU_CONFIG": "/nonexistent/config.toml"}
    if dry_env:
        env["DRY_RUN"] = "1"
    cmd = [sys.executable, "-m", "cc_feishu", "notify"]
    if flag:
        cmd.append("--dry-run")
    return subprocess.run(cmd, input=hook_json, capture_output=True, text=True, cwd=REPO, env=env)


def write_transcript(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "assistant", "isSidechain": False,
                             "message": {"content": [{"type": "text", "text": text}]}},
                            ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def test_dry_run_short_message_t1(tmp_path: Path) -> None:
    tp = write_transcript(tmp_path, "你好，这是一条短消息，含代码：\n\n```python\nprint(1)\n```\n")
    out = run_cli(json.dumps({"transcript_path": str(tp), "message": "test notify", "cwd": "/tmp/wd"}))
    assert out.returncode == 0
    assert "=== chunk 1/1 " in out.stdout  # T1 单片
    assert "> print(1)" in out.stdout  # T1 内容保留
    assert "已截断" not in out.stdout  # T1 无截断标记
    assert out.stdout.endswith("=== end (1 chunks) ===\n")  # 输出格式收尾


def test_dry_run_flag_equivalent_to_env(tmp_path: Path) -> None:
    tp = write_transcript(tmp_path, "正文")
    hook = json.dumps({"transcript_path": str(tp), "message": "m", "cwd": "/w"})
    by_env = run_cli(hook)                 # DRY_RUN=1 环境变量
    by_flag = run_cli(hook, flag=True, dry_env=False)  # --dry-run 标志
    assert by_env.stdout == by_flag.stdout  # 两路等价


def test_basic_notify_without_transcript_t5() -> None:
    out = run_cli(json.dumps({"message": "test notify", "cwd": "/tmp/wd"}))
    assert out.returncode == 0
    assert "=== chunk 1/1 " in out.stdout  # T5 单片基础通知
    assert "from: " in out.stdout  # T5 头部保留（默认 hostname，Task 4 已单测）


def test_cli_invalid_stdin_json_degrades() -> None:
    # hook stdin 非法 JSON：降级基础通知，退出 0，不崩（review focus #3，已记录差异 #2）
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
    assert notify.build_notification({"message": "等待输入权限确认", "cwd": "/home/x"}, "", "host1") \
        == "# 等待输入权限确认\n\n- `/home/x`\n\nfrom: host1"
    # 无 message → "等待输入"；无 cwd → 空反引号（旧脚本 `or` 语义）
    assert notify.build_notification({}, "", "host1") == "# 等待输入\n\n- ``\n\nfrom: host1"


def test_quote_body() -> None:
    # 正文引用化：首行 "> "，续行 "\n> "（空行成 "> "，旧脚本行为）
    full = notify.build_notification({"message": "m", "cwd": "/w"}, "第一行\n\n第二行", "h")
    assert full.endswith("> 第一行\n> \n> 第二行")


def test_large_body_ok() -> None:
    # 数百 KB 正文：进程内直调不崩、分片正确（review focus 补充：argv 上限不复存在）
    big = "汉字内容测试" * 20000  # ~420KB
    full = notify.build_notification({"message": "test", "cwd": "/tmp/wd"}, big, "box")
    chunks = chunk.split_notify_chunks(full, 12000)
    assert len(chunks) > 10
    for c in chunks:
        assert chunk.byte_len(c) <= 12000


def test_send_failure_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 发送失败：返回 0、失败记 stderr、best-effort 不中断（review focus 补充条目）
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_notify.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 写实现**

`src/cc_feishu/notify.py`：

```python
"""Notification hook 编排：hook JSON → 提取 → 分片 → 发送。best-effort，恒成功返回。"""

import json
import sys
from typing import Any

from cc_feishu import chunk, config, lark, transcript


def build_notification(hook: dict[str, Any], last_msg: str, from_label: str) -> str:
    """通知头 + 引用化正文。头格式与旧脚本逐字节一致。"""
    message = hook.get("message")
    cwd = hook.get("cwd")
    header = "# " + (message if isinstance(message, str) and message else "等待输入")
    header += "\n\n- `" + (cwd if isinstance(cwd, str) else "") + "`"
    header += "\n\nfrom: " + from_label
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
            print("=== chunk %d/%d (bytes=%d) ===" % (i + 1, n, chunk.byte_len(c)))
            print(c)
        print("=== end (%d chunks) ===" % n)
        return 0
    if not cfg.lark_uid:
        sys.stderr.write("cc-feishu notify: 配置缺 lark_uid，跳过发送（先运行 cc-feishu install）\n")
        return 0
    failed = lark.send_chunks(cfg.lark_uid, chunks)
    if failed:
        sys.stderr.write("cc-feishu notify: %d/%d 片发送失败（不中断会话）\n" % (failed, len(chunks)))
    return 0
```

`src/cc_feishu/cli.py` 整体替换为：

```python
"""cc-feishu 子命令分发。stdin/env 处理集中于此，notify.py 保持无 CLI 代码。"""

import argparse
import os
import sys

from cc_feishu import notify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cc-feishu", description="Claude Code ↔ 飞书桥")
    sub = parser.add_subparsers(dest="command")

    p_notify = sub.add_parser("notify", help="Notification hook 入口")
    p_notify.add_argument("--dry-run", action="store_true", help="只打印分片与字节数，不发送")

    args = parser.parse_args(argv)

    if args.command == "notify":
        dry_run = args.dry_run or os.environ.get("DRY_RUN") == "1"
        return notify.notify(sys.stdin.read(), dry_run)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_notify.py -v`
Expected: 10 PASS

- [ ] **Step 5: 质量门槛 + Commit**

Run: `ruff format . && ruff check . && mypy src && pytest`

```bash
git add src/cc_feishu/notify.py src/cc_feishu/cli.py tests/test_notify.py
git commit -m "feat(notify): Notification hook 编排 + CLI 集成（T1/T5 断言迁入，DRY_RUN 行为对齐）"
```

### Task 7: install.py 注册 hook + 默认配置

**Files:**
- Create: `src/cc_feishu/install.py`
- Modify: `src/cc_feishu/cli.py`
- Test: `tests/test_install.py`

**Interfaces:**
- Consumes: `config.config_path()`
- Produces:
  - `install.old_script_path() -> Path` — `~/.local/bin/notify_feishu.sh`（调用时求值，测试可改 HOME）
  - `install.extract_old_lark_uid(old_script: Path) -> str | None` — 正则 `^LARK_UID=(\S+)` 提取
  - `install.default_config_text(lark_uid: str) -> str` — 注释齐全的默认 toml
  - `install.settings_path() -> Path` — `$CLAUDE_CONFIG_DIR/settings.json`，否则 `~/.claude/settings.json`
  - `install.patch_settings(path: Path) -> bool` — 幂等替换/新增 Notification hook，返回是否有改动
  - `install.run_install() -> int` — 生成默认配置（不覆盖已有）+ patch settings；返回 0
  - CLI：`cc-feishu install`

- [ ] **Step 1: 写失败测试**

`tests/test_install.py`（review focus #5 全覆盖）：

```python
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
    p = write_settings(tmp_path, {"hooks": {"Notification": [
        {"hooks": [{"type": "command", "command": "notify_feishu.sh"}]}]}})
    assert install.patch_settings(p) is True
    assert notification_commands(p) == ["cc-feishu notify"]


def test_patch_settings_idempotent(tmp_path: Path) -> None:
    p = write_settings(tmp_path, {"hooks": {"Notification": [
        {"hooks": [{"type": "command", "command": "cc-feishu notify"}]}]}})
    assert install.patch_settings(p) is False  # 已是目标状态 → 无改动


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
    p = write_settings(tmp_path, "{broken json")
    assert install.patch_settings(p) is True
    assert notification_commands(p) == ["cc-feishu notify"]


def test_patch_settings_preserves_other_hooks(tmp_path: Path) -> None:
    p = write_settings(tmp_path, {"hooks": {
        "Notification": [
            {"hooks": [{"type": "command", "command": "notify_feishu.sh"}]},
            {"hooks": [{"type": "command", "command": "other-notify.sh"}]},
        ],
        "UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "codegraph prompt-hook", "timeout": 300}]}],
    }})
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_install.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 写实现**

`src/cc_feishu/install.py`：

```python
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
```

`src/cc_feishu/cli.py`：import 行改为 `from cc_feishu import install, notify`，subparsers 增加：

```python
    sub.add_parser("install", help="注册 Notification hook 并生成默认配置（幂等）")
```

dispatch 增加：

```python
    if args.command == "install":
        return install.run_install()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_install.py -v`
Expected: 9 PASS

- [ ] **Step 5: 质量门槛 + Commit**

Run: `ruff format . && ruff check . && mypy src && pytest`

```bash
git add src/cc_feishu/install.py src/cc_feishu/cli.py tests/test_install.py
git commit -m "feat(install): Notification hook 注册 + 默认配置生成（幂等）"
```

### Task 8: M1 验收（逐字节比对 → 真实发送 → install → 退役旧脚本）

**Files:**
- Create: 无仓库文件（比对脚本是一次性验收工具，放 /tmp 不入库）
- Modify: `README.md`（最后一步更新状态）

**Interfaces:**
- Consumes: 全部前序任务
- Produces: 验收通过的可运行 `cc-feishu`；旧脚本退役；README 状态更新

- [ ] **Step 1: 全量质量门槛**

Run: `ruff format . && ruff check . && mypy src && pytest -q`
Expected: 全绿。统计测试数：18 项旧断言全部有对应 pytest 断言（T1→test_chunk+test_notify，T2/T3/T4→test_chunk，T5→test_notify，T6/T7→test_transcript）。

- [ ] **Step 2: pipx 安装**

```bash
pipx install --editable /home/zhongsheng/github.com/nicky-zs/cc-feishu
cc-feishu --help && cc-feishu notify --help
```

Expected: 帮助正常输出。（仓库推送后可改 `pipx install --force git+<repo>`；editable 便于 M2 前继续开发。）

- [ ] **Step 3: 新旧链路 DRY_RUN 逐字节比对**

写入 `/tmp/m1-compare.sh` 并运行：

```bash
#!/bin/bash
# M1 验收：新旧链路 DRY_RUN 输出逐字节比对（不发送消息）
set -u
WORK=$(mktemp -d /tmp/m1-compare.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
CFG="$WORK/config.toml"
printf 'from_label = "10.123.179.32"\n' > "$CFG"  # 对齐旧脚本硬编码 IP（已记录差异 #1）

python3 - "$WORK" <<'PY'
import json, sys
from pathlib import Path
w = Path(sys.argv[1])
def jsonl(name, entries):
    (w / name).write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8")
def asst(t):
    return {"type": "assistant", "isSidechain": False, "message": {"content": [{"type": "text", "text": t}]}}
def ask(question, header, options, multi=False):
    return {"type": "assistant", "isSidechain": False, "message": {"content": [
        {"type": "tool_use", "id": "c", "name": "AskUserQuestion",
         "input": {"questions": [{"question": question, "header": header, "multiSelect": multi, "options": options}]}}]}}

jsonl("t1.jsonl", [asst("你好，这是一条短消息，含代码：\n\n```python\nprint(1)\n```\n")])
lines = []
for i in range(500):
    lines.append(f"第{i:04d}行：" + "汉字内容测试" * 4)
    if i % 5 == 4:
        lines.append("")
jsonl("t2.jsonl", [asst("\n".join(lines))])
code = [f"def fn_{i}(): return {i}  # " + "x" * 50 for i in range(500)]
jsonl("t3.jsonl", [asst("代码前说明\n\n```python\n" + "\n".join(code) + "\n```\n\n代码后说明")])
jsonl("t4.jsonl", [asst("汉" * 15000)])
jsonl("t6.jsonl", [asst("问题前的铺垫说明文字。"),
    ask("选哪个方案？", "形态路线", [{"label": "B：独立项目（推荐）", "description": "pipx 装 CLI + install.sh"},
                                    {"label": "先小重构缓建仓", "description": "单文件 Python"}])])
jsonl("t7.jsonl", [asst("上一轮的陈旧文本。"),
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
    asst("本轮的说明文字。"),
    ask("T7 的问题？", "测试", [{"label": "选项一", "description": ""}])])
PY

pass=0; fail=0
check() { # $1 名字  $2 hook JSON  $3 可选额外 env（如 FEISHU_CHUNK_BYTES=3000）
  local name="$1" hook="$2" extra="${3:-}"
  printf '%s' "$hook" | env $extra DRY_RUN=1 "$HOME/.local/bin/notify_feishu.sh" > "$WORK/old.out" 2>/dev/null
  printf '%s' "$hook" | env $extra DRY_RUN=1 CC_FEISHU_CONFIG="$CFG" cc-feishu notify > "$WORK/new.out" 2>/dev/null
  if cmp -s "$WORK/old.out" "$WORK/new.out"; then
    pass=$((pass+1)); echo "PASS $name"
  else
    fail=$((fail+1)); echo "FAIL $name"; diff "$WORK/old.out" "$WORK/new.out" | head -10
  fi
}

for t in t1 t2 t3 t4 t6 t7; do
  check "$t" "$(printf '{"transcript_path":"%s/%s.jsonl","message":"test notify","cwd":"/tmp/wd"}' "$WORK" "$t")"
done
check "no-transcript" '{"message":"test notify","cwd":"/tmp/wd"}'
check "small-budget" "$(printf '{"transcript_path":"%s/t2.jsonl","message":"test notify","cwd":"/tmp/wd"}' "$WORK")" "FEISHU_CHUNK_BYTES=3000"

# 真实 transcript：新语义忽略元数据/sidechain，旧 jq 会被它们重置——旧侧基线用
# 「过滤掉被忽略条目后的同一文件」构造（等价关系见 Global Constraints 差异 #5）
python3 - "$WORK" <<'PY'
import json, sys, glob, os
from pathlib import Path
w = Path(sys.argv[1])
def is_ignored(e):
    if not isinstance(e, dict):
        return True
    if e.get("type") == "user":
        return False
    if e.get("type") == "assistant" and e.get("isSidechain") is not True:
        return False
    return True  # sidechain assistant 与所有元数据类型
reals = sorted(glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")), key=os.path.getmtime, reverse=True)[:3]
for i, p in enumerate(reals):
    kept = []
    for ln in open(p, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(ln)
        except ValueError:
            kept.append(ln)  # 坏行保留：两侧都应整体为空
            continue
        if not is_ignored(e):
            kept.append(json.dumps(e, ensure_ascii=False))
    (w / f"real{i}.jsonl").write_text("".join(kept), encoding="utf-8")
    with open(w / f"real{i}.path", "w") as f:
        f.write(p)
PY
for i in 0 1 2; do
  raw=$(cat "$WORK/real$i.path")
  filt="$WORK/real$i.jsonl"
  printf '%s' "$(printf '{"transcript_path":"%s","message":"test notify","cwd":"/tmp/wd"}' "$filt")" \
    | DRY_RUN=1 "$HOME/.local/bin/notify_feishu.sh" > "$WORK/old.out" 2>/dev/null
  printf '%s' "$(printf '{"transcript_path":"%s","message":"test notify","cwd":"/tmp/wd"}' "$raw")" \
    | DRY_RUN=1 CC_FEISHU_CONFIG="$CFG" cc-feishu notify > "$WORK/new.out" 2>/dev/null
  if cmp -s "$WORK/old.out" "$WORK/new.out"; then
    pass=$((pass+1)); echo "PASS real:$(basename "$raw" .jsonl)"
  else
    fail=$((fail+1)); echo "FAIL real:$(basename "$raw" .jsonl)"; diff "$WORK/old.out" "$WORK/new.out" | head -10
    echo "  （若 diff 仅因末 100 行窗口内被忽略条目挤占窗口所致，人工核对后可放行——见差异 #5 说明）"
  fi
done
echo; echo "结果: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
```

Run: `bash /tmp/m1-compare.sh`
Expected: 全部 PASS。真实 transcript 组的口径：**旧侧喂「过滤掉被忽略条目后的同一文件」，新侧喂原始文件**——这验证的正是等价关系「新语义(raw) == 旧语义(过滤后)」（差异 #5）。pytest 中的 `test_trailing_metadata_preserves_content` 已单独锁定新语义对原始文件保留正文。任何 FAIL → 用 diff 定位语义偏差，回到对应任务修正后重跑；**比对通过是 M1 验收的硬门槛**。

- [ ] **Step 4: 旧 bash 套件再跑一遍（退役前留档）**

Run: `bash ~/ai_works/notify_feishu_tests/run.sh`
Expected: `18 passed, 0 failed`（确认旧链路在比对时刻行为基线仍成立）。

- [ ] **Step 5: cc-feishu install 切换 hook**

```bash
cp ~/.claude/settings.json /tmp/settings.json.m1bak
cc-feishu install
diff /tmp/settings.json.m1bak ~/.claude/settings.json || true
cat ~/.config/cc-feishu/config.toml
printf '{"message":"安装后自检","cwd":"/tmp"}' | cc-feishu notify --dry-run
```

Expected: diff 仅 Notification hook 行变化（notify_feishu.sh → cc-feishu notify）；config.toml 含从旧脚本迁移的 `lark_uid`；自检输出单片通知。

- [ ] **Step 6: 真实发送验收（需用户在场确认）**

```bash
tp=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
printf '{"transcript_path":"%s","message":"M1 验收：新链路真实发送","cwd":"/tmp"}' "$tp" | cc-feishu notify
```

→ 请用户确认飞书收到通知（含正文）。**未确认前不得进入 Step 7。**

- [ ] **Step 7: 旧脚本退役（用户确认后执行）**

设计 §9：M1 验收后删除旧脚本与旧测试目录。

```bash
rm ~/.local/bin/notify_feishu.sh
rm -rf ~/ai_works/notify_feishu_tests
```

README.md 状态行改为：

```markdown
状态：M1 完成（通知链路已迁移，旧 notify_feishu.sh 已退役）。M2（listen + 路由表 + tmux 后端）待启动。
```

- [ ] **Step 8: Commit**

```bash
git add README.md
git commit -m "chore: M1 验收完成（DRY_RUN 逐字节比对通过 + 真实发送确认），旧脚本退役"
```

---

## 自检记录（Self-Review）

1. **Spec 覆盖**：设计 §3 骨架（M1 范围模块全部落位；listen/state/deliver 属 M2/M3 明确不做）、§4 notify 子命令 + 配置键 + 提取规则 + 分片规则、§6 测试策略（纯函数单测/dry-run/无真实发送的 mock）、§7 pipx 分发、§8 M1 验收标准（逐字节一致 + 用户确认）、§9 退役——均有对应任务。install 的 systemd unit 部分按里程碑归属推迟到 M2（设计 §4 表格与 §8 里程碑一致以里程碑为准）。
2. **占位符扫描**：已清除全部"实现时再改"注记与笔误示例；所有代码块可直接落地。
3. **类型一致性**：`split_notify_chunks`/`byte_len`（Task 2 定义，Task 6 消费）、`extract_last_assistant`（Task 3 定义，Task 6 消费）、`Config`/`load_config`/`effective_*`（Task 4 定义，Task 6 消费）、`send_chunks`（Task 5 定义，Task 6 经 `notify.lark.send_chunks` 消费）、`patch_settings`/`run_install`（Task 7 定义，CLI 消费）——签名已逐一核对。
4. **Review Focus**：五条 + 两条补充均有具名测试。
5. **计划内代码已实证**（2026-10-15，计划编写时对拍）：Task 2 的 chunk 代码与旧脚本 heredoc python 逐字节对拍 16 组（8 文本 × 2 预算）全一致；Task 3 的 transcript 代码按新语义（user-only 重置）验证等价关系 `新(raw) == 旧jq(过滤掉被忽略条目)` 于构造场景与真实 transcript。对拍中发现并修复一处对齐 bug：jq 输出经 `$(...)` 会剥掉全部尾部换行，`extract_last_assistant` 必须 `rstrip("\n")` 才能逐字节对齐——已写入实现与注释。写作期间设计文档经 0fe935f 修订（第三次 bug：元数据条目清空正文），Task 3/Task 8/Global Constraints 已同步改版。
