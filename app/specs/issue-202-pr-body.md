## Summary

私聊（DM）没有频道，联网档因此回退到 **Agent 级配置 + 默认档**；`fetch` 在 DM 永不装配。
第二轮（2026-10-21 人定）把 Agent 侧显式 `null` 从「一律 400」改成「**清除覆盖、回到跟随频道**」，
于是编辑面的「跟随频道（默认）」是一个**可逆**档位。频道侧语义不变。

```text
web_settings_for_target(target_id, member_id)
  kind = 'dm'       → agent_config.web_access or 'search'      mode = 'read-only'
                      backend = 'local' / docker_egress = False / egress_hosts = []
  kind = 'channel'  → agent_config.web_access or channel.web_access or 'search'   （逐字未改）
  kind = 'task'     → 同上（经 task.channel_id）                                  （逐字未改）
  未知 target       → None
```

- `PATCH /api/agents/{id}` 的 `web_access` 是**三态**（ADR-0026 §十五，推翻 #184 的「一律 400」）：

```text
{"web_access": "off"|"search"|"search+fetch"} → 写这一档
{"web_access": null}                         → 写库 NULL（清除 Agent 级覆盖 = 跟随频道）
{…不含 web_access…}                          → 保持原值（部分更新）
{"role": null} 等其它字段显式 null            → 400，整体不写（放宽面只有 web_access）
```

- 实现真正区分「缺省」与「显式 null」：端点读 `model_fields_set`，库层用 `_UNSET` 哨兵 +
  `web_access = CASE WHEN ? THEN ? ELSE web_access END`（第一条参数是「提到了吗」）。
- Studio：Agent 编辑弹窗新增「联网」四档（**跟随频道（默认）** / `off` / `search` /
  `search+fetch`）；选中「跟随频道」提交 `web_access: null`，读回 `null` 也选中它。Agent 详情
  「运行时」补一行读数（无覆盖显示「跟随频道」，有覆盖显示档位）。

```text
openAgentEditModal ─→ renderAgentEditWebAccess(agent.web_access)   # 四档选中态（null → follow）
submitAgentEdit     ─→ web_access: 'off'|'search'|'search+fetch'|null（总是提交）
agentRuntimeNodes   ─→ + 一行「联网档」（无覆盖 = 跟随频道）
```

无 schema / 迁移改动；`fetch` 闸门、频道联网区块的写路径、`studio/app.css` 与
`alibot-design-system/**` 未动。

Closes #202

## Evidence

- **红→绿（第二轮，2026-10-21 人定）**：
  - `cd app && … pytest tests/test_web_access_config.py --no-header` → 实现前 `2 failed, 13 passed`
    （显式 null 两条用例卡在 `400`：`{"code":"validation_failed","fields":{"body":"该字段不能为 null…"}}`），
    实现后 `15 passed`；
  - `cd app && … pytest tests/test_web_assembly.py -k dm --no-header` → `1 failed, 4 passed` →
    `5 passed`；
  - `node --test app/tests/agent_detail_ui.test.cjs` → 基线 Studio 生产文件 + 新用例
    `tests 22 / pass 17 / fail 5` → 本分支 `pass 22 / fail 0`。
- **库内读数**（手工取证，脚本见验收文档 §2.5）：`PATCH {"web_access":"off"}` → 库内 `('off',)`；
  再 `PATCH {"web_access":null}` → `(None, 'null')`（SQLite `typeof` 证明是 `NULL` 而不是空串），
  `GET` 读回 `null`，`web_settings_for_target(dm, agent)["web_access"] == "search"`、`mode=read-only`；
  装配层挂回 `web_search` 且仍不挂 `fetch`。
- **红→绿（第一轮，DM 读路径 + 三档入口）**：同一组命令 `3 failed, 1 passed` → `4 passed`；
  `4 failed, 8 passed` → `12 passed`；`pass 18 / fail 3` → `pass 21 / fail 0`。
- `scripts/check.sh`（唯一门禁入口）全绿：pytest `1004 passed, 10 skipped`；ruff check
  `All checks passed`；ruff format `252 files already formatted`；mypy `no issues in 104 source files`；
  pyright `0 errors`；`node --test` `212 pass / 0 fail`（exit code `0`）。
- 判据 → 落点 → 读数：`app/specs/issue-202-acceptance.md`。
- 本轮 commits（同一分支、同一 PR，未新开分支 / PR）：`a3d70df`（实现：API 三态 + Studio 四档 +
  用例 + 文档）→ `66f9c34`（验收读数与双轴处置）→ `1841ef7` / `456f589` / `f999d5f`（PR 正文、
  提交清单、SHA 回填）；完整列表见本 PR 的 Commits。

## Merge Danger

**Door:** two-way（读路径 + Studio 入口 + Agent PATCH 的一个字段放宽；无 schema / 迁移，
回滚 = revert 本 PR）。

**Blast Radius:** low—moderate：DM 从「没有联网工具」变成默认挂只读 `web_search`；频道 / 任务分支
读数逐字不变；`PATCH /api/agents/{id}` 只放宽 `web_access` 一个字段的显式 `null`（其它字段仍整体
400，且不部分写入）。已知边界：DM 的系统提示仍沿用「挂了联网工具」的既有边界句（提到 `fetch`），
但工具清单里没有 `fetch`（`agent_seam.py` 不在本票改动面）；界面 token `follow` 不是后端档位，
发给 API 会得到 400（见验收文档 §7.2 / §7.4）。
