# Issue #198 验收：建任务留下频道指针消息 + 线程入口 + 消息计数

票面正本：GitHub [whutlichao/alibot#198](https://github.com/whutlichao/alibot/issues/198)。
本文件按「**判据 → 落点 → 命令与真实读数**」记录可复跑的验收证据。

## 0. 基线与分支

- 开工时 base：`main = a58f020`（`git rev-parse main` 与 `origin/main` 同值）。
- 工作分支：`issue-198-impl`（本 worktree）。
- 本票**不**放宽 `task_ref`、不 backfill 历史任务、不新增前端测试框架。

## 1. Schema 19 → 20（严格 +1）

| 判据 | 落点 |
| --- | --- |
| `SCHEMA_VERSION = 20`，只前进一号 | `app/alibot/db.py` |
| `message.task_pointer TEXT REFERENCES task(id)`（可空） | `db.SCHEMA` |
| 部分唯一索引：`task_pointer` 非空时唯一 | `idx_message_task_pointer` |
| `BEFORE INSERT` 触发器：非空指针必须是「channel 型 target 的 system 消息，且任务属于该频道」 | `message_task_pointer_is_scoped` |
| 链上一步 `19 → 20`，注册进 `STEPS` | `app/alibot/migrate.py::_step_19_to_20` |
| 历史留档（v20 对象 SQL + `VERSION_OBJECTS`） | `app/tests/_migration_history.py` |
| 链连续、覆盖到 `SCHEMA_VERSION`、每个源版本迁到终态 | `app/tests/test_migration_chain_guard.py` / `test_schema_migration.py` |

读数：

```text
$ cd app && ../.venv/bin/python -m pytest tests/test_migration_chain_guard.py
24 passed in 22.77s          # 与 test_task_channel_pointer.py 同批跑；链守卫含 v3..v20 全部源版本
```

迁移实现上的一处必要修正：`_create_missing_indexes` 现在会**跳过「索引引用的列还没到」的对象**。
从 v5 / v8 起步时，`idx_message_task_pointer` 会在补列之前被枚举到；跳过之后，执行器每步之后的
形态对齐（先补列、再建索引）仍会把它建出来，唯一性收紧不会被静默丢掉。

## 2. 建任务：同事务落频道指针消息

| 判据 | 落点 |
| --- | --- |
| 任务行 / 任务线程首条 system / 频道指针消息在**同一事务** | `Database.create_task` → `_append_task_pointer_locked` |
| 正文与任务线程那条逐字一致（`system.task.created`） | 同上；两处都取 `self._render_copy("system.task.created", title=...)` |
| author 恒为 owner；`task_pointer = task_id`；`task_ref = NULL` | 同上 |
| 任一插入失败 ⇒ 任务行与两条消息全无 | 事务回滚；用例 `test_task_creation_rolls_back_when_the_channel_pointer_insert_fails` |
| 客户端不能写 `task_pointer` | `MessageCreate(extra="forbid")`；用例 `test_clients_cannot_write_task_pointer` |

读数：

```text
$ cd app && ../.venv/bin/python -m pytest tests/test_task_channel_pointer.py
5 passed
```

其中 `test_creating_a_task_appends_exactly_one_channel_pointer_message` 断言：频道消息列表最后一条
`kind == "system"`、`task_pointer == task["id"]`、`task_ref is None`、`body == "任务已创建：整理验收清单"`、
`author.id == "owner"`；任务线程仍只有它自己那条 system。

## 3. 读面

| 判据 | 落点 |
| --- | --- |
| `_message_payload` 暴露 `task_pointer`（无值为 null） | `db._message_payload` |
| 按任务取频道指针消息（供 SSE 发布） | `Database.task_pointer_message(task_id)`，无则 `None` |
| 任务载荷增加 `message_count`（线程消息总条数，含 system/human/agent） | `Database.get_task` |
| 任务线程读面逐字不变 | `list_task_messages` 未改；用例断言线程仍只有一条 system |

读数：后端用例断言 `task["message_count"] == 1`，线程追加一条人类消息后变为 `2`；
`GET /api/channels/{id}/messages` 与 `POST` 响应里的 `message` 完全一致（同一条记录）。

## 4. `service.create_task`：频道 SSE + 返回 `message`

| 判据 | 落点 |
| --- | --- |
| 向**频道 target** 发布既有 `message` 事件（`{type, seq, message}`） | `WorkspaceService.create_task` |
| 返回载荷增加 `"message"` | 同上（无指针消息时为 `null`） |
| **不**对频道调 `orchestrator.notify`；`notify_task_created` 逐字保留 | 同上 |
| 频道指针 system 消息不计未读、不唤醒 | 未读 SQL 仍 `kind IN ('human','agent')`；用例断言频道/任务/工作台三处 `unread_count` 为 0、调度器无待跑轮次 |

真实事件流读数（`test_channel_event_stream_carries_the_pointer_message`）：连接 `GET /api/channels/{id}/events`
后建任务，收到 `type == "message"`、`message.task_pointer == <task id>`、`kind == "system"`，
帧游标等于消息自己的 `seq`。

## 5. Studio

| 判据 | 落点 |
| --- | --- |
| `message.task_pointer` 渲染 `.task-ref-chip`，`data-open-task=<task id>` | `app/alibot/studio/app.js::appendTaskRefChip` |
| 文案「进入任务线程 · {count} 条」，count = `taskById(...).message_count`（取不到为 0） | 同上 |
| aria-label 含任务标题与条数 | 同上（`msg.taskThreadEntryAria`） |
| 既有 `task_ref` 分支逐字保留 | 同上 |
| `submitTask` 吸收 `payload.message`，本频道立即 `renderMessages()` + 滚到底，否则 `refreshChannelList()` | `app/alibot/studio/app.js::submitTask` |
| 页签「消息 N」包含该条 | `renderMessages()` → `renderTabs()` 的既有计数来源 |
| 新词条 zh / en 同键 | `app/alibot/studio/i18n.js`（`msg.taskThreadEntry` / `msg.taskThreadEntryAria`） |

读数：

```text
$ node --test app/tests/task_pointer_ui.test.cjs
✔ 频道指针消息渲染出进入任务线程的 chip，文案带任务线程消息数，点击打开线程
✔ 建任务成功后吸收返回的 message：流里立刻出现该行，页签计数 +1
✔ 任务不在快照里时 chip 的计数显示 0，不编造条数
ℹ tests 3 / pass 3 / fail 0
```

## 6. 真机 UI 验证（ego-browser）

隔离数据目录 + 空闲端口启动：

```sh
.venv/bin/python -m alibot --port 8763 --data-dir /tmp/alibot-198-check
```

在 Studio 中新建频道「验收频道」，用「作为任务发送」建任务「验收任务 198」。实际读数（DOM 探针）：

```json
{
  "msgCount": "1",
  "taskCount": "1",
  "rows": [{ "cls": "msg sys",
             "text": "O Owner 事件 22:33 任务已创建：验收任务 198 进入任务线程 · 1 条" }],
  "chip": { "text": "进入任务线程 · 1 条",
            "openTask": "task_7c775a5571b6",
            "aria": "打开任务 验收任务 198 的任务线程，共 1 条消息" }
}
```

点 chip 后后端请求日志（真实 HTTP）：

```text
GET /api/tasks/task_7c775a5571b6/messages HTTP/1.1 200 OK
```

刷新整页后重新读数：`msgCount = "1"`、同一行 system 与同一 chip 仍在，频道栏**没有**未读徽标。
截图：`/tmp/alibot-198-channel-pointer.png`（频道流 + chip）、`/tmp/alibot-198-task-thread.png`（点开线程）。

## 7. 全量门禁

```sh
$ scripts/check.sh
# 读数见本节末尾（全量 pytest + ruff + ruff format --check + mypy + pyright + node --test）
```

（读数待填）

## 8. 双轴 code-review

（结论与处理待填）

## 9. Merge Danger

本票含 **schema 迁移（19 → 20，单向门）**：合并后旧代码无法读取新的库版本；升级必须显式执行
`python -m alibot --migrate --data-dir <数据目录>`（迁移前自动整目录备份）。旧任务不生成频道指针
消息（只前进、不回填）。
