"""team 模式的运行装配：成员名派生、每轮 pipeline 构建与预算拦截点。

三条裁定落在本模块（ADR-0018 / ADR-0019 / ADR-0020）：

- **D10 内部命名**：``TeamPipeline`` 要求成员名唯一且不等于队长名，而本仓 ``display_name``
  可以重复。pipeline 内部名一律由 **member id** 派生（``member_internal_name``），显示名只进
  成员描述，不参与唯一性。派生名不落库、不显示、不改成员身份。
- **D11 动态成员**：pipeline **每轮**按当前频道成员重建。本模块只提供「把这一轮的成员装配成
  一个 pipeline」的那一步；调用方每轮重新调用，因此成员加入/移除从下一轮生效。
- **T2 的拦截点**：``BudgetedTeamPipeline`` 覆写 ``_run_assignments``，把「这次分派要不要运行
  成员」收在一个可覆写的方法 ``_authorize_assignments`` 里。T1 原样放行（不实现预算扣减，
  那是 #143 / T2 的票面）；T2 只需覆写这个方法，不必再碰 ``TeamPipeline`` 的私有调用形状。

``agentscope`` 仍然**按需**导入：本模块在构造 pipeline 之前不 import 框架，于是只跑脚本化
替身或只读频道配置的路径不会因为加载框架而付代价（与 ``agent_seam`` 的纪律同源）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .agent_seam import member_internal_name

__all__ = [
    "TEAM_SCOPE",
    "TeamRoundContext",
    "build_team_pipeline",
    "member_description",
    "member_internal_name",
    "team_pipeline_class",
]

# team 模式覆盖该频道的 **channel 型 target 与全部 task target**（D23=B）。
# 它是一份固定读数，不是可写配置：PATCH 不接受它。
TEAM_SCOPE = "channel+task"


@dataclass(frozen=True)
class TeamRoundContext:
    """一轮 team 运行需要的成员面上下文（每轮重新构造，D11）。

    ``members`` 是**当前频道**的 Agent 成员行（不含队长、不含已删除成员），由编排在轮次开始
    时从库里读出；pipeline 从这份快照建出来，轮次中途的成员变化留到下一轮。
    """

    target_id: str
    leader_id: str
    members: tuple[dict[str, Any], ...]
    task_id: str | None = None
    #: 这一轮的 ``team_run`` 行（编排在开轮时创建）；分派运行记录挂在它下面。
    team_run_id: str | None = None
    #: 预算正本（生产是 :class:`alibot.db.Database`）：``try_consume_jump`` 与
    #: ``create/finish_team_assignment`` 的唯一落点。适配层只当它是窄接口，不 import 库。
    budget: Any = None

    @property
    def name_to_member(self) -> dict[str, str]:
        """D10 派生名 → member id（队长与成员都在表里，事件归属只认它）。"""
        names = {member_internal_name(self.leader_id): self.leader_id}
        for member in self.members:
            member_id = str(member["id"])
            names[member_internal_name(member_id)] = member_id
        return names


def member_description(member: Mapping[str, Any]) -> str:
    """队长在 ``TeamAssign`` 工具描述里看到的成员说明（显示名 + 角色，不参与唯一性）。"""
    name = str(member.get("display_name") or "").strip()
    role = str(member.get("role") or "").strip()
    if name and role:
        return f"{name}（{role}）"
    return name or role or "团队成员"


_PIPELINE_CLASS: Any = None


def team_pipeline_class() -> Any:
    """返回 ``BudgetedTeamPipeline``（首次调用时才 import 框架并建类）。"""
    global _PIPELINE_CLASS
    if _PIPELINE_CLASS is None:
        from agentscope.pipeline import TeamPipeline

        class BudgetedTeamPipeline(TeamPipeline):
            """带「分派前预算裁决」的团队流水线。

            ``_run_assignments`` 在成员运行前逐条裁决：获授权的 calls 才交给基类
            ``run_group``；未获授权的调用按原顺序得到 ``ERROR`` ``ToolResultBlock``，
            成员**不会**被运行。``target_budget.used`` 是跳数的唯一正本（ADR-0019）。
            """

            def __init__(
                self,
                *,
                target_id: str,
                name_to_member: dict[str, str],
                leader_id: str | None = None,
                task_id: str | None = None,
                team_run_id: str | None = None,
                budget: Any = None,
                **kwargs: Any,
            ) -> None:
                super().__init__(**kwargs)
                self.target_id = target_id
                self.task_id = task_id
                self.team_run_id = team_run_id
                # 预算正本由装配点注入（生产 = ``Database``）；T2 在成员运行前逐条裁决。
                self.budget = budget
                # 公开给 T2/T3 的映射：派生名 → member id。事件归属与分派记录都读它。
                self.name_to_member = dict(name_to_member)
                self._name_to_member = self.name_to_member
                self.leader_id = leader_id or self.name_to_member.get(self.leader.name, self.leader.name)
                # 同一轮里 call.id → 已落库的分派记录。HITL 恢复会把同一个 call 再送进来，
                # 这张表保证**不重复扣跳**、也不重复写运行记录。
                self._assignments: dict[str, dict[str, Any]] = {}

            def member_of(self, tool_call: Any) -> str:
                """把一条 ``TeamAssign`` 调用的成员名解析成 member id（不猜）。"""
                return self.name_to_member[self._parse(tool_call)["member"]]

            async def _run_assignments(
                self,
                tool_calls: list[Any],
                results: list[Any],
                resumed: Any = None,
            ):
                """逐条裁决后运行获授权的分派，并按**原始调用顺序**合并结果。

                基类只把 ``authorized`` 交给 ``run_group``；被拒调用的 ERROR 结果由
                ``_authorize_assignments`` 直接放进 ``rejected``，最后与基类结果一起按
                ``tool_calls`` 顺序写回 ``results`` —— 队长看到的顺序与它调用的顺序一致。
                """
                rejected: list[Any] = []
                authorized = self._authorize_assignments(tool_calls, rejected)
                authorized_results: list[Any] = []
                settled = False

                def settle(fallback: str | None = None, failure: BaseException | None = None) -> None:
                    nonlocal settled
                    if settled:
                        return
                    settled = True
                    self._finish_assignments(
                        authorized, authorized_results, fallback=fallback, failure=failure
                    )

                try:
                    async for event in super()._run_assignments(authorized, authorized_results, resumed):
                        yield event
                except asyncio.CancelledError:
                    settle("interrupted")
                    raise
                except Exception as error:
                    settle("failed", error)
                    raise
                else:
                    settle()
                    order = {call.id: index for index, call in enumerate(tool_calls)}
                    results.extend(
                        sorted(
                            [*authorized_results, *rejected],
                            key=lambda result: order.get(result.id, len(order)),
                        )
                    )
                finally:
                    # 消费者提前关闭生成器（或取消）时也要把已扣跳的分派收口成终态。
                    settle("interrupted")

            def _authorize_assignments(self, tool_calls: list[Any], results: list[Any]) -> list[Any]:
                """T2 的预算裁决：每次分派对**对应 target** 原子扣 1 跳。

                - 已裁决过的 ``call.id``（HITL 恢复）复用原裁决，不重复扣跳；
                - 获授权 ⇒ ``team_assignment(state="started", jump_consumed=1)``；
                - 耗尽 ⇒ 直接追加 ``ERROR`` ``ToolResultBlock``、不进入基类 ``run_group``，
                  并落 ``team_assignment(state="budget-exhausted", jump_consumed=0)``。
                """
                authorized: list[Any] = []
                for tool_call in tool_calls:
                    previous = self._assignments.get(tool_call.id)
                    if previous is not None:
                        if previous["jump_consumed"]:
                            authorized.append(tool_call)
                        else:
                            results.append(self._budget_exhausted_result(tool_call))
                        continue

                    member_id = self.member_of(tool_call)
                    prompt = str(self._parse(tool_call).get("prompt") or "")
                    decision = self._consume_jump()
                    self._assignments[tool_call.id] = self._record_assignment(
                        tool_call, member_id, prompt, decision
                    )
                    if decision["ok"]:
                        authorized.append(tool_call)
                    else:
                        results.append(self._budget_exhausted_result(tool_call))
                return authorized

            def _consume_jump(self) -> dict[str, Any]:
                if self.budget is None:
                    # 没有预算正本就运行成员 = 不可审计的越界。宁可整轮失败，也不静默放行。
                    raise RuntimeError("team budget ledger is not configured; refusing an unmetered dispatch")
                return self.budget.try_consume_jump(self.target_id)

            def _record_assignment(
                self,
                tool_call: Any,
                member_id: str,
                prompt: str,
                decision: dict[str, Any],
            ) -> dict[str, Any]:
                if self.team_run_id is None:
                    raise RuntimeError("team_run_id is required to record a team assignment")
                row = self.budget.create_team_assignment(
                    team_run_id=self.team_run_id,
                    target_id=self.target_id,
                    task_id=self.task_id,
                    leader_member_id=self.leader_id,
                    member_id=member_id,
                    tool_call_id=tool_call.id,
                    prompt=prompt,
                    state="started" if decision["ok"] else "budget-exhausted",
                    jump_consumed=1 if decision["ok"] else 0,
                )
                return {"id": str(row["id"]), "jump_consumed": int(row["jump_consumed"])}

            @staticmethod
            def _budget_exhausted_result(tool_call: Any) -> Any:
                from agentscope.message import TextBlock, ToolResultBlock, ToolResultState

                return ToolResultBlock(
                    id=tool_call.id,
                    name="TeamAssign",
                    output=[TextBlock(text="预算耗尽：本次分派未运行成员")],
                    state=ToolResultState.ERROR,
                )

            def _finish_assignments(
                self,
                authorized: list[Any],
                authorized_results: list[Any],
                *,
                fallback: str | None = None,
                failure: BaseException | None = None,
            ) -> None:
                """把本轮获授权的分派收口成终态（失败/中断同样保留 ``jump_consumed=1``）。"""
                from agentscope.message import ToolResultState

                by_call = {result.id: result for result in authorized_results}
                for tool_call in authorized:
                    record = self._assignments.get(tool_call.id)
                    if record is None or not record["jump_consumed"]:
                        continue
                    result = by_call.get(tool_call.id)
                    if result is None:
                        state = fallback or "interrupted"
                        excerpt = ""
                        error = str(failure) if state == "failed" and failure is not None else ""
                    else:
                        state = self._state_for(result, ToolResultState)
                        excerpt = self._result_excerpt(result)
                        error = excerpt if state == "failed" else ""
                    self.budget.finish_team_assignment(
                        record["id"],
                        state=state,
                        reply_excerpt=excerpt,
                        error=error,
                    )

            @staticmethod
            def _state_for(result: Any, states: Any) -> str:
                if result.state == states.SUCCESS:
                    return "succeeded"
                if result.state == states.INTERRUPTED:
                    return "interrupted"
                return "failed"

            @staticmethod
            def _result_excerpt(result: Any, limit: int = 500) -> str:
                from agentscope.message import TextBlock

                output = result.output
                if isinstance(output, str):
                    return output[:limit]
                texts = [block.text for block in output if isinstance(block, TextBlock) and block.text]
                return "\n".join(texts)[:limit]

        BudgetedTeamPipeline.__name__ = "BudgetedTeamPipeline"
        BudgetedTeamPipeline.__qualname__ = "BudgetedTeamPipeline"
        _PIPELINE_CLASS = BudgetedTeamPipeline
    return _PIPELINE_CLASS


def build_team_pipeline(
    *,
    leader_agent: Any,
    leader_id: str,
    members: Sequence[tuple[dict[str, Any], Any]],
    target_id: str,
    task_id: str | None = None,
    team_run_id: str | None = None,
    budget: Any = None,
) -> Any:
    """把**这一轮**的队长与成员装配成 ``BudgetedTeamPipeline``（D11）。

    ``members`` 是 ``(成员行, 已构造的 Agent)`` 对；内部名由 member id 派生（D10），
    因此 ``display_name`` 重名不会让构造失败，队长也不会与成员撞名。

    ``budget`` 是预算正本（生产 = ``Database``），``team_run_id`` 是这一轮的运行记录。
    二者由编排从 :class:`TeamRoundContext` 传下来；没有 TeamAssign 的轮次用不到它们。
    """
    from agentscope.pipeline import TeamMember

    entries = [TeamMember(agent=agent, description=member_description(member)) for member, agent in members]
    name_to_member = {member_internal_name(leader_id): leader_id}
    for member, _agent in members:
        member_id = str(member["id"])
        name_to_member[member_internal_name(member_id)] = member_id
    pipeline_class = team_pipeline_class()
    return pipeline_class(
        leader=leader_agent,
        members=entries,
        target_id=target_id,
        leader_id=leader_id,
        task_id=task_id,
        team_run_id=team_run_id,
        budget=budget,
        name_to_member=name_to_member,
    )
