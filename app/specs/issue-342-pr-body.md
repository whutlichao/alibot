## Summary

给频道加上**两个独立的软删除档位**（ADR-0040），`channel` 表因此多两列可空时间戳，schema `25 → 26`：

```text
channel.archived_at  ──→ 移出左栏活跃段 + 频道面只读（可恢复，历史仍可读、仍可被搜）
channel.deleted_at   ──→ 终态（不可恢复）：读路径一律当作「频道不存在」，一行数据都不删
```

读路径的四个落点（一处判定，没有第二份形状）：

```diff
 CHANNEL_SELECT_SQL      WHERE ... AND c.deleted_at IS NULL            # 列表过滤 + get_channel 自然 404
 UNREAD_TOTAL_SQL        LEFT JOIN channel c ON c.id = t.id            # 归档/删除的频道不计总未读
                         AND (t.kind <> 'channel' OR (c.archived_at IS NULL AND c.deleted_at IS NULL))
 SEARCH_ALL_HITS_SQL     AND (t.kind <> 'channel' OR c.deleted_at IS NULL)   # 删除的消息不可搜，归档的仍可搜
 _channel_payload        + "archived_at" / "deleted_at"                # 前端只有一套形状判断
```

`UNREAD_WHERE_SQL`（未读的唯一 SQL 形状，ADR-0004 决策 7）**一个字都没改**——只加了外层过滤。

写路径：三个新端点都是配置类写路径（不落消息、不唤醒、不消耗预算、不开世代）。

| 端点 | 语义 |
| --- | --- |
| `POST /api/channels/{id}/archive` | 幂等；`archived_at` 只记第一次那一刻 |
| `POST /api/channels/{id}/unarchive` | 幂等；清 `archived_at` |
| `DELETE /api/channels/{id}` | **不幂等**（二次 ⇒ 404，与 `DELETE /api/agents/{id}` 同口径）；写 `deleted_at` 并清 `archived_at` |

「已归档 ⇒ 频道面只读」的**单一判定落点**是 `service._require_writable_channel()`，五个既有写入口改调它：

```text
send_message / update_channel_config / set_channel_budget_limit / add_channel_member / create_task
  └─ _require_writable_channel()   # get_channel（继承删除态 404）+ 归档态 400

刻意不调它（写进 ADR §二）：
  send_task_message / update_task* / 任务线程消息   # 任务的生命周期由任务自己的五态表达
  mark_read / GET 系列 / SSE                        # 只读
  reminder_delivery                                  # 后端系统事实，不是人类面写入
```

已删除的频道连它的**任务入口**一起收走（ADR-0040 §一 第 5 条）：`GET /api/tasks` 与两类搜索的
任务命中都按 `channel.deleted_at` 过滤（那一行带着「#频道名」，而频道已经 404）；按 id 直接读
一张任务与它的线程仍然可用——**行不删、只是频道层面的入口不在了**，与消息同一条口径。

前端：

```diff
 <RailSidebar>         频道段 = 活跃段 + 可折叠「已归档频道」段（channel-archive-section）
 <ChannelHeader>       + btn-channel-archive  |  归档态只留 btn-channel-unarchive（其余整组不渲染）
 <ChannelConfigModal>  + 危险区（归档说明 + 删除）与行内确认条（.danger-confirm-text / .btn-danger）
 <ComposerBox>         复用既有 archived prop（禁用 + 「只读归档」占位），不新增 prop
 <App>                 archive/unarchive 接线；删除当前频道后落到下一个可用频道或空态
```

## Evidence

**Before**（基线 `6f02a51` 的隔离副本 + 本票新增用例）：

```text
# 后端 app/tests/test_channel_archive.py
KeyError: 'archived_at'      … 11 failed, 2 passed in 2.52s
# 且 /api/channels/{id}/archive 在基线上是 404（端点不存在）

# 前端 app/frontend/tests/channel-archive.test.js
× 归档频道进折叠段、活跃段不含它
× 归档态下频道工具行只剩「取消归档」…
× 点归档 ⇒ POST .../archive，并用响应覆盖左栏…
× 点「确认删除」才发 DELETE；请求在飞时两枚按钮都禁用
… Test Files 1 failed (1) / Tests 14 failed | 1 passed (15)
```

