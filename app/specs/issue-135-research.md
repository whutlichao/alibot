# Issue #135 研究：LocalWorkspace 文件工具的根限定

- 票型：research（G-research）
- 规格：`app/specs/agentscope-2-alignment.md` §3.6 / §5；ADR-0021
- 被测版本：`agentscope==2.0.10`（共享 venv；`python -c "import agentscope; print(agentscope.__version__)"` → `2.0.10`）
- 复跑脚本：`app/specs/issue-135-research/probe_workspace_root.py`
- 本报告不修改任何产品源码（`app/alibot/**`、`app/tests/**` 未动）。

## 结论（给 #148 用）

1. **2.0.10 的内置文件工具没有“工作区根限定”**。`LocalWorkspace.list_tools()` 返回的
   `Read/Write/Edit/Glob/Grep` 都不接收 `workdir`，工具执行路径上也没有“落在根内”的判断。
   只有 `Bash` 被设置了 `cwd=workdir`，而 `cwd` 不是沙箱。
2. **2.0.10 的 `realpath` 改动只在 `ToolBase` 的权限辅助函数里**（`_path_in_allowed_working_path`
   与 `_is_dangerous_path`），只被 `Write` / `Edit` / `Bash` 调用，而且只影响
   `ACCEPT_EDITS` 下的“是否自动放行”，不是执行准入闸门。**没有覆盖全部内置工具**，不能满足
   规格 §3.6 的“根外一律 `ToolResultState.ERROR`”。
3. 实测确认五类输入能越过根：**根外绝对路径**、**含 `..` 的绝对路径**、**根外 symlink**、
   **根外 hardlink（realpath 无法识别）**、**Glob/Grep 省略 `path` 时落到进程 cwd**。
   `Read/Write/Edit` 对“相对 `../`”报 ERROR 只是因为它们要求绝对路径，不是路径校验。
4. 装配层必须在**调用前**做根检查并重写路径。需要包装的工具：`Read`、`Write`、`Edit`、
   `Glob`、`Grep` 五个；`Bash` 默认不挂载（ADR-0021 / D15），显式开启时也无法做根限定，
   只能依赖逐次审批与“无沙箱”告知。
5. `LocalWorkspace.workdir` 是 `os.path.abspath`（不是 `realpath`）；根路径本身是 symlink 时，
   直接拿工具返回的 realpath 与它比较会误拒。装配层必须先把根 `realpath` 一次。
6. **残余**：hardlink 让根内路径指向根外 inode，任何基于路径的检查都无法识别；检查与执行之间的
   TOCTOU 同样是残余。见“仍可逃逸的输入形状”。

## 一、被测对象与临时目录布局

`LocalWorkspace.list_tools()`（2.0.10）返回六个工具：

```python
# agentscope/workspace/_local_workspace.py:141-162
backend = self.get_backend()
shell = Bash(cwd=self.workdir, backend=backend)  # 只有 shell 拿到 workdir
return [
    shell,
    Edit(backend=backend),
    Glob(**glob_kwargs),
    Grep(backend=backend),
    Read(backend=backend),
    Write(backend=backend),
]
```

`workdir` 来自 `os.path.abspath(workdir)`（同文件 `:120`）。文件工具没有 `cwd` 参数；
`Glob` / `Grep` 省略 `path` 时使用 `await backend.getcwd()`，而 `LocalBackend.getcwd()`
就是 `os.getcwd()`（`agentscope/tool/_builtin/_backend.py:909-916`）——**进程 cwd，不是
workspace 根**。

探针构造的临时目录（脚本退出时删除；`AS2_PROBE_KEEP=1` 保留并打印路径）：

