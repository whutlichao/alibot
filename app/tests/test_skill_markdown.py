"""#283 技能正文的只读端点：``GET /api/skills/{skill_id}/markdown``。

用户 2026-10-25 人定（设计笔记 D5 / Q1 双 A）：正文只读面落在 ``/api/skills/{id}`` 同族子资源，
复用既有的 404 文案、作用域校验与全局只读守卫。**正文 = front matter 之后的那段字节**，与装配
读进 ``Skill.markdown`` 的同一份（规格 US22「front matter 之后的指令块仍然可读」）。

只经 HTTP 面取证（临时 ``HOME`` 由 conftest 的 autouse fixture 隔离，本文件不自己改 ``HOME``）：

1. 成功 ⇒ ``200`` + ``state="ok"`` + ``markdown`` 与磁盘字节逐字相等、front matter 不在响应里、
   元数据载荷与 ``GET /api/skills`` 那一行一致；同一份字节被装配层读成 ``Skill.markdown`` 时逐字相等；
2. 字节缺失（目录 / ``SKILL.md`` 不在）⇒ ``200`` + ``state="missing"`` + ``markdown=null``，元数据照常；
3. 路径不安全（根外 symlink 目标 / 根外 symlink 祖先）⇒ ``200`` + ``state="invalid"``，
   响应里**不含**任何根外内容；
4. 读不出来（非 UTF-8 / 权限）⇒ ``200`` + ``state="unreadable"`` + ``markdown=null``；
5. ``skill_id`` 不存在 ⇒ ``404``（复用 ``error.skill_not_found``）；频道级旧行 ⇒ ``400``
   （复用 ``error.skill_scope_agent_only``）；
6. 只读性：不落消息、不产生轮次结果、预算读数不变、成员家目录的字节与 mtime 都不变。

唯一一处直连库的是「频道级旧行」——这个形状没有 HTTP 创建路径（安装器对 ``scope_kind='channel'``
明确 400），因此按 ``test_skill_copy.py`` 的既有体例用公开的 ``open_database`` 预置一行再断言。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alibot.db import open_database
from alibot.skills import SKILLS_DIR, load_skills
from alibot.workspace import global_home_root
from fastapi.testclient import TestClient

from tests.conftest import backend, budget, create_agent, create_channel, messages, outcomes

#: 磁盘上 ``SKILL.md`` 里 front matter 之后的那段字节（正文）。缩进与空行都是判据的一部分。
BODY = "# Review\n\n按步骤评审：\n\n    先读改动，再读测试\n\n- 一\n- 二"


def _write_skill(
    directory: Path,
    *,
    name: str = "review",
    description: str = "代码评审清单",
    body: str = BODY,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    (directory / "checklist.txt").write_text("a\nb\n", encoding="utf-8")
    return directory


def _member_home(member_id: str) -> Path:
    return Path(global_home_root()) / "agents" / member_id


def _skill_dir(member_id: str, name: str) -> Path:
    return _member_home(member_id) / SKILLS_DIR / name


def _install(client: TestClient, *, source: Path, member_id: str) -> dict:
    response = client.post(
        "/api/skills",
        json={"scope_kind": "agent", "scope_id": member_id, "source_path": str(source)},
    )
    assert response.status_code == 201, response.text
    return response.json()["skill"]


def _markdown(client: TestClient, skill_id: str):
    return client.get(f"/api/skills/{skill_id}/markdown")


def _tree(root: Path) -> dict[str, tuple[bytes, int]]:
    """一棵树的快照（相对路径 → 字节 + mtime_ns）；用来断言只读端点不写盘。"""
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_ok_returns_the_body_after_the_front_matter(client: TestClient, tmp_path: Path) -> None:
    """成功：正文与磁盘字节逐字相等；front matter 不在响应里；元数据与列表那一行一致。"""
    member = create_agent(client).json()["agent"]
    installed = _install(client, source=_write_skill(tmp_path / "origin"), member_id=member["id"])
    manifest = _skill_dir(member["id"], installed["name"]) / "SKILL.md"
    on_disk = manifest.read_text(encoding="utf-8-sig")
    assert on_disk.endswith(BODY + "\n")

    response = _markdown(client, installed["id"])
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["state"] == "ok"
    assert payload["markdown"] == BODY, "正文 = front matter 之后的那段字节，逐字相等"
    assert "description:" not in payload["markdown"], "front matter 不出现在正文里"
    assert not payload["markdown"].lstrip().startswith("---"), "正文不以 front matter 围栏开头"

    # 元数据是既有载荷（与 `GET /api/skills` 那一行逐字一致）。
    listed = client.get(f"/api/skills?scope_kind=agent&scope_id={member['id']}").json()["skills"]
    assert payload["skill"] == listed[0]

    # 与装配同源：装配层读进 `Skill.markdown` 的就是这一份。
    assembled = load_skills(listed, member_home=str(_member_home(member["id"])))
    assert [skill.markdown for skill in assembled] == [payload["markdown"]]


def test_missing_bytes_keep_the_metadata_and_null_body(client: TestClient, tmp_path: Path) -> None:
    """字节缺失：目录或 ``SKILL.md`` 不在 ⇒ ``missing``，元数据照常（行在、字节没了是真实状态）。"""
    member = create_agent(client).json()["agent"]
    installed = _install(client, source=_write_skill(tmp_path / "origin"), member_id=member["id"])

    (manifest_only,) = [
        p for p in _skill_dir(member["id"], installed["name"]).iterdir() if p.name == "SKILL.md"
    ]
    manifest_only.unlink()

    response = _markdown(client, installed["id"])
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "missing"
    assert payload["markdown"] is None
    assert payload["skill"]["name"] == installed["name"]
    assert payload["skill"]["content_hash"] == installed["content_hash"]

    # 整个目录没了也是同一种降级（不是错误）。
    (manifest_only.parent).rename(tmp_path / "moved-away")
    payload = _markdown(client, installed["id"]).json()
    assert payload["state"] == "missing"
    assert payload["markdown"] is None


@pytest.mark.parametrize("escape", ["manifest_symlink", "skills_dir_symlink"])
def test_paths_outside_the_global_root_are_never_returned(
    client: TestClient, tmp_path: Path, escape: str
) -> None:
    """根外 symlink（目标本身或祖先）⇒ ``invalid``，且响应里不含任何根外内容。

    ``manifest_symlink``：``SKILL.md`` 指向根外文件；
    ``skills_dir_symlink``：``<家目录>/skills`` 整体指向根外目录。
    """
    member = create_agent(client).json()["agent"]
    installed = _install(client, source=_write_skill(tmp_path / "origin"), member_id=member["id"])
    outside = tmp_path / "outside"
    _write_skill(outside / "skills" / installed["name"], body="SECRET-OUTSIDE-BODY")

    if escape == "manifest_symlink":
        manifest = _skill_dir(member["id"], installed["name"]) / "SKILL.md"
        manifest.unlink()
        os.symlink(outside / "skills" / installed["name"] / "SKILL.md", manifest)
    else:
        skills_dir = _member_home(member["id"]) / SKILLS_DIR
        os.rename(skills_dir, tmp_path / "member-skills-moved")
        os.symlink(outside / "skills", skills_dir)

    response = _markdown(client, installed["id"])
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "invalid"
    assert payload["markdown"] is None
    assert "SECRET-OUTSIDE-BODY" not in response.text, "守卫拒绝时绝不回任何根外内容"
    assert "SECRET-OUTSIDE-BODY" not in str(payload)


def test_unreadable_bytes_are_their_own_state(client: TestClient, tmp_path: Path) -> None:
    """读不出来：非 UTF-8（任何环境都能造）与权限拒绝（非 root 下可造）⇒ ``unreadable``。"""
    member = create_agent(client).json()["agent"]
    installed = _install(client, source=_write_skill(tmp_path / "origin"), member_id=member["id"])
    manifest = _skill_dir(member["id"], installed["name"]) / "SKILL.md"

    manifest.write_bytes(b"---\nname: review\ndescription: d\n---\n\n\xff\xfe not utf-8\n")
    payload = _markdown(client, installed["id"]).json()
    assert payload["state"] == "unreadable"
    assert payload["markdown"] is None
    assert payload["skill"]["name"] == installed["name"], "元数据照常返回"

    if os.geteuid() == 0:  # pragma: no cover - root 无视权限位，这一半在这里没有意义
        pytest.skip("以 root 运行时权限位不构成「读不出来」")
    manifest.write_text("---\nname: review\ndescription: d\n---\n\n# Review\n", encoding="utf-8")
    manifest.chmod(0o000)
    try:
        payload = _markdown(client, installed["id"]).json()
    finally:
        manifest.chmod(0o600)
    assert payload["state"] == "unreadable"
    assert payload["markdown"] is None


def test_unknown_skill_is_a_readable_404(client: TestClient) -> None:
    """``skill_id`` 不存在 ⇒ 404（复用既有的「技能不存在」文案）。"""
    response = _markdown(client, "sk_missing")
    assert response.status_code == 404, response.text
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert "sk_missing" in error["message"]


def test_channel_scoped_legacy_rows_are_rejected(data_dir: Path, tmp_path: Path) -> None:
    """频道级旧行没有家目录字节：400（复用「只接受成员作用域」文案）。"""
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
        response = _markdown(client, legacy["id"])
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "agent" in error["message"]
    assert "markdown" not in response.json()


def test_the_endpoint_is_read_only(client: TestClient, tmp_path: Path) -> None:
    """只读：不落消息、不产生轮次结果、不改预算、不写盘（字节与 mtime 都不变）。"""
    member = create_agent(client).json()["agent"]
    channel = create_channel(client, member_ids=[member["id"]]).json()["channel"]
    installed = _install(client, source=_write_skill(tmp_path / "origin"), member_id=member["id"])
    home = _member_home(member["id"])
    before = {
        "messages": messages(client, channel["id"]),
        "outcomes": outcomes(client, channel["id"]),
        "budget": budget(client, channel["id"]),
        "tree": _tree(home),
        "entries": sorted(path.name for path in home.iterdir()),
    }

    for _ in range(2):
        assert _markdown(client, installed["id"]).status_code == 200

    assert messages(client, channel["id"]) == before["messages"], "只读端点不落消息"
    assert outcomes(client, channel["id"]) == before["outcomes"], "只读端点不产生轮次结果"
    assert budget(client, channel["id"]) == before["budget"], "只读端点不改预算读数"
    assert _tree(home) == before["tree"], "只读端点不写成员家目录（含 mtime）"
    assert sorted(path.name for path in home.iterdir()) == before["entries"], "不新建任何条目"
