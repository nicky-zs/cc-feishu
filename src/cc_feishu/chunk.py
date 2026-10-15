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


def _hard_split(line: str, room: int, prefix: str, hard_budget: int) -> list[str] | None:
    """超长行按 UTF-8 字符边界硬切：首段进 room 字节，续段补 prefix。

    续段预算用 hard_budget - 32：旧脚本的续段 room 读模块级全局 budget（原始
    预算），不随 chunk_text 本次调用的（可能已缩减的）budget 变化，移植须复刻。
    返回 None 表示预算小到放不下一个字符，由调用方兜底。
    """
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
        room = hard_budget - 32  # 续段预算：留出 prefix、换行、围栏重开头和片尾闭合的余量
    piece = data.decode("utf-8", "replace")
    pieces.append(piece if first else prefix + piece)
    return pieces


def chunk_text(text: str, budget: int, *, hard_budget: int | None = None) -> list[str]:
    """尽量切在空行/换行处；切点落在代码围栏中间时片尾补闭合、下片重开。

    hard_budget 缺省为 budget；多片重切时调用方传原始 budget（见 split_notify_chunks）。
    """
    if hard_budget is None:
        hard_budget = budget
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
        pieces = _hard_split(line, budget - cur_b - reserve - 1, prefix, hard_budget)
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
    """分片并给第 2 片起加「（续 i/n）」标记：多片时预留标记空间后重切。"""
    chunks = chunk_text(full, budget)
    if len(chunks) > 1:
        # 预留「（续 i/n）」标记的空间后重切，确保加标记后仍不超预算；
        # hard_budget 传原始 budget：旧脚本续段 room 读全局（原始）预算，不随 -64 缩减
        chunks = chunk_text(full, budget - 64, hard_budget=budget)
        chunks[1:] = [f"（续 {i + 2}/{len(chunks)}）\n\n" + c for i, c in enumerate(chunks[1:])]
    return chunks
