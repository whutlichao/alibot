# #233 验收：MCP 声明三层合并（项目就近、同名覆盖）

> 实施票：<https://github.com/whutlichao/alibot/issues/233>
> 规格正本：#228（`app/specs/agent-home-layers-spec.md` 的 D4 MCP 半边、D8 装配前置、D9、D10、D11 Seam A、T1/T2/T4/T6）
> ADR：`docs/adr/0031-*.md`
> 基线：`agent-home-layers-integration` @ `85c42c1`

## 判据 → 落点 → 证据

| # | 验收判据 | 代码落点 | 测试（`app/tests/test_global_mcp.py` 除注明外） |
| --- | --- | --- | --- |
| 1 | 三层同名合并后每名恰好一条：项目 Agent 级 > 项目频道级 > 全局成员家目录；全局条目永不覆盖同名项目条目 | `mcp.merge_mcp_layers`（层按优先级传入，同名先出现者胜出）；`agent_seam._mcp_declarations` 组装三层 | `test_three_layers_keep_one_entry_per_name_and_report_losers_only_in_the_reason`（真 seam + stub provider 读本轮工具清单：`team` 三层同名只留 Agent 级、`channel` 两层同名只留频道级、`global` 只有全局层） |
| 2 | 落选条目（含频道级被 Agent 级覆盖）各一条含「名字 + 胜出层」的可读运行期提示 | `merge_mcp_layers` 返回 `(winners, [(name, winning_layer)])`；seam 用 `mcp.assembly.shadowed` + `mcp.layer.*` 渲染进独立列表 `assembly_notes` | 同上：`reason` 里 `team` 两条（频道级 / 全局级落选）、`broken` 与 `channel` 各一条；胜出的 `global` 无提示 |
| 3 | 提示**不进** provider system prompt | `assembly_notes` 与 `workspace_warnings` 分开；只拼 `RoundOutcome.reason` | 同上：`_system_text(...)` 不含「被更高优先级」「项目频道级」 |
| 4 | 落选条目不装配、不连接 | 合并发生在 `_assemble_mcp` 之前；落选者不进入 `assemble_mcp_tools` | 同上：落选的频道 `broken`（命令不存在）与全局 `team` / `channel` 的 `runtime_status` 均为 `None`；工具清单每名一条 |
| 5 | 非法 JSON ⇒ 全局层零条 + 可读日志，项目层不受影响 | `load_global_mcp_declarations`（`json.loads` 失败 → warning + `[]`） | `test_invalid_json_means_zero_global_declarations_and_a_readable_log`、`test_invalid_global_json_leaves_the_project_layer_untouched` |
| 6 | 单条目形状非法（transport / command / args / url / `env_names`）⇒ 跳过该条 + 日志 | `load_global_mcp_declarations` 逐条 `normalize_mcp_declaration`；`InvalidMCPDeclaration.field` 进日志 | `test_a_bad_entry_is_skipped_while_good_entries_still_load`、`test_a_bad_global_entry_is_skipped_without_blocking_the_good_entry` |
| 7 | 文件缺失 ⇒ 零条、不报错、不创建目录；读取走全局只读守卫（含根外 symlink 拒绝） | `load_global_mcp_declarations` → `workspace.resolve_global_read_path(全局根, "agents", safe(member), "mcp.json")` | `test_a_missing_global_file_means_zero_declarations_without_creating_anything`、`test_a_global_file_symlinked_outside_the_global_root_is_ignored`、`test_a_round_does_not_create_the_global_layer_or_write_global_entries_to_the_database`（运行前后目录清单相等） |
| 8 | 全局 stdio 的 `cwd` 仍是项目侧成员分区（Docker 内 `/workspace`），不指向全局层 | `_assemble_mcp` 的 `cwd` 逻辑未改：local `mount.agent_dir`，Docker `None`（框架回退 `workspace.workdir`） | 同上第 1 条：全局胜出条目的工具描述里 `cwd=<project>/.alibot/agents/<member_id>` |
| 9 | 全局远端未确认时不连接，运行期读数为 `consent_required`；确认状态按 `global:<member_id>:<name>` 从既有确认行读取 | 全局条目填 `consent_required=is_remote` / `consented=consent_of(id)`；复用 `assemble_mcp_tools` 既有分支；seam 新注入 `global_mcp_consent_of`，生产接 `Database.has_remote_consent_scope` | `test_unconfirmed_global_remote_is_not_connected_until_the_consent_row_exists`（`MCPClient.connect` 替身计数：确认前 0 次 + `consent_required` 文案；写入既有确认行后尝试 1 次） |
| 10 | 全局条目补齐合成 `id` / `scope_id` 与 `consent_required` 推导；不新增 `scope_kind` | `load_global_mcp_declarations`：`id = scope_id = "global:<member_id>:<name>"`，无 `scope_kind` | `test_global_entries_get_synthetic_identity_and_derive_remote_consent` |
| 11 | `env_names` 只存变量名，值只在装配时从进程环境读；库 / 文件 / 响应不出现值 | 沿用 `mcp._declared_env`；全局加载只产出变量名 | 同上第 1 条（`SECRET_VALUE` 只出现在 stub server 的工具描述里）；`test_a_round_does_not_create...`（`mcp_server` 表仍为空，全局 `mcp.json` 由测试以变量名写入） |
| 12 | 无项目根的私信轮次不装配 MCP（维持既有门控） | `mount is None` ⇒ 不调用 `_assemble_mcp`，也不读全局声明 | `test_a_round_without_a_project_mount_assembles_no_mcp_at_all` |
| 13 | 测试用最小本地 stdio 服务与传输替身；全局根 / HOME 指向临时目录，不联网、不读写真实 `~/.alibot` | `isolated_home` autouse fixture；`STUB_SERVER` 子进程；远端用 `MCPClient.connect` 替身 | 全文件 13 个用例 |
| 14 | 不新增 `scope_kind`、不改表、`SCHEMA_VERSION` 不变 | `db.py` 无迁移 / 表结构改动；全局声明无库行 | `git diff 85c42c1 -- app/alibot/db.py` 只有读路径与校验委托；`SCHEMA_VERSION` 仍为 23 |

