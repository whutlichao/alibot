"""#284 mcp 页签下块：成员家目录 ``mcp.json`` 的读写端点。

两条端点落在既有全局 MCP 视图端点的**同族子资源**（设计正本 D7 / D10）：

- ``GET /api/agents/{member_id}/global-mcp-file`` ⇒ ``{member_id, exists, text, servers, path}``；
- ``PUT /api/agents/{member_id}/global-mcp-file`` 读 ``{"text": <整份原文>}``，三道校验全部通过
  才**逐字**写盘，任一条不合法 ⇒ ``400`` + 可读原因 + ``fields`` 指认那一条 / 那个字段，**零写入**。

判据只经 HTTP 人类面取证（``TestClient`` 黑盒 + 临时 ``HOME``）：真实 ``~/.alibot`` 一个字节都不读、
不写。**唯一**直接读磁盘的地方是断言「写盘之后文件字节与提交文本逐字相等」与「拒绝时文件 mtime /
内容完全不变」——那两件事没有别的观察面。

``servers`` 的口径：文件里 ``servers`` 数组的**条目计数**（原文事实，不做合规过滤）；文件缺席或
不是 JSON 时为 0。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from alibot.workspace import global_home_root, safe_partition_id

from tests.conftest import backend, create_agent

#: 合法但**非标准**的原文：键序是 transport 在前、缩进两格、末尾带换行。它必须被逐字写回。
VERBATIM_WITH_TRAILING_NEWLINE = (
    "{\n"
    '  "servers": [\n'
    '    {"transport": "stdio", "command": "python3", "name": "notes", "args": ["-m", "notes"]},\n'
    "    {\n"
    '        "url": "https://example.test/mcp",\n'
    '        "transport": "http",\n'
    '        "name": "remote"\n'
    "    }\n"
    "  ]\n"
    "}\n"
)

#: 同一份形态、**末尾不带换行**：末尾换行与否是原文事实，不做补齐。
VERBATIM_WITHOUT_TRAILING_NEWLINE = VERBATIM_WITH_TRAILING_NEWLINE[:-1]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把 HOME 指到临时目录：本文件绝不读写真实的 ``~/.alibot``。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def _home() -> Path:
    return Path(global_home_root())


def _member_home(member_id: str) -> Path:
    return _home() / "agents" / safe_partition_id(member_id)


def _document_path(member_id: str) -> Path:
    return _member_home(member_id) / "mcp.json"


def _read(client, member_id: str):
    return client.get(f"/api/agents/{member_id}/global-mcp-file")


def _write(client, member_id: str, text: str):
    return client.put(f"/api/agents/{member_id}/global-mcp-file", json={"text": text})


def _tree(root: Path) -> list[str]:
    """整棵树的清单（目录带尾斜杠）：用来断言「拒绝时一个目录都没建」。"""
    if not root.exists():
        return []
    return sorted(
        path.relative_to(root).as_posix() + ("/" if path.is_dir() else "") for path in root.rglob("*")
    )


def _seed(member_id: str, raw: str) -> Path:
    path = _document_path(member_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return path


# ─── 1. 读：原文逐字 + 条目计数；缺席是「没有这个文件」而不是空文件 ──────────
def test_read_returns_the_verbatim_text_and_the_entry_count(data_dir: Path) -> None:
    raw = '{\n    "servers": [ {"name": "a", "transport": "stdio", "command": "python3"} ],\n    "note": "x"\n}\n'
    with backend(data_dir) as client:
        member = create_agent(client).json()["agent"]
        _seed(member["id"], raw)

        response = _read(client, member["id"])

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["member_id"] == member["id"]
    assert payload["exists"] is True
    assert payload["text"] == raw, "读到的是文件原文（逐字，不重排、不摘要）"
    assert payload["servers"] == 1, "servers = 文件里 servers 数组的条目计数"
    assert payload["path"] == os.path.realpath(str(_document_path(member["id"]))), (
        "path 是这份文件的真实路径（界面用它写缺失提示）"
    )


def test_read_of_a_missing_file_reports_absence_and_creates_nothing(data_dir: Path) -> None:
    with backend(data_dir) as client:
        member = create_agent(client).json()["agent"]
        # 家目录在、文件不在：这是「第一次配置」的真实起点。
        assert _member_home(member["id"]).is_dir(), "建 Agent 时应用会建出成员家目录"
        assert not _document_path(member["id"]).exists()
        before = _tree(_home())

        response = _read(client, member["id"])

        assert response.status_code == 200, response.text
        assert response.json() == {
            "member_id": member["id"],
            "exists": False,
            "text": "",
            "servers": 0,
            # 文件缺席时路径读数仍要给：缺失提示要写出它该在哪。
            "path": os.path.realpath(str(_document_path(member["id"]))),
            # 文件级可读问题与运行期装配共用同一份判定（缺席 ⇒ 没有问题）。
            "issues": [],
        }
        assert _tree(_home()) == before, "读端点不写盘、不建目录"
        assert not _document_path(member["id"]).exists(), "读端点不创建 mcp.json"


# ─── 2. 非法 JSON ⇒ 400 + 可读原因，文件 mtime 与内容完全不变 ────────────────
def test_invalid_json_is_a_readable_400_and_leaves_the_file_completely_untouched(data_dir: Path) -> None:
    with backend(data_dir) as client:
        member = create_agent(client).json()["agent"]
        path = _seed(member["id"], VERBATIM_WITH_TRAILING_NEWLINE)
        before = (path.read_bytes(), path.stat().st_mtime_ns)

        response = _write(client, member["id"], '{\n  "servers": [ }\n')

        assert response.status_code == 400, response.text
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert error["message"].strip(), "400 要带一句可读原因"
        assert "text" in error["fields"], "字段级错误要指认被提交的那份原文"
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before, "零写入：内容与 mtime 都不变"


# ─── 3. 顶层形状不对 ⇒ 400，零写入 ──────────────────────────────────────────
@pytest.mark.parametrize(
    "text",
    [
        '[{"name": "a", "transport": "stdio", "command": "python3"}]',
        '{"servers": {"name": "a", "transport": "stdio", "command": "python3"}}',
        '{"servers": "none"}',
        "[]",
    ],
)
def test_a_wrong_top_level_shape_is_a_400_with_no_write(data_dir: Path, text: str) -> None:
    with backend(data_dir) as client:
        member = create_agent(client).json()["agent"]
        path = _seed(member["id"], VERBATIM_WITHOUT_TRAILING_NEWLINE)
        before = (path.read_bytes(), path.stat().st_mtime_ns)

        response = _write(client, member["id"], text)

        assert response.status_code == 400, response.text
        error = response.json()["error"]
        assert error["message"].strip()
        assert "servers" in error["fields"]
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before, "零写入"


# ─── 4. 单条声明形状非法 ⇒ 400 且 fields 能指认是哪一条的哪个字段，零写入 ────
def test_an_invalid_single_declaration_points_at_that_entry_and_field(data_dir: Path) -> None:
    with backend(data_dir) as client:
        member = create_agent(client).json()["agent"]
        path = _seed(member["id"], VERBATIM_WITH_TRAILING_NEWLINE)
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        # 第 2 条（下标 1）的 transport 不在词表里：与库写路径同一份归一化内核会拒它。
        text = json.dumps(
            {
                "servers": [
                    {"name": "notes", "transport": "stdio", "command": "python3"},
                    {"name": "broken", "transport": "carrier-pigeon"},
                ]
            }
        )

        response = _write(client, member["id"], text)

        assert response.status_code == 400, response.text
        error = response.json()["error"]
        fields = error["fields"]
        assert any(key.startswith("servers[1]") for key in fields), (
            f"字段级错误要指出出错的那一条：{sorted(fields)}"
        )
        assert any("transport" in key for key in fields), f"还要指出出错字段：{sorted(fields)}"
        assert error["message"].strip()
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before, "零写入"

        # 同一份形状合法时是 200（反证：上面那条 400 来自那一条声明，不是写路径本身不通）。
        fixed = json.dumps(
            {
                "servers": [
                    {"name": "notes", "transport": "stdio", "command": "python3"},
                    {"name": "broken", "transport": "stdio", "command": "python3"},
                ]
            }
        )
        assert _write(client, member["id"], fixed).status_code == 200


# ─── 5. 合法原文 ⇒ 写盘后逐字相等（键序 / 缩进 / 末尾换行都原样） ─────────────
@pytest.mark.parametrize(
    "text",
    [VERBATIM_WITH_TRAILING_NEWLINE, VERBATIM_WITHOUT_TRAILING_NEWLINE],
)
def test_a_valid_document_is_written_verbatim(data_dir: Path, text: str) -> None:
    with backend(data_dir) as client:
        member = create_agent(client).json()["agent"]
        path = _document_path(member["id"])
        assert not path.exists()

        response = _write(client, member["id"], text)

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["exists"] is True, "家目录存在、文件不存在 ⇒ 保存建出该文件"
        assert payload["text"] == text
        assert payload["servers"] == 2
        assert payload["path"] == os.path.realpath(str(path)), "写端点回同一个真实路径"
        assert path.read_bytes() == text.encode("utf-8"), "写盘字节与提交文本逐字相等（不重排、不格式化）"

        # 覆盖也是逐字：第二次提交的原文替换第一次的字节。
        replaced = text.replace('"notes"', '"notes-renamed"')
        assert _write(client, member["id"], replaced).status_code == 200
        assert path.read_bytes() == replaced.encode("utf-8")


# ─── 6. 家目录不存在 ⇒ 400 且不创建任何目录 ─────────────────────────────────
def _remove_member_home(member_id: str) -> None:
    """把成员家目录整个删掉：模拟「全局层这一级还不存在」的起点。

    建 Agent 时应用会把家目录与 ReMe 工作区一并建出来（ADR-0035），因此「家目录不存在」
    只能从「删掉它」这个起点取证。
    """
    home = _member_home(member_id)
    for path in sorted(home.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and not path.is_symlink():
            path.rmdir()
        else:
            path.unlink()
    home.rmdir()


def test_a_missing_member_home_is_a_400_and_creates_no_directory(data_dir: Path) -> None:
    with backend(data_dir) as client:
        member = create_agent(client).json()["agent"]
        _remove_member_home(member["id"])
        before = _tree(_home())
        assert not _member_home(member["id"]).exists()

        response = _write(client, member["id"], VERBATIM_WITH_TRAILING_NEWLINE)

        assert response.status_code == 400, response.text
        error = response.json()["error"]
        assert error["message"].strip(), "要说明「家目录不存在」，而不是静默失败"
        assert _tree(_home()) == before, "应用不自动创建全局层：保存前后整棵 HOME 树的清单不变"
        assert not _member_home(member["id"]).exists()
        assert not _document_path(member["id"]).exists()


# ─── 7. 根外 symlink 祖先 / 根外目标 ⇒ 拒绝，且绝不写到根外 ─────────────────
def test_a_document_symlinked_outside_the_global_root_is_refused(data_dir: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside-mcp.json"
    outside.write_text(VERBATIM_WITH_TRAILING_NEWLINE, encoding="utf-8")
    outside_before = outside.read_bytes()
    with backend(data_dir) as client:
        member = create_agent(client).json()["agent"]
        path = _document_path(member["id"])
        os.symlink(outside, path)

        response = _write(client, member["id"], VERBATIM_WITHOUT_TRAILING_NEWLINE)

        assert response.status_code == 400, response.text
        assert response.json()["error"]["message"].strip()
        assert outside.read_bytes() == outside_before, "根外文件一个字节都不动"
        assert path.is_symlink(), "拒绝发生在写盘之前：链接本身原样保留"

        read = _read(client, member["id"])
        assert read.status_code == 200
        assert read.json()["text"] == "", "只读守卫同样不看根外内容"
        assert read.json()["exists"] is False
        assert read.json()["path"] is None, "根外目标连路径也不给（界面退到不带路径的提示）"


def test_a_member_home_symlinked_outside_the_global_root_is_refused(data_dir: Path, tmp_path: Path) -> None:
    outside_home = tmp_path / "outside-member-home"
    outside_home.mkdir()
    with backend(data_dir) as client:
        member = create_agent(client).json()["agent"]
        home = _member_home(member["id"])
        _remove_member_home(member["id"])
        os.symlink(outside_home, home, target_is_directory=True)

        response = _write(client, member["id"], VERBATIM_WITH_TRAILING_NEWLINE)

        assert response.status_code == 400, response.text
        assert response.json()["error"]["message"].strip()
        assert list(outside_home.iterdir()) == [], "根外目录里没有新建任何文件"
        assert _read(client, member["id"]).json()["exists"] is False
        assert _read(client, member["id"]).json()["path"] is None


# ─── 8. 成员不存在 ⇒ 404（与既有全局 MCP 视图端点同一条校验） ───────────────
def test_an_unknown_member_is_a_readable_404_for_both_ends(data_dir: Path) -> None:
    with backend(data_dir) as client:
        read = _read(client, "not-a-member")
        assert read.status_code == 404, read.text
        assert "not-a-member" in read.json()["error"]["message"]

        write = _write(client, "not-a-member", VERBATIM_WITH_TRAILING_NEWLINE)
        assert write.status_code == 404, write.text
        assert "not-a-member" in write.json()["error"]["message"]
        assert not _member_home("not-a-member").exists(), "404 也不建目录"
