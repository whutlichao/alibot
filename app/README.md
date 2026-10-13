# worksplice 应用后端 + 浏览器工作台（实施票 ①）

从**空数据**开始走完人类侧第一段闭环：新建频道 → 发送频道消息 → 后端持久化 → 前端呈现，
并且每个频道的**未读徽标**始终正确。

本目录只服务 **HTTP 人类面**（浏览器与人自己的脚本）。事件流实时推送、Agent 创建、
@提及与唤醒都在后续票交付，本票不引入任何模型调用。

## 运行

```sh
# 1) 装依赖（Python ≥ 3.11）——在仓库根建虚拟环境
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e "./app[dev]"

# 2) 启动后端（默认只绑定 loopback，默认端口 8788）
.venv/bin/python -m worksplice
#   或显式指定端口与数据目录
.venv/bin/python -m worksplice --port 8788 --data-dir /tmp/worksplice-smoke
```

浏览器打开 <http://127.0.0.1:8788/studio/>（根路径 `/` 会 302 到这个地址）。

首版无认证，因此**默认只绑定 loopback**，不提供局域网/远程访问。
确实需要时设 `WORKSPLICE_ALLOW_REMOTE=1` 显式越界，并自行承担风险。

### 数据目录：备份方式就是复制目录

| 项 | 值 |
| --- | --- |
| 默认端口 | `8788`（`--port` 可改，见 issue #2 评论里的取舍） |
| 默认数据目录 | `~/Worksplice/`（可见、可直接复制） |
| 覆盖方式 | `--data-dir <路径>` 或环境变量 `WORKSPLICE_DATA_DIR` |
| 库文件 | `<数据目录>/worksplice.db`（单文件 SQLite，WAL 模式） |

备份：`cp -R ~/Worksplice ~/Worksplice-backup-$(date +%F)`。恢复：反向复制回去。
库文件旁边可能有 `-wal` / `-shm` 两个伴随文件，一起复制即可。

## 数据模型（决策级）

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `member` | id, kind(`owner`/`agent`), 显示名, 头像色位 | 身份正本；空库只有一条 `owner` 行 |
| `channel` | id, 名称, 描述 | |
| `channel_member` | channel_id, member_id | 每频道显式成员表 |
| `channel_read` | member_id, channel_id, last_read_seq | 已读位置（未读的正本），只在同频道内取 max |
| `message` | id, target_id, seq, author_member_id, kind, 正文 | **append-only**，`UNIQUE(target_id, seq)` |

- **消息 append-only 是硬约束**：库级触发器拒绝任何 `UPDATE` / `DELETE`，HTTP 面也没有编辑或删除端点。
- **频道内单调 seq** 由后端在同一事务内分配，同时充当渲染顺序。
- **空库里唯一的成员行是 `owner`**（规格固定），不是预置业务数据；没有种子频道、种子消息、种子 Agent。
- 未读计数 = `last_read_seq` 之后、`kind ∈ {human, agent}` 的顶层消息条数（不计 `system`）。

## HTTP 契约

| 方法与路径 | 作用 |
| --- | --- |
| `GET /api/workspace` | 工作台快照（成员、频道、未读合计） |
| `GET /api/channels` | 频道列表（含 `last_seq` / `last_read_seq` / `unread_count`） |
| `POST /api/channels` | 新建频道 `{name, description, member_ids}`；校验失败**整体不创建** |
| `GET /api/channels/{id}/messages` | 按 seq 升序取消息（`?after_seq=n` 取增量） |
| `POST /api/channels/{id}/messages` | 发送人类消息 `{body}`（同事务分配 seq） |
| `GET /api/channels/{id}/members` | 频道成员 |
| `POST /api/channels/{id}/read` | 标读 `{seq}`（同频道内取 max，裁剪到当前最大 seq） |
| `GET /api/health` | 存活与数据目录 |

错误统一为 `{"error": {"code", "message", "fields"?}}`。

## 测试与检查

```sh
.venv/bin/python -m pytest          # HTTP 黑盒契约测试（每条用例独立临时数据目录）
.venv/bin/ruff check app && .venv/bin/ruff format --check app
.venv/bin/python -m mypy            # 配置在仓库根 mypy.ini
node ~/.pi-lens/tools/node_modules/pyright/index.js app/worksplice app/tests   # 配置在仓库根 pyrightconfig.json
```

测试只断言**外部可观察行为**（消息出现与顺序、seq 单调性、已读位置、未读计数、
不可变性、重启后一致），不断言内部函数或前端 DOM。每条用例的库都在 `tmp_path` 下，
**不触碰** `~/Worksplice`。

## 视觉契约

工作台只**只读**参照 `worksplice-design-system/`（原型保持原样，不被本票修改）：
token 名、三栏尺寸（rail 252 / 消息流 880 / dock 380）、方形头像块（7px 圆角 + `--av-0…--av-4`）、
未读徽标色族（`--unread` / `--unread-soft` / `--unread-deep` / `--unread-graphic`）、
四态状态点与空态文案都按名字消费。色值一律 `oklch()`，界面为简体中文。

业务状态的唯一正本是后端；前端不使用 `localStorage` 保存任何业务状态
（唯一允许的本地记忆是「上次打开的频道 id」）。
