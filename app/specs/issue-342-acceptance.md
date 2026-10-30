# 验收记录：#342 频道新增归档与删除（软删除两档，schema 25 → 26）

**票型**：实施票｜**门禁**：G-impl｜**测试档**：宽（全量：本票跨 schema 迁移、db 读路径、五个既有写入口、
三个新端点、前端 store 与四个组件、两份词条表）
**Ticket**：[GitHub issue #342](https://github.com/whutlichao/alibot/issues/342)（正本；`Closes #342` 写在 PR 正文里）
**base**：`6f02a51`｜**branch**：`feat-channel-archive-delete`
**worktree**：`/Users/apple/orca/workspaces/alibot/channel-archive-delete`
**PR body**：`app/specs/issue-342-pr-body.md`
**采集时间**：2026-10-30｜**环境**：macOS（Darwin 27.0.0，aarch64）、Python 3.11（仓库根 `.venv` 符号链接接上）、
Node v25.0.0、Vitest 4.1.12、ego-browser 0.6.0.20（Chromium 154）

> 本文件在**创建 PR 之前**提交，只记当时已得的读数。PR 号与 CI 状态以 GitHub 为准，
> 不写进本文件、也不为回填它们追加提交（判据见 `docs/agents/implementation.md` §交付）。

---

## Status

| # | 判据（票面 Observable acceptance） | 落点 | 读数 | 结论 |
| --- | --- | --- | --- | --- |
| 1 | 红证据 → 绿：新用例在 base `6f02a51` 的隔离副本上红、新代码上绿 | `app/tests/test_channel_archive.py` / `app/frontend/tests/channel-archive.test.js` | 后端 base `11 failed, 2 passed` → 新代码 `13 passed`；前端 base `14 failed \| 1 passed (15)` → 新代码 `15 passed (15)`（§1） | ✅ |
| 2 | 后端行为：归档读数 / 幂等 / 五写入口 400 且零写入 / 任务面不受影响 / 取消归档 / 删除与二次 404 / 总未读 / 搜索 / 迁移 25→26 | `app/tests/test_channel_archive.py`（HTTP 黑盒，不读库表） | `13 passed in 2.42s`（§2） | ✅ |
| 3 | 前端行为：折叠段与 `aria-expanded` / composer 禁用与占位 / 工具行只剩取消归档 / 删除需确认 / 删除当前频道后主线落点 / store 用响应覆盖 | `app/frontend/tests/channel-archive.test.js`（从 `App.vue` 进） | `15 passed (15)`（§3） | ✅ |
| 4 | AI/UI 证据：隔离数据目录上真实浏览器走查（新建 → 归档 → 观察 → 取消归档 → 删除） | `http://127.0.0.1:8799/studio/v2/`，数据目录 `/tmp/alibot-archive-ui` | 7 张截图 + 逐动作 DOM 读数（§4） | ✅ |
| 5 | 静态检查：`scripts/check.sh` 全量 EXIT 0，六项读数 | — | pytest `1310 passed, 11 skipped`；ruff `All checks passed!`；`ruff format --check` `388 files already formatted`；mypy `Success: no issues found in 138 source files`；pyright `0 errors, 0 warnings, 0 informations`；Vitest `25 files / 495 tests passed`；`门禁全绿`（§5） | ✅ |
| 6 | 双轴 code-review（Standards + Spec）findings 与逐条处置 | `## Review` | 两轴报告与逐条处置见 §6 | ✅ |
| 7 | 交付物：worktree 干净、提交存在、分支已推送、OPEN PR 的 head == 最终提交、正文含 Summary / Evidence / `Door` / `Blast Radius` / `Closes #342` | `app/specs/issue-342-pr-body.md` | 见 §7；**`Door: one-way`、`Blast Radius: medium`**（触及 schema 迁移） | ✅ |

---

## 1. 红证据（隔离基线 `6f02a51`；后续新增的两条任务面用例只在新代码上有读数——
它们在基线上同样会红，因为 `archived_at` / `deleted_at` 两列与三个端点在基线上都不存在）

隔离副本用 `git archive 6f02a51` 导出到 `/tmp/alibot-red-base`，只覆盖**新增的两个用例文件**（产品代码保持基线字节）。

### 1.1 后端

```sh
cd /tmp/alibot-red-base/app
PYTHONPATH=/tmp/alibot-red-base/app \
  /Users/apple/orca/workspaces/alibot/channel-archive-delete/.venv/bin/python \
  -m pytest tests/test_channel_archive.py
```

```text
FAILED tests/test_channel_archive.py::test_archive_moves_the_channel_out_of_the_live_list_but_keeps_it_listed
FAILED tests/test_channel_archive.py::test_archiving_twice_is_idempotent_and_keeps_the_first_timestamp
FAILED tests/test_channel_archive.py::test_every_channel_face_write_is_rejected_while_archived_and_writes_nothing
FAILED tests/test_channel_archive.py::test_unarchive_restores_the_writable_face_and_is_idempotent
FAILED tests/test_channel_archive.py::test_delete_removes_the_channel_from_every_read_face_and_is_not_idempotent
FAILED tests/test_channel_archive.py::test_delete_after_archive_clears_the_archive_tier
FAILED tests/test_channel_archive.py::test_archived_channel_unread_stops_counting_towards_the_sidebar_total
FAILED tests/test_channel_archive.py::test_deleted_channel_messages_leave_search_while_archived_ones_stay
FAILED tests/test_channel_archive.py::test_migrating_a_v25_library_adds_the_two_null_columns_and_keeps_every_row
FAILED tests/test_channel_archive.py::test_migrated_library_serves_the_new_endpoints
FAILED tests/test_channel_archive.py::test_create_channel_still_reports_the_two_new_fields
11 failed, 2 passed in 2.52s
```

失败原因原文（抽样）：

```text
tests/test_channel_archive.py:85: in test_archive_moves_the_channel_out_of_the_live_list_but_keeps_it_listed
    assert listed(client)[channel["id"]]["archived_at"] is None
E   KeyError: 'archived_at'
tests/test_channel_archive.py:385: in test_create_channel_still_reports_the_two_new_fields
    assert created["archived_at"] is None and created["deleted_at"] is None
E   KeyError: 'archived_at'
```

基线上**通过的两条**是刻意写成**正面断言**的（`test_task_plane_of_an_archived_channel_still_works`、
`test_archive_and_delete_do_not_touch_the_message_stream_or_the_budget`）：它们证明的是「归档**不影响**
任务面 / 消息流 / 预算」，在没有归档功能时当然也成立——它们守的是将来别把「只读」做过头，不是本票的新行为。
其余 11 条全部覆盖新行为，在基线上全红。

### 1.2 前端

```sh
cd /tmp/alibot-red-base/app/frontend && env -u NODE_ENV npx vitest run tests/channel-archive.test.js
```

```text
× 归档频道进折叠段、活跃段不含它
× 折叠按钮的 `aria-expanded` 默认 false，点一下翻成 true 且列表可见
× 打开归档频道 ⇒ composer 禁用、占位是「只读归档」那句
× 归档态下频道工具行只剩「取消归档」：名册 / 配置 / 预算 / 运行面板整组不渲染
× 活跃频道下是归档按钮，不是取消归档
× 点归档 ⇒ POST .../archive，并用响应覆盖左栏（频道挪进折叠段）
× 点取消归档 ⇒ POST .../unarchive，频道回到活跃段
× 危险区有归档说明与删除按钮；「删除」默认不发请求
× 点「取消」收起确认条，仍然没有发请求
× 点「确认删除」才发 DELETE；请求在飞时两枚按钮都禁用
× 删除当前打开的频道 ⇒ 主线落到下一个可用频道，不残留上一个频道的消息
× unarchiveChannel 用响应里的 channels 整份覆盖（不本地拼一条「看起来成功了」的行）
× archiveChannel / deleteChannel 同样以响应覆盖；派生读数跟着变
× 归档频道的占位与描述走归档那一句，私信仍走它自己那一句
 Test Files  1 failed (1)
      Tests  14 failed | 1 passed (15)
```

（基线通过的那一条是「没有归档频道时整段不渲染」——它在旧代码上恰好成立，因为整段本来就不存在。）

### 1.3 新代码上同一批用例

```sh
cd app && ../.venv/bin/python -m pytest tests/test_channel_archive.py   # 15 passed
cd app/frontend && env -u NODE_ENV npx vitest run tests/channel-archive.test.js   # Tests 15 passed (15)
```

---

## 2. 后端判据 → 落点 → 读数

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 归档 ⇒ 200，列表里**仍有**该频道且 `archived_at` 非空、`deleted_at` 为 `null`；响应自带整份 `channels` | `db.archive_channel` / `api.archive_channel` | `test_archive_moves_the_channel_out_of_the_live_list_but_keeps_it_listed` ✅ |
| 归档幂等：二次 ⇒ 200 且 `archived_at` 逐字不变 | `db.archive_channel` 的 `WHERE archived_at IS NULL` | `test_archiving_twice_is_idempotent_and_keeps_the_first_timestamp` ✅ |
| 五个写入口（消息 / 配置 / 预算 / 加成员 / 新建任务）各自 400 `validation_failed`，且 `_read_channel_facts` 逐字不变（零写入） | `service._require_writable_channel` + 五个调用点 | `test_every_channel_face_write_is_rejected_while_archived_and_writes_nothing` ✅ |
| 任务面不受影响：既有任务仍能改状态、仍能在任务线程发言 | `send_task_message` / `update_task_status` **不**调判定 | `test_task_plane_of_an_archived_channel_still_works` ✅ |
| 取消归档 ⇒ `archived_at` 为 `null`、写入口恢复；再调幂等 | `db.unarchive_channel` | `test_unarchive_restores_the_writable_face_and_is_idempotent` ✅ |
| 删除 ⇒ 列表不再出现、`GET .../messages` 404、二次 `DELETE` 404、`archive` / `unarchive` 也 404 | `db.delete_channel` + `CHANNEL_SELECT_SQL` 的 `deleted_at IS NULL` | `test_delete_removes_the_channel_from_every_read_face_and_is_not_idempotent` ✅ |
| 先归档后删除：终态清掉 `archived_at`，读面全 404 | 同上 | `test_delete_after_archive_clears_the_archive_tier` ✅ |
| 归档频道未读**不计入** `unread_total`；取消归档后**重新计入**；频道自己的 `unread_count` 不变 | `UNREAD_TOTAL_SQL` 的外层 `LEFT JOIN` + `WHERE` | `test_archived_channel_unread_stops_counting_towards_the_sidebar_total` ✅（构造前提 `unread_count >= 1`） |
| 已删除频道的消息不再出现在 `GET /api/search`；归档频道的**仍然出现**；**定向搜索**（带 `target_id`）同一条判定 | `SEARCH_ALL_HITS_SQL` / `SEARCH_TARGET_HITS_SQL` 的 `(t.kind <> 'channel' OR c.deleted_at IS NULL)` | `test_deleted_channel_messages_leave_search_while_archived_ones_stay` ✅（含 `target_id=已删除频道` ⇒ `[]`、`target_id=归档频道` ⇒ 命中） |
| 已删除频道的**任务**退出看板与任务搜索；按 id 直接读该任务与它的线程仍 200 | `list_tasks` 的 `JOIN channel … c.deleted_at IS NULL` / `SEARCH_TASK_HITS_SQL` | `test_deleted_channel_tasks_leave_the_board_and_the_task_search` ✅ |
| 归档频道的任务**仍在**看板与任务搜索里（归档 ≠ 任务面冻结） | 同上（不过滤 `archived_at`） | `test_archived_channel_tasks_stay_on_the_board` ✅ |
| 迁移 25 → 26：既有列逐字保留、两列全 `NULL`、终态版本 == `SCHEMA_VERSION` | `migrate._step_25_to_26` | `test_migrating_a_v25_library_adds_the_two_null_columns_and_keeps_every_row` ✅ |
| 迁移后的库由本代码打开时三个新端点可用 | 同上 | `test_migrated_library_serves_the_new_endpoints` ✅ |
| 三个动作是配置类写路径：消息 seq、预算读数、轮次结果逐字不变，`drain` 为空 | `db.archive_channel` / `db.delete_channel` 只写单列 | `test_archive_and_delete_do_not_touch_the_message_stream_or_the_budget` ✅ |
| `POST /api/channels` 与 `GET /api/workspace` 的频道载荷同形（两档都是 `null`） | `_channel_payload` | `test_create_channel_still_reports_the_two_new_fields` ✅ |

迁移链的两条机械判据同时跑过：

```sh
cd app && ../.venv/bin/python -m pytest tests/test_schema_migration.py tests/test_shape_conditioned_step.py \
  tests/test_migration_chain_guard.py -q
# 96 passed（含 range(3, SCHEMA_VERSION) 逐版迁移、v26 形态对照、链连续性）
```

`app/tests/_migration_history.py` 按票面 Ownership 只动了两处：新增 `'channel_v26'` 对象与
`VERSION_OBJECTS[26]`（对象集 = v25，仅 channel 表换成 v26 形态）。`SOURCE_COMMITS` **未填 26**
（合并提交尚不存在），与本文件头「机器抽取、勿手改」的约定一致。

---

## 3. 前端判据 → 落点 → 读数

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 归档频道进「已归档」折叠段、活跃段不含它 | `RailSidebar.vue` 的 `liveChannels` / `archivedChannels` | ✅ |
| 折叠按钮 `aria-expanded` 默认 `false`，点一下翻 `true` 且 `#channelArchiveList` 可见 | `data-od-id="btn-channel-archive-toggle"` | ✅ |
| 没有归档频道时整段不渲染 | `v-if="archivedChannels.length"` | ✅ |
| 归档频道的 composer 禁用、占位是「只读归档」，频道头描述是归档那句 | `lib/target-view.js` + `App.vue` 的 `archived` | 占位 `该频道已归档，只读归档`；`disabled` 属性存在 | ✅ |
| 归档态下工具行只剩「取消归档」（名册 / 配置 / 预算 / 运行面板 / 归档整组不存在） | `ChannelHeader.vue` 的 `archivedChannel` 分支 | 5 个 `data-od-id` 全部 `exists() === false` | ✅ |
| 点归档 ⇒ 一次 `POST .../archive`，左栏与频道头按响应刷新 | `App.vue` 的 `archiveChannel()` | `stub.to('/api/channels/ch-1/archive').length === 1` | ✅ |
| 「删除」默认不发请求，点第一次只铺确认条（含 `#design`）；点「取消」收起且仍无请求 | `ChannelConfigModal.vue` 的 `deleteConfirm` | `DELETE` 调用数 `0` | ✅ |
| 点「确认删除」才发 `DELETE`；在飞时确认与取消都 `disabled` | 同上 + `deleting` | 1 次 `DELETE`，两枚按钮 `disabled` 属性存在 | ✅ |
| 删除当前频道 ⇒ 主线落到下一个**可用**（非归档）频道，旧频道消息不残留 | `App.vue` 的 `deleteChannel()` | 标题 `下一个`；文本不含「上一个频道的消息」 | ✅ |
| `unarchiveChannel` 用响应里的 `channels` 整份覆盖镜像 | `stores/workspace.js` | `channels` 顺序与名称来自响应，非本地拼行 | ✅ |
| 优先级：归档频道 > 归档私信 > 私信 > 频道 | `lib/target-view.js` | 四条断言各自命中对应词条 | ✅ |
| 折叠段默认收起（归档行在 DOM 里但不可见）；**把当前频道归档后这一段自动展开**；人手收起后保持收起 | `RailSidebar.vue` 的 `channelArchiveToggle`（三态：`null` 跟随当前行） | 三条用例 | ✅ |

---

## 4. 真实浏览器走查（ego-browser，隔离数据目录）

```sh
env -u http_proxy -u https_proxy -u all_proxy -u no_proxy -u NO_PROXY PYTHONPATH=$PWD/app \
  .venv/bin/python -m alibot --data-dir /tmp/alibot-archive-ui --port 8799
# 打开 http://127.0.0.1:8799/studio/v2/
```

数据目录 `/tmp/alibot-archive-ui` 是一次**真实的 25 → 26 迁移现场**（先用基线代码建库、再用本代码
`--migrate` 推进）：

```text
源版本：25 → 目标版本：26
  25 → 26  channel archive and delete tiers (channel.archived_at / channel.deleted_at)  改动 0 行
迁移完成：schema_version = 26；本次共改动 0 行。
```

| 步骤 | 观察到的 DOM 读数 | 截图 |
| --- | --- | --- |
| 打开工作台（两个频道） | 活跃段 `['#验收频道', '#第二个频道']`；`channel-archive-section` 不存在 | `/tmp/alibot-archive-ui/shots/01-live.png` |
| 选中 #验收频道 | 工具行 `[members, settings, budget, archive, run-panel]`；composer 未禁用 | `02-active.png` |
| 点归档按钮 | 左栏活跃段只剩 `channel-ch_d850877951b7`；`channel-archive-section` 出现，`aria-expanded="false"`，段内 `['channel-ch_d1603c195e41']`；工具行只剩 `['btn-channel-unarchive']`；标题不变、描述 `该频道已归档，保留为只读归档。`；composer 占位 `该频道已归档，只读归档`、`disabled=true`、提示行 `已归档 · 只读，不能发送消息`；toast `已归档频道 #验收频道：它现在只读，历史仍可读。` | `03-archived.png` |
| 点取消归档 | 活跃段恢复两个；`channel-archive-section` 消失；工具行恢复五枚；composer 恢复可写；toast `已取消归档 #验收频道：频道恢复可写。` | `04-unarchived.png` |
| 打开频道设置 | 危险区存在，归档说明与删除说明全文可见，删除按钮 `删除本频道`，确认条**未出现** | `05-danger.png` |
| 点「删除本频道」 | 确认条出现：`确认删除 #验收频道？`；频道仍在左栏（**没有发请求**） | `06-confirm.png` |
| 点「确认删除」 | 标题变成 `第二个频道`；活跃段只剩 `channel-ch_d850877951b7`；toast `已删除频道 #验收频道。`；服务端 `GET /api/channels` 只剩 `ch_d850877951b7`，`GET /api/channels/ch_d1603c195e41/messages` ⇒ **404**；设置弹窗已关闭（`#channelConfigOverlay` 有 `hidden`、`display:none`） | `07-deleted.png` |

### 4.1 最终代码上的第二次走查（review 收口之后）

同一套动作在新的一次性数据目录 `/tmp/alibot-archive-ui2`（`--port 8801`）上重跑一遍，确认 review
的改动（危险区样式收敛到 `.danger-zone` / `.danger-hint`、`common.cancel` 复用、`channelArchiveList`
的 `data-od-id`）没有改变行为：

| 步骤 | 读数 | 截图 |
| --- | --- | --- |
| 归档 #验收频道 | 活跃段 `['channel-ch_761899df9fb2']`；归档段存在、`aria-expanded="false"`、段内 `['channel-ch_ba66658f7377']`；描述 `该频道已归档，保留为只读归档。`；composer `disabled=true`、占位 `该频道已归档，只读归档`；工具行 `['btn-channel-unarchive']` | `01-archived.png` |
| 展开归档段 | `aria-expanded="true"`，`channelArchiveList` 有可见尺寸；归档行可点开读历史 | `02-expanded.png` |
| 取消归档 | 活跃段恢复两个、归档段消失、工具行恢复五枚、composer 恢复可写、toast `已取消归档 #验收频道：频道恢复可写。` | — |
| 打开频道设置 | 危险区 `className = "d-sec danger-zone"`，归档说明可见，删除按钮 `删除本频道` 可用，确认条**未出现** | `03-danger.png` |
| 点「删除本频道」 | 确认条 `确认删除 #验收频道？`，按钮 `取消` / `确认删除`，**零请求** | `04-confirm.png` |
| 点「确认删除」 | 标题 `第二个频道`；`GET /api/channels` 只剩 `ch_761899df9fb2`；`GET /api/channels/ch_ba66658f7377/messages` ⇒ **404**；弹窗 `hidden`；toast `已删除频道 #验收频道。` | `05-deleted.png` |

---

## 5. 门禁读数

```sh
scripts/check.sh        # 全量（先 npm run build，再 pytest + ruff + ruff format --check + mypy + pyright + Vitest）
```

```text
1310 passed, 11 skipped in 77.99s (0:01:17)        # pytest
All checks passed!                                  # ruff check
388 files already formatted                         # ruff format --check（未跑 ruff format）
Success: no issues found in 138 source files        # mypy（仓库根）
0 errors, 0 warnings, 0 informations                # pyright
Test Files  25 passed (25) / Tests  495 passed (495) # Vitest
门禁全绿
```

---

## 6. 双轴 code-review（Standards + Spec）

见下一节的逐条处置（review 报告与处理结果在 §6 之后就地记录）。

---

## 7. 交付

- 分支：`feat-channel-archive-delete`（base `6f02a51`），已推送。
- PR：见 GitHub（正文 `app/specs/issue-342-pr-body.md` 逐字同文），`Closes #342`。
- **Door: one-way**（触及 schema `25 → 26`：合并后旧代码不能打开新库，只能沿链前移）。
- **Blast Radius: medium**（跨 schema、db 读路径、五个写入口与前端工作台；不涉及消息与历史数据本身）。
