# target 升为一等对象，channel 型 target 复用 channel.id

第二版要在频道之外开两种作用域：**DM**（两人私信）与**任务线程**。消息、已读位置、回应预算、轮次结果
四件事的作用域键因此不能再写死成"频道"。我们决定引入 `target(id, kind ∈ {channel, dm, task})`，四者全部挂
`target_id`；`UNIQUE(target_id, seq)` 不变——seq 仍是 **target 内**单调，而不是频道内单调。

关键取舍是 **channel 型 target 的 id 从哪来**。两条路：复用现有 `channel.id`（同一个字符串既是频道主键、
也是 target 主键），或新建 target 行再用映射表指向频道。我们选复用：`message.target_id` 今天的值**就是**
channel id（`CHANNEL_SELECT_SQL` 写的是 `m.target_id = c.id`），复用是零语义漂移；`target` 表对 channel 型
因此是一张**影子行**——id 同值、只承载 `kind`，频道自己的 `name` / `description` 仍留在 `channel` 表。

被否的映射方案代价是多一张映射表、每处读路径多一跳、404 早退多一次查询，而它买到的只是"target 表能独立
存在"这个形式上的整洁。**影子行的生命周期因此被频道的生命周期决定**：本 ADR 落地时本仓没有删除频道的入口，
所以影子行也没有删除路径——不存在"target 还在、频道没了"或反过来的中间态。
（**2026-10-30 的事实修正**：#342 / ADR-0040 给频道加了两档**软删除**。这一段的结论**不变**：删除只写
`channel.deleted_at`，影子行与它承载的消息、成员、已读、预算**一行都不删**；「频道没了」这件事在读路径上
表达为 404，而不是一行被删掉的记录。）

随之而来的是两处改名，因为旧名在新数据上是**错名**：`channel_read` → `target_read(member_id, target_id,
last_read_seq)`，`channel_budget` → `target_budget(target_id, epoch_seq, used)`。`kind IN ('dm','task')` 的行里
写 `channel_id` 会读成"这一行的频道是 X"，而 X 不是频道。`round_outcome.target_id` 本来就叫对了，不动。

代价是 `SCHEMA_VERSION` 要 bump 且**不做旧数据迁移**（新库从空数据开始，第二版不在本机做过一次数据搬迁）。
这与 ADR-0002 的关系是**泛化而非推翻**：跳数上界仍是 3、`used` 仍是跳数的唯一正本、耗尽仍是一个唤醒都不投递，
变的只是"每个频道一份预算"扩成"每个 target 一份"。跨 target 共享一份预算会把"一个频道烧完把另一个频道锁死"
变成一个真实故障，所以不做。
