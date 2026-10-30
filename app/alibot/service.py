"""协作域的应用服务：把 HTTP 人类面的动作翻译成持久化操作，并在消息落库后唤醒成员面。

两面分离（spec #1 §1）：

- **人类面**（浏览器与人自己的脚本）只经这里读写。Agent 没有任何 HTTP 路径可以调用。
- **成员面**（Agent 的一轮）由 :class:`~alibot.orchestration.WakeOrchestrator` 在
  **消息成功落库之后**直接从进程内驱动——唤醒信号只带标识，不带正文。

本模块也是「消息落库 → 推送 → 唤醒」这个顺序的唯一写点：先 ``append_message``（失败就抛，
后续都不执行），再推事件流，最后唤醒。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .agent_seam import (
    LOCAL_PROVIDERS,
    PROVIDERS,
    is_remote_provider,
    model_name_of,
    provider_of,
)
from .db import (
    _UNSET,
    ALLOWED_ATTACHMENT_MIME_TYPES,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_NAME_LENGTH,
    OWNER_ID,
    PLAN_RUN_RETENTION,
    SEARCH_RESULT_LIMIT,
    Database,
    DomainError,
    NotFound,
    SearchFacet,
    clean_message_body,
)
from .events import EventBus
from .i18n import Locale, join_list, locale_of, render
from .mcp import (
    MCP_DECLARATION_ERROR_KEYS,
    MEMBER_MCP_FILENAME,
    REMOTE_MCP_TRANSPORTS,
    InvalidMCPDeclaration,
    InvalidMCPDocument,
    MemberMCPDocument,
    clear_runtime_for_member,
    load_member_mcp_declarations,
    mcp_document_entry_count,
    member_mcp_server_id,
    normalize_mcp_declaration,
    runtime_status,
    split_member_mcp_server_id,
    validate_mcp_document,
    write_mcp_document_atomic,
)
from .memory import memory_workspace_dir
from .mentions import parse_mentions
from .team import TEAM_SCOPE
from .web import (
    SEARCH_BACKENDS,
    SEARCH_PROVIDERS,
    ProviderResolutionError,
    describe_search_provider,
    web_env,
)
from .workspace import (
    AGENTS_DIR,
    WorkspaceConfigError,
    clear_docker_failure,
    global_home_root,
    global_member_home,
    resolve_global_read_path,
    resolve_global_write_path,
    safe_partition_id,
)

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .orchestration import WakeOrchestrator
    from .sop import SOPRunner

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
        sops: SOPRunner | None = None,
        *,
        global_home: str | None = None,
    ):
        self._db = db
        self._member_id = member_id
        self._orchestrator = orchestrator
        self._events = events
        self._sops = sops
        # 全局层的根（#230 / ADR-0031）：只看 HOME，不随 --data-dir / ALIBOT_DATA_DIR 移动。
        # 未注入时在读路径现取 ``global_home_root()``，于是测试改 HOME 即可指向临时全局根。
        self._global_home_override = global_home

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

    # ─── 频道归档与删除（#342 / ADR-0040：两档软删除） ─────────────────────
    def archive_channel(self, channel_id: str) -> dict[str, Any]:
        """把频道移出左栏活跃段，返回该频道**这一行**的最新读数。

        **两个档位之一**：归档可恢复、只读、仍可读历史、仍可被搜索；删除（:meth:`delete_channel`）
        是终态。两者都是**配置类**写路径，与 ``DELETE /api/agents/{id}`` 同一纪律：不落消息、
        不唤醒、不消耗预算、不开世代。**幂等**：已归档再调 ⇒ 200 且时间戳不刷新。
        """
        return self._db.archive_channel(channel_id, self._member_id)

    def unarchive_channel(self, channel_id: str) -> dict[str, Any]:
        """把频道放回活跃段（幂等）。已删除的频道 ⇒ 404——删除是终态，没有恢复这条路。"""
        return self._db.unarchive_channel(channel_id, self._member_id)

    def delete_channel(self, channel_id: str) -> str:
        """软删除一个频道（终态），返回被删掉的 id。**不幂等**：二次调用 ⇒ 404。

        只写 ``channel.deleted_at``：频道行、影子行、消息、成员关系、任务与预算**全部保留**，
        读路径按这一列过滤（ADR-0040 §一）。因此本票不新增任何修改或删除消息的路径——
        消息 append-only（ADR-0001）不受影响。
        """
        return self._db.delete_channel(channel_id)

    def _require_writable_channel(self, channel_id: str) -> dict[str, Any]:
        """**「已归档 ⇒ 频道面只读」的单一判定落点**（#342 / ADR-0040 §二）。

        读一次频道（顺带继承「已删除 ⇒ 404」），归档了就抛 ``validation_failed``。五个既有
        人类面写入口都调它：频道消息 / 频道配置 / 回应预算上界 / 加入成员 / 新建任务。

        **刻意不在它上面的路径**（写进 ADR §二，不是缺陷）：任务面（``send_task_message`` /
        ``update_task*``）、标读、``GET`` 系列、SSE、提醒到点落 ``system`` 消息——那些要么属于
        任务自己的生命周期，要么是只读，要么是后端系统事实，都不是「频道这个对话面的人类写入」。
        """
        channel = self._db.get_channel(channel_id, self._member_id)
        if channel.get("archived_at"):
            raise DomainError(
                "validation_failed",
                self._locale_copy("error.channel_archived_read_only", name=channel["name"]),
                fields={"channel_id": self._locale_copy("error.field.channel_archived_read_only")},
            )
        return channel

    def list_dms(self) -> list[dict[str, Any]]:
        """工作台左栏私信；对方状态点沿用本机 Agent 的状态事实。"""
        agents = {agent["id"]: agent for agent in self.agents()}
        dms = self._db.list_dms(self._member_id)
        for dm in dms:
            agent = agents.get(dm["agent_id"], {})
            dm["peer"]["status"] = agent.get("status", "offline")
        return dms

    # ─── 工作台搜索 ───────────────────────────────────────────────────────
    def search(
        self,
        query: str,
        *,
        facet: SearchFacet = "all",
        target_id: str | None = None,
        limit: int = SEARCH_RESULT_LIMIT,
    ) -> list[dict[str, Any]]:
        """跨 channel / DM / task target 搜三类命中（消息 / 任务 / Agent，ADR-0013）。

        命中判定与高亮区间都由库层产生，这里只补一件库层拿不到的事实：Agent 命中的**状态点**——
        它由**编排**按后端事实推导（`_agent_view` 与成员面同一条口径），库层不重复推导。
        """
        hits = self._db.search(query, facet=facet, target_id=target_id, limit=limit)
        agent_hits = [hit for hit in hits if hit["kind"] == "agent"]
        if agent_hits:
            statuses = {agent["id"]: self._agent_view(agent)["status"] for agent in self._db.agent_members()}
            for hit in agent_hits:
                hit["agent"]["status"] = statuses[hit["agent"]["id"]]
        return hits

    def create_dm(self, agent_id: str) -> tuple[dict[str, Any], bool]:
        dm, created = self._db.create_dm(agent_id, owner_id=self._member_id)
        agent = self._agent_view(self._db.get_agent(dm["agent_id"]))
        dm["peer"]["status"] = agent["status"]
        return dm, created

    def dm_messages(self, dm_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        self._db.get_dm(dm_id, self._member_id)
        return self._db.list_messages(dm_id, after_seq=after_seq)

    def send_dm_message(self, dm_id: str, body: str, attachment_ids: Sequence[str] = ()) -> dict[str, Any]:
        """owner 在私信发言：消息成功 append 后唤醒私信成员面与被点名的穿透 Agent。"""
        self._db.get_dm(dm_id, self._member_id)
        return self._send_target_message(dm_id, body, None, attachment_ids)

    # ─── 附件（两步上传：先拿标识、随消息绑定；ADR-0014）──────────────────
    def upload_attachment(self, name: str, mime_type: str, data: bytes) -> dict[str, Any]:
        """人类面第一步：把字节与元数据落成一个**未绑定的草稿**，返回标识与可读元数据。

        大小与类型在**这里**拒绝（人类面才有可读原因与词条），错误体沿用既有的
        ``code`` + ``fields`` 形状。校验**先于**任何写入：被拒的请求既不留库内行、也不留孤儿字节。
        未绑定的草稿允许存在（它是**未发送的上传**），但读取面一律不暴露它。
        """
        locale = self._locale()
        # 文件名只作**元数据**与下载时的建议名：剥掉任何目录成分。字节路径由附件标识拼出，
        # 永不使用这个名字，因此这里不是安全闸门——但它保证下载名与显示名不会带上一段路径。
        clean_name = (name or "").strip().replace("\\", "/").rsplit("/", 1)[-1].strip()
        # 声明类型可能带参数（`text/plain; charset=utf-8`）：只按 MIME 本体判定，不按参数。
        clean_mime = (mime_type or "").split(";")[0].strip().lower()
        fields: dict[str, str] = {}
        if not clean_name:
            fields["name"] = render("error.attachment_name_empty", locale)
        elif len(clean_name) > MAX_ATTACHMENT_NAME_LENGTH:
            fields["name"] = render(
                "error.attachment_name_too_long", locale, limit=MAX_ATTACHMENT_NAME_LENGTH
            )
        if not data:
            fields["file"] = render("error.attachment_empty", locale)
        elif len(data) > MAX_ATTACHMENT_BYTES:
            fields["file"] = render("error.attachment_too_large", locale, limit=MAX_ATTACHMENT_BYTES)
        elif clean_mime not in ALLOWED_ATTACHMENT_MIME_TYPES:
            fields["file"] = render(
                "error.attachment_type_not_allowed",
                locale,
                mime=clean_mime or "application/octet-stream",
                types=join_list(ALLOWED_ATTACHMENT_MIME_TYPES, locale),
            )
        if fields:
            raise DomainError("validation_failed", render("error.attachment_rejected", locale), fields=fields)
        return self._db.store_attachment(original_name=clean_name, mime_type=clean_mime, data=data)

    def attachment_for_download(self, attachment_id: str) -> tuple[dict[str, Any], Path]:
        """人类面第三步的读取：已绑定附件的内容描述（元数据 + 字节路径）。

        草稿（还没随消息发送的上传）在这里就拿不到：读取面不暴露它，内容端点回 404。
        """
        return self._db.attachment_for_download(attachment_id)

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

    # ─── 频道配置（team / workspace；规格 §4.4） ──────────────────────────
    def channel_config(self, channel_id: str) -> dict[str, Any]:
        """频道配置读数（含队长的可读可用性）。

        读的是库内正本（``channel.mode`` / ``team_leader_member_id`` / workspace 三列）；队长的
        ``enabled`` / 凭证 / 成员关系由**编排**按后端事实推导，于是设置页看到的原因与真正唤醒
        裁决读的是同一份事实（D18 的「配置无效」不是另一套判断）。
        """
        self._db.get_channel(channel_id)  # 频道面入口：只认 channel 型 target
        config = self._db.get_channel_config(channel_id)
        return self._channel_config_view(config)

    def update_channel_config(self, channel_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """写频道配置（人类面动作）：**任一字段不合法整体拒绝**。

        它是配置写路径，与 ``PATCH /api/agents/{id}`` 同一纪律：**不落消息、不唤醒、不消耗预算、
        不开世代**。``mode=team`` 只在读路径生效（覆盖该频道 target 与全部 task target），
        不复制到 task 行；``auto_claim`` 不在本方法里被改写（D16 是读路径规则）。

        已归档的频道 ⇒ 400：归档 = 频道面只读（#342 / ADR-0040），判定只有一处
        （:meth:`_require_writable_channel`）。
        """
        self._require_writable_channel(channel_id)
        allowed = {
            "mode",
            "team_leader_member_id",
            "workspace_root",
            "workspace_backend",
            "permission_mode",
            "web_access",
            "docker_egress",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise DomainError(
                "validation_failed",
                self._locale_copy("error.channel_config_invalid"),
                fields={
                    key: self._locale_copy("error.field.channel_config_unknown") for key in sorted(unknown)
                },
            )
        config = self._db.update_channel_config(channel_id, **changes)
        # 工作区配置变了：清掉 Docker 失败短缓存，下一次轮次重新尝试（改回 local 也立即生效）。
        clear_docker_failure()
        return self._channel_config_view(config)

    def _locale_copy(self, key: str, **params: Any) -> str:
        return render(key, self._locale(), **params)

    def _channel_config_view(self, config: dict[str, Any]) -> dict[str, Any]:
        """配置载荷：库内字段 + 固定 ``team_scope`` + 队长的后端事实。"""
        if self._orchestrator is not None:
            availability = self._orchestrator.leader_status(config)
            leader_available = availability.ok
            leader_reason = "" if availability.ok else availability.reason
        else:  # pragma: no cover - 生产装配总有编排；没有时给出最保守的可读事实
            leader_available = False
            leader_reason = self._locale_copy("availability.orchestrator_absent")
        return {
            "channel_id": config["channel_id"],
            "mode": config["mode"],
            # team 覆盖 channel 型 target 与全部 task target（D23=B）；它不是可写字段。
            "team_scope": TEAM_SCOPE,
            "team_leader_member_id": config["team_leader_member_id"],
            "leader_available": leader_available,
            "leader_reason": leader_reason,
            "workspace_root": config["workspace_root"],
            "workspace_backend": config["workspace_backend"],
            "permission_mode": config["permission_mode"],
            # 联网档（#184 / ADR-0026 §二）：``None`` = 关闭。频道给默认值、Agent 可覆盖。
            "web_access": config["web_access"],
            # 容器出网开关（#187 / ADR-0026 §五）：``false``（缺省）= 容器无网。
            "docker_egress": config["docker_egress"],
        }

    # ─── 任务看板与任务线程 ───────────────────────────────────────────────
    def tasks(self, channel_id: str | None = None) -> list[dict[str, Any]]:
        return self._db.list_tasks(channel_id=channel_id, member_id=self._member_id)

    def create_task(self, channel_id: str, title: str) -> dict[str, Any]:
        """新建任务；随后由**编排**裁决这一张新任务该唤醒谁（`CONTEXT.md` **唤醒** / ADR-0025）。

        唤醒裁决不是 HTTP 面的一个动作，也不扩大**成员面适配接缝**的输出形状（仍是「零或多条待落库回复
        + 一个轮次结果」）：裁定与落库都在后端，模型不经手。两条分支：

        - **team 频道**：只向队长投递一轮必答，仍不认领、不改任务行、不唤醒成员、不扣跳（#179 A）；
          队长不可用 ⇒ 零唤醒 + 一条 ``invalid-leader``。
        - **autonomous / DM**：逐字维持既有认领规则（ADR-0010 / ADR-0016）。

        没人认领（开关关着 / 没有可用 Agent / 这张任务已被认领过）时读回的 `task` 与不认领逐字相同。

        已归档的频道 ⇒ 400（#342 / ADR-0040：归档 = 频道面只读，**新建任务也算频道面的写入**）。
        **既有任务的**改状态 / 线程发言不受影响——任务的生命周期由任务自己的五态表达，那句边界
        落在 :meth:`_require_writable_channel` 的说明里。
        """
        self._require_writable_channel(channel_id)
        task = self._db.create_task(channel_id=channel_id, title=title)
        # 频道流里的入口（#198 / ADR-0028）：指针消息与任务行同事务落库，这里把它作为**频道 target**
        # 的既有 ``message`` 事件推给订阅者。系统事件不唤醒、不计未读，因此这里**不**对频道调
        # ``orchestrator.notify``——唤醒裁决仍只有下面那一次。
        pointer = self._db.task_pointer_message(task["id"])
        if pointer is not None and self._events is not None:
            self._events.publish(channel_id, {"type": "message", "seq": pointer["seq"], "message": pointer})
        if self._orchestrator is not None:
            self._orchestrator.notify_task_created(task["id"], channel_id)
            task = self._db.get_task(task["id"])
        return {
            "task": task,
            "target": self._db.get_target(task["target_id"]),
            "message": pointer,
        }

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

    def task_round_outcomes(self, task_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        """任务 target 的轮次结果（重连补拉用；与频道同一份只读事实，作用域换成 task）。"""
        task = self._db.get_task(task_id, self._member_id)
        return self._db.list_round_outcomes(task["target_id"], after_seq=after_seq)

    def task_members(self, task_id: str) -> list[dict[str, Any]]:
        task = self._db.get_task(task_id, self._member_id)
        return self._db.list_target_members(task["target_id"])

    def task_budget(self, task_id: str) -> dict[str, Any]:
        task = self._db.get_task(task_id, self._member_id)
        return self._db.budget_state(task["target_id"])

    def team_runs(
        self,
        target_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """该 target 的运行记录（倒序分页）与各自的分派快照。

        这是运行面板断线后的快照兜底（ADR-0020 四）：``agent_event`` / ``team_assignment``
        是 live-only，重连后只能靠这份读面恢复。``target_id`` 既可以是频道，也可以是任务
        线程的 target；未知 target 由库层 404。
        """
        runs, has_more = self._db.list_team_runs_page(target_id, limit=limit, offset=offset, task_id=task_id)
        return {
            "target_id": target_id,
            "runs": runs,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
        }

    def plan(self, target_id: str, limit: int = PLAN_RUN_RETENTION) -> dict[str, Any]:
        """该 target 的 Plan 快照（按 member 分组；默认每个成员最近 20 个 run）。

        这是运行面板的只读兜底面：Plan 不是消息、不进未读/搜索/看板/预算，刷新与重启后
        仍从这里读回同一份清单（ADR-0023）。
        """
        return self._db.plan_view(target_id, limit=limit)

    # ─── SOP（owner 显式启动的多步运行；ADR-0023） ─────────────────────────
    # 这些方法只经人类面端点到；成员面没有 SOP 工具，Agent 不能自行创建或启动。
    def sops(self) -> list[dict[str, Any]]:
        return self._db.list_sop_definitions()

    def sop(self, definition_id: str) -> dict[str, Any]:
        return self._db.get_sop_definition(definition_id)

    def create_sop(
        self,
        *,
        name: str,
        description: str,
        steps: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._db.create_sop_definition(name=name, description=description, steps=steps)

    def update_sop(
        self,
        definition_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        steps: Sequence[dict[str, Any]] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        return self._db.update_sop_definition(
            definition_id,
            name=name,
            description=description,
            steps=steps,
            enabled=enabled,
        )

    def start_sop_run(self, definition_id: str, *, target_id: str) -> dict[str, Any]:
        if self._sops is None:
            raise DomainError("validation_failed", render("error.sop_agent_unavailable", self._locale()))
        return self._sops.start(definition_id, target_id=target_id)

    def sop_run(self, run_id: str) -> dict[str, Any]:
        if self._sops is None:
            raise NotFound(render("error.sop_run_not_found", self._locale(), sop_run_id=run_id))
        return self._sops.view(run_id)

    def resume_sop_run(
        self,
        run_id: str,
        *,
        reply_id: str | None = None,
        confirm_results: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if self._sops is None:
            raise NotFound(render("error.sop_run_not_found", self._locale(), sop_run_id=run_id))
        return self._sops.resume(run_id, reply_id=reply_id, confirm_results=confirm_results or [])

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
                self._orchestrator.notify_task_assignment(
                    task["target_id"], message["seq"], member_id, actor_member_id=actor["id"]
                )
            else:
                self._orchestrator.notify_task_reassignment(
                    task["target_id"], message["seq"], member_id, actor_member_id=actor["id"]
                )
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
        - 频道不存在 → 404（``get_channel`` 早退）；已删除同理（同一道读路径过滤）。
        - **已归档 ⇒ 400**：归档 = 频道面只读（#342 / ADR-0040），加成员也是频道面的写入。
        - 成员不存在或已删除 / 不是 Agent → ``validation_failed``，**整体不落库**（校验先于写入）。
          错误文案与 ``create_channel`` 的初始成员校验同口径：owner 由建频道自动加入，不经此端点。
        - 只写 ``target_member`` + 补 ``target_read``：两者都由 ``db.add_target_member`` 完成，
          这里不新增 SQL 路径、也不改它的语义。
        - **不写 system 事件消息**：显式加入是人的显式动作，与「穿透加入需要在流里留下一条
          agent 自己不会解释的变化」动机不同（裁定 1）。
        """
        self._require_writable_channel(channel_id)  # 404 / 已归档 400 都在这一个判定里
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
        路径与既有字段名一律不变。

        本票（#53）在既有五项之上补两个只读读数（``source`` / ``pending_limit``），落在
        :meth:`Database.channel_budget_view`：当前世代的**有效上界**及其来源，以及「已保存、
        从下一个人类世代起生效」的那个值——两者分开报，于是待生效配置不会被误读成当前额度。
        """
        self._db.get_channel(channel_id)  # 频道面入口：只认 channel 型 target
        return self._db.channel_budget_view(channel_id)

    def set_channel_budget_limit(self, channel_id: str, budget_limit: int) -> dict[str, Any]:
        """保存该频道的回应预算**上界**（人类面唯一的写路径，ADR-0007），返回刷新后的读数。

        服务层是**薄的一层**：档位裁决与「只写上界、不碰 ``used``」的边界都在
        :meth:`Database.set_channel_budget_limit`（与硬上界裁决同一个落点），因此这里不再重复
        一遍判据——两侧各判一次只会漂成两套事实。

        它不落消息、不唤醒、不消耗预算、不开世代：写的是**配置**，不是对话事实（与
        ``PATCH /api/settings`` / ``PATCH /api/agents/{id}`` 同一纪律）。返回的载荷与
        ``GET /api/channels/{id}/budget`` 同形，于是前端一次调用就能刷新「当前世代读数 + 待生效值」。

        已归档的频道 ⇒ 400（#342 / ADR-0040）：预算上界写的是频道配置，归档收走的就是写入面；
        ``GET`` 读入口不受影响。
        """
        self._require_writable_channel(channel_id)
        return self._db.set_channel_budget_limit(channel_id, budget_limit)

    # ─── 成员面：Agent 创建与配置 ─────────────────────────────────────────
    def agents(self) -> list[dict[str, Any]]:
        """全部 Agent 成员 + 配置 + 由后端事实推导的状态点 + 概览读数。"""
        # 概览读数一次聚合全部成员（与列表同一个来源），不做逐个成员的 N+1 查询。
        stats = self._db.agent_stats()
        return [self._agent_view(agent, stats.get(agent["id"])) for agent in self._db.agent_members()]

    def agent(self, member_id: str) -> dict[str, Any]:
        agent = self._db.get_agent(member_id)
        return self._agent_view(agent, self._db.agent_stats(member_id).get(member_id))

    def _agent_view(self, agent: dict[str, Any], stats: dict[str, Any] | None = None) -> dict[str, Any]:
        view = dict(agent)
        view["provider"] = provider_of(str(agent.get("model_id") or ""))
        view["is_remote"] = is_remote_provider(view["provider"])
        view["remote_consent"] = self._db.has_remote_consent(view["provider"]) if view["provider"] else False
        view["stats"] = self._agent_stats_view(stats)
        if self._orchestrator is not None:
            view["status"] = self._orchestrator.status_of(agent)
            availability = self._orchestrator.availability(agent)
            view["enabled_reason"] = "" if availability.ok else availability.reason
        else:  # pragma: no cover - 服务在没有编排器时也应给出四态里最保守的那一态
            view["status"] = "offline"
            view["enabled_reason"] = render("availability.orchestrator_absent", self._locale())
        # 系统提示是配置数据，不随列表返回全量正文以外的东西；这里原样带出以证明「落库即用户输入」。
        return view

    def create_agent(
        self,
        name: str,
        role: str,
        model_id: str,
        system_prompt: str,
        *,
        workspace_backend: str | None = None,
        permission_mode: str | None = None,
        allow_local_shell: bool = False,
        docker_image: str | None = None,
    ) -> dict[str, Any]:
        """新建 Agent。任一字段不合法则整体不创建，不留半成品成员行。

        Agent 级 workspace 覆盖（D14/D15）与其它配置同一条偏序：由库层校验取值，任一不合法则
        整条创建都不发生；它们是配置事实，不落消息、不唤醒。
        """
        locale = self._locale()
        error = model_id_error(model_id, locale)
        if error is not None:
            raise DomainError(
                "validation_failed", render("error.agent_invalid", locale), fields={"model_id": error}
            )
        agent = self._db.create_agent(
            name=name,
            role=role,
            model_id=model_id,
            system_prompt=system_prompt,
            workspace_backend=workspace_backend,
            permission_mode=permission_mode,
            allow_local_shell=allow_local_shell,
            docker_image=docker_image,
        )
        # 新建成员立刻有家目录与 ReMe 工作区（ADR-0035）：成员行已落库，家目录不是对话的前提，
        # 因此建目录失败只留一条可读告警——不回滚、不让创建失败。
        try:
            memory_workspace_dir(self._global_home(), agent["id"])
        except (WorkspaceConfigError, OSError) as error:
            _log.warning("Agent home is unavailable for %s (the member row is kept): %s", agent["id"], error)
        return self._agent_view(agent, self._db.agent_stats(agent["id"]).get(agent["id"]))

    def update_agent(
        self,
        member_id: str,
        *,
        display_name: str | None = None,
        role: str | None = None,
        model_id: str | None = None,
        system_prompt: str | None = None,
        enabled: bool | None = None,
        auto_claim: bool | None = None,
        workspace_backend: str | None = None,
        permission_mode: str | None = None,
        allow_local_shell: bool | None = None,
        docker_image: str | None = None,
        web_access: Any = _UNSET,
        context_window_messages: Any = _UNSET,
        context_trigger_ratio: Any = _UNSET,
        react_max_iters: Any = _UNSET,
    ) -> dict[str, Any]:
        """写 Agent 配置（人类面动作，ADR-0011）：**配置变更不是对话动作**。

        `model_id` 沿用与创建**同一套校验**（``model_id_error``：provider 白名单 + ``provider:model``
        形状）；改到远端 provider 也就沿用了同一套**远端数据流告知**语义——可用性判定读的是落库后的
        ``model_id``（``WakeOrchestrator.availability``），因此这里不需要第二个机制，改完的
        ``enabled_reason`` / 状态点由既有推导给出。

        「暂停」就是本方法上的 ``enabled=False``：它写 `enabled = 0`，**不改唤醒面**（ADR-0011）。
        任一字段不合法则整体不写（校验先于写入）。

        ``auto_claim``（#65 / ADR-0010）就是「**自动认领任务**」开关：它写 `agent_config.auto_claim`，
        编排在**新建任务之后**读它决定要不要替这个 Agent 接手；与其它配置字段同一条纪律——不落消息、
        不唤醒、不消耗预算、不开世代。它**只**影响之后新建的任务，不回填、不改已指派的任务。

        Agent 级 workspace 覆盖（D14/D15）：``workspace_backend`` / ``permission_mode`` /
        ``allow_local_shell`` / ``docker_image`` 写进同一条 ``agent_config`` 行；取值校验在库层
        （非法值整体拒绝）。写了任一项就清掉该成员的 Docker 失败短缓存，下一轮重新按新配置装配——
        改 ``docker_image`` 时池里的旧镜像容器也会在装配层因镜像不一致被替换。

        联网档（#184 / #202）的 ``web_access`` 与上面几个字段不同：它是**三态**——
        ``_UNSET``（没提到，保持原值）/ 具体档位 / 显式 ``None``（清除 Agent 级覆盖、回到跟随频道，
        写库 ``NULL``；2026-10-21 人定，ADR-0026 §十五）。区分发生在请求体层
        （``model_fields_set``），这里只把三态原样交给库层。
        """
        locale = self._locale()
        if model_id is not None:
            error = model_id_error(model_id, locale)
            if error is not None:
                raise DomainError(
                    "validation_failed",
                    render("error.agent_update_invalid", locale),
                    fields={"model_id": error},
                )
        self._db.update_agent(
            member_id,
            display_name=display_name,
            role=role,
            model_id=model_id,
            system_prompt=system_prompt,
            enabled=enabled,
            auto_claim=auto_claim,
            workspace_backend=workspace_backend,
            permission_mode=permission_mode,
            allow_local_shell=allow_local_shell,
            docker_image=docker_image,
            web_access=web_access,
            context_window_messages=context_window_messages,
            context_trigger_ratio=context_trigger_ratio,
            react_max_iters=react_max_iters,
        )
        if any(
            value is not None
            for value in (workspace_backend, permission_mode, allow_local_shell, docker_image)
        ):
            clear_docker_failure(member_id)
        return self.agent(member_id)

    def delete_agent(self, member_id: str) -> str:
        """完整软删除一个 Agent（人类面动作），返回被删掉的成员 id。

        落点只有两处：``member.deleted_at`` 与 ``target_member`` 里它的行（移出全部频道 / 任务线程）。
        消息、任务、DM target、``agent_config`` 与 ``round_outcome`` 一律保留——历史不是删除的代价。
        与配置变更同一条纪律：**不落消息、不唤醒、不消耗预算、不开世代**。
        不存在的 id / owner / 已删除的成员 ⇒ 可读 404（见 ``Database.delete_agent``）。
        """
        return self._db.delete_agent(str(member_id).strip())

    @staticmethod
    def _agent_stats_view(stats: dict[str, Any] | None) -> dict[str, Any]:
        """把 ``Database.agent_stats`` 的行级事实整理成概览页的读数（缺样本时成功率为 ``None``）。"""
        counts = stats or {}
        verdicts = int(counts.get("verdict_count") or 0)
        replied = int(counts.get("replied_count") or 0)
        return {
            "channel_count": int(counts.get("channel_count") or 0),
            "round_count": int(counts.get("round_count") or 0),
            "completed_task_count": int(counts.get("completed_task_count") or 0),
            "success_rate": (replied / verdicts) if verdicts else None,
        }

    # ─── 工作区设置（配置类取值；人类面可写，用量类事实不可写） ─────────────
    def settings(self) -> dict[str, Any]:
        """工作区设置（**语言偏好** / **消息密度** / **主题**）。正本是后端库内的那一行。"""
        return self._db.workspace_settings()

    def update_settings(
        self,
        locale: str | None = None,
        message_density: str | None = None,
        theme: str | None = None,
    ) -> dict[str, Any]:
        """写工作区设置（人类面动作）。只写配置类取值：

        - **不落消息**（设置不是对话事实，不污染 append-only 的取证面）；
        - **不唤醒**任何成员；
        - **不消耗预算**（`target_budget.used` 仍只由开世代与投递跳推进，ADR-0007 的边界）。

        三档的取值校验在 ``db.update_workspace_settings``（非法值整体拒绝，不留一半新一半旧）。
        """
        return self._db.update_workspace_settings(
            locale=None if locale is None else str(locale).strip(),
            message_density=None if message_density is None else str(message_density).strip(),
            theme=None if theme is None else str(theme).strip(),
        )

    # ─── 提醒（库内对象；ADR-0012） ───────────────────────────────────────
    def reminders(self) -> list[dict[str, Any]]:
        """全部提醒（含 ``fired_at`` 与派生 ``state``）。正本是库里的 ``reminder`` 表。"""
        return self._db.list_reminders()

    def create_reminder(self, text: str, time: str, channel_id: str | None = None) -> dict[str, Any]:
        """新建一条提醒（人类面动作）。``channel_id`` 省略 / ``None`` = 不归属任何频道。

        **增删**与设置面同一条纪律：**不落消息、不唤醒、不消耗预算**，也不新增 target、不计未读——
        提醒的增删不是对话事实（ADR-0012 / D18）。
        **到点**是另一条路径（ADR-0017 §一）：到期投递器会为有归属的提醒落一条 ``system`` 消息，
        但它**不唤醒**、不耗预算、不计未读；无归属的提醒到点只写 ``fired_at``。那条写路径不在本类
        （它跑在 HTTP 请求之外），本类只负责人类面的增删读。
        """
        return self._db.create_reminder(
            text=text,
            time=time,
            channel_id=None if channel_id is None else str(channel_id).strip(),
        )

    def delete_reminder(self, reminder_id: str) -> dict[str, Any]:
        """删除一条提醒（人类面动作），返回被删掉的那条。

        **删除动作本身**不落消息、不唤醒、不消耗预算；它也不回收已到点落下的那条 ``system`` 消息
        （消息 append-only，ADR-0001）。
        """
        return self._db.delete_reminder(str(reminder_id).strip())

    # ─── 远端数据流告知 ──────────────────────────────────────────────────
    def _web_provider_views(self) -> list[dict[str, Any]]:
        """五家的掩码状态；``env_configured`` 只表示「进程环境里有这一家的 key」，
        它不改变「库内是否配置」——清除 / 设为默认只对库内行生效。"""
        env = web_env(os.environ)
        views: list[dict[str, Any]] = []
        for view in self._db.web_provider_credential_views():
            backend = SEARCH_BACKENDS[view["provider"]]
            env_configured = bool(backend.env_name and env.get(backend.env_name))
            views.append({**view, "env_configured": env_configured})
        return views

    def _web_active(self) -> dict[str, Any]:
        runtime = self._db.web_provider_credentials()
        try:
            choice = describe_search_provider(
                os.environ,
                credentials=runtime["credentials"],
                default_provider=runtime["default_provider"],
            )
            return {"provider": choice.provider, "source": choice.source}
        except ProviderResolutionError as error:
            # 环境变量写错不该让一次只读请求变成 500：把「配置无效」作为读数交给界面。
            return {"provider": "", "source": "invalid", "reason": error.reason}

    def web_providers(self) -> dict[str, Any]:
        """联网 provider 的人类面读数：五家的掩码状态 + 当前生效的 provider 与来源。

        返回值**不含任何完整 key**：``providers[].masked`` 是唯一可回读的凭据形态；
        ``env_configured`` 只说明环境变量兜底是否可用，仍然不回吐取值。
        """
        return {"providers": self._web_provider_views(), "active": self._web_active()}

    def web_provider_credential(self, provider: str) -> dict[str, Any]:
        """一家 provider 的掩码读数（未配置时 ``configured=False``）。"""
        clean = (provider or "").strip()
        for view in self._web_provider_views():
            if view["provider"] == clean:
                return {"credential": view}
        raise DomainError(
            "validation_failed",
            render("error.web_provider_invalid", self._locale(), values=", ".join(SEARCH_PROVIDERS)),
            fields={
                "provider": render("error.field_choices", self._locale(), values=", ".join(SEARCH_PROVIDERS))
            },
        )

    def save_web_provider_credential(
        self, provider: str, api_key: str | None, is_default: bool | None
    ) -> dict[str, Any]:
        """保存 key（同时写数据面确认）或把已配置的一家设为默认；响应只有掩码。"""
        clean = (provider or "").strip()
        self._db.save_web_provider_credential(clean, api_key, is_default=is_default)
        views = self._web_provider_views()
        return {
            "credential": next(view for view in views if view["provider"] == clean),
            "providers": views,
            "active": self._web_active(),
        }

    def delete_web_provider_credential(self, provider: str) -> dict[str, Any]:
        """清空一家 provider 的凭据（连同默认标记）；响应只有掩码形态。"""
        clean = (provider or "").strip()
        self._db.delete_web_provider_credential(clean)
        views = self._web_provider_views()
        return {
            "credential": next(view for view in views if view["provider"] == clean),
            "providers": views,
            "active": self._web_active(),
        }

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
            # 联网 provider 的确认行（#186 / D6）：与上面那条模型告知同源，只读、只存「何时确认过」。
            # 它是「未确认 ⇒ 联网工具不装配」这条闸门的可读出处，也是人类面确认端点写入后能读回的证据。
            "web_consents": self._db.list_remote_consent_scopes("web"),
        }

    def confirm_web_provider(self, provider: str) -> dict[str, Any]:
        """确认一个联网 provider 的数据面告知（#186 / D6）：``remote_consent_scope('web', provider)``。

        这是 ADR-0026 §四 那条「未确认时不装配、且绝不发起任何调用」的人类面**唯一**写路径。
        幂等（重复确认不覆盖首次时间）；只存时间，不存凭证与连接参数——凭据落在
        ``web_provider_credential``（明文、掩码回读），保存 key 的动作也会写这张确认表。
        ``provider`` 只接受工具面自己的枚举（五家带凭据 provider），不接受任意字符串；免凭据的
        ``exa-mcp`` 不需要确认行。
        """
        locale = self._locale()
        clean = (provider or "").strip()
        if clean not in SEARCH_PROVIDERS:
            raise DomainError(
                "validation_failed",
                render("error.web_provider_invalid", locale, values=", ".join(SEARCH_PROVIDERS)),
                fields={
                    "provider": render("error.field_choices", locale, values=", ".join(SEARCH_PROVIDERS))
                },
            )
        return self._db.record_remote_consent_scope("web", clean)

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

    # ─── MCP 声明（ADR-0021 §三 / ADR-0031 修订：家目录 mcp.json 是唯一正本） ───
    #
    # 结构化编辑面与原文编辑面读写**同一份文件**；库里 ``mcp_server`` 旧行不再参与装配，
    # 只能经 ``export-mcp`` 迁进文件（迁移不删行、冲突拒绝覆盖）。
    def mcp_servers(self, scope_kind: str, scope_id: str) -> dict[str, Any]:
        """该 Agent 家目录 ``mcp.json`` 的条目与运行期读数（结构化编辑的读面）。

        作用域**只接受** ``agent``：频道级声明已随 ADR-0031 修订弃用，旧行留在库里但不进入
        装配、也不在这里出现。文件缺失 ⇒ 空列表 + ``exists=false``（前端据此给出创建入口）。
        可读问题只在原文编辑面（``global_mcp_document`` 的 ``issues``）给一份：文件不可用时
        结构化写路径本就以 400 + 可读原因拒绝，不在读面重复第二份判定。
        """
        self._assert_mcp_agent_scope(scope_kind, scope_id)
        document = self._member_mcp_document(scope_id)
        return {
            "mcp_servers": [self._mcp_server_view(item, scope_id) for item in document.declarations],
            "path": document.path,
            "exists": document.exists,
        }

    def create_mcp_server(self, scope_kind: str, scope_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        """往该 Agent 家目录 ``mcp.json`` 追加一条声明（文件不存在 ⇒ 新建）。

        同名条目视为冲突（409），**不静默覆盖**；文件当前不可用（坏 JSON / 顶层形状不对）⇒
        400 并让人先走原文编辑，绝不把人的原文覆盖成结构化结果。
        """
        self._assert_mcp_agent_scope(scope_kind, scope_id)
        declaration = self._normalize_mcp_values(values)
        payload, target = self._mcp_payload_for_write(scope_id)
        name = str(declaration["name"])
        if self._mcp_entry_index(payload, name) is not None:
            message = render("error.mcp_server_conflict", self._locale(), name=name)
            raise DomainError("mcp_server_conflict", message, status=409, fields={"name": message})
        payload["servers"].append(declaration)
        self._write_mcp_payload(target, payload, scope_id)
        return {"mcp_server": self._reload_mcp_server(scope_id, name)}

    def update_mcp_server(self, server_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        """部分更新文件里的一条声明；整条结果重新校验，重名拒绝。"""
        allowed = {"name", "transport", "command", "args", "url", "env_names", "enabled"}
        unknown = set(changes) - allowed
        if unknown:
            message = render("error.field_choices", self._locale(), values=", ".join(sorted(allowed)))
            raise DomainError(
                "validation_failed",
                message,
                fields=dict.fromkeys(unknown, message),
            )
        member_id, name = self._split_mcp_server_id(server_id)
        self._db.get_agent(member_id)  # 成员存在性；不存在 ⇒ NotFound（404）
        payload, target = self._mcp_payload_for_write(member_id)
        index = self._mcp_entry_index(payload, name)
        if index is None:
            raise NotFound(render("error.mcp_server_not_found", self._locale(), server_id=server_id))
        merged = {**dict(payload["servers"][index]), **dict(changes)}
        declaration = self._normalize_mcp_values(merged)
        renamed = str(declaration["name"])
        if renamed != name and self._mcp_entry_index(payload, renamed, exclude=index) is not None:
            message = render("error.mcp_server_conflict", self._locale(), name=renamed)
            raise DomainError("mcp_server_conflict", message, status=409, fields={"name": message})
        payload["servers"][index] = declaration
        self._write_mcp_payload(target, payload, member_id)
        return {"mcp_server": self._reload_mcp_server(member_id, renamed)}

    def delete_mcp_server(self, server_id: str) -> dict[str, Any]:
        """删掉文件里的一条声明（返回被删条目的最后读数）。"""
        member_id, name = self._split_mcp_server_id(server_id)
        self._db.get_agent(member_id)  # 成员存在性；不存在 ⇒ NotFound（404）
        payload, target = self._mcp_payload_for_write(member_id)
        index = self._mcp_entry_index(payload, name)
        if index is None:
            raise NotFound(render("error.mcp_server_not_found", self._locale(), server_id=server_id))
        removed = payload["servers"].pop(index)
        self._write_mcp_payload(target, payload, member_id)
        if isinstance(removed, Mapping):
            normalized = self._normalize_mcp_values(removed)
            remote = normalized["transport"] in REMOTE_MCP_TRANSPORTS
            return {
                "mcp_server": self._mcp_server_view(
                    {
                        **normalized,
                        "id": member_mcp_server_id(member_id, name),
                        "is_remote": remote,
                        "consent_required": remote,
                        "consented": False,
                    },
                    member_id,
                )
            }
        # 非对象条目只可能是人手工写坏的一条：删掉它就是修复，读数按空声明给出。
        return {
            "mcp_server": {
                "id": server_id,
                "name": name,
                "scope_kind": "agent",
                "scope_id": member_id,
                "runtime": None,
            }
        }

    def confirm_mcp_server(self, server_id: str) -> dict[str, Any]:
        """确认文件里一条远端声明的数据面告知（幂等；stdio 拒绝）。"""
        member_id, name = self._split_mcp_server_id(server_id)
        self._db.get_agent(member_id)  # 成员存在性；不存在 ⇒ NotFound（404）
        document = self._member_mcp_document(member_id)
        declaration = next((item for item in document.declarations if item["name"] == name), None)
        if declaration is None:
            raise NotFound(render("error.mcp_server_not_found", self._locale(), server_id=server_id))
        if not declaration["is_remote"]:
            message = render("error.mcp_consent_local", self._locale())
            raise DomainError("validation_failed", message, fields={"transport": message})
        consent = self._db.record_remote_consent_scope("mcp", str(declaration["id"]))
        refreshed = self._member_mcp_document(member_id)
        item = next((row for row in refreshed.declarations if row["name"] == name), declaration)
        return {"mcp_server": self._mcp_server_view(item, member_id), "consent": consent}

    def _assert_mcp_agent_scope(self, scope_kind: str, scope_id: str) -> None:
        """MCP 声明只挂在 Agent 作用域；频道级旧行已弃用（不静默启用）。"""
        if scope_kind != "agent":
            message = render("error.mcp_scope_invalid", self._locale())
            raise DomainError("validation_failed", message, fields={"scope_kind": message})
        self._db.get_agent(scope_id)  # 成员存在性；不存在 ⇒ NotFound（404）

    def _member_mcp_document(self, member_id: str) -> MemberMCPDocument:
        """读该成员家目录 ``mcp.json``（只读；确认态读既有 ``remote_consent_scope('mcp', …)``）。"""
        return load_member_mcp_declarations(
            self._global_home(),
            member_id,
            consent_of=lambda scope_id: self._db.has_remote_consent_scope("mcp", scope_id),
            locale=self._locale(),
        )

    def _mcp_server_view(self, declaration: Mapping[str, Any], member_id: str) -> dict[str, Any]:
        """文件条目的结构化视图；字段与旧库行响应兼容（前端表单据此回填）。"""
        return {
            "id": str(declaration["id"]),
            "name": str(declaration["name"]),
            "transport": str(declaration["transport"]),
            "command": str(declaration.get("command") or ""),
            "args": [str(item) for item in declaration.get("args") or []],
            "url": str(declaration.get("url") or ""),
            "env_names": [str(item) for item in declaration.get("env_names") or []],
            "enabled": bool(declaration.get("enabled", True)),
            "scope_kind": "agent",
            "scope_id": member_id,
            "is_remote": bool(declaration.get("is_remote")),
            "consent_required": bool(declaration.get("consent_required")),
            "consented": bool(declaration.get("consented")),
            "runtime": runtime_status(str(declaration["id"])),
        }

    def _normalize_mcp_values(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """形状校验的唯一实现在 ``mcp.normalize_mcp_declaration``；这里只翻译成既有错误码。"""
        try:
            return normalize_mcp_declaration(values)
        except InvalidMCPDeclaration as error:
            field, key = MCP_DECLARATION_ERROR_KEYS.get(
                error.field, ("transport", "error.mcp_transport_invalid")
            )
            message = render(key, self._locale())
            raise DomainError("validation_failed", message, fields={field: message}) from None

    def _split_mcp_server_id(self, server_id: str) -> tuple[str, str]:
        """合成标识 ``global:<member>:<name>`` → ``(member, name)``。

        库行时代的旧 id（``mcp_…``）不再指向任何声明：结构化编辑面只认文件条目的合成标识。
        """
        parsed = split_member_mcp_server_id(server_id)
        if parsed is None:
            raise NotFound(render("error.mcp_server_not_found", self._locale(), server_id=server_id))
        return parsed

    @staticmethod
    def _mcp_entry_index(payload: Mapping[str, Any], name: str, *, exclude: int | None = None) -> int | None:
        """文件 ``servers`` 里名字匹配的条目下标（跳过非对象条目与 ``exclude``）。"""
        for index, entry in enumerate(payload.get("servers") or []):
            if index == exclude or not isinstance(entry, Mapping):
                continue
            if str(entry.get("name") or "") == name:
                return index
        return None

    def _mcp_payload_for_write(self, member_id: str) -> tuple[dict[str, Any], str]:
        """结构化编辑的读-改-写入口：家目录必须存在、路径必须安全、文件必须可用。

        文件缺失 ⇒ 以空 ``servers`` 开始（「文件不存在」也有可用的创建路径）；文件存在但不是
        合法 JSON / 顶层形状不对 ⇒ 400，**绝不覆盖**——原文编辑是人的修复面。
        """
        locale = self._locale()
        root = self._global_home()
        home = global_member_home(member_id, root=root)
        if not os.path.isdir(home):
            message = render("error.global_mcp_home_missing", locale, path=home)
            raise DomainError("validation_failed", message, fields={"name": message})
        target = resolve_global_write_path(
            root, AGENTS_DIR, safe_partition_id(member_id), MEMBER_MCP_FILENAME
        )
        if target is None:
            message = render(
                "error.global_mcp_path_invalid",
                locale,
                path=os.path.join(home, MEMBER_MCP_FILENAME),
            )
            raise DomainError("validation_failed", message, fields={"name": message})
        try:
            text = Path(target).read_text(encoding="utf-8")
        except FileNotFoundError:  # 文件不存在：新建从空 servers 开始
            return {"servers": []}, target
        except (OSError, UnicodeDecodeError) as error:
            message = render("error.mcp_file_unusable", locale, reason=str(error))
            raise DomainError("validation_failed", message, fields={"name": message}) from None
        try:
            payload = json.loads(text)
        except ValueError as error:
            message = render("error.mcp_file_unusable", locale, reason=str(error))
            raise DomainError("validation_failed", message, fields={"name": message}) from None
        if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
            message = render(
                "error.mcp_file_unusable",
                locale,
                reason=render("error.global_mcp_servers_missing", locale),
            )
            raise DomainError("validation_failed", message, fields={"name": message})
        return payload, target

    def _write_mcp_payload(self, target: str, payload: Mapping[str, Any], member_id: str) -> None:
        """结构化结果写盘（缩进 2 的稳定 JSON），并清掉该成员的进程内运行时读数。"""
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        self._write_global_mcp_text(target, text)
        clear_runtime_for_member(member_id)

    def _write_global_mcp_text(self, target: str, text: str) -> None:
        """原子写一份 ``mcp.json`` 原文（实现对 ``mcp.write_mcp_document_atomic``）。"""
        try:
            write_mcp_document_atomic(target, text)
        except OSError as error:
            message = render("error.global_mcp_write_failed", self._locale(), reason=str(error))
            raise DomainError("validation_failed", message, fields={"text": message}) from None

    def _reload_mcp_server(self, member_id: str, name: str) -> dict[str, Any]:
        """写完再读：返回的是**文件事实**（含确认态与运行期读数），不是内存里的意图。"""
        document = self._member_mcp_document(member_id)
        item = next((row for row in document.declarations if row["name"] == name), None)
        if item is None:  # 刚写下的条目又读不到：把问题说清楚，而不是返回空
            raise NotFound(
                render(
                    "error.mcp_server_not_found",
                    self._locale(),
                    server_id=f"global:{member_id}:{name}",
                )
            )
        return self._mcp_server_view(item, member_id)

    def global_mcp_servers(self, member_id: str) -> dict[str, Any]:
        """该成员家目录 ``mcp.json`` 的**只读**条目与运行期读数（#236 的旧读面）。

        可读问题不在这里重复：原文编辑面（``global_mcp_file``）的 ``issues`` 是同一份判定，
        修文件的人看那一份。
        """
        self._db.get_agent(member_id)  # 成员存在性；不存在 ⇒ NotFound（404）
        document = self._member_mcp_document(member_id)
        return {
            "member_id": member_id,
            "global_mcp_servers": self._global_mcp_views(member_id, document=document),
            "path": document.path,
            "exists": document.exists,
        }

    def _global_mcp_views(
        self, member_id: str, *, document: MemberMCPDocument | None = None
    ) -> list[dict[str, Any]]:
        """全局声明的展示形态；确认态读既有 ``remote_consent_scope('mcp', 合成标识)``。"""
        source = document if document is not None else self._member_mcp_document(member_id)
        return [
            {
                "name": declaration["name"],
                "transport": declaration["transport"],
                "is_remote": declaration["is_remote"],
                "consented": declaration["consented"],
                "runtime": runtime_status(str(declaration["id"])),
            }
            for declaration in source.declarations
        ]

    def _global_home(self) -> str:
        return self._global_home_override if self._global_home_override is not None else global_home_root()

    def global_mcp_document(self, member_id: str) -> dict[str, Any]:
        """该成员家目录 ``mcp.json`` 的**原文**与条目计数（D7 读一半）。

        ``text`` 是文件原文（逐字，不做摘要、不重排）；不存在时为**空串**且 ``exists=false``——
        「没有这个文件」与「读到空文件」是两件事，前端据此给不同的说明。``servers`` 是文件里
        ``servers`` 数组的条目计数（原文事实，不做合规过滤）。``path`` 是这份文件的**真实路径**
        （写守卫取同一个目标、不判存在性：文件缺席时界面也要能写出它该在哪；根外 symlink
        祖先 / 根外目标 ⇒ ``None``）。读经全局只读守卫
        :func:`workspace.resolve_global_read_path`，**不创建、不写入**任何字节。
        """
        self._db.get_agent(member_id)  # 成员存在性；不存在 ⇒ NotFound（404）
        root = self._global_home()
        parts = (AGENTS_DIR, safe_partition_id(member_id), MEMBER_MCP_FILENAME)
        path = resolve_global_read_path(root, *parts)
        # 路径读数与「文件在不在」解耦：读守卫缺席时返回 None，写守卫返回同一个 realpath。
        target = resolve_global_write_path(root, *parts)
        # 可读问题与运行期装配共用同一份判定（读路径的 issues）：原文编辑面也看得见同一句话。
        issues = list(self._member_mcp_document(member_id).issues)
        if path is None:
            # 既包括「这一层没有这个文件」，也包括「路径被守卫拒（根外 symlink 祖先 / 目标）」。
            # 两者对外都是「读不到原文」，但都不能退化成直读；后者连路径也不给（target=None）。
            return {
                "member_id": member_id,
                "exists": False,
                "text": "",
                "servers": 0,
                "path": target,
                "issues": issues,
            }
        try:
            text = Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:  # realpath 与读取之间的删除：等价于缺席
            return {
                "member_id": member_id,
                "exists": False,
                "text": "",
                "servers": 0,
                "path": target,
                "issues": issues,
            }
        except (OSError, UnicodeDecodeError) as error:
            # 文件在但不可读 / 非 UTF-8：这不是「没有文件」，也不能静默当成空原文。
            message = render("error.global_mcp_read_failed", self._locale(), reason=str(error))
            raise DomainError("validation_failed", message, fields={"text": message}) from None
        return {
            "member_id": member_id,
            "exists": True,
            "text": text,
            "servers": mcp_document_entry_count(text),
            "path": path,
            "issues": issues,
        }

    def save_global_mcp_document(self, member_id: str, text: str) -> dict[str, Any]:
        """把整份 ``mcp.json`` **原文**写进该成员家目录（D7 写一半）。

        三道校验（JSON 可解析 → 顶层是对象且 ``servers`` 是数组 → 每条经
        :func:`mcp.validate_mcp_document` 的同一份内核）**全部通过才写盘**；任一条不合法 ⇒
        ``400`` + 可读原因 + ``fields`` 指出出错的那一条 / 字段，**零写入**。

        写盘语义（N3 第三格）：先写同目录临时文件再用 ``os.replace`` 原子替换（先例
        ``runtime.py`` 的状态落盘）；**原文逐字写**——不 ``json.dumps`` 重排、不重缩进、不动
        末尾换行。文件不存在但成员家目录存在 ⇒ 新建；**家目录本身不存在 ⇒ 400** 且不创建任何
        目录（应用不自动创建全局层，ADR-0031 §三）。文件存在 ⇒ 覆盖（人类显式动作）。
        响应与读端点同形（含 ``path``：这份文件写到的**真实路径**；``issues`` 是写后经装配层
        同一读路径的回读判定）。

        守卫：写前用同一内核（:func:`workspace.resolve_global_write_path`，与只读守卫共用
        ``realpath`` 判定）取目标的真实路径，拒绝根外 symlink 祖先与根外目标。
        """
        self._db.get_agent(member_id)  # 成员存在性；不存在 ⇒ NotFound（404）
        locale = self._locale()
        try:
            validate_mcp_document(text, locale)
        except InvalidMCPDocument as error:
            raise DomainError(
                "validation_failed", error.message, fields={error.location: error.message}
            ) from None

        root = self._global_home()
        home = global_member_home(member_id, root=root)
        if not os.path.isdir(home):
            message = render("error.global_mcp_home_missing", locale, path=home)
            raise DomainError("validation_failed", message, fields={"text": message})
        target = resolve_global_write_path(
            root, AGENTS_DIR, safe_partition_id(member_id), MEMBER_MCP_FILENAME
        )
        if target is None:
            message = render(
                "error.global_mcp_path_invalid",
                locale,
                path=os.path.join(home, MEMBER_MCP_FILENAME),
            )
            raise DomainError("validation_failed", message, fields={"text": message})

        self._write_global_mcp_text(target, text)
        # 原文换了内容，进程内的旧读数就不再代表文件事实；下一次装配会重建。
        clear_runtime_for_member(member_id)
        return {
            "member_id": member_id,
            "exists": True,
            "text": text,
            "servers": mcp_document_entry_count(text),
            "path": target,
            "issues": list(self._member_mcp_document(member_id).issues),
        }

    def confirm_global_mcp_server(self, member_id: str, name: str) -> dict[str, Any]:
        """确认该成员全局 ``mcp.json`` 的一条远端声明（#236 / D8）。

        写进既有 ``remote_consent_scope('mcp', 'global:<member_id>:<name>')``：幂等、只存时间、
        重启后仍有效；**不新增** ``scope_kind``、不改表、不 bump ``SCHEMA_VERSION``。确认之后，
        下一轮装配经同一个 ``has_remote_consent_scope`` 读到 ``consented=True``，不再走
        ``consent_required`` 分支。
        """
        self._db.get_agent(member_id)  # 成员存在性；不存在 ⇒ NotFound（404）
        locale = self._locale()
        clean = (name or "").strip()
        document = self._member_mcp_document(member_id)
        declaration = next((item for item in document.declarations if item["name"] == clean), None)
        if declaration is None:
            message = render("error.global_mcp_declaration_not_found", locale, name=clean)
            raise DomainError("validation_failed", message, fields={"name": message})
        if not declaration["is_remote"]:
            # stdio 是本机进程：不需要确认，也不接受确认——绝不写确认行。
            message = render("error.mcp_consent_local", locale)
            raise DomainError("validation_failed", message, fields={"transport": message})
        consent = self._db.record_remote_consent_scope("mcp", str(declaration["id"]))
        return {
            "global_mcp_server": next(
                row for row in self._global_mcp_views(member_id) if row["name"] == declaration["name"]
            ),
            "consent": consent,
        }

    # ─── 消息 ─────────────────────────────────────────────────────────────
    def send_message(
        self,
        channel_id: str,
        body: str,
        task_ref: str | None = None,
        attachment_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """频道中的人类消息；task_ref 若存在，数据库要求其与频道 target 同域。

        ``attachment_ids`` 是**先上传拿标识、随消息提交时绑定**的那一步（ADR-0014）；
        绑定与消息落库在 ``db`` 层的**同一事务**里完成。

        已归档的频道 ⇒ 400（#342 / ADR-0040）：归档 = 频道面只读，这是那五个写入口里的第一个。
        """
        self._require_writable_channel(channel_id)
        return self._send_target_message(channel_id, body, task_ref, attachment_ids)

    def send_task_message(
        self,
        task_id: str,
        body: str,
        task_ref: str | None = None,
        attachment_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """任务线程消息落在任务自己的 target，并沿用同一落库、事件流与唤醒路径。

        任务线程的回复框**没有**工具行（规格 D15：附件按钮随主 composer），但消息契约是同一条：
        附件一旦随消息提交，绑定与落库同样在同一事务里。
        """
        task = self._db.get_task(task_id, self._member_id)
        return self._send_target_message(task["target_id"], body, task_ref, attachment_ids)

    def _send_target_message(
        self,
        target_id: str,
        body: str,
        task_ref: str | None = None,
        attachment_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """人类消息落库 → 推送事件流 → 唤醒成员面的 Agent。

        顺序是契约的一部分：``append_message`` 抛错时后面两步都不执行（落库失败不唤醒）。
        提及解析结果作为**快照**随消息一起落库，唤醒与前端高亮都消费这一份。

        解析用的是 ``clean_message_body()`` 清洗过的正文，**不是**入参原文：快照的位置是相对正文的
        坐标，而 ``append_message`` 落库前会 strip。两边不是同一个串时，前导空白会让每个提及整体
        偏前（#291）。清洗与落库共用同一个函数，因此「解析的串」与「落库的串」在构造上是同一个。
        """
        members = self._db.list_members()
        snapshot = parse_mentions(clean_message_body(body), members)
        message = self._db.append_message(
            target_id=target_id,
            body=body,
            author_member_id=self._member_id,
            kind="human",
            mention_snapshot=snapshot,
            task_ref=task_ref,
            attachment_ids=attachment_ids,
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
