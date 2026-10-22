"""#230 规则三段拼装：全局基座 → ``agent_config.system_prompt`` → 项目补充。

判据落在**真的会被送进模型的 system prompt**上：单 Agent 与私信轮次用进程内 HTTP stub
provider 读真实请求体；team 轮次用进程内脚本模型（与 ``test_team_visibility.py`` 同源，
因为只有脚本模型能在同一轮里真的触发 ``TeamAssign``）。所有用例的全局根与 HOME 都指向
临时目录，绝不读写真实的 ``~/.alibot``。

SOP 执行器**不纳入**这三段规则：它持有的是内部执行身份，不是「同一个 Agent 在某个频道里的
轮次」（票面 Q3 裁决）。这一条用一个独立用例守护。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ToolCallBlock, ToolResultState, UserMsg
from agentscope.model import ChatModelBase, ChatResponse
from alibot.agent_seam import AgentScopeAgentSeam
from alibot.db import open_database
from alibot.team import TeamRoundContext, member_internal_name
from alibot.workspace import (
    DOCKER_GLOBAL_HOME,
    WorkspaceSettings,
    assemble_local_workspace,
    docker_global_member_home,
    docker_global_skills_home,
    global_home_root,
    global_member_home,
    resolve_global_read_path,
    session_partition_id,
)

GLOBAL_RULE = "全局基座正文：先读仓库约定。\n"
PROJECT_RULE = "项目补充正文：本项目一律用中文提交。\n"
CONFIG_PROMPT = "配置段正文：你是严谨的工程师。"


def _human(body: str = "开始吧") -> dict[str, Any]:
    return {"seq": 1, "kind": "human", "body": body, "author": {"id": "owner"}}


class _PlainReplyHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容端点：记录请求体并回一条固定正文。"""

    protocol_version = "HTTP/1.1"
    request_bodies: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length))
        type(self).request_bodies.append(body)
        payload = json.dumps(
            {
                "id": "chatcmpl-issue-230",
                "object": "chat.completion",
                "created": 0,
                "model": "stub-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "已处理。"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture
def stub_provider() -> Iterator[str]:
    _PlainReplyHandler.request_bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PlainReplyHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    os.environ["OPENAI_API_KEY"] = "sk-issue-230-stub"
    os.environ["OPENAI_BASE_URL"] = base_url
    try:
        yield base_url
    finally:
        os.environ.pop("OPENAI_BASE_URL", None)
        os.environ.pop("OPENAI_API_KEY", None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HOME 指向临时目录：任何默认全局根都不可能落到真实 ``~/.alibot``。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def _global_root(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".alibot"


def _write_global_rule(global_root: Path, member_id: str, text: str) -> Path:
    path = global_root / "agents" / member_id / "rules" / "AGENTS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_project_rule(project_root: Path, member_id: str, text: str) -> Path:
    path = project_root / ".alibot" / "rules" / member_id / "AGENTS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "".join(parts)


def _system_text(body: dict[str, Any]) -> str:
    return "\n".join(
        _content_text(message.get("content"))
        for message in body.get("messages") or []
        if message.get("role") == "system"
    )


def _seed(tmp_path: Path, *, workspace: bool = True) -> tuple[Any, dict[str, Any], dict[str, Any], Path]:
    project = tmp_path / "project"
    project.mkdir()
    database = open_database(tmp_path / "data")
    agent = database.create_agent("小明", "工程师", "openai:stub-model", CONFIG_PROMPT)
    channel = database.create_channel("项目频道", member_ids=[agent["id"]])
    if workspace:
        database.update_channel_config(channel["id"], workspace_root=str(project))
    return database, agent, channel, project


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


class _PipelineSeam(AgentScopeAgentSeam):
    """真实适配器 + 按构造顺序取用的脚本模型队列（队长在前、成员随后）。"""

    def __init__(self, models: list[ChatModelBase], **kwargs: Any) -> None:
        super().__init__(timeout_seconds=10.0, **kwargs)
        self._models = list(models)

    def _build_model(self, provider: str, model_name: str, stream: bool = False) -> Any:
        return self._models.pop(0)


def _text(text: str) -> list[ChatResponse]:
    return [ChatResponse(content=[TextBlock(text=text)], is_last=True)]


def _team_assign(member_id: str, prompt: str) -> list[ChatResponse]:
    block = ToolCallBlock(
        id="call-230",
        name="TeamAssign",
        input=json.dumps({"member": member_internal_name(member_id), "prompt": prompt}),
    )
    return [
        ChatResponse(content=[block], is_last=False),
        ChatResponse(content=[block], is_last=True),
    ]


# ─── 1. 单 Agent：三段按唯一顺序出现，各带可读标签 ──────────────────────────
def test_system_prompt_has_the_three_rule_segments_in_order(tmp_path: Path, stub_provider: str) -> None:
    database, agent, channel, project = _seed(tmp_path)
    try:
        _write_global_rule(_global_root(tmp_path), agent["id"], GLOBAL_RULE)
        _write_project_rule(project, agent["id"], PROJECT_RULE)
        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
            global_home=str(_global_root(tmp_path)),
        )

        outcome = seam.run_round(
            database.get_agent(agent["id"]),
            [_human()],
            mentioned=False,
            target_id=channel["id"],
        )
        assert outcome.status == "replied", outcome.reason

        prompt = _system_text(_PlainReplyHandler.request_bodies[0])
        assert f"全局基座：\n{GLOBAL_RULE}" in prompt
        assert f"项目补充：\n{PROJECT_RULE}" in prompt
        assert prompt.index(GLOBAL_RULE) < prompt.index(CONFIG_PROMPT) < prompt.index(PROJECT_RULE)
        # 既有角色句与回应 rubric 仍在配置段之后，措辞不变。
        assert "你的角色：工程师" in prompt
        assert prompt.index("你的角色：工程师") > prompt.index(PROJECT_RULE)
        assert prompt.index("回应纪律") > prompt.index(CONFIG_PROMPT)
    finally:
        database.close()


# ─── 2. 私信：没有项目根，只有前两段 ─────────────────────────────────────
def test_direct_message_has_only_the_global_base_and_the_config(tmp_path: Path, stub_provider: str) -> None:
    database, agent, _channel, project = _seed(tmp_path, workspace=False)
    try:
        dm, _created = database.create_dm(agent["id"])
        _write_global_rule(_global_root(tmp_path), agent["id"], GLOBAL_RULE)
        # 项目目录里就算有同名成员的项目规则，私信也没有项目根，因此不得读取。
        _write_project_rule(project, agent["id"], PROJECT_RULE)
        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
            global_home=str(_global_root(tmp_path)),
        )

        outcome = seam.run_round(
            database.get_agent(agent["id"]),
            [_human("你好")],
            mentioned=False,
            target_id=dm["id"],
            direct_message=True,
        )
        assert outcome.status == "replied", outcome.reason

        prompt = _system_text(_PlainReplyHandler.request_bodies[0])
        assert f"全局基座：\n{GLOBAL_RULE}" in prompt
        assert prompt.index(GLOBAL_RULE) < prompt.index(CONFIG_PROMPT)
        assert "项目补充" not in prompt
        assert PROJECT_RULE not in prompt
    finally:
        database.close()


# ─── 3. team：队长与成员两条路径拼装同一三段 ─────────────────────────────
def test_team_leader_and_member_each_get_the_three_rule_segments(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    database = open_database(tmp_path / "data")
    try:
        leader = database.create_agent("队长", "协调", "openai:stub-model", CONFIG_PROMPT)
        member = database.create_agent("成员", "执行", "openai:stub-model", "成员配置段。")
        channel = database.create_channel("团队频道", member_ids=[leader["id"], member["id"]])
        database.update_channel_config(channel["id"], workspace_root=str(project))
        _write_global_rule(_global_root(tmp_path), leader["id"], GLOBAL_RULE)
        _write_global_rule(_global_root(tmp_path), member["id"], "成员全局基座。\n")
        _write_project_rule(project, leader["id"], PROJECT_RULE)
        _write_project_rule(project, member["id"], "成员项目补充。\n")

        leader_systems: list[str] = []
        member_systems: list[str] = []
        seam = _PipelineSeam(
            [
                _RecordingModel(
                    [_team_assign(member["id"], "写结论"), _text("队长最终答复")],
                    leader_systems,
                ),
                _RecordingModel([_text("成员结论")], member_systems),
            ],
            workspace_of=database.workspace_settings_for_target,
            global_home=str(_global_root(tmp_path)),
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

        leader_prompt = leader_systems[0]
        assert f"全局基座：\n{GLOBAL_RULE}" in leader_prompt
        assert f"项目补充：\n{PROJECT_RULE}" in leader_prompt
        assert (
            leader_prompt.index(GLOBAL_RULE)
            < leader_prompt.index(CONFIG_PROMPT)
            < leader_prompt.index(PROJECT_RULE)
        )

        assert member_systems, "成员没有被 TeamAssign 驱动"
        member_prompt = member_systems[0]
        assert "全局基座：\n成员全局基座。\n" in member_prompt
        assert "成员配置段。" in member_prompt
        assert "项目补充：\n成员项目补充。\n" in member_prompt
        assert (
            member_prompt.index("成员全局基座。")
            < member_prompt.index("成员配置段。")
            < member_prompt.index("成员项目补充。")
        )
        assert "你是 team 模式频道里的成员" in member_prompt
    finally:
        database.close()


# ─── 4. 缺失 / 不可读 / 非 UTF-8：该段缺席 + 一条可读日志，本轮照常收口 ─────
def _rule_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.message for record in caplog.records if "rule" in record.message]


def test_missing_rule_files_are_absent_with_a_readable_log(
    tmp_path: Path, stub_provider: str, caplog: pytest.LogCaptureFixture
) -> None:
    database, agent, channel, _project = _seed(tmp_path, workspace=True)
    try:
        # 项目根存在，但全局基座与项目补充的目录都没有被创建（全局层是人的维护面）。
        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
            global_home=str(_global_root(tmp_path)),
        )
        caplog.set_level(logging.INFO)
        outcome = seam.run_round(
            database.get_agent(agent["id"]),
            [_human()],
            mentioned=False,
            target_id=channel["id"],
        )
        assert outcome.status == "replied", outcome.reason

        prompt = _system_text(_PlainReplyHandler.request_bodies[0])
        assert "全局基座" not in prompt
        assert "项目补充" not in prompt
        assert CONFIG_PROMPT in prompt
        messages = _rule_messages(caplog)
        assert sum("global layer rule file is absent" in message for message in messages) == 1
        assert sum("project layer rule file is absent" in message for message in messages) == 1
    finally:
        database.close()


@pytest.mark.parametrize("shape", ["non-utf8", "unreadable", "empty"])
def test_broken_rule_files_are_absent_with_a_readable_log(
    tmp_path: Path,
    stub_provider: str,
    caplog: pytest.LogCaptureFixture,
    shape: str,
) -> None:
    if shape == "unreadable" and hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root 无视文件权限位；『不可读』只有非 root 才可构造")
    database, agent, channel, project = _seed(tmp_path)
    try:
        global_path = _global_root(tmp_path) / "agents" / agent["id"] / "rules" / "AGENTS.md"
        global_path.parent.mkdir(parents=True, exist_ok=True)
        project_path = _write_project_rule(project, agent["id"], PROJECT_RULE)
        if shape == "non-utf8":
            global_path.write_bytes(b"\xff\xfe\x00not-utf8")
        elif shape == "unreadable":
            global_path.write_text(GLOBAL_RULE, encoding="utf-8")
            os.chmod(global_path, 0o000)
        else:
            global_path.write_text("", encoding="utf-8")
        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
            global_home=str(_global_root(tmp_path)),
        )
        caplog.set_level(logging.INFO)
        try:
            outcome = seam.run_round(
                database.get_agent(agent["id"]),
                [_human()],
                mentioned=False,
                target_id=channel["id"],
            )
        finally:
            if shape == "unreadable":
                os.chmod(global_path, 0o600)
        assert outcome.status == "replied", outcome.reason

        prompt = _system_text(_PlainReplyHandler.request_bodies[0])
        assert "全局基座" not in prompt, "坏文件必须让该段缺席，不留下空标签"
        assert GLOBAL_RULE not in prompt
        assert f"项目补充：\n{PROJECT_RULE}" in prompt, "坏文件只影响它自己那一段"
        expected = {
            "non-utf8": "global layer rule file is not UTF-8",
            "unreadable": "global layer rule file is unreadable",
            "empty": "global layer rule file is empty",
        }[shape]
        messages = _rule_messages(caplog)
        assert sum(expected in message for message in messages) == 1, messages
        assert project_path.exists()
    finally:
        database.close()


@pytest.mark.parametrize("escape", ["ancestor", "target"])
def test_symlink_outside_the_global_root_is_rejected(
    tmp_path: Path,
    stub_provider: str,
    caplog: pytest.LogCaptureFixture,
    escape: str,
) -> None:
    database, agent, channel, project = _seed(tmp_path)
    try:
        outside = tmp_path / "outside"
        (outside / "rules").mkdir(parents=True)
        (outside / "rules" / "AGENTS.md").write_text("根外秘密。\n", encoding="utf-8")
        member_dir = _global_root(tmp_path) / "agents" / agent["id"]
        _write_project_rule(project, agent["id"], PROJECT_RULE)
        if escape == "ancestor":
            # 祖先目录是指向根外的 symlink：realpath 后整条路径落在全局根之外。
            member_dir.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(outside, member_dir)
        else:
            rules_dir = member_dir / "rules"
            rules_dir.mkdir(parents=True, exist_ok=True)
            os.symlink(outside / "rules" / "AGENTS.md", rules_dir / "AGENTS.md")

        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
            global_home=str(_global_root(tmp_path)),
        )
        caplog.set_level(logging.INFO)
        outcome = seam.run_round(
            database.get_agent(agent["id"]),
            [_human()],
            mentioned=False,
            target_id=channel["id"],
        )
        assert outcome.status == "replied", outcome.reason

        prompt = _system_text(_PlainReplyHandler.request_bodies[0])
        assert "根外秘密" not in prompt
        assert "全局基座" not in prompt
        assert f"项目补充：\n{PROJECT_RULE}" in prompt
        messages = _rule_messages(caplog)
        assert sum("global layer rule path escapes its root" in message for message in messages) == 1
    finally:
        database.close()


