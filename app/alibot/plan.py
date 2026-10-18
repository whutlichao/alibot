"""Plan 的运行装配：AgentScope 四工具 + 每轮装载/回写（ADR-0023 / 规格 §3.5）。

Plan 是成员**轮次内**的工作清单，不是任务五态：这里的每一行只写 ``plan_run`` /
``plan_task``，从不触碰 ``task``、消息、未读、搜索、唤醒或预算（D13）。三个边界落在这里：

- **四工具**：``TaskCreate`` / ``TaskGet`` / ``TaskList`` / ``TaskUpdate`` 由本轮 toolkit 注入，
  它们操作 ``AgentState.tasks_context.tasks``；没有 Plan 的轮次不注册它们。
- **装载与回写**：每个 Agent 的每次 ``reply_stream`` 开一个新 run，开跑前把该
  ``(target_id, member_id)`` 最近一个 run 的任务装回 ``AgentState``，收口后以本 run 的 id 为界
  回写快照。于是进程重启后 ``GET /api/targets/{id}/plan`` 仍能读到同一份清单。
- **team 模式**：队长与**被 ``TeamAssign`` 的成员**各有一个 Plan run；成员仍是自己的清单，
  不获得任务上下文（任务上下文由 ``agent_seam`` 的既有装配决定，本模块不碰）。

``agentscope`` 仍按需导入：只读端点与脚本化替身的路径不会因为加载框架而付代价。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from typing import Any

from .db import Database, utc_now
from .events import plan_update

__all__ = [
    "PLAN_TOOL_NAMES",
    "PlanRun",
    "PlanSession",
    "plan_agent_class",
    "plan_tools",
]

_log = logging.getLogger(__name__)

# 规格 §3.5 的四个工具名：测试与装配共用同一份词表，避免「注入了哪些工具」两处漂移。
PLAN_TOOL_NAMES = ("TaskCreate", "TaskGet", "TaskList", "TaskUpdate")

Publish = Callable[[dict[str, Any]], None]


def plan_tools() -> list[Any]:
    """本轮要注册的 AgentScope Plan 工具（按需导入框架）。"""
    from agentscope.tool import TaskCreate, TaskGet, TaskList, TaskUpdate

    return [TaskCreate(), TaskGet(), TaskList(), TaskUpdate()]


def plan_agent_class() -> Any:
    """返回带 Plan 生命周期的 ``Agent`` 子类（首次调用时才 import 框架并建类）。

    子类的唯一职责是包住一次 ``reply_stream``：开 run → 把最近清单装进 ``AgentState`` →
    流结束后回写。推理、工具与事件消费仍全部走基类，行为不因 Plan 改变。
    """

    global _PLAN_AGENT_CLASS
    if _PLAN_AGENT_CLASS is None:
        from agentscope.agent import Agent

        class PlanAgent(Agent):
            """每次回复自动开/收一个 Plan run 的 Agent。"""

            _plan_session: PlanSession | None = None
            _plan_member_id: str | None = None

            async def reply_stream(self, *args: Any, **kwargs: Any):
                session = self._plan_session
                run: PlanRun | None = None
                if session is not None and self._plan_member_id is not None:
                    run = session.open(self._plan_member_id)
                    # 四工具经 tool_call 注入的 state 在**调用时**读取，因此只替换任务上下文
                    # 就够了；权限引擎等既有的 state 部件保持原样。
                    self.state.tasks_context = run.tasks_context
                failed = False
                try:
                    async for item in super().reply_stream(*args, **kwargs):
                        failed = failed or _reply_failed(item)
                        yield item
                except BaseException:
                    if run is not None:
                        # 原异常才是这一轮失败的原因；回写失败不能把它盖掉。
                        with suppress(Exception):
                            run.finalize("failed")
                    raise
                else:
                    if run is not None:
                        run.finalize("failed" if failed else "completed")

        PlanAgent.__name__ = "PlanAgent"
        PlanAgent.__qualname__ = "PlanAgent"
        _PLAN_AGENT_CLASS = PlanAgent
    return _PLAN_AGENT_CLASS


_PLAN_AGENT_CLASS: Any = None


def _reply_failed(item: Any) -> bool:
    """最终 ``Msg`` 是否以错误收口（框架把模型错误放在 ``error`` / ``finished_reason``）。"""
    if getattr(item, "error", None):
        return True
    return getattr(item, "finished_reason", None) == "error"


class PlanSession:
    """一轮运行内的 Plan 工厂：按 member id 开独立的 run（队长与成员各一份）。"""

    def __init__(
        self,
        db: Database,
        *,
        target_id: str,
        base_seq: int,
        publish: Publish | None = None,
    ) -> None:
        self._db = db
        self._target_id = target_id
        self._base_seq = int(base_seq)
        self._publish = publish

    @property
    def target_id(self) -> str:
        return self._target_id

    def open(self, member_id: str) -> PlanRun:
        return PlanRun(
            self._db,
            target_id=self._target_id,
            member_id=member_id,
            base_seq=self._base_seq,
            publish=self._publish,
        )


class PlanRun:
    """一次成员轮次的 Plan：装载最近清单、提供可写上下文、收口回写并广播 ``plan_update``。"""

    def __init__(
        self,
        db: Database,
        *,
        target_id: str,
        member_id: str,
        base_seq: int,
        publish: Publish | None = None,
    ) -> None:
        self._db = db
        self._member_id = member_id
        self._publish = publish
        self._before = {
            task["id"]: task for task in db.latest_plan_tasks(target_id=target_id, member_id=member_id)
        }
        self._run = db.start_plan_run(target_id=target_id, member_id=member_id, base_seq=base_seq)
        self.tasks_context = _task_context(list(self._before.values()))
        self._finished = False

    @property
    def plan_run_id(self) -> str:
        return str(self._run["id"])

    def finalize(self, status: str) -> None:
        """以本 run 为界回写当前清单，并广播创建/更新/删除。

        只回写**当前存活的**任务：``deleted`` 是 AgentScope 的删除动作，在快照里表现为缺席，
        因此不落 ``plan_task`` 行；SSE 用一条 ``state=deleted`` 的帧说明它刚被删除。
        """
        if self._finished:
            return
        self._finished = True
        currently = list(self.tasks_context.tasks)
        used: set[int] = set()
        after = [_task_payload(item, _next_seq(item, used)) for item in currently]
        self._db.finish_plan_run(
            self.plan_run_id,
            status="completed" if status == "completed" else "failed",
            tasks=after,
        )
        after_by_id = {task["id"]: task for task in after}
        for task in after:
            previous = self._before.get(task["id"])
            if previous is None or _plan_fields(previous) != _plan_fields(task):
                self._emit(task)
        for task_id, task in self._before.items():
            if task_id not in after_by_id:
                self._emit({**task, "state": "deleted", "deleted_at": utc_now()})

    def _emit(self, task: Mapping[str, Any]) -> None:
        if self._publish is None:
            return
        event = plan_update(plan_run_id=self.plan_run_id, member_id=self._member_id, task=task)
        try:
            self._publish(event)
        except Exception:  # 直播是观察面：发布失败不能改写这一轮的回写结果
            _log.debug("plan_update publish failed", exc_info=True)


def _task_context(tasks: Sequence[Mapping[str, Any]]) -> Any:
    """把库里的任务行还原成 AgentScope 的 ``TaskContext``（``id`` 与 ``seq`` 一一对应）。"""
    from agentscope.state import AgentState, Task, TaskContext

    return AgentState(
        tasks_context=TaskContext(
            tasks=[
                Task.model_validate(
                    {
                        "id": str(task["id"]),
                        "subject": str(task["subject"]),
                        "description": str(task["description"]),
                        "state": str(task["state"]),
                        "owner": task["owner"],
                        "blocks": list(task["blocks"]),
                        "blocked_by": list(task["blocked_by"]),
                        "metadata": dict(task["metadata"]),
                        "created_at": str(task["created_at"]),
                    }
                )
                for task in tasks
            ]
        )
    ).tasks_context


def _next_seq(task: Any, used: set[int]) -> int:
    """``plan_task.seq`` 取框架任务 id（数字字符串）；异常 id 退回下一个未占用序号。"""
    try:
        candidate = int(str(task.id))
    except (TypeError, ValueError):
        candidate = 0
    if candidate < 1 or candidate in used:
        candidate = 1
        while candidate in used:
            candidate += 1
    used.add(candidate)
    return candidate


def _task_payload(task: Any, seq: int) -> dict[str, Any]:
    """AgentScope ``Task`` → ``plan_task`` 行；``seq`` 保留框架的数字 id。"""
    now = utc_now()
    return {
        "id": str(seq),
        "seq": seq,
        "subject": str(task.subject),
        "description": str(task.description),
        "state": str(getattr(task.state, "value", task.state)),
        "owner": task.owner,
        "blocks": list(task.blocks),
        "blocked_by": list(task.blocked_by),
        "metadata": dict(task.metadata),
        "created_at": str(getattr(task, "created_at", "") or now),
        "updated_at": now,
    }


def _plan_fields(task: Mapping[str, Any]) -> tuple[Any, ...]:
    """比较「内容」而不是时间戳：同一份清单的装载不得产生一次假更新。"""
    return (
        task.get("subject"),
        task.get("description"),
        task.get("state"),
        task.get("owner"),
        tuple(task.get("blocks") or ()),
        tuple(task.get("blocked_by") or ()),
        tuple(sorted((task.get("metadata") or {}).items())),
    )
