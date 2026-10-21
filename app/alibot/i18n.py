"""后端**词条表**（`CONTEXT.md` **词条 / 词条表**）：后端生成的自然语言按**语言偏好**取值。

机制（ADR-0009 的第二层）：**后端生成的自然语言**——HTTP 错误的 ``message`` 与 ``fields`` 里的
自然语言、落库的 ``system`` 消息正文、``round_outcome.reason``、Agent 的 ``enabled_reason``、
远端数据流告知文案——由**后端按当前语言偏好渲染**。机器可读的东西**一律不译**：错误的 ``code``、
``fields`` 的**键**、``round_outcome.status`` 的词、任务状态词、``provider`` / ``model_id``。

与前端词条表（``studio/i18n.js``）的关系：**分属两层、各管各的**（ADR-0009 不许有第三层）。
两份表各有自己的键集；重合的只是「同一份文案在两侧都要说」这件事——结构化状态词由前端翻译成
界面说法，后端只在**要把它嵌进一句自然语言**时（例如「状态推进至 进行中」）自己渲染一份。

纪律：

- **源语言是简体中文**（设计系统包的文案与语气规范以它为准），``en`` 是第二语言取值。两份取值必须
  覆盖**同一个键集、无落单键**——由 ``tests/test_backend_copy.py`` 当着机器判据，不靠人眼。
- **参数是 ``{name}`` 形式**，值按字符串拼进去；缺参时让 ``format`` 直接报错（那是编程错误，
  不静默吞掉）。**只有这一层放后端文案**：调用点不再自己拼中文句子。
- **语言偏好读库**，绝不读请求头 / ``Accept-Language`` / 环境变量（ADR-0009 的决定性理由：写
  ``system`` 消息与 ``reason`` 的是**唤醒编排**，它跑在 HTTP 请求之外，没有请求头可读）。
  库内没有设置行时取**源语言**。
- **只影响新产生的文案**：已落库的 ``system`` 消息保持写入时的语言，不回填、不迁移（ADR-0001）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal, cast

# 已归一化的**语言偏好**取值：只有这两种能进入渲染（渲染取词条、缺取值会回落源语言）。
# 外部入口是 `str | None`（库里的列、HTTP 载荷），归一化在 :func:`normalize_locale` 一处完成——
# 于是「随便一个字符串当语言用」在类型层就被挡住，而不是靠约定。
Locale = Literal["zh-CN", "en"]

# 源语言：整张表的正本语言，也是「库内没有设置行 / 取值不认识」时的取值。
SOURCE_LOCALE: Locale = "zh-CN"

# 两份取值。键的数量与名字由上面的纪律约束：两边必须逐一对应（测试逐个比对）。
# 分组只是读数方便：``error.*`` 是 HTTP 错误面，``system.*`` 是落库的系统事件消息，
# ``status.task.*`` 是**嵌进自然语言**的任务状态说法，``reason.*`` 是轮次结果的原因，
# ``availability.*`` 是「为什么这个成员现在不能跑」，``seam.*`` 是适配层的一轮结论，
# ``remote.*`` 是远端数据流告知。
CATALOGS: dict[str, dict[str, str]] = {
    "zh-CN": {
        # ─── 通用 ────────────────────────────────────────────────────────
        "common.list_sep": "、",
        # ─── HTTP 错误：机器可读的 code / fields 键不在这里 ───────────────
        "error.round_not_running": "本轮已结束、已请求中断或不属于当前 target",
        "error.round_outcomes_immutable": "轮次结果不可编辑、不可删除：人类面不存在该端点",
        # 本键的**语义位置**是「预算的用量面不可写」（ADR-0007）。#54 打开上界写入口后它不能再
        # 宣称「不存在写端点」——那句在实机上已经是假话；留着键、改说边界：
        "error.budget_read_only": (
            "回应预算的用量只读：人类面只能改上界（PATCH），已用跳数与世代由后端独占维护"
        ),
        # 写入口拒绝**请求体里的用量字段**时用它（比上一条更具体：这一次提交的是用量）。
        "error.budget_used_read_only": (
            "回应预算的用量与世代事实只读：人类面只能改上界（PATCH），used 由后端独占维护"
        ),
        # #53 的配置落点：上界只接受固定档位，非法值整体拒绝（#31 §2 人定）。
        "error.budget_limit_invalid": "回应预算上界只接受固定档位：{tiers}，未保存任何改动",
        "error.channel_members_append_only": "频道成员只能加入、不能移出：人类面不存在该端点",
        "error.messages_immutable": "消息不可编辑、不可删除：人类面不存在该端点",
        "error.agent_delete_unsupported": "Agent 不可删除：人类面不存在该端点",
        "error.request_body_invalid": "请求体校验未通过",
        "error.field.body_invalid": "请求体不合法：{location}（{kind}）",
        "error.tier_value_null": "档位取值不能为 null（要保留原值请省略该字段）",
        "error.agent_field_null": "该字段不能为 null（要保留原值请省略该字段）",
        # 提醒（#68）：同一条「文本 / 时间」形状的校验在两个入口（新增 / 删除）都指向它。
        "error.reminder_invalid": "提醒校验未通过，未创建任何数据",
        "error.reminder_text_empty": "提醒内容不能为空",
        "error.reminder_text_too_long": "提醒内容最多 {limit} 个字符",
        "error.reminder_time_shape": "提醒时间需要是日期加钟点，例如 2026-10-18 09:00（现只支持一次性提醒，不支持重复）",
        "error.reminder_not_found": "提醒不存在：{reminder_id}",
        "availability.orchestrator_absent": "后端未启用成员面编排",
        "error.agent_transfer_needs_orchestrator": "Agent 转派任务需要启用成员面编排",
        "error.channel_member_join_failed": "加入频道校验未通过，未加入任何成员",
        "error.member_id_empty": "成员标识不能为空",
        "error.member_unavailable": "成员不存在或已删除：{member_id}",
        "error.member_not_agent": "初始成员只能是 Agent：{member_id}",
        "error.agent_invalid": "Agent 校验未通过，未创建任何数据",
        "error.agent_update_invalid": "Agent 配置校验未通过，未改动任何字段",
        "error.field.agent_workspace_backend_invalid": "工作区后端只能是这些之一：{values}",
        "error.field.agent_permission_mode_invalid": "权限档只能是这些之一：{values}",
        "error.field.agent_web_access_invalid": "联网档只能是这些之一：{values}",
        "error.field.agent_docker_image_too_long": "Docker 镜像引用过长（上限 {limit} 字符）",
        "error.model_id_empty": "模型标识不能为空",
        "error.model_id_provider_prefix": "模型标识需要提供商前缀：{model_id}（应为 provider:model，例如 dashscope:qwen-max）",
        "error.model_id_unknown_provider": "未知的模型提供商：{provider}（可用：{providers}）",
        "error.model_id_missing_name": "模型标识缺少模型名：{model_id}（应为 provider:model）",
        "error.unknown_provider": "未知的模型提供商",
        "error.field_choices": "必须是这些之一：{values}",
        "error.provider_not_remote": "{provider} 是本机模型提供商，不需要远端数据流告知",
        "error.provider_consent_not_needed": "本机提供商不需要确认",
        "error.remote_scope_kind_invalid": "远端数据面类型只能是 model、mcp、docker 或 web",
        "error.remote_scope_id_empty": "远端数据面的标识不能为空",
        "error.web_provider_invalid": "联网 provider 只能是这些之一：{values}",
        "error.web_provider_credential_invalid": "联网 provider 凭据校验未通过，未保存任何改动",
        "error.field.web_provider_key_empty": "先保存这家的凭据，才能把它设为默认",
        "error.field.web_provider_key_too_long": "凭据过长（上限 {limit} 个字符）",
        "error.task_action_wrong_target": "Agent 任务动作只能操作当前任务线程",
        "error.task_action_not_member": "只有任务成员可以更新任务状态",
        "error.continue_not_assignee": "只有这张任务的受派者可以申请自动续跑",
        "error.task_action_rejected": "Agent 任务动作被拒绝：{message}",
        # ─── 落库的系统事件消息（写入时的语言，不回填） ───────────────────
        "system.task.created": "任务已创建：{title}",
        "system.task.status": "状态推进至 {status}",
        "system.task.title": "标题已更改为：{title}",
        "system.task.assignee": "已指派给 {name}",
        "system.task.unassigned": "指派已取消",
        "system.task.claimed": "已认领：{name} 接手了这张任务",
        # 认领在同一个事务里落下的**第二条**记录（ADR-0016 二）：新键，不与 `system.task.status` 共键。
        # 后者继续只表示「有人推了状态」。
        "system.task.claim_started": "认领后自动开工：状态推进至 {status}",
        # 自动续跑（ADR-0027）：受派者用 `continue_task` 申请后，编排先落这条记录再唤醒同一人。
        # 它与「认领后自动开工」同族，但说的是另一件事：这一次是**受派者自己**申请继续干。
        "system.task.continue": "自动续跑：受派者申请继续推进这张任务",
        "system.task.continue_paused": (
            "自动续跑已暂停：本次人类世代内这张任务已跑满 {rounds} 轮；任务仍在进行中，发一条消息可继续"
        ),
        "system.member.pierced_join": (
            "{name} 加入了频道：它被点名时并不在此频道，回复时顺带加入（成员变化只随真实回复发生）"
        ),
        # 提醒的**到点**产物（ADR-0017 §一 / C18）：它是落库的 ``system`` 消息正文，由到期投递器在
        # HTTP 请求之外写入、没有请求头可读 ⇒ 只能按**库内语言偏好**渲染；写入后是 append-only 取证面，
        # **不随偏好切换回溯**。准点与补投**必须是两个键**（C21）：分档判据是投递时刻与到期时刻之差，
        # 不靠参数区分。这两条**只**在本表（前端词条表不许登记同一句）。
        "system.reminder.fired": "提醒到点了：{text}",
        "system.reminder.fired_late": "补投提醒（原定时间已过）：{text}",
        # ─── 嵌进自然语言的任务状态说法（结构化状态词本身仍是 ``status`` 那一列） ──
        "status.task.todo": "待认领",
        "status.task.in_progress": "进行中",
        "status.task.in_review": "待审",
        "status.task.done": "完成",
        "status.task.closed": "已关闭",
        # ─── 轮次结果的原因 ──────────────────────────────────────────────
        "reason.budget_exhausted": (
            "本 target 人类世代（epoch_seq={epoch}）的 agent→agent 预算已用尽（≤{limit}）："
            "本条 Agent 消息照常落库，但没有投递任何唤醒"
        ),
        "reason.mention_ignored": (
            "被个人提及是确定信号，必须回应；本轮自判忽略，按失败处理（原因为：{reason}）"
        ),
        "reason.mention_no_body": "被个人提及是确定信号，但本轮没有产出可落库的正文，按失败处理",
        # 声明沉默在确定信号下同样按失败处理（CONTEXT.md 的**回应判断**词条）：它是「不想说」，
        # 不是「没被问」，所以不能像空正文那样静默——诊断要把原始的 silent 理由带出来。
        "reason.mention_silent": (
            "被个人提及是确定信号，必须回应；本轮模型声明沉默，按失败处理（原因为：{reason}）"
        ),
        # 认领唤醒的两个**同级**文案（D6：与 mention 那一对同级，不是复用它们的正文——
        # 否则诊断会写成「被个人提及」，而那是一个不存在的事实）。
        "reason.claim_ignored": (
            "认领唤醒是确定信号，必须回应；本轮自判忽略，按失败处理（原因为：{reason}）"
        ),
        "reason.claim_no_body": (
            "认领唤醒是确定信号，但本轮没有产出可落库的正文、也没有提交任务动作，按失败处理"
        ),
        "reason.claim_silent": (
            "认领唤醒是确定信号，必须回应；本轮模型声明沉默，按失败处理（原因为：{reason}）"
        ),
        # 认领轮的可读后缀（ADR-0016 五）：照 `reason.pierced_suffix` 的先例追加进 reason 文本。
        # `reason` 只承担可读诊断，**不**承担纪律判据（那是进程内枚举 `ReplySignal` 的事）。
        "reason.claim_round": "{reason}；本轮由**认领**唤醒（编排把这张任务交给你，你必须回应）",
        # 自动续跑（ADR-0027）：第五类确定信号。它与认领同级，但来源是**受派者上一轮的申请**——
        # 复用认领那套会把这一轮写成「编排把任务交给你」，而那件事在上一轮已经发生过了。
        "reason.continue_ignored": (
            "自动续跑是确定信号，必须回应；本轮自判忽略，按失败处理（原因为：{reason}）"
        ),
        "reason.continue_no_body": (
            "自动续跑是确定信号，但本轮没有产出可落库的正文、也没有提交任务动作，按失败处理"
        ),
        "reason.continue_silent": (
            "自动续跑是确定信号，必须回应；本轮模型声明沉默，按失败处理（原因为：{reason}）"
        ),
        "reason.continue_round": (
            "{reason}；本轮由**自动续跑**唤醒（你上一轮申请继续推进这张任务，你必须回应）"
        ),
        # 队长的「任务变化」唤醒（#179 / ADR-0025 §六）：建任务 / 改派 / 转派把 team 队长叫醒的那一轮。
        # 它与认领轮**同级**（都是确定信号、同一个 `ReplySignal` 判据），但与认领**不同源**——
        # team 频道不发生认领，复用 `reason.claim_*` 会写下一条不存在的事实。因此四条词条与认领那套并列，
        # 用词是「任务变化」而不是「认领」。
        "reason.team_change_ignored": (
            "队长的任务变化唤醒是确定信号，必须回应；本轮自判忽略，按失败处理（原因为：{reason}）"
        ),
        "reason.team_change_no_body": (
            "队长的任务变化唤醒是确定信号，但本轮没有产出可落库的正文、也没有提交任务动作，按失败处理"
        ),
        "reason.team_change_silent": (
            "队长的任务变化唤醒是确定信号，必须回应；本轮模型声明沉默，按失败处理（原因为：{reason}）"
        ),
        "reason.team_change_round": (
            "{reason}；本轮由**任务变化**唤醒（team 队长被建任务 / 改派 / 转派叫醒，你必须回应）"
        ),
        # 人类消息唤醒（#189）：第四类确定信号。它与上面三套**并列**、**同级**，但来源是「人类直接
        # 对你说话」——因此既不能复用「任务变化」（那是建任务/改派才成立的事实），也不能复用
        # 「认领」或「被个人提及」。DM / autonomous channel / team 频道 / task 线程四个面共用这组词条。
        "reason.human_message_ignored": (
            "人类消息是确定信号，必须回应；本轮自判忽略，按失败处理（原因为：{reason}）"
        ),
        "reason.human_message_no_body": (
            "人类消息是确定信号，必须回应；本轮没有产出可落库的正文、也没有提交任务动作，按失败处理"
        ),
        "reason.human_message_silent": (
            "人类消息是确定信号，必须回应；本轮模型声明沉默，按失败处理（原因为：{reason}）"
        ),
        "reason.human_message_round": ("{reason}；本轮由**人类消息**唤醒（人类直接对你说话，你必须回应）"),
        "reason.pierced_suffix": (
            "{reason}；本轮是被点名的**穿透**唤醒（它原本不在该频道），回复时顺带加入，"
            "system 事件消息 seq={seq}"
        ),
        "reason.merged": (
            "该成员已有一轮在进行中：{count} 次重复唤醒（{bases}）合并进那一轮，"
            "以最大 seq #{largest} 为准；不排队、不重放"
        ),
        "reason.round_exception": "轮次内部异常：{type}: {error}",
        "reason.seam_exception": "Agent 适配层异常：{type}: {error}",
        # ─── 「为什么这个成员现在不能跑」 ────────────────────────────────
        "availability.no_model": "未配置模型：请先在 Agent 配置里填写模型标识（provider:model）",
        "availability.disabled": "该 Agent 的模型配置已停用",
        "availability.model_provider_prefix": "模型标识缺少提供商前缀：{model_id}（应为 provider:model，例如 dashscope:qwen-max）",
        "availability.model_name_missing": "模型标识缺少模型名：{model_id}（应为 provider:model）",
        "availability.remote_consent": (
            "尚未确认远端数据流告知：{provider} 会把{faces}发送到本机之外。确认后该 Agent 才会启用"
        ),
        "availability.credential_missing": "模型提供商 {provider} 缺少凭证：请先设置环境变量 {env}",
        "availability.credential_none": "（该提供商无需凭证）",
        "availability.credential_unreadable": "无法解析 {provider} 的凭证：{type}: {error}",
        # ─── 适配层的一轮结论 ────────────────────────────────────────────
        "seam.timeout": "模型调用超时（{seconds} 秒）",
        "seam.team_no_leader_reply": "team 轮次结束时队长没有产出最终回复",
        "seam.call_failed": "模型调用失败：{type}: {error}",
        "seam.reply_error": "模型返回错误：{error}",
        "seam.task_action_only": "模型通过任务工具提交了结构化动作",
        "seam.no_body": "模型没有产出可落库的正文（本轮自判不回应）",
        "error.field.agent_runtime_invalid": "运行时配置取值不合法：{field}",
        "seam.replied": "模型自主判断需要回应",
        "seam.exceed_max_iters": "达到迭代上限，结论可能不完整",
        "seam.interrupted": "本轮已由用户中断",
        "seam.workspace_degraded": "Docker 工作区不可用，本轮停用工作区工具并继续对话：{error}",
        # ``silent``（#58）：声明来自**结构化通道**（declare_silence 工具），不是从正文猜的。
        "seam.silent": "模型经结构化通道（declare_silence 工具）声明本轮沉默，没有产出正文",
        "seam.silence_with_action": (
            "模型声明了本轮沉默，但同时提交了结构化任务动作（本轮以动作为准，不产出正文）"
        ),
        "seam.unknown_credential_type": "AgentScope 不认识这个凭证类型：{credential_type}",
        "seam.approval_timeout": "审批等待超时（{seconds} 秒）：本轮失败",
        # ─── Docker 工作区（#150 / ADR-0021 §二） ─────────────────────────
        "workspace.docker-daemon": "Docker 不可用：{error}",
        "workspace.docker-dependency": "Docker 后端缺少依赖：{error}（请安装 aiodocker）",
        "workspace.docker-consent": "远端 Docker 镜像需要先确认数据面告知：{image}（未确认不拉取）",
        "workspace.docker-timeout": "Docker 工作区初始化超时（{seconds} 秒）：{image}",
        "workspace.docker-image-failed": "Docker 镜像拉取/构建失败：{image}：{error}",
        "workspace.docker-mount": "Docker 工作区挂载失败（{type}）：{error}",
        "workspace.docker-failed": "Docker 工作区启动失败（{type}）：{error}",
        # #187 / ADR-0026 §五：容器默认无网。这两条是**工具结果**里的可读原因（不是超时），
        # 与上面那一族同住一处，于是「为什么没有出网」只有一份说法、两种语言。
        "workspace.docker-egress-disabled": (
            "本频道的容器网络策略未允许出网：{tool} 没有发起任何请求"
            "（在频道配置里允许容器出网后，只有白名单 host 会放行）"
        ),
        "workspace.docker-egress-host": (
            "容器的出网白名单没有 {host}：{tool} 没有发起任何请求"
            "（白名单来自 fetch 的 allow 规则，当前为 {hosts}）"
        ),
        # ─── 权限规则 / 审批（#149） ──────────────────────────────────────
        "error.permission_rule_scope_invalid": "权限规则作用域只能是 channel 或 agent",
        "error.permission_rule_tool_empty": "权限规则的 tool_name 不能为空",
        "error.permission_rule_behavior_invalid": "权限规则行为只能是 allow / deny / ask",
        "error.permission_rule_scope_unknown": "权限规则作用域不存在：{scope_id}",
        "error.permission_rule_not_found": "权限规则不存在：{rule_id}",
        # ─── MCP 声明与装配（#151 / ADR-0021 §三） ───────────────────────
        "error.mcp_name_invalid": "MCP 声明名只能由字母、数字、下划线或连字符组成（最长 64）",
        "error.mcp_transport_invalid": "MCP 传输只能是 stdio、http 或 sse",
        "error.mcp_command_required": "stdio 传输必须提供 command",
        "error.mcp_url_required": "远端 MCP 必须提供 url",
        "error.mcp_url_invalid": "MCP url 必须是 http 或 https 绝对地址",
        "error.mcp_sse_url_invalid": "sse 传输的 url 必须以 /sse 或 /messages/ 结尾",
        "error.mcp_http_url_invalid": "http 传输的 url 不能指向 /sse 或 /messages/（那是 sse 传输）",
        "error.mcp_args_invalid": "MCP args 必须是字符串列表",
        "error.mcp_env_names_invalid": "MCP env_names 只能包含合法的环境变量名（只存名字，不存值）",
        "error.mcp_scope_invalid": "MCP 声明作用域只能是 channel 或 agent",
        "error.mcp_scope_unknown": "MCP 声明作用域不存在：{scope_id}",
        "error.mcp_server_not_found": "MCP 声明不存在：{server_id}",
        "error.mcp_server_conflict": "同一作用域内已存在同名 MCP 声明：{name}",
        "error.mcp_consent_local": "stdio MCP 在本机运行，不需要远端数据面确认",
        "error.skill_scope_invalid": "Skill 作用域只能是 channel 或 agent",
        "error.skill_scope_unknown": "Skill 作用域不存在：{scope_id}",
        "error.skill_workspace_root_missing": "频道没有配置 workspace_root，Skill 没有落点",
        "error.skill_workspace_root_invalid": "频道 workspace_root 不可用：{reason}",
        "error.skill_channel_ambiguous": "成员属于多个配置了项目根的频道，请指定 channel_id",
        "error.skill_member_not_in_channel": "成员不在该频道：{channel_id}",
        "error.skill_source_invalid": "Skill 来源必须是本机绝对目录",
        "error.skill_source_not_directory": "Skill 来源目录不存在或不是目录",
        "error.skill_manifest_missing": "技能根目录缺少 SKILL.md",
        "error.skill_front_matter_invalid": "SKILL.md 的 front matter 无法解析：{reason}",
        "error.skill_name_invalid": "SKILL.md 的 name 只能由字母、数字、下划线、点或连字符组成（最长 64）",
        "error.skill_description_missing": "SKILL.md 的 front matter 必须包含 description",
        "error.skill_description_too_long": "SKILL.md 的 description 最长 {limit} 个字符",
        "error.skill_file_too_large": "技能内的单个文件超过 {limit} 字节",
        "error.skill_too_large": "技能解包后超过 {limit} 字节",
        "error.skill_too_many_files": "技能文件数超过 {limit}",
        "error.skill_path_invalid": "技能路径不安全（绝对路径、..、symlink 或特殊文件）",
        "error.skill_archive_invalid": "上传的 zip 无法读取：{reason}",
        "error.skill_hash_invalid": "Skill 的内容 hash 形态不合法",
        "error.skill_not_found": "Skill 不存在：{skill_id}",
        "error.skill_conflict": "同一作用域内已存在同名 Skill：{name}",
        "mcp.runtime.consent_required": "远端 MCP 尚未确认数据面告知，未连接：{name}",
        "mcp.runtime.connect_failed": "MCP 连接失败：{name}",
        "error.approval_not_found": "审批请求不存在：{reply_id}",
        "error.approval_state_conflict": "审批请求已是 {state}，不能再提交决策",
        "error.approval_tool_call_unknown": "未知的待审批工具调用：{tool_call_id}",
        "error.approval_tool_call_decided": "该工具调用已经以相反结论决策过：{tool_call_id}",
        "error.approval_decision_duplicate": "同一请求里重复提交了工具调用：{tool_call_id}",
        "error.approval_state_invalid": "审批状态过滤只能是：{states}",
        # ─── 远端数据流告知 ──────────────────────────────────────────────
        "remote.face.body": "消息正文",
        "remote.face.author": "发送者显示名",
        "remote.face.channel": "频道名称与描述",
        "remote.face.mention": "被点名成员的显示名",
        "remote.statement": (
            "启用远端模型后，以下数据会离开本机并发送给所配置的模型提供商：{faces}。"
            "接收方可能是远端服务。本机存储不做应用层端到端加密，"
            "磁盘上的数据库按操作系统文件权限保护；后端默认只监听 loopback。"
        ),
        # ─── 持久化层（db.py）的可见文案 ─────────────────────────────────
        # 同一份纪律：这里放的是**用户可见**的自然语言（HTTP 的 code / fields 键、列名、
        # 状态词、SQL 一律不进来）。字段值的措辞与 service 层共用同一批键（例如
        # ``error.member_unavailable``），以免同一件事在两处各说各话。
        "error.unknown_member_kind": "未知的成员类型：{kind}",
        "error.field.member_kind_invalid": "成员类型非法",
        "error.member_not_found": "成员不存在：{member_id}",
        "error.agent_not_found": "Agent 不存在：{member_id}",
        "error.field.agent_name_empty": "Agent 名称不能为空",
        "error.field.agent_name_too_long": "Agent 名称最多 {limit} 个字符",
        "error.field.model_id_too_long": "模型标识最多 {limit} 个字符",
        "error.field.role_too_long": "角色最多 {limit} 个字符",
        "error.field.system_prompt_too_long": "系统提示最多 {limit} 个字符",
        "error.field.provider_empty": "模型提供商不能为空",
        "error.settings_invalid": "工作区设置校验未通过，未改动任何档位",
        "error.unknown_round_status": "未知的轮次结果状态：{status}",
        "error.field.round_status_invalid": "轮次结果状态非法",
        "error.target_not_found": "target 不存在：{target_id}",
        "error.task_title_empty": "任务标题不能为空",
        "error.task_not_found": "任务不存在：{task_id}",
        "error.task_missing_for_target": "任务 target 没有关联任务：{target_id}",
        "error.unknown_task_status": "未知的任务状态：{status}",
        "error.field.task_status_invalid": "任务状态非法",
        "error.task_transition_invalid": "任务不能从 {current} 迁移到 {target}；closed 是终态",
        "error.field.task_transition_invalid": "不允许从 {current} 迁移到 {target}",
        "error.task_self_approve": "被指派 Agent 不能批准自己的待审任务",
        "error.field.task_self_approve": "in_review → done 必须由指派者之外的成员批准",
        "error.assignee_missing": "指派对象不存在",
        "error.field.assignee_missing": "Agent 不存在",
        "error.assignee_not_agent": "任务只能指派给未删除的 Agent",
        "error.field.assignee_not_agent": "指派对象必须是 Agent",
        "error.transfer_not_assignee": "只有当前受派 Agent 可以转派这项任务",
        "error.transfer_needs_target": "Agent 转派必须指定另一位 Agent",
        "error.reassign_not_allowed": "只有 owner 或当前受派 Agent 可以改派任务",
        "error.unknown_target_kind": "未知的 target 类型：{kind}",
        "error.field.target_kind_invalid": "target 类型非法",
        "error.channel_target_explicit": "channel 型 target 由频道创建时落影子行，不经此处",
        "error.field.channel_target_explicit": "channel 型 target 复用 channel.id",
        "error.channel_not_found": "频道不存在：{channel_id}",
        "error.dm_peer_invalid": "私信对象必须是存在且未删除的 Agent",
        "error.field.dm_peer_invalid": "Agent 不存在或不可用：{member_id}",
        "error.dm_creator_not_owner": "私信创建者必须是 owner",
        "error.dm_not_found": "私信不存在：{dm_id}",
        "error.field.channel_name_empty": "频道名称不能为空",
        "error.field.channel_name_too_long": "频道名称最多 {limit} 个字符",
        "error.field.channel_description_too_long": "频道描述最多 {limit} 个字符",
        "error.channel_invalid": "频道校验未通过，未创建任何数据",
        # ─── 频道配置（team / workspace；#142 / ADR-0018 / ADR-0021） ─────
        "error.channel_config_invalid": "频道配置校验未通过，未改动任何字段",
        "error.field.channel_config_field_null": "该配置字段不能为 null（要保留原值请省略该字段）",
        "error.field.channel_config_unknown": "未知的频道配置字段",
        "error.field.channel_mode_invalid": "频道模式只能是这些之一：{values}",
        "error.field.channel_leader_required": "team 模式必须指定一个队长",
        "error.field.channel_leader_unavailable": "队长必须是该频道里未删除的 Agent：{member_id}",
        "error.field.channel_workspace_backend_invalid": "工作区后端只能是这些之一：{values}",
        "error.field.channel_permission_mode_invalid": "权限档只能是这些之一：{values}",
        "error.field.channel_web_access_invalid": "联网档只能是这些之一：{values}",
        "error.field.channel_workspace_root_not_absolute": "项目根必须是绝对路径：{path}",
        "error.team_run_status_invalid": "team 运行状态非法",
        "error.team_run_not_found": "team 运行不存在：{team_run_id}",
        "error.plan_run_status_invalid": "Plan 运行状态非法",
        "error.plan_run_not_found": "Plan 运行不存在：{plan_run_id}",
        "error.plan_base_seq_invalid": "Plan 的基础序号必须是非负整数",
        "error.sop_name_required": "SOP 名称不能为空",
        "error.sop_steps_required": "SOP 至少需要一个步骤",
        "error.sop_step_subject_required": "SOP 步骤必须有主题",
        "error.sop_step_executor_required": "SOP 步骤必须有 executor 成员",
        "error.sop_max_attempts_invalid": "SOP 步骤的 max_attempts 必须是正整数",
        "error.sop_member_unavailable": "SOP 参与者必须是未删除的 Agent：{member_id}",
        "error.sop_member_disabled": "SOP 参与者已停用：{member_id}",
        "error.sop_definition_not_found": "SOP 定义不存在：{definition_id}",
        "error.sop_run_not_found": "SOP 运行不存在：{sop_run_id}",
        "error.sop_phase_invalid": "SOP 运行阶段非法",
        "error.sop_state_required": "SOP 运行状态不能为空",
        "error.sop_definition_disabled": "SOP 已停用，不能启动",
        "error.sop_run_not_awaiting": "SOP 运行不在 awaiting 状态：{phase}",
        "error.sop_resume_reply_missing": "无法确定要恢复的等待标识；请显式提供 reply_id",
        "error.sop_agent_unavailable": "当前成员面适配器不支持装配 SOP 参与者",
        "error.sop_resume_invalid": "SOP 恢复载荷不合法",
        "error.sop_run_busy": "SOP 运行正在执行中",
        "error.team_assignment_state_invalid": "team 分派状态非法",
        "error.team_assignment_jump_invalid": "team 分派的跳数只能是 0 或 1",
        "error.team_assignment_not_found": "team 分派不存在：{assignment_id}",
        "team.leader_missing": "该频道未设置队长",
        "team.leader_deleted": "队长已被删除：{member_id}",
        "team.leader_disabled": "队长已停用：{member_id}",
        "team.leader_not_member": "队长不是该频道的成员：{member_id}",
        "error.unknown_message_kind": "未知的消息类型：{kind}",
        "error.field.message_kind_invalid": "消息类型非法",
        "error.message_body_empty": "消息正文不能为空",
        "error.message_body_too_long": "消息正文最多 {limit} 个字符",
        "error.task_ref_missing": "task_ref 指向的任务不存在",
        "error.field.task_ref_missing": "任务不存在",
        "error.task_ref_other_target": "task_ref 必须与消息位于同一 target",
        "error.field.task_ref_other_target": "不能引用其他 target 的任务",
        "error.message_not_found": "消息不存在：{target_id}#{seq}",
        "error.seq_invalid": "seq 必须是非负整数",
        # ─── 附件（ADR-0014；#67）────────────────────────────────────────
        "error.attachment_rejected": "附件校验未通过，未保存任何数据",
        "error.attachment_name_empty": "附件文件名不能为空",
        "error.attachment_name_too_long": "附件文件名最多 {limit} 个字符",
        "error.attachment_empty": "附件内容为空",
        "error.attachment_too_large": "附件超过单文件上限（{limit} 字节）",
        "error.attachment_type_not_allowed": "不支持的附件类型：{mime}（可用：{types}）",
        "error.attachment_not_found": "附件不存在：{attachment_id}",
        "error.attachment_unbound": "该附件尚未随消息发送：未绑定的上传不暴露",
        "error.attachment_already_bound": "该附件已绑定到一条消息，不能重复绑定：{attachment_id}",
        "error.attachments_immutable": "附件不可编辑、不可删除：人类面不存在该端点",
        "error.field.attachment_unusable": "附件不存在或已被绑定",
    },
    "en": {
        # ─── 通用 ────────────────────────────────────────────────────────
        "common.list_sep": ", ",
        # ─── HTTP 错误 ───────────────────────────────────────────────────
        "error.round_not_running": "This round has ended, was already interrupted, or belongs to another target",
        "error.round_outcomes_immutable": (
            "Round outcomes cannot be edited or deleted: the human surface has no such endpoint"
        ),
        "error.budget_read_only": (
            "Response-budget usage is read-only: the human surface can only change the upper bound "
            "(PATCH); used jumps and the epoch stay backend-owned"
        ),
        "error.budget_used_read_only": (
            "Response-budget usage and epoch facts are read-only: the human surface can only change "
            "the upper bound (PATCH); used stays backend-owned"
        ),
        "error.budget_limit_invalid": (
            "The response-budget upper bound accepts only the fixed tiers {tiers}; nothing was saved"
        ),
        "error.channel_members_append_only": (
            "Channel members can only be added, never removed: the human surface has no such endpoint"
        ),
        "error.messages_immutable": (
            "Messages cannot be edited or deleted: the human surface has no such endpoint"
        ),
        "error.agent_delete_unsupported": (
            "An Agent cannot be deleted: the human surface has no such endpoint"
        ),
        "error.request_body_invalid": "Request body failed validation",
        "error.field.body_invalid": "The request body is not valid: {location} ({kind})",
        "error.tier_value_null": "A tier value cannot be null (omit the field to keep the current value)",
        "error.agent_field_null": "This field cannot be null (omit it to keep the current value)",
        # Reminders (#68): the text / time shape check both entry points point at.
        "error.reminder_invalid": "Reminder validation failed; nothing was created",
        "error.reminder_text_empty": "Reminder text cannot be empty",
        "error.reminder_text_too_long": "Reminder text is limited to {limit} characters",
        "error.reminder_time_shape": "A reminder time needs a date and a clock time, such as 2026-10-18 09:00 (reminders fire once; repetition is not supported)",
        "error.reminder_not_found": "No such reminder: {reminder_id}",
        "availability.orchestrator_absent": "The backend runs without the member-surface orchestrator",
        "error.agent_transfer_needs_orchestrator": (
            "An Agent handing a task to another Agent requires the member-surface orchestrator"
        ),
        "error.channel_member_join_failed": "Channel join validation failed; no member was added",
        "error.member_id_empty": "The member id cannot be empty",
        "error.member_unavailable": "No such member, or it was deleted: {member_id}",
        "error.member_not_agent": "Initial members must be Agents: {member_id}",
        "error.agent_invalid": "Agent validation failed; nothing was created",
        "error.agent_update_invalid": ("Agent configuration validation failed; no field was changed"),
        "error.field.agent_workspace_backend_invalid": ("The workspace backend must be one of: {values}"),
        "error.field.agent_permission_mode_invalid": ("The permission mode must be one of: {values}"),
        "error.field.agent_web_access_invalid": ("Web access must be one of: {values}"),
        "error.field.agent_docker_image_too_long": (
            "The Docker image reference is too long (limit {limit} characters)"
        ),
        "error.model_id_empty": "The model id cannot be empty",
        "error.model_id_provider_prefix": (
            "The model id needs a provider prefix: {model_id} (expected provider:model, "
            "for example dashscope:qwen-max)"
        ),
        "error.model_id_unknown_provider": "Unknown model provider: {provider} (available: {providers})",
        "error.model_id_missing_name": (
            "The model id is missing a model name: {model_id} (expected provider:model)"
        ),
        "error.unknown_provider": "Unknown model provider",
        "error.field_choices": "Must be one of: {values}",
        "error.provider_not_remote": (
            "{provider} is a local model provider; the remote data-flow notice does not apply"
        ),
        "error.provider_consent_not_needed": "Local providers need no acknowledgement",
        "error.remote_scope_kind_invalid": "A remote data-flow kind must be model, mcp, docker, or web",
        "error.remote_scope_id_empty": "A remote data-flow identifier cannot be empty",
        "error.web_provider_invalid": "A web provider must be one of: {values}",
        "error.web_provider_credential_invalid": (
            "Web provider credential validation failed; nothing was saved"
        ),
        "error.field.web_provider_key_empty": (
            "Save a credential for this provider before making it the default"
        ),
        "error.field.web_provider_key_too_long": ("The credential is too long (limit: {limit} characters)"),
        "error.task_action_wrong_target": ("An Agent task action can only touch the task thread it runs in"),
        "error.task_action_not_member": "Only task members can update the task status",
        "error.continue_not_assignee": "Only the assignee of this task can request auto-continue",
        "error.task_action_rejected": "The Agent task action was rejected: {message}",
        # ─── 落库的系统事件消息 ──────────────────────────────────────────
        "system.task.created": "Task created: {title}",
        "system.task.status": "Status moved to {status}",
        "system.task.title": "Title changed to: {title}",
        "system.task.assignee": "Assigned to {name}",
        "system.task.unassigned": "Assignment cleared",
        "system.task.claimed": "Claimed: {name} took this task",
        # The **second** record a claim writes in the same transaction (ADR-0016 二): a new key,
        # deliberately not shared with `system.task.status`, which keeps meaning "someone moved the status".
        "system.task.claim_started": "Claim started the work: status moved to {status}",
        # Auto-continue (ADR-0027): after the assignee asks with `continue_task`, orchestration writes
        # this record first and then wakes the same member. Same family as the claim record above, but
        # a different fact: this time the **assignee itself** asked to keep going.
        "system.task.continue": "Auto-continue: the assignee asked to keep working on this task",
        "system.task.continue_paused": (
            "Auto-continue paused: this task has used all {rounds} rounds for this human generation; "
            "it is still in progress — send a message to continue"
        ),
        "system.member.pierced_join": (
            "{name} joined the channel: it was mentioned while outside this channel and joined "
            "along with its reply (membership only changes alongside a real reply)"
        ),
        # A reminder **coming due** (ADR-0017 §一 / C18): this is the stored body of a `system` message,
        # written by the due deliverer outside any HTTP request, so there is no request header to read —
        # it renders from the **stored locale preference** and never gets rewritten afterwards.
        # On time and late are **two keys** (C21): the tiering criterion is the gap between the delivery
        # moment and the due moment, not a parameter. These belong to this table only.
        "system.reminder.fired": "Reminder is due: {text}",
        "system.reminder.fired_late": "Reminder delivered late (its due time has passed): {text}",
        # ─── 嵌进自然语言的任务状态说法 ──────────────────────────────────
        "status.task.todo": "Unclaimed",
        "status.task.in_progress": "In progress",
        "status.task.in_review": "In review",
        "status.task.done": "Done",
        "status.task.closed": "Closed",
        # ─── 轮次结果的原因 ──────────────────────────────────────────────
        "reason.budget_exhausted": (
            "The agent→agent budget of this target's human epoch (epoch_seq={epoch}) is exhausted "
            "(≤{limit}): the Agent message is stored as usual, but no wake was delivered"
        ),
        "reason.mention_ignored": (
            "A personal mention is a definite signal and must be answered; this round chose to "
            "ignore it, so it counts as a failure (original reason: {reason})"
        ),
        "reason.mention_no_body": (
            "A personal mention is a definite signal, but this round produced no storable body, "
            "so it counts as a failure"
        ),
        "reason.mention_silent": (
            "A personal mention is a definite signal and must be answered; this round declared "
            "silence, so it counts as a failure (original reason: {reason})"
        ),
        # The two **sibling** claim-wake reasons (D6): same rank as the mention pair above,
        # deliberately not reusing their wording (which would name a mention that never happened).
        "reason.claim_ignored": (
            "A claim wake is a definite signal and must be answered; this round chose to ignore "
            "it, so it counts as a failure (original reason: {reason})"
        ),
        "reason.claim_no_body": (
            "A claim wake is a definite signal, but this round produced no storable body and "
            "submitted no task action, so it counts as a failure"
        ),
        "reason.claim_silent": (
            "A claim wake is a definite signal and must be answered; this round declared silence, "
            "so it counts as a failure (original reason: {reason})"
        ),
        # The readable suffix for a claim round (ADR-0016 五): appended to the reason text,
        # following the existing `reason.pierced_suffix` precedent.
        "reason.claim_round": (
            "{reason}; this round was woken by the **claim** (orchestration handed you this task, "
            "you must reply)"
        ),
        # Auto-continue (ADR-0027): the fifth definite signal. Same rank as a claim wake, but the source
        # is the **assignee's own request in the previous round** — reusing the claim wording would say
        # orchestration handed over the task again, which already happened.
        "reason.continue_ignored": (
            "An auto-continue wake is a definite signal and must be answered; this round chose to "
            "ignore it, so it counts as a failure (original reason: {reason})"
        ),
        "reason.continue_no_body": (
            "An auto-continue wake is a definite signal, but this round produced no storable body and "
            "submitted no task action, so it counts as a failure"
        ),
        "reason.continue_silent": (
            "An auto-continue wake is a definite signal and must be answered; this round declared "
            "silence, so it counts as a failure (original reason: {reason})"
        ),
        "reason.continue_round": (
            "{reason}; this round was woken by **auto-continue** (you asked to keep working on this "
            "task in the previous round, you must reply)"
        ),
        # A team leader's **task change** wake (#179 / ADR-0025 §六): creation / reassignment /
        # transfer called the leader. Same rank as a claim wake (both are definite signals carried by
        # `ReplySignal`) but a **different source**: team channels never claim, so reusing the
        # `reason.claim_*` wording would state a fact that never happened.
        "reason.team_change_ignored": (
            "A team-leader task-change wake is a definite signal and must be answered; this round "
            "chose to ignore it, so it counts as a failure (original reason: {reason})"
        ),
        "reason.team_change_no_body": (
            "A team-leader task-change wake is a definite signal, but this round produced no "
            "storable body and submitted no task action, so it counts as a failure"
        ),
        "reason.team_change_silent": (
            "A team-leader task-change wake is a definite signal and must be answered; this round "
            "declared silence, so it counts as a failure (original reason: {reason})"
        ),
        "reason.team_change_round": (
            "{reason}; this round was woken by a **task change** (a team leader was called by task "
            "creation / reassignment / transfer, you must reply)"
        ),
        # A **human message** wake (#189): the fourth definite signal. Sibling of the three sets above
        # (same rank) but a **different source** — a human is talking to you directly. Reusing the
        # task-change / claim / mention wording would state a fact that never happened. All four faces
        # (DM / autonomous channel / team channel / task thread) share these entries.
        "reason.human_message_ignored": (
            "A human message is a definite signal and must be answered; this round chose to ignore it, "
            "so it counts as a failure (original reason: {reason})"
        ),
        "reason.human_message_no_body": (
            "A human message is a definite signal and must be answered; this round produced no "
            "storable body and submitted no task action, so it counts as a failure"
        ),
        "reason.human_message_silent": (
            "A human message is a definite signal and must be answered; this round declared silence, "
            "so it counts as a failure (original reason: {reason})"
        ),
        "reason.human_message_round": (
            "{reason}; this round was woken by a **human message** (a human spoke to you directly, "
            "you must reply)"
        ),
        "reason.pierced_suffix": (
            "{reason}; this round was a mention-driven pierce wake (the member was not in this "
            "channel), so it joined along with its reply, system event message seq={seq}"
        ),
        "reason.merged": (
            "This member already had a round in flight: {count} repeated wakes ({bases}) were "
            "merged into it, keeping the largest seq #{largest}; nothing is queued or replayed"
        ),
        "reason.round_exception": "Exception inside the round: {type}: {error}",
        "reason.seam_exception": "Exception in the Agent adapter: {type}: {error}",
        # ─── 「为什么这个成员现在不能跑」 ────────────────────────────────
        "availability.no_model": (
            "No model configured: fill in the model id in the Agent configuration first (provider:model)"
        ),
        "availability.disabled": "This Agent's model configuration is disabled",
        "availability.model_provider_prefix": (
            "The model id is missing a provider prefix: {model_id} (expected provider:model, "
            "for example dashscope:qwen-max)"
        ),
        "availability.model_name_missing": (
            "The model id is missing a model name: {model_id} (expected provider:model)"
        ),
        "availability.remote_consent": (
            "The remote data-flow notice has not been acknowledged: {provider} sends {faces} off "
            "this machine. The Agent becomes enabled once you acknowledge it"
        ),
        "availability.credential_missing": (
            "Model provider {provider} is missing its credential: set the environment variable {env} first"
        ),
        "availability.credential_none": "(this provider needs no credential)",
        "availability.credential_unreadable": (
            "Cannot resolve the credential for {provider}: {type}: {error}"
        ),
        # ─── 适配层的一轮结论 ────────────────────────────────────────────
        "seam.timeout": "Model call timed out ({seconds} seconds)",
        "seam.team_no_leader_reply": "The team round ended without a final reply from the leader",
        "seam.call_failed": "Model call failed: {type}: {error}",
        "seam.reply_error": "The model returned an error: {error}",
        "seam.task_action_only": "The model submitted a structured action through the task tool",
        "seam.no_body": "The model produced no storable body (it chose not to answer this round)",
        "error.field.agent_runtime_invalid": "Invalid runtime configuration: {field}",
        "seam.replied": "The model decided an answer was needed",
        "seam.exceed_max_iters": "Iteration limit reached; the conclusion may be incomplete",
        "seam.interrupted": "This round was interrupted by the user",
        "seam.workspace_degraded": "Docker workspace unavailable; workspace tools are disabled for this round, conversation continues: {error}",
        "seam.silent": (
            "The model declared silence through the structured channel (the declare_silence tool) "
            "and produced no body"
        ),
        "seam.silence_with_action": (
            "The model declared silence for this round but also submitted a structured task action "
            "(the action wins and no body is stored)"
        ),
        "seam.unknown_credential_type": "AgentScope does not know this credential type: {credential_type}",
        "seam.approval_timeout": "Approval wait timed out ({seconds} seconds); this round failed",
        "workspace.docker-daemon": "Docker is unavailable: {error}",
        "workspace.docker-dependency": "The Docker backend is missing a dependency: {error} (install aiodocker)",
        "workspace.docker-consent": (
            "The remote Docker image needs a data-flow acknowledgement first: {image} "
            "(nothing is pulled before it is confirmed)"
        ),
        "workspace.docker-timeout": "Docker workspace initialization timed out ({seconds} seconds): {image}",
        "workspace.docker-image-failed": "Docker image pull/build failed: {image}: {error}",
        "workspace.docker-mount": "Docker workspace mount failed ({type}): {error}",
        "workspace.docker-failed": "Docker workspace startup failed ({type}): {error}",
        "workspace.docker-egress-disabled": (
            "This channel's container network policy does not allow egress: "
            "{tool} sent no request at all (allow container egress in the channel "
            "configuration to permit the whitelisted hosts only)"
        ),
        "workspace.docker-egress-host": (
            "The container egress whitelist does not include {host}: {tool} sent no "
            "request at all (the whitelist comes from fetch allow rules and is now {hosts})"
        ),
        # ─── Permission rules / approvals (#149) ─────────────────────────
        "error.permission_rule_scope_invalid": "A permission rule scope must be channel or agent",
        "error.permission_rule_tool_empty": "A permission rule tool_name cannot be empty",
        "error.permission_rule_behavior_invalid": "A permission rule behavior must be allow, deny, or ask",
        "error.permission_rule_scope_unknown": "No such permission rule scope: {scope_id}",
        "error.permission_rule_not_found": "No such permission rule: {rule_id}",
        # ─── MCP declarations and assembly (#151 / ADR-0021 §3) ──────────
        "error.mcp_name_invalid": (
            "An MCP declaration name may only contain letters, digits, underscores, or hyphens (max 64)"
        ),
        "error.mcp_transport_invalid": "An MCP transport must be stdio, http, or sse",
        "error.mcp_command_required": "A stdio MCP needs a command",
        "error.mcp_url_required": "A remote MCP needs a url",
        "error.mcp_url_invalid": "An MCP url must be an absolute http or https address",
        "error.mcp_sse_url_invalid": "An sse MCP url must end with /sse or /messages/",
        "error.mcp_http_url_invalid": "An http MCP url must not point at /sse or /messages/ (that is the sse transport)",
        "error.mcp_args_invalid": "MCP args must be a list of strings",
        "error.mcp_env_names_invalid": (
            "MCP env_names may only contain valid environment variable names (names, never values)"
        ),
        "error.mcp_scope_invalid": "An MCP declaration scope must be channel or agent",
        "error.mcp_scope_unknown": "No such MCP declaration scope: {scope_id}",
        "error.mcp_server_not_found": "No such MCP declaration: {server_id}",
        "error.mcp_server_conflict": "An MCP declaration with that name already exists in this scope: {name}",
        "error.mcp_consent_local": "A stdio MCP runs locally and needs no remote data-flow acknowledgement",
        "error.skill_scope_invalid": "A skill scope must be channel or agent",
        "error.skill_scope_unknown": "No such skill scope: {scope_id}",
        "error.skill_workspace_root_missing": "The channel has no workspace_root, so a skill has nowhere to land",
        "error.skill_workspace_root_invalid": "The channel workspace_root is unusable: {reason}",
        "error.skill_channel_ambiguous": (
            "The member belongs to multiple channels with a workspace root; pass channel_id"
        ),
        "error.skill_member_not_in_channel": "The member is not in that channel: {channel_id}",
        "error.skill_source_invalid": "A skill source must be an absolute local directory",
        "error.skill_source_not_directory": "The skill source directory is missing or is not a directory",
        "error.skill_manifest_missing": "The skill root has no SKILL.md",
        "error.skill_front_matter_invalid": "SKILL.md front matter cannot be parsed: {reason}",
        "error.skill_name_invalid": (
            "SKILL.md name may only contain letters, digits, underscores, dots or hyphens (max 64)"
        ),
        "error.skill_description_missing": "SKILL.md front matter must include a description",
        "error.skill_description_too_long": "SKILL.md description is longer than {limit} characters",
        "error.skill_file_too_large": "A file inside the skill exceeds {limit} bytes",
        "error.skill_too_large": "The expanded skill exceeds {limit} bytes",
        "error.skill_too_many_files": "The skill holds more than {limit} files",
        "error.skill_path_invalid": ("Unsafe skill path (absolute path, .., symlink or a special file)"),
        "error.skill_archive_invalid": "The uploaded zip cannot be read: {reason}",
        "error.skill_hash_invalid": "The skill content hash is malformed",
        "error.skill_not_found": "No such skill: {skill_id}",
        "error.skill_conflict": "A skill with that name already exists in this scope: {name}",
        "mcp.runtime.consent_required": (
            "The remote MCP has not been acknowledged for its data flow yet; not connected: {name}"
        ),
        "mcp.runtime.connect_failed": "MCP connection failed: {name}",
        "error.approval_not_found": "No such approval request: {reply_id}",
        "error.approval_state_conflict": "The approval request is already {state}; no further decisions are accepted",
        "error.approval_tool_call_unknown": "Unknown tool call awaiting approval: {tool_call_id}",
        "error.approval_tool_call_decided": "That tool call was already decided the other way: {tool_call_id}",
        "error.approval_decision_duplicate": "The same tool call appears twice in one request: {tool_call_id}",
        "error.approval_state_invalid": "Approval state filter must be one of: {states}",
        # ─── 远端数据流告知 ──────────────────────────────────────────────
        "remote.face.body": "message bodies",
        "remote.face.author": "sender display names",
        "remote.face.channel": "channel names and descriptions",
        "remote.face.mention": "display names of mentioned members",
        "remote.statement": (
            "Once a remote model is enabled, the following data leaves this machine and is sent "
            "to the configured model provider: {faces}. The recipient may be a remote service. "
            "Local storage is not end-to-end encrypted at the application layer; the database "
            "file on disk is protected by operating-system file permissions, and the backend "
            "only listens on loopback by default."
        ),
        # ─── 持久化层（db.py）的可见文案 ─────────────────────────────────
        "error.unknown_member_kind": "Unknown member kind: {kind}",
        "error.field.member_kind_invalid": "Invalid member kind",
        "error.member_not_found": "No such member: {member_id}",
        "error.agent_not_found": "No such Agent: {member_id}",
        "error.field.agent_name_empty": "The Agent name cannot be empty",
        "error.field.agent_name_too_long": "The Agent name is limited to {limit} characters",
        "error.field.model_id_too_long": "The model id is limited to {limit} characters",
        "error.field.role_too_long": "The role is limited to {limit} characters",
        "error.field.system_prompt_too_long": "The system prompt is limited to {limit} characters",
        "error.field.provider_empty": "The model provider cannot be empty",
        "error.settings_invalid": "Workspace settings validation failed; no tier was changed",
        "error.unknown_round_status": "Unknown round outcome status: {status}",
        "error.field.round_status_invalid": "Invalid round outcome status",
        "error.target_not_found": "No such target: {target_id}",
        "error.task_title_empty": "The task title cannot be empty",
        "error.task_not_found": "No such task: {task_id}",
        "error.task_missing_for_target": "This task target has no task row: {target_id}",
        "error.unknown_task_status": "Unknown task status: {status}",
        "error.field.task_status_invalid": "Invalid task status",
        "error.task_transition_invalid": ("A task cannot move from {current} to {target}; closed is final"),
        "error.field.task_transition_invalid": "Moving from {current} to {target} is not allowed",
        "error.task_self_approve": "The assigned Agent cannot approve its own task in review",
        "error.field.task_self_approve": (
            "in_review → done must be approved by a member other than the assignee"
        ),
        "error.assignee_missing": "No such assignee",
        "error.field.assignee_missing": "No such Agent",
        "error.assignee_not_agent": "A task can only be assigned to an Agent that is not deleted",
        "error.field.assignee_not_agent": "The assignee must be an Agent",
        "error.transfer_not_assignee": ("Only the currently assigned Agent can hand this task over"),
        "error.transfer_needs_target": "An Agent handing a task over must name another Agent",
        "error.reassign_not_allowed": (
            "Only the owner or the currently assigned Agent can change the assignee"
        ),
        "error.unknown_target_kind": "Unknown target kind: {kind}",
        "error.field.target_kind_invalid": "Invalid target kind",
        "error.channel_target_explicit": (
            "A channel target gets its shadow row when the channel is created, not through here"
        ),
        "error.field.channel_target_explicit": "A channel target reuses channel.id",
        "error.channel_not_found": "No such channel: {channel_id}",
        "error.dm_peer_invalid": "A DM peer must be an existing Agent that is not deleted",
        "error.field.dm_peer_invalid": "No such Agent, or it is unavailable: {member_id}",
        "error.dm_creator_not_owner": "Only the owner can create a DM",
        "error.dm_not_found": "No such DM: {dm_id}",
        "error.field.channel_name_empty": "The channel name cannot be empty",
        "error.field.channel_name_too_long": "The channel name is limited to {limit} characters",
        "error.field.channel_description_too_long": (
            "The channel description is limited to {limit} characters"
        ),
        "error.channel_invalid": "Channel validation failed; nothing was created",
        # Channel config (team / workspace; #142 / ADR-0018 / ADR-0021).
        "error.channel_config_invalid": ("Channel configuration validation failed; no field was changed"),
        "error.field.channel_config_field_null": (
            "This configuration field cannot be null (omit it to keep the current value)"
        ),
        "error.field.channel_config_unknown": "Unknown channel configuration field",
        "error.field.channel_mode_invalid": "The channel mode must be one of: {values}",
        "error.field.channel_leader_required": "team mode requires a leader",
        "error.field.channel_leader_unavailable": (
            "The leader must be an undeleted Agent in this channel: {member_id}"
        ),
        "error.field.channel_workspace_backend_invalid": ("The workspace backend must be one of: {values}"),
        "error.field.channel_permission_mode_invalid": ("The permission mode must be one of: {values}"),
        "error.field.channel_web_access_invalid": ("Web access must be one of: {values}"),
        "error.field.channel_workspace_root_not_absolute": (
            "The project root must be an absolute path: {path}"
        ),
        "error.team_run_status_invalid": "Invalid team-run status",
        "error.team_run_not_found": "No such team run: {team_run_id}",
        "error.plan_run_status_invalid": "Invalid Plan-run status",
        "error.plan_run_not_found": "No such Plan run: {plan_run_id}",
        "error.plan_base_seq_invalid": "The Plan base sequence must be a non-negative integer",
        "error.sop_name_required": "The SOP name cannot be empty",
        "error.sop_steps_required": "A SOP needs at least one step",
        "error.sop_step_subject_required": "Every SOP step needs a subject",
        "error.sop_step_executor_required": "Every SOP step needs an executor member",
        "error.sop_max_attempts_invalid": "A SOP step's max_attempts must be a positive integer",
        "error.sop_member_unavailable": "SOP participants must be undeleted agents: {member_id}",
        "error.sop_member_disabled": "The SOP participant is disabled: {member_id}",
        "error.sop_definition_not_found": "No such SOP definition: {definition_id}",
        "error.sop_run_not_found": "No such SOP run: {sop_run_id}",
        "error.sop_phase_invalid": "Invalid SOP run phase",
        "error.sop_state_required": "The SOP run state cannot be empty",
        "error.sop_definition_disabled": "This SOP is disabled and cannot start",
        "error.sop_run_not_awaiting": "The SOP run is not awaiting: {phase}",
        "error.sop_resume_reply_missing": (
            "Cannot determine which parked reply to resume; pass reply_id explicitly"
        ),
        "error.sop_agent_unavailable": "The member-face adapter cannot build SOP participants",
        "error.sop_resume_invalid": "Invalid SOP resume payload",
        "error.sop_run_busy": "The SOP run is already executing",
        "error.team_assignment_state_invalid": "Invalid team-assignment state",
        "error.team_assignment_jump_invalid": "A team assignment consumes zero or one jump",
        "error.team_assignment_not_found": "No such team assignment: {assignment_id}",
        "team.leader_missing": "This channel has no leader configured",
        "team.leader_deleted": "The leader was deleted: {member_id}",
        "team.leader_disabled": "The leader is disabled: {member_id}",
        "team.leader_not_member": "The leader is not a member of this channel: {member_id}",
        "error.unknown_message_kind": "Unknown message kind: {kind}",
        "error.field.message_kind_invalid": "Invalid message kind",
        "error.message_body_empty": "The message body cannot be empty",
        "error.message_body_too_long": "The message body is limited to {limit} characters",
        "error.task_ref_missing": "The task that task_ref points at does not exist",
        "error.field.task_ref_missing": "No such task",
        "error.task_ref_other_target": ("task_ref must point at a task in the same target as the message"),
        "error.field.task_ref_other_target": "Cannot reference a task from another target",
        "error.message_not_found": "No such message: {target_id}#{seq}",
        "error.seq_invalid": "seq must be a non-negative integer",
        # ─── 附件（ADR-0014；#67）────────────────────────────────────────
        "error.attachment_rejected": "Attachment validation failed; nothing was saved",
        "error.attachment_name_empty": "The attachment file name cannot be empty",
        "error.attachment_name_too_long": "The attachment file name is limited to {limit} characters",
        "error.attachment_empty": "The attachment content is empty",
        "error.attachment_too_large": "The attachment exceeds the per-file limit ({limit} bytes)",
        "error.attachment_type_not_allowed": "Unsupported attachment type: {mime} (allowed: {types})",
        "error.attachment_not_found": "No such attachment: {attachment_id}",
        "error.attachment_unbound": (
            "This attachment has not been sent with a message: unbound uploads are not exposed"
        ),
        "error.attachment_already_bound": (
            "This attachment is already bound to a message and cannot be bound again: {attachment_id}"
        ),
        "error.attachments_immutable": (
            "Attachments cannot be edited or deleted: the human surface has no such endpoint"
        ),
        "error.field.attachment_unusable": "No such attachment, or it is already bound",
    },
}


def normalize_locale(locale: str | None) -> Locale:
    """把任意取值收窄到一份已知取值：不认识（含 ``None`` / 空串）时取**源语言**。"""
    return cast(Locale, locale) if locale in CATALOGS else SOURCE_LOCALE


def render(key: str, locale: str | None, /, **params: Any) -> str:
    """取词条并填参数：当前语言 → 源语言 → 键名本身（缺词条时不静默成空串，好让判据看得见）。"""
    catalog = CATALOGS[normalize_locale(locale)]
    template = catalog.get(key, CATALOGS[SOURCE_LOCALE].get(key, key))
    return template.format(**params)


def join_list(items: Iterable[str], locale: str | None, /) -> str:
    """按语言用该语言的顿号 / 逗号连成一串（列举数据面、成员等自然语言列举的唯一落点）。"""
    return render("common.list_sep", locale).join(items)


def locale_of(db: Any) -> Locale:
    """当前**语言偏好**：读库内的工作区设置（ADR-0009 第二层），不读请求头 / 环境变量。

    读库是决定性的：写 ``system`` 消息与 ``reason`` 的是唤醒编排，它跑在 HTTP 请求之外，
    那里根本没有请求头可读。库内没有设置行、或读取本身失败（库已关闭 / 诊断路径）时取**源语言**——
    文案渲染是展示层，它失败不得让错误封装或唤醒编排二次失败。
    """
    try:
        settings = db.workspace_settings()
    except Exception:
        return SOURCE_LOCALE
    return normalize_locale(str(settings.get("locale") or ""))