# ─── 5. 创建纪律：一轮前后全局根与项目规则目录都不变 ─────────────────────
def _listing(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def test_a_round_neither_creates_nor_changes_the_global_or_project_rule_dirs(
    tmp_path: Path, stub_provider: str
) -> None:
    database, agent, channel, project = _seed(tmp_path)
    try:
        global_root = _global_root(tmp_path)
        _write_global_rule(global_root, agent["id"], GLOBAL_RULE)
        (global_root / "skills" / "shared").mkdir(parents=True)
        (global_root / "skills" / "shared" / "SKILL.md").write_text("公共技能\n", encoding="utf-8")
        before_global = _listing(global_root)
        assert not (project / ".alibot" / "rules").exists()

        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
            global_home=str(global_root),
        )
        outcome = seam.run_round(
            database.get_agent(agent["id"]),
            [_human()],
            mentioned=False,
            target_id=channel["id"],
        )
        assert outcome.status == "replied", outcome.reason

        assert _listing(global_root) == before_global, "全局层是人的维护面：应用不得创建或写入"
        assert not (project / ".alibot" / "rules").exists(), "项目补充目录也不得被自动创建"
    finally:
        database.close()


# ─── 6. 文件工具的根不变：全局层不是工作路径，三档权限档一致 ───────────────
def _run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


