"""长期记忆的 ReMe 装配：工作区、模型、模式、工具与收尾（ADR-0034）。

接缝说明：这些用例**替换** ``alibot.memory.ReMeMiddleware`` 为记录构造参数的假中间件，因此
断言的是「装配层交给框架什么」，而不是 ReMe 的内部行为；另有一条用例用**真实**
``ReMeMiddleware`` 证明 ``reme-ai`` 可导入且工具名是 ``memory_search``。

无项目根的轮次（DM / 频道未配项目根）里，文件工具只有当前会话的卸载目录（只读）；长期记忆
不再经过文件工具，落点是宿主路径 ``<global_home>/agents/<member_id>/reme``。
"""

import asyncio
from pathlib import Path
from typing import Any, ClassVar

import pytest
from agentscope.agent import Agent
from agentscope.message import Msg, TextBlock
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse
from alibot import memory as memory_module
from alibot.runtime import SessionOffloader

from tests.test_approval_seam import _ScriptedModel, _StubModelSeam, _tool_call


class MemoryModel(_ScriptedModel):
    """记录每次模型输入；``client`` 可被替换成记录关闭时序的替身。"""

    def __init__(self, script: Any) -> None:
        super().__init__(script)
        self.inputs: list[str] = []

    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Any:
        self.inputs.append(str(messages))
        return await super()._call_api(model_name, messages, tools=tools, tool_choice=tool_choice, **kwargs)


#: 被替换前的真实类：假中间件用它的 ``list_tools()`` 返回**真实**的 ``memory_search``
#: （只是从不调用它），因此 toolkit 里出现的确实是生产工具，而不是形状相似的替身。
_REAL_REME = memory_module.ReMeMiddleware


class _FakeReMeMiddleware(MiddlewareBase):
    """记录构造参数、工具清单与 ``close`` 次数；``events`` 用来断言关闭时序。"""

    instances: ClassVar[list["_FakeReMeMiddleware"]] = []
    events: ClassVar[list[str]] = []

    class Parameters:
        def __init__(self, **values: Any) -> None:
            self.chat_model = values.get("chat_model")
            self.mode = values.get("mode")
            self.top_k = values.get("top_k")

    def __init__(self, *, workspace_dir: str, parameters: Any) -> None:
        self.workspace_dir = workspace_dir
        self.parameters = parameters
        self.closed = 0
        _FakeReMeMiddleware.instances.append(self)

    async def list_tools(self) -> list[Any]:
        if self.parameters.mode == "static_control":
            return []
        delegate = _REAL_REME(
            workspace_dir=self.workspace_dir,
            parameters=_REAL_REME.Parameters(mode=self.parameters.mode, top_k=self.parameters.top_k),
        )
        return await delegate.list_tools()

    async def close(self) -> None:
        self.closed += 1
        _FakeReMeMiddleware.events.append("reme")


class _ClosingClient:
    """provider 客户端的关闭入口替身：只记录「模型客户端被关」。"""

    async def close(self) -> None:
        _FakeReMeMiddleware.events.append("model")


@pytest.fixture(autouse=True)
def fake_reme(monkeypatch: pytest.MonkeyPatch) -> type[_FakeReMeMiddleware]:
    """每个用例都替换掉真实的 ReMe 构造入口（真实中间件只在专用用例里构造）。"""
    _FakeReMeMiddleware.instances = []
    _FakeReMeMiddleware.events = []
    monkeypatch.setattr(memory_module, "ReMeMiddleware", _FakeReMeMiddleware)
    return _FakeReMeMiddleware


@pytest.fixture
def global_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时 HOME + 临时全局根：测试绝不读写真实的 ``~/.alibot``。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home / ".alibot"


def _member_home(global_root: Path, member: str = "member") -> Path:
    return global_root / "agents" / member


def _dm_seam(model: Any, tmp_path: Path, global_root: Path) -> _StubModelSeam:
    """DM 轮次：数据目录做 runtime_root，全局根是记忆工作区的宿主。"""
    return _StubModelSeam(model, runtime_root=tmp_path / "data", global_home=str(global_root))