## 全局 `mcp.json` 的顶层形状（供 #236 复用）

本票按票面授权定义并固定为：

```json
{
  "servers": [
    {
      "name": "notes",
      "transport": "stdio",
      "command": "python3",
      "args": ["-m", "example_mcp"],
      "env_names": ["EXAMPLE_TOKEN"],
      "enabled": true
    },
    {
      "name": "remote-search",
      "transport": "http",
      "url": "https://example.test/mcp",
      "env_names": ["SEARCH_TOKEN"]
    }
  ]
}
```

- 顶层必须是对象且含 `servers` 数组；裸数组、其它键形态都按「非法文件」处理（全局层零条 + warning）。
- 条目形状与库内项目声明**同一套校验**（`alibot.mcp.normalize_mcp_declaration`）：`stdio` 需要
  `command`；`http` / `sse` 需要 http(s) URL，`sse` path 以 `/sse` 或 `/messages/` 结尾、`http`
  不得指向这两种 path；`args` / `env_names` 都是字符串数组；`enabled` 缺省 `true`；未知键忽略。
- 同一文件内重名：首条生效，其余跳过并记 warning（项目层同名唯一由库约束保证，本票不新增规则）。
- `env_names` 只写变量名；值只在装配时从进程环境读取。

### #236 的复用入口

```python
from alibot.mcp import load_global_mcp_declarations, normalize_mcp_declaration

# 列表：合成 id 直接作为确认键（远端条目附带 consented 读数）
rows = load_global_mcp_declarations(
    global_home,
    member_id,
    consent_of=lambda scope_id: database.has_remote_consent_scope("mcp", scope_id),
)
# 每条：id == scope_id == "global:<member_id>:<name>"，is_remote / consent_required / consented

# 人工编辑写入前的校验（与库写路径同一实现；非法时抛 InvalidMCPDeclaration，field 指出字段）
normalized = normalize_mcp_declaration(candidate)
```

确认写路径沿用 `Database.record_remote_consent_scope("mcp", f"global:{member_id}:{name}")`；
**不要**新增 `scope_kind`、不要改表。

## 红 → 绿读数