async def _latest_chunk(tool: Any, **kwargs: Any) -> Any:
    result = await tool(**kwargs)
    chunks = [chunk async for chunk in result] if hasattr(result, "__aiter__") else [result]
    assert chunks
    return chunks[-1]


@pytest.mark.parametrize("mode", ["read-only", "ask-writes", "trusted-writes"])
def test_global_layer_is_not_a_file_tool_root(tmp_path: Path, mode: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "inside.txt").write_text("项目内文件\n", encoding="utf-8")
    global_file = _write_global_rule(_global_root(tmp_path), "mem_guard", GLOBAL_RULE)
    os.symlink(global_file, project / "global_link.txt")
    mount = _run(
        assemble_local_workspace(
            WorkspaceSettings(root=str(project), permission_mode=mode),
            member_id="mem_guard",
            session_id=session_partition_id("mem_guard", "ch_guard"),
        )
    )
    read = next(tool for tool in mount.tools if tool.name == "Read")

    inside = _run(_latest_chunk(read, file_path="inside.txt"))
    assert inside.state is not ToolResultState.ERROR, "相对路径仍从频道项目根起算"

    for raw in (str(global_file), "global_link.txt"):
        rejected = _run(_latest_chunk(read, file_path=raw))
        assert rejected.state is ToolResultState.ERROR, f"全局层路径不得成为文件工具输入：{raw}"
        assert GLOBAL_RULE not in rejected.content[0].text