def _capture_agents(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    original = Agent.__init__

    def capture(self: Any, *args: Any, **kwargs: Any) -> None:
        options.append(kwargs)
        original(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", capture)
    return options


def _toolkit_names(toolkit: Any) -> list[str]:
    schemas = asyncio.run(toolkit.get_tool_schemas())
    return [str(schema.get("function", {}).get("name")) for schema in schemas]


def test_reme_round_assembles_the_member_workspace_with_this_round_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, global_root: Path
) -> None:
    options = _capture_agents(monkeypatch)
    model = MemoryModel([[ChatResponse(content=[TextBlock(text="收到")], is_last=True)]])
    outcome = _dm_seam(model, tmp_path, global_root).run_round(
        {"id": "member", "model_id": "openai:stub"},
        [{"body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
        direct_message=True,
    )

    assert outcome.status == "replied", outcome.reason
    assert len(_FakeReMeMiddleware.instances) == 1
    middleware = _FakeReMeMiddleware.instances[0]
    workspace = _member_home(global_root) / "reme"
    assert Path(middleware.workspace_dir) == workspace
    assert workspace.is_dir(), "reme 工作区必须真的建出来"
    assert middleware.parameters.chat_model is model, "ReMe 的模型固定为本轮模型"
    assert middleware.parameters.mode == "both", "provider 能携带工具时同时注入与暴露工具"
    assert middleware.parameters.top_k == 5
    assert any(item is middleware for item in options[0]["middlewares"])
    assert "memory_search" in _toolkit_names(options[0]["toolkit"])
    assert middleware.closed == 1, "轮次结束必须 close 一次"


def test_a_provider_without_tool_calling_degrades_to_static_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, global_root: Path
) -> None:
    options = _capture_agents(monkeypatch)
    model = MemoryModel([[ChatResponse(content=[TextBlock(text="收到")], is_last=True)]])
    outcome = _dm_seam(model, tmp_path, global_root).run_round(
        {"id": "member", "model_id": "unknown-provider:stub"},
        [{"body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
        direct_message=True,
    )

    assert outcome.status == "replied", outcome.reason
    middleware = _FakeReMeMiddleware.instances[0]
    assert middleware.parameters.mode == "static_control"
    assert middleware.closed == 1
    assert "memory_search" not in _toolkit_names(options[0]["toolkit"]), "不能携带工具时不暴露 memory_search"


def test_every_round_assembles_memory_without_any_config_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, global_root: Path
) -> None:
    """长期记忆没有开关（ADR-0035）：成员载荷里没有任何档位字段，照样装配 ReMe。"""
    options = _capture_agents(monkeypatch)
    model = MemoryModel([[ChatResponse(content=[TextBlock(text="收到")], is_last=True)]])
    outcome = _dm_seam(model, tmp_path, global_root).run_round(
        {"id": "member", "model_id": "openai:stub"},
        [{"body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
    )

    assert outcome.status == "replied", outcome.reason
    assert len(_FakeReMeMiddleware.instances) == 1
    middleware = _FakeReMeMiddleware.instances[0]
    workspace = _member_home(global_root) / "reme"
    assert Path(middleware.workspace_dir) == workspace
    assert workspace.is_dir(), "家目录与 reme/ 必须被建出"
    assert any(item is middleware for item in options[0]["middlewares"])
    assert "memory_search" in _toolkit_names(options[0]["toolkit"])
    assert middleware.closed == 1


def test_the_round_closes_reme_before_the_model_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, global_root: Path
) -> None:
    _capture_agents(monkeypatch)
    model = MemoryModel([[ChatResponse(content=[TextBlock(text="收到")], is_last=True)]])
    model.client = _ClosingClient()  # type: ignore[attr-defined]
    outcome = _dm_seam(model, tmp_path, global_root).run_round(
        {"id": "member", "model_id": "openai:stub"},
        [{"body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
    )

    assert outcome.status == "replied", outcome.reason
    assert _FakeReMeMiddleware.events == ["reme", "model"], "先收 ReMe，再关模型客户端"


def test_dm_can_read_its_offloaded_context_without_reading_other_partitions(
    tmp_path: Path, global_root: Path
) -> None:
    """卸载文件可读、别的会话与项目文件不可读（落点在 ``<数据目录>/sessions/``）。"""
    data_root = tmp_path / "data"
    path = asyncio.run(
        SessionOffloader(data_root, "target--member", in_project=False).offload_context(
            "session", [Msg(name="owner", content=[TextBlock(text="older-context-content")], role="user")]
        )
    )
    other = asyncio.run(
        SessionOffloader(data_root, "other--member", in_project=False).offload_context(
            "other", [Msg(name="owner", content=[TextBlock(text="private-other-session")], role="user")]
        )
    )
    assert Path(path).is_relative_to(data_root / "sessions" / "target--member" / "offload")
    secret = tmp_path / "project.txt"
    secret.write_text("private-project-content")
    escaped = Path(path).parent / "escaped.txt"
    escaped.symlink_to(secret)
    model = MemoryModel(
        [
            _tool_call("read-offload", "Read", {"file_path": path}),
            _tool_call("read-other", "Read", {"file_path": other}),
            _tool_call("read-escaped", "Read", {"file_path": str(escaped)}),
            [ChatResponse(content=[TextBlock(text="已找回卸载上下文")], is_last=True)],
        ]
    )
    outcome = _dm_seam(model, tmp_path, global_root).run_round(
        # 记忆由 ReMe 自管、不经过文件工具；卸载目录的只读 Read 照样在。
        {"id": "member", "model_id": "openai:stub"},
        [{"body": "请读取卸载文件", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
        direct_message=True,
    )
    assert outcome.status == "replied", outcome.reason
    assert "older-context-content" in model.inputs[-1]
    assert "private-other-session" not in "\n".join(model.inputs)
    assert "private-project-content" not in "\n".join(model.inputs)


def test_sessions_land_in_the_data_directory_without_an_extra_alibot_layer(
    tmp_path: Path, global_root: Path
) -> None:
    """无项目根：``<数据目录>/sessions/<target>--<member>/state.json``，且不出现 ``.alibot`` 镜像层。"""
    model = MemoryModel([[ChatResponse(content=[TextBlock(text="完成")], is_last=True)]])
    outcome = _dm_seam(model, tmp_path, global_root).run_round(
        {"id": "member", "model_id": "openai:stub"},
        [{"seq": 1, "body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
    )

    assert outcome.status == "replied", outcome.reason
    data_root = tmp_path / "data"
    assert (data_root / "sessions" / "target--member" / "state.json").is_file()
    assert not (data_root / ".alibot").exists(), "数据目录下不得再出现 .alibot 镜像层"


def test_project_round_sessions_land_under_the_project_alibot_partition(
    tmp_path: Path, global_root: Path
) -> None:
    """有项目根：``<项目根>/.alibot/sessions/<target>--<member>/state.json``（与 local/docker 现状一致）。"""
    from alibot.workspace import WorkspaceSettings

    project = tmp_path / "project"
    project.mkdir()
    model = MemoryModel([[ChatResponse(content=[TextBlock(text="完成")], is_last=True)]])
    seam = _StubModelSeam(
        model,
        workspace_of=lambda *_: WorkspaceSettings(root=str(project)),
        global_home=str(global_root),
    )
    outcome = seam.run_round(
        {"id": "member", "model_id": "openai:stub"},
        [{"seq": 1, "body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="channel",
    )

    assert outcome.status == "replied", outcome.reason
    assert (project / ".alibot" / "sessions" / "channel--member" / "state.json").is_file()
    assert not (project / "sessions").exists(), "项目根下不出现裸 sessions/"


def test_real_reme_middleware_exposes_memory_search(tmp_path: Path) -> None:
    """真实 ``reme-ai`` 可导入，且 ``list_tools()`` 给出 ``memory_search``（不启动应用）。"""
    import reme
    from agentscope.middleware import ReMeMiddleware

    assert reme is not None
    middleware = ReMeMiddleware(
        workspace_dir=str(tmp_path / "reme"),
        parameters=ReMeMiddleware.Parameters(mode="both", top_k=5),
    )
    try:
        tools = asyncio.run(middleware.list_tools())
        assert [tool.name for tool in tools] == ["memory_search"]
    finally:
        asyncio.run(middleware.close())
