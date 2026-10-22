"""Skill 的安装与装配层（#152 / 规格 §3.6 / ADR-0021 §三）。

库里只存**元数据**（name / description / source_path / content_hash / enabled / scope），字节
是安装时复制到 ``workspace_root/.alibot/skills/`` 下的一棵树：

- ``scope_kind='agent'``：``.alibot/skills/<member_id>/<name>/``——每个成员一个分区；
- ``scope_kind='channel'``：``.alibot/skills/<channel_id>/<name>/``——频道级默认，装配时与成员
  分区合并，Agent 级同名**覆盖**频道级（与 MCP 声明同一优先级方向）。

安装只接受**本机目录**或**上传 zip**（D21）：不访问远端市场、不自动下载、不自动更新。安装前
校验 ``SKILL.md`` 的 YAML front matter（``name`` / ``description``）、路径安全与内容 hash；任何
一项不合法都**整体拒绝**——不落库、不留半棵树。

配置动作（安装 / 启停 / 删除）不落消息、不唤醒、不消耗预算（ADR-0011 同族；与 ADR-0021 §三
“MCP / Skill 是配置，不是消息或任务”一致）。删除只删配置行与工作区副本，**绝不删除用户源目录**。

装配层把启用的技能变成 AgentScope 的 :class:`agentscope.skill.Skill` 对象，由 ``Toolkit`` 注入
系统提示与内置 ``Skill`` 查看器；装配前重新核对 hash：工作区副本被改动或缺失时该技能不装配，
但不影响其他工具（失败可读、不拖垮一轮）。
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import posixpath
import re
import shutil
import uuid
import zipfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import frontmatter

from .i18n import Locale, render
from .workspace import (
    AGENTS_DIR,
    ALIBOT_DIR,
    WorkspaceConfigError,
    docker_global_member_home,
    docker_global_skills_home,
    ensure_state_partition,
    resolve_global_read_path,
    safe_partition_id,
    validate_workspace_root,
)

_log = logging.getLogger(__name__)

#: ``<workspace_root>/.alibot/skills``（与 AgentScope 自己的 ``skills/`` 分区分开命名）。
SKILLS_DIR = "skills"

# ─── 四层归属（#232 / ADR-0031 D4）─────────────────────────────────────────
#: 优先级从高到低；合并时先出现者胜出。项目层两级由库维护，全局层两级是纯文件。
SKILL_LAYER_PROJECT_AGENT = "project-agent"
SKILL_LAYER_PROJECT_CHANNEL = "project-channel"
SKILL_LAYER_GLOBAL_MEMBER = "global-member"
SKILL_LAYER_GLOBAL_PUBLIC = "global-public"

#: 运行期提示里的层名（两种语言同键集，见 ``i18n.CATALOGS``）。
_SKILL_LAYER_LABEL_KEYS = {
    SKILL_LAYER_PROJECT_AGENT: "skill.layer.project-agent",
    SKILL_LAYER_PROJECT_CHANNEL: "skill.layer.project-channel",
    SKILL_LAYER_GLOBAL_MEMBER: "skill.layer.global-member",
    SKILL_LAYER_GLOBAL_PUBLIC: "skill.layer.global-public",
}
#: 技能根目录里的清单文件。
SKILL_MANIFEST = "SKILL.md"
#: AgentScope 的技能名是 prompt 里的标识符：只接受无路径分隔符的短 slug。
SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
#: 上传 zip 的压缩字节上限（解包上限另算）。
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
#: 单文件解包上限。
MAX_SKILL_FILE_BYTES = 2 * 1024 * 1024
#: 整个技能树解包上限。
MAX_SKILL_TOTAL_BYTES = 8 * 1024 * 1024
#: 文件数量上限（防止小文件风暴）。
MAX_SKILL_FILES = 512
#: front matter 的 description 长度上限（会被写进系统提示）。
MAX_SKILL_DESCRIPTION_LENGTH = 1000


class SkillValidationError(Exception):
    """安装 / 删除输入不合法；``key`` 是 i18n 词条，由 HTTP 层渲染成可读正文。"""

    def __init__(
        self,
        key: str,
        *,
        field: str | None = None,
        status: int = 400,
        locale: Locale | None = None,
        **params: Any,
    ) -> None:
        self.key = key
        self.field = field
        self.status = status
        self.locale = locale
        self.params = params
        super().__init__(render(key, locale, **params))


@dataclass(frozen=True)
class _TreeFile:
    relative: str
    path: Path
    size: int


def skills_partition(root: str, scope_id: str) -> str:
    """一个 scope 的技能分区目录（``.alibot/skills/<scope_id>``）。"""
    return os.path.join(root, ALIBOT_DIR, SKILLS_DIR, safe_partition_id(scope_id))


def _variant(raw: Any) -> str:
    return str(raw or "").strip()


def _ensure_partition(root: str, partition: str, locale: Locale) -> None:
    """创建 ``.alibot/skills/<scope>``；根外 symlink 祖先在**写入前**拒绝。"""
    try:
        ensure_state_partition(root, partition)
    except WorkspaceConfigError as error:
        raise SkillValidationError(
            "error.skill_workspace_root_invalid",
            field="scope_id",
            locale=locale,
            reason=str(error),
        ) from error


def _real_root(raw: str, locale: Locale) -> str:
    """频道项目根必须是可用的绝对目录；读路径与安装路径同口径。"""
    try:
        return validate_workspace_root(raw)
    except WorkspaceConfigError as error:
        raise SkillValidationError(
            "error.skill_workspace_root_invalid",
            locale=locale,
            reason=str(error),
        ) from error


def resolve_install_root(
    database: Any,
    *,
    scope_kind: str,
    scope_id: str,
    channel_id: str | None,
    locale: Locale,
) -> str:
    """安装时的字节落点：由 scope + 可选频道上下文解析到**频道项目根**。

    Agent 级技能按成员分区，但一个成员可能属于多个频道（各自项目根不同）；因此安装必须能
    落到某个具体根上：显式 ``channel_id`` 优先，否则要求该成员**恰好只有一个**配置了根的频道。
    频道级技能直接用 ``scope_id`` 频道的根。
    """
    database.validate_skill_scope(scope_kind, scope_id)
    if scope_kind == "channel":
        root = _variant(database.channel_workspace_root(scope_id))
        if not root:
            raise SkillValidationError(
                "error.skill_workspace_root_missing",
                field="scope_id",
                locale=locale,
                scope_id=scope_id,
            )
        return _real_root(root, locale)

    if channel_id:
        if not database.is_channel_member(channel_id, scope_id):
            raise SkillValidationError(
                "error.skill_member_not_in_channel",
                field="channel_id",
                locale=locale,
                channel_id=channel_id,
            )
        root = _variant(database.channel_workspace_root(channel_id))
        if not root:
            raise SkillValidationError(
                "error.skill_workspace_root_missing",
                field="channel_id",
                locale=locale,
                scope_id=channel_id,
            )
        return _real_root(root, locale)

    roots = [root for root in database.workspace_roots_for_member(scope_id) if _variant(root)]
    if not roots:
        raise SkillValidationError(
            "error.skill_workspace_root_missing",
            field="scope_id",
            locale=locale,
            scope_id=scope_id,
        )
    if len(roots) > 1:
        raise SkillValidationError(
            "error.skill_channel_ambiguous",
            field="channel_id",
            locale=locale,
            scope_id=scope_id,
        )
    return _real_root(roots[0], locale)


def resolve_delete_roots(
    database: Any,
    *,
    scope_kind: str,
    scope_id: str,
    channel_id: str | None,
) -> list[str]:
    """删除时要清理的工作区副本所在根（不存在的根跳过；绝不触碰用户源目录）。

    Agent 级技能可能被安装到多个频道根，删除按**成员的所有已配置根**清理；频道级只清该频道根。
    """
    roots: list[str] = []
    candidates: list[str] = []
    if scope_kind == "channel":
        root = _variant(database.channel_workspace_root(scope_id))
        if root:
            candidates.append(root)
    else:
        # Agent 级技能可能装进多个频道根；成员退出频道后仍要能清掉遗留副本，因此按**库里
        # 所有已配置根**清理（只动该成员分区里的同名目录，不会碰到别的技能）。
        candidates.extend(database.all_workspace_roots())
        if channel_id:
            root = _variant(database.channel_workspace_root(channel_id))
            if root:
                candidates.append(root)
    for raw in candidates:
        try:
            root = validate_workspace_root(raw)
        except WorkspaceConfigError:
            continue
        if root not in roots:
            roots.append(root)
    return roots


def _manifest_root(directory: Path) -> Path:
    """接受「清单在根」或「包了一层单目录」两种形态（与 AgentScope 的解包约定一致）。"""
    if (directory / SKILL_MANIFEST).is_file():
        return directory
    children = [child for child in directory.iterdir() if child.is_dir()]
    if len(children) == 1 and (children[0] / SKILL_MANIFEST).is_file():
        return children[0]
    raise SkillValidationError("error.skill_manifest_missing", field="source", locale=None)


def _skill_files(root: Path) -> list[_TreeFile]:
    """校验一棵技能树：普通文件、无 symlink、无越界路径、有大小与数量上界。"""
    files: list[_TreeFile] = []
    total = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in list(dirnames):
            if (base / name).is_symlink():
                raise SkillValidationError("error.skill_path_invalid", field="source", locale=None)
        for name in filenames:
            path = base / name
            if path.is_symlink() or not path.is_file():
                raise SkillValidationError("error.skill_path_invalid", field="source", locale=None)
            size = path.stat().st_size
            if size > MAX_SKILL_FILE_BYTES:
                raise SkillValidationError(
                    "error.skill_file_too_large",
                    field="source",
                    locale=None,
                    limit=MAX_SKILL_FILE_BYTES,
                )
            total += size
            if total > MAX_SKILL_TOTAL_BYTES:
                raise SkillValidationError(
                    "error.skill_too_large",
                    field="source",
                    locale=None,
                    limit=MAX_SKILL_TOTAL_BYTES,
                )
            files.append(_TreeFile(path.relative_to(root).as_posix(), path, size))
            if len(files) > MAX_SKILL_FILES:
                raise SkillValidationError(
                    "error.skill_too_many_files",
                    field="source",
                    locale=None,
                    limit=MAX_SKILL_FILES,
                )
    files.sort(key=lambda item: item.relative)
    return files


def _content_hash(files: Sequence[_TreeFile]) -> str:
    """内容 hash：排序后的相对路径 + 文件大小 + 字节，任一变化都会改变结果。"""
    digest = hashlib.sha256()
    for item in files:
        digest.update(item.relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item.size).encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _front_matter(skill_root: Path, locale: Locale) -> tuple[str, str]:
    manifest = skill_root / SKILL_MANIFEST
    if manifest.is_symlink() or not manifest.is_file():
        raise SkillValidationError("error.skill_manifest_missing", field="source", locale=locale)
    try:
        text = manifest.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise SkillValidationError(
            "error.skill_front_matter_invalid",
            field="source",
            locale=locale,
            reason=str(error),
        ) from error
    try:
        parsed = frontmatter.loads(text)
    except Exception as error:  # YAML 解析失败：整条安装拒绝
        raise SkillValidationError(
            "error.skill_front_matter_invalid",
            field="source",
            locale=locale,
            reason=str(error),
        ) from error

    raw_name = parsed.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not SKILL_NAME_PATTERN.fullmatch(name):
        raise SkillValidationError("error.skill_name_invalid", field="name", locale=locale)
    raw_description = parsed.get("description")
    description = raw_description.strip() if isinstance(raw_description, str) else ""
    if not description:
        raise SkillValidationError(
            "error.skill_description_missing",
            field="description",
            locale=locale,
        )
    if len(description) > MAX_SKILL_DESCRIPTION_LENGTH:
        raise SkillValidationError(
            "error.skill_description_too_long",
            field="description",
            locale=locale,
            limit=MAX_SKILL_DESCRIPTION_LENGTH,
        )
    return name, description


def _prepare(source: Path, staging: Path, *, locale: Locale) -> dict[str, Any]:
    """校验技能根并**复制**到 staging；任何失败由调用方清理（源目录永不移动）。"""
    skill_root = _manifest_root(source)
    name, description = _front_matter(skill_root, locale)
    # 先校验源树，再复制；hash 在**复制结果**上计算，保证库里的值与工作区字节一致
    # （源目录在两次读之间被改动也不会让安装记录与副本对不上）。
    _skill_files(skill_root)
    shutil.copytree(skill_root, staging, symlinks=True, copy_function=shutil.copy2)
    content_hash = _content_hash(_skill_files(staging))
    return {"name": name, "description": description, "content_hash": content_hash}


def _install_prepared(
    prepared: Mapping[str, Any],
    *,
    root: str,
    scope_id: str,
    staging: Path,
    source_path: str,
    locale: Locale,
) -> dict[str, Any]:
    partition = skills_partition(root, scope_id)
    destination = os.path.join(partition, str(prepared["name"]))
    if os.path.lexists(destination):
        raise SkillValidationError(
            "error.skill_conflict",
            field="name",
            status=409,
            locale=locale,
            name=str(prepared["name"]),
        )
    os.replace(staging, destination)
    return {
        "name": str(prepared["name"]),
        "description": str(prepared["description"]),
        "content_hash": str(prepared["content_hash"]),
        "source_path": source_path,
        "dir_name": str(prepared["name"]),
    }


def install_directory(
    source_path: str | os.PathLike[str],
    *,
    root: str,
    scope_id: str,
    locale: Locale,
) -> dict[str, Any]:
    """从**本机目录**安装：校验 front matter / 路径 / hash 后复制进成员（或频道）分区。"""
    raw = _variant(os.fspath(source_path))
    if not raw or not os.path.isabs(raw):
        raise SkillValidationError("error.skill_source_invalid", field="source_path", locale=locale)
    source = Path(os.path.realpath(raw))
    if not source.is_dir():
        raise SkillValidationError("error.skill_source_not_directory", field="source_path", locale=locale)

    partition = skills_partition(root, scope_id)
    partition_real = os.path.realpath(partition)
    if os.path.commonpath([partition_real, str(source)]) == str(source):
        # staging 就建在 partition 里；把 partition 的祖先当来源会复制到自己内部。
        raise SkillValidationError("error.skill_source_invalid", field="source_path", locale=locale)
    _ensure_partition(root, partition, locale)
    staging_root = Path(partition) / f".staging-{uuid.uuid4().hex}"
    staging_root.mkdir()
    try:
        prepared = _prepare(source, staging_root / "skill", locale=locale)
        return _install_prepared(
            prepared,
            root=root,
            scope_id=scope_id,
            staging=staging_root / "skill",
            source_path=str(source),
            locale=locale,
        )
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _archive_member_path(info: zipfile.ZipInfo, *, locale: Locale) -> str:
    name = info.filename.replace("\\", "/")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if (
        not name
        or name.startswith("/")
        or name.startswith("\\")
        or ".." in parts
        or re.match(r"^[A-Za-z]:", name)
    ):
        raise SkillValidationError("error.skill_path_invalid", field="source", locale=locale)
    return "/".join(parts)


def _extract_archive(stream: BinaryIO, staging: Path, *, locale: Locale) -> None:
    total = 0
    count = 0
    with zipfile.ZipFile(stream) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                raise SkillValidationError("error.skill_archive_invalid", field="source", locale=locale)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:  # symlink
                raise SkillValidationError("error.skill_path_invalid", field="source", locale=locale)
            relative = _archive_member_path(info, locale=locale)
            if not relative:
                continue
            if info.file_size > MAX_SKILL_FILE_BYTES:
                raise SkillValidationError(
                    "error.skill_file_too_large",
                    field="source",
                    locale=locale,
                    limit=MAX_SKILL_FILE_BYTES,
                )
            total += info.file_size
            if total > MAX_SKILL_TOTAL_BYTES:
                raise SkillValidationError(
                    "error.skill_too_large",
                    field="source",
                    locale=locale,
                    limit=MAX_SKILL_TOTAL_BYTES,
                )
            count += 1
            if count > MAX_SKILL_FILES:
                raise SkillValidationError(
                    "error.skill_too_many_files",
                    field="source",
                    locale=locale,
                    limit=MAX_SKILL_FILES,
                )
            target = staging.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def install_zip(
    stream: BinaryIO,
    *,
    filename: str,
    root: str,
    scope_id: str,
    locale: Locale,
) -> dict[str, Any]:
    """从**上传 zip** 安装：路径安全、解包上限与 front matter 全部先校验，再原子落位。"""
    partition = skills_partition(root, scope_id)
    _ensure_partition(root, partition, locale)
    staging_root = Path(partition) / f".staging-{uuid.uuid4().hex}"
    staging_root.mkdir()
    extracted = staging_root / "extracted"
    extracted.mkdir()
    try:
        try:
            _extract_archive(stream, extracted, locale=locale)
        except SkillValidationError:
            raise
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, ValueError) as error:
            raise SkillValidationError(
                "error.skill_archive_invalid",
                field="source",
                locale=locale,
                reason=str(error),
            ) from error
        prepared = _prepare(extracted, staging_root / "skill", locale=locale)
        return _install_prepared(
            prepared,
            root=root,
            scope_id=scope_id,
            staging=staging_root / "skill",
            source_path=f"zip:{_variant(filename) or 'upload.zip'}",
            locale=locale,
        )
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def uninstall(root: str, *, scope_id: str, name: str) -> None:
    """删除工作区副本（只删这个 scope 的同名目录；不触碰任何 ``source_path``）。"""
    partition = skills_partition(root, scope_id)
    target = os.path.join(partition, name)
    if os.path.islink(target):
        os.unlink(target)
    elif os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
    with suppress(OSError):
        os.rmdir(partition)


def _load_manifest_markdown(skill_root: Path) -> tuple[str, str, str, float] | None:
    manifest = skill_root / SKILL_MANIFEST
    try:
        text = manifest.read_text(encoding="utf-8-sig")
        parsed = frontmatter.loads(text)
    except Exception:
        return None
    name = parsed.get("name")
    description = parsed.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        return None
    return name.strip(), description.strip(), parsed.content, manifest.stat().st_mtime


def load_skills(
    rows: Sequence[Mapping[str, Any]],
    *,
    root: str,
    container_root: str | None = None,
) -> list[Any]:
    """把启用技能读成 AgentScope ``Skill`` 对象；hash 不符 / 缺失 / 非法都只跳过该技能。

    ``container_root`` 非空时（Docker 后端）``dir`` 换成容器内路径；字节仍从同一个 host 项目根
    读取（宿主目录挂载到 ``/workspace``）。
    """
    from agentscope.skill import Skill

    try:
        root_real = validate_workspace_root(root)
    except WorkspaceConfigError:
        return []

    skills: list[Any] = []
    for row in rows:
        name = _variant(row.get("name"))
        if not SKILL_NAME_PATTERN.fullmatch(name):
            _log.warning("skip skill with invalid name: %r", name)
            continue
        scope = safe_partition_id(str(row.get("scope_id") or ""))
        skill_root = Path(root_real) / ALIBOT_DIR / SKILLS_DIR / scope / name
        if skill_root.is_symlink() or not skill_root.is_dir():
            _log.warning("skill %r is missing from the workspace partition %s", name, scope)
            continue
        try:
            files = _skill_files(skill_root)
        except SkillValidationError as error:
            _log.warning("skill %r cannot be read: %s", name, error)
            continue
        if _content_hash(files) != str(row.get("content_hash") or ""):
            _log.warning("skill %r content hash does not match the installed record", name)
            continue
        manifest = _load_manifest_markdown(skill_root)
        if manifest is None:
            _log.warning("skill %r has no readable SKILL.md", name)
            continue
        loaded_name, description, markdown, updated_at = manifest
        if loaded_name != name:
            _log.warning("skill %r manifest name %r does not match", name, loaded_name)
            continue
        if container_root:
            directory = posixpath.join(container_root, ALIBOT_DIR, SKILLS_DIR, scope, name)
        else:
            directory = str(skill_root)
        skills.append(
            Skill(
                name=name,
                description=description,
                dir=directory,
                markdown=markdown,
                updated_at=updated_at,
            )
        )
    return skills


def _skill_layer_label(layer: str, locale: Locale | None = None) -> str:
    key = _SKILL_LAYER_LABEL_KEYS.get(layer)
    return render(key, locale) if key else layer


def _global_skill_layer(
    global_root: str,
    parts: tuple[str, ...],
    *,
    container_dir: str | None,
) -> list[Any]:
    """读一层全局技能（成员家目录或公共库）；目录缺失 / 条目损坏都只跳过。

    全部读取经 :func:`workspace.resolve_global_read_path`：以全局根自己的 ``realpath`` 为根，
    根外 symlink 祖先与根外目标一律拒绝。这里**只读**，绝不创建目录或写占位文件。
    """
    from agentscope.skill import Skill

    directory = resolve_global_read_path(global_root, *parts)
    if directory is None:
        # 全局层是人的维护面，缺席是正常状态：本轮零条，但留一条可读记录（D9）。
        _log.info("global skill layer is absent: %s", posixpath.join(global_root, *parts))
        return []
    try:
        entries = sorted(Path(directory).iterdir(), key=lambda item: item.name)
    except OSError as error:
        _log.warning("cannot list global skill layer %s: %s", directory, error)
        return []

    skills: list[Any] = []
    for entry in entries:
        name = entry.name
        if not SKILL_NAME_PATTERN.fullmatch(name):
            _log.warning("skip global skill with invalid name: %r", name)
            continue
        skill_dir = resolve_global_read_path(global_root, *parts, name)
        if skill_dir is None:
            _log.warning("skip global skill %r: path is missing or escapes the global root", name)
            continue
        if not os.path.isdir(skill_dir):
            _log.warning("skip global skill %r: not a directory", name)
            continue
        manifest_path = resolve_global_read_path(global_root, *parts, name, SKILL_MANIFEST)
        if manifest_path is None:
            _log.warning("skip global skill %r: SKILL.md is missing or escapes the global root", name)
            continue
        try:
            _skill_files(Path(skill_dir))
        except (SkillValidationError, OSError) as error:
            _log.warning("skip global skill %r: cannot read its files: %s", name, error)
            continue
        manifest = _load_manifest_markdown(Path(skill_dir))
        if manifest is None:
            _log.warning("skip global skill %r: SKILL.md is not readable", name)
            continue
        loaded_name, description, markdown, updated_at = manifest
        if loaded_name != name:
            _log.warning("skip global skill %r: manifest name %r does not match", name, loaded_name)
            continue
        visible_dir = posixpath.join(container_dir, name) if container_dir is not None else skill_dir
        skills.append(
            Skill(
                name=name,
                description=description,
                dir=visible_dir,
                markdown=markdown,
                updated_at=updated_at,
            )
        )
    return skills


def load_global_skills(
    global_root: str,
    member_id: str,
    *,
    docker: bool = False,
) -> dict[str, list[Any]]:
    """读全局层的两层技能（纯文件，不进库）。

    - 成员家目录：``<全局根>/agents/<member_id>/skills/<name>/SKILL.md``；
    - 公共技能库：``<全局根>/skills/<name>/SKILL.md``。

    目录缺失 ⇒ 该层零条、不报错、不创建。``docker`` 为真时 ``Skill.dir`` 换成容器内可见路径
    （``/alibot-home/agents/<member_id>/skills/<name>`` 与 ``/alibot-home/skills/<name>``），
    字节仍从宿主全局根读取；挂载缺失时宿主目录通常也不存在，于是按「该层缺席」降级。
    """
    member_parts = (AGENTS_DIR, safe_partition_id(member_id), SKILLS_DIR)
    member_container = posixpath.join(docker_global_member_home(member_id), SKILLS_DIR) if docker else None
    return {
        SKILL_LAYER_GLOBAL_MEMBER: _global_skill_layer(
            global_root, member_parts, container_dir=member_container
        ),
        SKILL_LAYER_GLOBAL_PUBLIC: _global_skill_layer(
            global_root,
            (SKILLS_DIR,),
            container_dir=docker_global_skills_home() if docker else None,
        ),
    }


def _entry_name(entry: Any) -> str:
    if isinstance(entry, Mapping):
        return _variant(entry.get("name"))
    return _variant(getattr(entry, "name", ""))


def merge_skill_layers(
    layers: Sequence[tuple[str, Sequence[Any]]],
    *,
    locale: Locale | None = None,
) -> tuple[list[tuple[str, Any]], list[str]]:
    """按「先出现者胜出」合并各层技能声明：返回 (``(胜出层, 条目)`` 列表, 落选提示)。

    ``layers`` 必须按优先级从高到低排列（项目 Agent 级 → 项目频道级 → 全局成员家目录 →
    全局公共技能库）。去重键是技能名；条目可以是**未装配的库行**（``Mapping``，装配层随后
    按 ``enabled`` 过滤并读字节），也可以是已加载的 ``Skill`` 对象。每个落选条目产生一条
    含「名字 + 胜出层」的可读提示，调用方只把它拼进运行期 ``reason``，**不**放进 provider
    的 system prompt。
    """
    winners: list[tuple[str, Any]] = []
    notes: list[str] = []
    winning_layer: dict[str, str] = {}
    for layer, entries in layers:
        for entry in entries:
            name = _entry_name(entry)
            if not name:
                continue
            existing = winning_layer.get(name)
            if existing is not None:
                notes.append(
                    render(
                        "skill.assembly.shadowed",
                        locale,
                        name=name,
                        winner=_skill_layer_label(existing, locale),
                        loser=_skill_layer_label(layer, locale),
                    )
                )
                continue
            winning_layer[name] = layer
            winners.append((layer, entry))
    return winners, notes


def archive_payload(raw: bytes) -> BinaryIO:
    """把上传字节包成 ``ZipFile`` 接受的二进制流（同时供测试与 HTTP 层使用）。"""
    return io.BytesIO(raw)
