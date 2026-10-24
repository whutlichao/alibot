"""身份句：成员显示名 → 一句写进 system prompt 的话（ADR-0033）。

显示名（``member.display_name``）是**配置数据**，此前只出现在消息作者名与 ``TeamAssign`` 的工具描述
里，system prompt 里没有一句说「你叫什么」，模型只能从历史 assistant 消息的 ``name`` 字段间接推断。
本模块是这句身份说明的**唯一来源**：三处拼装点（``agent_seam._system_prompt`` /
``agent_seam._team_member_prompt`` / ``sop.sop_agent_prompt``）都从这里取，不各写一份字符串。

两个既有口径不变：

- **D10**：框架内部名仍是 ``m-<member_id>``——它是给事件归属用的事实，不是给人读的名字，**不进**
  prompt；身份句只用显示名。
- **不声明唯一性**：成员显示名允许重复（``CONTEXT.md`` 的**成员**词条口径），所以措辞是「别人这样
  称呼你」，不是「你是唯一的」。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["identity_line"]


def identity_line(member: Mapping[str, Any]) -> str | None:
    """成员行 → 一句身份说明；显示名为空 / 全空白时返回 None（该段缺席）。

    空显示名不写空标题、不写占位：这一段要么是一句可读的事实，要么不存在。措辞硬编码中文，与既有
    ``你的角色：`` 等 prompt 文案同口径，不进 i18n。
    """
    display_name = str(member.get("display_name") or "").strip()
    if not display_name:
        return None
    return f"你在工作台里的名字是「{display_name}」。成员与人类都用这个名字称呼你。"
