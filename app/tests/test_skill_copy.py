"""#273 技能复制的 HTTP 契约：``POST /api/skills/{id}/copy``。

只经人类面取证（沿用 `client` / `data_dir` / `backend` 既有装置，临时 HOME 由 conftest 的
autouse fixture 隔离）：

1. 复制成功：201；目标成员家目录出现同内容字节；目标行的 `content_hash` 与源一致；
   `enabled` 继承；源行与源字节一字不动；
2. 目标已存在同名：409，目标字节未被覆盖（仍是原来那份），源不变；
3. 复制给自己：400，不产生第二份；
4. 未知 `skill_id` 404；频道级旧行 400；正文带额外字段 400；
5. 源路径走全局只读守卫：根外 symlink 目标 / symlink 祖先一律 400，根外内容不被复制进来。

唯一一处直连库的是「频道级旧行」——这个形状没有 HTTP 创建路径（安装器对 `scope_kind='channel'`
明确 400），因此按既有体例用公开的 `open_database` 预置一行，再经 HTTP 断言行为。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alibot.db import open_database
from alibot.workspace import global_home_root
from fastapi.testclient import TestClient

from tests.conftest import backend, create_agent


def _write_skill(directory: Path, *, name: str = "review", description: str = "代码评审清单") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# Review\n\n按步骤评审。\n",
        encoding="utf-8",
    )
    references = directory / "references"
    references.mkdir(exist_ok=True)
    (references / "checklist.txt").write_text("a\nb\n", encoding="utf-8")
    return directory


def _member_skills_dir(member_id: str) -> Path:
    return Path(global_home_root()) / "agents" / member_id / "skills"


def _install(client: TestClient, *, source: Path, member_id: str, enabled: bool | None = None):
    response = client.post(
        "/api/skills",
        json={"scope_kind": "agent", "scope_id": member_id, "source_path": str(source)},
    )
    assert response.status_code == 201, response.text
    skill = response.json()["skill"]
    if enabled is False:
        disabled = client.patch(f"/api/skills/{skill['id']}", json={"enabled": False})
        assert disabled.status_code == 200, disabled.text
        skill = disabled.json()["skill"]
    return skill


def _skills(client: TestClient, member_id: str) -> list[dict]:
    response = client.get(f"/api/skills?scope_kind=agent&scope_id={member_id}")
    assert response.status_code == 200, response.text
    return response.json()["skills"]


def _tree(root: Path) -> dict[str, bytes]:
    """一棵技能树的字节快照（相对路径 → 内容）；用来断言「同内容 / 未被覆盖」。"""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _two_members(client: TestClient) -> tuple[dict, dict]:
    source = create_agent(client, name="源成员").json()["agent"]
    target = create_agent(client, name="目标成员").json()["agent"]
    return source, target


def test_copy_writes_the_same_bytes_and_inherits_enabled(client: TestClient, tmp_path: Path) -> None:
    """复制成功：目标字节与源一致、hash 一致、enabled 继承，源行与源字节不变。"""
    source, target = _two_members(client)
    origin = _write_skill(tmp_path / "origin")
    installed = _install(client, source=origin, member_id=source["id"], enabled=False)
    source_dir = _member_skills_dir(source["id"]) / installed["name"]
    source_before = _tree(source_dir)

    response = client.post(f"/api/skills/{installed['id']}/copy", json={"scope_id": target["id"]})
    assert response.status_code == 201, response.text
    copied = response.json()["skill"]

    assert copied["name"] == installed["name"]
    assert copied["scope_kind"] == "agent"
    assert copied["scope_id"] == target["id"]
    assert copied["content_hash"] == installed["content_hash"]
    assert copied["enabled"] is False, "启用态继承源行（停用的技能复制后仍是停用）"
    assert _tree(_member_skills_dir(target["id"]) / copied["name"]) == source_before

    # 源行与源字节一字不动；两份清单各记各的。
    assert _skills(client, source["id"])[0]["content_hash"] == installed["content_hash"]
    assert _tree(source_dir) == source_before
    assert [item["name"] for item in _skills(client, target["id"])] == [installed["name"]]


def test_copy_to_a_member_that_already_has_the_name_conflicts_without_overwriting(
    client: TestClient, tmp_path: Path
) -> None:
    """目标已有同名：409；目标字节保持原样，源也不动。"""
    source, target = _two_members(client)
    origin = _write_skill(tmp_path / "origin", description="源版本")
    installed = _install(client, source=origin, member_id=source["id"])
    source_dir = _member_skills_dir(source["id"]) / installed["name"]
    source_before = _tree(source_dir)

    existing = _write_skill(tmp_path / "existing", description="目标自己的版本")
    (existing / "SKILL.md").write_text(
        "---\nname: review\ndescription: 目标自己的版本\n---\n\n# 目标版本\n",
        encoding="utf-8",
    )
    target_skill = _install(client, source=existing, member_id=target["id"])
    target_dir = _member_skills_dir(target["id"]) / target_skill["name"]
    target_before = _tree(target_dir)
    assert target_before["SKILL.md"] != source_before["SKILL.md"]

    response = client.post(f"/api/skills/{installed['id']}/copy", json={"scope_id": target["id"]})
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "skill_conflict"
    assert installed["name"] in error["message"]

    assert _tree(target_dir) == target_before, "冲突时目标字节不许被覆盖"
    assert _skills(client, target["id"])[0]["content_hash"] == target_skill["content_hash"]
    assert _tree(source_dir) == source_before, "源字节也不许因为一次冲突的复制而变化"


def test_copy_to_the_same_member_is_rejected_without_a_second_copy(
    client: TestClient, tmp_path: Path
) -> None:
    """复制给自己 = 400，不产生第二份（源分区与源清单都不变）。"""
    source, _target = _two_members(client)
    installed = _install(client, source=_write_skill(tmp_path / "origin"), member_id=source["id"])
    before = _skills(client, source["id"])

    response = client.post(f"/api/skills/{installed['id']}/copy", json={"scope_id": source["id"]})
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert installed["name"] in error["message"]

    assert _skills(client, source["id"]) == before
    entries = sorted(path.name for path in _member_skills_dir(source["id"]).iterdir())
    assert entries == [installed["name"]]


def test_unknown_skill_and_unknown_target_are_readable_errors(client: TestClient, tmp_path: Path) -> None:
    """未知 skill_id 404；未知目标成员 400（且不动任何字节）。"""
    source, _target = _two_members(client)
    installed = _install(client, source=_write_skill(tmp_path / "origin"), member_id=source["id"])

    missing = client.post("/api/skills/sk_missing/copy", json={"scope_id": "mem_missing"})
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "not_found"

    unknown_target = client.post(f"/api/skills/{installed['id']}/copy", json={"scope_id": "mem_missing"})
    assert unknown_target.status_code == 400, unknown_target.text
    assert "mem_missing" in unknown_target.json()["error"]["message"]
    assert not (Path(global_home_root()) / "agents" / "mem_missing").exists()


def test_channel_scoped_legacy_rows_are_rejected(data_dir: Path, tmp_path: Path) -> None:
    """频道级旧行不参与复制：400（旧行与旧字节原样保留）。"""
    database = open_database(data_dir)
    channel = database.create_channel("旧频道")
    legacy = database.create_skill(
        scope_kind="channel",
        scope_id=channel["id"],
        name="legacy",
        description="频道级旧行",
        source_path=str(tmp_path / "legacy-source"),
        content_hash="a" * 64,
    )
    database.close()

    with backend(data_dir) as client:
        target = create_agent(client, name="目标成员").json()["agent"]
        response = client.post(f"/api/skills/{legacy['id']}/copy", json={"scope_id": target["id"]})
        assert response.status_code == 400, response.text
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert "agent" in error["message"]
        assert _skills(client, target["id"]) == []
        assert not (_member_skills_dir(target["id"]) / "legacy").exists()


def test_extra_fields_in_the_body_are_rejected(client: TestClient, tmp_path: Path) -> None:
    """正文只收目标成员：带 ``source_path`` 之类的键整体拒绝（不接受调用方给路径）。"""
    source, target = _two_members(client)
    installed = _install(client, source=_write_skill(tmp_path / "origin"), member_id=source["id"])

    response = client.post(
        f"/api/skills/{installed['id']}/copy",
        json={"scope_id": target["id"], "source_path": str(tmp_path / "origin")},
    )
    assert response.status_code == 400, response.text
    assert _skills(client, target["id"]) == []
    assert not (_member_skills_dir(target["id"]) / installed["name"]).exists()


@pytest.mark.parametrize("escape", ["skills_dir_symlink", "skill_dir_symlink"])
def test_source_outside_the_global_root_is_never_copied(
    client: TestClient, tmp_path: Path, escape: str
) -> None:
    """根外 symlink（祖先或目标本身）一律拒绝：400，且根外内容不会进目标成员家目录。

    ``skills_dir_symlink``：``<家目录>/skills`` 整体指向根外；
    ``skill_dir_symlink``：``<家目录>/skills/<name>`` 指向根外。
    """
    source, target = _two_members(client)
    installed = _install(client, source=_write_skill(tmp_path / "origin"), member_id=source["id"])
    skills_dir = _member_skills_dir(source["id"])
    name = installed["name"]

    outside = tmp_path / "outside"
    _write_skill(outside / "skills" / name, description="根外的内容")

    if escape == "skills_dir_symlink":
        moved = tmp_path / "member-skills-moved"
        os.rename(skills_dir, moved)
        os.symlink(outside / "skills", skills_dir)
    else:
        os.rename(skills_dir / name, tmp_path / "moved-skill")
        os.symlink(outside / "skills" / name, skills_dir / name)

    response = client.post(f"/api/skills/{installed['id']}/copy", json={"scope_id": target["id"]})
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    # 守卫的判据是「路径不安全」这一档（根外 symlink 祖先 / 根外目标），不是普通的目录检查。
    assert "symlink" in error["message"], error["message"]

    assert _skills(client, target["id"]) == []
    assert not (_member_skills_dir(target["id"]) / name).exists(), "根外内容不许被复制进来"
