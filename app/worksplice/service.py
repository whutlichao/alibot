"""协作域的应用服务：把 HTTP 人类面的动作翻译成持久化操作。

人类面（浏览器与人自己的脚本）只经这里读写；Agent 的任何动作不经 HTTP（spec #1 §1）。
本票只走 HTTP 面，不含 Agent / 事件流 / 提及解析。
"""

from __future__ import annotations

from typing import Any

from .db import OWNER_ID, Database

UNREAD_BADGE_MIN = 1


class WorkspaceService:
    """工作台读写的窄接口。"""

    def __init__(self, db: Database, member_id: str = OWNER_ID):
        self._db = db
        self._member_id = member_id

    # ─── 空态快照 ─────────────────────────────────────────────────────────
    def bootstrap(self) -> dict[str, Any]:
        """工作台启动时的全量快照。空库返回空频道表、零业务消息、仅 owner 成员行。"""
        channels = self._db.list_channels(self._member_id)
        return {
            "member": self._db.get_member(self._member_id),
            "channels": channels,
            "members": self._db.list_members(),
            "unread_total": sum(channel["unread_count"] for channel in channels),
            "last_seq": max((channel["last_seq"] for channel in channels), default=0),
        }

    # ─── 频道 ─────────────────────────────────────────────────────────────
    def list_channels(self) -> list[dict[str, Any]]:
        return self._db.list_channels(self._member_id)

    def create_channel(
        self, name: str, description: str = "", member_ids: list[str] | None = None
    ) -> dict[str, Any]:
        """创建频道；校验不通过则整体不创建（事务内不留半成品行）。"""
        channel = self._db.create_channel(
            name=name, description=description, member_ids=member_ids, creator_id=self._member_id
        )
        return channel

    def channel_messages(self, channel_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        return self._db.list_messages(channel_id, after_seq=after_seq)

    def channel_members(self, channel_id: str) -> list[dict[str, Any]]:
        return self._db.list_channel_members(channel_id)

    # ─── 消息 ─────────────────────────────────────────────────────────────
    def send_message(self, channel_id: str, body: str) -> dict[str, Any]:
        """人类消息落库；在同一事务内分配频道内单调 seq。

        消息 append-only：本服务不提供编辑或删除消息的方法，HTTP 面也没有对应端点。
        """
        return self._db.append_message(
            channel_id=channel_id, body=body, author_member_id=self._member_id, kind="human"
        )

    # ─── 已读位置（未读的正本） ───────────────────────────────────────────
    def mark_read(self, channel_id: str, seq: int) -> dict[str, Any]:
        """把该频道已读位置单调推进到 ``seq``（同一频道内取 max，跨频道互不影响）。"""
        return self._db.mark_read(channel_id, seq, member_id=self._member_id)
