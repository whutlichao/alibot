"""协作域的应用服务：把 HTTP 人类面的动作翻译成持久化操作，并在消息落库后唤醒成员面。

两面分离（spec #1 §1）：

- **人类面**（浏览器与人自己的脚本）只经这里读写。Agent 没有任何 HTTP 路径可以调用。
- **成员面**（Agent 的一轮）由 :class:`~alibot.orchestration.WakeOrchestrator` 在
  **消息成功落库之后**直接从进程内驱动——唤醒信号只带标识，不带正文。

本模块也是「消息落库 → 推送 → 唤醒」这个顺序的唯一写点：先 ``append_message``（失败就抛，
后续都不执行），再推事件流，最后唤醒。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .agent_seam import (
    LOCAL_PROVIDERS,
    PROVIDERS,
    is_remote_provider,
    model_name_of,
    provider_of,
)
from .db import OWNER_ID, SEARCH_RESULT_LIMIT, Database, DomainError
from .events import EventBus
from .i18n import Locale, join_list, locale_of, render
from .mentions import parse_mentions

if TYPE_CHECKING:
    from .orchestration import WakeOrchestrator

# 会离开本机的数据面（spec #1 §8）：告知文案与接口载荷共用这一份，避免两处漂移。
#
# 这里是**词条键**而不是成品句子：同一组数据面既要出现在告知文案里，也要出现在编排的
# 「为什么这个 Agent 未启用」里（ADR-0009 的第二层要求两者都按**当前语言偏好**渲染），
# 因此取值在调用点按语言取，落点仍然只有这一处。
REMOTE_DATA_FACE_KEYS: tuple[str, ...] = (
    "remote.face.body",
    "remote.face.author",
    "remote.face.channel",
    "remote.face.mention",
)


def remote_data_faces(locale: Locale) -> tuple[str, ...]:
    """会离开本机的数据面（按**语言偏好**渲染；编排的可用性原因也读它，避免两处措辞漂移）。"""
    return tuple(render(key, locale) for key in REMOTE_DATA_FACE_KEYS)


def model_id_error(model_id: str, locale: Locale) -> str | None:
    """模型标识的形状校验（``provider:model``）。返回错误文案（按语言偏好），通过则返回 None。"""
    clean = (model_id or "").strip()
    if not clean:
        return render("error.model_id_empty", locale)
    provider = provider_of(clean)
    if not provider:
        return render("error.model_id_provider_prefix", locale, model_id=clean)
    if provider not in PROVIDERS:
        return render(
            "error.model_id_unknown_provider", locale, provider=provider, providers=", ".join(PROVIDERS)
        )
    if not model_name_of(clean):
        return render("error.model_id_missing_name", locale, model_id=clean)
    return None


class WorkspaceService:
    """工作台读写的窄接口。"""

    def __init__(
        self,
        db: Database,
        member_id: str = OWNER_ID,
        orchestrator: WakeOrchestrator | None = None,
        events: EventBus | None = None,
    ):
        self._db = db
        self._member_id = member_id
        self._orchestrator = orchestrator
        self._events = events

    # ─── 文案的语言偏好（ADR-0009 第二层：读库，不读请求头） ────────────────
    def _locale(self) -> Locale:
        """当前**语言偏好**（库内的工作区设置）；本模块产出的自然语言一律经它渲染。"""
        return locale_of(self._db)

    # ─── 空态快照 ─────────────────────────────────────────────────────────
    def bootstrap(self) -> dict[str, Any]:
        """工作台启动时的全量快照。空库返回空频道表、零业务消息、仅 owner 成员行。"""
        channels = self._db.list_channels(self._member_id)
        dms = self.list_dms()
        return {
            "member": self._db.get_member(self._member_id),
            "channels": channels,
            "dms": dms,
            "members": self._db.list_members(),
            "agents": self.agents(),
            # `unread_total` = **channel 型 + dm 型**之和（决策 G5），由**未读那一条 SQL 形状**直接算，
            # 不再由频道列表相加——否则 dm 型 target 的未读永远进不来（它不在 `channels` 里）。
            # 现有数据只有 channel 型，因此读数与改名前的 `sum(channels[].unread_count)` 逐字相等。
            "unread_total": self._db.unread_total(self._member_id),
            "last_seq": max((channel["last_seq"] for channel in channels), default=0),
            "model_providers": list(PROVIDERS),
            "local_providers": list(LOCAL_PROVIDERS),
            "remote_consents": self._db.list_remote_consents(),
        }

    # ─── 频道 ─────────────────────────────────────────────────────────────
    def list_channels(self) -> list[dict[str, Any]]:
        return self._db.list_channels(self._member_id)

    def list_dms(self) -> list[dict[str, Any]]:
        """工作台左栏私信；对方状态点沿用本机 Agent 的状态事实。"""
        agents = {agent["id"]: agent for agent in self.agents()}
        dms = self._db.list_dms(self._member_id)
        for dm in dms:
            agent = agents.get(dm["agent_id"], {})
            dm["peer"]["status"] = agent.get("status", "offline")
        return dms

    # ─── 工作台搜索 ───────────────────────────────────────────────────────
    def search_messages(
        self, query: str, *, target_id: str | None = None, limit: int = SEARCH_RESULT_LIMIT
    ) -> list[dict[str, Any]]:
        """跨 channel / DM / task target 搜索普通消息；可显式收窄到一个 target。"""
        return self._db.search_messages(query, target_id=target_id, limit=limit)

    def create_dm(self, agent_id: str) -> tuple[dict[str, Any], bool]:
        dm, created = self._db.create_dm(agent_id, owner_id=self._member_id)
        agent = self._agent_view(self._db.get_agent(dm["agent_id"]))
        dm["peer"]["status"] = agent["status"]
        return dm, created

    def dm_messages(self, dm_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        self._db.get_dm(dm_id, self._member_id)
        return self._db.list_messages(dm_id, after_seq=after_seq)

    def send_dm_message(self, dm_id: str, body: str) -> dict[str, Any]:
        """owner 在私信发言：消息成功 append 后唤醒私信成员面与被点名的穿透 Agent。"""
        self._db.get_dm(dm_id, self._member_id)
        return self._send_target_message(dm_id, body)

    def mark_dm_read(self, dm_id: str, seq: int) -> dict[str, Any]:
        self._db.get_dm(dm_id, self._member_id)
        self._db.mark_read(dm_id, seq, member_id=self._member_id)
        return self._db.get_dm(dm_id, self._member_id)

    def create_channel(
        self, name: str, description: str = "", member_ids: list[str] | None = None
    ) -> dict[str, Any]:
        """创建频道；校验不通过则整体不创建（事务内不留半成品行）。"""
        return self._db.create_channel(
            name=name, description=description, member_ids=member_ids, creator_id=self._member_id
        )

    # ─── 任务看板与任务线程 ───────────────────────────────────────────────
    def tasks(self, channel_id: str | None = None) -> list[dict[str, Any]]:
        return self._db.list_tasks(channel_id=channel_id, member_id=self._member_id)

    def create_task(self, channel_id: str, title: str) -> dict[str, Any]:
        task = self._db.create_task(channel_id=channel_id, title=title)
        return {"task": task, "target": self._db.get_target(task["target_id"])}

    def task(self, task_id: str) -> dict[str, Any]:
        task = self._db.get_task(task_id, self._member_id)
        return {"task": task, "target": self._db.get_target(task["target_id"])}

    def task_messages(self, task_id: str, after_seq: int = 0) -> dict[str, Any]:
        task = self._db.get_task(task_id, self._member_id)
        return {
            "task": task,
            "target": self._db.get_target(task["target_id"]),
            "messages": self._db.list_task_messages(task_id, after_seq=after_seq),
        }

    def task_members(self, task_id: str) -> list[dict[str, Any]]:
        task = self._db.get_task(task_id, self._member_id)
        return self._db.list_target_members(task["target_id"])

    def task_budget(self, task_id: str) -> dict[str, Any]:
        task = self._db.get_task(task_id, self._member_id)
        return self._db.budget_state(task["target_id"])

    def mark_task_read(self, task_id: str, seq: int) -> dict[str, Any]:
        task = self._db.get_task(task_id, self._member_id)
        self._db.mark_read(task["target_id"], seq, member_id=self._member_id)
        return self._db.get_task(task_id, self._member_id)

    def update_task_status(self, task_id: str, status: str) -> dict[str, Any]:
        task, message = self._db.update_task_status(task_id, status, actor_member_id=self._member_id)
        if message is not None and self._events is not None:
            self._events.publish(
                task["target_id"], {"type": "message", "seq": message["seq"], "message": message}
            )
        return {"task": task, "message": message}

    def update_task_title(self, task_id: str, title: str) -> dict[str, Any]:
        task, message = self._db.update_task_title(task_id, title)
        if message is not None and self._events is not None:
            self._events.publish(
                task["target_id"], {"type": "message", "seq": message["seq"], "message": message}
            )
        return {"task": task, "message": message}

    def update_task_assignee(self, task_id: str, member_id: str | None) -> dict[str, Any]:
        actor = self._db.get_member(self._member_id)
        if actor["kind"] == "agent" and self._orchestrator is None:
            raise DomainError(
                "validation_failed", render("error.agent_transfer_needs_orchestrator", self._locale())
            )
        task, message = self._db.update_task_assignee(task_id, member_id, actor_member_id=self._member_id)
        if message is not None and self._events is not None:
            self._events.publish(
                task["target_id"], {"type": "message", "seq": message["seq"], "message": message}
            )
        if message is not None and member_id is not None and self._orchestrator is not None:
            if actor["kind"] == "owner":
                self._orchestrator.notify_task_assignment(task["target_id"], message["seq"], member_id)
            else:
                self._orchestrator.notify_task_reassignment(task["target_id"], message["seq"], member_id)
        return {"task": task, "message": message}

    def channel_messages(self, channel_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        self._db.get_channel(channel_id)  # 频道面入口：只认 channel 型 target
        return self._db.list_messages(channel_id, after_seq=after_seq)

    def channel_members(self, channel_id: str) -> list[dict[str, Any]]:
        """该频道的成员表——即它的 channel 型 target 的成员行（同一个 `target_id` 键）。"""
        self._db.get_channel(channel_id)  # 频道面入口：只认 channel 型 target
        return self._db.list_target_members(channel_id)

    def add_channel_member(self, channel_id: str, member_id: str) -> dict[str, Any]:
        """把一个 **Agent 成员**加入频道（人类面动作）；返回是否**新增**与该频道最新成员表。

        语义（本票的裁定）：

        - **幂等**：已在频道里再调不报错、不产生重复行；``joined`` 区分「新增」与「本来就在」
          （它直接取自 ``add_channel_member`` 的返回值——幂等的事实来源是既有写点，不是这里再算一遍）。
        - 频道不存在 → 404（``get_channel`` 早退）。
        - 成员不存在或已删除 / 不是 Agent → ``validation_failed``，**整体不落库**（校验先于写入）。
          错误文案与 ``create_channel`` 的初始成员校验同口径：owner 由建频道自动加入，不经此端点。
        - 只写 ``target_member`` + 补 ``target_read``：两者都由 ``db.add_target_member`` 完成，
          这里不新增 SQL 路径、也不改它的语义。
        - **不写 system 事件消息**：显式加入是人的显式动作，与「穿透加入需要在流里留下一条
          agent 自己不会解释的变化」动机不同（裁定 1）。
        """
        self._db.get_channel(channel_id)  # 404 早退
        locale = self._locale()
        fields: dict[str, str] = {}
        if not (member_id or "").strip():
            fields["member_id"] = render("error.member_id_empty", locale)
        else:
            try:
                member = self._db.get_member(member_id)
            except DomainError:
                fields["member_id"] = render("error.member_unavailable", locale, member_id=member_id)
            else:
                if member.get("deleted_at"):
                    fields["member_id"] = render("error.member_unavailable", locale, member_id=member_id)
                elif member["kind"] != "agent":
                    fields["member_id"] = render("error.member_not_agent", locale, member_id=member_id)
        if fields:
            raise DomainError(
                "validation_failed", render("error.channel_member_join_failed", locale), fields=fields
            )

        joined = self._db.add_target_member(channel_id, member_id)
        return {
            "joined": joined,
            "member": self._db.get_member(member_id),
            "members": self._db.list_target_members(channel_id),
            "channel": self._db.get_channel(channel_id, self._member_id),
        }

    def round_outcomes(self, channel_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        """该频道的轮次结果（只读；首版没有可观测页面）。"""
        self._db.get_channel(channel_id)  # 频道面入口：只认 channel 型 target
        return self._db.list_round_outcomes(channel_id, after_seq=after_seq)

    def channel_budget(self, channel_id: str) -> dict[str, Any]:
        """该频道当前人类世代的有界响应预算（只读、落库的后端事实）。

        ``used``/``remaining`` 读的是 ``target_budget`` 正本，因此后端重启后与压制行为一致。
        预算是 **每 target 一份**（ADR-0004）：channel 型 target 与频道同 id，因此这个只读端点的
        路径与字段名一律不变。
        """
        self._db.get_channel(channel_id)  # 频道面入口：只认 channel 型 target
        return self._db.budget_state(channel_id)

    # ─── 成员面：Agent 创建与配置 ─────────────────────────────────────────
    def agents(self) -> list[dict[str, Any]]:
        """全部 Agent 成员 + 配置 + 由后端事实推导的状态点。"""
        agents = self._db.agent_members()
        return [self._agent_view(agent) for agent in agents]

    def agent(self, member_id: str) -> dict[str, Any]:
        return self._agent_view(self._db.get_agent(member_id))

    def _agent_view(self, agent: dict[str, Any]) -> dict[str, Any]:
        view = dict(agent)
        view["provider"] = provider_of(str(agent.get("model_id") or ""))
        view["is_remote"] = is_remote_provider(view["provider"])
        view["remote_consent"] = self._db.has_remote_consent(view["provider"]) if view["provider"] else False
        if self._orchestrator is not None:
            view["status"] = self._orchestrator.status_of(agent)
            availability = self._orchestrator.availability(agent)
            view["enabled_reason"] = "" if availability.ok else availability.reason
        else:  # pragma: no cover - 服务在没有编排器时也应给出四态里最保守的那一态
            view["status"] = "offline"
            view["enabled_reason"] = render("availability.orchestrator_absent", self._locale())
        # 系统提示是配置数据，不随列表返回全量正文以外的东西；这里原样带出以证明「落库即用户输入」。
        return view

    def create_agent(self, name: str, role: str, model_id: str, system_prompt: str) -> dict[str, Any]:
        """新建 Agent。任一字段不合法则整体不创建，不留半成品成员行。"""
        locale = self._locale()
        error = model_id_error(model_id, locale)
        if error is not None:
            raise DomainError(
                "validation_failed", render("error.agent_invalid", locale), fields={"model_id": error}
            )
        agent = self._db.create_agent(name=name, role=role, model_id=model_id, system_prompt=system_prompt)
        return self._agent_view(agent)

    # ─── 工作区设置（配置类取值；人类面可写，用量类事实不可写） ─────────────
    def settings(self) -> dict[str, Any]:
        """工作区设置（**语言偏好** / **消息密度**）。正本是后端库内的那一行。"""
        return self._db.workspace_settings()

    def update_settings(
        self, locale: str | None = None, message_density: str | None = None
    ) -> dict[str, Any]:
        """写工作区设置（人类面动作）。只写配置类取值：

        - **不落消息**（设置不是对话事实，不污染 append-only 的取证面）；
        - **不唤醒**任何成员；
        - **不消耗预算**（`target_budget.used` 仍只由开世代与投递跳推进，ADR-0007 的边界）。
        """
        return self._db.update_workspace_settings(
            locale=None if locale is None else str(locale).strip(),
            message_density=None if message_density is None else str(message_density).strip(),
        )

    # ─── 远端数据流告知 ──────────────────────────────────────────────────
    def remote_data_flow(self) -> dict[str, Any]:
        """告知文案的数据面：哪些数据会离开本机、发给谁、以及已经确认过哪些提供商。"""
        locale = self._locale()
        faces = remote_data_faces(locale)
        agents = self._db.agent_members()
        destinations: list[str] = []
        for agent in agents:
            provider = provider_of(str(agent.get("model_id") or ""))
            if provider and is_remote_provider(provider) and provider not in destinations:
                destinations.append(provider)
        if not destinations:
            destinations = [p for p in PROVIDERS if is_remote_provider(p)][:1]
        return {
            "data_faces": list(faces),
            "destinations": destinations,
            "consents": self._db.list_remote_consents(),
            "local_providers": list(LOCAL_PROVIDERS),
            # 本机存储不做应用层加密：这条字段是「不得声称 E2EE」的事实来源。
            "end_to_end_encrypted": False,
            "loopback_only": True,
            "statement": render("remote.statement", locale, faces=join_list(faces, locale)),
        }

    def acknowledge_remote_flow(self, provider: str) -> dict[str, Any]:
        """确认一次数据面告知。幂等；不记录也不接收任何凭证。"""
        locale = self._locale()
        clean = (provider or "").strip()
        if clean not in PROVIDERS:
            raise DomainError(
                "validation_failed",
                render("error.unknown_provider", locale),
                fields={"provider": render("error.field_choices", locale, values=", ".join(PROVIDERS))},
            )
        if not is_remote_provider(clean):
            raise DomainError(
                "validation_failed",
                render("error.provider_not_remote", locale, provider=clean),
                fields={"provider": render("error.provider_consent_not_needed", locale)},
            )
        record = self._db.record_remote_consent(clean)
        return {
            "consent": record,
            "consents": self._db.list_remote_consents(),
            "agents": self.agents(),
        }

    # ─── 消息 ─────────────────────────────────────────────────────────────
    def send_message(self, channel_id: str, body: str, task_ref: str | None = None) -> dict[str, Any]:
        """频道中的人类消息；task_ref 若存在，数据库要求其与频道 target 同域。"""
        self._db.get_channel(channel_id)  # 频道面入口只认 channel 型 target
        return self._send_target_message(channel_id, body, task_ref)

    def send_task_message(self, task_id: str, body: str, task_ref: str | None = None) -> dict[str, Any]:
        """任务线程消息落在任务自己的 target，并沿用同一落库、事件流与唤醒路径。"""
        task = self._db.get_task(task_id, self._member_id)
        return self._send_target_message(task["target_id"], body, task_ref)

    def _send_target_message(self, target_id: str, body: str, task_ref: str | None = None) -> dict[str, Any]:
        """人类消息落库 → 推送事件流 → 唤醒成员面的 Agent。

        顺序是契约的一部分：``append_message`` 抛错时后面两步都不执行（落库失败不唤醒）。
        提及解析结果作为**快照**随消息一起落库，唤醒与前端高亮都消费这一份。
        """
        members = self._db.list_members()
        snapshot = parse_mentions(body, members)
        message = self._db.append_message(
            target_id=target_id,
            body=body,
            author_member_id=self._member_id,
            kind="human",
            mention_snapshot=snapshot,
            task_ref=task_ref,
        )
        if self._events is not None:
            self._events.publish(target_id, {"type": "message", "seq": message["seq"], "message": message})
        if self._orchestrator is not None:
            self._orchestrator.notify(target_id, message["seq"])
        return message

    def member_statuses(self) -> dict[str, Any]:
        """全部 Agent 的状态点（四态，只表达后端事实）。"""
        if self._orchestrator is None:
            return {"statuses": {}}
        return {"statuses": self._orchestrator.member_statuses()}

    def member_status(self, member_id: str) -> dict[str, Any]:
        agent = self._db.get_agent(member_id)
        if self._orchestrator is None:
            return {
                "member_id": member_id,
                "status": "offline",
                "reason": render("availability.orchestrator_absent", self._locale()),
            }
        availability = self._orchestrator.availability(agent)
        return {
            "member_id": member_id,
            "status": self._orchestrator.status_of(agent),
            "reason": "" if availability.ok else availability.reason,
        }

    # ─── 已读位置（未读的正本） ───────────────────────────────────────────
    def mark_read(self, channel_id: str, seq: int) -> dict[str, Any]:
        """把该频道已读位置单调推进到 ``seq``（同一 target 内取 max，跨 target 互不影响）。

        已读位置的正本已泛化为 ``target_read``；这个方法是 **channel 面**的入口，因此仍旧只接受
        频道（非 channel 型 target 不在此命名空间里）。

        响应体是**外部契约**（票面 Constraint：响应字段名一律不变），所以由这里用 ``get_channel``
        组出——``db.mark_read`` 只推进位置、返回 None，不把「channel 型的响应长什么样」写进 db 层的
        分支里。
        """
        self._db.get_channel(channel_id)  # 频道面入口：只认 channel 型 target
        self._db.mark_read(channel_id, seq, member_id=self._member_id)
        return self._db.get_channel(channel_id, self._member_id)