# ─── 7. 全局根固定：数据目录覆盖不改变规则读取位置 ─────────────────────────
def test_global_root_ignores_the_data_dir_override(
    tmp_path: Path, stub_provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "explicit-data"
    monkeypatch.setenv("ALIBOT_DATA_DIR", str(override))
    assert global_home_root() == str(tmp_path / "home" / ".alibot")

    database, agent, channel, _project = _seed(tmp_path)
    try:
        assert str(database.data_dir) != str(_global_root(tmp_path))
        _write_global_rule(Path(global_home_root()), agent["id"], GLOBAL_RULE)
        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
            global_home=global_home_root(),
        )
        outcome = seam.run_round(
            database.get_agent(agent["id"]),
            [_human()],
            mentioned=False,
            target_id=channel["id"],
        )
        assert outcome.status == "replied", outcome.reason
        prompt = _system_text(_PlainReplyHandler.request_bodies[0])
        assert f"全局基座：\n{GLOBAL_RULE}" in prompt, "规则仍从 ~/.alibot 读取"
    finally:
        database.close()


# ─── 8. SOP 执行器排除：内部执行身份不拼频道协作规则 ─────────────────────
def test_sop_participant_does_not_get_the_three_rule_segments(tmp_path: Path) -> None:
    database, agent, _channel, project = _seed(tmp_path)
    try:
        _write_global_rule(_global_root(tmp_path), agent["id"], GLOBAL_RULE)
        _write_project_rule(project, agent["id"], PROJECT_RULE)
        systems: list[str] = []
        seam = _PipelineSeam(
            [_RecordingModel([_text("完成")], systems)],
            workspace_of=database.workspace_settings_for_target,
            global_home=str(_global_root(tmp_path)),
        )
        participant = seam.sop_agent(database.get_agent(agent["id"]), role="executor")
        _run(participant.reply(UserMsg(name="owner", content="开始")))

        assert systems, "SOP 参与者没有真的向模型发起请求"
        prompt = systems[0]
        assert "全局基座" not in prompt
        assert GLOBAL_RULE not in prompt
        assert "项目补充" not in prompt
        assert PROJECT_RULE not in prompt
        # 既有 SOP 身份提示仍在（排除的只是三段频道协作规则）。
        assert CONFIG_PROMPT in prompt
        assert "executor" in prompt
    finally:
        database.close()