```text
$TMP = <mktemp -d 的 realpath>              # macOS: /private/var/folders/.../as2-135-XXXX
├── root/                                   # workspace_root；LocalWorkspace(workdir=root)
│   ├── inside.txt                          # 根内读/写基准
│   ├── edit-target.txt                     # 根内 Edit 基准
│   ├── read_link.txt    -> ../outside/read-secret.txt
│   ├── write_link.txt   -> ../outside/write-victim.txt
│   ├── edit_link.txt    -> ../outside/edit-link-victim.txt
│   ├── grep_link.txt    -> ../outside/grep-secret.txt
│   ├── link_dir         -> ../outside
│   ├── read_hardlink.txt  ←→ ../outside/read-hardlink-victim.txt
│   ├── write_hardlink.txt ←→ ../outside/write-hardlink-victim.txt
│   └── edit_hardlink.txt  ←→ ../outside/edit-hardlink-victim.txt
├── outside/                                # 根外
│   ├── read-secret.txt / read-hardlink-victim.txt
│   ├── edit-abs.txt / edit-dotdot.txt / edit-link-victim.txt / edit-hardlink-victim.txt
│   ├── write-victim.txt / write-hardlink-victim.txt
│   ├── grep-secret.txt
│   └── newdir/deep/created.txt             # Write 在根外新建的父目录
└── process-cwd/
    └── cwd-marker.txt                      # 脚本 chdir 到这里，观察 Glob/Grep 的默认 path
```

复跑命令（仓库根；只写临时目录）：

```bash
.venv/bin/python app/specs/issue-135-research/probe_workspace_root.py
# 保留现场：
AS2_PROBE_KEEP=1 .venv/bin/python app/specs/issue-135-research/probe_workspace_root.py
```

每行格式：`<tool> | <case> | <verdict> | state=<raw> | <detail>`。`verdict` 的口径：
`ToolResultState.ERROR` → `ERROR`；工具调用抛异常 → `EXCEPTION`；其余 chunk → `SUCCESS`
（`Read/Write/Edit/Bash` 是流式工具，最后一个 chunk 的 `state` 常为 `running`，故保留原始 state，
不要把 `running` 当成失败）。

## 二、逐工具读数

以下读数来自 2.0.10 实跑；`$TMP` 是当次临时目录。判据覆盖票面四类输入：根内、根外绝对路径、
`../` 逃逸、根外 symlink；另加 hardlink / 相对路径 / 省略 path 作为补充。

### Read（`file_path` 必须是绝对路径）

| case | verdict | detail（节选） |
| --- | --- | --- |
| 根内绝对路径 | SUCCESS | `1 inside-marker` |
| 根外绝对路径 | **SUCCESS** | `1 read-outside-marker` |
| 根外经 `..`（绝对） | **SUCCESS** | `1 read-outside-marker` |
| 相对 `../outside/...` | ERROR | `file_path must be an absolute path`（仅 `isabs` 检查，不是根检查） |
| 根外 symlink（根内 `read_link.txt`） | **SUCCESS** | `1 read-outside-marker` |
| 根外 hardlink（根内 `read_hardlink.txt`） | **SUCCESS** | `1 read-hardlink-original` |

### Write

| case | verdict | detail（节选） |
| --- | --- | --- |
| 根内绝对路径 | SUCCESS | 写入 `$TMP/root/written.txt` |
| 根外绝对路径 | **SUCCESS** | 写入 `$TMP/outside/pwned.txt` |
| 根外经 `..`（绝对） | **SUCCESS** | 写入 `$TMP/root/../outside/pwned2.txt` |
| 根外新建多级目录 | **SUCCESS** | 创建并写入 `$TMP/outside/newdir/deep/created.txt`（`LocalBackend.write_file` 内部 `os.makedirs`） |
| 相对 `../outside/...` | ERROR | `file_path must be an absolute path` |
| 根外 symlink（根内 `write_link.txt`） | **SUCCESS** | 根外目标 `write-victim.txt` 内容变为 `WRITTEN-BY-PROBE` |
| 根外 hardlink（根内 `write_hardlink.txt`） | **SUCCESS** | 根外目标 `write-hardlink-victim.txt` 内容变为 `WRITTEN-BY-PROBE` |

副作用读数（写后回读）：根内 / 根外 / 根外 `..` / 根外新目录 / 根外 symlink 目标 / 根外 hardlink
目标全部为 `WRITTEN-BY-PROBE`。

### Edit

