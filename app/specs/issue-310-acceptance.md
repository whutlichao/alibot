# 票 #310 验收：Vue Agent 详情三分页签

本票迁移 `/studio/v2/` 的 Agent 详情。完整判据、实现落点、红证据和浏览器读数见
[Vue 阶段一验收报告 §十](vue-refactor-phase1-acceptance.md#十阶段-c--票-310agent-详情三分页签vue-重构的最后一块)。

| 判据 | 验证 |
| --- | --- |
| rail 进入详情；base / skills / mcp 顺序固定，切页签保留草稿 | `app/frontend/tests/agent-detail.test.js` 从 `App.vue` 进入；ego-browser 截图 `issue-310-final-base.png`、`issue-310-final-skills.png` |
| 配置保存保留上下文三项；远端数据流告知可确认 | 同一测试文件断言 7 字段 PATCH、确认请求、数据面未读取时禁用与重复点击 |
| skills 选中项正文、导入与行内启停；mcp 库行和文件原文 | 同一测试文件断言父组件接线、请求及文件写入失败；截图 `issue-310-final-mcp.png` |
| 删除确认、失败原因、成功后返回主线与私信归档 | 同一测试文件断言 DELETE 前零请求、失败可重试和归档只读；截图 `issue-310-final-delete-confirm.png` |
| 关闭详情恢复进入前的 dock 形态与开合 | 同一测试文件：进入前开 run 面板 ⇒ 进详情让位 ⇒ `closeAgentDetail()` 后连形态一起开回来 |
| 「发送消息」在途闸门 | 同一测试文件：连点两次只发一条 `POST /api/dms`，按钮同时禁用 |

## 可复跑命令与读数（均属最终提交 `bd853a0`）

```sh
cd app/frontend && env -u NODE_ENV npm run build      # ✓ 64 modules transformed；产物 index-DtB21X7m.js
./scripts/check.sh --fast                            # exit 0
./scripts/check.sh                                   # exit 0（全量，含 pytest）
```

| 门禁 | 读数 |
| --- | --- |
| pytest（全量） | `1264 passed, 11 skipped in 753.25s (0:12:33)` |
| ruff check / ruff format --check | `All checks passed!` / `363 files already formatted` |
| mypy | `Success: no issues found in 133 source files` |
| pyright | `0 errors, 0 warnings, 0 informations`（逐字引用工具输出，故保留其 `informations` 拼法） |
| node --test（旧页面族） | `tests 389 / pass 389 / fail 0` |
| Vitest（v2 工作台） | `Test Files 21 passed (21)` / `Tests 416 passed (416)` |

全量门禁日志：`.scratch/v2-p2c/issue-310-full-final2.log`（已 gitignore，按上面三条命令可复跑）。

双轴评审的回归用例均在 `tests/agent-detail.test.js` 内（共 37 条）：dock 形态与开合恢复、
发送消息连点只发一条、删除技能 toast 取词条、启停失败按启停归因、主读数失败持久错误行且写动作禁用、
主读数在途切走不误报、打字时后台刷新不丢输入与焦点。

浏览器复验使用 `http://127.0.0.1:8814/studio/v2/` 与独立数据目录 `/tmp/alibot-p2c-310`；
截图位于已忽略的 `.scratch/v2-p2c/`，脚本在 `/Users/apple/ego/t10-agent-detail-20261027/round-07.mjs`，
结束时显式调用 `page.close()` 和 `task.finish()`。

#310 原票面关于 `mcp.json` 同名优先级的描述已被 #275 / PR #294 和 ADR-0031 修订取代：
文件是导入源并由 Studio 提供写面，运行期仅装配 Agent 级库行。本票遵循现行事实，未改后端装配或 schema。
