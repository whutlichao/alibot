## Summary

Agent 详情 mcp 页签按用户反馈做减法，并补一处真实路径（功能面不动）：

```diff
 mcp tab
-  MCP 声明                                  (标题)
-  来自库行 mcp_server（scope_kind=agent）…    (数据库来源说明)
-  只显示环境变量名；变量值由服务进程…          (环境变量说明)
   库行列表 / 新增表单
+  [ 添加 MCP ]                              (btn-primary，主按钮)
   家目录 mcp.json                            (小标题保留)
-  来自这个 Agent 家目录的 mcp.json（原文）…    (文件来源说明)
   缺失提示：…还没有 mcp.json（<真实路径>）     (新增后端 path)
   原文文本域 + 条目数 + 保存
-  全局 MCP（只读）                            (小标题)
   条目读数 + 远端确认动作（保留）
```

后端只做**增量字段**：`GET/PUT /api/agents/{id}/global-mcp-file` 的响应多一个 `path`（该成员 `mcp.json` 的真实路径；文件缺席时给写守卫算出的同一目标，路径越界时 `null`）。前端缺失提示用这个路径，不自己拼 `~/.alibot/...`。

```text
global_mcp_document(member_id)
  path   = resolve_global_read_path(...)   # 原文读取（存在性守卫）
  target = resolve_global_write_path(...)  # 同一目标的 realpath（不判存在性）
  return {member_id, exists, text, servers, path: path ?? target}
```

## Evidence

- **Before:** mcp 页签顶部三行说明（标题 / 库行来源 / 环境变量说明）、文件块来源说明、条目视图「全局 MCP（只读）」标题都在；缺失提示不写路径；「添加 MCP」是普通 `btn btn-sm`。
  **After:** 真实浏览器（ego lite，`/studio/v2/` + 临时 HOME）读数：

```json
{
  "hasMcpTitle": false, "hasSourceDb": false, "hasScopeNote": false,
  "hasFileSource": false, "hasGlobalTitle": false,
  "addButtonClass": "btn btn-sm btn-primary", "addButtonText": "添加 MCP",
  "missing": "这个 Agent 的家目录里还没有 mcp.json（/private/tmp/alibot-ui-home/.alibot/agents/mem_35467e3e21fd/mcp.json）：保存一次会按你写的内容新建它。",
  "fileTitle": "家目录 mcp.json", "globalSection": true, "fileTextarea": true
}
```

- 红证据（基线 `e68dcee`，只换测试文件）：前端 `4 failed | 44 passed`（正是新增/改写的 4 条 mcp 用例）；后端 `6 failed, 8 passed`（全部是新增的 `path` 断言）。
- 绿读数：`app/frontend/tests/agent-detail.test.js` 48 passed；`app/tests/test_global_mcp_file.py` 14 passed。
- 门禁：`scripts/check.sh --fast` 与 `scripts/check.sh`（全量）均到末尾「门禁全绿」；ruff / format-check / mypy（136 files）/ pyright（0 errors）/ 全量 pytest / Vitest 462 passed。
- 验收记录：`app/specs/mcp-tab-feedback-acceptance.md`。

## Merge Danger

**Door:** two-way — 界面文案与节点撤除 + 一个**新增**响应字段，无 schema / 迁移 / 装配变化；回滚只需 revert。

**Blast Radius:** low — 只落在 Agent 详情 mcp 页签与 `global-mcp-file` 两个端点的响应形状（`path` 为增量字段，旧消费方忽略即可）。