| case | verdict | detail（节选） |
| --- | --- | --- |
| 根内绝对路径 | SUCCESS | `Successfully replaced 1 occurrence` |
| 根外绝对路径 | **SUCCESS** | 根外 `edit-abs.txt: alpha → beta` |
| 根外经 `..`（绝对） | **SUCCESS** | 根外 `edit-dotdot.txt: alpha → beta` |
| 相对 `../outside/...` | ERROR | `file_path must be an absolute path` |
| 根外 symlink（根内 `edit_link.txt`） | **SUCCESS** | 根外 `edit-link-victim.txt: alpha → beta` |
| 根外 hardlink（根内 `edit_hardlink.txt`） | **SUCCESS** | 根外 `edit-hardlink-victim.txt: alpha → beta` |

### Glob（`path` 可选；省略时用进程 cwd）

| case | verdict | detail（节选） |
| --- | --- | --- |
| 根内 `path=root, pattern=*.txt` | SUCCESS | 返回 `$TMP/root/...` 文件 |
| 根外绝对 `path=outside` | **SUCCESS** | 返回 `$TMP/outside/...` 文件 |
| 根外经 `..` `path=root/../outside` | **SUCCESS** | 返回 `$TMP/root/../outside/...` |
| 相对 `path=../outside` | **SUCCESS** | 返回 `../outside/...`（相对进程 cwd 解析） |
| symlink 目录作为 `path`（`root/link_dir`） | **SUCCESS** | 返回 `$TMP/root/link_dir/...`（跟随 base symlink） |
| symlink 目录出现在 pattern（`link_dir/*.txt`） | SUCCESS（无结果） | `No files found matching pattern`——helper 对中间段 `follow_symlinks=False` |
| 省略 `path` | **SUCCESS(根外)** | 返回 `$TMP/process-cwd/cwd-marker.txt`（进程 cwd，不是 root） |
| `pattern=../outside/*.txt` | SUCCESS（无结果） | helper 按名字匹配，pattern 里的 `..` 不会穿越；逃逸来自 `path` 参数 |

### Grep（`path` 可选；省略时用进程 cwd）

| case | verdict | detail（节选） |
| --- | --- | --- |
| 根内 `path=root` | SUCCESS | `$TMP/root/inside.txt` |
| 根外绝对 `path=outside` | **SUCCESS** | `$TMP/outside/grep-secret.txt` |
| 根外经 `..` | **SUCCESS** | `$TMP/root/../outside/grep-secret.txt` |
| 相对 `path=../outside` | **SUCCESS** | `../outside/grep-secret.txt` |
| symlink 目录作为 `path` | **SUCCESS** | `$TMP/root/link_dir/grep-secret.txt` |
| symlink 文件作为 `path` | **SUCCESS** | `$TMP/root/grep_link.txt` |
| 省略 `path` | **SUCCESS(根外)** | `$TMP/process-cwd/cwd-marker.txt`（进程 cwd） |

### Bash 对照（`LocalWorkspace.list_tools()` 默认包含）

| case | verdict | detail |
| --- | --- | --- |
| `pwd; cat $TMP/outside/read-secret.txt; id -u` | SUCCESS | `$TMP/root read-outside-marker 501` |

`cwd` 确实是 workspace 根，但命令照样用绝对路径读到了根外文件。这与 ADR-0021 的
“cwd 不是沙箱”一致；本地默认必须过滤掉 Bash。

## 三、2.0.10 `ToolBase` 的 realpath 改动覆盖到哪里

源码事实（`.venv/lib/python3.11/site-packages/agentscope/`）：

| 位置 | 内容 | 被谁调用 |
| --- | --- | --- |
| `tool/_base.py:390-440` | `_path_in_allowed_working_path()`，`:422/:425` 用 `os.path.realpath` 比较进程 cwd + `context.working_directories` | `Write`（`_write.py:149`）、`Edit`（`_edit.py:172`）、`Bash`（`_bash.py:375`） |
| `tool/_base.py:442-490` | `_is_dangerous_path()`，`:471` 用 `realpath` 判断敏感文件/目录 | `Write`（`_write.py:132`）、`Edit`（`_edit.py:155`）、`Bash`（`_bash.py:563`） |
| `tool/_builtin/_read.py:176-186` | `check_permissions` 直接 `PASSTHROUGH` | —（`call()` 只检查 `isabs`/存在性/目录） |
| `tool/_builtin/_glob.py:135-142` | `check_permissions` 直接 `PASSTHROUGH` | — |
| `tool/_builtin/_grep.py:181-186` | `check_permissions` 直接 `PASSTHROUGH` | — |

