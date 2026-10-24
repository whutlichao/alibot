"""#263 每轮 system prompt 的运行环境段：工作区 / 后端 / 权限档 / 长期记忆形态。

判据落在**真的会被送进模型的 system prompt**上：所有用例都跑真实 ``AgentScopeAgentSeam``，
只把模型换成进程内脚本替身（``_RecordingModel``），断言它收到的 system 消息。这样测的不是
手抄的文案副本，而是本轮装配出来的那段文本。

四种事实组合：无工作区 + ``reme``；local + ``off``；docker；team 队长与成员各自的事实。
另外守住位置：段在 ``你的角色：…`` 之后、回应 rubric / team 说明之前。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse
from alibot.agent_seam import AgentScopeAgentSeam
from alibot.db import open_database
from alibot.team import TeamRoundContext, member_internal_name

ENV_TITLE = "本轮运行环境（以此为准；与记忆内容冲突时以本段为准）"
NO_WORKSPACE = "工作区：未配置（没有项目根；不能读写项目文件，只有当前会话的卸载目录可读）"
REME_ON = "长期记忆：已开启（ReMe；系统自动记录和检索，不由文件工具读写；不要声称或猜测它的路径）"
MEMORY_OFF = "长期记忆：未开启"
NO_FABRICATION = "不要编造本段没有给出的路径。"
CONFIG_PROMPT = "配置段正文：你是严谨的工程师。"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HOME 指向临时目录：默认全局根不可能落到真实 ``~/.alibot``。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def _human(body: str = "开始吧") -> dict[str, Any]:
    return {"seq": 1, "kind": "human", "body": body, "author": {"id": "owner"}}


class _RecordingModel(ChatModelBase):
    """进程内脚本模型：记录每次请求的 system 文本，并按脚本回包。"""

    def __init__(self, script: list[list[ChatResponse]], systems: list[str]) -> None:
        super().__init__(
            credential=None,  # type: ignore[arg-type]
            model="scripted",
            parameters=ChatModelBase.Parameters(),
        )
        self.formatter = OpenAIChatFormatter()
        self.script = list(script)
        self.systems = systems
        self.calls = 0

    async def _call_api(self, *args: Any, **kwargs: Any):
        self.systems.append(_messages_text(kwargs.get("messages") or []))
        chunks = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1

        async def stream():
            for chunk in chunks:
                yield chunk

        return stream()


def _messages_text(messages: list[Any]) -> str:
    parts: list[str] = []
    for message in messages:
        if getattr(message, "role", "") != "system":
            continue
        content = getattr(message, "content", "")
        if isinstance(content, str):
            parts.append(content)
            continue
        for block in content or []:
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            if text:
                parts.append(str(text))
    return "\n".join(parts)


def _text(text: str) -> list[ChatResponse]:
    return [ChatResponse(content=[TextBlock(text=text)], is_last=True)]


def _team_assign(member_id: str, prompt: str) -> list[ChatResponse]:
    block = ToolCallBlock(
        id="call-263",
        name="TeamAssign",
        input=json.dumps({"member": member_internal_name(member_id), "prompt": prompt}),
    )
    return [
        ChatResponse(content=[block], is_last=False),
        ChatResponse(content=[block], is_last=True),
    ]


class _PromptSeam(AgentScopeAgentSeam):
    """真实适配器 + 按构造顺序取用的脚本模型队列（队长在前、成员随后）。"""

    def __init__(self, models: list[ChatModelBase], **kwargs: Any) -> None:
        super().__init__(timeout_seconds=10.0, **kwargs)
        self._models = list(models)

    def _build_model(self, provider: str, model_name: str, stream: bool = False) -> Any:
        return self._models.pop(0)


class _NoMountPromptSeam(_PromptSeam):
    """docker 事实只看 ``WorkspaceSettings``：不真的起容器（机器上有没有 daemon 不影响判据）。"""

    async def _runtime_mount(self, *args: Any, **kwargs: Any) -> Any:
        return None


def _seed(
    tmp_path: Path,
    *,
    workspace: bool = False,
    backend: str | None = None,
    permission_mode: str | None = None,
    memory_mode: str = "off",
) -> tuple[Any, dict[str, Any], dict[str, Any], Path]:
    project = tmp_path / "project"
    project.mkdir()
    database = open_database(tmp_path / "data")
    agent = database.create_agent(
        "小明",
        "工程师",
        "openai:stub-model",
        CONFIG_PROMPT,
        permission_mode=permission_mode,
    )
    if memory_mode != "off":
        agent = database.update_agent(agent["id"], memory_mode=memory_mode)
    channel = database.create_channel("项目频道", member_ids=[agent["id"]])
    if workspace:
        database.update_channel_config(channel["id"], workspace_root=str(project), workspace_backend=backend)
    return database, database.get_agent(agent["id"]), channel, project


# ─── 1. 无工作区 + reme：说清「未配置」与记忆形态，且不出现任何路径 ─────────
def test_unconfigured_workspace_and_reme_memory_are_stated_without_paths(tmp_path: Path) -> None:
    database, agent, channel, _project = _seed(tmp_path, memory_mode="reme")
    try:
        systems: list[str] = []
        seam = _PromptSeam([_RecordingModel([_text("已处理。")], systems)])
        outcome = seam.run_round(
            database.get_agent(agent["id"]),
            [_human()],
            mentioned=False,
            target_id=channel["id"],
        )
        assert outcome.status == "replied", outcome.reason

        prompt = systems[0]
        assert prompt.count(ENV_TITLE) == 1
        assert NO_WORKSPACE in prompt
        assert REME_ON in prompt
        assert MEMORY_OFF not in prompt
        assert NO_FABRICATION in prompt
        # 环境段只写事实，不写任何记忆 / 数据 / 会话卸载目录的路径。
        for path_like in ("alibot/data", "memory/", "~/.alibot", ".alibot/", "/sessions/"):
            assert path_like not in prompt, f"prompt 不该出现路径字样：{path_like}"
        # 位置：配置 → 身份句 → 角色 → 运行环境 → 场景 rubric。
        assert prompt.index(CONFIG_PROMPT) < prompt.index("你的角色：工程师") < prompt.index(ENV_TITLE)
        assert prompt.index(ENV_TITLE) < prompt.index("回应纪律")
    finally:
        database.close()


# ─── 2. local + off：项目根、后端、权限档、记忆关闭 ────────────────────────
def test_local_workspace_reports_root_backend_and_permission(tmp_path: Path) -> None:
    database, agent, channel, project = _seed(tmp_path, workspace=True, permission_mode="trusted-writes")
    try:
        systems: list[str] = []
        seam = _PromptSeam(
            [_RecordingModel([_text("已处理。")], systems)],
            workspace_of=database.workspace_settings_for_target,
        )
        outcome = seam.run_round(
            database.get_agent(agent["id"]),
            [_human()],
            mentioned=False,
            target_id=channel["id"],
        )
        assert outcome.status == "replied", outcome.reason

        prompt = systems[0]
        assert f"工作区（文件工具的根）：{project}" in prompt
        assert "后端：local" in prompt
        assert "权限档：trusted-writes" in prompt
        assert MEMORY_OFF in prompt
        assert REME_ON not in prompt
        assert NO_FABRICATION in prompt
        assert prompt.index("你的角色：工程师") < prompt.index(ENV_TITLE) < prompt.index("回应纪律")
    finally:
        database.close()


# ─── 3. docker：模型可见根是 /workspace，宿主路径不进 prompt ─────────────────
def test_docker_workspace_reports_the_container_root_not_the_host_path(tmp_path: Path) -> None:
    database, agent, channel, project = _seed(
        tmp_path, workspace=True, backend="docker", permission_mode="read-only"
    )
    try:
        systems: list[str] = []
        seam = _NoMountPromptSeam(
            [_RecordingModel([_text("已处理。")], systems)],
            workspace_of=database.workspace_settings_for_target,
        )
        outcome = seam.run_round(
            database.get_agent(agent["id"]),
            [_human()],
            mentioned=False,
            target_id=channel["id"],
        )
        assert outcome.status == "replied", outcome.reason

        prompt = systems[0]
        assert "工作区（文件工具的根）：/workspace" in prompt
        assert "后端：docker" in prompt
        assert "权限档：read-only" in prompt
        assert str(project) not in prompt, "宿主项目根不得写进 docker 轮次的 prompt"
    finally:
        database.close()


# ─── 4. team：队长与成员各自拿到自己的事实 ──────────────────────────────────
def test_team_leader_and_member_each_get_their_own_environment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    database = open_database(tmp_path / "data")
    try:
        leader = database.create_agent(
            "队长", "协调", "openai:stub-model", "队长配置段。", permission_mode="trusted-writes"
        )
        member = database.create_agent(
            "成员", "执行", "openai:stub-model", "成员配置段。", permission_mode="read-only"
        )
        member = database.update_agent(member["id"], memory_mode="reme")
        channel = database.create_channel("团队频道", member_ids=[leader["id"], member["id"]])
        database.update_channel_config(channel["id"], workspace_root=str(project))

        leader_systems: list[str] = []
        member_systems: list[str] = []
        seam = _PromptSeam(
            [
                _RecordingModel(
                    [_team_assign(member["id"], "写结论"), _text("队长最终答复")],
                    leader_systems,
                ),
                _RecordingModel([_text("成员结论")], member_systems),
            ],
            workspace_of=database.workspace_settings_for_target,
        )
        team_run = database.create_team_run(
            target_id=channel["id"], leader_member_id=leader["id"], base_seq=1
        )
        context = TeamRoundContext(
            target_id=channel["id"],
            leader_id=leader["id"],
            members=(database.get_agent(member["id"]),),
            team_run_id=team_run["id"],
            budget=database,
        )

        outcome = seam.run_round_stream(
            database.get_agent(leader["id"]),
            [_human("开工")],
            mentioned=False,
            target_id=channel["id"],
            team=context,
        )
        assert outcome.status == "replied", outcome.reason
        assert member_systems, "成员没有被 TeamAssign 驱动"

        leader_prompt = leader_systems[0]
        assert leader_prompt.count(ENV_TITLE) == 1
        assert f"工作区（文件工具的根）：{project}" in leader_prompt
        assert "权限档：trusted-writes" in leader_prompt
        assert MEMORY_OFF in leader_prompt
        assert REME_ON not in leader_prompt
        # 队长：角色 → 运行环境 → team 队长说明。
        assert (
            leader_prompt.index("你的角色：协调")
            < leader_prompt.index(ENV_TITLE)
            < leader_prompt.index("你是这个 team 频道的队长")
        )

        member_prompt = member_systems[0]
        assert member_prompt.count(ENV_TITLE) == 1
        assert f"工作区（文件工具的根）：{project}" in member_prompt
        assert "权限档：read-only" in member_prompt
        assert REME_ON in member_prompt
        assert MEMORY_OFF not in member_prompt
        # 成员：角色 → 运行环境 → team 成员说明。
        assert (
            member_prompt.index("你的角色：执行")
            < member_prompt.index(ENV_TITLE)
            < member_prompt.index("你是 team 模式频道里的成员")
        )
    finally:
        database.close()
