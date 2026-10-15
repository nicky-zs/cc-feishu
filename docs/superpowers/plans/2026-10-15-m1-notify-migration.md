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
- M1 行为不变：新旧链路 DRY_RUN 输出**逐字节一致**。仅允许四个明确记录的差异：
  1. `from:` 行默认用 hostname（旧脚本硬编码 `10.123.179.32`；比对验收时用 `from_label` 配置对齐）
  2. hook stdin 非法 JSON 时降级为基础通知并退出 0（旧链路 python 直接崩）
  3. `FEISHU_CHUNK_BYTES` 非整数值时忽略（旧链路崩溃）
  4. transcript jsonl 中的空行跳过而非毒化整体结果（旧链路 jq 遇空行报错、只发基础通知）
- 通知是 best-effort：发送失败只记 stderr，**hook 退出码恒 0**
- 配置键名与默认值 verbatim 依设计 §4：`lark_uid`（默认 `""`）、`chunk_bytes = 12000`、`from_label`（默认 `""`）、`delivery = "tmux"`、`resume_prefix = "/go"`、`routing_ttl_hours = 24`；路径 `~/.config/cc-feishu/config.toml`
- 环境变量：`CC_FEISHU_CONFIG` 覆盖配置路径（测试用）；`FEISHU_CHUNK_BYTES` 覆盖单片预算（与旧脚本同名同义）；`DRY_RUN=1` 等价 `--dry-run`；`CLAUDE_CONFIG_DIR` 覆盖 settings.json 位置（install 用）
- 直接在 main 分支开发（新仓库，M1 即首个功能，无需 worktree）
- lark-cli 发送命令 verbatim：`lark-cli im +messages-send --as bot --user-id <uid> --markdown <chunk>`；片间 sleep 0.3s；单片失败只记 stderr 前 500 字符
- 迁移源（只读参考，勿改动）：`~/.local/bin/notify_feishu.sh`、`~/ai_works/notify_feishu_tests/run.sh`

## Review Focus

以下五类输入是设计文档未显式覆盖、但最可能坑到使用者的场景（已逐条落实到所属任务的测试）：

1. **transcript 尾部半行**（Claude Code 正在写入时触发 hook）：旧链路 jq 全有或全无——任一行坏则整体返回空、发基础通知，绝不能输出半截内容 → Task 3 `test_malformed_line_returns_empty` + `test_partial_content_never_leaks`
2. **AskUserQuestion 选项 description 空串 vs 缺失**（jq 真值语义：空串也是真）：`""` 要产出 `"- 选项一："`（带冒号），缺失则不带 → Task 3 `test_option_description_empty_vs_missing`
3. **hook stdin 非法 JSON**：降级为基础通知、退出 0，不能崩（已记录的与旧链路差异）→ Task 6 `test_cli_invalid_stdin_json_degrades`
4. **极小/垃圾 chunk_bytes**：floor 到 1000；预算小到硬切放不下一个字符时兜底允许单片略超、不崩 → Task 2 `test_hard_split_fallback` + Task 4 `test_chunk_bytes_floor`
5. **settings.json 缺失/损坏/Notification 里并存其他 hook**：install 不丢数据、不重复注册、幂等 → Task 7 `test_patch_settings_creates_missing` / `test_patch_settings_malformed` / `test_patch_settings_preserves_other_hooks`

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


def test_hard_split_fallback() -> None:
    # 预算小到硬切放不下一个字符：兜底单片略超也不崩（review focus #4）
    text = "汉" * 50
    chunks = chunk.chunk_text(quote(text), 200)
    assert len(chunks) >= 1
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

**jq 语义对照**（迁移必须逐条对齐，jq 真值规则：只有 `false`/`null` 为假，`""` 为真）：

| 旧 jq 行为 | Python 迁移 |
|---|---|
| `reduce`：主链 assistant（`type=="assistant"` 且 `isSidechain != true`）追加，**其余任何条目（含 sidechain assistant）重置** | `if _is_main_assistant(e): … else: texts = []` |
| `map(select(length>0))`：空内容条目被过滤但**不重置**，也不产生分隔符 | `if t: texts.append(t)` |
| `if $x.header`：`""` 为真 → `"** ** "` | `isinstance(header, str)`（不做真值判断） |
| `if .description`：`""` 为真 → `"："` 结尾 | `isinstance(desc, str)` → 带 `"："`；键缺失 → 不带 |
| jq 解析错误（坏行）→ 整体输出空 | `except ValueError: return ""` |
| blocks 中非 text/AskUserQuestion → `empty`（不进列表） | 只 append 匹配块 |

- [ ] **Step 1: 写失败测试**

`tests/test_transcript.py`（T6/T7 断言的函数级迁移 + review focus #1/#2）：

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
    # 半行（Claude 正在写入时触发 hook）：join 前被截断的内容不能泄漏（review focus #1）
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
    assert "上一轮的陈旧文本" not in out  # T7 旧轮文本被边界重置


def test_option_description_empty_vs_missing(tmp_path: Path) -> None:
    # jq 真值语义：description="" 也是真 → 带冒号；缺失 → 不带（review focus #2）
    p = write(tmp_path, [
        ask_entry([{"question": "Q?", "header": "H", "multiSelect": False,
                    "options": [{"label": "选项一", "description": ""},
                                {"label": "选项二"}]}]),
    ])
    out = transcript.extract_last_assistant(p)
    assert "- 选项一：" in out
    assert "- 选项二" in out and "- 选项二：" not in out


def test_sidechain_assistant_resets(tmp_path: Path) -> None:
    # sidechain assistant 也在 else 分支：参与重置且自身不出现
    p = write(tmp_path, [
        assistant_entry("主链前文"),
        sidechain_entry("侧链内容"),
        assistant_entry("主链后文"),
    ])
    out = transcript.extract_last_assistant(p)
    assert out == "主链后文"


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

`src/cc_feishu/transcript.py`（自旧脚本 jq 程序逐语义迁移）：

```python
"""transcript jsonl 解析 → 最后一段连续 assistant 内容。

两次线上 bug 的修复结论固化于此：
1. AskUserQuestion 的问题在 tool_use 块里——漏掉则通知只有铺垫文本
2. 提问时需携带上文——只取 last 条目则丢失上下文
"""

import json
from pathlib import Path
from typing import Any

TAIL_LINES = 100  # 与旧脚本 tail -n 100 等价


def _is_main_assistant(e: object) -> bool:
    """主链 assistant：type == "assistant" 且 isSidechain 不为 true（jq: $e.isSidechain != true）。"""
    return isinstance(e, dict) and e.get("type") == "assistant" and e.get("isSidechain") is not True


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

    任一行坏 JSON → 返回 ""（与旧 jq 链路一致：全有或全无，绝不出半截内容）。
    空行跳过（旧链路遇空行整体报错，此处更稳；已记录的无害差异）。
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
        else:
            texts = []  # user/tool_result/sidechain 边界 → 重置
    return "\n\n".join(texts)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_transcript.py -v`
Expected: 13 PASS

- [ ] **Step 5: 质量门槛 + Commit**

Run: `ruff format . && ruff check . && mypy src && pytest`

```bash
git add src/cc_feishu/transcript.py tests/test_transcript.py
git commit -m "feat(transcript): jsonl 提取最后连续 assistant 段（T6/T7 断言迁入）"
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
