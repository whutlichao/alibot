"""单文件 SQLite 持久化：target、成员、消息与每 target 已读位置的正本。

本模块是应用后端的持久化正本（spec #1 §2、第二版规格 #23 §2）。约束：

- 消息 **append-only**：不存在更新或删除消息的代码路径（库级触发器兜底）。
- **target 内**单调 ``seq``：``UNIQUE(target_id, seq)``，由本模块在同一事务内分配。
- 已读位置 ``target_read.last_read_seq`` 只在同一 target 内取 max 单调推进。
- 未读计数 = ``last_read_seq`` 之后 ``kind in ('human','agent')``、**作者不是本人**的顶层
  消息条数（#4 的人类裁定排除本人作者：#2 原本把 owner 自己的消息也计入）。

**消息作用域是 target，不是频道**（ADR-0004）：``target(id, kind ∈ {channel, dm, task})`` 是消息、
已读位置、回应预算与轮次结果的共同键。**channel 型 target 复用 ``channel.id``**——同一个字符串既是
频道主键也是 target 主键，``target`` 行对 channel 型是一张**影子行**（id 同值、只承载 ``kind``）；
频道的 ``name`` / ``description`` 仍留在 ``channel`` 表，**没有**映射表。三处旧名随作用域改名：
``channel_member → target_member``、``channel_read → target_read``、``channel_budget → target_budget``。

空库只含一条固定成员行 ``owner``（规格固定的唯一成员行），没有任何业务种子数据。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from .i18n import SOURCE_LOCALE, locale_of, render
from .permissions import effective_rules
from .reminder_delivery import reminder_state
from .web import SEARCH_PROVIDERS, egress_hosts, mask_credential

OWNER_ID = "owner"
OWNER_DISPLAY_NAME = "Owner"
OWNER_AVATAR_SLOT = 4

# 头像色位族大小：与设计系统的 --av-0…--av-4 一一对应（色位按 slot 取模）。
# 前端 studio/app.js 的同名常量必须与本值一致。
AVATAR_SLOT_COUNT = 5

# MCP 声明名同时是 AgentScope 的 MCPClient 名（工具名 ``mcp__{name}__{tool}``），框架只接受
# ``[a-zA-Z0-9_-]+``；这里在写路径就拒绝，避免「存得进去、装配时才炸」。
MCP_SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# ``env_names`` 只存变量**名**：这个形状同时排除了 ``KEY=value`` 这类把值写进名字的输入。
MCP_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

# schema 版本（`schema_meta.schema_version`）。闸门见 `_assert_schema_compatible`：不等就**拒绝**。
# 迁移**不是**启动路径的一步，而是显式命令 `python -m alibot --migrate`（ADR-0015 取代 ADR-0004
# 的「不做旧数据迁移」口径）。号段由规格 #60 的协调裁定分配，实施票不得自行改号：
#   7 = #61（工作区设置面）已合并；8 = #68（reminder 表）已合并；9 = #67（附件）；
#   10 = #71（`workspace_settings.theme` 新增一列）已合并；11 = #35 的 #58（结构化沉默）；
#   12 = **本票 #65**（`agent_config.auto_claim` 与 `task.claimed_at` 各新增一列）。
# 号段是**跨在途票的分配**而不是「每次 +1」：两个在途迁移撞号会让两边都以为自己的库是当前版本。
# #67 的落地口径（补收尾纠正）：原预留的 9 取不到——#71 已把 10 落到 main，带着 9 合入会让
# 版本号在历史上**不单调**（10 → 9），且被 v10 代码创建过的库在本版下会被闸门判为版本不符。
# 正确判据是「**必须高于当前 main**」而不是「等于被分配的预留号」，故 #67 取 11（12 归 #65）；
# 该票已随 PR #83 落在 main（b619336），main 当前读数 11。
# #65 的 12 高于当前 main 的 11，合并后**保持 12**（不回退到 11）。
# 13 = **本票 #112**（提醒到点触发的库形态：`reminder.time` 形状扩为「日期 + 钟点」、新增可空列
# `reminder.fired_at`）。它取 13 的判据同上：`main` 是 12、13 是满足「必须高于当前 main」的最小号
# （ADR-0017 §五）。
# 代价（与既有纪律一致）：合力后 main 的号由 12 升到 13，被 12 及更早代码创建过的库在本版下
# 会被闸门拒绝——处置由拒绝正文给出：先备份整个数据目录，再用 `python -m alibot --migrate` 逐版
# 推进（迁移链覆盖 3..13，见 `app/alibot/migrate.py`）。
# 14 = **本票 #53**（回应预算的配置落点：`channel_budget_limit` 表 +
# `target_budget.applied_limit` 快照列）。判据同前：「必须高于当前 main」——main 是 13，
# 14 是满足它的最小号；后续票从 15 起（号段是跨在途票的分配，不是每次 +1）。
# 代价同上：被 13 及更早代码创建过的库在本版下会被闸门拒绝，处置是备份整个数据目录后用
# `python -m alibot --migrate` 逐版推进（迁移链覆盖 3..14）。
# 15 = **本票 #58**（结构化沉默信号 `silent`：`round_outcome.status` 的库级 CHECK 纳入第六个词，
# ADR-0008）。SQLite 改表级 CHECK 只能重建表，因此多了一步 `14 → 15`（`migrate._step_14_to_15`）；
# 判据同前：main 是 14、15 是满足「必须高于当前 main」的最小号。
# 16 = **本票 #138**（AgentScope 2 对齐：team 模式 / Plan / SOP 的纯增量库形态，规格 §4.1 / §4.2 / ADR-0018 /
# ADR-0022 / ADR-0023）。它只加列与表、不重建既有表、不回填业务数据；`15 → 16` 一步
# （`migrate._step_15_to_16`）。判据同前：main 是 15、16 是满足「必须高于当前 main」的最小号。
# 17 = **本票 #139**（AgentScope 2 对齐：workspace / permission / MCP / Skill / approval 的纯增量库形态，
# 规格 §4.1 / §4.3 / ADR-0021）。它只加列与表、不重建既有表、不回填业务数据；旧 `remote_consent`
# 行复制成 `remote_consent_scope` 的 `model` 行（旧表保留）；`16 → 17` 一步
# （`migrate._step_16_to_17`）。判据同前：main 是 16、17 是满足「必须高于当前 main」的最小号。
# 代价同上：被 16 及更早代码创建过的库在本版下会被闸门拒绝，处置是备份整个数据目录后用
# `python -m alibot --migrate` 逐版推进（迁移链覆盖 3..18）。
# 18 = **本票 #184**（Agent 联网的库形态：`remote_consent_scope.scope_kind` 纳入 `web` 档，
# ADR-0026 §四；`channel.web_access` / `agent_config.web_access` 两个单值配置位，ADR-0026 §二）。
# 改表级 CHECK 只能重建表，因此 `17 → 18` 里既有 `remote_consent_scope` 的重建（旧行逐行保真），
# 也有两列的 `ADD COLUMN`；**不为网络请求新增任何用量表 / 计数列**（预算口径不变，ADR-0026 §六）。
# 判据同前：main 是 17、18 是满足「必须高于当前 main」的最小号。
# 19 = **本票 #187**（Docker 后端的容器网络策略：``channel.docker_egress`` 一个频道级开关列，
# ADR-0026 §五）。它是**纯增量**：``ADD COLUMN`` + 列级 CHECK，不回填旧数据（旧频道停在 0 =
# 容器默认无网），因此 ``18 → 19`` 一步（`migrate._step_18_to_19`）。判据同前：main 是 18、
# 19 是满足「必须高于当前 main」的最小号。
# 20 = **票 #198**（建任务在所属频道落一条可进入任务线程的指针消息：``message.task_pointer``
# 可空列 + 部分唯一索引 + BEFORE INSERT 触发器，ADR-0028）。它是**纯增量**：``ADD COLUMN`` +
# 索引 + 触发器，**不回填旧数据**（旧任务没有频道消息，读路径如实返回 null），因此 ``19 → 20``
# 一步（`migrate._step_19_to_20`）。判据同前：main 是 19、20 是满足「必须高于当前 main」的最小号。
# 21 = **票 #195**（自动认领默认开启 + 仅在自主频道生效，ADR-0029）。它 flip
# `agent_config.auto_claim` 的 DDL 默认值 0 → 1，并在 ``20 → 21`` 里按当前 ``SCHEMA`` 重建该表、
# 把存量 ``auto_claim = 0`` 的行一次性回填为 1（返回真实改动行数）。判据同前：合并时的 main 已是 20
# （#198 的频道指针消息），21 是满足「必须高于当前 main」的最小号——本票原先预留的 20 已被 #198 占用。
# 22 = **本票 #199**（多 provider 搜索 + 凭据入库 + 联网档默认 ``search`` / 显式 ``off``）。
# ``web_provider_credential`` 是新表；``WEB_ACCESS_LEVELS`` 纳入 ``off`` 需要重建 ``channel`` 与
# ``agent_config`` 两表的列级 CHECK（SQLite 改列约束只能重建），``21 → 22`` 一步
# （`migrate._step_21_to_22`）。不回填：既有行逐字保留，读路径把 ``NULL`` 解释成默认档。
# 号段说明：本票原先自行取的是 21，但 #195（自动认领默认开启）随 main 先落地了 21，按 ADR-0015
# 「必须高于当前 main」的判据，本步顺延为 ``21 → 22``。
# 23 = Agent 运行时：可空的轮次结束原因，以及上下文窗口、压缩阈值、迭代上限、长期记忆配置。
SCHEMA_VERSION = 23

# 本版设计支持的**源版本**下界（规格 D4）：从 3 连续支持到 `SCHEMA_VERSION`。
# 3 是改名 worksplice → alibot 落地时的号，也是**唯一**同时存在于新旧数据目录的号；v1 / v2 的库只
# 可能在旧数据目录 `~/Worksplice`，`app/README.md` 已明写改名不保留兼容。它出现在拒绝正文里，
# 与 `SCHEMA_VERSION` 一起构成「本版支持的源版本范围」这条可读诊断。
MIN_SUPPORTED_SOURCE_VERSION = 3

# 工作区设置（`CONTEXT.md` **工作区设置**）：本机单用户工作台的**配置类**取值集合——
# **语言偏好** / **消息密度** / **主题**三档。列集就是「只有这些档位」这条约束的落点：
# 没有通用 key-value 面，因此这个设置面不可能被当成通用存储。
LOCALES = ("zh-CN", "en")
MESSAGE_DENSITIES = ("comfortable", "compact")
# 主题三档（#71）：显式浅色 / 显式深色 / 跟随系统外观。
# `system` 是**缺省**档（空库的取值就是它）：由浏览器的 `prefers-color-scheme` 裁决；
# 显式两档把标记写在 `<html data-theme>` 上。三档的**取值层**在 `alibot-design-system/`
# （同一 token 名集合的第二组取值，`decisions/ADR-0017-dark-value-layer.md`）。
THEMES = ("light", "dark", "system")

# 缺省语言 = 词条表的**源语言**（同一件事不写第二遍：改源语言只改 `i18n.SOURCE_LOCALE`）。
DEFAULT_LOCALE = SOURCE_LOCALE
DEFAULT_MESSAGE_DENSITY = "comfortable"
DEFAULT_THEME = "system"

DEFAULT_PORT = 8788
# 数据目录与全局层共享隐藏根 ``~/.alibot/``（ADR-0031）：新默认是它下面的 ``data/``。
# 旧默认 ``~/Alibot`` 只做**兼容读取**，搬迁永远是人的显式动作。
DATA_DIR_ROOT_NAME = ".alibot"
DEFAULT_DATA_DIR_NAME = "data"
LEGACY_DATA_DIR_NAME = "Alibot"
DATA_DIR_ENV = "ALIBOT_DATA_DIR"
DATABASE_FILE_NAME = "alibot.db"

MESSAGE_KINDS = ("human", "agent", "system")

# target 的三种 kind（ADR-0004 / 规格 #23 §1）：channel 型复用 channel.id（影子行），
# dm / task 由各自的服务路径创建与管理；本表统一承载三种 target 作用域。
TARGET_KINDS = ("channel", "dm", "task")

# team 模式（D1 / ADR-0018）：频道级拓扑开关。缺省 autonomous；team 必须有一个队长（由库内
# 触发器守不变量，写入路径在 :meth:`Database.update_channel_config` 再做可读校验）。
CHANNEL_MODES = ("autonomous", "team")

# 频道级 workspace 默认值（ADR-0021）：``workspace_root`` 可空，后端与权限档是默认值。
WORKSPACE_BACKENDS = ("local", "docker")
PERMISSION_MODES = ("read-only", "ask-writes", "trusted-writes")

# 联网档（ADR-0026 §二，2026-10-20 修订）：三档。``NULL`` 是「未配置」——读路径把它解释成
# 默认档 ``search``；``off`` 才是**显式关闭**。`search` 与 `search+fetch` 只差 ``fetch`` 装不装。
# 它与 ``allow_local_shell`` 是两个开关：那个的风险面是本机任意进程，这个是数据出网。
WEB_ACCESS_LEVELS = ("off", "search", "search+fetch")

#: 联网档的缺省值（读路径的最后一层回退）：未配置 = 默认开启搜索、但不抓取。
DEFAULT_WEB_ACCESS = "search"

#: 凭据长度的硬上限（写路径校验；超长整体拒绝，不截断保存）。
MAX_WEB_CREDENTIAL_LENGTH = 4096

#: ``remote_consent_scope`` 的 scope 档位（ADR-0021 §三 / ADR-0026 §四）。``web`` 档（#186 / D6）与另三档
#: 同一纪律：**只存「何时确认过」**，不存凭证、不存连接参数；确认单位是 provider，host 级粒度归
#: ``permission_rule``。
REMOTE_CONSENT_SCOPE_KINDS = ("model", "mcp", "docker", "web")

# team 运行记录的状态词（规格 §4.2）。它不是 ``round_outcome``：只描述一次队长轮次的生命周期。
TEAM_RUN_STATUSES = ("running", "completed", "failed", "interrupted", "invalid-leader")

# Plan 运行记录的状态词与保留策略（ADR-0023 / 规格 §3.5）。Plan 是**成员轮次内的工作清单**，
# 不是任务五态：每个 ``(target_id, member_id)`` 保留最近 20 个 run 或 30 天（先到者为准），
# 更旧的 run 连同它的 ``plan_task`` 一并清理。
PLAN_RUN_STATUSES = ("running", "completed", "failed")
PLAN_TASK_STATES = ("pending", "in_progress", "completed")
PLAN_RUN_RETENTION = 20
PLAN_RETENTION_DAYS = 30

# SOP 运行记录的状态词与保留策略（ADR-0023 / 规格 §3.5）。SOP 是 **owner 启动的多步运行**，
# 不是任务五态：运行保留最近 50 个或 90 天（先到者为准），完成/失败后**不删除定义**。
SOP_PHASES = ("pending", "running", "awaiting", "completed", "failed")
SOP_RUN_RETENTION = 50
SOP_RETENTION_DAYS = 90

# 一次 ``TeamAssign`` 的运行记录状态（规格 §3.4 / §4.2）。``budget-exhausted`` 是「没有运行成员」，
# 其余四态描述「成员这一条分派」的结果；跳一旦扣减就不退款，失败/中断也保留 ``jump_consumed=1``。
TEAM_ASSIGNMENT_STATES = ("started", "succeeded", "failed", "interrupted", "budget-exhausted")

# 「这次请求没提到这个字段」与「显式清空」是两件事；``workspace_root`` 与两处 ``web_access``
# （channel / agent_config）允许后者（ADR-0021 §六 / ADR-0026 §二、§十五）。
_UNSET = object()

# 计入工作台快照 ``unread_total`` 的 kind（规格 §2 / 决策 G5）：channel 型 + dm 型之和。
# **task 型不进** ``unread_total``（看板卡片自带徽标，各自读它）。
UNREAD_TOTAL_KINDS = ("channel", "dm")

TASK_STATUSES = ("todo", "in_progress", "in_review", "done", "closed")
TASK_STATUS_TRANSITIONS = {
    "todo": {"in_progress", "closed"},
    "in_progress": {"in_review", "todo", "closed"},
    "in_review": {"done", "in_progress", "todo"},
    "done": {"in_progress", "closed"},
    "closed": set(),
}


def allowed_task_transitions(status: str) -> list[str]:
    """某个状态的全部合法迁移目标（#191）。

    顺序固定为 ``TASK_STATUSES`` 的顺序，终态是空数组——这是任务载荷
    ``allowed_transitions`` 的**唯一派生点**，因此呈现面不必（也不许）自带一份迁移表副本。
    """
    return [target for target in TASK_STATUSES if target in TASK_STATUS_TRANSITIONS.get(status, set())]


# 认领在同一个事务里把任务推进到的状态（ADR-0016 二：认领是**开工动作**）。
# 它是**表里的一个格子**而不是任意取值：``claim_task`` 用
# ``CLAIM_START_STATUS in TASK_STATUS_TRANSITIONS['todo']`` 做合法性判定，因此收紧迁移表时
# 认领会跟着失败，而不是绕过这张表静默越界。
CLAIM_START_STATUS = "in_progress"

# 轮次结果的六种状态（spec #1 §5 + #58 / ADR-0008）。status 是「自判忽略」「模型声明沉默」与
# 「处理失败」的区分来源：``ignored`` 与 ``silent`` 都不是失败，前者是既有的 seam 自判忽略，后者是
# 模型经结构化通道（``declare_silence`` 工具）明确声明本轮沉默。``reason`` 只是可读诊断，不承担判别。
ROUND_STATUSES = ("replied", "ignored", "failed", "merged", "budget-exhausted", "silent")

# 有界响应预算（策略 B，spec #1 §5）：一个**人类世代内同一 target** 允许的 agent→agent 跳数上界。
# 内置默认值仍是 3（ADR-0002 保留原文：耗尽仍零投递、默认值仍是它）。频道可以把它**上调/下调**到
# 固定档位之一（#31 §2 人定，落库于 ``channel_budget_limit``），未配置的频道回落到这里。
BUDGET_LIMIT = 3

# 自动续跑（ADR-0027）：**一次人类世代内**，受派者在一张任务上最多跑几轮（含第一轮 / 认领轮）。
# 它和 `BUDGET_LIMIT` 是两个计数器：跳数只数 agent→agent 的投递，续跑轮不是跳（它不唤醒其他
# Agent），因此不能共用 `target_budget.used`。3 轮续跑 = 认领轮之后最多再干 3 轮。
TASK_CONTINUE_ROUNDS = 3
# 上限落在「轮数」上：第一轮（认领 / 指派唤醒）+ 最多 `TASK_CONTINUE_ROUNDS` 轮续跑。
# 计数读 `round_outcome`（持久、跨重启一致），不新增 DDL。
TASK_WORK_ROUNDS_PER_EPOCH = 1 + TASK_CONTINUE_ROUNDS

# 频道可选的**固定档位**（#31 §2 人定）：不支持任意整数、0 或 1。非法值必须明确拒绝、不部分写入
# （写路径见 :meth:`Database.set_channel_budget_limit`）。
BUDGET_LIMIT_TIERS = (3, 5, 10)

# 「当前世代的**有效上界**」来自哪一层：机器可读的词，不译（文案由读方各自取词条）。
# 这两个值只出现在只读载荷的 ``source`` 字段里，判据是「上一层是否有已保存的频道覆盖」。
BUDGET_LIMIT_SOURCE_CHANNEL = "channel-override"
BUDGET_LIMIT_SOURCE_DEFAULT = "built-in-default"

# ``GET /api/{channels,tasks}/{id}/budget`` 的**既有**五项载荷。字段名一个字不改（票面 Constraint：
# 只做扩展）——频道面在它之上再补 ``source`` / ``pending_limit``（见 ``channel_budget_view``），
# 而 task 型读路径仍只给这五项（它的载荷契约由既有测试逐字钉住）。
BUDGET_STATE_KEYS = ("channel_id", "epoch_seq", "limit", "used", "remaining")

# 工作台搜索的硬上限：前端可以请求更小的窗口，但不能把整张消息表一次搬到浏览器。
SEARCH_RESULT_LIMIT = 200

# 判断「这个库是不是空库」时要看的业务表名（见 :meth:`Database._assert_schema_compatible`）。
# 版本号缺失时**无法**从版本号判断库来自哪一代，所以新旧两代的名字都在列表里：
# 只要其中**任意一张表有任何一行**，这个库就有数据，不是空库。
BUSINESS_TABLES = (
    "member",
    "agent_config",
    "channel",
    "channel_member",
    "target",
    "target_member",
    "task",
    "message",
    "attachment",
    "channel_read",
    "target_read",
    "channel_budget",
    "target_budget",
    "round_outcome",
    "remote_consent",
    "workspace_settings",
    "reminder",
)

# 逐表计数语句：由上面的常量在模块级拼好（与 ``AGENT_SELECT_SQL`` / ``UNREAD_COUNT_SQL`` 同一体例：
# **不把拼接写进 ``execute`` 的实参**）。表名只能来自上面的字面量元组，不是外部输入，
# 因此这里不存在标识符注入面。
BUSINESS_COUNT_SQL: dict[str, str] = {table: f'SELECT COUNT(*) FROM "{table}"' for table in BUSINESS_TABLES}

MAX_CHANNEL_NAME_LENGTH = 80
MAX_CHANNEL_DESCRIPTION_LENGTH = 500
MAX_MESSAGE_BODY_LENGTH = 8000
MAX_MEMBER_NAME_LENGTH = 40
MAX_AGENT_ROLE_LENGTH = 80
MAX_SYSTEM_PROMPT_LENGTH = 8000
MAX_MODEL_ID_LENGTH = 120
#: Docker 镜像引用（name[:tag|@digest]）的长度上界；只挡明显误用，不做语法解析。
MAX_DOCKER_IMAGE_LENGTH = 255

# 附件的**人类面**闸门（ADR-0014）：大小与类型都在这里被拒绝并给可读原因。
# 单文件上限取 10 MiB——它是「一眼看出传错了东西」的闸门，不是配额策略（多附件并发上限之外的
# 配额策略是规格明写的 Out of Scope）。
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

# 附件**原文件名**的长度上限：它是元数据（也只作元数据），但过长会让错误提示与下载名失控。
MAX_ATTACHMENT_NAME_LENGTH = 200

# 允许的 MIME 类型**窄清单**：按**声明类型**判定（本机单用户、无鉴权，不做内容嗅探）。
# 只收「本机用户会真的挂到消息上」的那几类；其余一律拒绝，拒绝文案走词条表。
ALLOWED_ATTACHMENT_MIME_TYPES = (
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
)

# 附件字节落数据目录下的这个子目录（库里只存**相对路径**，于是整个数据目录可整体搬迁）。
ATTACHMENTS_DIR_NAME = "attachments"

# 提醒（`CONTEXT.md` **提醒**：本机用户给自己设的一条**时间 + 文本**备忘，可关联频道）。
# 列的形状由实施票给定（ADR-0012）：**时间 + 文本 + 可归属一个频道**；ADR-0017 §二把时间语义升为
# **一次性**的「日期 + 钟点」，因此这里的合法形状是 24 小时制、零填充的 `YYYY-MM-DD HH:MM`。
# **没有重复字段**（「每天 / 每周」不接受）：重复这件事在本仓由**任务**承载，提醒不造第二个重复
# 语义（ADR-0017 §二）。
MAX_REMINDER_TEXT_LENGTH = 200
REMINDER_TIME_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01]) ([01]\d|2[0-3]):[0-5]\d$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS member (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('owner', 'agent')),
  display_name TEXT NOT NULL,
  avatar_slot  INTEGER NOT NULL,
  created_at   TEXT NOT NULL,
  deleted_at   TEXT
);

CREATE TABLE IF NOT EXISTS channel (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  -- team 是**频道级**拓扑开关（ADR-0018 / ADR-0022）：缺省 `autonomous`，旧行按默认值解释。
  -- team 必须有一个非空队长；这条不变量用触发器守，避免为「加约束」重建 channel 表。
  mode        TEXT NOT NULL DEFAULT 'autonomous' CHECK (mode IN ('autonomous', 'team')),
  team_leader_member_id TEXT REFERENCES member(id),
  -- workspace 是**频道级项目边界**（ADR-0021 §一）：所有成员共享这个根，文件工具的相对路径从这里
  -- 起算。``NULL`` = 未配置，沿用首版「没有文件 / shell 工具」的行为（ADR-0021 §六）。
  -- 后端与权限档是频道的**默认值**，Agent 可在 ``agent_config`` 上覆盖；旧行按列默认值解释
  -- （根未配置 / ``local`` / ``ask-writes``，规格 §4.1）。
  workspace_root TEXT,
  workspace_backend TEXT NOT NULL DEFAULT 'local'
    CHECK (workspace_backend IN ('local', 'docker')),
  permission_mode TEXT NOT NULL DEFAULT 'ask-writes'
    CHECK (permission_mode IN ('read-only', 'ask-writes', 'trusted-writes')),
  -- 联网档（#184 / ADR-0026 §二，2026-10-20 修订）：``NULL`` = 未配置（读路径回退到默认档
  -- ``search``）；``off`` = 显式关闭；其余两档只差 ``fetch`` 装不装，权限裁决在装配层。
  -- 它不是 ``allow_local_shell`` 的同义词：那个开关的风险面是本机任意进程，这个是数据出网。
  web_access TEXT CHECK (web_access IN ('off', 'search', 'search+fetch')),
  -- 容器出网开关（#187 / ADR-0026 §五）：``0`` = 容器以 ``--network none`` 创建（默认）；
  -- ``1`` = 容器接上 Docker 默认的网络，且联网工具只放行 ``permission_rule`` 里 ``fetch`` 的
  -- allow 规则的 host（白名单来源见 ``web.egress_hosts``）。它是**频道级**配置，与
  -- ``workspace_backend`` / ``web_access`` 同级但不同开关：那两个决定「挂什么工具」，
  -- 这个决定「容器能不能出网」。
  docker_egress INTEGER NOT NULL DEFAULT 0 CHECK (docker_egress IN (0, 1))
);

CREATE TRIGGER IF NOT EXISTS channel_team_requires_leader_insert
BEFORE INSERT ON channel
WHEN NEW.mode = 'team' AND NEW.team_leader_member_id IS NULL
BEGIN
  SELECT RAISE(ABORT, 'team channel requires a leader');
END;

CREATE TRIGGER IF NOT EXISTS channel_team_requires_leader_update
BEFORE UPDATE OF mode, team_leader_member_id ON channel
WHEN NEW.mode = 'team' AND NEW.team_leader_member_id IS NULL
BEGIN
  SELECT RAISE(ABORT, 'team channel requires a leader');
END;

-- target 是一切消息面的**唯一作用域键**（ADR-0004）：消息、已读位置、回应预算、轮次结果四件事
-- 全部挂 ``target_id``，``UNIQUE(target_id, seq)`` 因此说的是「target 内单调」。
-- **channel 型是一张影子行**：``id`` 与 ``channel.id`` 同值（复用，同一个字符串既是频道主键也是
-- target 主键），本表只承载 ``kind``；频道自己的 ``name`` / ``description`` 仍留在 ``channel`` 表，
-- 因此**没有**映射表。影子行的生命周期由频道决定：本仓没有删除频道的入口，也就没有删除它的路径。
-- 列的 CHECK 是 ``kind`` 受约束的正本（三种取值）；dm / task 型由各自的业务路径创建。
CREATE TABLE IF NOT EXISTS target (
  id                 TEXT PRIMARY KEY,
  kind               TEXT NOT NULL CHECK (kind IN ('channel', 'dm', 'task')),
  dm_peer_member_id  TEXT REFERENCES member(id),
  created_at         TEXT NOT NULL
);

-- 每个 Agent 最多一条 DM；频道与任务行的该列为空，不参与索引。
CREATE UNIQUE INDEX IF NOT EXISTS idx_target_dm_peer_unique
ON target(dm_peer_member_id) WHERE kind = 'dm' AND dm_peer_member_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS target_member (
  target_id TEXT NOT NULL REFERENCES target(id),
  member_id TEXT NOT NULL REFERENCES member(id),
  PRIMARY KEY (target_id, member_id)
);

-- 任务当前态在 task 行上；变更历史只进入该任务 target 的 append-only 消息流（ADR-0006）。
CREATE TABLE IF NOT EXISTS task (
  id                 TEXT PRIMARY KEY,
  target_id          TEXT NOT NULL UNIQUE REFERENCES target(id),
  channel_id         TEXT NOT NULL REFERENCES channel(id),
  title              TEXT NOT NULL CHECK (length(trim(title)) > 0),
  status             TEXT NOT NULL CHECK (status IN ('todo', 'in_progress', 'in_review', 'done', 'closed')),
  assignee_member_id TEXT REFERENCES member(id),
  -- 认领标记（#65 / ADR-0010）：``NULL`` = 这张任务**从未被认领过**，非空 = 首次认领那一刻。
  -- 它是「**每张任务至多一次**」这条构造性保证的落点：认领只看这个标记（而不是只看
  -- ``assignee_member_id``），于是即便任务后来被取消指派，也不可能触发第二次世代推进与预算重置。
  -- 它只记「认领发生过」，不记「认领了多久」：认领者是谁仍以 ``assignee_member_id`` 为准。
  claimed_at         TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  UNIQUE (target_id, id)
);

CREATE TRIGGER IF NOT EXISTS task_target_must_be_task
BEFORE INSERT ON task
WHEN (SELECT kind FROM target WHERE id = NEW.target_id) != 'task'
BEGIN
  SELECT RAISE(ABORT, 'task must own a task target');
END;

CREATE TRIGGER IF NOT EXISTS task_channel_must_be_channel
BEFORE INSERT ON task
WHEN (SELECT kind FROM target WHERE id = NEW.channel_id) != 'channel'
BEGIN
  SELECT RAISE(ABORT, 'task must belong to a channel target');
END;

CREATE TABLE IF NOT EXISTS message (
  id               TEXT PRIMARY KEY,
  target_id        TEXT NOT NULL REFERENCES target(id),
  seq              INTEGER NOT NULL,
  author_member_id TEXT NOT NULL REFERENCES member(id),
  kind             TEXT NOT NULL CHECK (kind IN ('human', 'agent', 'system')),
  body             TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  mention_snapshot TEXT NOT NULL DEFAULT '[]',
  task_ref         TEXT,
  -- 频道指针消息（#198 / ADR-0028）：**只由后端在建任务时写入**。它不是一个**放宽的** ``task_ref``
  -- ——``task_ref`` 仍要求与消息同 target（任务线程内引用），这一列反过来承载「频道 system 消息 →
  -- 本频道下的任务」这条跨 target 指向。``NULL`` 是常态，旧数据因此不需要回填。
  task_pointer     TEXT REFERENCES task(id),
  FOREIGN KEY (target_id, task_ref) REFERENCES task(target_id, id),
  UNIQUE (target_id, seq)
);

-- 一张任务至多一条频道指针消息：``task_pointer`` 非空时唯一。部分索引（``WHERE`` 子句）让
-- 「绝大多数消息的这一列是 NULL」不进入唯一性判定。
CREATE UNIQUE INDEX IF NOT EXISTS idx_message_task_pointer
  ON message(task_pointer) WHERE task_pointer IS NOT NULL;

-- 指针消息的作用域不变量（#198 / ADR-0028）：``task_pointer`` 非空 ⇒ **system** + **channel 型
-- target** + **该任务属于这个频道**。它挡的是「把 task_ref 的跨 target 引用偷偷挪到新列」——
-- 客户端没有写这一列的路径（``MessageCreate`` 不收它），触发器是库层面最后一道。
CREATE TRIGGER IF NOT EXISTS message_task_pointer_is_scoped
BEFORE INSERT ON message
WHEN NEW.task_pointer IS NOT NULL
  AND (
    NEW.kind IS NOT 'system'
    OR (SELECT kind FROM target WHERE id = NEW.target_id) IS NOT 'channel'
    OR (SELECT channel_id FROM task WHERE id = NEW.task_pointer) IS NOT NEW.target_id
  )
BEGIN
  SELECT RAISE(ABORT, 'task pointer must be a channel system message for this channel task');
END;

-- 附件（ADR-0014）：**元数据落库、字节落数据目录**。
-- ``relative_path`` 是相对**数据目录**的路径（不是绝对路径），于是整个数据目录可整体搬迁；
-- 字节**不进** SQLite：单文件库是这个仓的业务正本与备份单位，把二进制塞进去会让库体积、
-- 备份与损坏半径一起失控。
-- ``message_id`` 可空：``NULL`` = **未绑定的草稿**（读取面不暴露），随消息创建时在同一事务内绑定。
-- 不变量：绑定一次即不可变（见下面的 ``attachment_no_rebind``），且没有删除路径（``attachment_no_delete``）。
CREATE TABLE IF NOT EXISTS attachment (
  id            TEXT PRIMARY KEY,
  original_name TEXT NOT NULL,
  size_bytes    INTEGER NOT NULL CHECK (size_bytes >= 0),
  mime_type     TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  message_id    TEXT REFERENCES message(id)
);

CREATE INDEX IF NOT EXISTS idx_attachment_message ON attachment(message_id);

-- 附件的 append-only 兜底（与 ``message`` 同口径，ADR-0014 引 ADR-0001）。两条不变量：
--   1. **元数据不可变**：上传后不改内容、不改名、不覆盖 —— 任何列被改写都拒绝；
--   2. **绑定是单向的一次性动作**：``message_id`` 只能 NULL → 某条消息，不能改绑、不能解绑。
-- 于是「草稿 → 随消息绑定」是这张表上**唯一**允许的写后修改，与 ADR-0014 逐字一致。
CREATE TRIGGER IF NOT EXISTS attachment_metadata_immutable
BEFORE UPDATE ON attachment
WHEN OLD.id IS NOT NEW.id
  OR OLD.original_name IS NOT NEW.original_name
  OR OLD.size_bytes IS NOT NEW.size_bytes
  OR OLD.mime_type IS NOT NEW.mime_type
  OR OLD.relative_path IS NOT NEW.relative_path
  OR OLD.created_at IS NOT NEW.created_at
BEGIN
  SELECT RAISE(ABORT, 'attachment is immutable');
END;

CREATE TRIGGER IF NOT EXISTS attachment_binding_is_immutable
BEFORE UPDATE ON attachment
WHEN OLD.message_id IS NOT NULL AND NEW.message_id IS NOT OLD.message_id
BEGIN
  SELECT RAISE(ABORT, 'attachment binding is immutable');
END;

CREATE TRIGGER IF NOT EXISTS attachment_no_delete
BEFORE DELETE ON attachment
BEGIN
  SELECT RAISE(ABORT, 'attachment is append-only');
END;

CREATE TABLE IF NOT EXISTS target_read (
  member_id     TEXT NOT NULL REFERENCES member(id),
  target_id     TEXT NOT NULL REFERENCES target(id),
  last_read_seq INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (member_id, target_id)
);

-- 有界响应预算（策略 B，spec #1 §5）的正本：每 **target** 一行（ADR-0004 的泛化，机制逐字不变）。
-- ``epoch_seq`` = 人类世代（该 target 最近一次**开世代动作**所在消息的 seq；0 = 还没有开世代动作）；
-- ``used`` = 该世代内**已投递**的 agent→agent 跳数。
-- 落库而不是放进程内存：「已用/剩余」必须在后端重启后仍是同一条事实（验收项明写）。
-- **每 target 一份**：「一个频道烧完把另一个频道锁死」因此不可能发生（跨 target 不锁死）。
-- ``applied_limit``（#53）= 该世代**生效上界**的快照：开世代动作落库时写一次，之后只读。
-- ``NULL`` = 该世代用**内置默认值**（``BUDGET_LIMIT``），非空 = 该世代用**频道覆盖**
-- （``channel_budget_limit`` 那一行）。它同时是三条判据的落点：①「已保存的新值从下一个人类
-- 世代生效」（快照不随配置漂移，所以当前世代不会被回溯改写）；②「上界跨后端重启保持」；
-- ③「只读面展示的 effective limit 与后端压制行为是同一份事实」（两处都读这个快照）。
CREATE TABLE IF NOT EXISTS target_budget (
  target_id TEXT PRIMARY KEY REFERENCES target(id),
  epoch_seq INTEGER NOT NULL DEFAULT 0,
  used      INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
  applied_limit INTEGER CHECK (applied_limit IN (3, 5, 10))
);

-- 频道回应预算的**配置**落点（#53 / 规格 #31 §2 的 L2「库内逐频道配置」）。
-- 一行 = 该频道已保存的上界；**没有行 = 回落到内置默认值**（``BUDGET_LIMIT``）。
-- 与 ``target_budget`` 的分工是**配置 vs 用量**：这里只有配置，用量正本永远是 ``target_budget.used``
-- （人类面不能写用量，ADR-0007）。``budget_limit`` 的取值集合就是裁定过的固定档位——非法档位在
-- 建行那一刻被 CHECK 挡住，于是「回应预算始终是合法档位」是结构性的，不是约定。
-- 生效时机：保存这一行**不动**任何世代的 ``applied_limit`` / ``used``；下一个人类世代开世代时
-- 才把它快照进 ``target_budget.applied_limit``（见 ``Database.set_channel_budget_limit``）。
-- 按 ``channel_id`` 而不是 ``target_id`` 建键：配置的作用域是**频道**（ADR-0004 规定 channel 型
-- target 复用 ``channel.id``，因此两个 id 同值，但这一列声明的是「它只能挂在一个真实频道上」）。
CREATE TABLE IF NOT EXISTS channel_budget_limit (
  channel_id   TEXT PRIMARY KEY REFERENCES channel(id),
  budget_limit INTEGER NOT NULL CHECK (budget_limit IN (3, 5, 10)),
  updated_at   TEXT NOT NULL
);

-- ─── team 运行记录（ADR-0018 / ADR-0022 / 规格 §4.2） ───────────────────────
-- team 模式经 ``task.channel_id -> channel.mode`` 解析；``task`` 表**不新增 mode 列**（ADR-0022 §一）。
-- 因此下面两张表的 ``target_id`` 既可以是 channel 型，也可以是该频道下的 task 型；
-- ``team_assignment.task_id`` 在 task target 上非空。它们**不是**消息、不进未读/搜索/预算。
CREATE TABLE IF NOT EXISTS team_run (
  id                TEXT PRIMARY KEY,
  target_id         TEXT NOT NULL REFERENCES target(id),
  leader_member_id  TEXT NOT NULL REFERENCES member(id),
  base_seq          INTEGER NOT NULL,
  status            TEXT NOT NULL CHECK (status IN
                      ('running', 'completed', 'failed', 'interrupted', 'invalid-leader')),
  started_at        TEXT NOT NULL,
  finished_at       TEXT
);

CREATE TABLE IF NOT EXISTS team_assignment (
  id                TEXT PRIMARY KEY,
  team_run_id       TEXT NOT NULL REFERENCES team_run(id),
  target_id         TEXT NOT NULL REFERENCES target(id),
  task_id           TEXT REFERENCES task(id),
  leader_member_id  TEXT NOT NULL REFERENCES member(id),
  member_id         TEXT NOT NULL REFERENCES member(id),
  tool_call_id      TEXT NOT NULL,
  prompt            TEXT NOT NULL,
  state             TEXT NOT NULL CHECK (state IN
                      ('started', 'succeeded', 'failed', 'interrupted', 'budget-exhausted')),
  jump_consumed     INTEGER NOT NULL DEFAULT 0 CHECK (jump_consumed IN (0, 1)),
  reply_excerpt     TEXT NOT NULL DEFAULT '',
  error             TEXT NOT NULL DEFAULT '',
  started_at        TEXT NOT NULL,
  finished_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_team_assignment_target
ON team_assignment(target_id, started_at);
CREATE INDEX IF NOT EXISTS idx_team_assignment_team_run ON team_assignment(team_run_id);

-- ─── Plan / SOP 运行记录（ADR-0023 / 规格 §4.2） ────────────────────────────
-- Plan 与 SOP 各有独立状态机，**不是**任务五态：它们不进看板、未读、搜索、唤醒、预算，
-- 也不自动改 ``task`` 行。Plan 是工作态（可更新、可按 run 级联清理），SOP 的历史由 ``sop_run`` 承载。
CREATE TABLE IF NOT EXISTS plan_run (
  id           TEXT PRIMARY KEY,
  target_id    TEXT NOT NULL REFERENCES target(id),
  member_id    TEXT NOT NULL REFERENCES member(id),
  base_seq     INTEGER NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
  started_at   TEXT NOT NULL,
  finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS plan_task (
  id           TEXT PRIMARY KEY,
  plan_run_id  TEXT NOT NULL REFERENCES plan_run(id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL,
  subject      TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  state        TEXT NOT NULL CHECK (state IN ('pending', 'in_progress', 'completed')),
  owner        TEXT,
  blocks       TEXT NOT NULL DEFAULT '[]',
  blocked_by   TEXT NOT NULL DEFAULT '[]',
  metadata     TEXT NOT NULL DEFAULT '{}',
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  deleted_at   TEXT,
  UNIQUE (plan_run_id, seq)
);

CREATE TABLE IF NOT EXISTS sop_definition (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  -- JSON：executor/verifier member ids、subject、description、max_attempts（ADR-0023 §三）。
  steps        TEXT NOT NULL,
  enabled      INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sop_run (
  id                 TEXT PRIMARY KEY,
  sop_definition_id  TEXT NOT NULL REFERENCES sop_definition(id),
  target_id          TEXT NOT NULL REFERENCES target(id),
  leader_member_id   TEXT NOT NULL REFERENCES member(id),
  -- SOPRunState.model_dump_json() 的原样快照。
  state              TEXT NOT NULL,
  phase              TEXT NOT NULL CHECK (phase IN
                       ('pending', 'running', 'awaiting', 'completed', 'failed')),
  started_at         TEXT NOT NULL,
  finished_at        TEXT,
  updated_at         TEXT NOT NULL
);

-- Agent 的模型配置：模型是可配置数据而不是代码常量（spec #1 §2）。
-- role 是 Agent 的配置数据（用户输入原样落库），不是代码常量、也不折进系统提示。
-- ``auto_claim``（#65 / ADR-0010）是「频道内新建任务后，编排要不要替它接手这张任务」的配置开关。
-- 从 #195 / ADR-0029 起**缺省开启**：新建 Agent 不写这一列就拿到默认 1；它只在**自主频道**
-- （``channel.mode = autonomous``）里生效，team 频道的认领裁决在读路径上直接早退。写入路径只有
-- ``PATCH /api/agents/{id}``（可逐 Agent 关闭）。
CREATE TABLE IF NOT EXISTS agent_config (
  member_id     TEXT PRIMARY KEY REFERENCES member(id),
  role          TEXT NOT NULL DEFAULT '',
  model_id      TEXT NOT NULL DEFAULT '',
  system_prompt TEXT NOT NULL DEFAULT '',
  enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  auto_claim    INTEGER NOT NULL DEFAULT 1 CHECK (auto_claim IN (0, 1)),
  -- workspace 配置（ADR-0021 §一 / §二 / §四）：后端与权限档是 Agent 级覆盖，``NULL`` = 跟随频道
  -- 默认（旧行于是逐字保持「未配置」的语义）；``allow_local_shell`` 缺省关闭——本地 Bash 默认不挂，
  -- 显式开启后每次调用仍走审批；``docker_image`` 缺省即 AgentScope 的内置镜像。
  workspace_backend   TEXT CHECK (workspace_backend IN ('local', 'docker')),
  permission_mode     TEXT CHECK (permission_mode IN ('read-only', 'ask-writes', 'trusted-writes')),
  allow_local_shell   INTEGER NOT NULL DEFAULT 0 CHECK (allow_local_shell IN (0, 1)),
  docker_image        TEXT,
  -- 联网档的 Agent 级覆盖（#184 / ADR-0026 §二）：``NULL`` = 跟随频道（旧行逐字保持既有行为），
  -- 非空时覆盖频道那一档。取值与频道同一条白名单，库级 CHECK 是第二道闸。
  web_access          TEXT CHECK (web_access IN ('off', 'search', 'search+fetch')),
  context_window_messages INTEGER DEFAULT 40 CHECK (context_window_messages > 0),
  context_trigger_ratio REAL DEFAULT 0.7 CHECK (context_trigger_ratio > 0 AND context_trigger_ratio <= 0.9),
  react_max_iters INTEGER DEFAULT 30 CHECK (react_max_iters > 0),
  memory_mode TEXT DEFAULT 'off' CHECK (memory_mode IN ('off', 'agentic')),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

-- 工作区设置（`CONTEXT.md` **工作区设置**）：**单行表**（``id = 1``），列就是明确列出的档位
-- （语言偏好 / 消息密度 / 主题三档）。它是**配置类**取值（人类面经 ``GET`` / ``PATCH /api/settings``
-- 读写），与预算表的边界是**写什么**：人类面可以写配置，但**不得**写用量类事实
-- （``target_budget.used`` 仍只由后端推进，ADR-0007）。
-- 刻意不做成通用 key-value 表：那会让「只有这几档」这条约束失去落点（词条的 `_Avoid_` 明写）。
-- 改设置**不落消息、不唤醒、不消耗预算**——它不是对话事实。
CREATE TABLE IF NOT EXISTS workspace_settings (
  id              INTEGER PRIMARY KEY CHECK (id = 1),
  locale          TEXT NOT NULL DEFAULT 'zh-CN' CHECK (locale IN ('zh-CN', 'en')),
  message_density TEXT NOT NULL DEFAULT 'comfortable'
                  CHECK (message_density IN ('comfortable', 'compact')),
  -- 主题三档（#71）：显式浅色 / 显式深色 / 跟随系统外观。缺省是 ``system``
  -- （空库的取值就是「跟随系统」）；取值层在 alibot-design-system/。
  theme           TEXT NOT NULL DEFAULT 'system'
                  CHECK (theme IN ('light', 'dark', 'system')),
  updated_at      TEXT NOT NULL
);

-- 提醒（`CONTEXT.md` **提醒**）：一条**时间 + 文本**备忘，**可归属**一个频道。
-- **库内对象**（ADR-0012）：后端持有它是唯一正本，前端不持 localStorage 副本——既有的那条边界
-- 「业务状态的唯一正本是后端」（`CONTEXT.md` **工作台**）在提醒上没有理由例外。
-- 与消息面的边界（ADR-0012 / D18）：提醒**不是**对话事实。增删提醒**不落消息、不唤醒、
-- 不消耗预算**；提醒**不新增 target**、不计未读、不参与唤醒——它与频道的关系是**归属**，
-- 不是 target 作用域（因此这里没有也不能有 `target_id`）。
-- ``channel_id`` **可空**：空 = 这条提醒不归属任何频道（空工作台 / DM 视图下也设得成提醒）。
-- ``time`` 的形状由 :data:`REMINDER_TIME_PATTERN` 在写路径上校验（24 小时的 `YYYY-MM-DD HH:MM`，
-- **一次性**到期时刻；按本机本地时区解释，见 ADR-0017 §二）。
-- ``fired_at`` **可空**：``NULL`` = 还没到点；非空 = 已投递（ISO-8601 **UTC** 串、带 ``Z`` 后缀，与
-- ``created_at`` 同一族口径）。它是**唯一**的「已投递」标记，同时承担「至多投递一次」与「至多补投
-- 一次」（ADR-0017 §三）。**不落**第二个状态列（``state`` 是读路径的派生值，不是列）。
CREATE TABLE IF NOT EXISTS reminder (
  id          TEXT PRIMARY KEY,
  text        TEXT NOT NULL CHECK (length(trim(text)) > 0),
  time        TEXT NOT NULL,
  channel_id  TEXT REFERENCES channel(id),
  created_at  TEXT NOT NULL,
  fired_at    TEXT
);

-- 远端数据流告知的一次性确认（spec #1 §8）：按模型提供商记一次，与成员无关。
-- 这里**只**存「何时确认过」，不存任何凭证。
CREATE TABLE IF NOT EXISTS remote_consent (
  provider        TEXT PRIMARY KEY,
  acknowledged_at TEXT NOT NULL
);

-- 远端数据面的**统一**一次性确认（#139 / ADR-0021 §三）：模型 / 远端 MCP / Docker registry / 联网
-- 四类数据面各按 scope 记一次。它是**新**的读路径正本；旧 ``remote_consent`` 只作为兼容面保留，
-- 迁移把它已有的 ``provider`` 行复制成 ``('model', provider, acknowledged_at)``（``16 → 17``）。
-- 与旧表一样**只**存「何时确认过」，不存凭证、不存连接参数。
-- ``web`` 档（#184 / ADR-0026 §四）与另外三类同一纪律：按**提供方**记一次（host 级粒度归
-- ``permission_rule``），加档需要一次 schema bump + 重建表（SQLite 改表级 CHECK 只能重建）。
CREATE TABLE IF NOT EXISTS remote_consent_scope (
  scope_kind       TEXT NOT NULL CHECK (scope_kind IN ('model', 'mcp', 'docker', 'web')),
  scope_id         TEXT NOT NULL,
  acknowledged_at  TEXT NOT NULL,
  PRIMARY KEY (scope_kind, scope_id)
);

-- 联网 provider 的凭据（#199 / ADR-0026 §四修订）：**一家一行**，`provider` 是主键。
-- ``api_key`` 明文入库（部署层保证库文件权限 0600）；任何 HTTP 响应 / 日志 / 事件载荷只回
-- 掩码后的形态（``web.mask_credential``），绝不回吐完整取值。``is_default = 1`` 至多一行
-- （部分唯一索引），它只决定「没有显式环境变量时先用哪一家」，不是第二份 provider 配置。
-- ``created_at`` 是首次保存时间；``updated_at`` 在换 key / 换默认时推进。
CREATE TABLE IF NOT EXISTS web_provider_credential (
  provider   TEXT PRIMARY KEY
             CHECK (provider IN ('brave', 'tavily', 'perplexity', 'jina', 'exa')),
  api_key    TEXT NOT NULL CHECK (length(trim(api_key)) > 0),
  is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_web_provider_credential_default
ON web_provider_credential(is_default) WHERE is_default = 1;

-- ─── 权限规则 / 审批 / MCP / Skill（#139 / 规格 §4.3 / ADR-0021 §三 / §四） ──
-- 权限规则是**配置**（不是消息、不是任务）：``scope_kind`` + ``scope_id`` 指向频道或 Agent，
-- ``behavior`` 只允许三档（allow / deny / ask）——不暴露 BYPASS / DONT_ASK（ADR-0021 §四）。
-- 规则按 ``tool_name`` + ``pattern`` 匹配；空 pattern = 该工具的全部调用。
CREATE TABLE IF NOT EXISTS permission_rule (
  id          TEXT PRIMARY KEY,
  scope_kind  TEXT NOT NULL CHECK (scope_kind IN ('channel', 'agent')),
  scope_id    TEXT NOT NULL,
  tool_name   TEXT NOT NULL,
  pattern     TEXT NOT NULL DEFAULT '',
  behavior    TEXT NOT NULL CHECK (behavior IN ('allow', 'deny', 'ask')),
  created_at  TEXT NOT NULL
);

-- HITL 审批（#139 / ADR-0021 §四）：一条待决的 ``RequireUserConfirmEvent``。``tool_calls`` 是
-- JSON（工具调用块 + suggestions），``decisions`` 是 JSON 数组；**不存凭证**。``state`` 的
-- ``expired`` 是进程重启时对残留 pending 的处置；跨重启恢复不在首版（D19）。
CREATE TABLE IF NOT EXISTS approval_request (
  reply_id     TEXT PRIMARY KEY,
  target_id    TEXT NOT NULL REFERENCES target(id),
  member_id    TEXT NOT NULL REFERENCES member(id),
  team_run_id  TEXT REFERENCES team_run(id),
  tool_calls   TEXT NOT NULL,
  state        TEXT NOT NULL CHECK (state IN ('pending', 'approved', 'denied', 'expired')),
  decisions    TEXT NOT NULL DEFAULT '[]',
  requested_at TEXT NOT NULL,
  resolved_at  TEXT
);

-- MCP 声明（#139 / ADR-0021 §三）：只存 transport / command / args / url 与 ``env_names``
-- （变量**名**，值只从进程环境读——库里不存密钥）。同名 server 在同一个 scope 内唯一。
CREATE TABLE IF NOT EXISTS mcp_server (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  transport    TEXT NOT NULL CHECK (transport IN ('stdio', 'http', 'sse')),
  command      TEXT NOT NULL DEFAULT '',
  args         TEXT NOT NULL DEFAULT '[]',
  url          TEXT NOT NULL DEFAULT '',
  env_names    TEXT NOT NULL DEFAULT '[]',
  enabled      INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  scope_kind   TEXT NOT NULL CHECK (scope_kind IN ('channel', 'agent')),
  scope_id     TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  UNIQUE (scope_kind, scope_id, name)
);

-- Skill 元数据（#139 / ADR-0021 §三）：字节在 ``workspace_root/.alibot/skills/<member_id>/``，
-- 库里只存来源路径与内容 hash；同名 skill 在同一个 scope 内唯一。安装只接受本机来源。
CREATE TABLE IF NOT EXISTS skill (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  source_path  TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  enabled      INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  scope_kind   TEXT NOT NULL CHECK (scope_kind IN ('channel', 'agent')),
  scope_id     TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  UNIQUE (scope_kind, scope_id, name)
);

-- 轮次结果（spec #1 §5）：一轮的处理结果。append-only，与消息同级的取证面。
-- ``consumed_budget`` 是**行级**标记，不是跳的计数：它说的是「这一轮是由**一次已投递并被计一次的
-- agent→agent 跳**送来的」。**一次跳投递 N 个成员就有 N 条为 1 的行**，而跳数只 +1，因此
-- **它不可按行求和**。该人类世代的**跳数唯一正本**是 ``target_budget.used``（见上面的表注释）。
CREATE TABLE IF NOT EXISTS round_outcome (
  id              TEXT PRIMARY KEY,
  member_id       TEXT NOT NULL REFERENCES member(id),
  target_id       TEXT NOT NULL REFERENCES target(id),
  base_seq        INTEGER NOT NULL,
  status          TEXT NOT NULL CHECK (status IN
                    ('replied', 'ignored', 'failed', 'merged', 'budget-exhausted', 'silent')),
  reason          TEXT NOT NULL DEFAULT '',
  finished_reason TEXT,
  started_at      TEXT NOT NULL,
  finished_at     TEXT NOT NULL,
  consumed_budget INTEGER NOT NULL DEFAULT 0 CHECK (consumed_budget IN (0, 1)),
  merged          INTEGER NOT NULL DEFAULT 0 CHECK (merged IN (0, 1)),
  reply_seq       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_round_outcome_target ON round_outcome(target_id, finished_at);
CREATE INDEX IF NOT EXISTS idx_round_outcome_member ON round_outcome(member_id, finished_at);

CREATE INDEX IF NOT EXISTS idx_target_member_member ON target_member(member_id);

-- append-only 兜底：任何更新/删除消息的路径（含未来的代码路径与人工 SQL）都被拒绝。
CREATE TRIGGER IF NOT EXISTS message_no_update
BEFORE UPDATE ON message
BEGIN
  SELECT RAISE(ABORT, 'message is append-only');
END;

CREATE TRIGGER IF NOT EXISTS message_no_delete
BEFORE DELETE ON message
BEGIN
  SELECT RAISE(ABORT, 'message is append-only');
END;

-- 轮次结果同样 append-only：它是「当时为什么没说话」的事实记录，不接受事后改写。
CREATE TRIGGER IF NOT EXISTS round_outcome_no_update
BEFORE UPDATE ON round_outcome
BEGIN
  SELECT RAISE(ABORT, 'round_outcome is append-only');
END;

CREATE TRIGGER IF NOT EXISTS round_outcome_no_delete
BEFORE DELETE ON round_outcome
BEGIN
  SELECT RAISE(ABORT, 'round_outcome is append-only');
END;
"""

