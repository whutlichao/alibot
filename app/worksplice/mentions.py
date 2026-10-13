"""提及解析：按成员名表把正文里的 ``@名字`` 解析成提及快照（spec #1 §2 / §10）。

三条纪律：

1. **最左最长匹配**：同一位置有多个可匹配的成员名时取最长者（成员 ``小明`` 与 ``小明王``
   并存时，``@小明王`` 命中后者）。扫描从左到右，一次匹配消费掉的字不再回看。
2. **引号形式**：名字含空格时写 ``@"带 空格的名字"``（``“”`` 亦可用），引号内容必须与成员名
   **完全相等**才算提及。
3. **不存在同名成员就不算提及**——两条落实：
   - 无引号形式的候选串必须**恰好等于**某个成员名（不做前缀匹配）。于是 ``@小明丰``
     （没有成员叫这个名字）不会因为前缀是 ``小明`` 而被误判，而 ``@小明王`` 在 ``小明`` 与
     ``小明王`` 并存时命中更长的那个。
   - ``@`` 的**左边界**：前面是 ASCII 字母或数字时不算提及 —— 邮箱式文本
     （``user@example.com``）由此被排除。
     其余位置都可以开始提及，包括紧贴中文的 ``你好@小明``（人类已裁定：只排除邮箱式）。

解析结果的**位置**（``start`` / ``end``，按 Python 字符下标）随消息一起落库：唤醒与前端高亮
都消费这一份快照，不各自实现一遍、也不事后按当前成员名表重新解析（改名不让历史提及漂移）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# 允许留在「名字候选串」里的字符之外的分隔符：命中这些字符即认为候选串到此结束。
# 名字可以是 ``dev-1``、``v2.0`` 这类，所以 ``- . _ +`` 不在此列（它们是词内字符）。
_TOKEN_STOPS = frozenset(" \t\r\n@,，。、;；:：!！?？'\"“”‘’()（）[]【】{}「」<>《》/\\|~#$%^&*=")

# 引号形式：成对的开引号 → 闭引号。
_QUOTE_PAIRS = {'"': '"', "“": "”", "‘": "’"}

_ASCII_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_ASCII_DIGITS = frozenset("0123456789")


def _is_ascii_alnum(char: str) -> bool:
    """是否是 **ASCII** 字母或数字（中文、全角数字不算）。

    边界判据刻意用的是 ASCII 而不是 ``str.isalnum()``：后者对中日韩文字也为真，会把紧贴中文的
    ``你好@小明`` 也排除掉——而人类已裁定只排除邮箱式的那种。
    """
    return len(char) == 1 and (char in _ASCII_LETTERS or char in _ASCII_DIGITS)


def _left_boundary_ok(body: str, index: int) -> bool:
    """``body[index]`` 是 ``@`` 时，判断它左边是否可以开始一个提及。"""
    if index == 0:
        return True
    return not _is_ascii_alnum(body[index - 1])


def _candidate_token(body: str, start: int) -> str:
    """从 ``@`` 之后读一个无引号的候选名字串：遇到空白或分隔符即止。"""
    cursor = start
    while cursor < len(body) and body[cursor] not in _TOKEN_STOPS:
        cursor += 1
    return body[start:cursor]


def parse_mentions(body: str, members: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """把正文解析成提及快照。

    Args:
        body: 消息正文。
        members: 成员名表（每项至少含 ``id`` 与 ``display_name``）。重名时取首个出现的成员，
            顺序由调用方决定（本仓为 ``list_members`` 的稳定顺序），因此结果可复现。

    Returns:
        ``[{"member_id", "display_name", "start", "end"}]``，按出现位置升序。
        ``start`` 是 ``@`` 的下标，``end`` 是最后一个被消费字符的下标 + 1（引号形式含闭引号）。
    """
    text = body or ""
    by_name: dict[str, str] = {}
    for member in members:
        name = str(member.get("display_name") or "")
        member_id = str(member.get("id") or "")
        if name and member_id:
            by_name.setdefault(name, member_id)
    # 最长优先：同一位置先试最长的名字，命中即「最左最长」。
    ordered = sorted(by_name.items(), key=lambda item: (-len(item[0]), item[0]))

    spans: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(text):
        if text[cursor] != "@":
            cursor += 1
            continue
        if not _left_boundary_ok(text, cursor):
            cursor += 1
            continue

        match = _match_quoted(text, cursor, by_name) or _match_bare(text, cursor, ordered)
        if match is None:
            cursor += 1
            continue

        name, member_id, end = match
        spans.append({"member_id": member_id, "display_name": name, "start": cursor, "end": end})
        cursor = end
    return spans


def _match_quoted(text: str, at: int, by_name: Mapping[str, str]) -> tuple[str, str, int] | None:
    """``@"带 空格的名字"`` 形式：引号内容必须与成员名完全相等。"""
    if at + 1 >= len(text):
        return None
    closer = _QUOTE_PAIRS.get(text[at + 1])
    if closer is None:
        return None
    close_at = text.find(closer, at + 2)
    if close_at < 0:
        return None
    name = text[at + 2 : close_at]
    member_id = by_name.get(name)
    if member_id is None:
        return None
    return name, member_id, close_at + 1


def _match_bare(text: str, at: int, ordered: list[tuple[str, str]]) -> tuple[str, str, int] | None:
    """无引号形式：候选串必须**恰好等于**某个成员名（最长的优先）。

    不做前缀匹配，所以 ``@小明丰``（没有成员叫这个名字）不会因为 ``小明`` 是它的前缀而被误判；
    而 ``@小明王`` 在 ``小明`` 与 ``小明王`` 并存时命中更长的那个（最左最长）。
    """
    token = _candidate_token(text, at + 1)
    if not token:
        return None
    for name, member_id in ordered:
        if token == name:
            return name, member_id, at + 1 + len(name)
    return None


def mentioned_member_ids(spans: Iterable[Mapping[str, Any]]) -> list[str]:
    """快照里去重后的被提及成员 id（保持首次出现顺序）。"""
    seen: list[str] = []
    for span in spans:
        member_id = str(span.get("member_id") or "")
        if member_id and member_id not in seen:
            seen.append(member_id)
    return seen
