"""HTTP 人类面：本机单用户的频道消息契约。

端点（浏览器只经这里读写，业务正本在后端单文件库）：

- ``GET  /api/workspace``                     工作台快照（空库 = 空工作台）
- ``GET  /api/channels``                      频道列表（含未读计数）
- ``POST /api/channels``                      新建频道（名称 + 描述 + 初始成员，原子校验）
- ``GET  /api/channels/{id}/messages``        按 seq 升序取消息（``after_seq`` 增量）
- ``POST /api/channels/{id}/messages``        发送人类消息（同事务分配频道内单调 seq）
- ``GET  /api/channels/{id}/members``         频道成员
- ``POST /api/channels/{id}/read``            标读（同一频道内按 seq 单调取 max）

消息是 append-only：**没有**编辑或删除消息的端点。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .db import (
    OWNER_ID,
    Database,
    DomainError,
    open_database,
)
from .service import WorkspaceService

PACKAGE_DIR = Path(__file__).resolve().parent


def repo_root() -> Path:
    """仓库根（``app/worksplice/api.py`` → 上溯三层）。"""
    return PACKAGE_DIR.parent.parent


def studio_dir() -> Path:
    return PACKAGE_DIR / "studio"


def design_system_dir() -> Path | None:
    """只读参照的设计系统目录（视觉契约的正本），不存在时返回 None。"""
    candidate = repo_root() / "worksplice-design-system"
    return candidate if candidate.is_dir() else None


class ChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    description: str = ""
    member_ids: list[str] = Field(default_factory=list)


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = ""


class MarkRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # strict："3" 这类字符串不会被静默当成 3。
    seq: Annotated[int, Field(strict=True)]


def create_app(data_dir: Path | str | None = None, db: Database | None = None) -> FastAPI:
    database = db if db is not None else open_database(data_dir)
    service = WorkspaceService(database)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            database.close()

    app = FastAPI(title="Worksplice", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.db = database
    app.state.service = service
    app.state.data_dir = database.data_dir

    def _not_found(message: str = "接口不存在") -> JSONResponse:
        return JSONResponse({"error": {"code": "not_found", "message": message}}, status_code=404)

    # ─── 错误封装：统一 {"error": {"code", "message", "fields?"}} ──────────
    @app.exception_handler(DomainError)
    async def _domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(exc.payload(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": "validation_failed",
                    "message": "请求体校验未通过",
                    "fields": {"body": str(exc.errors()[:1])},
                }
            },
            status_code=400,
        )

    # ─── 工作台 ────────────────────────────────────────────────────────────
    @app.get("/api/workspace")
    def get_workspace() -> dict[str, Any]:
        return service.bootstrap()

    @app.get("/api/health")
    def get_health() -> dict[str, Any]:
        return {"status": "ok", "member_id": OWNER_ID, "data_dir": str(app.state.data_dir)}

    # ─── 频道 ─────────────────────────────────────────────────────────────
    @app.get("/api/channels")
    def list_channels() -> dict[str, Any]:
        return {"channels": service.list_channels()}

    @app.post("/api/channels", status_code=201)
    def create_channel(payload: ChannelCreate) -> dict[str, Any]:
        channel = service.create_channel(
            name=payload.name, description=payload.description, member_ids=payload.member_ids
        )
        return {"channel": channel, "channels": service.list_channels()}

    @app.get("/api/channels/{channel_id}")
    def get_channel(channel_id: str) -> dict[str, Any]:
        return {"channel": database.get_channel(channel_id)}

    @app.get("/api/channels/{channel_id}/messages")
    def list_messages(channel_id: str, after_seq: int = Query(default=0, ge=0)) -> dict[str, Any]:
        messages = service.channel_messages(channel_id, after_seq=after_seq)
        channel = database.get_channel(channel_id)
        return {"channel": channel, "messages": messages}

    @app.post("/api/channels/{channel_id}/messages", status_code=201)
    def create_message(channel_id: str, payload: MessageCreate) -> dict[str, Any]:
        message = service.send_message(channel_id, payload.body)
        return {"message": message}

    @app.get("/api/channels/{channel_id}/members")
    def list_channel_members(channel_id: str) -> dict[str, Any]:
        return {"members": service.channel_members(channel_id)}

    # ─── 已读位置 ─────────────────────────────────────────────────────────
    @app.post("/api/channels/{channel_id}/read")
    def mark_channel_read(channel_id: str, payload: MarkRead) -> dict[str, Any]:
        channel = service.mark_read(channel_id, payload.seq)
        return {"channel": channel, "channels": service.list_channels()}

    # ─── 人类面不存在的写路径：明确回 404 而不是 500 ───────────────────────
    @app.api_route(
        "/api/channels/{channel_id}/messages/{seq}",
        methods=["GET", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def no_message_mutation(channel_id: str, seq: int) -> JSONResponse:
        """消息 append-only：人类 HTTP 面没有编辑或删除消息的端点。"""
        return _not_found("消息不可编辑、不可删除：人类面不存在该端点")

    # ─── 静态资源：工作台 + 只读设计系统 ─────────────────────────────────
    app.mount("/studio", StaticFiles(directory=str(studio_dir()), html=True), name="studio")

    design = design_system_dir()
    if design is not None:
        app.mount("/design", StaticFiles(directory=str(design)), name="design")

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/studio/")

    return app