# 频道读取的唯一一条 SQL（无拼接，值全部经命名占位符绑定）：
# - ``:channel_id IS NULL`` 时返回全部频道（列表）；给值时返回单条。
# - 未读计数口径**只在这里写一次**（规格 §2 与 #4 的人类裁定）：已读位置之后、
#   kind ∈ {human, agent}、**且作者不是本人**的顶层消息；不计 system。
#   unread_count（列表/单条）与 unread_total（workspace 汇总）因此共享同一个形状。
# - 作用域键是 **target_id**，不是 channel_id：channel 型 target 复用 ``channel.id``（影子行），
#   所以 ``m.target_id = c.id`` 这条既有事实逐字不变，但查询本身是 **target 作用域**——
#   **不按 kind 分支**（ADR-0004 决策 8）。
# Agent 行的列清单（member 行 + agent_config 配置列）。三处查询共用：
# 加一个配置列时只改这里，不会只改到其中一两处。
# 三处 FROM/WHERE 各自保留（起点与过滤条件本就不同）—— #3 的双轴 review 已为这个形态裁决过。
AGENT_COLUMNS = (
    "m.*, c.role, c.model_id, c.system_prompt, c.enabled, c.auto_claim, "
    "c.workspace_backend, c.permission_mode, c.allow_local_shell, c.docker_image, c.web_access, "
    "c.context_window_messages, c.context_trigger_ratio, c.react_max_iters, c.memory_mode, "
    "c.updated_at AS config_updated_at"
)

# 由常量 ``AGENT_COLUMNS`` 拼出的 Agent 选取语句（**在模块级拼好**，不把拼接写进
# ``execute`` 的实参：查询文本对静态检查是可见常量，也不是注入面）。
AGENT_SELECT_SQL = "SELECT " + AGENT_COLUMNS + " "

# Agent 概览读数的三条聚合语句（同样**在模块级拼好**，同上一条体例）。
# 每条的成员过滤是**同一个形状** ``(:member_id IS NULL OR …)``：``agent_stats()`` 的「全部成员」
# 与「某一个成员」两条路径共用一条语句，因此不存在第二份只差一个 ``WHERE`` 的拷贝；
# 参数一律走命名占位符（传 ``{"member_id": …}``），没有标识符注入面。
AGENT_STATS_CHANNEL_SQL = (
    "SELECT tm.member_id AS member_id, COUNT(*) AS n FROM target_member tm "
    "JOIN target t ON t.id = tm.target_id "
    "WHERE t.kind = 'channel' AND (:member_id IS NULL OR tm.member_id = :member_id) "
    "GROUP BY tm.member_id"
)
AGENT_STATS_ROUND_SQL = (
    "SELECT member_id, COUNT(*) AS n, "
    "SUM(CASE WHEN status = 'replied' THEN 1 ELSE 0 END) AS replied, "
    "SUM(CASE WHEN status IN ('replied', 'ignored', 'failed') THEN 1 ELSE 0 END) AS verdicts "
    "FROM round_outcome WHERE (:member_id IS NULL OR member_id = :member_id) "
    "GROUP BY member_id"
)
AGENT_STATS_TASK_SQL = (
    "SELECT assignee_member_id AS member_id, COUNT(*) AS n FROM task "
    "WHERE status = 'done' AND assignee_member_id IS NOT NULL "
    "AND (:member_id IS NULL OR assignee_member_id = :member_id) "
    "GROUP BY assignee_member_id"
)

# 未读的**唯一一个 WHERE 形状**（ADR-0004 决策 7）：已读位置之后、``kind ∈ {human, agent}``、
# 作者非本人、**只算顶层**。三种 kind 用同一个形状，**不按 kind 分支**。
# 它是**单一字面量落点**：下面三个消费点（频道列表、工作台汇总、单 target 计数）都把它原样拼进
# 自己的 FROM 里，于是不存在第二个形状。别名统一用 ``m``（三个消费点里它是各自子查询的局部名字，
# 互不冲突），因此这里只需一条常量，而不是按别名派生多份。
UNREAD_WHERE_SQL = """
                     AND m.seq > COALESCE(
                           (SELECT r.last_read_seq FROM target_read r
                             WHERE r.member_id = :member_id AND r.target_id = m.target_id), 0)
                     AND m.kind IN ('human', 'agent')
                     AND m.author_member_id != :member_id
"""

CHANNEL_SELECT_SQL = (
    """
    SELECT c.*,
           COALESCE((SELECT MAX(seq) FROM message m WHERE m.target_id = c.id), 0) AS last_seq,
           COALESCE(r.last_read_seq, 0) AS last_read_seq,
           COALESCE((SELECT COUNT(*) FROM message m
                      WHERE m.target_id = c.id
"""
    + UNREAD_WHERE_SQL
    + """
                    ), 0) AS unread_count
      FROM channel c
      LEFT JOIN target_read r ON r.target_id = c.id AND r.member_id = :member_id
     WHERE (:channel_id IS NULL OR c.id = :channel_id)
     ORDER BY c.created_at, c.id
"""
)

# 工作台快照的 ``unread_total`` = **channel 型 + dm 型**之和（决策 G5）：**task 型不进它**。
# 它用的是上面**同一条 WHERE 形状**（同一个 ``UNREAD_WHERE_SQL``），只是在 target 上按 kind 过滤后
# 汇总 ——「只有一个 SQL 形状」因此是结构上的事实，而不是两句彼此长得像的 SQL。
# 取值域由上面的 ``UNREAD_TOTAL_KINDS`` **拼出来**（不是另写一份字面量）：G5 的「channel + dm」
# 因此只有一个落点，改一处不会与另一处漂移。
_UNREAD_TOTAL_KIND_LIST = ", ".join(f"'{kind}'" for kind in UNREAD_TOTAL_KINDS)
UNREAD_TOTAL_SQL = (
    "SELECT COALESCE(SUM((SELECT COUNT(*) FROM message m\n"
    "                      WHERE m.target_id = t.id\n" + UNREAD_WHERE_SQL + "                    )), 0)\n"
    "  FROM target t\n"
    " WHERE t.kind IN (" + _UNREAD_TOTAL_KIND_LIST + ")\n"
)

# 单个 target 的未读条数：**同样的 WHERE 形状**（别名同为 m），区别只在于作用域键直接由参数给出。
UNREAD_COUNT_SQL = "SELECT COUNT(*) FROM message m\n WHERE m.target_id = :target_id\n" + UNREAD_WHERE_SQL

DM_SELECT_SQL = (
    "SELECT t.id, t.created_at, 'dm' AS kind, "
    "       p.id AS peer_id, p.kind AS peer_kind, p.display_name AS peer_display_name, "
    "       p.avatar_slot AS peer_avatar_slot, p.created_at AS peer_created_at, "
    "       p.deleted_at AS peer_deleted_at, COALESCE(MAX(m.seq), 0) AS last_seq, "
    "       COALESCE(r.last_read_seq, 0) AS last_read_seq, "
    "       COALESCE((SELECT COUNT(*) FROM message unread WHERE unread.target_id = t.id\n"
    + UNREAD_WHERE_SQL.replace("m.", "unread.")
    + "       ), 0) AS unread_count "
    "  FROM target t "
    "  JOIN target_member owner_members ON owner_members.target_id = t.id "
    "                              AND owner_members.member_id = :member_id "
    "  JOIN member p ON p.id = t.dm_peer_member_id "
    "  LEFT JOIN message m ON m.target_id = t.id "
    "  LEFT JOIN target_read r ON r.target_id = t.id AND r.member_id = :member_id "
    " WHERE t.kind = 'dm' AND (:dm_id IS NULL OR t.id = :dm_id) "
    " GROUP BY t.id, t.created_at, p.id, p.kind, p.display_name, p.avatar_slot, "
    "          p.created_at, p.deleted_at, r.last_read_seq "
    " ORDER BY t.created_at, t.id"
)

# 提醒的**唯一一条选取形状**：提醒行 + 归属频道的可读名。
# 三处共用（列表、新增后读回、删除前读回）——加一列时只改这里，不会只改到其中一两处。
# 归属是**可空**的（空 = 不归属任何频道），因此是 ``LEFT JOIN``：写成 ``JOIN`` 会把
# 「不归属」的提醒整条从列表里丢掉。ORDER BY 与 WHERE 由调用点各自拼在后面（起点不同）。
REMINDER_SELECT_SQL = (
    "SELECT r.*, c.name AS channel_name FROM reminder r LEFT JOIN channel c ON c.id = r.channel_id "
)

# 工作台搜索的唯一判定：SQLite CASEFOLD + LIKE 子串匹配，前端只消费后端高亮区间。
#
# ``LIKE`` 的模式由 ``_search_like_pattern`` 绑定为 ``%关键词%``，并转义用户输入里的通配符，
# 所以它仍是严格的子串匹配。全工作区版本由 target 驱动，经 message 上既有的
# ``UNIQUE(target_id, seq)`` 索引读取各 target 消息；不新增 schema / FTS5。
#
# 命中来源有**三类**（ADR-0013）：消息（正文）、任务（标题）、Agent（显示名与角色）。
# ``facet`` 是**后端的过滤参数**（不是前端过滤）：选中哪一类就只查哪一条 SQL，
# 三类共用上面这一条判定纪律（大小写不敏感的子串匹配），因此不存在第二个判定落点。
SEARCH_HIT_SELECT_SQL = """
    SELECT m.*, a.kind AS author_kind, a.display_name AS author_name,
           a.avatar_slot AS author_avatar_slot, a.deleted_at AS author_deleted_at,
           t.kind AS target_kind,
           CASE t.kind
             WHEN 'channel' THEN c.name
             WHEN 'dm' THEN peer.display_name
             WHEN 'task' THEN task.title
           END AS target_display_name
"""
SEARCH_HIT_FROM_SQL = """
      FROM target t
      CROSS JOIN message m
      JOIN member a ON a.id = m.author_member_id
 LEFT JOIN channel c ON c.id = t.id AND t.kind = 'channel'
 LEFT JOIN member peer ON peer.id = t.dm_peer_member_id AND t.kind = 'dm'
 LEFT JOIN task ON task.target_id = t.id AND t.kind = 'task'
"""
SEARCH_ALL_HITS_SQL = (
    SEARCH_HIT_SELECT_SQL
    + SEARCH_HIT_FROM_SQL
    + """
     WHERE m.target_id = t.id
       AND m.kind != 'system'
       AND CASEFOLD(m.body) LIKE :pattern ESCAPE '\\'
     LIMIT :limit
"""
)
SEARCH_TARGET_HITS_SQL = (
    SEARCH_HIT_SELECT_SQL
    + SEARCH_HIT_FROM_SQL
    + """
     WHERE m.target_id = :target_id
       AND m.target_id = t.id
       AND m.kind != 'system'
       AND CASEFOLD(m.body) LIKE :pattern ESCAPE '\\'
     LIMIT :limit
"""
)