`_path_in_allowed_working_path` 的语义是：在 `PermissionMode.ACCEPT_EDITS`（以及 `DONT_ASK`）下，
如果目标在“进程 cwd 或 `working_directories`”内就返回 `ALLOW`；不在就 `PASSTHROUGH`，交给
`PermissionEngine` 的规则与默认 ASK 处理。它**不返回 DENY**，也不在执行前阻断。探针在设置
`working_directories={root: ...}`、`mode=ACCEPT_EDITS` 后逐形状读取该函数（五个工具共享基类实现，
读数相同）：

| 形状 | 返回值 | 说明 |
| --- | --- | --- |
| 根内 | True | 自动放行 |
| 根本身 | True | 允许根本身 |
| 根外绝对 | False | 只意味着“不自动放行”，之后仍可能被 allow 规则放行/ASK 批准 |
| 根外经 `..` | False | realpath 已折叠 `..` |
| 根内 symlink → 根外 | False | realpath 生效 |
| 根内 hardlink → 根外 inode | True | realpath 仍是根内路径，无法识别 |
| 兄弟前缀（`root-evil/x.txt`） | False | `realpath` + `startswith(root + os.sep)` 没有前缀误判 |

对照 `PermissionEngine`（`permission/_engine.py`）：`DEFAULT` / `ACCEPT_EDITS` 的模式兜底是
`ASK`，`EXPLORE` 对只读工具直接 `ALLOW`，都没有“路径必须在根内”的分支。因此：

> **2.0.10 的 realpath 改动没有覆盖所有内置工具；对 `Read`/`Glob`/`Grep` 完全不生效，对
> `Write`/`Edit` 也只是自动放行分类，不是根限定。** 规格 §3.6 要求的“根外一律
> `ToolResultState.ERROR`”必须由装配层实现。

## 四、仍可逃逸的输入形状

按“用装配层根检查能否拦住”分类：

| # | 形状 | 实测 | 装配层 realpath 检查 |
| --- | --- | --- | --- |
| 1 | 根外绝对路径（五个工具） | Read/Write/Edit/Glob/Grep 全部成功 | 拦住 |
| 2 | 绝对路径含 `..`（例如 `root/../outside/x`） | 五个工具全部成功 | 拦住（realpath 折叠后判根外） |
| 3 | 相对 `../outside/x` | Read/Write/Edit ERROR（仅因要求绝对路径）；Glob/Grep **成功**（相对进程 cwd） | 拦住：相对路径先锚到根再 realpath；不合法则 ERROR |
| 4 | 根内 symlink → 根外文件/目录 | Read/Write/Edit/Grep 成功；Glob 作为 base `path` 成功（作为中间 pattern 段不被跟随） | 拦住（realpath 解析到根外） |
| 5 | 根内 hardlink → 根外 inode | Read/Write/Edit 成功；Glob 列出；Grep 读到 | **拦不住**（路径仍指向根内）；残余风险 |
| 6 | Glob/Grep 省略 `path` | 落到进程 cwd（可能是仓库根或任意目录） | 拦住：省略时注入根 |
| 7 | Write 在根外新建父目录 | `outside/newdir/deep/created.txt` 成功 | 拦住（目标 realpath 在根外；注意 `os.path.realpath` 对不存在尾部按字面拼接，父目录校验仍成立） |
| 8 | 根路径本身是 symlink | `LocalWorkspace(workdir=alias).workdir != realpath(root)`（读数 `False`） | 装配层必须对根先 realpath；否则会误拒根内路径 |
| 9 | check 与执行之间替换 symlink（TOCTOU） | 未构造稳定复现；见 `_backend.py` 的 `open()` 与检查分离 | **拦不住**；残余风险 |
| 10 | `Glob` 的 `pattern` 里写 `..` / 绝对段 | 实测 `../outside/*.txt` 无结果（helper 按名字匹配） | 无需依赖；仍建议拒绝 `..` 段做纵深防御 |
| 11 | `~` / 环境变量 | 本地工具不展开 `~`（`os.path` 直接当字面路径） | 无需特殊处理；不要自行 `expanduser` 放大面 |
| 12 | 大小写/前缀 | `root-evil`（兄弟目录）在 realpath gate 为 False | 用 `commonpath` 或“root + 分隔符”比较，别裸 `startswith(root)` |

