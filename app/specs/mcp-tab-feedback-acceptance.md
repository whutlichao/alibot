# 验收记录：Agent 详情 mcp 页签按用户反馈精简 + 缺失路径补全

**票型**：实施票｜**门禁**：G-impl｜**测试档**：窄（相关前端用例 + 构建 + `scripts/check.sh`）
**Ticket**：本票**没有** GitHub issue（用户反馈直派），交付件用描述性命名。
**base**：`e68dcee`（= 派活时 `main`）｜**branch**：`mcp-feedback`
**PR**：[#335](https://github.com/whutlichao/alibot/pull/335)｜**PR body**：`app/specs/mcp-tab-feedback-pr-body.md`
**CI**：[run 40656503088](https://github.com/whutlichao/alibot/actions/runs/40656503088) success（`d99a5f9`，含代码提交 `a344286`）｜
[run 40657858041](https://github.com/whutlichao/alibot/actions/runs/40657858041) success（`a112caf`）；文档-only 提交不逐条登记 run
**采集时间**：2026-10-28｜**环境**：macOS（aarch64）、Python 3.11.13（仓库根 `.venv`）、ego-browser 0.6.0.20

---

## Status

| 判据（票面 Observable acceptance） | 落点 | 读数 | 结论 |
| --- | --- | --- | --- |
| ① 删除「MCP 声明」标题 | `AgentDetailView.vue` mcp 面板 | 浏览器探针 `[data-od-id="agent-capabilities-mcp"]` = `null`；vitest 同断言 | ✅ |
| ② 删除数据库来源说明 | 同上 | `[data-od-id="mcp-source-db"]` = `null`；词条 `mcp.sourceDb` 两份语言表一并删除 | ✅ |
| ③ 删除环境变量说明 | 同上 | `[data-od-id="mcp-scope-note"]` = `null`；词条 `mcp.scopeNote` 一并删除 | ✅ |
| ④ 删除家目录文件来源说明 | 同上 | `[data-od-id="agent-detail-mcp-file-source"]` = `null`；词条 `agentDetail.mcpFileSource` 一并删除 | ✅ |
| ⑤ 删除「全局 MCP（只读）」标题 | 同上 | `[data-od-id="agent-detail-section-global-mcp-title"]` = `null`；条目视图容器与确认动作仍在 | ✅ |
| ⑥ 高亮「添加 MCP」按钮 | `mcp-save` 按钮 | 浏览器：`className = "btn btn-sm btn-primary"`、`textContent = "添加 MCP"`；vitest `classes()` 含 `btn-primary` | ✅ |
| ⑦ 缺失文件提示写真实 mcp.json 路径 | 后端 `GET/PUT …/global-mcp-file` 新增 `path`；前端 `mcpFilePath` → 提示词条 `{path}` | 浏览器读到：`这个 Agent 的家目录里还没有 mcp.json（/private/tmp/alibot-ui-home/.alibot/agents/mem_35467e3e21fd/mcp.json）：保存一次会按你写的内容新建它。` | ✅ |
| 保留功能与其他内容 | 库行列表 / 表单 / 启停 / 确认、原文文本域 / 条目数 / 保存、条目视图 + 远端确认 | 浏览器：`fileTitle=家目录 mcp.json`、`globalSection=true`、`fileTextarea=true`；vitest 功能面断言全绿 | ✅ |
| 路径必须来自真实后端数据 | `resolve_global_write_path`（与只读守卫共用 realpath 内核，不判存在性） | 后端用例：存在 ⇒ 文件 realpath；缺席 ⇒ 目标 realpath；根外 symlink ⇒ `null`（前端退到无路径句） | ✅ |
| 构建产物 | `app/frontend` → `app/alibot/studio/v2/` | `scripts/check.sh` 头部 `npm run build` 通过；产物**不入库**（ADR-0039），无产物提交 | ✅ |
| 门禁 | `scripts/check.sh`（全量） | 末尾 `门禁全绿`：ruff / ruff format --check / mypy（136 files）/ pyright（0 errors）/ 全量 pytest / Vitest 462 passed | ✅ |

### 红证据（隔离基线 `e68dcee`）

基线 worktree `/tmp/mcp-feedback-baseline`（`git worktree add --detach … e68dcee`），只把**当前测试文件**覆盖进去，
产品代码保持基线字节（`AgentDetailView.vue` md5 `722cce138897f11e081e521adbdcaccc`，当前为 `1b9bbce57b81859a80045e42517e7757`）：

```text
# 前端（app/frontend/tests/agent-detail.test.js）
 FAIL  mcp 页签 > 两块面都在：上块库行（装配正本）+ 下块家目录 mcp.json；说明性标注已撤
 FAIL  mcp 页签 > 「添加 MCP」是这块的主按钮（btn-primary）
 FAIL  mcp 页签 > mcp.json 缺失 ⇒ 提示写后端给的真实路径
 FAIL  mcp 页签 > 路径被全局层守卫拒（后端 path=null）⇒ 退到不带路径的缺失提示
      Tests  4 failed | 44 passed (48)

# 后端（app/tests/test_global_mcp_file.py）
6 failed, 8 passed  # 失败全部是新增的 path 断言（KeyError: 'path' / 精确字典差一个键）
```

当前分支同一批用例：前端 48 passed；后端 14 passed。

## 改动落点

| 文件 | 改动 |
| --- | --- |
| `app/alibot/service.py` | `global_mcp_document()` / `save_global_mcp_document()` 响应增加 `path`（读守卫取原文，写守卫取同一目标路径） |
| `app/alibot/api.py` | 两条 `global-mcp-file` 端点 docstring 补 `path` 契约 |
| `app/README.md` | 端点表补 `path` 字段与「缺席也给路径 / 越界为 null」口径 |
| `app/alibot/studio/i18n.js` | 删 5 个无消费点词条；`mcpFileMissing` 加 `{path}`，新增 `mcpFileMissingNoPath`（zh/en 同键） |
| `app/frontend/src/stores/agent-detail.js` | 新增 `mcpFilePath` 读数（读 / 写两条路径都落） |
| `app/frontend/src/components/AgentDetailView.vue` | 撤 5 处说明性节点；`mcp-save` 加 `btn-primary`；缺失提示走 `mcpFileMissingText` |
| `app/frontend/tests/agent-detail.test.js` | 改写「两块面都在」用例（撤除项 + 保留项），新增主按钮 / 真实路径 / 无路径回退 3 条 |
| `app/tests/test_global_mcp_file.py` | 读 / 写 / 缺席 / 根外 symlink 四种 `path` 读数 |

## 验证命令与读数

```sh
scripts/check.sh --fast   # 开发循环：build + ruff + format-check + mypy + pyright + vitest(462) 全绿
scripts/check.sh          # 全量：以上 + 全量 pytest，末尾「门禁全绿」（124.50s）
```

浏览器探针（ego lite，临时 HOME `/tmp/alibot-ui-home` + 临时数据目录 `/tmp/alibot-ui-data`，端口 8799）：

```json
{
  "missing": "这个 Agent 的家目录里还没有 mcp.json（/private/tmp/alibot-ui-home/.alibot/agents/mem_35467e3e21fd/mcp.json）：保存一次会按你写的内容新建它。",
  "hasMcpTitle": false, "hasSourceDb": false, "hasScopeNote": false,
  "hasFileSource": false, "hasGlobalTitle": false,
  "addButtonClass": "btn btn-sm btn-primary", "addButtonText": "添加 MCP",
  "fileTitle": "家目录 mcp.json", "globalSection": true, "fileTextarea": true
}
```

截图：`.scratch/mcp-feedback/mcp-tab.png`（本地证据，`/.scratch/` 不入库）。

## 说明

- 构建产物按 ADR-0039 **不入库**（`.gitignore` 已忽略 `app/alibot/studio/v2/`）：门禁头部与 hatch hook 各自构建，
  因此本票没有产物提交；「重新构建」由 `scripts/check.sh` 的构建步取证。
- 未跑双轴 code-review：改动面可圈定（一个页签的说明性节点 + 一个增量响应字段），票面已同意省略。
- 未跑无关模块专项测试（票面「本票不跑」）。