# 任务命中：命中本体是**任务标题**；``channel`` 是它的来源（``.r-top`` 里显示的那个「#频道」）。
SEARCH_TASK_HITS_SQL = """
    SELECT t.id AS id, t.target_id AS target_id, t.title AS title, t.status AS status,
           c.id AS channel_id, c.name AS channel_name
      FROM task t
      JOIN channel c ON c.id = t.channel_id
     WHERE CASEFOLD(t.title) LIKE :pattern ESCAPE '\\'
     ORDER BY t.created_at, t.id
     LIMIT :limit
"""

# Agent 命中：命中本体是**显示名与角色**（两个字段各自是子串匹配的候选）。
# soft-delete 的成员不是可呈现的 Agent，因此排除；没有 ``agent_config`` 行的成员仍可被显示名命中，
# 因此是 LEFT JOIN（与 ``agent_members()`` 同一口径，两处不能分叉）。
SEARCH_AGENT_HITS_SQL = """
    SELECT m.id AS id, m.display_name AS display_name, m.avatar_slot AS avatar_slot,
           COALESCE(c.role, '') AS role, COALESCE(c.model_id, '') AS model_id
      FROM member m
 LEFT JOIN agent_config c ON c.member_id = m.id
     WHERE m.kind = 'agent'
       AND m.deleted_at IS NULL
       AND (CASEFOLD(m.display_name) LIKE :pattern ESCAPE '\\'
            OR CASEFOLD(COALESCE(c.role, '')) LIKE :pattern ESCAPE '\\')
     ORDER BY m.display_name, m.id
     LIMIT :limit
"""

# 附件（ADR-0014）：一条消息**已绑定**的附件（草稿 —— ``message_id IS NULL`` —— 不在此列）。
ATTACHMENTS_BY_MESSAGE_SQL = "SELECT * FROM attachment WHERE message_id = ? ORDER BY created_at, id"

# 一个 target 里已绑定的附件（按消息归组在 Python 侧做）：读消息列表时一次补齐，避免 N+1。
ATTACHMENTS_BY_TARGET_SQL = """
    SELECT att.* FROM attachment att JOIN message m ON m.id = att.message_id
     WHERE m.target_id = ? AND m.seq > ?
     ORDER BY att.created_at, att.id
"""

# 多条消息的附件（消息命中一次补齐用）。``IN`` 的取值域由 ``json_each`` 从**一个**绑定参数展开：
# 本仓的既有纪律是「查询文本对静态检查是可见常量，拼接的不是外部输入」，这条常量因此也不需要
# 在调用点拼占位符——连「几个占位符」这种计数拼接都省掉了。
ATTACHMENTS_BY_MESSAGES_SQL = (
    "SELECT * FROM attachment "
    "WHERE message_id IN (SELECT value FROM json_each(:message_ids)) "
    "ORDER BY created_at, id"
)

# 搜索的四档 facet（原型 `.facet-row` 的取值与顺序）。它同时是 HTTP 查询参数的类型，
# 因此「哪几档」只有这一处定义：FastAPI 的校验与库层的分支读的是同一个 Literal。
SearchFacet = Literal["all", "message", "task", "agent"]


def _sqlite_casefold(value: str | None) -> str | None:
    """为 SQLite ``LIKE`` 提供与 Python 正文区间判定一致的 Unicode 大小写折叠。"""
    return value.casefold() if value is not None else None


# 把 ``schema_meta.schema_version`` 钉到本代码版本。``_initialize`` 与迁移引擎共用**同一份**
# 语句：版本号只有一个落点，初始化与迁移不会各写各的 UPSERT。
STAMP_SCHEMA_VERSION_SQL = (
    "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
)

# 工作区设置的单行缺省行（``id = 1``）。取值来自本模块的 Python 常量（语言 / 密度 / 主题），
# 不另抄字面量：``_initialize`` 与迁移引擎共用**同一份**语句与同一组绑定值。
INSERT_WORKSPACE_SETTINGS_SQL = (
    "INSERT INTO workspace_settings(id, locale, message_density, theme, updated_at) "
    "VALUES (1, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING"
)


class SchemaVersionMismatch(Exception):
    """库里的 schema 版本与本代码需要的版本不一致：**拒绝**，且不改动旧库一个字节。

    **进程级**的拒绝，不是一个 HTTP 领域错误：它由版本闸门（:meth:`Database._assert_schema_compatible`）
    在任何 schema 变更之前抛出。三条入口——无旗标启动、``--migrate``、``--repair-targets``——都经这
    同一道闸门，于是同一个库在三条路径上得到**同一个答复与同一个退出码**（``__main__`` 的
    ``DATA_NOT_ACCEPTED_EXIT_CODE``）。HTTP 层因此不会把它折成 500——启动路径根本不会开始监听。

    ``found_version`` 为 ``None`` 表示「版本号缺失」（没有 ``schema_meta`` 表，或没有
    ``schema_version`` 那一行）。缺失时只有库里**已有业务数据**才会抛这个错：真的空库照常初始化
    （否则空目录首次启动就起不来了）。
    """

    def __init__(self, db_path: Path, found_version: str | None, expected_version: int):
        self.db_path = Path(db_path)
        self.found_version = found_version
        self.expected_version = expected_version
        super().__init__(self._render())

    def _render(self) -> str:
        found = (
            str(self.found_version)
            if self.found_version is not None
            else "缺失（schema_meta 里没有 schema_version 这一行）"
        )
        return (
            "拒绝：数据库的 schema 版本与本代码不匹配。\n"
            f"  库文件：{self.db_path}\n"
            f"  库里的版本：{found}\n"
            f"  本代码需要的版本：{self.expected_version}\n"
            f"  本版支持的源版本范围：{MIN_SUPPORTED_SOURCE_VERSION} .. {self.expected_version}"
            "（可用迁移入口 python -m alibot --migrate 从 3 逐版推进到本版；"
            "启动路径与 --repair-targets 仍按本规则拒绝）。\n"
            "  可用这条命令推进：\n"
            f"    python -m alibot --migrate --data-dir {self.db_path.parent}\n"
            "  在动手之前请先备份整个数据目录（备份 = 复制该数据目录，附件字节也在其中）；\n"
            "  迁移前也会自动整目录备份，备份失败即拒绝迁移。\n"
        )


