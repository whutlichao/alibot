# #236 验收：全局远端 MCP 的确认（成员 + 名字）

父规格：`app/specs/agent-home-layers-spec.md`（D8 / D10 / Out of Scope 第 6 条 / T1 / T2 / T4 / T6）；
决策正本：`docs/adr/0031-agent-home-is-a-global-layer-beside-the-project-boundary.md`。

本票复用 #233 的公开入口 `alibot.mcp.load_global_mcp_declarations`（顶层形状固定
`{"servers": [...]}`）与 `alibot.mcp.runtime_status`，**没有**第二份 `mcp.json` 解析。
确认行写进既有 `remote_consent_scope` 的 `mcp` 档：`scope_kind="mcp"`、
`scope_id="global:<member_id>:<name>"`；不新增 `scope_kind`、不改表、`SCHEMA_VERSION` 不变。

## 人类面

| 方法与路径 | 作用 |
| --- | --- |
| `GET /api/agents/{member_id}/global-mcp-servers` | 该成员全局 `mcp.json` 的只读条目 + 运行期读数 |
| `POST /api/agents/{member_id}/global-mcp-servers/{name}/consent` | 远端条目的数据面确认（幂等） |

列表每行只有 `name` / `transport` / `is_remote` / `consented` / `runtime`；「可确认」由
`is_remote && !consented` 派生，stdio 因此永远不在可确认的那一半里。Studio 把它放在
**Agent 详情 → 运行时页签**的只读小节（`agent-detail-section-global-mcp`），不渲染编辑 / 启停 /
删除控件，也不写回全局层。

## 判据 → 落点 → 证据

| 验收判据 | 落点 | 证据 |
| --- | --- | --- |
| 人类面存在按「成员 + 名字」唯一定位的只读列表与确认入口（HTTP + Studio） | `api.py` 两条路由；`service.WorkspaceService.global_mcp_servers` / `confirm_global_mcp_server`；`studio/app.js` 的 `globalMcpSection` / `globalMcpRowNode` / `confirmGlobalMcpServer` | `tests/test_global_mcp_consent.py::test_list_lists_only_real_global_entries_with_runtime_readings`、`::test_confirm_writes_the_existing_scope_row_is_idempotent_and_survives_restart`；`tests/agent_detail_ui.test.cjs` 的「全局 MCP 小节是只读行」用例 |
| 确认写入既有 `remote_consent_scope` 的 `mcp` 行，`scope_id` 为 `global:<member_id>:<name>`；幂等、只存时间；不新增 `scope_kind`、不改表、`SCHEMA_VERSION` 不变 | `service.confirm_global_mcp_server` → `Database.record_remote_consent_scope("mcp", …)`；`db.py` / schema 未改 | 同上确认用例断言 `scope_kind=mcp`、`scope_id=global:<id>:remote`、载荷只有三键、二次确认时间不变、重启后仍已确认 |
| 确认前远端工具不连接、运行期给 `consent_required`；确认后重启仍保持，并在下一轮装配连接 | `load_global_mcp_declarations(consent_of=…)` + `assemble_mcp_tools` 既有分支；`api.create_app` 的 `global_mcp_consent_of` 接线未改 | `::test_http_consent_unlocks_the_next_assembly`：确认前 `attempts == []` 且 `runtime.error` 含「确认」；HTTP 确认后 `consented=True`、`attempts == ["remote"]` |
| 全局 stdio 条目在写入口被拒绝且原因可读，也不出现在可确认列表 | `confirm_global_mcp_server` 的 `is_remote` 分支（复用 `error.mcp_consent_local`）；Studio 只给 `is_remote && !consented` 渲染按钮 | `::test_write_path_refuses_missing_member_name_and_stdio_without_writing_a_row`；前端用例断言 stdio 行没有 `data-global-mcp-consent` |
| 成员不存在、名字未出现、条目不是远端时：写入口返回可读错误且不写确认行 | 成员：`Database.get_agent` → NotFound(404)；名字：`error.global_mcp_declaration_not_found`；非远端：`error.mcp_consent_local` | 同上用例：404/400/400 均有可读 `error.message`；随后用公开读路径 `has_remote_consent_scope` 断言三个合成 id 都没有行，并用「同名条目改成远端后仍 `consented=false`」做黑盒反证 |
| 全局 `mcp.json` 被人工改名或删除后，旧确认行不导致报错，应用不自动清理、不写回全局层 | 列表/确认只读当前文件；残留行只被 `consent_of` 读取，无清理路径 | `::test_file_renamed_or_deleted_after_consent_leaves_the_row_alone_and_never_errors`：改名/删除后 GET 均 200 空列表，确认尝试 400 可读，文件仍不存在，库里旧行仍在 |
| Studio 的全局条目行是只读行，不渲染编辑/启停/删除按钮；新文案 zh-CN / en 成对 | `studio/app.js` 的全局 MCP 小节与事件委派；`studio/i18n.js` 两份词条；`studio/app.css` 一行布局 | `agent_detail_ui.test.cjs` 断言三个控件选择器均为 `null`、确认 POST 发往 `/api/agents/mem-1/global-mcp-servers/remote/consent`、zh/en 键成对；`i18n_ui.test.cjs` 全量键集比对通过 |
| 测试使用临时全局根与临时 HOME；不联网；不读写真实 `~/.alibot` | 新测试文件的 autouse `isolated_home`（每用例 `tmp_path/home`）；远端传输用 `MCPClient.connect` 替身 | `test_global_mcp_consent.py` 全部 5 条；装配用例只计数不建连 |