Hardlink 的处置建议：#148 的验收只承诺“路径 realpath 落在根内”；hardlink 作为已知残余写进
报告/代码注释即可。若要求更强，必须在挂载/文件系统层解决（不同设备/只读源），超出本票。

## 五、装配层根检查伪代码与包装清单

### 5.1 包装清单

| 工具 | 需要包装 | 被改写的输入 | 说明 |
| --- | --- | --- | --- |
| `Read` | 是 | `file_path` | 工具要求绝对路径；相对路径由装配层锚到根并改写为绝对 |
| `Write` | 是 | `file_path` | 同上；注意它会 `mkdir -p` 父目录 |
| `Edit` | 是 | `file_path` | 同上 |
| `Glob` | 是 | `path`（可选）、`pattern` | `path` 省略 → 根；相对 → 根；pattern 拒绝 `..` 段 |
| `Grep` | 是 | `path`（可选） | 同上；`pattern` 是正则，不是路径 |
| `Bash` | **不包装、默认不挂载** | — | `cwd` 不是沙箱；本地显式开启时每次审批，并明示“可越过项目根” |

### 5.2 核心函数

```python
import os

ROOT = os.path.realpath(channel.workspace_root)  # 只算一次；也用这个值建 LocalWorkspace


def _within(real: str, root: str) -> bool:
    if real == root:
        return True
    try:
        return os.path.commonpath([real, root]) == root
    except ValueError:  # Windows 跨盘
        return False


def resolve_in_root(raw: str | None, *, allow_missing: bool = False) -> str:
    """把工具输入规范化为根内绝对 realpath；根外抛 WorkspaceRootViolation。"""
    if raw is None:
        return ROOT  # 只允许 Glob/Grep 省略 path
    candidate = raw if os.path.isabs(raw) else os.path.join(ROOT, raw)
    real = os.path.realpath(candidate)
    if not _within(real, ROOT):
        raise WorkspaceRootViolation(raw, real, ROOT)
    return real
```

要点：

- **根先 realpath**；`LocalWorkspace(workdir=ROOT)` 也用 `ROOT`，避免 `abspath` 与 `realpath` 混用。
- 相对路径**锚到根**，不是进程 cwd；这与 ADR-0021 “文件工具的相对路径从这里起算”一致。
- 用 `commonpath`（或 `real == root or real.startswith(root + os.sep)`），不要裸 `startswith(root)`
  （`root-evil` 前缀）。
- 规范化后把**绝对 realpath** 传给工具，避免工具再按进程 cwd 解释相对路径。

### 5.3 接入方式（推荐：middleware）

`ToolBase` 没有构造后追加 middleware 的公开 API（`_middlewares` 只在 `__init__` 赋值），所以装配层
**自己构造**五个文件工具，而不是直接使用 `LocalWorkspace.list_tools()` 的返回值：

```python
class WorkspaceRootGuard(ToolMiddlewareBase):
    def __init__(self, root: str) -> None:
        self.root = root

    async def on_tool_call(self, tool, input_kwargs, next_handler):
        key = {
            "Read": "file_path",
            "Write": "file_path",
            "Edit": "file_path",
            "Glob": "path",
            "Grep": "path",
        }[tool.name]
        try:
            real = resolve_in_root(input_kwargs.get(key))  # None -> ROOT
            if tool.name == "Glob":
                _reject_dotdot_segments(input_kwargs["pattern"])
        except WorkspaceRootViolation as exc:
            yield ToolChunk(
                content=[TextBlock(text=f"Error: path escapes workspace root: {exc.raw}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )
            return
        input_kwargs[key] = real
        async for chunk in next_handler(**input_kwargs):
            yield chunk
```

