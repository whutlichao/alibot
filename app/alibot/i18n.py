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
        "error.round_outcomes_immutable": "轮次结果不可编辑、不可删除：人类面不存在该端点",
        "error.budget_read_only": "回应预算只读：人类面不存在写入它的端点",
        "error.channel_members_append_only": "频道成员只能加入、不能移出：人类面不存在该端点",
        "error.messages_immutable": "消息不可编辑、不可删除：人类面不存在该端点",
        "error.agent_delete_unsupported": "Agent 不可删除：人类面不存在该端点",
        "error.request_body_invalid": "请求体校验未通过",
        "error.field.body_invalid": "请求体不合法：{location}（{kind}）",
        "error.tier_value_null": "档位取值不能为 null（要保留原值请省略该字段）",
        "error.agent_field_null": "该字段不能为 null（要保留原值请省略该字段）",
        "availability.orchestrator_absent": "后端未启用成员面编排",
        "error.agent_transfer_needs_orchestrator": "Agent 转派任务需要启用成员面编排",
        "error.channel_member_join_failed": "加入频道校验未通过，未加入任何成员",
        "error.member_id_empty": "成员标识不能为空",
        "error.member_unavailable": "成员不存在或已删除：{member_id}",
        "error.member_not_agent": "初始成员只能是 Agent：{member_id}",
        "error.agent_invalid": "Agent 校验未通过，未创建任何数据",
        "error.agent_update_invalid": "Agent 配置校验未通过，未改动任何字段",
        "error.model_id_empty": "模型标识不能为空",
        "error.model_id_provider_prefix": "模型标识需要提供商前缀：{model_id}（应为 provider:model，例如 dashscope:qwen-max）",
        "error.model_id_unknown_provider": "未知的模型提供商：{provider}（可用：{providers}）",
        "error.model_id_missing_name": "模型标识缺少模型名：{model_id}（应为 provider:model）",
        "error.unknown_provider": "未知的模型提供商",
        "error.field_choices": "必须是这些之一：{values}",
        "error.provider_not_remote": "{provider} 是本机模型提供商，不需要远端数据流告知",
        "error.provider_consent_not_needed": "本机提供商不需要确认",
        "error.task_action_wrong_target": "Agent 任务动作只能操作当前任务线程",
        "error.task_action_not_member": "只有任务成员可以更新任务状态",
        "error.task_action_rejected": "Agent 任务动作被拒绝：{message}",
        # ─── 落库的系统事件消息（写入时的语言，不回填） ───────────────────
        "system.task.created": "任务已创建",
        "system.task.status": "状态推进至 {status}",
        "system.task.title": "标题已更改为：{title}",
        "system.task.assignee": "已指派给 {name}",
        "system.task.unassigned": "指派已取消",
        "system.task.claimed": "已认领：{name} 接手了这张任务",
        "system.member.pierced_join": (
            "{name} 加入了频道：它被点名时并不在此频道，回复时顺带加入（成员变化只随真实回复发生）"
        ),
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
        "seam.call_failed": "模型调用失败：{type}: {error}",
        "seam.reply_error": "模型返回错误：{error}",
        "seam.task_action_only": "模型通过任务工具提交了结构化动作",
        "seam.no_body": "模型没有产出可落库的正文（本轮自判不回应）",
        "seam.replied": "模型自主判断需要回应",
        "seam.unknown_credential_type": "AgentScope 不认识这个凭证类型：{credential_type}",
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
    },
    "en": {
        # ─── 通用 ────────────────────────────────────────────────────────
        "common.list_sep": ", ",
        # ─── HTTP 错误 ───────────────────────────────────────────────────
        "error.round_outcomes_immutable": (
            "Round outcomes cannot be edited or deleted: the human surface has no such endpoint"
        ),
        "error.budget_read_only": (
            "The response budget is read-only: the human surface has no endpoint that writes it"
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
        "error.task_action_wrong_target": ("An Agent task action can only touch the task thread it runs in"),
        "error.task_action_not_member": "Only task members can update the task status",
        "error.task_action_rejected": "The Agent task action was rejected: {message}",
        # ─── 落库的系统事件消息 ──────────────────────────────────────────
        "system.task.created": "Task created",
        "system.task.status": "Status moved to {status}",
        "system.task.title": "Title changed to: {title}",
        "system.task.assignee": "Assigned to {name}",
        "system.task.unassigned": "Assignment cleared",
        "system.task.claimed": "Claimed: {name} took this task",
        "system.member.pierced_join": (
            "{name} joined the channel: it was mentioned while outside this channel and joined "
            "along with its reply (membership only changes alongside a real reply)"
        ),
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
        "seam.call_failed": "Model call failed: {type}: {error}",
        "seam.reply_error": "The model returned an error: {error}",
        "seam.task_action_only": "The model submitted a structured action through the task tool",
        "seam.no_body": "The model produced no storable body (it chose not to answer this round)",
        "seam.replied": "The model decided an answer was needed",
        "seam.unknown_credential_type": "AgentScope does not know this credential type: {credential_type}",
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
