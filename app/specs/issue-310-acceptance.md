# 票 #310 验收：Vue Agent 详情三分页签

本票迁移 `/studio/v2/` 的 Agent 详情。完整判据、实现落点、红证据和浏览器读数见
[Vue 阶段一验收报告 §十](vue-refactor-phase1-acceptance.md#十阶段-c--票-310agent-详情三分页签vue-重构的最后一块)。

| 判据 | 验证 |
| --- | --- |
| rail 进入详情；base / skills / mcp 顺序固定，切页签保留草稿 | `app/frontend/tests/agent-detail.test.js` 从 `App.vue` 进入；ego-browser 截图 `issue-310-final-base.png`、`issue-310-final-skills.png` |
| 配置保存保留上下文三项；远端数据流告知可确认 | 同一测试文件断言 7 字段 PATCH、确认请求、数据面未读取时禁用与重复点击 |
| skills 选中项正文、导入与行内启停；mcp 库行和文件原文 | 同一测试文件断言父组件接线、请求及文件写入失败；截图 `issue-310-final-mcp.png` |
| 删除确认、失败原因、成功后返回主线与私信归档 | 同一测试文件断言 DELETE 前零请求、失败可重试和归档只读；截图 `issue-310-final-delete-confirm.png` |

最终工作区执行 `env -u NODE_ENV npm run build` 成功，`./scripts/check.sh --fast` 成功：
Node 389 passed、Vitest 21 files / 408 passed、mypy 133 files 无问题、Pyright 0 errors。
完整门禁在随后修复上述回归之前通过：pytest 1264 passed / 11 skipped；最终提交的完整门禁以 CI 为准。

浏览器复验使用 `http://127.0.0.1:8814/studio/v2/` 与独立数据目录 `/tmp/alibot-p2c-310`；
截图位于已忽略的 `.scratch/v2-p2c/`，脚本在 `/Users/apple/ego/t10-agent-detail-20261027/round-07.mjs`，
结束时显式调用 `page.close()` 和 `task.finish()`。

#310 原票面关于 `mcp.json` 同名优先级的描述已被 #275 / PR #294 和 ADR-0031 修订取代：
文件是导入源并由 Studio 提供写面，运行期仅装配 Agent 级库行。本票遵循现行事实，未改后端装配或 schema。