## 红 → 绿读数

开发循环（`cd app && ../.venv/bin/python -m pytest …`）：

| 切片 | 红读数 | 绿读数 |
| --- | --- | --- |
| 只读列表 | `test_list_lists_only_real_global_entries_with_runtime_readings` → `assert 404 == 200`（1 failed） | `1 passed` |
| 确认 + 幂等 + 重启 | `test_confirm_writes_the_existing_scope_row_is_idempotent_and_survives_restart` → `assert 404 == 200`（1 failed, 1 passed） | `2 passed` |
| 三类错误不写行 | `assert declaration is not None`（AssertionError） | `3 passed` |
| 文件改名/删除 | 设计上由前两切片直接兑现（新增为回归守卫） | `4 passed` |
| 装配联动 | 设计上由确认写路径与既有装配层直接兑现（新增为端到端守卫） | `5 passed` |
| Studio 只读小节 | `Agent 详情的全局 MCP 小节…` → `stdio 条目仍要只读展示`（pass 24 / fail 1） | pass 25 / fail 0 |

定稿门禁读数：

```text
# cd app && ../.venv/bin/python -m pytest tests/test_global_mcp_consent.py
5 passed in 0.48s

# 受影响 pytest（test_global_mcp_consent / test_remote_data_flow / test_mcp_servers /
#   test_global_mcp / test_backend_copy / test_agent_detail / test_global_skills /
#   test_test_layering_guard / test_docs_vocabulary_guard）
85 passed in 12.38s

# node --test app/tests/agent_detail_ui.test.cjs        → 25 pass / 0 fail
# node --test app/tests/i18n_ui.test.cjs               → 53 pass / 0 fail
# node --test app/tests/*.test.cjs                     → 259 pass / 0 fail
# .venv/bin/python -m ruff check app                   → All checks passed!
# .venv/bin/python -m mypy                             → Success: no issues found in 121 source files
# pyright app/alibot app/tests                         → 0 errors, 0 warnings, 0 informations
```

## 已知偏差 / 风险

1. **`scripts/check.sh --fast` 的格式检查在本分支基线上已红**：`ruff format --check app` 只报
   `app/alibot/agent_seam.py`（缺少一个空行），该文件在本票中**未改动**：
   `git diff --stat 1fcf08e -- app/alibot/agent_seam.py` 为空。其余门禁（ruff check / mypy /
   pyright / node glob）均通过；本票新增/修改的 Python 文件已格式化。
2. 列表没有额外的 `can_consent` 字段：可确认性由 `is_remote && !consented` 派生，避免同一事实
   有两个来源；stdio 的 `consented` 恒为 `false`。
3. 写入口的错误码统一是 `validation_failed`（400），成员不存在沿用既有 `NotFound`（404 +
   `error.agent_not_found`）；三类失败都在任何写入之前返回。
4. 本票只动人类面与既有确认行；全局 `mcp.json` 的解析/合并仍归 #233，全局条目的安装/编辑/删除
   仍明确不在范围内。
