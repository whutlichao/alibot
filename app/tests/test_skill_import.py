"""#277 技能导入：``GET /api/skills/candidates`` 的只读候选面 + 经既有安装器写入。

用户 2026-10-25 人定（ADR-0036）：技能有两个安装来源——本机目录 / 上传 zip（既有），以及第三方
CLI（``npx skills add … -g -a codex``）落在 ``~/.agents/skills/`` 之后由本应用**导入**。应用不执行
npx、不起 subprocess、不下载第三方包；候选端点**只读**，导入写路径**复用** ``POST /api/skills``。

只经 HTTP 面取证（临时 ``HOME`` 由 conftest 的 autouse fixture 隔离，本文件不自己改 ``HOME``）：

1. 来源根缺失 ⇒ ``200``、``available=false``、空列表，且**没有创建**那个目录；
2. 合法 / 无 ``SKILL.md`` / front matter 坏 三种条目同时存在：坏条目逐条降级（``importable=false``
   + 非空可读原因），其余照常列出；非目录条目（``.skill-lock.json``）不进候选；
3. 该成员库里已有同名行 ⇒ ``installed=true``；
4. 选中项经 ``POST /api/skills``（``source_path`` = 候选 ``path``）⇒ ``201``，字节落进成员家目录，
   ``GET /api/skills`` 能看到，内容 hash 与源一致；
5. 重复导入 ⇒ ``409``，成员家目录字节**不被覆盖**；
6. 配置动作零副作用：不落消息、不产生轮次结果、预算读数不变；
7. ``scope_kind=channel`` ⇒ ``400``（与安装器同一口径）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from alibot.skills import read_skill_tree
from alibot.workspace import global_home_root
from fastapi.testclient import TestClient

from tests.conftest import budget, create_agent, create_channel, messages, outcomes


def _import_root() -> Path:
    """外部 CLI 的落点（``~/.agents/skills``）；``HOME`` 已被 conftest 指到临时目录。

    候选条目与 ``source`` 都经 ``realpath`` 规范化（macOS 的 ``/var`` → ``/private/var``），
    因此这里也按 ``realpath`` 比对。
    """
    return Path(os.path.realpath(Path(os.environ["HOME"]) / ".agents" / "skills"))


def _write_skill(directory: Path, *, name: str = "review", description: str = "代码评审清单") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n按步骤评审。\n",
        encoding="utf-8",
    )
    (directory / "checklist.txt").write_text("a\nb\n", encoding="utf-8")
    return directory


def _candidates(client: TestClient, *, scope_kind: str = "agent", scope_id: str):
    return client.get(
        "/api/skills/candidates",
        params={"scope_kind": scope_kind, "scope_id": scope_id},
    )


def _by_name(payload: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in payload["candidates"]}


def _member_skills_dir(member_id: str) -> Path:
    return Path(global_home_root()) / "agents" / member_id / "skills"


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _skills(client: TestClient, member_id: str) -> list[dict]:
    response = client.get("/api/skills", params={"scope_kind": "agent", "scope_id": member_id})
    assert response.status_code == 200, response.text
    return response.json()["skills"]


def test_missing_source_root_is_available_false_and_is_not_created(client: TestClient) -> None:
    member = create_agent(client).json()["agent"]
    root = _import_root()
    assert not root.exists(), "conftest 的临时 HOME 里不该先有 ~/.agents/skills"

    response = _candidates(client, scope_id=member["id"])

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["available"] is False
    assert payload["candidates"] == []
    assert payload["source"] == str(root), "响应要说出它看的到底是哪个来源根"
    assert not root.exists(), "只读扫描绝不创建第三方 CLI 的落点目录"


def test_bad_entries_degrade_one_by_one_and_the_rest_are_listed(client: TestClient) -> None:
    member = create_agent(client).json()["agent"]
    root = _import_root()
    _write_skill(root / "review", name="review", description="代码评审清单")
    (root / "t18-escape").mkdir(parents=True)  # 没有 SKILL.md
    broken = root / "broken-front-matter"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_text("---\nname: [unclosed\n---\n", encoding="utf-8")
    (root / ".skill-lock.json").write_text(json.dumps({"version": 1}), encoding="utf-8")

    response = _candidates(client, scope_id=member["id"])

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["available"] is True
    entries = _by_name(payload)
    assert sorted(entries) == ["broken-front-matter", "review", "t18-escape"], "非目录条目不进候选"

    assert entries["review"]["importable"] is True
    assert entries["review"]["reason"] is None
    assert entries["review"]["description"] == "代码评审清单"
    assert entries["review"]["path"] == str(root / "review")

    for name in ("t18-escape", "broken-front-matter"):
        assert entries[name]["importable"] is False, f"{name} 不可导入"
        assert isinstance(entries[name]["reason"], str) and entries[name]["reason"], f"{name} 要给出可读原因"
        assert entries[name]["path"] == str(root / name)

    assert entries["review"]["installed"] is False


def test_a_name_already_installed_is_reported_as_installed(client: TestClient) -> None:
    member = create_agent(client).json()["agent"]
    root = _import_root()
    _write_skill(root / "review", name="review")
    installed = client.post(
        "/api/skills",
        json={"scope_kind": "agent", "scope_id": member["id"], "source_path": str(root / "review")},
    )
    assert installed.status_code == 201, installed.text

    payload = _candidates(client, scope_id=member["id"]).json()

    assert _by_name(payload)["review"]["installed"] is True


def test_import_writes_the_bytes_into_the_member_home_with_the_source_hash(
    client: TestClient,
) -> None:
    member = create_agent(client).json()["agent"]
    root = _import_root()
    source = _write_skill(root / "review", name="review", description="代码评审清单")
    source_before = _tree(source)
    candidate = _by_name(_candidates(client, scope_id=member["id"]).json())["review"]

    response = client.post(
        "/api/skills",
        json={"scope_kind": "agent", "scope_id": member["id"], "source_path": candidate["path"]},
    )

    assert response.status_code == 201, response.text
    skill = response.json()["skill"]
    assert skill["name"] == "review"
    assert skill["source_path"] == str(source)
    assert skill["content_hash"] == read_skill_tree(source)["content_hash"], "内容 hash 与源一致"

    landed = _member_skills_dir(member["id"]) / "review"
    assert _tree(landed) == _tree(source), "成员家目录里的字节与来源逐字一致"
    assert _tree(source) == source_before, "来源目录只读：导入不搬走、不改写它"
    assert [row["name"] for row in _skills(client, member["id"])] == ["review"]


def test_a_second_import_is_409_and_leaves_the_installed_bytes_untouched(
    client: TestClient,
) -> None:
    member = create_agent(client).json()["agent"]
    root = _import_root()
    source = _write_skill(root / "review", name="review")
    body = {"scope_kind": "agent", "scope_id": member["id"], "source_path": str(source)}
    assert client.post("/api/skills", json=body).status_code == 201
    landed = _member_skills_dir(member["id"]) / "review"
    before = _tree(landed)

    # 来源在两次导入之间被改过：409 必须拒绝，且**不覆盖**已装好的那一份。
    (source / "checklist.txt").write_text("changed\n", encoding="utf-8")
    again = client.post("/api/skills", json=body)

    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "skill_conflict"
    assert _tree(landed) == before, "重复导入不覆盖成员家目录里的字节"
    assert len(_skills(client, member["id"])) == 1


def test_scanning_and_importing_have_no_message_round_or_budget_side_effects(
    client: TestClient,
) -> None:
    member = create_agent(client).json()["agent"]
    channel = create_channel(client, member_ids=[member["id"]]).json()["channel"]
    root = _import_root()
    _write_skill(root / "review", name="review")
    before = {
        "messages": messages(client, channel["id"]),
        "outcomes": outcomes(client, channel["id"]),
        "budget": budget(client, channel["id"]),
    }

    assert _candidates(client, scope_id=member["id"]).status_code == 200
    candidate = _by_name(_candidates(client, scope_id=member["id"]).json())["review"]
    assert (
        client.post(
            "/api/skills",
            json={"scope_kind": "agent", "scope_id": member["id"], "source_path": candidate["path"]},
        ).status_code
        == 201
    )

    assert messages(client, channel["id"]) == before["messages"], "配置动作不落消息"
    assert outcomes(client, channel["id"]) == before["outcomes"], "配置动作不产生轮次结果"
    assert budget(client, channel["id"]) == before["budget"], "配置动作不改预算读数"


def test_channel_scope_is_rejected_with_400(client: TestClient) -> None:
    member = create_agent(client).json()["agent"]
    channel = create_channel(client, member_ids=[member["id"]]).json()["channel"]

    response = _candidates(client, scope_kind="channel", scope_id=channel["id"])

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "validation_failed"