```python
backend = workspace.get_backend()
guard = WorkspaceRootGuard(ROOT)
tools = [
    Read(backend=backend, middlewares=[guard]),
    Write(backend=backend, middlewares=[guard]),
    Edit(backend=backend, middlewares=[guard]),
    Glob(backend=backend, middlewares=[guard]),
    Grep(backend=backend, middlewares=[guard]),
]
# Bash 不在此列表；allow_local_shell 时才从 workspace.list_tools() 取 Bash，
# 并保留 PermissionEngine 的逐次 ask。
```

若 #148 坚持复用 `list_tools()`：可以过滤掉 `Bash`，然后对每个文件工具做
`tool._middlewares.append(guard)`；这是私有字段，建议只留一处并加注释说明版本约束。
优先选“自己构造”。

### 5.4 与 Docker 后端的关系

本报告的读数全部来自 Local 后端。Docker 后端的工具路径是**容器内** `/workspace/...`，主机
`os.path.realpath` 不适用；容器挂载本身限制了可达文件系统，但“容器内路径是否落在 `/workspace`”
仍需后端语义的检查。`BackendBase`（2.0.10）没有通用的 `realpath` 原语，因此本票不承诺
Docker 侧的装配层根检查；#148 只需在 Local 后端实现，Docker 侧在票面/代码注释里标注为后续。

## 六、与规格/ADR 的对应

| 要求 | 实测/结论 |
| --- | --- |
| §3.6 “Read/Write/Edit/Glob/Grep 每个输入路径先 realpath，必须落在 `workspace_root` 内” | 2.0.10 未实现；装配层按 §五 实现 |
| §3.6 “`..`、绝对路径逃逸、根外 symlink 一律 `ToolResultState.ERROR`” | 当前五工具全部成功；包装后返回 ERROR chunk |
| §3.6 “2.0.10 `ToolBase` 已把 `abspath` 改成 `realpath`，但仍需装配层根检查” | 改动只在 `_path_in_allowed_working_path` / `_is_dangerous_path`；覆盖 2/5 文件工具且非准入闸门 |
| §3.6 “本地 Bash 默认不挂” | `list_tools()` 默认包含 Bash，且 cwd=root 仍可读根外；装配层必须过滤 |
| §5.1 “pytest + 真实临时目录：`../`、绝对路径、根外 symlink 均 ERROR；根内读写成功；默认无 Bash” | 可作为 #148 的测试矩阵；symlink/hardlink 用例需 POSIX（CI Linux 可用） |
| ADR-0021 “文件工具的相对路径从这里起算” | 装配层必须把相对路径锚到根并改写为绝对；工具本身不会 |

## 七、复跑与验收对照

```bash
# 版本
.venv/bin/python -c "import agentscope; print(agentscope.__version__)"

# 全量读数（约 1-2 秒；只写 $TMP）
.venv/bin/python app/specs/issue-135-research/probe_workspace_root.py

# 保留现场
AS2_PROBE_KEEP=1 .venv/bin/python app/specs/issue-135-research/probe_workspace_root.py
```

- 判据 1（逐工具四类输入）：见 §二；每类均有 verdict 与 detail。
- 判据 2（realpath 覆盖范围 + 可逃逸形状）：见 §三、§四；源码行号 + gate 读数 + 12 类形状。
- 判据 3（装配层伪代码 + 工具清单）：见 §五；未改产品源码。
- 判据 4（报告路径 + 临时布局 + 复跑命令）：本文件、§一、§七；脚本
  `app/specs/issue-135-research/probe_workspace_root.py`。

## 八、已知边界（未验证）

- Docker / E2B 后端未实测（本票只覆盖 LocalWorkspace；见 §5.4）。
- Windows 路径语义（`ntpath`、跨盘 `commonpath`）未实测；伪代码已按 `ValueError → 拒绝` 处理。
- hardlink 与 TOCTOU 是路径检查的结构性残余，本报告不给“已解决”的结论。
- 探针只调用工具层，不经过 `PermissionEngine`；权限/审批组合属于 #148/#151 的验收范围。
