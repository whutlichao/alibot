"""#152 Skill 的 HTTP 契约：安装、校验、分区、启停/删除、重启与零副作用。

只经人类面取证：

1. 本机目录与上传 zip 都能安装；``SKILL.md`` front matter、hash 与路径先校验，非法输入
   整体拒绝（不留半行、不留半棵树）；
2. 技能按 scope 分区（agent → ``.alibot/skills/<member_id>/``，channel → 频道分区）；
   启停/删除只影响该 scope，且**不删用户源目录**；
3. 配置动作不落消息、不产生轮次结果、不改变预算读数；没有远端市场/下载入口；
4. 重启后列表与启用态保持。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import budget, create_agent, create_channel, messages, outcomes

MANIFEST = """---
name: review
description: 代码评审清单
---

# Review

按以下步骤评审。
"""


def _write_skill(
    directory: Path, *, name: str = "review", description: str = "代码评审清单", body: str = "# Review\n"
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )
    (directory / "checklist.txt").write_text("a\nb\n", encoding="utf-8")
    return directory


def _channel_with_root(client: TestClient, root: Path, *, agents: int = 1) -> tuple[dict, list[dict]]:
    created_agents = [create_agent(client, name=f"成员{i}").json()["agent"] for i in range(agents)]
    channel = create_channel(client, member_ids=[agent["id"] for agent in created_agents]).json()["channel"]
    root.mkdir(parents=True, exist_ok=True)
    patched = client.patch(f"/api/channels/{channel['id']}/config", json={"workspace_root": str(root)})
    assert patched.status_code == 200, patched.text
    return channel, created_agents


def _install_local(
    client: TestClient,
    *,
    source: Path,
    scope_kind: str,
    scope_id: str,
    channel_id: str,
):
    return client.post(
        "/api/skills",
        json={
            "scope_kind": scope_kind,
            "scope_id": scope_id,
            "source_path": str(source),
            "channel_id": channel_id,
        },
    )


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _install_zip(
    client: TestClient,
    *,
    archive: bytes,
    scope_kind: str,
    scope_id: str,
    channel_id: str,
    filename: str = "review.zip",
):
    return client.post(
        "/api/skills",
        params={
            "scope_kind": scope_kind,
            "scope_id": scope_id,
            "channel_id": channel_id,
            "filename": filename,
        },
        content=archive,
        headers={"content-type": "application/zip"},
    )


def _skills(client: TestClient, scope_kind: str | None = None, scope_id: str | None = None) -> list[dict]:
    params = {}
    if scope_kind is not None:
        params["scope_kind"] = scope_kind
    if scope_id is not None:
        params["scope_id"] = scope_id
    response = client.get("/api/skills", params=params)
    assert response.status_code == 200, response.text
    return response.json()["skills"]


def test_local_directory_install_round_trips_and_lands_in_the_member_partition(
    client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    channel, (agent,) = _channel_with_root(client, root)
    source = _write_skill(tmp_path / "source-review")

    response = _install_local(
        client, source=source, scope_kind="agent", scope_id=agent["id"], channel_id=channel["id"]
    )
    assert response.status_code == 201, response.text
    installed = response.json()["skill"]
    assert installed["name"] == "review"
    assert installed["description"] == "代码评审清单"
    assert installed["scope_kind"] == "agent"
    assert installed["scope_id"] == agent["id"]
    assert installed["enabled"] is True
    assert installed["source_path"] == str(source)
    assert len(installed["content_hash"]) == 64
    int(installed["content_hash"], 16)

    partition = root / ".alibot" / "skills" / agent["id"] / "review"
    assert (partition / "SKILL.md").is_file()
    assert (partition / "checklist.txt").read_text(encoding="utf-8") == "a\nb\n"
    # 安装是**复制**：用户源目录原样保留。
    assert (source / "SKILL.md").is_file()
    assert (source / "checklist.txt").is_file()

    listed = _skills(client, "agent", agent["id"])
    assert [skill["name"] for skill in listed] == ["review"]
    assert listed[0]["content_hash"] == installed["content_hash"]

    # 同一内容在不同 scope 得到同一个 hash（hash 是内容事实，不是占位）。
    other_channel, (other_agent,) = _channel_with_root(client, tmp_path / "other-project")
    second = _install_local(
        client,
        source=source,
        scope_kind="agent",
        scope_id=other_agent["id"],
        channel_id=other_channel["id"],
    )
    assert second.status_code == 201, second.text
    assert second.json()["skill"]["content_hash"] == installed["content_hash"]

    # 改一个字节后装到**另一个** scope：hash 必须变化（内容 hash 不是按名字生成）。
    source.joinpath("checklist.txt").write_text("changed\n", encoding="utf-8")
    modified = _install_local(
        client,
        source=source,
        scope_kind="channel",
        scope_id=other_channel["id"],
        channel_id=other_channel["id"],
    )
    assert modified.status_code == 201, modified.text
    assert modified.json()["skill"]["content_hash"] != installed["content_hash"]

    # 同一 scope 同名再次安装：409，不静默覆盖，也不多留一棵树。
    duplicate = _install_local(
        client,
        source=source,
        scope_kind="channel",
        scope_id=other_channel["id"],
        channel_id=other_channel["id"],
    )
    assert duplicate.status_code == 409, duplicate.text


def test_zip_upload_installs_and_illegal_inputs_are_rejected_wholesale(
    client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    channel, (agent,) = _channel_with_root(client, root)

    archive = _zip_bytes(
        {
            "SKILL.md": MANIFEST.encode("utf-8"),
            "notes/run.md": b"run it\n",
        }
    )
    response = _install_zip(
        client, archive=archive, scope_kind="agent", scope_id=agent["id"], channel_id=channel["id"]
    )
    assert response.status_code == 201, response.text
    installed = response.json()["skill"]
    assert installed["name"] == "review"
    assert installed["source_path"] == "zip:review.zip"
    assert (root / ".alibot" / "skills" / agent["id"] / "review" / "notes" / "run.md").is_file()

    before = _skills(client)
    assert len(before) == 1

    def rejects(payload: bytes, label: str, **kwargs) -> None:
        rejected = _install_zip(
            client,
            archive=payload,
            scope_kind="agent",
            scope_id=agent["id"],
            channel_id=channel["id"],
            filename=f"{label}.zip",
            **kwargs,
        )
        assert rejected.status_code == 400, (label, rejected.text)
        assert rejected.json()["error"]["code"] == "validation_failed"
        # 整体拒绝：没有新行、没有半棵树。
        assert _skills(client) == before
        assert not (root / ".alibot" / "skills" / agent["id"] / label).exists()

    # 缺 ``SKILL.md``
    rejects(_zip_bytes({"hello.txt": b"hi"}), "no-manifest")
    # front matter 缺 description
    rejects(
        _zip_bytes({"SKILL.md": b"---\nname: review\n---\n\nbody\n"}),
        "no-description",
    )
    # 非法 name（带路径分隔符）
    rejects(
        _zip_bytes({"SKILL.md": b"---\nname: ../escape\ndescription: x\n---\n\nbody\n"}),
        "bad-name",
    )
    # 缺 ``scope_id`` / 未配置项目根
    missing_scope = client.post(
        "/api/skills",
        json={"scope_kind": "agent", "scope_id": "mem_missing", "source_path": str(tmp_path)},
    )
    assert missing_scope.status_code == 400
    no_root_channel = create_channel(client, name="没有根").json()["channel"]
    no_root_agent = create_agent(client, name="无根成员").json()["agent"]
    client.post(f"/api/channels/{no_root_channel['id']}/members", json={"member_id": no_root_agent["id"]})
    source = _write_skill(tmp_path / "source-no-root")
    no_root = _install_local(
        client,
        source=source,
        scope_kind="agent",
        scope_id=no_root_agent["id"],
        channel_id=no_root_channel["id"],
    )
    assert no_root.status_code == 400
    assert _skills(client) == before

    # zip slip：绝对路径与 ``..`` 都不许逃出解包目录。
    for name in ("../escape.txt", "/tmp/escape.txt"):
        slipped = _zip_bytes({"SKILL.md": MANIFEST.encode("utf-8"), name: b"evil"})
        rejected = _install_zip(
            client,
            archive=slipped,
            scope_kind="agent",
            scope_id=agent["id"],
            channel_id=channel["id"],
            filename="slip.zip",
        )
        assert rejected.status_code == 400, rejected.text
        assert not (tmp_path / "escape.txt").exists()
    assert _skills(client) == before


def test_scope_partitions_are_isolated_and_delete_never_touches_the_source(
    client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    channel, (first, second) = _channel_with_root(client, root, agents=2)
    first_source = _write_skill(tmp_path / "first-source", description="第一份")
    second_source = _write_skill(tmp_path / "second-source", description="第二份")

    first_row = _install_local(
        client, source=first_source, scope_kind="agent", scope_id=first["id"], channel_id=channel["id"]
    ).json()["skill"]
    second_row = _install_local(
        client, source=second_source, scope_kind="agent", scope_id=second["id"], channel_id=channel["id"]
    ).json()["skill"]
    first_partition = root / ".alibot" / "skills" / first["id"] / "review"
    second_partition = root / ".alibot" / "skills" / second["id"] / "review"
    assert first_partition.is_dir() and second_partition.is_dir()

    # 启停只影响该 scope。
    patched = client.patch(f"/api/skills/{first_row['id']}", json={"enabled": False})
    assert patched.status_code == 200
    assert patched.json()["skill"]["enabled"] is False
    assert _skills(client, "agent", first["id"])[0]["enabled"] is False
    assert _skills(client, "agent", second["id"])[0]["enabled"] is True

    # 删除只删配置与工作区副本：另一 scope 与用户源目录都保持原样。
    deleted = client.delete(f"/api/skills/{first_row['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["skill"]["id"] == first_row["id"]
    assert not first_partition.exists()
    assert second_partition.is_dir()
    assert first_source.is_dir() and (first_source / "SKILL.md").is_file()
    assert second_source.is_dir() and (second_source / "SKILL.md").is_file()
    assert [skill["id"] for skill in _skills(client, "agent", first["id"])] == []
    assert [skill["id"] for skill in _skills(client, "agent", second["id"])] == [second_row["id"]]
    missing = client.delete(f"/api/skills/{first_row['id']}")
    assert missing.status_code == 404
    assert missing.json()["error"] == {
        "code": "not_found",
        "message": f"Skill 不存在：{first_row['id']}",
    }

    # 频道作用域是频道自己的分区：合并进成员视图时 Agent 级同名覆盖频道级。
    channel_row = _install_local(
        client,
        source=first_source,
        scope_kind="channel",
        scope_id=channel["id"],
        channel_id=channel["id"],
    ).json()["skill"]
    channel_partition = root / ".alibot" / "skills" / channel["id"] / "review"
    assert channel_partition.is_dir()
    assert client.delete(f"/api/skills/{channel_row['id']}").status_code == 200
    assert not channel_partition.exists()
    assert second_partition.is_dir()


def test_extra_fields_cannot_smuggle_a_remote_market(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "project"
    channel, (agent,) = _channel_with_root(client, root)
    source = _write_skill(tmp_path / "source-remote")
    for key, value in (("url", "https://market.example/skill.zip"), ("market", "hub")):
        response = client.post(
            "/api/skills",
            json={
                "scope_kind": "agent",
                "scope_id": agent["id"],
                "source_path": str(source),
                "channel_id": channel["id"],
                key: value,
            },
        )
        assert response.status_code == 400, (key, response.text)
    assert _skills(client) == []


def test_configuration_changes_do_not_message_wake_or_spend_budget(
    client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    channel, (agent,) = _channel_with_root(client, root)
    source = _write_skill(tmp_path / "source")
    before_messages = messages(client, channel["id"])
    before_budget = budget(client, channel["id"])
    before_outcomes = outcomes(client, channel["id"])

    installed = _install_local(
        client, source=source, scope_kind="agent", scope_id=agent["id"], channel_id=channel["id"]
    ).json()["skill"]
    assert client.patch(f"/api/skills/{installed['id']}", json={"enabled": False}).status_code == 200
    assert client.delete(f"/api/skills/{installed['id']}").status_code == 200

    assert messages(client, channel["id"]) == before_messages
    assert budget(client, channel["id"]) == before_budget
    assert outcomes(client, channel["id"]) == before_outcomes


def test_restart_keeps_list_and_enabled_state(launched: dict, tmp_path: Path) -> None:
    client = launched["client"]
    root = tmp_path / "project"
    channel, (agent,) = _channel_with_root(client, root)
    source = _write_skill(tmp_path / "source")
    installed = _install_local(
        client, source=source, scope_kind="agent", scope_id=agent["id"], channel_id=channel["id"]
    ).json()["skill"]
    assert client.patch(f"/api/skills/{installed['id']}", json={"enabled": False}).status_code == 200

    after = launched["restart"]()
    listed = _skills(after, "agent", agent["id"])
    assert [skill["id"] for skill in listed] == [installed["id"]]
    assert listed[0]["enabled"] is False
    assert listed[0]["content_hash"] == installed["content_hash"]
    assert (root / ".alibot" / "skills" / agent["id"] / "review" / "SKILL.md").is_file()


def test_local_install_rejects_bad_manifests_and_unsafe_paths_wholesale(
    client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    channel, (agent,) = _channel_with_root(client, root)
    before = _skills(client)

    def rejects(source: Path, field: str) -> None:
        rejected = _install_local(
            client,
            source=source,
            scope_kind="agent",
            scope_id=agent["id"],
            channel_id=channel["id"],
        )
        assert rejected.status_code == 400, (source, rejected.text)
        assert rejected.json()["error"]["code"] == "validation_failed"
        assert _skills(client) == before
        partition = root / ".alibot" / "skills" / agent["id"]
        assert not partition.exists() or not any(partition.iterdir())

    # 没有 SKILL.md
    no_manifest = tmp_path / "no-manifest"
    no_manifest.mkdir()
    (no_manifest / "notes.txt").write_text("x", encoding="utf-8")
    rejects(no_manifest, "source")
    # 没有 front matter
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "SKILL.md").write_text("just text\n", encoding="utf-8")
    rejects(bare, "source")
    # name 非法
    rejects(_write_skill(tmp_path / "bad-name", name="bad name"), "name")
    # 空 description
    rejects(_write_skill(tmp_path / "empty-description", description=""), "description")
    # 指向 symlink 的工作区树不安全
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    linked = _write_skill(tmp_path / "linked")
    (linked / "leak.txt").symlink_to(outside)
    rejects(linked, "source")
    # 路径不是绝对目录 / 不存在
    relative = _install_local(
        client,
        source=Path("relative/source"),
        scope_kind="agent",
        scope_id=agent["id"],
        channel_id=channel["id"],
    )
    assert relative.status_code == 400
    missing = _install_local(
        client,
        source=tmp_path / "does-not-exist",
        scope_kind="agent",
        scope_id=agent["id"],
        channel_id=channel["id"],
    )
    assert missing.status_code == 400
    # 项目根本身是分区的祖先：拒绝，避免把 staging 复制进自己的来源树。
    ancestor = _install_local(
        client,
        source=root,
        scope_kind="agent",
        scope_id=agent["id"],
        channel_id=channel["id"],
    )
    assert ancestor.status_code == 400
    assert _skills(client) == before


def test_garbage_upload_and_patch_identity_fields_are_rejected(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "project"
    channel, (agent,) = _channel_with_root(client, root)
    source = _write_skill(tmp_path / "source")
    installed = _install_local(
        client, source=source, scope_kind="agent", scope_id=agent["id"], channel_id=channel["id"]
    ).json()["skill"]

    garbage = client.post(
        "/api/skills",
        params={
            "scope_kind": "agent",
            "scope_id": agent["id"],
            "channel_id": channel["id"],
            "filename": "garbage.zip",
        },
        content=b"not-a-zip",
        headers={"content-type": "application/zip"},
    )
    assert garbage.status_code == 400
    assert garbage.json()["error"]["code"] == "validation_failed"

    for changes in ({"name": "renamed"}, {"enabled": False, "content_hash": "0" * 64}):
        rejected = client.patch(f"/api/skills/{installed['id']}", json=changes)
        assert rejected.status_code == 400, (changes, rejected.text)
    assert _skills(client, "agent", agent["id"])[0]["name"] == "review"
