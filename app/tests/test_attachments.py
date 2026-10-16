"""附件（ADR-0014）的 HTTP 黑盒契约测试：两步上传、同事务绑定、读取面与不可变性。

票面验收判据逐条落在这里（每条都只经 HTTP 面观察，数据目录是 `tmp_path` 下的独立目录）：

- 字节落**数据目录的独立子目录**、库里只有元数据（给两个读数：库文件大小 / 字节位置）；
- 上传后**未绑定**：不出现在任何消息读取面里（正反两向断言）；
- 绑定与消息落库**同一事务**：绑定失败 ⇒ 没有绑定、没有消息、没有孤儿字节；
- 超限大小 / 非法类型被拒（状态码 + `code`）且不留半成品；
- **没有**删除端点（给出实际响应）。

「孤儿字节」这类结构事实没有 HTTP 路径可观察，因此这里按既有体例（`test_schema_version_gate.py`
的「库未变」）**只读**地看那一份数据目录：列出附件子目录、比对文件与字节，**不写**任何东西。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alibot import i18n
from alibot.db import (
    ALLOWED_ATTACHMENT_MIME_TYPES,
    ATTACHMENTS_DIR_NAME,
    DATABASE_FILE_NAME,
    MAX_ATTACHMENT_BYTES,
)
from fastapi.testclient import TestClient

from tests.conftest import create_agent, create_channel, messages, send

ZH = "zh-CN"
EN = "en"


def upload(
    client: TestClient,
    data: bytes = b"hello attachment",
    *,
    name: str = "note.txt",
    mime: str = "text/plain",
):
    """上传一个附件草稿（原始字节 + 声明类型 + query 里的文件名）。"""
    return client.post(
        "/api/attachments",
        params={"name": name},
        content=data,
        headers={"Content-Type": mime},
    )


def uploaded_id(client: TestClient, data: bytes = b"hello attachment", **kwargs) -> str:
    response = upload(client, data, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()["attachment"]["id"]


def attachment_files(data_dir: Path) -> dict[str, bytes]:
    """附件子目录里的**实际**字节（文件名 = 附件标识）。目录不存在时是「一个字节都没有」。"""
    directory = data_dir / ATTACHMENTS_DIR_NAME
    if not directory.is_dir():
        return {}
    return {entry.name: entry.read_bytes() for entry in directory.iterdir() if entry.is_file()}


def db_size(data_dir: Path) -> int:
    return (data_dir / DATABASE_FILE_NAME).stat().st_size


def use_locale(client: TestClient, locale: str) -> None:
    assert client.patch("/api/settings", json={"locale": locale}).status_code == 200


# ─── 1. 两步上传的第一步：拿标识与元数据，仍是草稿 ──────────────────────────


def test_upload_returns_an_identifier_and_readable_metadata(client: TestClient) -> None:
    response = upload(client, b"attachment payload", name="报告 v2.txt", mime="text/plain")
    assert response.status_code == 201, response.text
    attachment = response.json()["attachment"]
    assert attachment["id"].startswith("att_")
    assert attachment["name"] == "报告 v2.txt"
    assert attachment["size"] == len(b"attachment payload")
    assert attachment["mime_type"] == "text/plain"
    assert attachment["message_id"] is None, "上传回来的是**草稿**：还没有绑定任何消息"
    assert attachment["created_at"]
    assert attachment["url"] == f"/api/attachments/{attachment['id']}"


def test_draft_is_not_readable_on_any_surface_until_it_is_bound(client: TestClient) -> None:
    """未绑定的上传 = 未发送的草稿：消息读取面看不到它，内容端点也拿不到它。"""
    channel = create_channel(client).json()["channel"]
    attachment_id = uploaded_id(client, b"draft bytes", name="draft.txt")

    assert client.get(f"/api/attachments/{attachment_id}").status_code == 404
    assert messages(client, channel["id"]) == []
    assert client.get("/api/tasks").json()["tasks"] == []
    hits = client.get("/api/search", params={"q": "draft"}).json()["hits"]
    assert hits == []
    assert client.get("/api/workspace").status_code == 200

    # 反向：绑定之后它**才**出现在读取面里（正反两向都断言，避免「永远看不到」也算通过）。
    sent = send(client, channel["id"], "带附件的一条", attachment_ids=[attachment_id])
    assert sent.status_code == 201, sent.text
    listed = messages(client, channel["id"])
    assert [item["attachments"][0]["id"] for item in listed] == [attachment_id]
    assert client.get(f"/api/attachments/{attachment_id}").status_code == 200


# ─── 2. 字节在数据目录、库里只有元数据 ────────────────────────────────────


def test_bytes_land_in_the_data_dir_subdirectory_while_the_library_stays_metadata_only(
    client: TestClient, data_dir: Path
) -> None:
    payload = os.urandom(512 * 1024)
    size_before = db_size(data_dir)

    response = upload(client, payload, name="big.bin", mime="application/pdf")
    assert response.status_code == 201, response.text
    attachment = response.json()["attachment"]

    files = attachment_files(data_dir)
    assert list(files) == [attachment["id"]], "字节落在数据目录下的独立子目录，文件名就是附件标识"
    assert files[attachment["id"]] == payload, "数据目录里的字节与上传的逐字节一致"

    size_after = db_size(data_dir)
    # 两个读数（票面要求）：字节在数据目录，库文件只长了一个元数据行。
    assert attachment["size"] == len(payload)
    assert size_after - size_before < 32 * 1024, f"库文件不该因附件而显著增长：{size_before} → {size_after}"
    assert payload not in (data_dir / DATABASE_FILE_NAME).read_bytes(), "字节不进 SQLite"


# ─── 3. 绑定与消息落库同一事务 ────────────────────────────────────────────


def test_binding_and_message_share_one_transaction(client: TestClient, data_dir: Path) -> None:
    """绑定失败时：没有绑定、没有消息、也没有孤儿字节。"""
    channel = create_channel(client).json()["channel"]
    good_id = uploaded_id(client, b"good bytes", name="good.txt")
    other_id = uploaded_id(client, b"other bytes", name="other.txt")
    before = attachment_files(data_dir)

    # 第二个标识不存在 ⇒ 第一条绑定的 UPDATE 必须跟着一起回滚。
    rejected = send(client, channel["id"], "这条不该落库", attachment_ids=[good_id, "att_不存在"])
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "validation_failed"

    assert messages(client, channel["id"]) == [], "消息落库失败 ⇒ 一条消息都没有"
    assert client.get(f"/api/attachments/{good_id}").status_code == 404, "没有留下绑定"
    assert attachment_files(data_dir) == before, "没有留下孤儿字节"

    # 回滚干净的证据：两个草稿都还能**正常**绑定。
    assert send(client, channel["id"], "这条落库", attachment_ids=[good_id]).status_code == 201
    assert send(client, channel["id"], "另一条落库", attachment_ids=[other_id]).status_code == 201


def test_an_already_bound_attachment_cannot_be_bound_twice(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    first = uploaded_id(client, b"first", name="first.txt")
    second = uploaded_id(client, b"second", name="second.txt")
    assert send(client, channel["id"], "先绑第一条", attachment_ids=[first]).status_code == 201

    rejected = send(client, channel["id"], "想复用", attachment_ids=[second, first])
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "validation_failed"
    payload = rejected.json()["error"]
    assert payload["fields"]["attachment_ids"] == i18n.render("error.field.attachment_unusable", ZH)

    assert [item["body"] for item in messages(client, channel["id"])] == ["先绑第一条"]
    assert client.get(f"/api/attachments/{second}").status_code == 404, "失败的绑定不留半成品"


def test_task_and_dm_messages_share_the_same_binding_contract(client: TestClient) -> None:
    """消息契约是**同一条**：任务线程与私信都随消息绑定，且都只暴露已绑定的附件。"""
    channel = create_channel(client).json()["channel"]
    task_id = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "带附件"}).json()["task"][
        "id"
    ]
    task_attachment = uploaded_id(client, b"task bytes", name="task.txt")
    task_message = client.post(
        f"/api/tasks/{task_id}/messages",
        json={"body": "任务里也挂一个", "attachment_ids": [task_attachment]},
    )
    assert task_message.status_code == 201, task_message.text
    assert task_message.json()["message"]["attachments"][0]["id"] == task_attachment

    agent = create_agent(client, name="Ada").json()["agent"]
    dm = client.post("/api/dms", json={"agent_id": agent["id"]}).json()["dm"]
    dm_attachment = uploaded_id(client, b"dm bytes", name="dm.txt")
    dm_message = client.post(
        f"/api/dms/{dm['id']}/messages",
        json={"body": "私信里也挂一个", "attachment_ids": [dm_attachment]},
    )
    assert dm_message.status_code == 201, dm_message.text
    assert dm_message.json()["message"]["attachments"][0]["id"] == dm_attachment
    listed = client.get(f"/api/dms/{dm['id']}/messages").json()["messages"]
    assert [item["attachments"][0]["id"] for item in listed] == [dm_attachment]


# ─── 4. 大小与类型上限：拒绝并给可读原因，不留半成品 ──────────────────────


def test_oversize_upload_is_rejected_without_leaving_a_half_written_draft(
    client: TestClient, data_dir: Path
) -> None:
    response = upload(client, b"x" * (MAX_ATTACHMENT_BYTES + 1), name="too-big.bin", mime="application/pdf")
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert error["fields"]["file"] == i18n.render(
        "error.attachment_too_large", ZH, limit=MAX_ATTACHMENT_BYTES
    )
    assert attachment_files(data_dir) == {}, "被拒的请求不留孤儿字节"


def test_disallowed_mime_type_is_rejected_without_leaving_a_half_written_draft(
    client: TestClient, data_dir: Path
) -> None:
    response = upload(client, b"#!/bin/sh\n", name="run.sh", mime="application/x-sh")
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "application/x-sh" in error["fields"]["file"]
    assert attachment_files(data_dir) == {}


@pytest.mark.parametrize(
    ("name", "mime", "key"),
    [
        ("", "text/plain", "error.attachment_name_empty"),
        ("  ", "text/plain", "error.attachment_name_empty"),
        ("a" * 201, "text/plain", "error.attachment_name_too_long"),
        ("note.txt", "text/plain", "error.attachment_empty"),
    ],
)
def test_missing_name_and_empty_content_are_rejected(
    client: TestClient, data_dir: Path, name: str, mime: str, key: str
) -> None:
    payload = b"content" if key != "error.attachment_empty" else b""
    response = upload(client, payload, name=name, mime=mime)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "validation_failed"
    assert attachment_files(data_dir) == {}


def test_rejection_copy_follows_the_language_preference(client: TestClient) -> None:
    """人类面拒绝原因按**语言偏好**取值（ADR-0009 第二层）：两份取值都要真的取到。"""
    use_locale(client, EN)
    english = upload(client, b"#!/bin/sh\n", name="run.sh", mime="application/x-sh").json()["error"]
    assert english["message"] == i18n.render("error.attachment_rejected", EN)
    assert english["fields"]["file"] == i18n.render(
        "error.attachment_type_not_allowed",
        EN,
        mime="application/x-sh",
        types=", ".join(ALLOWED_ATTACHMENT_MIME_TYPES),
    )

    use_locale(client, ZH)
    chinese = upload(client, b"#!/bin/sh\n", name="run.sh", mime="application/x-sh").json()["error"]
    assert chinese["message"] == i18n.render("error.attachment_rejected", ZH)
    assert chinese["fields"]["file"] != english["fields"]["file"]


# ─── 5. 下载 / 预览与「没有删除端点」 ─────────────────────────────────────


def test_bound_attachment_is_downloadable_with_its_original_name(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    payload = b"downloadable bytes"
    attachment_id = uploaded_id(client, payload, name="报告.pdf", mime="application/pdf")
    assert send(client, channel["id"], "拿去", attachment_ids=[attachment_id]).status_code == 201

    response = client.get(f"/api/attachments/{attachment_id}")
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"].startswith("application/pdf")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*" in disposition, "非 ASCII 原名按 RFC 5987 编码"
    assert len(response.content) == len(payload)

    inline = client.get(f"/api/attachments/{attachment_id}", params={"disposition": "inline"})
    assert inline.status_code == 200
    assert inline.headers["content-disposition"].startswith("inline;")


def test_unknown_attachment_is_not_found(client: TestClient) -> None:
    response = client.get("/api/attachments/att_不存在")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_attachments_have_no_mutation_endpoint(client: TestClient, method: str) -> None:
    """与消息同口径：没有删除（也没有编辑 / 覆盖）附件的端点。"""
    channel = create_channel(client).json()["channel"]
    attachment_id = uploaded_id(client, b"immutable", name="keep.txt")
    assert send(client, channel["id"], "绑定它", attachment_ids=[attachment_id]).status_code == 201

    response = client.request(method, f"/api/attachments/{attachment_id}", json={})
    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "not_found",
        "message": i18n.render("error.attachments_immutable", ZH),
    }
    assert client.get(f"/api/attachments/{attachment_id}").status_code == 200, "内容一个字节没变"
