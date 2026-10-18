"""SOP：owner 显式启动的多步运行（ADR-0023 / 规格 §3.5、§4.4）。

SOP 是**人的编排动作**，不是模型的自由动作，也不是任务：

- 定义（``sop_definition``）保存 name / description / steps；每个 step 用 **member id** 引用
  executor 与可空 verifier，并带 ``description`` 与 ``max_attempts``。
- 运行（``sop_run``）保存一次 ``SOPRunState.model_dump_json()``、派生的 ``phase``、启动者
  （固定为 owner）与所属 target。运行记录与 ``sop_event`` 是唯一过程面：**不**改任务五态、
  不写消息、不进看板/未读/搜索/唤醒/预算、不新增 target。
- 引擎只在进程内运行：``SOPEngine`` 走真实 ``SOPStep``（executor 产出 → verifier 判定，
  verifier 拒绝一次后由引擎重试，``max_attempts`` 用尽则 ``failed``）。参与者由成员面适配器
  按 member id 装配；本模块不依赖具体模型，也不在导入时加载 AgentScope。
- ``awaiting`` 是终止本轮流的“停车”态：owner 经 ``POST /api/sop-runs/{id}/resume`` 送回
  ``UserConfirmResultEvent`` 后从持久化的 ``SOPRunState`` 继续。参与者 AgentState 的跨重启恢复
  不在本票（D19）；SOP 自己的状态可恢复。
- 保留策略：``sop_run`` 保留最近 50 个或 90 天；完成/失败**不**自动删除定义。

``agentscope`` 按需导入：只读端点与脚本化替身路径不为加载框架付代价。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .db import SOP_RUN_RETENTION, Database, DomainError
from .events import EventBus, sop_event
from .i18n import locale_of, render

__all__ = [
    "SOP_RUN_RETENTION",
    "SOPRunner",
    "build_sop",
    "sop_agent_prompt",
]

_log = logging.getLogger(__name__)

AgentFactory = Callable[..., Any]


def sop_agent_prompt(member: Mapping[str, Any], *, role: str) -> str:
    """SOP 参与者的 system prompt：成员配置 + 它在本次运行里的职责。

    步骤本身会在每次尝试里注入 subject / description 与上一次的拒绝原因；这里只交代身份。
    """
    parts: list[str] = []
    base = str(member.get("system_prompt") or "").strip()
    if base:
        parts.append(base)
    member_role = str(member.get("role") or "").strip()
    if member_role:
        parts.append(f"你的角色：{member_role}")
    if role == "verifier":
        parts.append(
            "你是一次 SOP 运行的 verifier：只判定 executor 的产出是否满足步骤要求，"
            "不要替它完成工作；用结构化 verdict 给出通过与否，拒绝时说清具体缺什么。"
        )
    else:
        parts.append(
            "你是一次 SOP 运行的 executor：按步骤要求完成工作，"
            "并用结构化 handover 交出后续步骤能独立读懂的结果。"
        )
    return "\n\n".join(parts)


def build_sop(
    definition: Mapping[str, Any],
    *,
    agent_factory: AgentFactory,
    agents: dict[str, Mapping[str, Any]],
) -> tuple[Any, list[Any]]:
    """把定义行装配成 AgentScope ``SOP``；返回 ``(sop, 参与者列表)``。

    ``agents`` 是 ``member_id → agent 行`` 的映射（由调用方读库并校验），``agent_factory``
    由成员面适配器提供（生产：``AgentScopeAgentSeam.sop_agent``；测试：进程内 stub）。
    """
    from agentscope.sop import SOP, SOPStep

    participants: list[Any] = []
    steps: list[Any] = []
    for raw in definition.get("steps") or []:
        executor_id = str(raw.get("executor_member_id") or "")
        executor = agent_factory(agents[executor_id], role="executor")
        participants.append(executor)
        verifier_id = raw.get("verifier_member_id")
        verifier = None
        if verifier_id:
            verifier = agent_factory(agents[str(verifier_id)], role="verifier")
            participants.append(verifier)
        steps.append(
            SOPStep(
                subject=str(raw.get("subject") or ""),
                description=str(raw.get("description") or ""),
                executor=executor,
                verifier=verifier,
                max_attempts=int(raw.get("max_attempts") or 3),
            )
        )
    return (
        SOP(
            name=str(definition.get("name") or ""),
            steps=steps,
            description=str(definition.get("description") or ""),
        ),
        participants,
    )


class SOPRunner:
    """SOP 定义的运行装配：owner 启动 → 后台跑真实引擎 → 每步落状态并广播 ``sop_event``。"""

    def __init__(
        self,
        db: Database,
        events: EventBus,
        *,
        scheduler: Any,
        agent_factory: AgentFactory | None,
    ) -> None:
        self._db = db
        self._events = events
        self._scheduler = scheduler
        self._agent_factory = agent_factory
        # 进程内事实：当前停车的 ``reply_id`` / ``tool_calls``（不落库；重启后由 owner 显式给）。
        self._awaiting: dict[str, dict[str, Any]] = {}
        self._active: set[str] = set()
        self._lock = threading.RLock()

    # ─── 启动 / 恢复 ─────────────────────────────────────────────────────
    def start(self, definition_id: str, *, target_id: str) -> dict[str, Any]:
        """owner 启动一次运行；返回落库后的 run 快照（``phase=pending``）。"""
        definition = self._db.get_sop_definition(definition_id)
        if not definition["enabled"]:
            raise DomainError("validation_failed", self._copy("error.sop_definition_disabled"))
        self._db.get_target(target_id)  # 404 早退
        sop, participants = self._build(definition)
        from agentscope.sop import SOPRunState

        run = self._db.create_sop_run(
            sop_definition_id=definition_id,
            target_id=target_id,
            state=SOPRunState().model_dump_json(),
            phase="pending",
        )
        self._submit(run["id"], sop, participants, target_id, None)
        return self.view(run["id"])

    def resume(
        self,
        run_id: str,
        *,
        reply_id: str | None = None,
        confirm_results: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """恢复一个 ``awaiting`` 的运行：送入人类确认（与审批同形的结构化结果）。"""
        run = self._db.get_sop_run(run_id)
        if run["phase"] != "awaiting":
            raise DomainError(
                "validation_failed",
                self._copy("error.sop_run_not_awaiting", phase=run["phase"]),
            )
        with self._lock:
            if run_id in self._active:
                raise DomainError("validation_failed", self._copy("error.sop_run_busy"))
        tracked = self._awaiting.get(run_id) or {}
        parked_reply = str(reply_id or tracked.get("reply_id") or "")
        if not parked_reply:
            raise DomainError("validation_failed", self._copy("error.sop_resume_reply_missing"))
        event = self._confirmation(parked_reply, confirm_results or [])
        definition = self._db.get_sop_definition(str(run["sop_definition_id"]))
        sop, participants = self._build(definition)
        with self._lock:
            self._awaiting.pop(run_id, None)
        resumed_state = dict(run["state"])
        resumed_state["phase"] = "running"
        self._db.update_sop_run_state(
            run_id,
            state=_dump_state(resumed_state),
            phase="running",
        )
        self._submit(run_id, sop, participants, str(run["target_id"]), event)
        return self.view(run_id)

    def view(self, run_id: str) -> dict[str, Any]:
        """运行快照：库里的持久事实 + 进程内的 ``awaiting`` 详情（没有则为 ``None``）。"""
        run = self._db.get_sop_run(run_id)
        run["awaiting"] = self._awaiting.get(run_id)
        return run

    # ─── 引擎驱动 ────────────────────────────────────────────────────────
    def _submit(
        self,
        run_id: str,
        sop: Any,
        participants: list[Any],
        target_id: str,
        resume_input: Any,
    ) -> None:
        with self._lock:
            self._active.add(run_id)
        self._scheduler.submit(self._drive, run_id, sop, participants, target_id, resume_input)

    def _drive(
        self,
        run_id: str,
        sop: Any,
        participants: list[Any],
        target_id: str,
        resume_input: Any,
    ) -> None:
        try:
            asyncio.run(self._drive_async(run_id, sop, target_id, resume_input))
        except Exception:
            _log.exception("SOP run %s failed to execute", run_id)
            self._mark_failed(run_id, target_id)
        finally:
            with self._lock:
                self._active.discard(run_id)
            asyncio.run(self._close(participants))

    async def _drive_async(
        self,
        run_id: str,
        sop: Any,
        target_id: str,
        resume_input: Any,
    ) -> None:
        from agentscope.sop import SOPEngine, SOPRunState

        stored = self._db.get_sop_run(run_id)
        state = SOPRunState.model_validate(_stored_state(stored))
        engine = SOPEngine(sop, state)
        try:
            async for item in engine.reply_stream(resume_input):
                self._remember_awaiting(run_id, item)
                self._observe(run_id, target_id, engine, item)
                self._persist(run_id, engine)
        finally:
            self._persist(run_id, engine)
            final_phase = engine.state.phase.value
            if final_phase != "awaiting":
                with self._lock:
                    self._awaiting.pop(run_id, None)
            self._publish_final(run_id, target_id, engine, final_phase)

    def _observe(self, run_id: str, target_id: str, engine: Any, item: Any) -> None:
        """把引擎的步骤事件映射成 ``sop_event``；未知事件不广播。"""
        from agentscope.event import CustomEvent

        if not isinstance(item, CustomEvent):
            return
        name = str(getattr(item, "name", ""))
        if name not in ("SOP_STEP_STARTED", "SOP_STEP_ENDED"):
            return
        value = getattr(item, "value", None) or {}
        index = _current_step(engine.state)
        record = engine.state.steps[index]
        phase = "running" if name == "SOP_STEP_STARTED" else record.phase.value
        payload: dict[str, Any] = {
            "step": value.get("step", ""),
            "attempt": value.get("attempt", len(getattr(record, "verifications", []))),
        }
        if name == "SOP_STEP_ENDED":
            payload["phase"] = phase
            payload["attempts"] = len(getattr(record, "verifications", []))
            if record.verifications:
                payload["verdict"] = record.verifications[-1].model_dump(mode="json")
        self._publish(
            target_id,
            sop_event(
                sop_run_id=run_id,
                step_index=index,
                phase=phase,
                payload=payload,
            ),
        )

    def _publish_final(self, run_id: str, target_id: str, engine: Any, final_phase: str) -> None:
        state = engine.state
        index = _current_step(state)
        payload: dict[str, Any] = {"run_phase": final_phase}
        awaiting = self._awaiting.get(run_id)
        if final_phase == "awaiting" and awaiting is not None:
            payload["reply_id"] = awaiting.get("reply_id")
            if awaiting.get("tool_calls"):
                payload["tool_calls"] = awaiting["tool_calls"]
        self._publish(
            target_id,
            sop_event(
                sop_run_id=run_id,
                step_index=index,
                phase=final_phase,
                payload=payload,
            ),
        )

    def _persist(self, run_id: str, engine: Any) -> None:
        self._db.update_sop_run_state(
            run_id,
            state=engine.state.model_dump_json(),
            phase=engine.state.phase.value,
        )

    def _mark_failed(self, run_id: str, target_id: str) -> None:
        """引擎异常时把运行收口成 ``failed``；持久状态保持可读（不丢已完成步骤）。"""
        try:
            stored = self._db.get_sop_run(run_id)
            from agentscope.sop import SOPRunState

            state = SOPRunState.model_validate(_stored_state(stored))
            self._db.update_sop_run_state(run_id, state=state.model_dump_json(), phase="failed")
            with self._lock:
                self._awaiting.pop(run_id, None)
            self._publish(
                target_id,
                sop_event(
                    sop_run_id=run_id,
                    step_index=_current_step(state),
                    phase="failed",
                    payload={"run_phase": "failed"},
                ),
            )
        except Exception:  # 收口失败不能盖住原始异常
            _log.debug("SOP run %s could not be marked failed", run_id, exc_info=True)

    # ─── 辅助 ────────────────────────────────────────────────────────────
    def _build(self, definition: Mapping[str, Any]) -> tuple[Any, list[Any]]:
        if self._agent_factory is None:
            raise DomainError("validation_failed", self._copy("error.sop_agent_unavailable"))
        agents: dict[str, Mapping[str, Any]] = {}
        for raw in definition.get("steps") or []:
            for key in ("executor_member_id", "verifier_member_id"):
                member_id = raw.get(key)
                if member_id and str(member_id) not in agents:
                    agents[str(member_id)] = self._db.require_sop_agent(str(member_id))
        return build_sop(
            definition,
            agent_factory=self._agent_factory,
            agents=agents,
        )

    def _remember_awaiting(self, run_id: str, item: Any) -> None:
        from agentscope.event import (
            RequireExternalExecutionEvent,
            RequireUserConfirmEvent,
        )

        if isinstance(item, RequireUserConfirmEvent):
            self._awaiting[run_id] = {
                "kind": "confirm",
                "reply_id": str(item.reply_id),
                "tool_calls": [call.model_dump(mode="json") for call in item.tool_calls],
            }
        elif isinstance(item, RequireExternalExecutionEvent):
            self._awaiting[run_id] = {
                "kind": "external",
                "reply_id": str(item.reply_id),
                "tool_calls": [call.model_dump(mode="json") for call in item.tool_calls],
            }

    def _confirmation(self, reply_id: str, confirm_results: Sequence[Mapping[str, Any]]) -> Any:
        from agentscope.event import ConfirmResult, UserConfirmResultEvent

        try:
            results = [ConfirmResult.model_validate(raw) for raw in confirm_results]
        except Exception as error:
            raise DomainError("validation_failed", self._copy("error.sop_resume_invalid")) from error
        return UserConfirmResultEvent(reply_id=reply_id, confirm_results=results)

    async def _close(self, participants: Sequence[Any]) -> None:
        from .agent_seam import _aclose_model_client

        for participant in participants:
            await _aclose_model_client(getattr(participant, "model", None))

    def _publish(self, target_id: str, frame: Mapping[str, Any]) -> None:
        try:
            self._events.publish(target_id, frame)
        except Exception:  # 直播是观察面：发布失败不能改写运行结果
            _log.debug("sop_event publish failed", exc_info=True)

    def _copy(self, key: str, /, **params: Any) -> str:
        return render(key, locale_of(self._db), **params)


def _stored_state(run: Mapping[str, Any]) -> Mapping[str, Any]:
    state = run.get("state")
    return state if isinstance(state, Mapping) else {}


def _dump_state(state: Mapping[str, Any]) -> str:
    import json

    return json.dumps(dict(state), ensure_ascii=False)


def _current_step(state: Any) -> int:
    """当前步骤下标：第一个未完成/未失败的步骤；全部收口时取最后一步。"""
    steps = list(getattr(state, "steps", []))
    for index, record in enumerate(steps):
        if getattr(record.phase, "value", record.phase) not in ("completed", "failed"):
            return index
    return max(0, len(steps) - 1)
