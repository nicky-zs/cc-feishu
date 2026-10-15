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
        assert len(fences) % 2 == 0, f"chunk {i + 1}: unbalanced fences ({len(fences)})"


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


def test_hard_split_continuation_uses_original_budget() -> None:
    # 旧脚本 hard_split 的续段预算 room = budget - 32 读的是模块级全局（首次的原始
    # budget），重切 pass 传 budget-64 不改变续段 room——移植必须复刻，否则 T4 逐字
    # 节对拍失败（2026-10-15 M1 验收实证：旧 [48,11933,11969,11969,9137]）。期望值
    # 取自旧脚本 heredoc python 原样执行的输出。
    full = build_full("汉" * 15000)
    rechunked = chunk.chunk_text(full, 11936, hard_budget=12000)  # 重切 pass 实参
    naive = chunk.chunk_text(full, 11936)  # 续段预算跟随本参（错误语义，作对照）
    assert [chunk.byte_len(c) for c in rechunked] == [48, 11933, 11969, 11969, 9137]
    assert [chunk.byte_len(c) for c in naive] == [48, 11933, 11906, 11906, 9263]
    # 端到端：split_notify_chunks 含「（续 i/n）」标记后的整链路尺寸
    chunks = chunk.split_notify_chunks(full, 12000)
    assert [chunk.byte_len(c) for c in chunks] == [48, 11948, 11984, 11984, 9152]