**After**：

```text
cd app && ../.venv/bin/python -m pytest tests/test_channel_archive.py      # 15 passed
cd app/frontend && env -u NODE_ENV npx vitest run tests/channel-archive.test.js   # 17 passed (17)

scripts/check.sh（全量）
  1310 passed, 11 skipped in 77.99s        # pytest（含 range(3, SCHEMA_VERSION) 逐版迁移）
  All checks passed!                        # ruff check
  388 files already formatted               # ruff format --check（未跑 ruff format）
  Success: no issues found in 138 source files   # mypy
  0 errors, 0 warnings, 0 informations      # pyright
  Test Files 25 passed (25) / Tests 495 passed (495)   # Vitest
  门禁全绿
```

**真实浏览器走查**（ego-browser，隔离数据目录 `/tmp/alibot-archive-ui`，`--port 8799`），其中一次是
**真实的 25 → 26 迁移现场**（基线代码建库 → 本代码 `--migrate`）：

```text
源版本：25 → 目标版本：26
  25 → 26  channel archive and delete tiers (channel.archived_at / channel.deleted_at)  改动 0 行
迁移完成：schema_version = 26；本次共改动 0 行。
```

| 动作 | 读数 |
| --- | --- |
| 打开工作台 | 活跃段 `['#验收频道', '#第二个频道']`，没有归档段 |
| 点归档 | 活跃段只剩第二个频道；`channel-archive-section` 出现（`aria-expanded="false"`）；工具行只剩 `['btn-channel-unarchive']`；描述 `该频道已归档，保留为只读归档。`；composer 占位 `该频道已归档，只读归档`、`disabled=true` |
| 点取消归档 | 活跃段恢复两个、归档段消失、工具行恢复五枚、composer 恢复可写 |
| 点「删除本频道」 | 只出现确认条 `确认删除 #验收频道？`，左栏未变（**零请求**） |
| 点「确认删除」 | 标题变 `第二个频道`；服务端只剩一个频道；`GET /api/channels/ch_d1603c195e41/messages` ⇒ **404** |

截图：`/tmp/alibot-archive-ui/shots/01-live.png` … `07-deleted.png`。
验收记录（判据 → 落点 → 读数全文）：`app/specs/issue-342-acceptance.md`。

## Merge Danger

**Door:** one-way

本 PR 把 `SCHEMA_VERSION` 从 25 推到 26。合并之后，任何被新代码打开过的数据目录都会被闸门判为 v26；
**旧代码（v25）打不开它**。回滚只能靠「合并前备份整个数据目录」——启动路径与 `--migrate` 都会先备份，
但回滚本身是人工动作，不是 revert 一次提交就能完成的事。迁移本身是纯增量（`ADD COLUMN` 两列可空时间戳、
不重建表、不回填、`改动 0 行`），所以**数据**没有风险；风险在版本号这一侧。

**Blast Radius:** medium

- 读路径：频道列表 / `get_channel`（进而消息、配置、预算、事件流的所有 404 早退）/ 总未读 / 全局搜索
  四处都多了过滤。已删除的频道对它们「不存在」。
- 写路径：五个既有入口换了一次前置读（多一个归档态 400）。**任务面、标读与只读面刻意不受影响**（ADR-0040 §二）。
- 前端：左栏频道段拆成两段、频道头工具行多一枚按钮并在归档态收敛到一枚、频道设置弹窗底部多一个危险区。
  不影响私信、（消息流 / 看板 / 搜索 / Agent 详情之外）其余主线视图。
- 数据库：两列可空、无默认值、无 CHECK；旧数据逐字不变，历史消息 / 任务 / 成员关系一行不删
  （消息 append-only，ADR-0001 不变）。

Closes #342