class DomainError(Exception):
    """可映射为 HTTP 错误响应的领域错误。"""

    def __init__(self, code: str, message: str, status: int = 400, fields: dict[str, str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.fields = fields or {}

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
        if self.fields:
            body["error"]["fields"] = self.fields
        return body


class NotFound(DomainError):
    """404 一族：``message`` 必须显式给出（调用点按**语言偏好**渲染，见 ``Database._copy``）。

    这里**故意没有缺省文案**：缺省值要么是钉死源语言的一句话（切到 English 后就露出中文），要么
    是一条不可达的死词条。删掉缺省，让「404 的正文来自词条表」成为签名级约束——漏传立即报错，
    而不是悄悄说错语言。
    """

    def __init__(self, message: str):
        super().__init__("not_found", message, status=404)


def utc_now() -> str:
    """ISO-8601 毫秒精度时间戳（UTC，带 Z 后缀）。"""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


DataDirSource = Literal["explicit", "new-default", "legacy-compat", "created"]

# 启动输出里给「这个数据目录是从哪来的」用的可读标签（规格 D2 / D12）。
DATA_DIR_SOURCE_LABELS: dict[DataDirSource, str] = {
    "explicit": "显式覆盖",
    "new-default": "新默认",
    "legacy-compat": "旧位置兼容",
    "created": "新建",
}


@dataclass(frozen=True)
class DataDirResolution:
    """一次数据目录解析的结果：实际路径、来源，以及可选的可读提示。

    **解析本身是只读的**：它只做 ``exists()`` 判定，不创建、不删除、不搬移任何目录。创建只发生在
    进程启动路径的「新建」分支（见 ``__main__.main``），且只会建新默认路径及其 ``~/.alibot`` 父目录。
    """

    path: Path
    source: DataDirSource
    notice: str | None = None

    @property
    def source_label(self) -> str:
        return DATA_DIR_SOURCE_LABELS[self.source]


def new_default_data_dir() -> Path:
    """新默认数据目录：``~/.alibot/data/``（ADR-0031）。"""
    return Path.home() / DATA_DIR_ROOT_NAME / DEFAULT_DATA_DIR_NAME


def legacy_data_dir() -> Path:
    """旧默认数据目录：``~/Alibot``（只做兼容读取）。"""
    return Path.home() / LEGACY_DATA_DIR_NAME


def resolve_data_dir(explicit: Path | str | None = None) -> DataDirResolution:
    """按固定优先级解析数据目录（规格 D2）——结果是确定值，不依赖时间戳或遍历顺序。

    1. ``explicit``（``--data-dir``）或 ``ALIBOT_DATA_DIR``：使用显式值，既有语义不变；
    2. 新默认 ``~/.alibot/data/`` 已存在：使用新默认；
    3. 旧 ``~/Alibot`` 已存在：继续用旧位置，并给一条可读兼容提示；
    4. 两者都不存在：新建 ``~/.alibot/data/``（创建由启动路径执行）。

    新旧同时存在时**确定地选新默认**并说明旧位置被忽略。本函数**不删除、不搬移、不合并**两份数据。
    """
    if explicit is not None and str(explicit) != "":
        return DataDirResolution(Path(explicit).expanduser(), "explicit")

    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return DataDirResolution(Path(override).expanduser(), "explicit")

    new_default = new_default_data_dir()
    legacy = legacy_data_dir()
    if new_default.exists():
        notice = None
        if legacy.exists():
            notice = (
                f"提示：检测到旧数据目录 {legacy}；由于新默认 {new_default} 已存在，已忽略旧位置。"
                "两份数据均未删除、未搬移、未合并。"
            )
        return DataDirResolution(new_default, "new-default", notice)
    if legacy.exists():
        return DataDirResolution(
            legacy,
            "legacy-compat",
            (
                f"提示：新默认 {new_default} 不存在，继续使用旧数据目录 {legacy} 做兼容读取；"
                "数据未搬移。迁移是显式动作：停应用 → 整目录复制 → 校验 → 指向新位置。"
            ),
        )
    return DataDirResolution(new_default, "created")


def default_data_dir() -> Path:
    """当前默认数据目录（解析后的实际路径），可用环境变量覆盖。

    保留这个窄接口供既有调用方使用；需要来源与提示时用 :func:`resolve_data_dir`。
    """
    return resolve_data_dir().path


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Database:
    """单文件库的窄接口。写路径串行化（SQLite 单写者）。

    **启动时的版本闸门**（见 :meth:`_assert_schema_compatible`）：打开库之后、任何 schema 变更
    之前，先读 ``schema_meta.schema_version`` 与本代码的 :data:`SCHEMA_VERSION` 比对，不等就抛
    :class:`SchemaVersionMismatch` —— 于是旧库不会被「静默半改名」（版本号被改写、新表建出来但
    是空的、旧表数据还在）。
    """

    def __init__(self, data_dir: Path | str):
        self._connect(Path(data_dir))
        self._initialize()

    @classmethod
    def open_without_initializing(cls, data_dir: Path | str) -> Database:
        """打开库、过**同一道版本闸门**，但**不**做 schema 初始化（不建表、不写行）。

        存在的理由：显式修复入口需要先“看清这个库实际长什么样”——某张表到底在不在、里面有多少行
        ——才能决定补什么。用 ``Database(...)`` 做不到这点：它的 ``_initialize()`` 会立刻把缺的表
        ``CREATE IF NOT EXISTS`` 出来，于是“表缺失”这个待诊断的事实会在诊断之前就被抹掉
        （而且那是一次 schema 变更，不该发生在“先看一眼”的时候）。

        版本闸门照旧生效：版本不等时**连看都不让看**，抛 :class:`SchemaVersionMismatch`。
        """
        database = cls.__new__(cls)
        database._connect(Path(data_dir))
        database._assert_schema_compatible()
        return database

    def _connect(self, data_dir: Path) -> None:
        """建立连接（构造函数与 :meth:`open_without_initializing` 共用）。

        这里**只**做连接级设置：它们不写业务行、不改 schema，也**不改库文件一个字节**，因此可以
        安全地出现在版本闸门之前（闸门要在一个原样的库上做判断）。
        """
        self.data_dir = data_dir
        self.path = self.data_dir / DATABASE_FILE_NAME
        self._lock = threading.RLock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.create_function("CASEFOLD", 1, _sqlite_casefold, deterministic=True)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")

    def _apply_write_pragmas(self) -> None:
        """把这个库切到 WAL。**必须在版本闸门之后调用。**

        ``PRAGMA journal_mode = WAL`` 不是连接级开关：它要把库文件头里的 journal mode 从
        ``delete`` 改成 ``wal``（实测：对一个非 WAL 库执行它，文件 sha256 会变）。对**拒绝路径**
        来说那就等于「改动了旧库」，而本票的硬要求是旧库**一个字节都不许变**。所以它不能和上面
        那两条连接级 PRAGMA 放在一起，而必须等闸门放行之后才执行。

        这一条是实测发现的：初版把它放在闸门之前，只有用**已是 WAL 形态**的旧库取证时看不出问题
        （那时这条 PRAGMA 是 no-op）；换一个非 WAL 旧库，sha256 就变了。
        """
        self._conn.execute("PRAGMA journal_mode = WAL")

    # ─── 生命周期 ──────────────────────────────────────────────────────────
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _stored_schema_version(self) -> str | None:
        """读库里的 ``schema_meta.schema_version``；表或行不存在时返回 ``None``（**只读**）。"""
        meta = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
        ).fetchone()
        if meta is None:
            return None
        row = self._conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        return None if row is None else str(row[0])

    def table_names(self) -> set[str]:
        """库里当前存在的表名（只读）。

        形态判定（``repair.missing_current_required_tables``）与空库判定（:meth:`has_business_data`）
        **共用这一个查询落点**：三条进程级入口（启动 / ``--migrate`` / ``--repair-targets``）都不该
        各自去读 ``sqlite_master``。
        """
        with self._lock:
            return {
                str(row[0])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

    def has_business_data(self) -> bool:
        """库里是否**已有业务数据**（任意业务表有任何一行）。只读；表不存在时按没有数据处理。

        公开而非私有：它是「这个库算不算空库」这条事实的落点，启动闸门、``--migrate`` 与
        ``--repair-targets`` 三条入口共读同一个判据（``BUSINESS_TABLES``）。
        """
        existing = self.table_names()
        for table in BUSINESS_TABLES:
            if table not in existing:
                continue
            row = self._conn.execute(BUSINESS_COUNT_SQL[table]).fetchone()
            if row is not None and int(row[0]) > 0:
                return True
        return False

    def _assert_schema_compatible(self) -> None:
        """版本闸门：不等就**拒绝**，且**不做任何 schema 变更**。

        三条分支（与票面 Change 逐条对应）：

        - 库不存在 / 没有任何业务数据（且版本号缺失或就是本版）⇒ 放行，由 :meth:`_initialize` 建库。
        - 版本号**等于** :data:`SCHEMA_VERSION` ⇒ 放行（既有路径行为一字不变）。
        - 版本号**不等于**本版 ⇒ 抛 :class:`SchemaVersionMismatch`（旧库、未来库、垃圾版本号都算）。
          版本号**缺失但库里有业务数据**同样拒绝——那不是空库，是来源不明的库。

        **不修版本号、不跳过、不猜测**：闸门在**任何** schema 变更之前收口，旧库原样留着（一个字节
        都没被动过）。跨版本处置**不是**启动路径的一步，而是显式命令 ``python -m alibot --migrate``
        （ADR-0015 取代 ADR-0004 的「不做旧数据迁移」口径）；迁移链覆盖
        ``MIN_SUPPORTED_SOURCE_VERSION .. SCHEMA_VERSION``，拒绝正文给出该命令与备份指引。
        """
        stored = self._stored_schema_version()
        if stored == str(SCHEMA_VERSION):
            return
        if stored is None and not self.has_business_data():
            return
        raise SchemaVersionMismatch(self.path, found_version=stored, expected_version=SCHEMA_VERSION)

    def _initialize(self) -> None:
        """闸门先于**任何** schema 变更（也先于 WAL 切换）：不等就抛，于是旧库不会被静默半改名。"""
        with self._lock:
            self._assert_schema_compatible()
            self._apply_write_pragmas()
            self._conn.executescript(SCHEMA)
            self._conn.execute(STAMP_SCHEMA_VERSION_SQL, (str(SCHEMA_VERSION),))
            self._conn.execute(
                "INSERT INTO member(id, kind, display_name, avatar_slot, created_at) "
                "VALUES (?, 'owner', ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                (OWNER_ID, OWNER_DISPLAY_NAME, OWNER_AVATAR_SLOT, utc_now()),
            )
            # 工作区设置的**单行**：缺省值就是源语言、默认密度与「跟随系统」主题，与「空库 = 空工作台」
            # 同一条纪律（空库里没有任何用户取值，但设置面必须可读）。语句与迁移引擎共用同一份常量。
            self._conn.execute(
                INSERT_WORKSPACE_SETTINGS_SQL,
                (DEFAULT_LOCALE, DEFAULT_MESSAGE_DENSITY, DEFAULT_THEME, utc_now()),
            )

    # ─── 事务助手 ──────────────────────────────────────────────────────────
    def _begin(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._conn.execute("COMMIT")

    def _rollback(self) -> None:
        with suppress(sqlite3.OperationalError):  # 事务已经结束
            self._conn.execute("ROLLBACK")

    # ─── 可见文案（ADR-0009 第二层：按**库内**语言偏好渲染） ───────────────
    def _render_copy(self, key: str, /, **params: Any) -> str:
        """本层产出的**用户可见**自然语言一律经它取值：HTTP 错误的 ``message`` / ``fields`` 的
        自然语言，以及落库的 ``system`` 消息正文。

        取值方式选的是「本层自读偏好」而不是「由调用方注入」（票面授权里两种都算合规）：

        - 语言偏好的正本就是本层的 ``workspace_settings`` 那一行，自读与编排路径（无请求头）
          读的是**同一个来源**，因此两条路径不可能拿到不同语言；
        - 落库正文必须在**写入那一刻**定型（append-only，历史不回填），把语言判断留在写路径内部
          才不会被某个调用点漏掉；
        - 代价是一次单行主键读（``locale_of`` → ``workspace_settings``），且**不做缓存**：
          缓存会让「刚切换语言」的读数滞后，那正是这条机制要保证的。
        """
        return render(key, locale_of(self), **params)

    # ─── 成员 ─────────────────────────────────────────────────────────────
    def list_members(self, kind: str | None = None, include_deleted: bool = False) -> list[dict[str, Any]]:
        if kind not in (None, "owner", "agent"):
            raise DomainError(
                "validation_failed",
                self._render_copy("error.unknown_member_kind", kind=kind),
                fields={"kind": self._render_copy("error.field.member_kind_invalid")},
            )
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM member "
                "WHERE (:include_deleted = 1 OR deleted_at IS NULL) "
                "AND (:kind IS NULL OR kind = :kind) "
                "ORDER BY CASE kind WHEN 'owner' THEN 0 ELSE 1 END, created_at, id",
                {"include_deleted": 1 if include_deleted else 0, "kind": kind},
            ).fetchall()
        return [self._member_payload(row) for row in rows]

    def get_member(self, member_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM member WHERE id = ?", (member_id,)).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.member_not_found", member_id=member_id))
        return self._member_payload(row)

    @staticmethod
    def _member_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "display_name": row["display_name"],
            "avatar_slot": row["avatar_slot"],
            "created_at": row["created_at"],
            "deleted_at": row["deleted_at"],
        }

    # ─── Agent 配置（模型与系统提示是可配置数据，不是代码常量） ─────────
    def agent_members(self, include_disabled: bool = True) -> list[dict[str, Any]]:
        """全部 Agent 成员（含其配置），按创建顺序。"""
        with self._lock:
            rows = self._conn.execute(
                AGENT_SELECT_SQL + "FROM member m LEFT JOIN agent_config c ON c.member_id = m.id "
                "WHERE m.kind = 'agent' AND m.deleted_at IS NULL "
                "ORDER BY m.created_at, m.id",
            ).fetchall()
        return [self._agent_payload(row) for row in rows]

    def get_agent(self, member_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                AGENT_SELECT_SQL + "FROM member m LEFT JOIN agent_config c ON c.member_id = m.id "
                "WHERE m.id = ? AND m.kind = 'agent'",
                (member_id,),
            ).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.agent_not_found", member_id=member_id))
        return self._agent_payload(row)

    @staticmethod
    def _agent_payload(row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        return {
            "id": row["id"],
            "kind": row["kind"],
            "display_name": row["display_name"],
            "avatar_slot": row["avatar_slot"],
            "created_at": row["created_at"],
            "deleted_at": row["deleted_at"],
            "role": row["role"] or "",
            "model_id": row["model_id"] or "",
            "system_prompt": row["system_prompt"] or "",
            "enabled": bool(row["enabled"]) if row["enabled"] is not None else False,
            "auto_claim": bool(row["auto_claim"]) if row["auto_claim"] is not None else False,
            # Agent 级 workspace 覆盖（D14/D15）。NULL = 跟随频道默认；docker_image 的确认状态
            # 不在读路径推断（确认是 `remote_consent_scope` 的独立事实，由 workspace 读路径给出）。
            "workspace_backend": row["workspace_backend"] if "workspace_backend" in keys else None,
            "permission_mode": row["permission_mode"] if "permission_mode" in keys else None,
            "allow_local_shell": (
                bool(row["allow_local_shell"])
                if "allow_local_shell" in keys and row["allow_local_shell"] is not None
                else False
            ),
            "docker_image": row["docker_image"] if "docker_image" in keys else None,
            # 联网档（#184 / ADR-0026 §二）：``NULL`` = 跟随频道那一档，不是一个“关闭”值。
            "web_access": row["web_access"] if "web_access" in keys else None,
            "context_window_messages": row["context_window_messages"],
            "context_trigger_ratio": row["context_trigger_ratio"],
            "react_max_iters": row["react_max_iters"],
            "memory_mode": row["memory_mode"],
            "config_updated_at": row["config_updated_at"] if "config_updated_at" in keys else None,
        }

    def _agent_workspace_values(
        self,
        *,
        workspace_backend: str | None,
        permission_mode: str | None,
        docker_image: str | None,
        message_key: str,
    ) -> tuple[str | None, str | None, str | None]:
        """校验 Agent 级 workspace 覆盖（D14/D15）；``None`` = 这次请求没提到这一项。

        ``docker_image`` 的空串是**显式清空**（回到 AgentScope 内置镜像），归一成 ``None``；
        这与「没提到」在调用方是两种输入，由调用方在写 SQL 时区分。
        """
        fields: dict[str, str] = {}
        backend = None if workspace_backend is None else str(workspace_backend).strip()
        if backend is not None and backend not in WORKSPACE_BACKENDS:
            fields["workspace_backend"] = self._render_copy(
                "error.field.agent_workspace_backend_invalid", values=", ".join(WORKSPACE_BACKENDS)
            )
        mode = None if permission_mode is None else str(permission_mode).strip()
        if mode is not None and mode not in PERMISSION_MODES:
            fields["permission_mode"] = self._render_copy(
                "error.field.agent_permission_mode_invalid", values=", ".join(PERMISSION_MODES)
            )
        image = None if docker_image is None else str(docker_image).strip()
        if image is not None and len(image) > MAX_DOCKER_IMAGE_LENGTH:
            fields["docker_image"] = self._render_copy(
                "error.field.agent_docker_image_too_long", limit=MAX_DOCKER_IMAGE_LENGTH
            )
        if fields:
            raise DomainError("validation_failed", self._render_copy(message_key), fields=fields)
        return backend, mode, image or None

    def create_agent(
        self,
        name: str,
        role: str,
        model_id: str,
        system_prompt: str,
        avatar_slot: int | None = None,
        *,
        workspace_backend: str | None = None,
        permission_mode: str | None = None,
        allow_local_shell: bool = False,
        docker_image: str | None = None,
    ) -> dict[str, Any]:
        """校验整体通过后才写库：任一字段不合法则**不留任何半成品行**。

        名称与模型标识必填；模型标识的格式（provider 白名单）由服务层校验，
        本层只负责长度与空值这一类与持久化同构的判据。Agent 级 workspace 覆盖
        （``workspace_backend`` / ``permission_mode`` / ``allow_local_shell`` / ``docker_image``）
        与其它配置同一条偏序：任一取值不合法则整条创建都不发生（D14/D15）。
        """
        clean_name = (name or "").strip()
        clean_role = (role or "").strip()
        clean_model = (model_id or "").strip()
        clean_prompt = (system_prompt or "").strip()
        clean_backend, clean_mode, clean_image = self._agent_workspace_values(
            workspace_backend=workspace_backend,
            permission_mode=permission_mode,
            docker_image=docker_image,
            message_key="error.agent_invalid",
        )

        fields: dict[str, str] = {}
        if not clean_name:
            fields["name"] = self._render_copy("error.field.agent_name_empty")
        elif len(clean_name) > MAX_MEMBER_NAME_LENGTH:
            fields["name"] = self._render_copy(
                "error.field.agent_name_too_long", limit=MAX_MEMBER_NAME_LENGTH
            )
        if not clean_model:
            fields["model_id"] = self._render_copy("error.model_id_empty")
        elif len(clean_model) > MAX_MODEL_ID_LENGTH:
            fields["model_id"] = self._render_copy("error.field.model_id_too_long", limit=MAX_MODEL_ID_LENGTH)
        if len(clean_role) > MAX_AGENT_ROLE_LENGTH:
            fields["role"] = self._render_copy("error.field.role_too_long", limit=MAX_AGENT_ROLE_LENGTH)
        if len(clean_prompt) > MAX_SYSTEM_PROMPT_LENGTH:
            fields["system_prompt"] = self._render_copy(
                "error.field.system_prompt_too_long", limit=MAX_SYSTEM_PROMPT_LENGTH
            )
        if fields:
            raise DomainError("validation_failed", self._render_copy("error.agent_invalid"), fields=fields)

        with self._lock:
            if avatar_slot is None:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM member WHERE kind = 'agent'").fetchone()
                avatar_slot = int(row["n"]) % AVATAR_SLOT_COUNT
            member_id = new_id("mem")
            now = utc_now()
            self._begin()
            try:
                self._conn.execute(
                    "INSERT INTO member(id, kind, display_name, avatar_slot, created_at) "
                    "VALUES (?, 'agent', ?, ?, ?)",
                    (member_id, clean_name, avatar_slot, now),
                )
                self._conn.execute(
                    "INSERT INTO agent_config(member_id, role, model_id, system_prompt, enabled, "
                    "workspace_backend, permission_mode, allow_local_shell, docker_image, "
                    "context_window_messages, context_trigger_ratio, react_max_iters, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)",
                    (
                        member_id,
                        clean_role,
                        clean_model,
                        clean_prompt,
                        clean_backend,
                        clean_mode,
                        1 if allow_local_shell else 0,
                        clean_image,
                        now,
                        now,
                    ),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_agent(member_id)

    def update_agent(
        self,
        member_id: str,
        *,
        display_name: str | None = None,
        role: str | None = None,
        model_id: str | None = None,
        system_prompt: str | None = None,
        enabled: bool | None = None,
        auto_claim: bool | None = None,
        workspace_backend: str | None = None,
        permission_mode: str | None = None,
        allow_local_shell: bool | None = None,
        docker_image: str | None = None,
        web_access: Any = _UNSET,
        context_window_messages: Any = _UNSET,
        context_trigger_ratio: Any = _UNSET,
        react_max_iters: Any = _UNSET,
        memory_mode: Any = _UNSET,
    ) -> dict[str, Any]:
        """部分更新 Agent 配置（``None`` = 这次请求没提到这一字段，保持原值）。

        ``web_access`` 与四个运行时配置字段是**例外**：它们是允许显式清空的字段，用 ``_UNSET`` 区分「没提到」与
        「显式 ``null`` = 清除 Agent 级覆盖、回到跟随频道」（2026-10-21 人定，ADR-0026 §十五；
        与 ``update_channel_config`` 的同名字段同一口径）。

        校验先于写入：**任一字段不合法则整体不改**（与 ``create_agent`` /
        ``update_workspace_settings`` 同一偏序——没有「一半新一半旧」的中间态）。``model_id`` 的
        **形状**校验（provider 白名单）由服务层在进到本层之前完成；本层只判与持久化同构的判据
        （空值 / 长度 / Agent 级 workspace 取值）。

        它只写 ``member.display_name`` 与 ``agent_config`` 那一行（后者由 ``create_agent`` 在同一
        事务里创建，因此本写路径没有「缺行」的中间态）：**不落消息、不唤醒、不消耗任何预算、
        也不开世代**——配置变更不是一次对话动作（ADR-0011）。
        """
        self.get_agent(member_id)  # 存在性 + 「必须是 Agent」：不是就抛 NotFound
        runtime_values = {
            "context_window_messages": context_window_messages,
            "context_trigger_ratio": context_trigger_ratio,
            "react_max_iters": react_max_iters,
            "memory_mode": memory_mode,
        }
        invalid_runtime: dict[str, str] = {}
        for key, value in runtime_values.items():
            if value is _UNSET or value is None:
                continue
            if key == "memory_mode":
                valid = value in ("off", "agentic")
            elif key == "context_trigger_ratio":
                valid = isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 0.9
            else:
                valid = isinstance(value, int) and not isinstance(value, bool) and value > 0
            if not valid:
                invalid_runtime[key] = self._render_copy("error.field.agent_runtime_invalid", field=key)
        if invalid_runtime:
            raise DomainError(
                "validation_failed", self._render_copy("error.agent_update_invalid"), fields=invalid_runtime
            )
        clean_backend, clean_mode, clean_image = self._agent_workspace_values(
            workspace_backend=workspace_backend,
            permission_mode=permission_mode,
            docker_image=docker_image,
            message_key="error.agent_update_invalid",
        )
        # 联网档的 Agent 级覆盖（#184 / ADR-0026 §二）：白名单与频道同一条。三态入参：
        # ``_UNSET`` = 这次没提到（保持原值）、``None`` = 显式清空（写库 ``NULL`` = 跟随频道）、
        # 字符串 = 写这一档。``NULL`` 与「没提到」在 SQL 里由两个参数分开表达（见下方 UPDATE）。
        if web_access is _UNSET or web_access is None:
            clean_web_access: Any = web_access
        else:
            clean_web_access = str(web_access).strip()
        if (
            clean_web_access is not _UNSET
            and clean_web_access is not None
            and clean_web_access not in WEB_ACCESS_LEVELS
        ):
            raise DomainError(
                "validation_failed",
                self._render_copy("error.agent_update_invalid"),
                fields={
                    "web_access": self._render_copy(
                        "error.field.agent_web_access_invalid", values=", ".join(WEB_ACCESS_LEVELS)
                    )
                },
            )
        clean = {
            "display_name": None if display_name is None else (display_name or "").strip(),
            "role": None if role is None else (role or "").strip(),
            "model_id": None if model_id is None else (model_id or "").strip(),
            "system_prompt": None if system_prompt is None else (system_prompt or "").strip(),
        }

        fields: dict[str, str] = {}
        if clean["display_name"] is not None:
            if not clean["display_name"]:
                fields["display_name"] = self._render_copy("error.field.agent_name_empty")
            elif len(clean["display_name"]) > MAX_MEMBER_NAME_LENGTH:
                fields["display_name"] = self._render_copy(
                    "error.field.agent_name_too_long", limit=MAX_MEMBER_NAME_LENGTH
                )
        if clean["model_id"] is not None:
            if not clean["model_id"]:
                fields["model_id"] = self._render_copy("error.model_id_empty")
            elif len(clean["model_id"]) > MAX_MODEL_ID_LENGTH:
                fields["model_id"] = self._render_copy(
                    "error.field.model_id_too_long", limit=MAX_MODEL_ID_LENGTH
                )
        if clean["role"] is not None and len(clean["role"]) > MAX_AGENT_ROLE_LENGTH:
            fields["role"] = self._render_copy("error.field.role_too_long", limit=MAX_AGENT_ROLE_LENGTH)
        if clean["system_prompt"] is not None and len(clean["system_prompt"]) > MAX_SYSTEM_PROMPT_LENGTH:
            fields["system_prompt"] = self._render_copy(
                "error.field.system_prompt_too_long", limit=MAX_SYSTEM_PROMPT_LENGTH
            )
        if fields:
            raise DomainError(
                "validation_failed", self._render_copy("error.agent_update_invalid"), fields=fields
            )

        with self._lock:
            self._begin()
            try:
                if clean["display_name"] is not None:
                    self._conn.execute(
                        "UPDATE member SET display_name = ? WHERE id = ?",
                        (clean["display_name"], member_id),
                    )
                # `COALESCE` 就是「没提到的字段保持原值」这条部分更新语义的落点：它让「只写该写的」
                # 与「绝不部分写入」在**同一条语句**里成立，不靠 Python 侧拼 SQL。
                self._conn.execute(
                    "UPDATE agent_config SET "
                    "role = COALESCE(?, role), "
                    "model_id = COALESCE(?, model_id), "
                    "system_prompt = COALESCE(?, system_prompt), "
                    "enabled = COALESCE(?, enabled), "
                    "auto_claim = COALESCE(?, auto_claim), "
                    "workspace_backend = COALESCE(?, workspace_backend), "
                    "permission_mode = COALESCE(?, permission_mode), "
                    "allow_local_shell = COALESCE(?, allow_local_shell), "
                    # 空串是**显式清空**（回到内置镜像）；「没提到」保持原值。COALESCE 无法区分
                    # 这两种输入，因此这里用两个参数：第一个是「这次提到了吗」。
                    "docker_image = CASE WHEN ? THEN ? ELSE docker_image END, "
                    # 联网档的覆盖：**三态**，COALESCE 表达不了，因此与 ``docker_image`` 同一条
                    # 「这次提到了吗」写法。第一个参数是「提到了」、第二个是「写成什么」——
                    # 显式 ``None`` 因此能落成 ``NULL``（回到跟随频道），而没提到保持原值。
                    "web_access = CASE WHEN ? THEN ? ELSE web_access END, "
                    "context_window_messages = CASE WHEN ? THEN ? ELSE context_window_messages END, "
                    "context_trigger_ratio = CASE WHEN ? THEN ? ELSE context_trigger_ratio END, "
                    "react_max_iters = CASE WHEN ? THEN ? ELSE react_max_iters END, "
                    "memory_mode = CASE WHEN ? THEN ? ELSE memory_mode END, "
                    "updated_at = ? WHERE member_id = ?",
                    (
                        clean["role"],
                        clean["model_id"],
                        clean["system_prompt"],
                        None if enabled is None else int(bool(enabled)),
                        None if auto_claim is None else int(bool(auto_claim)),
                        clean_backend,
                        clean_mode,
                        None if allow_local_shell is None else int(bool(allow_local_shell)),
                        docker_image is not None,
                        clean_image,
                        clean_web_access is not _UNSET,
                        None if clean_web_access is _UNSET else clean_web_access,
                        context_window_messages is not _UNSET,
                        None if context_window_messages is _UNSET else context_window_messages,
                        context_trigger_ratio is not _UNSET,
                        None if context_trigger_ratio is _UNSET else context_trigger_ratio,
                        react_max_iters is not _UNSET,
                        None if react_max_iters is _UNSET else react_max_iters,
                        memory_mode is not _UNSET,
                        None if memory_mode is _UNSET else memory_mode,
                        utc_now(),
                        member_id,
                    ),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        # 读回落库后的那一份（不是把入参回显出去）：调用方看到的是**持久化事实**。
        return self.get_agent(member_id)

    def agent_stats(self, member_id: str | None = None) -> dict[str, dict[str, Any]]:
        """按成员聚合的**只读既有事实**（Agent 概览页每个数字的唯一来源）——不新增数据面。

        - ``channel_count``：它是成员的 **channel 型 target** 数（``target_member`` 与 ``target.kind``）；
        - ``round_count``：它的 ``round_outcome`` 行数（参与过的轮次），append-only 事实；
        - ``completed_task_count``：指派给它且 ``status='done'`` 的 ``task`` 行数；
        - ``replied_count`` / ``verdict_count``：成功率的两项（``replied`` ÷ 有结论的轮次
          ``replied|ignored|failed``）。``merged``（不是它的决定）与 ``budget-exhausted``
          （根本没跑起来）**不计入**分母。

        三处读的都是**行级事实**，不读内存、不做推断：后端重启后读数逐字不变。
        ``member_id`` 为 ``None`` 时一次聚合全部成员（列表面用它，避免 N+1 查询）——两条路径共用
        同一条语句（过滤是 ``AGENT_STATS_*_SQL`` 里的 ``:member_id IS NULL OR …``），因此不存在
        第二份只差一个 ``WHERE`` 的拷贝。
        """
        args = {"member_id": member_id}
        stats: dict[str, dict[str, Any]] = {}
        with self._lock:
            for row in self._conn.execute(AGENT_STATS_CHANNEL_SQL, args).fetchall():
                stats.setdefault(row["member_id"], {})["channel_count"] = int(row["n"])
            for row in self._conn.execute(AGENT_STATS_ROUND_SQL, args).fetchall():
                entry = stats.setdefault(row["member_id"], {})
                entry["round_count"] = int(row["n"])
                entry["replied_count"] = int(row["replied"] or 0)
                entry["verdict_count"] = int(row["verdicts"] or 0)
            for row in self._conn.execute(AGENT_STATS_TASK_SQL, args).fetchall():
                stats.setdefault(row["member_id"], {})["completed_task_count"] = int(row["n"])
        return stats

    # ─── 远端数据流告知（按模型提供商一次性确认） ────────────────────────
    def list_remote_consents(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT provider, acknowledged_at FROM remote_consent ORDER BY acknowledged_at, provider"
            ).fetchall()
        return [{"provider": row["provider"], "acknowledged_at": row["acknowledged_at"]} for row in rows]

    def has_remote_consent(self, provider: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM remote_consent WHERE provider = ?", (provider,)
            ).fetchone()
        return row is not None

    def record_remote_consent(self, provider: str) -> dict[str, Any]:
        """记下一次告知确认。幂等：重复确认不覆盖首次时间。本表不存任何凭证。"""
        clean = (provider or "").strip()
        if not clean:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.field.provider_empty"),
                fields={"provider": self._render_copy("error.field.provider_empty")},
            )
        now = utc_now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO remote_consent(provider, acknowledged_at) VALUES (?, ?) "
                "ON CONFLICT(provider) DO NOTHING",
                (clean, now),
            )
            row = self._conn.execute(
                "SELECT provider, acknowledged_at FROM remote_consent WHERE provider = ?", (clean,)
            ).fetchone()
        return {"provider": row["provider"], "acknowledged_at": row["acknowledged_at"]}

    # ─── 远端数据面 scope（model / mcp / docker / web；#139 建表 / #150 先用于 docker / #186 D6 补 web）───
    def has_remote_consent_scope(self, scope_kind: str, scope_id: str) -> bool:
        """按 ``remote_consent_scope`` 判断一次告知是否已确认（只读）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM remote_consent_scope WHERE scope_kind = ? AND scope_id = ?",
                (scope_kind, scope_id),
            ).fetchone()
        return row is not None

    def record_remote_consent_scope(self, scope_kind: str, scope_id: str) -> dict[str, Any]:
        """记一次 scope 告知确认。幂等；不存凭证、不存连接参数、不落消息/不唤醒。

        ``web`` 档（#186 / D6）与另三档同一纪律：确认单位是 **provider**（``brave`` / ``tavily``），
        人类面写入口是 ``POST /api/web-providers/{provider}/consent``；装配层读它决定挂不挂联网工具。
        """
        if scope_kind not in REMOTE_CONSENT_SCOPE_KINDS:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.remote_scope_kind_invalid"),
                fields={
                    "scope_kind": self._render_copy(
                        "error.field_choices", values=", ".join(REMOTE_CONSENT_SCOPE_KINDS)
                    )
                },
            )
        clean = (scope_id or "").strip()
        if not clean:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.remote_scope_id_empty"),
                fields={"scope_id": self._render_copy("error.remote_scope_id_empty")},
            )
        now = utc_now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) "
                "VALUES (?, ?, ?) ON CONFLICT(scope_kind, scope_id) DO NOTHING",
                (scope_kind, clean, now),
            )
            row = self._conn.execute(
                "SELECT scope_kind, scope_id, acknowledged_at FROM remote_consent_scope "
                "WHERE scope_kind = ? AND scope_id = ?",
                (scope_kind, clean),
            ).fetchone()
        return {
            "scope_kind": str(row["scope_kind"]),
            "scope_id": str(row["scope_id"]),
            "acknowledged_at": str(row["acknowledged_at"]),
        }

    def list_remote_consent_scopes(self, scope_kind: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if scope_kind is None:
                rows = self._conn.execute(
                    "SELECT scope_kind, scope_id, acknowledged_at FROM remote_consent_scope "
                    "ORDER BY acknowledged_at, scope_kind, scope_id"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT scope_kind, scope_id, acknowledged_at FROM remote_consent_scope "
                    "WHERE scope_kind = ? ORDER BY acknowledged_at, scope_id",
                    (scope_kind,),
                ).fetchall()
        return [
            {
                "scope_kind": str(row["scope_kind"]),
                "scope_id": str(row["scope_id"]),
                "acknowledged_at": str(row["acknowledged_at"]),
            }
            for row in rows
        ]

    # ─── 联网 provider 凭据（#199；明文入库、掩码回读） ────────────────────
    def _require_web_provider(self, provider: str) -> str:
        """凭据写路径的 provider 白名单：非法取值整条 400，不做部分写入。"""
        clean = (provider or "").strip()
        if clean not in SEARCH_PROVIDERS:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.web_provider_invalid", values=", ".join(SEARCH_PROVIDERS)),
                fields={
                    "provider": self._render_copy("error.field_choices", values=", ".join(SEARCH_PROVIDERS))
                },
            )
        return clean

    def web_provider_credentials(self) -> dict[str, Any]:
        """装配路径的**内部**读数：provider → 明文 key，以及默认 provider。

        这个方法只给装配层（``agent_seam``）用：工具需要真实 key 才能发请求。人类面一律走
        :meth:`web_provider_credential_views` / HTTP 载荷，那两条路径只回掩码。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT provider, api_key, is_default FROM web_provider_credential ORDER BY provider"
            ).fetchall()
        credentials = {str(row["provider"]): str(row["api_key"]) for row in rows}
        default = next(
            (str(row["provider"]) for row in rows if bool(row["is_default"])),
            None,
        )
        return {"credentials": credentials, "default_provider": default}

    def web_provider_credential_views(self) -> list[dict[str, Any]]:
        """人类面读数：五家都出现，只有「已配置 / 掩码 / 是否默认 / 更新时间」。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT provider, api_key, is_default, updated_at FROM web_provider_credential"
            ).fetchall()
        saved = {str(row["provider"]): row for row in rows}
        views: list[dict[str, Any]] = []
        for provider in SEARCH_PROVIDERS:
            row = saved.get(provider)
            views.append(
                {
                    "provider": provider,
                    "configured": row is not None,
                    "masked": mask_credential(str(row["api_key"])) if row is not None else "",
                    "is_default": bool(row["is_default"]) if row is not None else False,
                    "updated_at": str(row["updated_at"]) if row is not None else "",
                }
            )
        return views

    def save_web_provider_credential(
        self, provider: str, api_key: str | None = None, *, is_default: bool | None = None
    ) -> dict[str, Any]:
        """保存 / 切换默认凭据（人类面动作）：要么写入非空 key，要么只把已配置项设为默认。

        保存 key 的动作**同时**写 ``remote_consent_scope('web', provider)``（数据面确认，幂等、
        只存时间）。任一校验失败整条拒绝、不部分写入；返回的载荷只有掩码。
        """
        clean = (provider or "").strip()
        if clean not in SEARCH_PROVIDERS:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.web_provider_invalid", values=", ".join(SEARCH_PROVIDERS)),
                fields={
                    "provider": self._render_copy("error.field_choices", values=", ".join(SEARCH_PROVIDERS))
                },
            )
        key_provided = api_key is not None
        key = (api_key or "").strip()
        field_errors: dict[str, str] = {}
        if key_provided and not key:
            field_errors["api_key"] = self._render_copy("error.field.web_provider_key_empty")
        if key and len(key) > MAX_WEB_CREDENTIAL_LENGTH:
            field_errors["api_key"] = self._render_copy(
                "error.field.web_provider_key_too_long", limit=MAX_WEB_CREDENTIAL_LENGTH
            )
        with self._lock:
            existing = self._conn.execute(
                "SELECT provider FROM web_provider_credential WHERE provider = ?", (clean,)
            ).fetchone()
        if not key_provided and existing is None:
            field_errors["api_key"] = self._render_copy("error.field.web_provider_key_empty")
        if field_errors:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.web_provider_credential_invalid"),
                fields=field_errors,
            )
        now = utc_now()
        with self._lock:
            self._begin()
            try:
                if key:
                    force_default = is_default is True
                    if force_default:
                        self._conn.execute("UPDATE web_provider_credential SET is_default = 0")
                    self._conn.execute(
                        "INSERT INTO web_provider_credential("
                        "provider, api_key, is_default, created_at, updated_at"
                        ") VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(provider) DO UPDATE SET "
                        "api_key = excluded.api_key, "
                        "is_default = CASE WHEN ? THEN 1 ELSE is_default END, "
                        "updated_at = excluded.updated_at",
                        (clean, key, 1 if force_default else 0, now, now, 1 if force_default else 0),
                    )
                    self._conn.execute(
                        "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) "
                        "VALUES ('web', ?, ?) ON CONFLICT(scope_kind, scope_id) DO NOTHING",
                        (clean, now),
                    )
                elif is_default:
                    self._conn.execute("UPDATE web_provider_credential SET is_default = 0")
                    self._conn.execute(
                        "UPDATE web_provider_credential SET is_default = 1, updated_at = ? "
                        "WHERE provider = ?",
                        (now, clean),
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return next(view for view in self.web_provider_credential_views() if view["provider"] == clean)

    def delete_web_provider_credential(self, provider: str) -> dict[str, Any]:
        """清空一家 provider 的凭据（连同它的 ``is_default``）。删除不撤销确认行。"""
        clean = (provider or "").strip()
        if clean not in SEARCH_PROVIDERS:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.web_provider_invalid", values=", ".join(SEARCH_PROVIDERS)),
                fields={
                    "provider": self._render_copy("error.field_choices", values=", ".join(SEARCH_PROVIDERS))
                },
            )
        with self._lock:
            self._begin()
            try:
                self._conn.execute("DELETE FROM web_provider_credential WHERE provider = ?", (clean,))
                self._commit()
            except Exception:
                self._rollback()
                raise
        return next(view for view in self.web_provider_credential_views() if view["provider"] == clean)

    # ─── 工作区设置（配置类取值：人类面可写，用量类事实不可写） ─────────────
    def workspace_settings(self) -> dict[str, Any]:
        """当前工作区设置（**只读**）。行的缺省值在空库初始化时就已写入。

        ``open_without_initializing`` 打开的库可能是「单行不存在」的形态（诊断路径不做 schema
        变更），因此这里按缺省值回退，而不是在只读路径上补一行——读一个库不该改它。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT locale, message_density, theme, updated_at FROM workspace_settings WHERE id = 1"
            ).fetchone()
        if row is None:
            return {
                "locale": DEFAULT_LOCALE,
                "message_density": DEFAULT_MESSAGE_DENSITY,
                "theme": DEFAULT_THEME,
                "updated_at": "",
            }
        return {
            "locale": row["locale"],
            "message_density": row["message_density"],
            "theme": row["theme"],
            "updated_at": row["updated_at"],
        }

    def update_workspace_settings(
        self,
        locale: str | None = None,
        message_density: str | None = None,
        theme: str | None = None,
    ) -> dict[str, Any]:
        """部分更新工作区设置（``None`` = 这一档没被这次请求提到，保持原值）。

        校验先于写入：任一档位非法则**整体不改**（与 `add_channel_member` / `create_agent`
        的偏序一致——没有「一半新一半旧」的中间态）。它只写 ``workspace_settings`` 一行：
        **不落消息、不唤醒、不消耗预算**——设置不是对话事实。
        """
        fields: dict[str, str] = {}
        if locale is not None and locale not in LOCALES:
            fields["locale"] = self._render_copy("error.field_choices", values=", ".join(LOCALES))
        if message_density is not None and message_density not in MESSAGE_DENSITIES:
            fields["message_density"] = self._render_copy(
                "error.field_choices", values=", ".join(MESSAGE_DENSITIES)
            )
        if theme is not None and theme not in THEMES:
            fields["theme"] = self._render_copy("error.field_choices", values=", ".join(THEMES))
        if fields:
            raise DomainError("validation_failed", self._render_copy("error.settings_invalid"), fields=fields)
        with self._lock:
            current = self.workspace_settings()
            self._conn.execute(
                "INSERT INTO workspace_settings(id, locale, message_density, theme, updated_at) "
                "VALUES (1, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET locale = excluded.locale, "
                "message_density = excluded.message_density, theme = excluded.theme, "
                "updated_at = excluded.updated_at",
                (
                    current["locale"] if locale is None else locale,
                    current["message_density"] if message_density is None else message_density,
                    current["theme"] if theme is None else theme,
                    utc_now(),
                ),
            )
        return self.workspace_settings()

    # ─── 提醒（库内对象；ADR-0012：增删不落消息、不唤醒、不消耗预算） ────────
    def list_reminders(self) -> list[dict[str, Any]]:
        """全部提醒，按创建序（``created_at, rowid``）。

        归属频道用 ``LEFT JOIN`` 取名字（见 :data:`REMINDER_SELECT_SQL`）：**空 = 不归属**
        （可空列的正本事实），因此不能写成 ``JOIN``——那会把「不归属任何频道的提醒」整条丢掉。

        并列的判据必须是 ``rowid``（= **插入序**），不能是 ``id``：``utc_now`` 只有**毫秒**
        精度，同一毫秒内连着建的几条 ``created_at`` 逐字相同，而 ``id`` 是 ``uuid4`` 的随机段——
        拿它当并列判据会让「创建序」变成随机序（同一毫秒里建的 3 条，恰好升序的概率只有 1/6）。
        """
        with self._lock:
            rows = self._conn.execute(REMINDER_SELECT_SQL + "ORDER BY r.created_at, r.rowid").fetchall()
        return [self._reminder_payload(row) for row in rows]

    def _get_reminder(self, reminder_id: str) -> dict[str, Any]:
        """按 id 取一条提醒的载荷（含归属频道名）；不存在 ⇒ 404 ``not_found``。

        **调用方负责持锁**（新增路径与删除路径都在同一个锁里连着做两件事，不该在中间放锁）。
        读与删因此共用同一个 404 判据：新增后读回、删除前读回，两处不可能给出不同的说法。
        """
        row = self._conn.execute(REMINDER_SELECT_SQL + "WHERE r.id = ?", (reminder_id,)).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.reminder_not_found", reminder_id=reminder_id))
        return self._reminder_payload(row)

    @staticmethod
    def _reminder_payload(row: sqlite3.Row) -> dict[str, Any]:
        """一条提醒的读路径载荷。

        ``fired_at`` 与 ``state`` 是**恒有**的两个键（C20）：

        - ``fired_at``：``null`` = 还没到点；非空 = 已投递（ISO-8601 **UTC** 串、带 ``Z`` 后缀，与
          ``created_at`` 同一族口径）。它是**无归属**提醒唯一可观察的到点证据——那些提醒到点不落消息。
        - ``state``：``pending`` / ``fired`` / ``missed`` 三取一的**只读派生值**，由 ``fired_at`` 与到期
          时刻之差推导（唯一规则在 :func:`alibot.reminder_delivery.reminder_state`）。它**不落库、不进列**：
          D4 否掉的是「第三个状态**列**」，派生字段不是列。
        """
        fired_at = row["fired_at"]
        return {
            "id": row["id"],
            "text": row["text"],
            "time": row["time"],
            "channel_id": row["channel_id"],
            # 不归属时是空串而不是 null：前端那一行按「有归属才显示频道」分支，空串就是没有。
            "channel_name": row["channel_name"] or "",
            "created_at": row["created_at"],
            "fired_at": fired_at,
            "state": reminder_state(str(row["time"]), fired_at),
        }

    def fire_reminder(
        self,
        reminder_id: str,
        copy_key: str | None = None,
        *,
        fired_at: str | None = None,
    ) -> dict[str, Any] | None:
        """把一条提醒标成**已投递**，并在给了 ``copy_key`` 时落一条 ``system`` 消息。

        **同一事务**（C5）：落消息与写 ``fired_at`` 在同一个 ``_begin()`` / ``_commit()`` 里收口，任一步
        失败整体回滚——于是「消息落了但标记没写」这条会重复投递的中间态在构造上不可达（体例照
        :meth:`append_message` 的事务边界）。回滚意味着「从未成功」，下一轮轮询会重试。

        幂等（「至多投递一次」的唯一落点）：``SELECT ... WHERE fired_at IS NULL`` 先把「已投过」的行
        排除掉。已被别的路径投过 ⇒ 返回 ``None``、**改动 0 行**，调用方据此知道这一批不必再落消息。

        ``copy_key`` 为 ``None`` 表示「只写标记、不落消息」的两条分支：**无归属**提醒（不新增 target，
        ADR-0012）与**窗口外**的错过（人类面只呈现为已错过）。消息的作者恒为 **owner**，``kind='system'``
        ——它不是对话回合，因此不唤醒、不消耗预算、不计未读、不进搜索（既有口径一个都不变，C3）。

        ``fired_at`` 是审计戳：它取**投递时刻**（由投递器传入，与 ``created_at`` 同一族口径：UTC、
        毫秒精度、``Z`` 后缀）。不在这里取墙钟的原因：投递器的判定用的是**注入时刻**，两者必须是
        同一个瞬时，否则「已投递」会晚于「判定到点」，C9 的 30 秒精度判据与 C20 的 `state` 推导
        会在同一次投递里各算两份时间。

        返回 ``{"reminder": <最新载荷>, "message": <新消息或 None>}``；已投递过则整条返回 ``None``。
        """
        # **一个瞬时服务三处**：``fired_at``（已投递标记）、新消息的 ``created_at``、以及投递器的判定口径。
        # 若在这里各自再取一次墙钟，注入时刻的行为测试会落出「``fired_at`` 晚于 ``created_at``」的
        # 审计行，C9 的 30 秒精度判据与 C20 的 `state` 推导也会在同一次投递里算出两份时间。
        stamp = fired_at if fired_at is not None else utc_now()
        channel_id: str | None = None
        seq: int | None = None
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT text, channel_id FROM reminder WHERE id = ? AND fired_at IS NULL",
                    (reminder_id,),
                ).fetchone()
                if row is None:
                    # 已投递过（或这条提醒不在了）：**改动 0 行**，这是「至多投递一次」的构造性保证。
                    self._rollback()
                    return None
                self._conn.execute("UPDATE reminder SET fired_at = ? WHERE id = ?", (stamp, reminder_id))
                channel_id = row["channel_id"]
                if copy_key is not None and channel_id is not None:
                    # 正文按**库内语言偏好**渲染（投递跑在 HTTP 请求之外，没有请求头可读，ADR-0009），
                    # 且在写入那一刻定型：落库后不随偏好切换回溯（append-only 取证面）。
                    seq = self._append_target_message_locked(
                        str(channel_id), self._render_copy(copy_key, text=str(row["text"])), stamp
                    )
                self._commit()
            except Exception:
                # 落消息那一步失败 ⇒ 整体回滚：消息没落、``fired_at`` 没写，下一轮轮询会**重试**
                # （那是「从未成功」，不是重复投递）。
                self._rollback()
                raise
            # 消息载荷走**既有那一条**装配路径（与 ``append_message`` 同样的收尾）：事件流的订阅者
            # 因此拿到与 ``GET /messages`` 逐字同形的一条（含 ``id`` 与 ``author``），
            # 不在投递路径上自拼一份少字段的近似形状。
            message = (
                self.get_message(str(channel_id), seq) if seq is not None and channel_id is not None else None
            )
            reminder = self._get_reminder(reminder_id)
        return {"reminder": reminder, "message": message}

    def create_reminder(self, text: str, time: str, channel_id: str | None = None) -> dict[str, Any]:
        """落一条提醒；校验先于写入（任一字段不合法则**不留任何半成品行**）。

        归属频道（若给了）必须存在。``channel_id`` 是**请求体里的引用**，因此与
        ``create_channel`` 的 ``member_ids`` / ``add_channel_member`` 同口径：
        ``validation_failed`` + ``fields``（复用 ``error.channel_not_found`` 的取值），
        而不是把「路径资源不存在」的 404 用在这里。

        这里的自然语言与其它模块一样经 :meth:`_render_copy` 取（后端文案按库内语言偏好渲染，
        ADR-0009 第二层）：本票新增的文案也是后端可见文案，没有理由绕开那一条路径。
        """
        clean_text = (text or "").strip()
        clean_time = (time or "").strip()
        fields: dict[str, str] = {}
        if not clean_text:
            fields["text"] = self._render_copy("error.reminder_text_empty")
        elif len(clean_text) > MAX_REMINDER_TEXT_LENGTH:
            fields["text"] = self._render_copy("error.reminder_text_too_long", limit=MAX_REMINDER_TEXT_LENGTH)
        if not REMINDER_TIME_PATTERN.match(clean_time):
            fields["time"] = self._render_copy("error.reminder_time_shape")
        if fields:
            raise DomainError("validation_failed", self._render_copy("error.reminder_invalid"), fields=fields)
        if channel_id is not None:
            try:
                self.get_channel(channel_id)
            except NotFound:
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.reminder_invalid"),
                    fields={
                        "channel_id": self._render_copy("error.channel_not_found", channel_id=channel_id)
                    },
                ) from None

        reminder_id = new_id("rm")
        with self._lock:
            self._conn.execute(
                "INSERT INTO reminder(id, text, time, channel_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (reminder_id, clean_text, clean_time, channel_id, utc_now()),
            )
            reminder = self._get_reminder(reminder_id)
        return reminder

    def delete_reminder(self, reminder_id: str) -> dict[str, Any]:
        """删一条提醒，并返回**这次真的删掉的**那一条（不写墓碑、不落消息）。

        不存在的 id ⇒ 404，与 ``get_channel`` / ``get_task``「键不存在就是 404」同口径。
        读与删在同一个锁里做，因此返回的那条不可能是「读完之后被别人删掉」的那条。
        """
        with self._lock:
            reminder = self._get_reminder(reminder_id)
            self._conn.execute("DELETE FROM reminder WHERE id = ?", (reminder_id,))
        return reminder

    # ─── 轮次结果（append-only） ────────────────────────────────────────
    def append_round_outcome(
        self,
        member_id: str,
        target_id: str,
        base_seq: int,
        status: str,
        reason: str = "",
        started_at: str | None = None,
        finished_at: str | None = None,
        consumed_budget: bool = False,
        merged: bool = False,
        reply_seq: int | None = None,
        finished_reason: str | None = None,
    ) -> dict[str, Any]:
        """落一条轮次结果。

        ``consumed_budget`` 是**行级**标记（「这一轮所属的那次跳」），**不是**跳的计数：
        一次跳投递 N 个成员会写 N 条 ``consumed_budget=1`` 的行，而跳数只 +1。**不可按行求和** ——
        该人类世代的跳数唯一正本是 ``target_budget.used``（:meth:`budget_state`）。
        行数与跳数之间**没有固定关系**（投递的成员若无可落库内容就不落行），所以按行求和得到的数
        既可能偏大也可能偏小；它不是跳数。
        """
        if status not in ROUND_STATUSES:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.unknown_round_status", status=status),
                fields={"status": self._render_copy("error.field.round_status_invalid")},
            )
        now = utc_now()
        outcome_id = new_id("round")
        with self._lock:
            self._conn.execute(
                "INSERT INTO round_outcome(id, member_id, target_id, base_seq, status, reason, started_at, "
                "finished_at, consumed_budget, merged, reply_seq, finished_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    outcome_id,
                    member_id,
                    target_id,
                    int(base_seq),
                    status,
                    reason,
                    started_at or now,
                    finished_at or now,
                    1 if consumed_budget else 0,
                    1 if merged else 0,
                    reply_seq,
                    finished_reason,
                ),
            )
        return {
            "id": outcome_id,
            "member_id": member_id,
            "target_id": target_id,
            "base_seq": int(base_seq),
            "status": status,
            "reason": reason,
            "started_at": started_at or now,
            "finished_at": finished_at or now,
            "consumed_budget": bool(consumed_budget),
            "merged": bool(merged),
            "reply_seq": reply_seq,
            "finished_reason": finished_reason,
        }

    def list_round_outcomes(self, target_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        """按 ``finished_at`` 升序取该 target 的轮次结果（``after_seq`` 按 ``base_seq`` 过滤）。"""
        self.get_target(target_id)  # 404 早退
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM round_outcome WHERE target_id = ? AND base_seq >= ? ORDER BY finished_at, id",
                (target_id, after_seq),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "member_id": row["member_id"],
                "target_id": row["target_id"],
                "base_seq": row["base_seq"],
                "status": row["status"],
                "reason": row["reason"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "consumed_budget": bool(row["consumed_budget"]),
                "merged": bool(row["merged"]),
                "reply_seq": row["reply_seq"],
                "finished_reason": row["finished_reason"],
            }
            for row in rows
        ]

    # ─── target（消息作用域的正本） ───────────────────────────────────────
    def get_target(self, target_id: str) -> dict[str, Any]:
        """取一个 target 行；不存在则 404。**不按 kind 分支**（ADR-0004 决策 8）。

        它是所有**目标作用域**读写的 404 早退点：``message`` / ``target_read`` / ``target_budget`` /
        ``round_outcome`` 四张表的外键都指向这里，因此'键存在'就是'作用域可用'。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT id, kind, created_at FROM target WHERE id = ?", (target_id,)
            ).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.target_not_found", target_id=target_id))
        return {"id": row["id"], "kind": row["kind"], "created_at": row["created_at"]}

    # ─── 任务 ─────────────────────────────────────────────────────────────
    def create_task(self, channel_id: str, title: str) -> dict[str, Any]:
        clean_title = (title or "").strip()
        if not clean_title:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.task_title_empty"),
                fields={"title": self._render_copy("error.task_title_empty")},
            )
        self.get_channel(channel_id)  # 必须恰属于一个存在的 channel 型 target

        task_id = new_id("task")
        target_id = new_id("tgt")
        now = utc_now()
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "INSERT INTO target(id, kind, created_at) VALUES (?, 'task', ?)", (target_id, now)
                )
                self._conn.execute(
                    "INSERT INTO task(id, target_id, channel_id, title, status, assignee_member_id, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, 'todo', NULL, ?, ?)",
                    (task_id, target_id, channel_id, clean_title, now, now),
                )
                self._conn.execute(
                    "INSERT INTO target_member(target_id, member_id) VALUES (?, ?)", (target_id, OWNER_ID)
                )
                self._conn.execute(
                    "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES (?, ?, 0)",
                    (OWNER_ID, target_id),
                )
                self._conn.execute(
                    "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at, "
                    "mention_snapshot) VALUES (?, ?, 1, ?, 'system', ?, ?, '[]')",
                    (
                        new_id("msg"),
                        target_id,
                        OWNER_ID,
                        self._render_copy("system.task.created", title=clean_title),
                        now,
                    ),
                )
                # 频道流里的可进入任务线程的入口（#198 / ADR-0028）：与任务行、任务线程首条记录
                # **同一个事务**。任一插入失败都整体回滚，于是不存在「任务在、频道指针不在」的
                # 半成品——这正是「建任务 = 一处动作」在库层面的构造性保证。
                self._append_task_pointer_locked(task_id, channel_id, clean_title, now)
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_task(task_id)

    def _append_task_pointer_locked(self, task_id: str, channel_id: str, title: str, now: str) -> None:
        """在**已开着的事务内**向频道 target 落一条 ``system`` 指针消息（#198 / ADR-0028）。

        正文与任务线程首条记录**逐字一致**（同一个 ``system.task.created`` 词条、同一个标题），
        作者仍是 owner：系统事件消息的既有口径不改。``task_pointer`` 是它与任务之间唯一的连接，
        ``task_ref`` 保持 ``NULL``——两条引用字段的语义不混用。

        写形状复用 :meth:`_append_target_message_locked`；这个方法保留为**具名的一步**：它是
        「建任务 = 频道入口」这条意图的落点，也是原子性用例的注入点。
        """
        self._append_target_message_locked(
            channel_id,
            self._render_copy("system.task.created", title=title),
            now,
            task_pointer=task_id,
        )

    def task_pointer_message(self, task_id: str) -> dict[str, Any] | None:
        """该任务的**频道指针消息**（#198 / ADR-0028）；没有时返回 ``None``。

        「没有」是合法读数：本版之前的任务不补历史（只前进、不回填），因此读路径必须能用
        ``None`` 如实表达它，而不是伪造一条。
        """
        with self._lock:
            row = self._conn.execute(
                """
                SELECT m.*, a.kind AS author_kind, a.display_name AS author_name,
                       a.avatar_slot AS author_avatar_slot
                  FROM message m JOIN member a ON a.id = m.author_member_id
                 WHERE m.task_pointer = ?
                """,
                (task_id,),
            ).fetchone()
        return None if row is None else self._message_payload(row)

    def get_task(self, task_id: str, member_id: str = OWNER_ID) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT t.*, c.name AS channel_name, "
                "(SELECT COUNT(*) FROM message m WHERE m.target_id = t.target_id) AS message_count "
                "FROM task t JOIN channel c ON c.id = t.channel_id WHERE t.id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.task_not_found", task_id=task_id))
        task = {
            "id": row["id"],
            "target_id": row["target_id"],
            "channel_id": row["channel_id"],
            "channel_name": row["channel_name"],
            "title": row["title"],
            "status": row["status"],
            "assignee_member_id": row["assignee_member_id"],
            "claimed_at": row["claimed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            # 任务线程的消息总条数（#198）：频道 chip 的「进入任务线程 · N 条」读它。
            # 含 system / human / agent 三类，**不是**未读数（未读另有 ``unread_count``）。
            "message_count": int(row["message_count"]),
        }
        # 合法迁移集合随载荷出行（#191）：从同一张表派生，前端只读它、不持正本。
        task["allowed_transitions"] = allowed_task_transitions(task["status"])
        task["unread_count"] = self.unread_count(task["target_id"], member_id)
        return task

    def list_tasks(self, channel_id: str | None = None, member_id: str = OWNER_ID) -> list[dict[str, Any]]:
        if channel_id is not None:
            self.get_channel(channel_id)  # 非法筛选频道明确 404，不能伪装成空结果
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM task WHERE (:channel_id IS NULL OR channel_id = :channel_id) "
                "ORDER BY created_at, id",
                {"channel_id": channel_id},
            ).fetchall()
        return [self.get_task(row["id"], member_id) for row in rows]

    def task_for_target(self, target_id: str) -> dict[str, Any] | None:
        """取 task 型 target 对应的任务；channel / DM 型 target 返回 None。"""
        target = self.get_target(target_id)
        if target["kind"] != "task":
            return None
        with self._lock:
            row = self._conn.execute("SELECT id FROM task WHERE target_id = ?", (target_id,)).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.task_missing_for_target", target_id=target_id))
        return self.get_task(str(row["id"]))

    def list_task_messages(self, task_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        task = self.get_task(task_id)  # task 面只认任务，不把任意 target 当成任务线程
        return self.list_messages(task["target_id"], after_seq=after_seq)

    def _append_target_message_locked(
        self, target_id: str, body: str, now: str, *, task_pointer: str | None = None
    ) -> int:
        """在**已开着的事务内**分配 target 内单调 seq，并落一条 ``system`` 消息（作者恒为 owner）。

        任务面的四处状态推进、提醒的到点投递与建任务的频道指针共用这一条写形状：它不 ``begin`` /
        ``commit``（调用方持有事务边界），因此「落消息 + 同一个事务里的其它写」在构造上原子。
        ``task_pointer`` 只有建任务的频道入口会传（#198 / ADR-0028），其余调用点保持 ``NULL``。
        """
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM message WHERE target_id = ?", (target_id,)
        ).fetchone()
        seq = int(row["next_seq"])
        self._conn.execute(
            "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at, "
            "mention_snapshot, task_pointer) VALUES (?, ?, ?, ?, 'system', ?, ?, '[]', ?)",
            (new_id("msg"), target_id, seq, OWNER_ID, body, now, task_pointer),
        )
        return seq

    def update_task_status(
        self, task_id: str, status: str, *, actor_member_id: str = OWNER_ID
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if status not in TASK_STATUSES:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.unknown_task_status", status=status),
                fields={"status": self._render_copy("error.field.task_status_invalid")},
            )
        actor = self.get_member(actor_member_id)
        self.get_task(task_id)  # 404 早退
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT target_id, status, assignee_member_id FROM task WHERE id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(self._render_copy("error.task_not_found", task_id=task_id))
                current = str(row["status"])
                target_id = str(row["target_id"])
                if status == current:
                    self._commit()
                    return self.get_task(task_id), None
                if status not in TASK_STATUS_TRANSITIONS[current]:
                    raise DomainError(
                        "validation_failed",
                        self._render_copy("error.task_transition_invalid", current=current, target=status),
                        fields={
                            "status": self._render_copy(
                                "error.field.task_transition_invalid", current=current, target=status
                            )
                        },
                    )
                if (
                    current == "in_review"
                    and status == "done"
                    and actor["kind"] == "agent"
                    and row["assignee_member_id"] == actor_member_id
                ):
                    raise DomainError(
                        "validation_failed",
                        self._render_copy("error.task_self_approve"),
                        fields={"status": self._render_copy("error.field.task_self_approve")},
                    )
                now = utc_now()
                self._conn.execute(
                    "UPDATE task SET status = ?, updated_at = ? WHERE id = ?", (status, now, task_id)
                )
                seq = self._append_target_message_locked(
                    target_id,
                    self._render_copy(
                        "system.task.status", status=self._render_copy(f"status.task.{status}")
                    ),
                    now,
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_task(task_id), self.get_message(target_id, seq)

    def update_task_title(self, task_id: str, title: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        clean_title = (title or "").strip()
        if not clean_title:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.task_title_empty"),
                fields={"title": self._render_copy("error.task_title_empty")},
            )
        self.get_task(task_id)  # 404 早退
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT target_id, title FROM task WHERE id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(self._render_copy("error.task_not_found", task_id=task_id))
                target_id = str(row["target_id"])
                if clean_title == row["title"]:
                    self._commit()
                    return self.get_task(task_id), None
                now = utc_now()
                self._conn.execute(
                    "UPDATE task SET title = ?, updated_at = ? WHERE id = ?", (clean_title, now, task_id)
                )
                seq = self._append_target_message_locked(
                    target_id, self._render_copy("system.task.title", title=clean_title), now
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_task(task_id), self.get_message(target_id, seq)

    def update_task_assignee(
        self, task_id: str, member_id: str | None, *, actor_member_id: str = OWNER_ID
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        actor = self.get_member(actor_member_id)
        assignee: dict[str, Any] | None = None
        if member_id is not None:
            try:
                assignee = self.get_member(member_id)
            except NotFound as exc:
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.assignee_missing"),
                    fields={"member_id": self._render_copy("error.field.assignee_missing")},
                ) from exc
            if assignee["deleted_at"] is not None or assignee["kind"] != "agent":
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.assignee_not_agent"),
                    fields={"member_id": self._render_copy("error.field.assignee_not_agent")},
                )
        self.get_task(task_id)  # 404 早退
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT target_id, assignee_member_id FROM task WHERE id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(self._render_copy("error.task_not_found", task_id=task_id))
                target_id = str(row["target_id"])
                previous_id = row["assignee_member_id"]
                if actor["kind"] == "agent":
                    if actor["deleted_at"] is not None or previous_id != actor_member_id:
                        raise DomainError(
                            "validation_failed", self._render_copy("error.transfer_not_assignee")
                        )
                    if member_id is None:
                        raise DomainError(
                            "validation_failed", self._render_copy("error.transfer_needs_target")
                        )
                elif actor["kind"] != "owner":
                    raise DomainError("validation_failed", self._render_copy("error.reassign_not_allowed"))
                if member_id == previous_id:
                    self._commit()
                    return self.get_task(task_id), None
                now = utc_now()
                self._conn.execute(
                    "UPDATE task SET assignee_member_id = ?, updated_at = ? WHERE id = ?",
                    (member_id, now, task_id),
                )
                if previous_id is not None:
                    self._conn.execute(
                        "DELETE FROM target_member WHERE target_id = ? AND member_id = ?",
                        (target_id, previous_id),
                    )
                    self._conn.execute(
                        "DELETE FROM target_read WHERE target_id = ? AND member_id = ?",
                        (target_id, previous_id),
                    )
                if member_id is not None:
                    self._conn.execute(
                        "INSERT INTO target_member(target_id, member_id) VALUES (?, ?)",
                        (target_id, member_id),
                    )
                    self._conn.execute(
                        "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES (?, ?, 0)",
                        (member_id, target_id),
                    )
                label = (
                    self._render_copy("system.task.assignee", name=assignee["display_name"])
                    if assignee is not None
                    else self._render_copy("system.task.unassigned")
                )
                seq = self._append_target_message_locked(target_id, label, now)
                if member_id is not None and actor["kind"] == "owner":
                    # owner 改指派 = task 型 target 的一次开世代动作（ADR-0005）：世代标识是那条
                    # system 记录的 seq；预算用量重置为满额，并按当世代生效上界拍快照。
                    self._reset_epoch_budget_locked(target_id, seq)
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_task(task_id), self.get_message(target_id, seq)

    def claim_task(self, task_id: str, member_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """**认领**写路径（`CONTEXT.md` **认领** / ADR-0010 / ADR-0016）：把一张**尚未指派**的任务指派给认领者
        自己，并**在同一个事务里开工**。

        它与 :meth:`update_task_assignee` 是**两条不同的写路径**，不是同一端点的另一种用法：那条要求
        ``previous_id == actor_member_id``（Agent 只能转派自己手上的任务），因此**直接拒绝** Agent 认领
        未指派任务。认领的裁定在**编排**（谁认领、开不开开关、可不可用），这里落它后的**全部七件事**：

        1. ``assignee_member_id`` / ``claimed_at`` / ``updated_at``；
        2. 认领者进入该任务 target 的 **target 成员**（与 owner 指派同一张表、同一个键）；
        3. 一条可读的 ``system`` 记录「已认领」（**认领不是跳**：``system`` 记录不是对话回合，不触发通用唤醒）；
        4. **首次认领开一次世代**：``epoch_seq`` = 那条「已认领」记录的 seq，``used`` 重置为 0
           （按当世代生效的**回应预算上界**给回满额）；
        5. **状态推进** ``todo → in_progress``（ADR-0016 二：认领是**开工动作**）；
        6. 第二条 ``system`` 记录「认领后自动开工」（**新键** ``system.task.claim_started``，``seq`` 在「已认领」那条之后）；
        7. 唤醒认领者由调用方走既有的 ``notify_task_assignment``（那条路径**不消耗跳数**）。

        **任一步失败 ⇒ 全部零改动**（同一个 ``try/except`` 的 ``_rollback`` 兜住）。

        **状态推进的合法性复用迁移表、不复用 actor 校验**（ADR-0016 三）：目标状态取
        ``TASK_STATUS_TRANSITIONS["todo"]`` 里那一个可开工的迁移，而不是硬写一个字面量；又因为认领的
        授权来自**编排**（配置层裁决）而不是成员身份，这里**不经** :meth:`update_task_status`
        （那条路要求 actor 是存在的成员，并在 ``in_review → done`` 里拒绝受派者自我批准）。
        ``system`` 记录的 ``author`` 恒为 ``OWNER_ID``：那是**系统事件消息**的既有口径，**不表示人推的**；
        「人推的」与「编排替它开工」的区别**只在词条键**。

        **每张任务至多一次**由 ``task.claimed_at`` 这条构造性保证封住——不靠界面不给按钮，也不靠
        「任务恰好还指着某个指派者」：认领只在 ``claimed_at IS NULL`` 时成立，因此任务之后被取消指派
        也不会再触发第二次世代推进与预算重置（这正是 ADR-0002 三跳上界仍然可执行的前提）。

        不成立时是**幂等无操作**：返回 ``(task, [])``——空列表表示**本次没落任何消息**（不推进世代、
        不改成员、不改状态），与 :meth:`update_task_assignee` 遇到「指派没变」时的收口同形。成功时第二个
        元素是**本次事务按 seq 升序**新增的 ``system`` 消息（两条）——一次写事务可以产出多条消息，返回值
        如实说这件事，调用方才能把两条都推给订阅者（补拉与实时视图不分叉）。
        """
        agent = self.get_agent(member_id)  # 存在 + 「必须是 Agent」：认领者只能是一个活着的 Agent
        if agent.get("deleted_at") is not None:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.assignee_not_agent"),
                fields={"member_id": self._render_copy("error.field.assignee_not_agent")},
            )
        self.get_task(task_id)  # 404 早退
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT target_id, assignee_member_id, claimed_at FROM task WHERE id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(self._render_copy("error.task_not_found", task_id=task_id))
                target_id = str(row["target_id"])
                if row["claimed_at"] is not None or row["assignee_member_id"] is not None:
                    self._commit()
                    return self.get_task(task_id), []
                now = utc_now()
                self._conn.execute(
                    "UPDATE task SET assignee_member_id = ?, claimed_at = ?, updated_at = ? WHERE id = ?",
                    (member_id, now, now, task_id),
                )
                self._conn.execute(
                    "INSERT INTO target_member(target_id, member_id) VALUES (?, ?) "
                    "ON CONFLICT(target_id, member_id) DO NOTHING",
                    (target_id, member_id),
                )
                self._conn.execute(
                    "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES (?, ?, 0) "
                    "ON CONFLICT(member_id, target_id) DO NOTHING",
                    (member_id, target_id),
                )
                seq = self._append_target_message_locked(
                    target_id,
                    self._render_copy("system.task.claimed", name=agent["display_name"]),
                    now,
                )
                # 首次认领 = 该 task target 的一次开世代动作（ADR-0010 三）：世代标识是那条 system
                # 记录的 seq（与 owner 指派同一个落点），用量按当世代生效的上界重置为满额。
                self._reset_epoch_budget_locked(target_id, seq)
                # 认领即开工（ADR-0016 二）：同一个事务里推进状态，并落**第二条**记录。
                # 记录② 的 seq 在记录① 之后，**不参与开世代**（``epoch_seq`` 仍是上面那个 seq）。
                started_seq = self._claim_start_status(task_id, target_id, now)
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_task(task_id), [
            self.get_message(target_id, seq),
            self.get_message(target_id, started_seq),
        ]

    def _claim_start_status(self, task_id: str, target_id: str, now: str) -> int:
        """认领事务内部的两件开工动作：推状态 + 落「认领后自动开工」记录（返回它的 seq）。

        **复用迁移表做合法性判定**：目标状态 ``CLAIM_START_STATUS`` 必须落在
        ``TASK_STATUS_TRANSITIONS["todo"]`` 里；将来收紧那张表时，认领会随之**失败**而不是静默越界。
        **不复用** :meth:`update_task_status` 的 actor 校验：认领的授权来自**编排**（配置层裁决），
        不来自成员身份；这里也不嵌套第二个事务，否则「单事务开工」（ADR-0016 二）当场破。

        只供 :meth:`claim_task` 在**已开着的事务内**调用；它自己不 ``begin`` / ``commit``。
        """
        if CLAIM_START_STATUS not in TASK_STATUS_TRANSITIONS["todo"]:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.task_transition_invalid", current="todo", target=CLAIM_START_STATUS),
                fields={
                    "status": self._render_copy(
                        "error.field.task_transition_invalid", current="todo", target=CLAIM_START_STATUS
                    )
                },
            )
        self._conn.execute(
            "UPDATE task SET status = ?, updated_at = ? WHERE id = ?",
            (CLAIM_START_STATUS, now, task_id),
        )
        return self._append_target_message_locked(
            target_id,
            self._render_copy(
                "system.task.claim_started",
                status=self._render_copy(f"status.task.{CLAIM_START_STATUS}"),
            ),
            now,
        )

    def create_target(self, kind: str, target_id: str | None = None) -> dict[str, Any]:
        """落一个新的 **dm / task 型** target 行。

        **本方法没有生产调用点**：它只插一行 target，不做 dm / task 业务校验；业务创建分别走
        :meth:`create_dm` / :meth:`create_task` 的完整写路径。这里只供
        `app/tests/test_target_scope.py` 构造作用域数据、取证共享 target 结构。

        **channel 型不经此处**：它复用 ``channel.id``、由 :meth:`create_channel` 落影子行，
        这里显式拒绝，以免出现'有 target 行却没有对应频道行'的破坏不变量的中间态。
        """
        if kind not in TARGET_KINDS:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.unknown_target_kind", kind=kind),
                fields={"kind": self._render_copy("error.field.target_kind_invalid")},
            )
        if kind == "channel":
            raise DomainError(
                "validation_failed",
                self._render_copy("error.channel_target_explicit"),
                fields={"kind": self._render_copy("error.field.channel_target_explicit")},
            )
        new_target_id = target_id or new_id("tgt")
        with self._lock:
            self._conn.execute(
                "INSERT INTO target(id, kind, created_at) VALUES (?, ?, ?)",
                (new_target_id, kind, utc_now()),
            )
        return self.get_target(new_target_id)

    def unread_total(self, member_id: str = OWNER_ID) -> int:
        """工作台快照的总未读 = **channel 型 + dm 型**之和（决策 G5）；**task 型不进它**。

        它用的是与频道列表**同一条未读 WHERE 形状**（``UNREAD_WHERE_SQL`` 这个单一文本落点），
        因此「未读计数只有一个 SQL 形状」是结构上的事实：汇总只多一层 ``kind IN ('channel','dm')``
        过滤，不是第二套计数口径。
        """
        with self._lock:
            row = self._conn.execute(UNREAD_TOTAL_SQL, {"member_id": member_id}).fetchone()
        return int(row[0]) if row is not None else 0

    # ─── 频道（channel 型 target 的呈现面：name / description 仍留在 channel 表） ──
    def list_channels(self, member_id: str = OWNER_ID) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                CHANNEL_SELECT_SQL, {"member_id": member_id, "channel_id": None}
            ).fetchall()
        return [self._channel_payload(row) for row in rows]

    def get_channel(self, channel_id: str, member_id: str = OWNER_ID) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                CHANNEL_SELECT_SQL, {"member_id": member_id, "channel_id": channel_id}
            ).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.channel_not_found", channel_id=channel_id))
        return self._channel_payload(row)

    def workspace_settings_for_target(self, target_id: str, member_id: str) -> dict[str, Any] | None:
        """把 target 解析到频道的 workspace 配置，并叠加 Agent 级覆盖（#148 的装配读路径）。

        返回 ``None`` 表示**未配置**（频道没有 ``workspace_root`` / 不是 channel 或 task target /
        target 不存在）——调用方据此保持「没有文件与 shell 工具」的既有行为（ADR-0021 §六）。
        本方法只读配置；写入仍由频道配置端点（#142）负责。
        """
        with self._lock:
            target = self._conn.execute("SELECT kind FROM target WHERE id = ?", (target_id,)).fetchone()
            if target is None:
                return None
            if target["kind"] == "channel":
                channel_id = target_id
            elif target["kind"] == "task":
                task = self._conn.execute(
                    "SELECT channel_id FROM task WHERE target_id = ?", (target_id,)
                ).fetchone()
                if task is None:
                    return None
                channel_id = str(task["channel_id"])
            else:  # DM 没有项目根
                return None

            channel = self._conn.execute(
                "SELECT workspace_root, workspace_backend, permission_mode, docker_egress "
                "FROM channel WHERE id = ?",
                (channel_id,),
            ).fetchone()
            if channel is None or not str(channel["workspace_root"] or "").strip():
                return None
            agent = self._conn.execute(
                "SELECT workspace_backend, permission_mode, allow_local_shell, docker_image "
                "FROM agent_config WHERE member_id = ?",
                (member_id,),
            ).fetchone()
            docker_image = str(agent["docker_image"]) if agent is not None and agent["docker_image"] else None
            consent = (
                self._conn.execute(
                    "SELECT 1 FROM remote_consent_scope WHERE scope_kind = 'docker' AND scope_id = ?",
                    (docker_image,),
                ).fetchone()
                if docker_image
                else None
            )

        return {
            "root": str(channel["workspace_root"]),
            "backend": str(
                (agent["workspace_backend"] if agent is not None else None)
                or channel["workspace_backend"]
                or "local"
            ),
            "permission_mode": str(
                (agent["permission_mode"] if agent is not None else None)
                or channel["permission_mode"]
                or "ask-writes"
            ),
            "allow_local_shell": bool(agent["allow_local_shell"]) if agent is not None else False,
            "docker_image": docker_image,
            # 远端镜像确认是**配置事实**：装配层只读，未确认时不拉取、不启动容器。
            "docker_image_consented": consent is not None,
            # 容器出网开关（#187）：频道级事实，Agent 没有覆盖列（见 issue-187 验收文档）。
            "docker_egress": bool(channel["docker_egress"]),
        }

    def permission_settings_for_target(self, target_id: str, member_id: str) -> dict[str, Any] | None:
        """解析一轮的权限档与规则（频道级 + Agent 级原样返回，合并由 ``permissions`` 负责）。

        与 :meth:`workspace_settings_for_target` 同一 target→channel 解析口径；DM / 未知 target
        返回 ``None``。规则只读配置，不落消息、不唤醒。
        """
        with self._lock:
            channel_id = self._channel_id_for_target_locked(target_id)
            if channel_id is None:
                return None
            channel = self._conn.execute(
                "SELECT workspace_root, permission_mode FROM channel WHERE id = ?",
                (channel_id,),
            ).fetchone()
            if channel is None:
                return None
            agent = self._conn.execute(
                "SELECT permission_mode FROM agent_config WHERE member_id = ?",
                (member_id,),
            ).fetchone()
            channel_rules = self._permission_rule_rows_locked("channel", channel_id)
            agent_rules = self._permission_rule_rows_locked("agent", member_id)
        return {
            "mode": str(
                (agent["permission_mode"] if agent is not None else None)
                or channel["permission_mode"]
                or "ask-writes"
            ),
            "root": str(channel["workspace_root"]) if channel["workspace_root"] else None,
            "channel_rules": channel_rules,
            "agent_rules": agent_rules,
        }

    def web_settings_for_target(self, target_id: str, member_id: str) -> dict[str, Any] | None:
        """联网工具（#186）的装配读路径：``web_access``、权限档与已确认的 web provider。

        与 :meth:`permission_settings_for_target` 同一 target→channel 解析口径；未知 target
        返回 ``None``。**DM 没有频道**（#202）：它只读 ``agent_config``，读数形状与频道分支一致
        ——``web_access`` = ``agent_config.web_access or 'search'``、``mode`` 固定 ``'read-only'``
        （没有权限档与审批面，``fetch`` 因此永不装配）、``backend='local'`` /
        ``docker_egress=False`` / ``egress_hosts=[]``（没有频道 workspace）。

        ``web_access`` 是一个**独立**配置位（ADR-0026 §二：与 workspace 同级但不是同一个开关），
        因此这条读路径**不经过** :meth:`workspace_settings_for_target`——频道没配项目根时它也生效。
        频道 / 任务分支是三态合并 ``agent → channel → 'search'``；``'off'`` 在两条分支里都归一成
        ``None``（装配层据此不挂工具）。``consented_providers`` 是
        ``remote_consent_scope('web', provider)`` 里已确认的那些行；``credentials`` /
        ``default_provider`` 是凭据表的**内部**读数（明文只给工具发请求用）。
        本方法只读配置：不落消息、不唤醒，也不改动库。
        """
        with self._lock:
            target = self._conn.execute("SELECT kind FROM target WHERE id = ?", (target_id,)).fetchone()
            if target is None:
                return None
            if target["kind"] == "dm":
                return self._dm_web_settings_locked(member_id)
            channel_id = self._channel_id_for_target_locked(target_id)
            if channel_id is None:
                return None
            channel = self._conn.execute(
                "SELECT web_access, permission_mode, workspace_backend, docker_egress "
                "FROM channel WHERE id = ?",
                (channel_id,),
            ).fetchone()
            if channel is None:
                return None
            agent = self._conn.execute(
                "SELECT web_access, permission_mode, workspace_backend FROM agent_config WHERE member_id = ?",
                (member_id,),
            ).fetchone()
            web = self._web_shared_settings_locked()
            # D3：容器出网白名单复用 ``permission_rule`` 里 ``fetch`` 的 allow 规则的 host，
            # **不新建 host 白名单表**；合并仍走 ``permissions.effective_rules``（Agent 级覆盖频道级）。
            allowed_hosts = egress_hosts(
                effective_rules(
                    self._permission_rule_rows_locked("channel", channel_id),
                    self._permission_rule_rows_locked("agent", member_id),
                )
            )
        channel_web_access = channel["web_access"]
        agent_web_access = agent["web_access"] if agent is not None else None
        # 三态合并（2026-10-20）：Agent 覆盖 → 频道 → 默认档。``NULL`` = 未配置 = 默认 ``search``；
        # 只有显式 ``off`` 才关闭。装配层仍用 ``if not access: return []``，因此这里把 ``off``
        # 归一成 ``None``——「关闭」与「未注入读路径」在装配层是同一个方向。
        resolved_access = agent_web_access or channel_web_access or DEFAULT_WEB_ACCESS
        return {
            "web_access": None if resolved_access == "off" else resolved_access,
            "mode": str(
                (agent["permission_mode"] if agent is not None else None)
                or channel["permission_mode"]
                or "ask-writes"
            ),
            "consented_providers": web["consented_providers"],
            # 凭据只给装配层：HTTP 载荷一律走 ``web_provider_credential_views`` 的掩码读数。
            "credentials": web["credentials"],
            "default_provider": web["default_provider"],
            # 容器出网策略（#187）：只有 ``backend == 'docker'`` 时才进工具闸门；本地后端逐字不变。
            "backend": str(
                (agent["workspace_backend"] if agent is not None else None)
                or channel["workspace_backend"]
                or "local"
            ),
            "docker_egress": bool(channel["docker_egress"]),
            "egress_hosts": allowed_hosts,
        }

    def _dm_web_settings_locked(self, member_id: str) -> dict[str, Any]:
        """DM 的联网档读数（#202）：只读 ``agent_config``，形状与频道分支一致。

        DM 没有频道，也就没有频道级默认值：``web_access`` 只取 Agent 级配置，未配置回退默认档
        ``'search'``、显式 ``'off'`` 归一成 ``None``（与频道分支同一归一）。DM 也没有权限档与
        审批面，因此 ``mode`` 固定 ``'read-only'``——装配层 ``FETCH_ACCESS_LEVEL and
        mode != "read-only"`` 的闸门据此永不装 ``fetch``。``backend='local'`` /
        ``docker_egress=False`` / ``egress_hosts=[]`` 是「没有频道 workspace」的同形状读数。

        调用方已持有 ``self._lock``（与频道分支整段读在同一把锁里）。
        """
        agent = self._conn.execute(
            "SELECT web_access FROM agent_config WHERE member_id = ?", (member_id,)
        ).fetchone()
        web = self._web_shared_settings_locked()
        agent_web_access = agent["web_access"] if agent is not None else None
        # 与频道分支同一归一：``NULL`` = 未配置 = 默认档 ``search``；只有显式 ``off`` 才关闭。
        resolved_access = agent_web_access or DEFAULT_WEB_ACCESS
        return {
            "web_access": None if resolved_access == "off" else resolved_access,
            "mode": "read-only",
            "consented_providers": web["consented_providers"],
            # 凭据只给装配层：HTTP 载荷一律走 ``web_provider_credential_views`` 的掩码读数。
            "credentials": web["credentials"],
            "default_provider": web["default_provider"],
            "backend": "local",
            "docker_egress": False,
            "egress_hosts": [],
        }

    def _web_shared_settings_locked(self) -> dict[str, Any]:
        """联网工具两条读路径共享的三样读数（#186 / #202）。

        来源只有一处：``remote_consent_scope('web', provider)`` 的已确认行与
        ``web_provider_credentials()`` 的明文凭据 / 默认 provider。频道 / 任务分支与 DM 分支都
        调用它——「DM 的凭据与频道分支同一来源」因此由同一条语句保证，而不是靠两份逐字相同的
        SQL 维持。调用方已持有 ``self._lock``。
        """
        consented = self._conn.execute(
            "SELECT scope_id FROM remote_consent_scope WHERE scope_kind = 'web' "
            "ORDER BY acknowledged_at, scope_id"
        ).fetchall()
        runtime_credentials = self.web_provider_credentials()
        return {
            "consented_providers": tuple(str(row["scope_id"]) for row in consented),
            "credentials": dict(runtime_credentials["credentials"]),
            "default_provider": runtime_credentials["default_provider"],
        }

    def _channel_id_for_target_locked(self, target_id: str) -> str | None:
        """target → 频道 id（channel 型同值；task 型经 ``task.channel_id``；DM 无频道）。"""
        target = self._conn.execute("SELECT kind FROM target WHERE id = ?", (target_id,)).fetchone()
        if target is None:
            return None
        if target["kind"] == "channel":
            return target_id
        if target["kind"] == "task":
            task = self._conn.execute(
                "SELECT channel_id FROM task WHERE target_id = ?", (target_id,)
            ).fetchone()
            return str(task["channel_id"]) if task is not None else None
        return None

    # ─── 权限规则（配置；不落消息、不唤醒） ───────────────────────────────
    def _permission_rule_rows_locked(self, scope_kind: str, scope_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM permission_rule WHERE scope_kind = ? AND scope_id = ? ORDER BY created_at, id",
            (scope_kind, scope_id),
        ).fetchall()
        return [self._permission_rule_payload(row) for row in rows]

    @staticmethod
    def _permission_rule_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "scope_kind": str(row["scope_kind"]),
            "scope_id": str(row["scope_id"]),
            "tool_name": str(row["tool_name"]),
            "pattern": str(row["pattern"]),
            "behavior": str(row["behavior"]),
            "created_at": str(row["created_at"]),
        }

    def list_permission_rules(
        self, scope_kind: str | None = None, scope_id: str | None = None
    ) -> list[dict[str, Any]]:
        """按 scope 过滤的规则列表；不给 scope 时返回全部（只读）。"""
        clauses: list[str] = []
        params: list[Any] = []
        if scope_kind is not None:
            clauses.append("scope_kind = ?")
            params.append(scope_kind)
        if scope_id is not None:
            clauses.append("scope_id = ?")
            params.append(scope_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM permission_rule{where} ORDER BY created_at, id", params
            ).fetchall()
        return [self._permission_rule_payload(row) for row in rows]

    def create_permission_rule(
        self,
        *,
        scope_kind: str,
        scope_id: str,
        tool_name: str,
        pattern: str = "",
        behavior: str,
    ) -> dict[str, Any]:
        """写一条规则；同一 ``(scope, tool_name, pattern)`` 视为**覆盖**（先删后插）。

        规则是配置：不落消息、不唤醒、不消耗预算。行为只接受 ``allow`` / ``deny`` / ``ask``。
        """
        clean_tool = str(tool_name or "").strip()
        clean_pattern = str(pattern or "")
        clean_behavior = str(behavior or "").strip()
        if scope_kind not in ("channel", "agent"):
            raise DomainError(
                "validation_failed",
                self._render_copy("error.permission_rule_scope_invalid"),
            )
        if not clean_tool:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.permission_rule_tool_empty"),
            )
        if clean_behavior not in ("allow", "deny", "ask"):
            raise DomainError(
                "validation_failed",
                self._render_copy("error.permission_rule_behavior_invalid"),
            )
        with self._lock:
            if scope_kind == "channel":
                exists = self._conn.execute("SELECT 1 FROM channel WHERE id = ?", (scope_id,)).fetchone()
            else:
                exists = self._conn.execute(
                    "SELECT 1 FROM member WHERE id = ? AND kind = 'agent' AND deleted_at IS NULL",
                    (scope_id,),
                ).fetchone()
            if exists is None:
                raise NotFound(self._render_copy("error.permission_rule_scope_unknown", scope_id=scope_id))
            self._conn.execute(
                "DELETE FROM permission_rule WHERE scope_kind = ? AND scope_id = ? "
                "AND tool_name = ? AND pattern = ?",
                (scope_kind, scope_id, clean_tool, clean_pattern),
            )
            rule_id = new_id("prm")
            self._conn.execute(
                "INSERT INTO permission_rule(id, scope_kind, scope_id, tool_name, pattern, "
                "behavior, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    rule_id,
                    scope_kind,
                    scope_id,
                    clean_tool,
                    clean_pattern,
                    clean_behavior,
                    utc_now(),
                ),
            )
            row = self._conn.execute("SELECT * FROM permission_rule WHERE id = ?", (rule_id,)).fetchone()
        return self._permission_rule_payload(row)

    def delete_permission_rule(self, rule_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM permission_rule WHERE id = ?", (rule_id,)).fetchone()
            if row is None:
                raise NotFound(self._render_copy("error.permission_rule_not_found", rule_id=rule_id))
            self._conn.execute("DELETE FROM permission_rule WHERE id = ?", (rule_id,))
        return self._permission_rule_payload(row)

    # ─── MCP 声明（配置；不落消息、不唤醒、不消耗预算） ─────────────────────
    #
    # 库里只有声明：transport / command / args / url / ``env_names``。值只从进程环境读，
    # 本层永远不会接受、回显或写入 env 值（ADR-0021 §三、§五）。
    _MCP_SELECT = (
        "SELECT ms.*, EXISTS("
        "SELECT 1 FROM remote_consent_scope rcs "
        "WHERE rcs.scope_kind = 'mcp' AND rcs.scope_id = ms.id"
        ") AS consented FROM mcp_server ms"
    )

    @staticmethod
    def _mcp_server_payload(row: sqlite3.Row) -> dict[str, Any]:
        transport = str(row["transport"])
        is_remote = transport in ("http", "sse")
        raw_args = json.loads(row["args"] or "[]")
        raw_env_names = json.loads(row["env_names"] or "[]")
        return {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "transport": transport,
            "command": str(row["command"]),
            "args": [str(item) for item in raw_args] if isinstance(raw_args, list) else [],
            "url": str(row["url"]),
            "env_names": [str(item) for item in raw_env_names] if isinstance(raw_env_names, list) else [],
            "enabled": bool(row["enabled"]),
            "scope_kind": str(row["scope_kind"]),
            "scope_id": str(row["scope_id"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "is_remote": is_remote,
            "consent_required": is_remote,
            "consented": bool(row["consented"]) if is_remote else False,
        }

    def _assert_mcp_scope_locked(self, scope_kind: str, scope_id: str) -> None:
        """与权限规则同口径：channel 必须存在；agent 必须是未删除的 Agent。"""
        if scope_kind not in ("channel", "agent"):
            raise DomainError(
                "validation_failed",
                self._render_copy("error.mcp_scope_invalid"),
                fields={"scope_kind": self._render_copy("error.mcp_scope_invalid")},
            )
        if scope_kind == "channel":
            exists = self._conn.execute("SELECT 1 FROM channel WHERE id = ?", (scope_id,)).fetchone()
        else:
            exists = self._conn.execute(
                "SELECT 1 FROM member WHERE id = ? AND kind = 'agent' AND deleted_at IS NULL",
                (scope_id,),
            ).fetchone()
        if exists is None:
            message = self._render_copy("error.mcp_scope_unknown", scope_id=scope_id)
            raise DomainError("validation_failed", message, fields={"scope_id": message})

    def _normalize_mcp_declaration(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """校验并收敛一条声明的**完整**形态（创建与部分更新共用）。"""
        name = str(values.get("name") or "").strip()
        if not MCP_SERVER_NAME_PATTERN.fullmatch(name):
            message = self._render_copy("error.mcp_name_invalid")
            raise DomainError("validation_failed", message, fields={"name": message})

        transport = str(values.get("transport") or "").strip()
        if transport not in ("stdio", "http", "sse"):
            message = self._render_copy("error.mcp_transport_invalid")
            raise DomainError("validation_failed", message, fields={"transport": message})

        raw_args = values.get("args")
        if raw_args is None:
            raw_args = []
        if not isinstance(raw_args, (list, tuple)) or any(not isinstance(item, str) for item in raw_args):
            message = self._render_copy("error.mcp_args_invalid")
            raise DomainError("validation_failed", message, fields={"args": message})
        args = list(raw_args)

        raw_env_names = values.get("env_names")
        if raw_env_names is None:
            raw_env_names = []
        if not isinstance(raw_env_names, (list, tuple)):
            message = self._render_copy("error.mcp_env_names_invalid")
            raise DomainError("validation_failed", message, fields={"env_names": message})
        env_names: list[str] = []
        for item in raw_env_names:
            if not isinstance(item, str):
                message = self._render_copy("error.mcp_env_names_invalid")
                raise DomainError("validation_failed", message, fields={"env_names": message})
            clean = item.strip()
            if not MCP_ENV_NAME_PATTERN.fullmatch(clean):
                message = self._render_copy("error.mcp_env_names_invalid")
                raise DomainError("validation_failed", message, fields={"env_names": message})
            if clean not in env_names:
                env_names.append(clean)

        command = str(values.get("command") or "").strip()
        url = str(values.get("url") or "").strip()
        if transport == "stdio":
            if not command:
                message = self._render_copy("error.mcp_command_required")
                raise DomainError("validation_failed", message, fields={"command": message})
            url = ""
        else:
            if not url:
                message = self._render_copy("error.mcp_url_required")
                raise DomainError("validation_failed", message, fields={"url": message})
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                message = self._render_copy("error.mcp_url_invalid")
                raise DomainError("validation_failed", message, fields={"url": message})
            looks_sse = parts.path.endswith("/sse") or parts.path.endswith("/messages/")
            if transport == "sse" and not looks_sse:
                message = self._render_copy("error.mcp_sse_url_invalid")
                raise DomainError("validation_failed", message, fields={"url": message})
            if transport == "http" and looks_sse:
                # AgentScope 按 URL path 选传输：http 声明指向 /sse 会被静默当成 sse。
                # 在写路径拒绝，避免「声明一种、实际另一种」。
                message = self._render_copy("error.mcp_http_url_invalid")
                raise DomainError("validation_failed", message, fields={"url": message})
            command = ""

        return {
            "name": name,
            "transport": transport,
            "command": command,
            "args": args,
            "url": url,
            "env_names": env_names,
            "enabled": bool(values.get("enabled", True)),
        }

    def _assert_mcp_name_free_locked(
        self, *, scope_kind: str, scope_id: str, name: str, exclude_id: str | None = None
    ) -> None:
        row = self._conn.execute(
            "SELECT id FROM mcp_server WHERE scope_kind = ? AND scope_id = ? AND name = ?",
            (scope_kind, scope_id, name),
        ).fetchone()
        if row is not None and str(row["id"]) != (exclude_id or ""):
            message = self._render_copy("error.mcp_server_conflict", name=name)
            raise DomainError("mcp_server_conflict", message, status=409, fields={"name": message})

    @classmethod
    def _mcp_select_sql(cls, where: str = "") -> str:
        return f"{cls._MCP_SELECT}{where}"

    def list_mcp_servers(
        self, scope_kind: str | None = None, scope_id: str | None = None
    ) -> list[dict[str, Any]]:
        """按 scope 过滤的声明列表（只读；不含 env 值）。"""
        clauses: list[str] = []
        params: list[Any] = []
        if scope_kind is not None:
            clauses.append("ms.scope_kind = ?")
            params.append(scope_kind)
        if scope_id is not None:
            clauses.append("ms.scope_id = ?")
            params.append(scope_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                self._mcp_select_sql(f"{where} ORDER BY ms.created_at, ms.id"), params
            ).fetchall()
        return [self._mcp_server_payload(row) for row in rows]

    def get_mcp_server(self, server_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(self._mcp_select_sql(" WHERE ms.id = ?"), (server_id,)).fetchone()
        return self._mcp_server_payload(row) if row is not None else None

    def create_mcp_server(
        self,
        *,
        scope_kind: str,
        scope_id: str,
        name: str,
        transport: str,
        command: str = "",
        args: list[str] | None = None,
        url: str = "",
        env_names: list[str] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """写一条声明；同名（同一 scope）视为冲突，不静默覆盖。

        配置动作：不落消息、不唤醒、不消耗预算。
        """
        declaration = self._normalize_mcp_declaration(
            {
                "name": name,
                "transport": transport,
                "command": command,
                "args": args,
                "url": url,
                "env_names": env_names,
                "enabled": enabled,
            }
        )
        now = utc_now()
        server_id = new_id("mcp")
        with self._lock:
            self._assert_mcp_scope_locked(scope_kind, scope_id)
            self._assert_mcp_name_free_locked(
                scope_kind=scope_kind, scope_id=scope_id, name=declaration["name"]
            )
            self._conn.execute(
                "INSERT INTO mcp_server(id, name, transport, command, args, url, env_names, "
                "enabled, scope_kind, scope_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    server_id,
                    declaration["name"],
                    declaration["transport"],
                    declaration["command"],
                    json.dumps(declaration["args"], ensure_ascii=False),
                    declaration["url"],
                    json.dumps(declaration["env_names"], ensure_ascii=False),
                    1 if declaration["enabled"] else 0,
                    scope_kind,
                    scope_id,
                    now,
                    now,
                ),
            )
            row = self._conn.execute(self._mcp_select_sql(" WHERE ms.id = ?"), (server_id,)).fetchone()
        return self._mcp_server_payload(row)

    def update_mcp_server(self, server_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        """部分更新声明；scope 是身份的一部分，不可改。整条结果重新校验。"""
        allowed = {"name", "transport", "command", "args", "url", "env_names", "enabled"}
        unknown = set(changes) - allowed
        if unknown:
            message = self._render_copy("error.field_choices", values=", ".join(sorted(allowed)))
            raise DomainError(
                "validation_failed",
                message,
                fields=dict.fromkeys(unknown, message),
            )
        with self._lock:
            row = self._conn.execute(self._mcp_select_sql(" WHERE ms.id = ?"), (server_id,)).fetchone()
            if row is None:
                raise NotFound(self._render_copy("error.mcp_server_not_found", server_id=server_id))
            current = self._mcp_server_payload(row)
            merged = {**current, **dict(changes)}
            declaration = self._normalize_mcp_declaration(merged)
            self._assert_mcp_name_free_locked(
                scope_kind=current["scope_kind"],
                scope_id=current["scope_id"],
                name=declaration["name"],
                exclude_id=server_id,
            )
            self._conn.execute(
                "UPDATE mcp_server SET name = ?, transport = ?, command = ?, args = ?, url = ?, "
                "env_names = ?, enabled = ?, updated_at = ? WHERE id = ?",
                (
                    declaration["name"],
                    declaration["transport"],
                    declaration["command"],
                    json.dumps(declaration["args"], ensure_ascii=False),
                    declaration["url"],
                    json.dumps(declaration["env_names"], ensure_ascii=False),
                    1 if declaration["enabled"] else 0,
                    utc_now(),
                    server_id,
                ),
            )
            row = self._conn.execute(self._mcp_select_sql(" WHERE ms.id = ?"), (server_id,)).fetchone()
        return self._mcp_server_payload(row)

    def delete_mcp_server(self, server_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(self._mcp_select_sql(" WHERE ms.id = ?"), (server_id,)).fetchone()
            if row is None:
                raise NotFound(self._render_copy("error.mcp_server_not_found", server_id=server_id))
            self._conn.execute("DELETE FROM mcp_server WHERE id = ?", (server_id,))
        return self._mcp_server_payload(row)

    def record_mcp_consent(self, server_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """记录一次远端 MCP 的数据面确认（幂等；不落消息、不唤醒）。

        返回 ``(声明, 确认记录)``。stdio 不需要这条确认，调用即被拒绝，避免把「本机」误标成远端。
        """
        with self._lock:
            row = self._conn.execute(self._mcp_select_sql(" WHERE ms.id = ?"), (server_id,)).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.mcp_server_not_found", server_id=server_id))
        server = self._mcp_server_payload(row)
        if not server["consent_required"]:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.mcp_consent_local"),
                fields={"transport": self._render_copy("error.mcp_consent_local")},
            )
        consent = self.record_remote_consent_scope("mcp", server_id)
        refreshed = self.get_mcp_server(server_id)
        assert refreshed is not None
        return refreshed, consent

    def mcp_servers_for_target(self, target_id: str, member_id: str) -> list[dict[str, Any]]:
        """一轮装配的声明清单（频道级 + Agent 级；Agent 级同名覆盖频道级）。

        只返回**启用**的声明。远端声明带 ``consented``：装配层据此决定是否连接。与
        :meth:`permission_settings_for_target` 同一 target→channel 解析口径；DM / 未知 target
        没有频道声明（Agent 级仍无上下文，返回空）。
        """
        with self._lock:
            channel_id = self._channel_id_for_target_locked(target_id)
            if channel_id is None:
                return []
            rows = self._conn.execute(
                self._mcp_select_sql(
                    " WHERE (ms.scope_kind = 'channel' AND ms.scope_id = ?) "
                    "OR (ms.scope_kind = 'agent' AND ms.scope_id = ?) "
                    # 先频道后 Agent：同名时 Agent 覆盖频道（与权限规则同一优先级方向）。
                    "ORDER BY CASE ms.scope_kind WHEN 'channel' THEN 0 ELSE 1 END, "
                    "ms.created_at, ms.id"
                ),
                (channel_id, member_id),
            ).fetchall()
            merged: dict[str, dict[str, Any]] = {}
            for row in rows:
                server = self._mcp_server_payload(row)
                # Agent 级覆盖频道级：同名只留 Agent 的那条，但保留首次出现的位置。
                merged[server["name"]] = server
        return [server for server in merged.values() if server["enabled"]]

    # ─── Skill（#152 / 规格 §3.6 / §4.3 / ADR-0021 §三） ─────────────────────

    @staticmethod
    def _skill_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "description": str(row["description"]),
            "source_path": str(row["source_path"]),
            "content_hash": str(row["content_hash"]),
            "enabled": bool(row["enabled"]),
            "scope_kind": str(row["scope_kind"]),
            "scope_id": str(row["scope_id"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def validate_skill_scope(self, scope_kind: str, scope_id: str) -> None:
        """与 MCP / 权限规则同口径：channel 必须存在；agent 必须是未删除的 Agent。"""
        if scope_kind not in ("channel", "agent"):
            raise DomainError(
                "validation_failed",
                self._render_copy("error.skill_scope_invalid"),
                fields={"scope_kind": self._render_copy("error.skill_scope_invalid")},
            )
        if scope_kind == "channel":
            exists = self._conn.execute("SELECT 1 FROM channel WHERE id = ?", (scope_id,)).fetchone()
        else:
            exists = self._conn.execute(
                "SELECT 1 FROM member WHERE id = ? AND kind = 'agent' AND deleted_at IS NULL",
                (scope_id,),
            ).fetchone()
        if exists is None:
            message = self._render_copy("error.skill_scope_unknown", scope_id=scope_id)
            raise DomainError("validation_failed", message, fields={"scope_id": message})

    def channel_workspace_root(self, channel_id: str) -> str:
        """频道的 ``workspace_root``（未配置 / 不存在返回空串；不校验目录是否存在）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT workspace_root FROM channel WHERE id = ?", (channel_id,)
            ).fetchone()
        return str(row["workspace_root"] or "") if row is not None else ""

    def is_channel_member(self, channel_id: str, member_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM target_member WHERE target_id = ? AND member_id = ?",
                (channel_id, member_id),
            ).fetchone()
        return row is not None

    def workspace_roots_for_member(self, member_id: str) -> list[str]:
        """该成员所属频道里**已配置**的项目根（去重、稳定顺序；用于 Agent 级技能落点/清理）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT c.workspace_root FROM channel c "
                "JOIN target_member cm ON cm.target_id = c.id "
                "WHERE cm.member_id = ? AND c.workspace_root IS NOT NULL "
                "AND TRIM(c.workspace_root) <> '' ORDER BY c.workspace_root",
                (member_id,),
            ).fetchall()
        return [str(row["workspace_root"]) for row in rows]

    def all_workspace_roots(self) -> list[str]:
        """所有频道的已配置项目根（去重；删除 Agent 级技能时清掉可能遗留的副本）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT workspace_root FROM channel "
                "WHERE workspace_root IS NOT NULL AND TRIM(workspace_root) <> '' "
                "ORDER BY workspace_root"
            ).fetchall()
        return [str(row["workspace_root"]) for row in rows]

    def _assert_skill_name_free_locked(
        self, *, scope_kind: str, scope_id: str, name: str, exclude_id: str | None = None
    ) -> None:
        row = self._conn.execute(
            "SELECT id FROM skill WHERE scope_kind = ? AND scope_id = ? AND name = ?",
            (scope_kind, scope_id, name),
        ).fetchone()
        if row is not None and str(row["id"]) != (exclude_id or ""):
            message = self._render_copy("error.skill_conflict", name=name)
            raise DomainError("skill_conflict", message, status=409, fields={"name": message})

    def _normalize_skill(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """校验一条技能的**完整**元数据（安装 / 写入共用；安装器已先校验过内容）。"""
        from .skills import MAX_SKILL_DESCRIPTION_LENGTH, SKILL_NAME_PATTERN

        name = str(values.get("name") or "").strip()
        if not SKILL_NAME_PATTERN.fullmatch(name):
            message = self._render_copy("error.skill_name_invalid")
            raise DomainError("validation_failed", message, fields={"name": message})
        description = str(values.get("description") or "").strip()
        if not description:
            message = self._render_copy("error.skill_description_missing")
            raise DomainError("validation_failed", message, fields={"description": message})
        if len(description) > MAX_SKILL_DESCRIPTION_LENGTH:
            message = self._render_copy(
                "error.skill_description_too_long", limit=MAX_SKILL_DESCRIPTION_LENGTH
            )
            raise DomainError("validation_failed", message, fields={"description": message})
        source_path = str(values.get("source_path") or "").strip()
        if not source_path:
            message = self._render_copy("error.skill_source_invalid")
            raise DomainError("validation_failed", message, fields={"source_path": message})
        content_hash = str(values.get("content_hash") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
            message = self._render_copy("error.skill_hash_invalid")
            raise DomainError("validation_failed", message, fields={"content_hash": message})
        return {
            "name": name,
            "description": description,
            "source_path": source_path,
            "content_hash": content_hash,
            "enabled": bool(values.get("enabled", True)),
        }

    def list_skills(self, scope_kind: str | None = None, scope_id: str | None = None) -> list[dict[str, Any]]:
        """按 scope 过滤的技能列表（只读配置；不落消息、不唤醒）。"""
        clauses: list[str] = []
        params: list[Any] = []
        if scope_kind is not None:
            clauses.append("scope_kind = ?")
            params.append(scope_kind)
        if scope_id is not None:
            clauses.append("scope_id = ?")
            params.append(scope_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM skill{where} ORDER BY created_at, id", params
            ).fetchall()
        return [self._skill_payload(row) for row in rows]

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM skill WHERE id = ?", (skill_id,)).fetchone()
        return self._skill_payload(row) if row is not None else None

    def create_skill(
        self,
        *,
        scope_kind: str,
        scope_id: str,
        name: str,
        description: str,
        source_path: str,
        content_hash: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """写一条技能元数据；同 scope 同名冲突（不静默覆盖）。配置动作，不落消息。"""
        metadata = self._normalize_skill(
            {
                "name": name,
                "description": description,
                "source_path": source_path,
                "content_hash": content_hash,
                "enabled": enabled,
            }
        )
        now = utc_now()
        skill_id = new_id("sk")
        with self._lock:
            self.validate_skill_scope(scope_kind, scope_id)
            self._assert_skill_name_free_locked(
                scope_kind=scope_kind, scope_id=scope_id, name=metadata["name"]
            )
            self._conn.execute(
                "INSERT INTO skill(id, name, description, source_path, content_hash, enabled, "
                "scope_kind, scope_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    skill_id,
                    metadata["name"],
                    metadata["description"],
                    metadata["source_path"],
                    metadata["content_hash"],
                    1 if metadata["enabled"] else 0,
                    scope_kind,
                    scope_id,
                    now,
                    now,
                ),
            )
            row = self._conn.execute("SELECT * FROM skill WHERE id = ?", (skill_id,)).fetchone()
        return self._skill_payload(row)

    def update_skill(self, skill_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        """启停（``enabled``）。scope / 内容 hash 是身份，不可改。"""
        allowed = {"enabled"}
        unknown = set(changes) - allowed
        if unknown:
            message = self._render_copy("error.field_choices", values=", ".join(sorted(allowed)))
            raise DomainError("validation_failed", message, fields=dict.fromkeys(unknown, message))
        with self._lock:
            row = self._conn.execute("SELECT * FROM skill WHERE id = ?", (skill_id,)).fetchone()
            if row is None:
                raise NotFound(self._render_copy("error.skill_not_found", skill_id=skill_id))
            enabled = bool(changes.get("enabled", row["enabled"]))
            self._conn.execute(
                "UPDATE skill SET enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, utc_now(), skill_id),
            )
            row = self._conn.execute("SELECT * FROM skill WHERE id = ?", (skill_id,)).fetchone()
        return self._skill_payload(row)

    def delete_skill(self, skill_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM skill WHERE id = ?", (skill_id,)).fetchone()
            if row is None:
                raise NotFound(self._render_copy("error.skill_not_found", skill_id=skill_id))
            self._conn.execute("DELETE FROM skill WHERE id = ?", (skill_id,))
        return self._skill_payload(row)

    def skills_for_target(self, target_id: str, member_id: str) -> list[dict[str, Any]]:
        """一轮装配的技能清单（频道级 + Agent 级；Agent 级同名覆盖频道级，只返回启用的）。

        与 :meth:`mcp_servers_for_target` 同一 target→channel 解析口径；DM / 未知 target 没有
        频道级技能，Agent 级仍按成员自身分区装配。
        """
        with self._lock:
            channel_id = self._channel_id_for_target_locked(target_id)
            if channel_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM skill WHERE scope_kind = 'agent' AND scope_id = ? ORDER BY created_at, id",
                    (member_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM skill WHERE "
                    "(scope_kind = 'channel' AND scope_id = ?) OR "
                    "(scope_kind = 'agent' AND scope_id = ?) "
                    "ORDER BY CASE scope_kind WHEN 'channel' THEN 0 ELSE 1 END, "
                    "created_at, id",
                    (channel_id, member_id),
                ).fetchall()
            merged: dict[str, dict[str, Any]] = {}
            for row in rows:
                skill = self._skill_payload(row)
                # Agent 级覆盖频道级：同名只留 Agent 的那条，但保留首次出现的位置。
                merged[skill["name"]] = skill
        return [skill for skill in merged.values() if skill["enabled"]]

    def skill_layers_for_target(self, target_id: str, member_id: str) -> dict[str, list[dict[str, Any]]]:
        """**未合并**的两级项目技能行（含停用），供四层装配生成落选提示（#232）。

        :meth:`skills_for_target` 在库层就把「项目频道级被项目 Agent 级覆盖」的行丢掉了，
        装配层因此看不到落选者。本方法保留两层原始行、不按名字合并，返回
        ``{"agent": [...], "channel": [...]}``；两条路径共享同一 target→channel 解析口径。

        这里**不**过滤 ``enabled``：装配层先把各层按名字排出胜出者，再只装配启用的胜出者。
        否则停用的 Agent 级技能会消失，频道级同名条目就会违规复活（既有语义是「停用 Agent
        级 = 该成员没有这个技能」）。``load_skills`` 不读 ``enabled``，过滤由装配层负责。
        """
        with self._lock:
            channel_id = self._channel_id_for_target_locked(target_id)
            agent_rows = self._conn.execute(
                "SELECT * FROM skill WHERE scope_kind = 'agent' AND scope_id = ? ORDER BY created_at, id",
                (member_id,),
            ).fetchall()
            channel_rows = (
                []
                if channel_id is None
                else self._conn.execute(
                    "SELECT * FROM skill WHERE scope_kind = 'channel' AND scope_id = ? "
                    "ORDER BY created_at, id",
                    (channel_id,),
                ).fetchall()
            )
        return {
            "agent": [self._skill_payload(row) for row in agent_rows],
            "channel": [self._skill_payload(row) for row in channel_rows],
        }

    # ─── 审批（HITL；状态机正本，不存凭证） ───────────────────────────────
    @staticmethod
    def _approval_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "reply_id": str(row["reply_id"]),
            "target_id": str(row["target_id"]),
            "member_id": str(row["member_id"]),
            "team_run_id": str(row["team_run_id"]) if row["team_run_id"] else None,
            "tool_calls": json.loads(row["tool_calls"] or "[]"),
            "state": str(row["state"]),
            "decisions": json.loads(row["decisions"] or "[]"),
            "requested_at": str(row["requested_at"]),
            "resolved_at": str(row["resolved_at"]) if row["resolved_at"] else None,
        }

    def get_approval_request(self, reply_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM approval_request WHERE reply_id = ?", (reply_id,)
            ).fetchone()
        return self._approval_payload(row) if row is not None else None

    def list_approval_requests(
        self, target_id: str | None = None, state: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if target_id is not None:
            clauses.append("target_id = ?")
            params.append(target_id)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM approval_request{where} ORDER BY requested_at DESC",
                params,
            ).fetchall()
        return [self._approval_payload(row) for row in rows]

    def create_approval_request(
        self,
        *,
        reply_id: str,
        target_id: str,
        member_id: str,
        tool_calls: list[dict[str, Any]],
        team_run_id: str | None = None,
        requested_at: str | None = None,
    ) -> dict[str, Any]:
        """插入一条 pending；同一个 ``reply_id`` 已存在时原样返回（幂等）。"""
        now = requested_at or utc_now()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM approval_request WHERE reply_id = ?", (reply_id,)
            ).fetchone()
            if row is not None:
                return self._approval_payload(row)
            self._conn.execute(
                "INSERT INTO approval_request(reply_id, target_id, member_id, team_run_id, "
                "tool_calls, state, decisions, requested_at, resolved_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', '[]', ?, NULL)",
                (
                    reply_id,
                    target_id,
                    member_id,
                    team_run_id,
                    json.dumps(tool_calls, ensure_ascii=False),
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_request WHERE reply_id = ?", (reply_id,)
            ).fetchone()
        return self._approval_payload(row)

    def update_approval_request(
        self,
        reply_id: str,
        *,
        tool_calls: list[dict[str, Any]] | None = None,
        decisions: list[dict[str, Any]] | None = None,
        state: str | None = None,
        requested_at: Any = _UNSET,
        resolved_at: Any = _UNSET,
    ) -> dict[str, Any]:
        """更新审批记录的可变字段（工具调用合并、决策、终态与两个时间戳）。

        两个时间戳都用 ``_UNSET`` 区分「这次没提到」（保持原值）与「显式写入」——``resolved_at``
        要能写回 ``NULL``（重开终态记录时清掉上一批的解决时间），``requested_at`` 要能被刷新到
        这一批的起点（``#215``）；其余字段仍是 ``None`` = 不修改。
        """
        fields: list[str] = []
        params: list[Any] = []
        if tool_calls is not None:
            fields.append("tool_calls = ?")
            params.append(json.dumps(tool_calls, ensure_ascii=False))
        if decisions is not None:
            fields.append("decisions = ?")
            params.append(json.dumps(decisions, ensure_ascii=False))
        if state is not None:
            if state not in ("pending", "approved", "denied", "expired"):
                raise ValueError(f"unsupported approval state: {state!r}")
            fields.append("state = ?")
            params.append(state)
        if requested_at is not _UNSET:
            fields.append("requested_at = ?")
            params.append(requested_at)
        if resolved_at is not _UNSET:
            fields.append("resolved_at = ?")
            params.append(resolved_at)
        if not fields:
            record = self.get_approval_request(reply_id)
            if record is None:
                raise NotFound(self._render_copy("error.approval_not_found", reply_id=reply_id))
            return record
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE approval_request SET {', '.join(fields)} WHERE reply_id = ?",
                (*params, reply_id),
            )
            if cursor.rowcount == 0:
                raise NotFound(self._render_copy("error.approval_not_found", reply_id=reply_id))
            row = self._conn.execute(
                "SELECT * FROM approval_request WHERE reply_id = ?", (reply_id,)
            ).fetchone()
        return self._approval_payload(row)

    def expire_pending_approvals(self, *, resolved_at: str | None = None) -> list[str]:
        """启动时把上个进程残留的 pending 全部标 expired（D19）；返回被改的 reply_id。"""
        now = resolved_at or utc_now()
        with self._lock:
            rows = self._conn.execute(
                "SELECT reply_id FROM approval_request WHERE state = 'pending'"
            ).fetchall()
            reply_ids = [str(row["reply_id"]) for row in rows]
            if reply_ids:
                self._conn.execute(
                    "UPDATE approval_request SET state = 'expired', resolved_at = ? WHERE state = 'pending'",
                    (now,),
                )
        return reply_ids

    def create_dm(self, agent_id: str, owner_id: str = OWNER_ID) -> tuple[dict[str, Any], bool]:
        """创建或复用 owner 与单一 Agent 的两方私信，返回 (DM, 是否新建)。"""
        clean_agent_id = (agent_id or "").strip()
        with self._lock:
            row = self._conn.execute("SELECT * FROM member WHERE id = ?", (clean_agent_id,)).fetchone()
        if row is None or row["deleted_at"] is not None or row["kind"] != "agent":
            raise DomainError(
                "validation_failed",
                self._render_copy("error.dm_peer_invalid"),
                fields={
                    "agent_id": self._render_copy("error.field.dm_peer_invalid", member_id=clean_agent_id)
                },
            )
        owner = self.get_member(owner_id)
        if owner["kind"] != "owner":
            raise DomainError("validation_failed", self._render_copy("error.dm_creator_not_owner"))

        target_id: str
        created = False
        with self._lock:
            self._begin()
            try:
                existing = self._conn.execute(
                    "SELECT id FROM target WHERE kind = 'dm' AND dm_peer_member_id = ?",
                    (clean_agent_id,),
                ).fetchone()
                if existing is not None:
                    target_id = str(existing["id"])
                else:
                    target_id = new_id("dm")
                    now = utc_now()
                    self._conn.execute(
                        "INSERT INTO target(id, kind, dm_peer_member_id, created_at) VALUES (?, 'dm', ?, ?)",
                        (target_id, clean_agent_id, now),
                    )
                    for member_id in (owner_id, clean_agent_id):
                        self._conn.execute(
                            "INSERT INTO target_member(target_id, member_id) VALUES (?, ?)",
                            (target_id, member_id),
                        )
                        self._conn.execute(
                            "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES (?, ?, 0)",
                            (member_id, target_id),
                        )
                    created = True
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_dm(target_id, owner_id), created

    def list_dms(self, member_id: str = OWNER_ID) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(DM_SELECT_SQL, {"member_id": member_id, "dm_id": None}).fetchall()
        return [self._dm_payload(row) for row in rows]

    def get_dm(self, dm_id: str, member_id: str = OWNER_ID) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(DM_SELECT_SQL, {"member_id": member_id, "dm_id": dm_id}).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.dm_not_found", dm_id=dm_id))
        return self._dm_payload(row)

    @staticmethod
    def _dm_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "created_at": row["created_at"],
            "agent_id": row["peer_id"],
            "peer": {
                "id": row["peer_id"],
                "kind": row["peer_kind"],
                "display_name": row["peer_display_name"],
                "avatar_slot": row["peer_avatar_slot"],
                "created_at": row["peer_created_at"],
                "deleted_at": row["peer_deleted_at"],
            },
            "last_seq": row["last_seq"],
            "last_read_seq": row["last_read_seq"],
            "unread_count": row["unread_count"],
        }

    @staticmethod
    def _channel_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "created_at": row["created_at"],
            "last_seq": row["last_seq"],
            "last_read_seq": row["last_read_seq"],
            "unread_count": row["unread_count"],
        }

    def list_target_members(self, target_id: str) -> list[dict[str, Any]]:
        """该 target 的成员行（成员面/唤醒面的正本）。**不按 kind 分支**（三种 kind 同一张表、一个键）。"""
        self.get_target(target_id)  # 404 早退
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.* FROM target_member tm JOIN member m ON m.id = tm.member_id "
                "WHERE tm.target_id = ? ORDER BY CASE m.kind WHEN 'owner' THEN 0 ELSE 1 END, m.created_at",
                (target_id,),
            ).fetchall()
        return [self._member_payload(row) for row in rows]

    def list_target_agent_members(self, target_id: str) -> list[dict[str, Any]]:
        """该 target 的全部 Agent 成员（唤醒面的正本；调用方自行排除作者）。**不按 kind 分支**。"""
        self.get_target(target_id)  # 404 早退
        with self._lock:
            rows = self._conn.execute(
                AGENT_SELECT_SQL + "FROM target_member tm JOIN member m ON m.id = tm.member_id "
                "LEFT JOIN agent_config c ON c.member_id = m.id "
                "WHERE tm.target_id = ? AND m.kind = 'agent' AND m.deleted_at IS NULL "
                "ORDER BY m.created_at, m.id",
                (target_id,),
            ).fetchall()
        return [self._agent_payload(row) for row in rows]

    def create_channel(
        self,
        name: str,
        description: str = "",
        member_ids: Sequence[str] | None = None,
        creator_id: str = OWNER_ID,
    ) -> dict[str, Any]:
        """校验整体通过后才写库：任一字段不合法则**不留任何半成品行**。"""
        clean_name = (name or "").strip()
        clean_description = (description or "").strip()
        requested = list(dict.fromkeys(member_ids or []))

        fields: dict[str, str] = {}
        if not clean_name:
            fields["name"] = self._render_copy("error.field.channel_name_empty")
        elif len(clean_name) > MAX_CHANNEL_NAME_LENGTH:
            fields["name"] = self._render_copy(
                "error.field.channel_name_too_long", limit=MAX_CHANNEL_NAME_LENGTH
            )
        if len(clean_description) > MAX_CHANNEL_DESCRIPTION_LENGTH:
            fields["description"] = self._render_copy(
                "error.field.channel_description_too_long", limit=MAX_CHANNEL_DESCRIPTION_LENGTH
            )
        if fields:
            raise DomainError("validation_failed", self._render_copy("error.channel_invalid"), fields=fields)

        self.get_member(creator_id)
        selected: list[dict[str, Any]] = []
        for member_id in requested:
            with self._lock:
                row = self._conn.execute("SELECT * FROM member WHERE id = ?", (member_id,)).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.channel_invalid"),
                    fields={"member_ids": self._render_copy("error.member_unavailable", member_id=member_id)},
                )
            if row["kind"] != "agent":
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.channel_invalid"),
                    fields={"member_ids": self._render_copy("error.member_not_agent", member_id=member_id)},
                )
            selected.append(self._member_payload(row))

        channel_id = new_id("ch")
        now = utc_now()
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "INSERT INTO channel(id, name, description, created_at) VALUES (?, ?, ?, ?)",
                    (channel_id, clean_name, clean_description, now),
                )
                participants = [creator_id] + [member["id"] for member in selected]
                # channel 型的**影子行**：id 与 channel.id 同值、只承载 kind（ADR-0004 决策 1）。
                # 写在这里而不是别处，是因为它必须与频道行**同一个事务**：否则会出现
                # 「频道在、影子行不在」的中间态，而 message.target_id 的外键就指不到它了。
                self._conn.execute(
                    "INSERT INTO target(id, kind, created_at) VALUES (?, 'channel', ?)",
                    (channel_id, now),
                )
                for member_id in participants:
                    self._conn.execute(
                        "INSERT INTO target_member(target_id, member_id) VALUES (?, ?)",
                        (channel_id, member_id),
                    )
                    self._conn.execute(
                        "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES (?, ?, 0) "
                        "ON CONFLICT(member_id, target_id) DO NOTHING",
                        (member_id, channel_id),
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_channel(channel_id, creator_id)

    # ─── 频道配置（team / workspace；人类面唯一写路径是 PATCH config） ─────
    def get_channel_config(self, channel_id: str) -> dict[str, Any]:
        """频道配置的库内正本；不存在则 404。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, mode, team_leader_member_id, workspace_root, workspace_backend, "
                "permission_mode, web_access, docker_egress FROM channel WHERE id = ?",
                (channel_id,),
            ).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.channel_not_found", channel_id=channel_id))
        return {
            "channel_id": row["id"],
            "mode": row["mode"],
            "team_leader_member_id": row["team_leader_member_id"],
            "workspace_root": row["workspace_root"],
            "workspace_backend": row["workspace_backend"],
            "permission_mode": row["permission_mode"],
            "web_access": row["web_access"],
            "docker_egress": bool(row["docker_egress"]),
        }

    def update_channel_config(
        self,
        channel_id: str,
        *,
        mode: str | None = None,
        team_leader_member_id: str | None = None,
        workspace_root: Any = _UNSET,
        workspace_backend: str | None = None,
        permission_mode: str | None = None,
        web_access: Any = _UNSET,
        docker_egress: bool | None = None,
    ) -> dict[str, Any]:
        """写频道配置：**任一字段不合法整体拒绝**（校验先于写入，事务内一次 UPDATE）。

        ``None`` 在这些参数上表示「这次请求没提到它」（与 ``update_agent`` 同一条纪律）；
        ``workspace_root`` 与 ``web_access`` 是允许显式清空的字段（``_UNSET`` 区分「没提到」与
        「清空」）：「未配置项目根」与「联网回到未配置（读路径回退默认档）」本身就是合法状态
        （ADR-0021 §六 / ADR-0026 §二修订）；显式关闭联网要写 ``"off"``。
        不给清空路径就变成单向开关。

        team 的队长校验（规格 §4.4）：必须是**当前频道成员**里未删除的 Agent。库内触发器还守着
        「team 不能没有队长」这条不变量；这里先给出可读的字段错误，而不是让 SQL 的 ABORT 文案
        露给用户。``mode=team`` 作用于该频道 target 与它的全部 task target，写路径不复制任何
        状态到 task 行（解析在读路径 ``task.channel_id -> channel.mode``）。
        """
        current = self.get_channel_config(channel_id)
        fields: dict[str, str] = {}

        resolved_mode = current["mode"] if mode is None else mode
        if resolved_mode not in CHANNEL_MODES:
            fields["mode"] = self._render_copy(
                "error.field.channel_mode_invalid", values=", ".join(CHANNEL_MODES)
            )
        resolved_leader = (
            current["team_leader_member_id"] if team_leader_member_id is None else team_leader_member_id
        )
        leader_error = None
        if resolved_mode == "team" or team_leader_member_id is not None:
            leader_error = self._channel_leader_error(
                channel_id,
                resolved_leader,
                require_member=resolved_mode == "team",
            )
        if leader_error is not None:
            fields["team_leader_member_id"] = leader_error
        if resolved_mode == "team" and not resolved_leader:
            fields["team_leader_member_id"] = self._render_copy("error.field.channel_leader_required")

        if workspace_backend is not None and workspace_backend not in WORKSPACE_BACKENDS:
            fields["workspace_backend"] = self._render_copy(
                "error.field.channel_workspace_backend_invalid",
                values=", ".join(WORKSPACE_BACKENDS),
            )
        if permission_mode is not None and permission_mode not in PERMISSION_MODES:
            fields["permission_mode"] = self._render_copy(
                "error.field.channel_permission_mode_invalid",
                values=", ".join(PERMISSION_MODES),
            )

        # 联网档（#184 / ADR-0026 §二）：``NULL`` = 关闭（合法状态，与 ``workspace_root`` 同理）；
        # 其余取值只能落在两档白名单里，否则整条拒绝。
        if web_access is not _UNSET and web_access is not None:
            candidate_web_access = str(web_access).strip()
            if candidate_web_access not in WEB_ACCESS_LEVELS:
                fields["web_access"] = self._render_copy(
                    "error.field.channel_web_access_invalid",
                    values=", ".join(WEB_ACCESS_LEVELS),
                )
            else:
                resolved_web_access: str | None = candidate_web_access
        elif web_access is _UNSET:
            resolved_web_access = current["web_access"]
        else:
            resolved_web_access = None

        resolved_root: Any
        if workspace_root is _UNSET:
            resolved_root = current["workspace_root"]
        elif workspace_root is None:
            resolved_root = None
        else:
            candidate = str(workspace_root).strip()
            if not candidate or not os.path.isabs(candidate):
                fields["workspace_root"] = self._render_copy(
                    "error.field.channel_workspace_root_not_absolute",
                    path=candidate or str(workspace_root),
                )
            else:
                resolved_root = candidate

        # 容器出网开关（#187）：布尔单值，没有第三档；``None`` = 这次请求没提到它。
        resolved_docker_egress = current["docker_egress"] if docker_egress is None else bool(docker_egress)

        if fields:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.channel_config_invalid"),
                fields=fields,
            )

        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "UPDATE channel SET mode = ?, team_leader_member_id = ?, workspace_root = ?, "
                    "workspace_backend = ?, permission_mode = ?, web_access = ?, docker_egress = ? "
                    "WHERE id = ?",
                    (
                        resolved_mode,
                        resolved_leader,
                        resolved_root,
                        current["workspace_backend"] if workspace_backend is None else workspace_backend,
                        current["permission_mode"] if permission_mode is None else permission_mode,
                        resolved_web_access,
                        1 if resolved_docker_egress else 0,
                        channel_id,
                    ),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_channel_config(channel_id)

    def _channel_leader_error(
        self, channel_id: str, leader_member_id: str | None, *, require_member: bool
    ) -> str | None:
        """队长的可读校验；返回字段错误文案（通过则 ``None``）。"""
        if not leader_member_id:
            return (
                None
                if leader_member_id is None
                else self._render_copy("error.field.channel_leader_unavailable", member_id=leader_member_id)
            )
        with self._lock:
            row = self._conn.execute(
                "SELECT kind, deleted_at FROM member WHERE id = ?", (leader_member_id,)
            ).fetchone()
            if row is None or row["kind"] != "agent" or row["deleted_at"] is not None:
                return self._render_copy("error.field.channel_leader_unavailable", member_id=leader_member_id)
            if not require_member:
                return None
            member = self._conn.execute(
                "SELECT 1 FROM target_member WHERE target_id = ? AND member_id = ?",
                (channel_id, leader_member_id),
            ).fetchone()
        if member is None:
            return self._render_copy("error.field.channel_leader_unavailable", member_id=leader_member_id)
        return None

    # ─── team 运行记录（ADR-0018 / ADR-0022；不是消息、不进未读/搜索） ─────
    def create_team_run(
        self,
        *,
        target_id: str,
        leader_member_id: str,
        base_seq: int,
        status: str = "running",
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> dict[str, Any]:
        if status not in TEAM_RUN_STATUSES:
            raise DomainError("validation_failed", self._render_copy("error.team_run_status_invalid"))
        self.get_target(target_id)
        now = started_at or utc_now()
        team_run_id = new_id("teamrun")
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "INSERT INTO team_run(id, target_id, leader_member_id, base_seq, status, "
                    "started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        team_run_id,
                        target_id,
                        leader_member_id,
                        int(base_seq),
                        status,
                        now,
                        finished_at,
                    ),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_team_run(team_run_id)

    def finish_team_run(self, team_run_id: str, status: str) -> dict[str, Any]:
        if status not in TEAM_RUN_STATUSES:
            raise DomainError("validation_failed", self._render_copy("error.team_run_status_invalid"))
        with self._lock:
            self._begin()
            try:
                cursor = self._conn.execute(
                    "UPDATE team_run SET status = ?, finished_at = ? WHERE id = ?",
                    (status, utc_now(), team_run_id),
                )
                if cursor.rowcount == 0:
                    raise NotFound(self._render_copy("error.team_run_not_found", team_run_id=team_run_id))
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_team_run(team_run_id)

    def get_team_run(self, team_run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM team_run WHERE id = ?", (team_run_id,)).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.team_run_not_found", team_run_id=team_run_id))
        return self._team_run_payload(row)

    def list_team_runs(self, target_id: str, limit: int = 50) -> list[dict[str, Any]]:
        self.get_target(target_id)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM team_run WHERE target_id = ? ORDER BY started_at DESC, id DESC LIMIT ?",
                (target_id, int(limit)),
            ).fetchall()
        return [self._team_run_payload(row) for row in rows]

    def list_team_runs_page(
        self,
        target_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        task_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """该 target 的运行记录页（``started_at DESC, id DESC``），各带自己的分派快照。

        ``task_id`` 非空时只保留**至少含一条该 task 分派**的 run，并只返回这些分派；
        它不是 task 行存在性的校验（不存在的 task 与「没有命中」同形，都是空页）。
        多取一条判断 ``has_more``，因此 ``offset + limit == total`` 时不会谎报还有下一页。
        """
        self.get_target(target_id)  # 404 早退
        page_size = max(1, int(limit))
        start = max(0, int(offset))
        where = "tr.target_id = ?"
        params: list[Any] = [target_id]
        if task_id is not None:
            where += (
                " AND EXISTS (SELECT 1 FROM team_assignment a WHERE a.team_run_id = tr.id AND a.task_id = ?)"
            )
            params.append(task_id)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT tr.* FROM team_run tr WHERE {where} "
                "ORDER BY tr.started_at DESC, tr.id DESC LIMIT ? OFFSET ?",
                (*params, page_size + 1, start),
            ).fetchall()
            has_more = len(rows) > page_size
            rows = rows[:page_size]
            runs = [self._team_run_payload(row) for row in rows]
            if runs:
                placeholders = ", ".join("?" for _ in runs)
                assignment_sql = f"SELECT * FROM team_assignment WHERE team_run_id IN ({placeholders})"
                assignment_params: list[Any] = [run["id"] for run in runs]
                if task_id is not None:
                    assignment_sql += " AND task_id = ?"
                    assignment_params.append(task_id)
                assignment_sql += " ORDER BY started_at, id"
                grouped: dict[str, list[dict[str, Any]]] = {run["id"]: [] for run in runs}
                for row in self._conn.execute(assignment_sql, assignment_params):
                    grouped[str(row["team_run_id"])].append(self._team_assignment_payload(row))
                for run in runs:
                    run["assignments"] = grouped[run["id"]]
        return runs, has_more

    @staticmethod
    def _team_run_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "target_id": row["target_id"],
            "leader_member_id": row["leader_member_id"],
            "base_seq": row["base_seq"],
            "status": row["status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    # ─── Plan 运行记录（ADR-0023 / 规格 §3.5） ────────────────────────────
    # Plan 是成员轮次内的工作清单，不是任务五态：本组方法只写 ``plan_run`` / ``plan_task``，
    # 从不触碰 ``task``、消息、未读、搜索、唤醒或预算。``plan_task.seq`` 与 AgentScope
    # ``Task.id``（数字字符串）一一对应，于是重启装载回 ``AgentState.tasks`` 时任务标识稳定。
    def start_plan_run(self, *, target_id: str, member_id: str, base_seq: int) -> dict[str, Any]:
        """开一次成员轮次的 Plan run；状态 ``running``，结束由 :meth:`finish_plan_run` 收口。"""
        if int(base_seq) < 0:
            raise DomainError("validation_failed", self._render_copy("error.plan_base_seq_invalid"))
        self.get_target(target_id)
        self.get_member(member_id)
        plan_run_id = new_id("planrun")
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "INSERT INTO plan_run(id, target_id, member_id, base_seq, status, started_at) "
                    "VALUES (?, ?, ?, ?, 'running', ?)",
                    (plan_run_id, target_id, member_id, int(base_seq), utc_now()),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_plan_run(plan_run_id)

    def finish_plan_run(
        self,
        plan_run_id: str,
        *,
        status: str,
        tasks: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """以 ``plan_run_id`` 为界回写本次 Plan 的任务并收口。

        每个 run 保存的是**当前清单的一份快照**：先删掉本 run 的旧行，再按传入顺序重写。
        ``plan_task`` 随 ``plan_run`` 级联删除；收口后应用保留策略。
        """
        if status not in ("completed", "failed"):
            raise DomainError("validation_failed", self._render_copy("error.plan_run_status_invalid"))
        prepared = [self._prepare_plan_task(task) for task in tasks]
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT status FROM plan_run WHERE id = ?", (plan_run_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(self._render_copy("error.plan_run_not_found", plan_run_id=plan_run_id))
                self._conn.execute("DELETE FROM plan_task WHERE plan_run_id = ?", (plan_run_id,))
                now = utc_now()
                for task in prepared:
                    self._conn.execute(
                        "INSERT INTO plan_task(id, plan_run_id, seq, subject, description, state, "
                        "owner, blocks, blocked_by, metadata, created_at, updated_at, deleted_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                        (
                            new_id("plantask"),
                            plan_run_id,
                            task["seq"],
                            task["subject"],
                            task["description"],
                            task["state"],
                            task["owner"],
                            json.dumps(task["blocks"], ensure_ascii=False),
                            json.dumps(task["blocked_by"], ensure_ascii=False),
                            json.dumps(task["metadata"], ensure_ascii=False),
                            task["created_at"],
                            task["updated_at"] or now,
                        ),
                    )
                self._conn.execute(
                    "UPDATE plan_run SET status = ?, finished_at = COALESCE(finished_at, ?) WHERE id = ?",
                    (status, now, plan_run_id),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        self.prune_plan_runs()
        return self.get_plan_run(plan_run_id)

    @staticmethod
    def _prepare_plan_task(task: Mapping[str, Any]) -> dict[str, Any]:
        """校验并归一化一条待回写的 Plan 任务（工具侧只产生这三种状态）。"""
        state = str(task.get("state") or "pending")
        if state not in PLAN_TASK_STATES:
            raise DomainError("validation_failed", f"invalid plan task state: {state}")
        subject = str(task.get("subject") or "").strip()
        if not subject:
            raise DomainError("validation_failed", "plan task subject is required")
        seq = int(task.get("seq") or 0)
        if seq < 1:
            raise DomainError("validation_failed", "plan task seq must be positive")
        return {
            "seq": seq,
            "subject": subject,
            "description": str(task.get("description") or ""),
            "state": state,
            "owner": task.get("owner"),
            "blocks": list(task.get("blocks") or []),
            "blocked_by": list(task.get("blocked_by") or []),
            "metadata": dict(task.get("metadata") or {}),
            "created_at": str(task.get("created_at") or utc_now()),
            "updated_at": str(task.get("updated_at") or ""),
        }

    def get_plan_run(self, plan_run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM plan_run WHERE id = ?", (plan_run_id,)).fetchone()
            if row is None:
                raise NotFound(self._render_copy("error.plan_run_not_found", plan_run_id=plan_run_id))
            tasks = self._plan_tasks_locked(plan_run_id)
        return self._plan_run_payload(row, tasks)

    def latest_plan_tasks(self, *, target_id: str, member_id: str) -> list[dict[str, Any]]:
        """该 ``(target_id, member_id)`` 最近一个 run 的任务清单（轮次开始装载用）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM plan_run WHERE target_id = ? AND member_id = ? "
                "ORDER BY started_at DESC, rowid DESC LIMIT 1",
                (target_id, member_id),
            ).fetchone()
            if row is None:
                return []
            return self._plan_tasks_locked(str(row["id"]))

    def plan_view(self, target_id: str, limit: int = PLAN_RUN_RETENTION) -> dict[str, Any]:
        """按 member 分组的 Plan 快照（每个成员保留最近 ``limit`` 个 run）。"""
        self.get_target(target_id)
        limit = max(1, int(limit))
        with self._lock:
            member_rows = self._conn.execute(
                "SELECT DISTINCT pr.member_id, m.display_name "
                "FROM plan_run pr LEFT JOIN member m ON m.id = pr.member_id "
                "WHERE pr.target_id = ? ORDER BY m.display_name, pr.member_id",
                (target_id,),
            ).fetchall()
            members = []
            for member_row in member_rows:
                run_rows = self._conn.execute(
                    "SELECT * FROM plan_run WHERE target_id = ? AND member_id = ? "
                    "ORDER BY started_at DESC, rowid DESC LIMIT ?",
                    (target_id, str(member_row["member_id"]), limit),
                ).fetchall()
                runs = [
                    self._plan_run_payload(run, self._plan_tasks_locked(str(run["id"]))) for run in run_rows
                ]
                members.append(
                    {
                        "member_id": str(member_row["member_id"]),
                        "display_name": member_row["display_name"],
                        "runs": runs,
                    }
                )
        return {"target_id": target_id, "members": members}

    def prune_plan_runs(self) -> int:
        """应用 Plan 保留策略：每个 ``(target, member)`` 最近 20 run 或 30 天（先到者为准）。

        返回被删除的 run 数；``plan_task`` 由外键级联清理。启动时与每轮收口后调用。
        """
        cutoff = (
            (datetime.now(UTC) - timedelta(days=PLAN_RETENTION_DAYS))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, target_id, member_id, started_at FROM plan_run "
                "ORDER BY target_id, member_id, started_at DESC, rowid DESC"
            ).fetchall()
            keep: dict[tuple[str, str], int] = {}
            doomed: list[str] = []
            for row in rows:
                pair = (str(row["target_id"]), str(row["member_id"]))
                rank = keep.get(pair, 0)
                keep[pair] = rank + 1
                if rank >= PLAN_RUN_RETENTION or str(row["started_at"]) < cutoff:
                    doomed.append(str(row["id"]))
            if not doomed:
                return 0
            self._begin()
            try:
                for start in range(0, len(doomed), 500):
                    chunk = doomed[start : start + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    self._conn.execute(f"DELETE FROM plan_run WHERE id IN ({placeholders})", chunk)
                self._commit()
            except Exception:
                self._rollback()
                raise
        return len(doomed)

    def _plan_task_rows_locked(self, plan_run_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM plan_task WHERE plan_run_id = ? ORDER BY seq",
            (plan_run_id,),
        ).fetchall()

    def _plan_tasks_locked(self, plan_run_id: str) -> list[dict[str, Any]]:
        return [self._plan_task_payload(row) for row in self._plan_task_rows_locked(plan_run_id)]

    @staticmethod
    def _plan_task_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            # AgentScope 的 ``Task.id``：装载时按 ``seq`` 还原成数字字符串。
            "id": str(row["seq"]),
            "seq": int(row["seq"]),
            "subject": row["subject"],
            "description": row["description"],
            "state": row["state"],
            "owner": row["owner"],
            "blocks": json.loads(row["blocks"] or "[]"),
            "blocked_by": json.loads(row["blocked_by"] or "[]"),
            "metadata": json.loads(row["metadata"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "deleted_at": row["deleted_at"],
        }

    @staticmethod
    def _plan_run_payload(row: sqlite3.Row, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "target_id": row["target_id"],
            "member_id": row["member_id"],
            "base_seq": row["base_seq"],
            "status": row["status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "tasks": tasks,
        }

    # ─── SOP 定义与运行记录（ADR-0023 / 规格 §3.5、§4.2） ──────────────────
    # 定义是可编辑的配置；运行是只增不改的历史 + 可恢复的 ``SOPRunState``。两者都**不是**任务：
    # 本组方法只写 ``sop_definition`` / ``sop_run``，从不触碰 ``task``、消息、未读、搜索、唤醒或预算。
    def create_sop_definition(
        self,
        *,
        name: str,
        description: str,
        steps: Sequence[Mapping[str, Any]],
        enabled: bool = True,
    ) -> dict[str, Any]:
        """新建一条 SOP 定义；``steps`` 里的成员在这里校验并归一化。"""
        clean_name = str(name or "").strip()
        if not clean_name:
            raise DomainError("validation_failed", self._render_copy("error.sop_name_required"))
        prepared = self._prepare_sop_steps(steps)
        now = utc_now()
        definition_id = new_id("sop")
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "INSERT INTO sop_definition(id, name, description, steps, enabled, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        definition_id,
                        clean_name,
                        str(description or ""),
                        json.dumps(prepared, ensure_ascii=False),
                        1 if enabled else 0,
                        now,
                        now,
                    ),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_sop_definition(definition_id)

    def update_sop_definition(
        self,
        definition_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        steps: Sequence[Mapping[str, Any]] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """编辑定义（启停 / 名称 / 描述 / 步骤）。只影响后续 run；既有 run 的 ``state`` 不重写。"""
        current = self.get_sop_definition(definition_id)
        clean_name = current["name"] if name is None else str(name or "").strip()
        if not clean_name:
            raise DomainError("validation_failed", self._render_copy("error.sop_name_required"))
        prepared = self._prepare_sop_steps(steps) if steps is not None else current["steps"]
        with self._lock:
            self._begin()
            try:
                cursor = self._conn.execute(
                    "UPDATE sop_definition SET name = ?, description = ?, steps = ?, "
                    "enabled = ?, updated_at = ? WHERE id = ?",
                    (
                        clean_name,
                        current["description"] if description is None else str(description),
                        json.dumps(prepared, ensure_ascii=False),
                        1 if (current["enabled"] if enabled is None else bool(enabled)) else 0,
                        utc_now(),
                        definition_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise NotFound(
                        self._render_copy("error.sop_definition_not_found", definition_id=definition_id)
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_sop_definition(definition_id)

    def get_sop_definition(self, definition_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sop_definition WHERE id = ?", (definition_id,)).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.sop_definition_not_found", definition_id=definition_id))
        return self._sop_definition_payload(row)

    def list_sop_definitions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM sop_definition ORDER BY created_at, id").fetchall()
        return [self._sop_definition_payload(row) for row in rows]

    def _prepare_sop_steps(self, steps: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """校验并归一化 ``steps``：subject / executor / 可空 verifier / max_attempts。

        成员必须是**未删除且启用的 Agent**：SOP 的参与者是成员面的 Agent，owner 不是执行者。
        这里一次校验整份定义，任一步非法就整体拒绝（不留半成品）。
        """
        if not steps:
            raise DomainError("validation_failed", self._render_copy("error.sop_steps_required"))
        prepared: list[dict[str, Any]] = []
        for raw in steps:
            subject = str(raw.get("subject") or "").strip()
            if not subject:
                raise DomainError("validation_failed", self._render_copy("error.sop_step_subject_required"))
            executor_id = str(raw.get("executor_member_id") or "").strip()
            if not executor_id:
                raise DomainError("validation_failed", self._render_copy("error.sop_step_executor_required"))
            self.require_sop_agent(executor_id)
            verifier_raw = raw.get("verifier_member_id")
            verifier_id = str(verifier_raw).strip() if verifier_raw else None
            if verifier_id:
                self.require_sop_agent(verifier_id)
            max_attempts = raw.get("max_attempts", 3)
            try:
                attempts = int(max_attempts)
            except (TypeError, ValueError):
                attempts = 0
            if attempts < 1:
                raise DomainError("validation_failed", self._render_copy("error.sop_max_attempts_invalid"))
            prepared.append(
                {
                    "subject": subject,
                    "description": str(raw.get("description") or ""),
                    "executor_member_id": executor_id,
                    "verifier_member_id": verifier_id,
                    "max_attempts": attempts,
                }
            )
        return prepared

    def require_sop_agent(self, member_id: str) -> dict[str, Any]:
        agent = self.get_agent(member_id)
        if agent.get("deleted_at") is not None:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.sop_member_unavailable", member_id=member_id),
            )
        if not agent.get("enabled", True):
            raise DomainError(
                "validation_failed",
                self._render_copy("error.sop_member_disabled", member_id=member_id),
            )
        return agent

    @staticmethod
    def _sop_definition_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "steps": json.loads(row["steps"] or "[]"),
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_sop_run(
        self,
        *,
        sop_definition_id: str,
        target_id: str,
        leader_member_id: str = OWNER_ID,
        state: str,
        phase: str = "pending",
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> dict[str, Any]:
        """落一次 SOP 运行；``state`` 是 ``SOPRunState.model_dump_json()`` 的原样快照。"""
        if phase not in SOP_PHASES:
            raise DomainError("validation_failed", self._render_copy("error.sop_phase_invalid"))
        if not state:
            raise DomainError("validation_failed", self._render_copy("error.sop_state_required"))
        self.get_target(target_id)
        self.get_member(leader_member_id)
        now = started_at or utc_now()
        run_id = new_id("soprun")
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "INSERT INTO sop_run(id, sop_definition_id, target_id, leader_member_id, "
                    "state, phase, started_at, finished_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        sop_definition_id,
                        target_id,
                        leader_member_id,
                        state,
                        phase,
                        now,
                        finished_at,
                        now,
                    ),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        self.prune_sop_runs()
        return self.get_sop_run(run_id)

    def update_sop_run_state(
        self,
        run_id: str,
        *,
        state: str,
        phase: str,
    ) -> dict[str, Any]:
        """把一次运行的最新 ``SOPRunState`` 与派生 ``phase`` 写回；不改定义、不写消息。"""
        if phase not in SOP_PHASES:
            raise DomainError("validation_failed", self._render_copy("error.sop_phase_invalid"))
        finished_at = utc_now() if phase in ("completed", "failed") else None
        with self._lock:
            self._begin()
            try:
                cursor = self._conn.execute(
                    "UPDATE sop_run SET state = ?, phase = ?, updated_at = ?, "
                    "finished_at = COALESCE(?, finished_at) WHERE id = ?",
                    (state, phase, utc_now(), finished_at, run_id),
                )
                if cursor.rowcount == 0:
                    raise NotFound(self._render_copy("error.sop_run_not_found", sop_run_id=run_id))
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_sop_run(run_id)

    def get_sop_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sop_run WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.sop_run_not_found", sop_run_id=run_id))
        return self._sop_run_payload(row)

    def prune_sop_runs(self) -> int:
        """保留最近 50 个或 90 天内的 SOP 运行；返回删除条数。

        定义**不**参与清理：完成/失败后不自动删除定义（ADR-0023 §三）。启动时与新建 run 后调用。
        """
        cutoff = (
            (datetime.now(UTC) - timedelta(days=SOP_RETENTION_DAYS))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, started_at FROM sop_run ORDER BY started_at DESC, rowid DESC"
            ).fetchall()
            doomed = [
                str(row["id"])
                for rank, row in enumerate(rows)
                if rank >= SOP_RUN_RETENTION or str(row["started_at"]) < cutoff
            ]
            if not doomed:
                return 0
            self._begin()
            try:
                for start in range(0, len(doomed), 500):
                    chunk = doomed[start : start + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    self._conn.execute(f"DELETE FROM sop_run WHERE id IN ({placeholders})", chunk)
                self._commit()
            except Exception:
                self._rollback()
                raise
        return len(doomed)

    @staticmethod
    def _sop_run_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "sop_definition_id": row["sop_definition_id"],
            "target_id": row["target_id"],
            "leader_member_id": row["leader_member_id"],
            "phase": row["phase"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "updated_at": row["updated_at"],
            "state": json.loads(row["state"] or "{}"),
        }

    def create_team_assignment(
        self,
        *,
        team_run_id: str,
        target_id: str,
        task_id: str | None,
        leader_member_id: str,
        member_id: str,
        tool_call_id: str,
        prompt: str,
        state: str = "started",
        jump_consumed: int = 1,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> dict[str, Any]:
        """落一条分派运行记录；``state`` / ``jump_consumed`` 由裁决结果决定。

        ``budget-exhausted`` 是一次**没运行成员**的分派（``jump_consumed=0``）；``started`` 是
        已扣跳、成员正在跑的那一条，收口时由 :meth:`finish_team_assignment` 改成
        ``succeeded`` / ``failed`` / ``interrupted``。跳一旦扣减就不退款，因此本方法**不提供**
        把 ``jump_consumed`` 从 1 改回 0 的路径。
        """
        if state not in TEAM_ASSIGNMENT_STATES:
            raise DomainError("validation_failed", self._render_copy("error.team_assignment_state_invalid"))
        if int(jump_consumed) not in (0, 1):
            raise DomainError("validation_failed", self._render_copy("error.team_assignment_jump_invalid"))
        now = started_at or utc_now()
        if finished_at is None and state != "started":
            finished_at = now
        assignment_id = new_id("assign")
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "INSERT INTO team_assignment(id, team_run_id, target_id, task_id, "
                    "leader_member_id, member_id, tool_call_id, prompt, state, jump_consumed, "
                    "started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        assignment_id,
                        team_run_id,
                        target_id,
                        task_id,
                        leader_member_id,
                        member_id,
                        tool_call_id,
                        prompt,
                        state,
                        int(jump_consumed),
                        now,
                        finished_at,
                    ),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_team_assignment(assignment_id)

    def finish_team_assignment(
        self,
        assignment_id: str,
        *,
        state: str,
        reply_excerpt: str = "",
        error: str = "",
        finished_at: str | None = None,
    ) -> dict[str, Any]:
        """把一条分派收口成终态；``jump_consumed`` 原样保留（不退款）。"""
        if state not in TEAM_ASSIGNMENT_STATES or state == "started":
            raise DomainError("validation_failed", self._render_copy("error.team_assignment_state_invalid"))
        with self._lock:
            self._begin()
            try:
                cursor = self._conn.execute(
                    "UPDATE team_assignment SET state = ?, reply_excerpt = ?, error = ?, "
                    "finished_at = ? WHERE id = ?",
                    (state, reply_excerpt, error, finished_at or utc_now(), assignment_id),
                )
                if cursor.rowcount == 0:
                    raise NotFound(
                        self._render_copy("error.team_assignment_not_found", assignment_id=assignment_id)
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_team_assignment(assignment_id)

    def get_team_assignment(self, assignment_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM team_assignment WHERE id = ?", (assignment_id,)
            ).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.team_assignment_not_found", assignment_id=assignment_id))
        return self._team_assignment_payload(row)

    def list_team_assignments(self, target_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """该 target 的分派运行记录（按开始时间；同秒内按 id 定序）。"""
        self.get_target(target_id)  # 404 早退
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM team_assignment WHERE target_id = ? ORDER BY started_at, id LIMIT ?",
                (target_id, int(limit)),
            ).fetchall()
        return [self._team_assignment_payload(row) for row in rows]

    @staticmethod
    def _team_assignment_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "team_run_id": row["team_run_id"],
            "target_id": row["target_id"],
            "task_id": row["task_id"],
            "leader_member_id": row["leader_member_id"],
            "member_id": row["member_id"],
            "tool_call_id": row["tool_call_id"],
            "prompt": row["prompt"],
            "state": row["state"],
            "jump_consumed": int(row["jump_consumed"]),
            "reply_excerpt": row["reply_excerpt"],
            "error": row["error"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    # ─── 消息（作用域键是 **target_id**） ────────────────────────────────
    def list_messages(self, target_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        self.get_target(target_id)  # 404 早退
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT m.*, a.kind AS author_kind, a.display_name AS author_name,
                       a.avatar_slot AS author_avatar_slot
                  FROM message m JOIN member a ON a.id = m.author_member_id
                 WHERE m.target_id = ? AND m.seq > ?
                 ORDER BY m.seq
                """,
                (target_id, after_seq),
            ).fetchall()
            attached = self._attachments_by_target_locked(target_id, after_seq)
        return [self._message_payload(row, attached.get(row["id"], [])) for row in rows]

    def search(
        self,
        query: str,
        *,
        facet: SearchFacet = "all",
        target_id: str | None = None,
        limit: int = SEARCH_RESULT_LIMIT,
    ) -> list[dict[str, Any]]:
        """搜索三类命中；``facet`` 决定查哪几类，``target_id`` 收窄到单个 target 的消息面。

        本仓是单用户工作台，target_member 表表达唤醒与未读的成员面，不是可见性 ACL；所以这里
        **不**附加 target_member 过滤。target 仅在调用方显式限定时校验存在，避免把错误 target
        伪装成零命中。

        给定 ``target_id`` 时**只有消息命中适用**（沿用既有语义：只在该作用域内搜消息）——
        任务命中挂它所属的频道、Agent 命中不挂任何 target，两者都不属于「这一个 target」。
        ``limit`` 是**每类**命中的条数上限（``facet='all'`` 时三类各自最多 ``limit`` 条）。

        每条命中的形状统一（ADR-0013）：``kind`` / ``text`` / ``highlight_ranges`` / ``target``
        （所属 target，Agent 命中为 ``null``），外加该类的对象标识（``message`` / ``task`` / ``agent``）。
        区间缺失时（空表）前端整段呈现，**不得**自行在文本里找位置。
        """
        if target_id is not None:
            self.get_target(target_id)
        params = {
            "pattern": self._search_like_pattern(query),
            "target_id": target_id,
            "limit": limit,
        }
        hits: list[dict[str, Any]] = []
        with self._lock:
            if facet in ("all", "message"):
                sql = SEARCH_TARGET_HITS_SQL if target_id is not None else SEARCH_ALL_HITS_SQL
                hits.extend(self._message_hit_payload(row, query) for row in self._conn.execute(sql, params))
                # 附件不是命中来源（ADR-0014：附件不进搜索），但消息命中里嵌的是**完整消息形状**：
                # 让它的 ``attachments`` 说实话（一次查询补齐，不是每条命中各查一次）。
                self._fill_message_hit_attachments_locked(hits)
            if target_id is not None:
                return hits
            if facet in ("all", "task"):
                hits.extend(
                    self._task_hit_payload(row, query)
                    for row in self._conn.execute(SEARCH_TASK_HITS_SQL, params)
                )
            if facet in ("all", "agent"):
                hits.extend(
                    self._agent_hit_payload(row, query)
                    for row in self._conn.execute(SEARCH_AGENT_HITS_SQL, params)
                )
        return hits

    def append_message(
        self,
        target_id: str,
        body: str,
        author_member_id: str = OWNER_ID,
        kind: str = "human",
        mention_snapshot: Iterable[Mapping[str, Any]] | None = None,
        task_ref: str | None = None,
        attachment_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """在同一事务内分配 **target 内**单调 seq 并落库。失败则不落任何行。

        ``attachment_ids`` 是**先上传拿到标识、随消息提交时绑定**的那一步（ADR-0014）：绑定与消息
        落库**同一事务**——任一步失败都整体回滚，于是不会留下「绑了一半的草稿」或「有消息没附件」。
        绑定成功后，附件不再是草稿；未绑定的上传在读取面里不存在。
        """
        if kind not in MESSAGE_KINDS:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.unknown_message_kind", kind=kind),
                fields={"kind": self._render_copy("error.field.message_kind_invalid")},
            )
        clean_body = (body or "").strip()
        if not clean_body:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.message_body_empty"),
                fields={"body": self._render_copy("error.message_body_empty")},
            )
        if len(clean_body) > MAX_MESSAGE_BODY_LENGTH:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.message_body_too_long", limit=MAX_MESSAGE_BODY_LENGTH),
                fields={
                    "body": self._render_copy("error.message_body_too_long", limit=MAX_MESSAGE_BODY_LENGTH)
                },
            )
        self.get_target(target_id)  # 404 早退
        self.get_member(author_member_id)
        if task_ref is not None:
            try:
                referenced_task = self.get_task(task_ref)
            except NotFound as exc:
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.task_ref_missing"),
                    fields={"task_ref": self._render_copy("error.field.task_ref_missing")},
                ) from exc
            if referenced_task["target_id"] != target_id:
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.task_ref_other_target"),
                    fields={"task_ref": self._render_copy("error.field.task_ref_other_target")},
                )

        message_id = new_id("msg")
        now = utc_now()
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM message WHERE target_id = ?",
                    (target_id,),
                ).fetchone()
                seq = int(row["next_seq"])
                self._conn.execute(
                    "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at, "
                    "mention_snapshot, task_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        message_id,
                        target_id,
                        seq,
                        author_member_id,
                        kind,
                        clean_body,
                        now,
                        json.dumps(list(mention_snapshot or []), ensure_ascii=False),
                        task_ref,
                    ),
                )
                # 绑定与消息落库**同一事务**（ADR-0014）：任何一条绑定失败都整体回滚，
                # 于是既不留绑定、也不留消息——没有「绑了一半」的中间态。
                self._bind_attachments_locked(attachment_ids, message_id)
                if kind == "human":
                    # 人类消息落库**即**开启新世代并把回应预算重置为满额（spec #1 §5）。
                    # 写在这里而不是等到下一次扣款时惰性重置：「人类消息随后落库即重置」
                    # 是落库那一刻的事实，预算状态端点与压制行为必须同时看到它。
                    # 预算按 **target** 一份（ADR-0004 决策 4）：同一个写点服务三种 kind，
                    # 并在这一刻把该世代生效的上界拍成快照（#53：保存的新值从这里才生效）。
                    self._reset_epoch_budget_locked(target_id, seq)
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_message(target_id, seq)

    def get_message(self, target_id: str, seq: int) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT m.*, a.kind AS author_kind, a.display_name AS author_name,
                       a.avatar_slot AS author_avatar_slot
                  FROM message m JOIN member a ON a.id = m.author_member_id
                 WHERE m.target_id = ? AND m.seq = ?
                """,
                (target_id, seq),
            ).fetchone()
            attached = self._attachments_by_message_locked(str(row["id"])) if row is not None else []
        if row is None:
            raise NotFound(self._render_copy("error.message_not_found", target_id=target_id, seq=seq))
        return self._message_payload(row, attached)

    @staticmethod
    def _message_payload(row: sqlite3.Row, attachments: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
        try:
            mentions = json.loads(row["mention_snapshot"])
        except (ValueError, TypeError):
            mentions = []
        return {
            "id": row["id"],
            "target_id": row["target_id"],
            "seq": row["seq"],
            "kind": row["kind"],
            "body": row["body"],
            "created_at": row["created_at"],
            "mention_snapshot": mentions,
            "task_ref": row["task_ref"],
            "task_pointer": row["task_pointer"],
            "attachments": list(attachments),
            "author": {
                "id": row["author_member_id"],
                "kind": row["author_kind"],
                "display_name": row["author_name"],
                "avatar_slot": row["author_avatar_slot"],
            },
        }

    @staticmethod
    def _search_like_pattern(query: str) -> str:
        """把用户文本变为 SQLite ``LIKE '%kw%' ESCAPE '\\'`` 的字面子串模式。"""
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return "%" + escaped.casefold() + "%"

    @staticmethod
    def _search_highlight_ranges(body: str, query: str) -> list[dict[str, int]]:
        """给出全部不重叠命中的 UTF-16 区间，供浏览器直接 ``slice`` 呈现。"""
        folded_body: list[str] = []
        original_starts: list[int] = []
        original_ends: list[int] = []
        for index, character in enumerate(body):
            folded_character = character.casefold()
            folded_body.append(folded_character)
            original_starts.extend([index] * len(folded_character))
            original_ends.extend([index + 1] * len(folded_character))

        folded_text = "".join(folded_body)
        folded_query = query.casefold()
        if not folded_query:
            return []
        ranges: list[dict[str, int]] = []
        seen: set[tuple[int, int]] = set()
        previous_original_end = 0
        cursor = 0
        while True:
            folded_start = folded_text.find(folded_query, cursor)
            if folded_start < 0:
                break
            folded_end = folded_start + len(folded_query)
            start = original_starts[folded_start]
            end = original_ends[folded_end - 1]
            original_range = (start, end)
            # JavaScript 的 String.slice 按 UTF-16 code unit 计数；转换留在唯一判定落点，前端无需重算。
            if original_range not in seen and start >= previous_original_end:
                ranges.append(
                    {
                        "start": len(body[:start].encode("utf-16-le")) // 2,
                        "end": len(body[:end].encode("utf-16-le")) // 2,
                    }
                )
                seen.add(original_range)
                previous_original_end = end
                cursor = folded_end
            else:
                # casefold 展开可能让 folded 匹配映回重叠原文；从下一个 folded 起点继续，
                # 不能直接跳过候选尾部，否则会漏掉紧随其后的非重叠原文命中。
                cursor = folded_start + 1
        return ranges

    @classmethod
    def _message_hit_payload(cls, row: sqlite3.Row, query: str) -> dict[str, Any]:
        """消息命中：可呈现文本是消息正文，区间由后端给出（``kind='system'`` 已被 SQL 排除）。"""
        message = cls._message_payload(row)
        message["author"]["deleted"] = row["author_deleted_at"] is not None
        return {
            "kind": "message",
            "text": message["body"],
            "highlight_ranges": cls._search_highlight_ranges(message["body"], query),
            "target": {
                "id": row["target_id"],
                "kind": row["target_kind"],
                "display_name": row["target_display_name"],
            },
            "message": message,
        }

    @classmethod
    def _task_hit_payload(cls, row: sqlite3.Row, query: str) -> dict[str, Any]:
        """任务命中：可呈现文本是**任务标题**，``target`` 是它所属的频道（点击落到任务线程）。"""
        title = row["title"]
        return {
            "kind": "task",
            "text": title,
            "highlight_ranges": cls._search_highlight_ranges(title, query),
            "target": {
                "id": row["channel_id"],
                "kind": "channel",
                "display_name": row["channel_name"],
            },
            "task": {
                "id": row["id"],
                "target_id": row["target_id"],
                "channel_id": row["channel_id"],
                "channel_name": row["channel_name"],
                "title": title,
                "status": row["status"],
            },
        }

    @classmethod
    def _agent_hit_payload(cls, row: sqlite3.Row, query: str) -> dict[str, Any]:
        """Agent 命中：命中本体是显示名与角色，因此可呈现文本是「显示名 · 角色」。

        两个候选字段（显示名 / 角色）里哪一个命中的位置由后端的区间给出——前端只按区间包标签。
        Agent 不挂任何 target，所以 ``target`` 是 ``null``（对象标识在 ``agent`` 里）。
        ``status`` 是**编排**给出的事实，由服务层补齐（与 ``list_dms`` 补 ``peer.status`` 同一体例）。
        """
        display_name = row["display_name"]
        role = row["role"]
        text = f"{display_name} · {role}" if role else display_name
        return {
            "kind": "agent",
            "text": text,
            "highlight_ranges": cls._search_highlight_ranges(text, query),
            "target": None,
            "agent": {
                "id": row["id"],
                "display_name": display_name,
                "avatar_slot": row["avatar_slot"],
                "role": role,
                "model_id": row["model_id"],
            },
        }

    # ─── 有界响应预算（策略 B 的正本，落库） ──────────────────────────────
    def _saved_budget_limit_locked(self, target_id: str) -> int | None:
        """该 target 所在**频道**已保存的上界覆盖；没有配置（或不是频道）时 ``None``。

        ``channel_budget_limit.channel_id`` 的外键指向 ``channel(id)``，而 channel 型 target 复用
        ``channel.id``（ADR-0004），因此按 target id 查这一列在 channel 型上就是按频道查；
        dm / task 型的 id 不在 ``channel`` 表里，查询自然无行 —— 「逐频道配置」不可能落到它们头上。

        只由 :meth:`_budget_facts_locked` 与 :meth:`_applied_limit_snapshot_locked` 调用（都在锁内）。
        """
        row = self._conn.execute(
            "SELECT budget_limit FROM channel_budget_limit WHERE channel_id = ?", (target_id,)
        ).fetchone()
        return None if row is None else int(row["budget_limit"])

    def _budget_facts_locked(self, target_id: str) -> dict[str, Any]:
        """预算读数的**唯一**组装点：五项既有载荷 + 当前上界的来源 + 下一世代的待生效值。

        有效上界的解析只有这一个落点（读路径与硬上界裁决都用它的结果，否则「展示的 limit」与
        「压制行为」会分成两条事实）。判定顺序：

        1. 库里有 ``target_budget`` 行 ⇒ 上界是该世代**开世代那一刻的快照** ``applied_limit``：
           非空 = 频道覆盖、``NULL`` = 内置默认。快照一写就不再随配置漂移，于是「保存的新值从
           下一个人类世代生效」与「当前世代不被回溯改写」是同一件事。
        2. 库里没有行 ⇒ 这个 target 还没有开过世代、也没消耗过跳，没有「当前世代的有效上界」可
           快照；按 #54 票面口径（「保存的改动不改变当前世代的有效上界与用量，从下一个人类世代
           生效，在此之前作为待生效值读回」），此时报**内置默认**，已保存的覆盖只出现在
           ``pending_limit`` 上——否则保存一下就会看起来「立刻生效」。
        3. ``pending_limit`` 只在「已保存的覆盖与当前有效上界**不同**」时给出：相同就没有「下个
           世代会变」这回事，报一个与当前值相等的待生效值只会制造噪音。
        """
        row = self._conn.execute(
            "SELECT epoch_seq, used, applied_limit FROM target_budget WHERE target_id = ?", (target_id,)
        ).fetchone()
        epoch_seq = int(row["epoch_seq"]) if row is not None else 0
        used = int(row["used"]) if row is not None else 0
        saved = self._saved_budget_limit_locked(target_id)
        if row is None:
            applied: int | None = None
        else:
            applied = None if row["applied_limit"] is None else int(row["applied_limit"])
        limit = BUDGET_LIMIT if applied is None else applied
        source = BUDGET_LIMIT_SOURCE_DEFAULT if applied is None else BUDGET_LIMIT_SOURCE_CHANNEL
        return {
            "channel_id": target_id,
            "epoch_seq": epoch_seq,
            "limit": limit,
            "used": used,
            "remaining": max(limit - used, 0),
            "source": source,
            "pending_limit": saved if saved is not None and saved != limit else None,
        }

    def _applied_limit_snapshot_locked(self, target_id: str) -> int | None:
        """写 ``target_budget.applied_limit`` 时取的**快照值**（两处写入共用的唯一落点）。

        两处写入是开世代动作（:meth:`_reset_epoch_budget_locked`）与消耗首跳时的惰性建行
        （:meth:`consume_budget`）；它们都要求「快照取哪个值」与读路径解析的是同一份配置，
        因此这个取值只在这里写一遍——两处的 INSERT 形参、冲突子句仍各自不同。
        """
        return self._saved_budget_limit_locked(target_id)

    def _reset_epoch_budget_locked(self, target_id: str, epoch_seq: int) -> None:
        """开世代动作落库：把用量重置为满额，并**快照**该世代生效的上界（#53）。

        三种开世代动作（人类消息落库 / owner 改指派 / 首次认领）共用这一个写点——预算按 **target**
        一份（ADR-0004 决策 4），三种 kind 走同一段代码。``applied_limit`` 是「该世代生效上界」的
        唯一落点：写成**快照**而不是每次读配置，才同时满足「保存的新值下个世代才生效」与
        「上界跨后端重启保持」。

        调用方必须已在事务内、且持有 ``self._lock``。
        """
        self._conn.execute(
            "INSERT INTO target_budget(target_id, epoch_seq, used, applied_limit) VALUES (?, ?, 0, ?) "
            "ON CONFLICT(target_id) DO UPDATE SET epoch_seq = excluded.epoch_seq, used = 0, "
            "applied_limit = excluded.applied_limit",
            (target_id, epoch_seq, self._applied_limit_snapshot_locked(target_id)),
        )

    def budget_state(self, target_id: str) -> dict[str, Any]:
        """该 target 当前人类世代的预算状态（只读）。没有行时 used=0、epoch_seq=0。

        ``epoch_seq=0`` 表示还没有开世代动作 —— 此时预算按满额生效：预算约束的是
        「一次人类发言能引发多少 agent 往返」，没有开世代动作时没有可越界的世代。

        ``limit`` 是**当前世代的有效上界**（频道覆盖或内置默认，见 :meth:`_budget_facts_locked`），
        ``remaining`` 按它算；``used`` 是 ``target_budget.used`` 正本，不由轮次结果行求和。

        载荷只给 :data:`BUDGET_STATE_KEYS` 那五项（既有字段名一个字不改）。频道设置视图要的
        ``source`` / ``pending_limit`` 由 :meth:`channel_budget_view` 在同一份事实上补出来。

        返回载荷里的 ``channel_id`` 是 **HTTP 面既有字段名**（票面 Constraint：响应字段名一律不变），
        它承载的是这个 target 的 id——channel 型下就等于频道 id。
        """
        self.get_target(target_id)  # 404 早退
        with self._lock:
            facts = self._budget_facts_locked(target_id)
        return {key: facts[key] for key in BUDGET_STATE_KEYS}

    def channel_budget_view(self, channel_id: str) -> dict[str, Any]:
        """频道设置视图的只读读数：``budget_state`` 的五项 + ``source`` / ``pending_limit``。

        **只做扩展**（票面 Constraint：既有字段名一个字不改）：

        - ``limit`` / ``used`` / ``remaining`` / ``epoch_seq`` / ``channel_id`` 语义与既有端点逐字相同
          （其中 ``limit`` 现在是**当前世代的有效上界**，``remaining`` 按它算）；
        - ``source`` = ``channel-override``（频道覆盖）/ ``built-in-default``（内置默认），机器可读、
          不译（文案由读方各自取词条）；
        - ``pending_limit`` = 已保存、**从下一个人类世代起生效**的上界；没有待生效差异时为 ``null``。
          它绝不参与当前 ``remaining`` 的计算。
        """
        self.get_channel(channel_id)  # 频道面入口：只认 channel 型 target
        # 目标作用域 404 早退：与 ``budget_state`` 同一道门。``get_channel`` 只读 ``channel`` 行，
        # 它在「channel 行在、target 影子行缺」的事故形态里不会 404，因此这里必须补 target 这一道
        # （否则本端点会在事故库里给出一个「看起来正常」的读数）。
        self.get_target(channel_id)
        with self._lock:
            return self._budget_facts_locked(channel_id)

    def set_channel_budget_limit(self, channel_id: str, budget_limit: int) -> dict[str, Any]:
        """保存该频道的回应预算上界（**配置**，不是用量），返回保存后的只读读数。

        三条边界（ADR-0007 / 规格 #31 §2）：

        - **只写上界**：本方法不碰 ``target_budget``——``used`` 仍只由后端在开世代时重置、在一次
          实际投递的跳上推进；这里没有「重置用量」这条路。
        - **只收固定档位** ``{3, 5, 10}``：其余取值整体拒绝（不留半成品），档位集合同时由建表语句
          的列级 CHECK 兜底。
        - **从下一个人类世代生效**：这里只写配置表，当前世代的 ``applied_limit`` 快照与 ``used``
          一字不改（快照由开世代动作写，见 :meth:`_reset_epoch_budget_locked`）。于是保存后读回的
          载荷里 ``limit`` 不变、``pending_limit`` 才是新值——直到下一个人类世代开世代。**还没有
          任何世代**（``target_budget`` 无行）的频道也照这条口径报：``limit`` = 内置默认、新值只作
          ``pending_limit``（见 :meth:`_budget_facts_locked` 第 2 条）。

        人类面的 ``PATCH /api/channels/{id}/budget``（#54）是它的调用方；本票只建落点与读路径。
        """
        self.get_channel(channel_id)  # 404 早退
        self.get_target(channel_id)  # 目标作用域 404 早退（与读路径同一道门）
        if budget_limit not in BUDGET_LIMIT_TIERS:
            tiers = ", ".join(str(tier) for tier in BUDGET_LIMIT_TIERS)
            raise DomainError(
                "validation_failed",
                self._render_copy("error.budget_limit_invalid", tiers=tiers),
                fields={"budget_limit": self._render_copy("error.field_choices", values=tiers)},
            )
        with self._lock:
            self._conn.execute(
                "INSERT INTO channel_budget_limit(channel_id, budget_limit, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(channel_id) DO UPDATE SET budget_limit = excluded.budget_limit, "
                "updated_at = excluded.updated_at",
                (channel_id, int(budget_limit), utc_now()),
            )
        return self.channel_budget_view(channel_id)

    def human_epoch_seq(self, target_id: str) -> int:
        """该 target 最近一条**人类**消息的 seq（人类世代的定义，spec #1 §5）。没有则为 0。"""
        self.get_target(target_id)  # 404 早退
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS epoch_seq FROM message "
                "WHERE target_id = ? AND kind = 'human'",
                (target_id,),
            ).fetchone()
        return int(row["epoch_seq"])

    def consume_budget(self, target_id: str, epoch_seq: int, amount: int = 1) -> int:
        """把预算推进到世代 ``epoch_seq`` 并消耗 ``amount``；返回消耗后的 ``used``。

        开新世代并重置用量发生在**人类消息落库那一刻**（``append_message``），本方法只负责
        累加已经发生的投递。这里仍按 seq 兜底：若调用顺序意外让旧世代的行盖住新世代，
        用量就从 0 起算，而不是把旧世代的额度算到新世代头上。

        上界（= 该世代生效上界，见 :meth:`_budget_facts_locked`）由调用方
        （``WakeOrchestrator._spend_jump``）在同一把锁内裁决后才调用本方法 —— 这里只记事实，
        不重复做上界判定。

        惰性建行（这个 target 还没有预算行）时**连生效上界一起快照**，与开世代动作同一口径
        （两处共用 :meth:`_applied_limit_snapshot_locked`）：快照只在写入时取一次，之后读回的上界
        与 ``used`` / ``epoch_seq`` 一起成为持久事实，不再随配置漂移。
        """
        self.get_target(target_id)  # 404 早退
        with self._lock:
            row = self._conn.execute(
                "SELECT epoch_seq, used FROM target_budget WHERE target_id = ?", (target_id,)
            ).fetchone()
            previous = None if row is None else (int(row["epoch_seq"]), int(row["used"]))
            used = previous[1] if previous is not None and previous[0] >= int(epoch_seq) else 0
            used += max(int(amount), 0)
            self._conn.execute(
                "INSERT INTO target_budget(target_id, epoch_seq, used, applied_limit) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(target_id) DO UPDATE SET epoch_seq = excluded.epoch_seq, used = excluded.used",
                (target_id, int(epoch_seq), used, self._applied_limit_snapshot_locked(target_id)),
            )
        return used

    def try_consume_jump(self, target_id: str) -> dict[str, Any]:
        """team 分派的**原子**跳扣减：检查与扣减在同一条条件 UPDATE / 同一事务里。

        实现约束（ADR-0019 / R1 §3）：

        - ``BEGIN IMMEDIATE`` 先拿写锁，再解析当前世代的生效上界：``target_budget.applied_limit``
          快照（``NULL`` = 内置默认 :data:`BUDGET_LIMIT`）。快照与读路径
          （:meth:`_budget_facts_locked`）同源，不随配置漂移。
        - ``UPDATE ... SET used = used + 1 WHERE target_id = ? AND used < ?``：受影响行数为 0
          ⇒ 耗尽。不能先读后写——R1 的 100 并发对照证明那会越过上界。
        - 没有预算行（这个 target 从未开世代、也没消耗过跳）时，先在同事务里惰性建行
          （``used=0`` + 内置默认上界），否则条件 UPDATE 的 0 行会被误判成「耗尽」。
        - 返回 ``ok`` / ``epoch_seq`` / ``used`` / ``limit``：``used`` 是**扣减后**的正本读数，
          与 ``target_budget.used`` 一致；``ok=False`` 时 ``used`` 不变。

        它不负责写 ``team_assignment``（那是 :meth:`create_team_assignment` 的落点）：预算正本
        只有一个写点，运行记录不参与裁决。
        """
        self.get_target(target_id)  # 404 早退
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT epoch_seq, used, applied_limit FROM target_budget WHERE target_id = ?",
                    (target_id,),
                ).fetchone()
                if row is None:
                    # 没有行 = 还没有开世代：当前世代的有效上界是**内置默认**（NULL 快照），
                    # 已保存的频道覆盖只作 ``pending_limit``，从下一个人类世代才生效
                    # （与 ``_budget_facts_locked`` 的第 2 条读法同一口径）。
                    self._conn.execute(
                        "INSERT INTO target_budget(target_id, epoch_seq, used, applied_limit) "
                        "VALUES (?, 0, 0, NULL) ON CONFLICT(target_id) DO NOTHING",
                        (target_id,),
                    )
                    row = self._conn.execute(
                        "SELECT epoch_seq, used, applied_limit FROM target_budget WHERE target_id = ?",
                        (target_id,),
                    ).fetchone()
                if row is None:  # INSERT/ON CONFLICT 之后必有行
                    raise RuntimeError("target_budget row missing after the lazy upsert")
                epoch_seq = int(row["epoch_seq"])
                used = int(row["used"])
                limit = BUDGET_LIMIT if row["applied_limit"] is None else int(row["applied_limit"])
                cursor = self._conn.execute(
                    "UPDATE target_budget SET used = used + 1 WHERE target_id = ? AND used < ?",
                    (target_id, limit),
                )
                consumed = cursor.rowcount > 0
                self._commit()
            except Exception:
                self._rollback()
                raise
        return {
            "ok": consumed,
            "epoch_seq": epoch_seq,
            "used": used + (1 if consumed else 0),
            "limit": limit,
        }

    # ─── target 成员（唯一写点：人类面「加入」与穿透加入共用） ──────────────
    def is_target_member(self, target_id: str, member_id: str) -> bool:
        self.get_target(target_id)  # 404 早退
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM target_member WHERE target_id = ? AND member_id = ?",
                (target_id, member_id),
            ).fetchone()
        return row is not None

    def add_target_member(self, target_id: str, member_id: str) -> bool:
        """把成员加入该 target；返回是否**新增**（已在该 target 里则返回 False，幂等）。"""
        self.get_target(target_id)  # 404 早退
        self.get_member(member_id)
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO target_member(target_id, member_id) VALUES (?, ?) "
                "ON CONFLICT(target_id, member_id) DO NOTHING",
                (target_id, member_id),
            )
            inserted = cursor.rowcount > 0
            if inserted:
                self._conn.execute(
                    "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES (?, ?, 0) "
                    "ON CONFLICT(member_id, target_id) DO NOTHING",
                    (member_id, target_id),
                )
        return inserted

    # ─── 已读位置（未读的正本） ───────────────────────────────────────────
    def latest_round_status(self, member_id: str) -> str | None:
        """该成员最近一条**关于它能否说话**的轮次结果（库里的正本；没有轮次时为 None）。

        状态点的「出错」必须由**落库的事实**推导，而不是内存里的一张失败表：
        否则后端重启后状态点会回到「在线」，而库里最近一轮仍是 failed —— 同一条事实两个说法。
        按 ``finished_at`` 与 ``id`` 同序列定序，与列表端点的顺序一致。

        ``merged`` 行被跳过：被合并是**它正在跑的那一轮**的调度事实（那条事实写在同一成员自己的
        行为轮次收口时落库的行上），不是「它刚刚跑完一轮」—— 把它当最近一条轮次结果会让
        状态点在 in-flight 期间被读成在线。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM round_outcome WHERE member_id = ? AND status != 'merged' "
                "ORDER BY finished_at DESC, id DESC LIMIT 1",
                (member_id,),
            ).fetchone()
        return row["status"] if row is not None else None

    def agent_cursor(self, target_id: str, member_id: str) -> int:
        """该成员在该 target 已经读到哪一条（消费游标）。

        复用 ``target_read``——该表语义就是「每成员每 target 的已读位置」，owner 的行是未读徽标的
        正本，Agent 的行只是它自己的消费位置，两者互不干扰。没有行时为 0（从头读）。
        """
        self.get_target(target_id)  # 404 早退
        with self._lock:
            row = self._conn.execute(
                "SELECT last_read_seq FROM target_read WHERE member_id = ? AND target_id = ?",
                (member_id, target_id),
            ).fetchone()
        return int(row["last_read_seq"]) if row is not None else 0

    def mark_read(self, target_id: str, seq: int, member_id: str = OWNER_ID) -> None:
        """把未读位置单调推进到 ``min(seq, 该 target 当前最大 seq)``（无返回）。

        上界裁剪保证「把未来消息提前标为已读」不可能发生；已读位置永不回退。
        推进的是 **target** 的已读位置（ADR-0004）。

        **返回 None**（早先返回 dict）：channel 面的响应体是**外部契约**（票面 Constraint：响应字段名
        一律不变），因此它由 channel 面入口 ``WorkspaceService.mark_read`` 用 ``get_channel`` 组出；
        ``db`` 层不把「channel 型的响应长什么样」写进自己的分支里（于是查询路径上没有 kind 分支）。
        """
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.seq_invalid"),
                fields={"seq": self._render_copy("error.seq_invalid")},
            )
        self.get_target(target_id)  # 404 早退
        self.get_member(member_id)
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS last_seq FROM message WHERE target_id = ?",
                    (target_id,),
                ).fetchone()
                effective = min(seq, int(row["last_seq"]))
                self._conn.execute(
                    "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES (?, ?, ?) "
                    "ON CONFLICT(member_id, target_id) DO UPDATE SET "
                    "last_read_seq = MAX(target_read.last_read_seq, excluded.last_read_seq)",
                    (member_id, target_id, effective),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise

    def unread_count(self, target_id: str, member_id: str = OWNER_ID) -> int:
        """该 target 的未读条数（**与频道列表同一条 WHERE 形状**：``UNREAD_COUNT_SQL``）。

        形状与 ``CHANNEL_SELECT_SQL`` 里的 ``unread_count`` 子查询、``unread_total`` 汇总
        完全同源（同一个 ``UNREAD_WHERE_SQL``），**不按 kind 分支**。
        """
        self.get_target(target_id)  # 404 早退
        with self._lock:
            row = self._conn.execute(
                UNREAD_COUNT_SQL, {"member_id": member_id, "target_id": target_id}
            ).fetchone()
        return int(row[0]) if row is not None else 0

    # ─── 附件（ADR-0014：元数据落库、字节落数据目录） ─────────────────────
    def attachments_dir(self) -> Path:
        """附件的字节目录：**数据目录下的独立子目录**（库里只存相对路径）。"""
        return self.data_dir / ATTACHMENTS_DIR_NAME

    @staticmethod
    def _attachment_payload(row: sqlite3.Row) -> dict[str, Any]:
        """附件的外部形状。``url`` 是内容端点：**草稿取它会 404**（读取面不暴露未发送的上传）。"""
        return {
            "id": row["id"],
            "name": row["original_name"],
            "size": row["size_bytes"],
            "mime_type": row["mime_type"],
            "created_at": row["created_at"],
            "message_id": row["message_id"],
            "url": f"/api/attachments/{row['id']}",
        }

    def store_attachment(self, original_name: str, mime_type: str, data: bytes) -> dict[str, Any]:
        """落一个**未绑定**的附件（草稿）：字节写数据目录、元数据落库，返回标识与可读元数据。

        半成品纪律（票面硬要求：被拒的请求与失败的事务都不留库内行、不留孤儿字节）：

        - 大小 / 类型 / 文件名由**人类面**在调到这里之前拒绝（那里才有可读原因与词条）；
        - 库内行与字节写在**同一事务**里，任一步失败 ⇒ 回滚（不留行）**并**删掉刚写的字节
          （不留孤儿）。两条清理缺一不可：只回滚会留一个无人知晓的字节文件，
          只删字节会留一条指向不存在文件的元数据行。
        """
        attachment_id = new_id("att")
        relative_path = f"{ATTACHMENTS_DIR_NAME}/{attachment_id}"
        destination = self.data_dir / relative_path
        with self._lock:
            self.attachments_dir().mkdir(parents=True, exist_ok=True)
            self._begin()
            try:
                self._conn.execute(
                    "INSERT INTO attachment(id, original_name, size_bytes, mime_type, relative_path, "
                    "created_at, message_id) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (attachment_id, original_name, len(data), mime_type, relative_path, utc_now()),
                )
                destination.write_bytes(data)
                self._commit()
            except BaseException:
                # 取消（KeyboardInterrupt）同样要清干净字节：磁盘上的半成品不属于任何一条记录。
                self._rollback()
                with suppress(OSError):
                    destination.unlink()
                raise
            row = self._conn.execute("SELECT * FROM attachment WHERE id = ?", (attachment_id,)).fetchone()
        if row is None:  # pragma: no cover - 同一事务里刚提交的行
            raise NotFound(self._render_copy("error.attachment_not_found", attachment_id=attachment_id))
        return self._attachment_payload(row)

    def attachment_for_download(self, attachment_id: str) -> tuple[dict[str, Any], Path]:
        """取一个**已绑定**附件的内容描述：``(元数据, 字节路径)``。

        草稿（``message_id IS NULL``）在这里就拿不到：它是**未发送的上传**，读取面一律不暴露——
        因此内容端点对草稿回 404，而不是先给出一个「还没发出去」的中间态。
        """
        with self._lock:
            row = self._conn.execute("SELECT * FROM attachment WHERE id = ?", (attachment_id,)).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.attachment_not_found", attachment_id=attachment_id))
        if row["message_id"] is None:
            raise NotFound(self._render_copy("error.attachment_unbound"))
        return self._attachment_payload(row), self.data_dir / str(row["relative_path"])

    def _attachments_by_message_locked(self, message_id: str) -> list[dict[str, Any]]:
        """一条消息**已绑定**的附件（调用方需已持锁或正处于同一连接的事务里）。"""
        rows = self._conn.execute(ATTACHMENTS_BY_MESSAGE_SQL, (message_id,)).fetchall()
        return [self._attachment_payload(row) for row in rows]

    def _attachments_by_target_locked(
        self, target_id: str, after_seq: int = 0
    ) -> dict[str, list[dict[str, Any]]]:
        """该 target 里**已绑定**的附件，按 ``message_id`` 归组（草稿不在此列——读取面不暴露）。"""
        rows = self._conn.execute(ATTACHMENTS_BY_TARGET_SQL, (target_id, after_seq)).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row["message_id"]), []).append(self._attachment_payload(row))
        return grouped

    def _attachments_for_messages_locked(self, message_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        """多条消息的已绑定附件，按 ``message_id`` 归组（消息命中一次补齐用）。

        ``IN`` 的取值域由 ``json_each`` 从**一个**绑定参数展开（``ATTACHMENTS_BY_MESSAGES_SQL``），
        因此这里没有任何东西被拼进 SQL 文本——与上面那些模块级 ``*_SQL`` 常量同一条纪律。
        """
        if not message_ids:
            return {}
        rows = self._conn.execute(
            ATTACHMENTS_BY_MESSAGES_SQL, {"message_ids": json.dumps(list(message_ids))}
        ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row["message_id"]), []).append(self._attachment_payload(row))
        return grouped

    def _fill_message_hit_attachments_locked(self, hits: list[dict[str, Any]]) -> None:
        """给消息命中补上它**真实**的附件表：附件不是命中来源（附件不进搜索），但消息命中里嵌的
        是完整消息形状，标题里说「附件」的那个字段就不该对已挂附件的消息撒谎。"""
        message_ids = [str(hit["message"]["id"]) for hit in hits if hit["kind"] == "message"]
        attached = self._attachments_for_messages_locked(message_ids)
        for hit in hits:
            if hit["kind"] == "message":
                hit["message"]["attachments"] = attached.get(str(hit["message"]["id"]), [])

    def _bind_attachments_locked(self, attachment_ids: Sequence[str], message_id: str) -> None:
        """在**已有事务内**把草稿逐个绑定到刚落的这条消息。

        校验（存在 + 尚未绑定）与写入都在同一事务内，因此「验证通过」与「绑定落地」之间没有窗口，
        也不存在「A 绑定成功、B 失败」的半成品：B 一失败，整个事务连 A 一起回滚
        （由 :meth:`append_message` 的 ``except`` 负责）。
        """
        for attachment_id in attachment_ids:
            row = self._conn.execute(
                "SELECT message_id FROM attachment WHERE id = ?", (attachment_id,)
            ).fetchone()
            if row is None:
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.attachment_not_found", attachment_id=attachment_id),
                    fields={"attachment_ids": self._render_copy("error.field.attachment_unusable")},
                )
            if row["message_id"] is not None:
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.attachment_already_bound", attachment_id=attachment_id),
                    fields={"attachment_ids": self._render_copy("error.field.attachment_unusable")},
                )
            self._conn.execute(
                "UPDATE attachment SET message_id = ? WHERE id = ?", (message_id, attachment_id)
            )


def open_database(data_dir: Path | str | None = None) -> Database:
    return Database(data_dir if data_dir is not None else default_data_dir())