# ─── 9. 三段都为空：既有身份句与回应 rubric 照旧 ─────────────────────────
def test_empty_rules_keep_the_existing_identity_and_response_rubric(
    tmp_path: Path, stub_provider: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    database = open_database(tmp_path / "data")
    try:
        agent = database.create_agent("小明", "工程师", "openai:stub-model", "")
        channel = database.create_channel("空规则频道", member_ids=[agent["id"]])
        database.update_channel_config(channel["id"], workspace_root=str(project))
        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
            global_home=str(_global_root(tmp_path)),
        )

        outcome = seam.run_round(
            database.get_agent(agent["id"]),
            [_human()],
            mentioned=False,
            target_id=channel["id"],
        )
        assert outcome.status == "replied", outcome.reason

        prompt = _system_text(_PlainReplyHandler.request_bodies[0])
        assert "全局基座" not in prompt
        assert "项目补充" not in prompt
        assert "你的角色：工程师" in prompt
        assert "回应纪律" in prompt, "没有自定义规则不等于失去行为纪律"
    finally:
        database.close()


# ─── 10. 全局层契约（后续票共用）：成员家目录解析与 Docker 挂载点常量 ─────
def test_global_member_home_and_docker_mount_constants(tmp_path: Path) -> None:
    assert DOCKER_GLOBAL_HOME == "/alibot-home"
    assert global_member_home("mem_1", root=str(_global_root(tmp_path))) == str(
        _global_root(tmp_path) / "agents" / "mem_1"
    )
    assert docker_global_member_home("mem_1") == "/alibot-home/agents/mem_1"
    assert docker_global_skills_home() == "/alibot-home/skills"


