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
            """带「分派前裁决」接缝的团队流水线。

            T1 只提供结构：``_authorize_assignments`` 原样返回全部调用。T2（#143）在这里覆写
            预算的**原子**裁决，未获授权的调用必须按原顺序追加 ``ERROR`` ``ToolResultBlock``，
            且**不进入**基类的 ``run_group`` —— 基类一旦收到调用就会运行成员。
            """

            def __init__(
                self,
                *,
                target_id: str,
                name_to_member: dict[str, str],
                task_id: str | None = None,
                **kwargs: Any,
            ) -> None:
                super().__init__(**kwargs)
                self.target_id = target_id
                self.task_id = task_id
                # 公开给 T2/T3 的映射：派生名 → member id。事件归属与分派记录都读它。
                self.name_to_member = dict(name_to_member)
                self._name_to_member = self.name_to_member

            def member_of(self, tool_call: Any) -> str:
                """把一条 ``TeamAssign`` 调用的成员名解析成 member id（不猜）。"""
                return self.name_to_member[self._parse(tool_call)["member"]]

            async def _run_assignments(
                self,
                tool_calls: list[Any],
                results: list[Any],
                resumed: Any = None,
            ):
                authorized = self._authorize_assignments(tool_calls, results)
                async for event in super()._run_assignments(authorized, results, resumed):
                    yield event

            def _authorize_assignments(self, tool_calls: list[Any], results: list[Any]) -> list[Any]:
                """T2 的独占覆写点：默认原样放行（T1 不扣预算、不拦截）。

                ``results`` 是基类收集 ``ToolResultBlock`` 的同一个列表；拦截实现要把被拒调用
                的错误结果按调用顺序补进去，基类才会把它们按顺序交给队长。
                """
                return list(tool_calls)

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
) -> Any:
    """把**这一轮**的队长与成员装配成 ``BudgetedTeamPipeline``（D11）。

    ``members`` 是 ``(成员行, 已构造的 Agent)`` 对；内部名由 member id 派生（D10），
    因此 ``display_name`` 重名不会让构造失败，队长也不会与成员撞名。
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
        task_id=task_id,
        name_to_member=name_to_member,
    )
