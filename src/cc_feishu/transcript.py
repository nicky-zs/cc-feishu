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
    """主链 assistant：type == "assistant" 且 isSidechain 不为 true。"""
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
