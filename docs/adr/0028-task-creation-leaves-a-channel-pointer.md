# 建任务在频道流留下系统指针、不唤醒不计未读、只前进不回填

用户于 2026-10-20 报「建任务之后频道里看不到它」：任务只在自己的线程里有一条 `system` 起点，
频道的「消息」页签仍是 0，也没有进入任务线程的入口。本 ADR 记录这次裁定：入口落在**消息流**里、
它为什么必须是 `system`、为什么需要一列新字段，以及它**不**扩展到什么。

## 背景：任务创建目前只在任务 target 内留痕

`db.create_task` 在同一事务里建 `task` 行与它的 task 型 `target`，并向**任务线程**落一条
`system.task.created`（「任务已创建：{title}」）。频道 target 上没有任何记录，因此：

1. 频道消息流的读者看不到「这个频道多了一张任务」；
2. `task_ref` 帮不上忙——它的语义是**与消息同 target**（复合外键
   `(target_id, task_ref) → task(target_id, id)`），频道消息引用任务会被写路径拒绝；
3. 「消息 N」页签与未读徽标都读频道消息面，于是那条 `system` 起点对它们**完全不可见**。

## 一、入口是频道消息流里的一条 **system** 消息，带独立列 `task_pointer`

新增可空列 `message.task_pointer`（`REFERENCES task(id)`），**只由后端在建任务时写入**。

- **不复用、不放宽 `task_ref`**：两列是同一种「消息在说什么」的不同方向。`task_ref` 仍是
  「同 target 内的任务引用」（任务线程里的讨论锚点），`task_pointer` 是「频道 system 消息 →
  本频道下的任务」这条跨 target 指向。把 `task_ref` 放宽到跨 target 会让既有复合外键与
  `error.task_ref_other_target` 的裁决失去意义，也会让「同 target」这条不变量变得不可判。
- **客户端没有写它的路径**：`MessageCreate` 仍是 `extra="forbid"`，`task_pointer` 不在字段里。
  库层面再加两道保险：部分唯一索引（`task_pointer` 非空时唯一 ⇒ 一张任务至多一条频道指针）与
  `BEFORE INSERT` 触发器 `message_task_pointer_is_scoped`（非空时必须 `kind='system'`、所在
  target 是 channel 型、且 `task.channel_id = NEW.target_id`）。
- **正文复用同一词条**：body 就是 `system.task.created`，与任务线程那条**逐字一致**；作者仍是
  owner（系统事件消息的既有口径：作者不表示「人推的」）。

## 二、任务行与频道指针同一个事务

`create_task` 在既有事务里追加这条消息：任一插入失败 ⇒ **整个任务创建回滚**，不存在
「任务在、频道入口没落」或「入口在、任务没建」的半成品。返回形状仍是任务字典（`db` 层），
`service.create_task` 再把它作为 `"message"` 随响应返回，任一侧都不需要第二次写。

## 三、system 语义照旧：不唤醒、不消耗预算、不计未读

频道指针消息走既有的 **系统事件消息** 口径：

- **不唤醒**：`service.create_task` 只向**频道 target** 发布既有 SSE `message` 事件，
  **不**对频道调 `orchestrator.notify`；任务线程那一次 `notify_task_created`（队长 / 认领）逐字保留。
- **不计未读**：未读 SQL 的 `kind IN ('human', 'agent')` 一个字没改，因此频道 `unread_count`
  与 `unread_total` 都不因它变化。
- **不消耗预算**：预算只在人类消息落库（开世代）与跳投递时变化，`system` 不参与。

## 四、只前进、不回填

`SCHEMA_VERSION` 19 → 20，一步 `19 → 20`：`ADD COLUMN` + 索引 + 触发器。升级前的任务**不生成**
频道消息——读路径用 `task_pointer = null` / `task_pointer_message(...) = None` 如实表达
「这张任务没有频道入口」，而不是替历史造一条。频道 chip 的计数因此对旧任务不存在这一整行
（不是显示 0 的半成品）。

## 五、呈现：一个 chip，计数来自任务载荷

频道消息流把带 `task_pointer` 的 system 行渲染成「进入任务线程 · N 条」的 chip，
`data-open-task=<task id>` 复用既有 `openTask`；N 取任务载荷的 `message_count`
（该任务 target 的消息总条数，含 system / human / agent——**不是**未读）。建任务成功后
`submitTask` 吸收响应里的 `message`，当前频道立即重绘并滚到底，「消息 N」页签同时 +1。