# ─── 11. 下游复用入口（#232 / #233 / #234）：公开只读守卫 ────────────────
def test_resolve_global_read_path_guards_and_returns_only_existing_paths(tmp_path: Path) -> None:
    global_root = _global_root(tmp_path)
    legal = _write_global_rule(global_root, "mem_reader", GLOBAL_RULE)
    outside = tmp_path / "outside"
    (outside / "rules").mkdir(parents=True)
    (outside / "rules" / "AGENTS.md").write_text("根外秘密。\n", encoding="utf-8")
    # 根外 symlink 祖先。
    os.symlink(outside, global_root / "agents" / "mem_ancestor")
    # 根外目标 symlink。
    target_rules = global_root / "agents" / "mem_target" / "rules"
    target_rules.mkdir(parents=True)
    os.symlink(outside / "rules" / "AGENTS.md", target_rules / "AGENTS.md")

    before = _listing(global_root)
    # 成员家目录内的合法路径：返回 realpath（只读，不创建任何东西）。
    assert resolve_global_read_path(str(global_root), "agents", "mem_reader", "rules", "AGENTS.md") == str(
        legal.resolve()
    )
    # 路径不存在（含父目录缺失）⇒ None，由调用方按「该层缺席」处理。
    assert resolve_global_read_path(str(global_root), "agents", "mem_reader", "rules", "missing.md") is None
    assert resolve_global_read_path(str(global_root), "agents", "nobody", "rules", "AGENTS.md") is None
    # 根外 symlink 祖先 / 根外目标一律拒绝。
    assert resolve_global_read_path(str(global_root), "agents", "mem_ancestor", "rules", "AGENTS.md") is None
    assert resolve_global_read_path(str(global_root), "agents", "mem_target", "rules", "AGENTS.md") is None

    # 守卫只读：调用前后全局根的目录清单不变。
    assert _listing(global_root) == before