| 切片 | 红（实现前） | 绿（实现后） |
| --- | --- | --- |
| 1. 全局文件加载 | `pytest tests/test_global_mcp.py` → `ImportError: cannot import name 'load_global_mcp_declarations' from 'alibot.mcp'`（collection error） | `tests/test_global_mcp.py tests/test_mcp_servers.py tests/test_mcp_assembly.py` → `16 passed, 1 skipped` |
| 2. 项目未合并读路径 | `tests/test_global_mcp.py::test_project_layers_keep_both_scopes_while_the_merged_reader_is_unchanged` → `AttributeError: 'Database' object has no attribute 'mcp_layers_for_target'` | 同用例通过；`tests/test_mcp_assembly.py::test_agent_scope_overrides_channel_scope_by_name` 仍绿（原合并读路径形状不变） |
| 3. 三层合并 + 提示 | `test_three_layers_...` → provider 工具清单缺全局条目，且日志出现 `Duplicate tool name 'mcp__team__echo'`（未合并的两层重复装配） | 同用例通过（每名一条；提示只进 reason） |
| 4. 远端确认 | 行为锁：加载器的 `consent_of` 语义在切片 1 已红→绿；seam 注入在切片 3 落地后该用例首次运行即绿 | `test_unconfirmed_global_remote_is_not_connected_until_the_consent_row_exists` 通过 |
| 5. 降级 / DM / 创建纪律 / cwd | 行为锁：实现已覆盖后首次运行即绿，作为验收判据保留 | 同组用例通过 |

命令（从 `app/` 运行；`app/pyproject.toml` 已带 `-q`，命令行不再补 `-q`）：

```sh
../.venv/bin/python -m pytest tests/test_global_mcp.py
# 13 passed in 3.9s

../.venv/bin/python -m pytest tests/test_global_mcp.py tests/test_mcp_assembly.py \
  tests/test_mcp_servers.py tests/test_agent_round.py tests/test_team_mode.py \
  tests/test_backend_copy.py tests/test_test_layering_guard.py tests/test_docs_vocabulary_guard.py
# 108 passed, 1 skipped in 21.06s

../.venv/bin/python -m pytest    # 代码定稿后全量（从 app/）
# 1100 passed, 10 skipped in 619.83s
```

## 门禁读数

从 worktree 根运行：

```sh
scripts/check.sh --fast
# ruff check: All checks passed!
# ruff format --check: 290 files already formatted
# mypy（仓库根）: Success: no issues found
# pyright: 0 errors, 0 warnings, 0 informations
# node --test（glob）: tests 258 / pass 258 / fail 0
# 门禁全绿
```

## 范围与偏差

- **零 schema 变更**：`SCHEMA_VERSION` 仍为 23；没有新表 / 新列 / 新 `scope_kind`。
- **零新 seam / 零新服务模块**：全局 MCP 读路径是 `AgentScopeAgentSeam.__init__` 的新增窄注入
  `global_mcp_consent_of`；全局文件加载 / 合并是 `app/alibot/mcp.py` 的公开纯函数，不依赖库。
- **生产接线变更**：`api.create_app` 的 `mcp_of` 从已合并的 `mcp_servers_for_target` 换成未合并的
  `mcp_layers_for_target`（保留原方法的返回形状与既有测试）；同时接 `global_mcp_consent_of`。
- **落选提示的粒度**：每个被同名覆盖的声明各一条提示，因此同一个名字被两层同时覆盖时会出现两条
  文案相同的提示。这是票面「落选条目各一条」的直接实现；提示只给「名字 + 胜出层」，不额外暴露
  落选层（避免把内部层序写进用户文案）。
- **失败轮的提示**：本票只在成功收口路径把 `assembly_notes` 拼进 `reason`；失败分支保持既有形状
  （与 `workspace_warnings` 在失败分支的既有处理一致）。
- **Docker `cwd`**：代码路径未改；本票用 local 后端的工具描述取证 `cwd=<项目>/.alibot/agents/<member>`。
  Docker 侧沿用既有 `cwd=None` → `/workspace` 分支，不把全局挂载点当工作目录。
- **不在本票范围**：人类面确认入口与确认写路径（#236）、全局 `mcp.json` 的 UI 安装 / 编辑 / 删除、
  全局规则 / 技能 / 记忆、Docker bind 实际落地、任何 schema 变更。
- **日志语言**：沿用仓库既有日志风格（英文、含文件路径与字段名），不做 i18n；用户可见的落选提示走
  `i18n.CATALOGS` 的中英成对词条。
