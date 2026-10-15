import json
from pathlib import Path
from typing import Any

from cc_feishu import transcript


def assistant_entry(text: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "isSidechain": False,
            "message": {"content": [{"type": "text", "text": text}]},
        },
        ensure_ascii=False,
    )


def ask_entry(questions: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "isSidechain": False,
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_x",
                        "name": "AskUserQuestion",
                        "input": {"questions": questions},
                    }
                ]
            },
        },
        ensure_ascii=False,
    )


def user_entry() -> str:
    return json.dumps(
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}
    )


def sidechain_entry(text: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "isSidechain": True,
            "message": {"content": [{"type": "text", "text": text}]},
        },
        ensure_ascii=False,
    )


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
    p.write_text(
        assistant_entry("完整行") + '\n{"type":"assistant","isSidechain":false,', encoding="utf-8"
    )
    assert transcript.extract_last_assistant(p) == ""


def test_plain_string_content(tmp_path: Path) -> None:
    # 旧版 content 为字符串的条目（老格式兼容）
    p = tmp_path / "t.jsonl"
    p.write_text(
        json.dumps(
            {"type": "assistant", "isSidechain": False, "message": {"content": "字符串正文"}},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    assert transcript.extract_last_assistant(p) == "字符串正文"


def test_ask_user_question(tmp_path: Path) -> None:  # T6 场景（4 断言）
    p = write(
        tmp_path,
        [
            assistant_entry("问题前的铺垫说明文字。"),
            ask_entry(
                [
                    {
                        "question": "选哪个方案？",
                        "header": "形态路线",
                        "multiSelect": False,
                        "options": [
                            {
                                "label": "B：独立项目（推荐）",
                                "description": "pipx 装 CLI + install.sh",
                            },
                            {"label": "先小重构缓建仓", "description": "单文件 Python"},
                        ],
                    }
                ]
            ),
        ],
    )
    out = transcript.extract_last_assistant(p)
    assert "**形态路线** 选哪个方案？" in out  # T6 问题与标题
    assert "- B：独立项目（推荐）：pipx 装 CLI + install.sh" in out  # T6 选项含描述
    assert "- 先小重构缓建仓：单文件 Python" in out  # T6 第二个选项
    assert "问题前的铺垫说明文字" in out  # T6 铺垫与问题结合呈现


def test_multiselect_marker(tmp_path: Path) -> None:
    p = write(
        tmp_path,
        [
            ask_entry([{"question": "Q?", "multiSelect": True, "options": [{"label": "a"}]}]),
        ],
    )
    assert "（可多选）" in transcript.extract_last_assistant(p)


def test_boundary_reset(tmp_path: Path) -> None:  # T7 场景（2 断言）
    p = write(
        tmp_path,
        [
            assistant_entry("上一轮的陈旧文本。"),
            user_entry(),
            assistant_entry("本轮的说明文字。"),
            ask_entry(
                [
                    {
                        "question": "T7 的问题？",
                        "header": "测试",
                        "multiSelect": False,
                        "options": [{"label": "选项一", "description": ""}],
                    }
                ]
            ),
        ],
    )
    out = transcript.extract_last_assistant(p)
    assert "本轮的说明文字" in out and "**测试** T7 的问题？" in out  # T7 本轮文本+问题都在
    assert "上一轮的陈旧文本" not in out  # T7 旧轮文本被 user 边界重置


def test_trailing_metadata_preserves_content(tmp_path: Path) -> None:
    # 第三次线上 bug（设计 0fe935f）：turn 结束后追加的元数据不得清空正文
    p = write(
        tmp_path,
        [
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
        ],
    )
    assert transcript.extract_last_assistant(p) == "本轮生成的主要内容。"


def test_metadata_between_turns_same_as_old(tmp_path: Path) -> None:
    # 元数据夹在两轮之间且随后有 user 边界：新旧语义等价（都只剩本轮）
    p = write(
        tmp_path,
        [
            assistant_entry("上一轮内容"),
            meta_entry("last-prompt"),
            meta_entry("ai-title"),
            user_entry(),
            assistant_entry("本轮内容"),
            meta_entry("mode"),
        ],
    )
    assert transcript.extract_last_assistant(p) == "本轮内容"


def test_only_metadata_returns_empty(tmp_path: Path) -> None:
    # 尾部 100 行只有元数据（真实 case）：发基础通知
    p = write(tmp_path, [meta_entry("last-prompt"), meta_entry("mode"), meta_entry("system")])
    assert transcript.extract_last_assistant(p) == ""


def test_option_description_empty_vs_missing(tmp_path: Path) -> None:
    # jq 真值语义：description="" 也是真 → 带冒号；缺失 → 不带
    p = write(
        tmp_path,
        [
            ask_entry(
                [
                    {
                        "question": "Q?",
                        "header": "H",
                        "multiSelect": False,
                        "options": [{"label": "选项一", "description": ""}, {"label": "选项二"}],
                    }
                ]
            ),
        ],
    )
    out = transcript.extract_last_assistant(p)
    assert "- 选项一：" in out
    assert "- 选项二" in out and "- 选项二：" not in out


def test_sidechain_assistant_ignored(tmp_path: Path) -> None:
    # sidechain：内容不进通知、也不构成重置边界（本计划决定，见语义表）
    p = write(
        tmp_path,
        [
            assistant_entry("主链前文"),
            sidechain_entry("侧链内容"),
            assistant_entry("主链后文"),
        ],
    )
    assert transcript.extract_last_assistant(p) == "主链前文\n\n主链后文"
    p = write(tmp_path, [sidechain_entry("只有侧链")])
    assert transcript.extract_last_assistant(p) == ""


def test_empty_assistant_entry_does_not_reset(tmp_path: Path) -> None:
    # 无可用内容的 assistant 条目：过滤但不重置（jq map(select(length>0))）
    p = write(
        tmp_path,
        [
            assistant_entry("前文"),
            json.dumps(
                {
                    "type": "assistant",
                    "isSidechain": False,
                    "message": {"content": [{"type": "thinking", "thinking": "…"}]},
                }
            ),
            assistant_entry("后文"),
        ],
    )
    assert transcript.extract_last_assistant(p) == "前文\n\n后文"


def test_tail_limit_100_lines(tmp_path: Path) -> None:
    lines = [assistant_entry(f"行{i:03d}") for i in range(150)]
    out = transcript.extract_last_assistant(write(tmp_path, lines))
    assert "行000" not in out and "行049" not in out  # 末 100 行之外的被丢弃
    assert "行050" in out and "行149" in out
