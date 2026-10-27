/* ============================================================================
   alibot 工作台前端的**词条表**（`CONTEXT.md` **词条 / 词条表**）。

   机制（ADR-0009 的第一层）：界面文案一律按**稳定键 + 参数**取值，键在这里有且仅有两条：
   `zh-CN`（**源语言**，设计系统包的文案与语气规范以它为准）与 `en`（第二语言取值）。
   两份取值必须覆盖**同一个键集、无落单键**——这条由 `app/tests/i18n_ui.test.cjs` 的键集比对
   与裸中文扫描当着机器判据，不靠人眼。

   纪律：
   - **只有这一层放中文**：`index.html` / `app.js` / `app.css` 三件套里不再有裸中文文案
     （它们的静态文案挂 `data-i18n` / `data-i18n-attr`，动态文案走 `t(key, params)`）。
   - **参数是 `{name}` 形式**，值一律按字符串拼进去；缺参时保留原样的占位符（不静默吞掉）。
   - 这里的文案是**界面文案**，与后端生成的自然语言（`system` 消息、HTTP 错误 message、
   轮次 reason）**不是一层**：后者由后端按**语言偏好**渲染（#62），前端不代它措辞。
   - 语言选项名（`settings.languageZh` / `settings.languageEn`）按**当前语言**给出：
     `en` 里是 `Chinese (Simplified)` / `English`，`zh-CN` 里是 `简体中文` / `English`。
     不把源语言的**自称**原样搬进第二语言——切到 English 后设置弹窗属于「全部英文」的验收面。
   - 结构化状态词（`success/...` 那类四态状态点、任务状态、轮次结果状态）后端只给**词**，
     这里只把它们**翻译**成界面说法：键名与后端词一一对应，前端不重命名语义。

   本文件（#61 建立、#71 追加第三档）落**工作区设置**三个档位的界面文案：语言偏好 / 消息密度 /
   主题。主题的三档取值（`light` / `dark` / `system`）与它们对应的深色取值层都在
   `alibot-design-system/`，这里只有标签与说明的措辞。
   ========================================================================== */
'use strict';

/** 源语言：词条表的正本语言（设计系统包声明 UI locale 为简体中文，这里读作「源文案语言」）。 */
const I18N_SOURCE_LOCALE = 'zh-CN';

/** 支持的语言偏好取值（`CONTEXT.md` **语言偏好**）。顺序就是设置弹窗里选项的顺序。 */
const I18N_LOCALES = ['zh-CN', 'en'];

const I18N_CATALOGS = {
  'zh-CN': {
    /* ─── 应用外壳 ─────────────────────────────────────────────────── */
    'app.title': 'alibot · 协作工作区',
    'app.brandSub': 'LOCAL · 本机工作台',
    // 「这个入口还没迁到 Vue 版工作台」：Vue 版是**分阶段迁移**的入口（ADR-0038 §五），
    // 尚未迁过去的面在那一版里不渲染入口；仍保留可见的那些（新建频道 / 新建 Agent）用它作禁用说明，
    // 于是「按下去没反应」不会发生——按钮是禁用的，理由写在 title 里。
    'app.notMigrated': '这个入口还没迁到 Vue 版工作台，请用 /studio/ 打开',
    'identity.human': '人类 · 本机',
    'identity.me': '我',

    /* ─── 移动端壳（≤900px）：顶部栏只有一个控件 ──────────────────────── */
    'mobile.railToggle': '打开导航',

    /* ─── 左栏 ─────────────────────────────────────────────────────── */
    'rail.nav': '工作区导航',
    'rail.newChannel': '新建频道',
    'rail.searchTitle': '搜索工作台',
    /* 搜索入口按钮上的提示语（原型 `:522` 的「搜索消息、任务…」，末尾的省略号是文案的一部分） */
    'rail.searchPlaceholder': '搜索消息、任务…',
    'rail.newAgent': '新建 Agent',
    'rail.newAgentTitle': '新建 Agent',
    'rail.groupChannels': '频道',
    'rail.groupDms': '私信',
    'rail.groupArchivedDms': '已归档',
    'rail.groupAgents': '本机 Agent',
    'rail.emptyChannels': '还没有频道。新建一个频道就能开始记录。',
    'rail.emptyDms': '还没有私信。在 Agent 详情里点「发送消息」后会在这里显示。',
    'rail.emptyAgents': '还没有 Agent。',
    /* 左栏 Agent 行在「Agent 详情面还没迁过来」时的点击语义是「打开与它的私信」，
       因此无障碍名说的是私信而不是详情（详情那张票把 `agentDetailAvailable` 翻正后换回
       `agentDetail.openAria`）。 */
    'rail.openDmAria': '打开与 {name} 的私信',
    'settings.open': '设置',
    'rail.reminders': '提醒',

    /* ─── 状态点与任务状态（后端只给词，这里只给说法） ──────────────── */
    'status.online': '空闲',
    'status.working': '正在回复',
    'status.error': '出错',
    'status.offline': '未启用',
    'task.status.todo': '待认领',
    'task.status.in_progress': '进行中',
    'task.status.in_review': '待审',
    'task.status.done': '完成',
    'task.status.closed': '已关闭',
    'task.transition.in_progress': '推进至进行中',
    'task.transition.in_review': '提交待审',
    'task.transition.done': '标记完成',
    'task.transition.todo': '退回待认领',
    'task.transition.closed': '关闭任务',

    /* ─── 轮次结果的四种「正常选择」（`failed` 单独成行，`replied` 由回复承载） ── */
    'outcome.ignored.label': '自判忽略',
    'outcome.ignored.note': '自判忽略（正常的协议选择，不是失败）',
    'outcome.budget-exhausted.label': '预算耗尽',
    'outcome.budget-exhausted.note': '预算已用尽，本轮零投递（不是出错，成员状态点仍空闲）',
    'outcome.merged.label': '被合并',
    'outcome.merged.note': '重复唤醒被合并进正在进行的那一轮（不是失败）',
    /* `silent`（#58 / ADR-0008）：模型经**结构化通道**（`declare_silence` 工具）明确声明本轮沉默。
       它不是失败，也不是 `ignored`（后者是既有 seam 的正常自判忽略，没有结构化声明）。 */
    'outcome.silent.label': '模型声明沉默',
    'outcome.silent.note':
      '模型经结构化通道声明本轮沉默，没有产出正文（不是失败，也不是自判忽略）',
    'outcome.summaryAria': '{names} 没有回应：{count} 条轮次结果',
    'outcome.collapse': '收起原因',
    'outcome.expand': '没有回应 · 展开看原因',
    'outcome.lineReasonEmpty': '后端没有留下原因。',
    'outcome.failedAria': '{name} 未回应：{reason}',
    'outcome.tag': '未回应',
    'outcome.stateDisabled': '没启用',
    'outcome.stateFailed': '这一轮失败',
    'outcome.inspect': '看成员状态',
    'outcome.disabledNote': '{name} 现在是「未启用」，所以这一轮没有回复。后端给出的原因：',

    /* ─── 旁白式交代（一条消息的**呈现降档**，不是一种轮次结果） ──────────
       旁白仍是真实消息：它占自己的 `seq`、计未读。这里只有它那一行怎么读、以及展开后
       「关联轮次结果的 reason」这块的说法——不并入 `outcome.*` 摘要行的文案。 */
    'narration.tag': '旁白',
    'narration.expand': '展开看这一轮的结果',
    'narration.collapse': '收起这一轮的结果',
    'narration.toggleAria': '{name} 的旁白：{action}',
    'narration.reasonMissing': '这一轮的结论暂时读不到。',

    /* ─── 模型提供商（远端 / 本机是**事实**，不是档位） ───────────────── */
    'provider.dashscope': 'DashScope（远端）',
    'provider.openai': 'OpenAI（远端）',
    'provider.openai-response': 'OpenAI Responses（远端）',
    'provider.anthropic': 'Anthropic（远端）',
    'provider.gemini': 'Gemini（远端）',
    'provider.deepseek': 'DeepSeek（远端）',
    'provider.moonshot': 'Moonshot（远端）',
    'provider.minimax': 'MiniMax（远端）',
    'provider.volcengine': '火山引擎（远端）',
    'provider.xai': 'xAI（远端）',
    'provider.ollama': 'Ollama（本机）',

    /* ─── 通用 ─────────────────────────────────────────────────────── */
    'common.cancel': '取消',
    'common.close': '关闭',
    'common.nameListSep': '、',
    'common.colonSep': '：',
    'common.nameListMore': '{names} 等 {count} 人',
    'common.clauseSep': '；',
    'time.today': '今天',
    'time.unknown': '时间未知',
    'error.badRequestBody': '请求体不合法',
    'error.httpStatus': '后端返回 {status}',

    /* ─── 搜索（主线视图：四档 facet + 三类命中） ──────────────────── */
    'search.fieldPlaceholder': '搜索消息、任务、成员…',
    'search.fieldLabel': '搜索工作台',
    'search.facetsLabel': '搜索范围',
    'search.facetAll': '全部',
    'search.facetMessage': '消息',
    'search.facetTask': '任务',
    'search.facetAgent': 'Agent',
    'search.sourceAgent': '成员',
    'search.targetChannel': '频道 · #{name}',
    'search.targetDm': '私信 · {name}',
    'search.targetTask': '任务 · {name}',
    'search.unknownTarget': '未知 target',
    'search.unknownAuthor': '未知作者',
    'search.authorDeleted': '作者已删除',
    'search.snipAuthor': '来自 {name}',
    'search.snipTask': '#{id} · {status}',
    'search.snipAgent': '{status} · {model}',
    'search.searching': '正在搜索…',
    'search.idleTitle': '搜索整个工作区',
    'search.idleBody': '消息不可编辑、不可删除；搜索覆盖消息、任务与 Agent。',
    'search.emptyTitle': '没有找到“{query}”',
    'search.emptyBody': '换个关键词，或检查拼写。',
    'search.offline': '后端不可达：无法执行搜索。',
    'search.failed': '搜索失败：后端不可达或请求未完成（{reason}）。',

    /* ─── ⌘K 命令面板（只呈现**真绑定**的快捷键，见 D10） ───────────── */
    'cmd.ariaLabel': '命令面板',
    'cmd.placeholder': '输入命令或搜索…',
    'cmd.empty': '无匹配命令',
    'cmd.groupJump': '跳转',
    'cmd.groupAction': '动作',
    'cmd.groupChannels': '频道',
    'cmd.messages': '消息视图',
    'cmd.board': '任务看板',

    /* ─── 连接状态与通道外壳 ───────────────────────────────────────── */
    'conn.banner': '后端不可达：当前显示的是最后一次成功读到的内容，发送已被禁止。',
    'conn.retry': '重试连接',
    'conn.reconnecting': '事件流正在重连：恢复后会自动补拉错过的消息。',
    'load.workspaceFailed': '读取后端失败：{reason}',
    'tabs.viewLabel': '频道视图',
    'tabs.messages': '消息',
    'tabs.tasks': '任务',
    'tabs.tasksApprovalDot': '该频道有 {count} 个任务在等待审批',
    'empty.noChannelTitle': '还没有频道',
    'empty.noChannelBody': '新建一个频道，就能发出第一条永久记录。',
    'empty.noMessageTitle': '该频道还没有消息',
    'empty.noMessageBody': '这里的消息不可编辑、不可删除，是永久记录。',
    'header.dmDesc': '与 {name} 的两方私信',
    'header.dmPlaceholder': '发消息给 @{name} —— 可手动 @ 送达其他 Agent',
    'header.dmArchivedDesc': '该 Agent 已删除，这段私信保留为只读归档。',
    'header.dmArchivedPlaceholder': '该 Agent 已删除，这段私信只读归档',
    'header.channelPlaceholder': '发消息到 #{name} —— 输入 @ 提及 Agent',
    'typing.working': '{name} 正在回复…',
    'typing.nameSeparator': '、',
    'typing.stopNamedShort': '停止 {name}',
    'typing.stopNamed': '停止 {name} 的本轮',
    /* ─── composer 与 @ 补全 ────────────────────────────────────────── */
    'composer.mentionLabel': '可提及的当前频道 Agent 成员',
    'composer.placeholder': '发消息到 #频道名',
    'composer.inputLabel': '消息正文',
    'composer.mentionAction': '提及 Agent',
    'composer.attachAction': '附件',
    'composer.attachRemove': '移除附件 {name}',
    'composer.attaching': '正在上传 {count} 个附件…',
    'composer.attachTooLarge': '{name} 超过单文件上限（{limit}）',
    'composer.asTaskAction': '作为任务发送',
    'composer.hint': 'Enter 发送 · Shift+Enter 换行',
    'composer.archivedHint': '已归档 · 只读，不能发送消息',
    'composer.send': '发送',
    'composer.note':
      '消息不可编辑、不可删除，是永久记录 · 本机单用户，后端只监听 loopback · 启用远端模型前会先告知出境数据面',
    'mention.hintPick': '↑↓ 选择 · Enter 选中 · Esc 关闭',
    'mention.hintDismiss': 'Enter 发送 · Esc 关闭清单',
    'mention.unavailable': '暂时读不到该频道的成员清单（后端请求失败）：恢复后按 @ 会重新给出候选。',
    'mention.empty':
      '该频道还没有 Agent 成员：在右侧「频道成员」面板的「未加入」一段点「加入」，或在新建 Agent 后到那里加入。',
    'mention.cannotMention': '无法 @',
    'mention.quoteUnsupported':
      '「{name}」同时含 " 、” 、’ 三类闭引号，提及解析器没有能表达这种名字的引号形式，因此没有插入任何文本。',

    /* ─── 任务看板与任务线程 ───────────────────────────────────────── */
    'board.viewLabel': '任务视图',
    'board.board': '看板',
    'board.list': '列表',
    'board.filtersLabel': '任务筛选',
    'board.newTask': '新建任务',
    'board.mine': '我的任务',
    'board.allChannels': '全部频道',
    'board.emptyColumn': '这一列还没有任务',
    'board.noMatchTitle': '没有匹配的任务',
    'board.noMatchBody': '试试关掉筛选，或新建一个任务。',
    'board.openThread': '打开线程 →',
    'board.cardAria': '{title}，{status}，打开任务线程',
    'board.cardApprovalAria': '{title}，{status}，{count} 条待审批，打开任务线程',
    'board.unreadAria': '{count} 条未读任务消息',
    'board.approvalBadge': '待审批 {count}',
    'board.approvalBadgeAria': '「{title}」有 {count} 条待审批',
    'board.cardMenuAria': '「{title}」的流转菜单',
    'board.cardMenuLabel': '任务流转',
    'task.selectChannel': '选择所属频道',
    'task.createChannelFirst': '请先创建频道',
    'task.threadTag': '任务线程',
    'task.teamNotice': '任务线程只唤醒队长',
    'task.threadHeadSub': '#{channel} · 讨论 {count} 条',
    'task.threadUnread': ' · {count} 条未读',
    'task.noTransitions': '已关闭，无可用推进',
    'task.discussionCount': '讨论 · {count} 条',
    'task.repliesEmpty': '还没有讨论，来写第一条回复',
    'task.historyEmpty': '暂无变更记录',
    'task.unassigned': '未认领',
    // 删除后的任务历史仍指向原成员 id；成员表里已没有它时显示这句可读回退（不露裸 id）。
    'task.deletedAssignee': '已删除的成员',
    'task.unassignedPill': '未认领 · 点击指派',
    'task.reassignHint': '· 改派',
    'task.assignUnavailable': '暂时读不到该频道的成员表：指派候选不可用。',
    'task.assignLoading': '正在读取该频道的成员…',
    'task.assignNoAgents': '该频道还没有 Agent 成员：先在右侧「频道成员」面板把 Agent 加入这个频道。',
    'task.clearAssign': '取消指派',
    'dock.move': '推进',
    'dock.assign': '指派',
    'dock.history': '状态变更记录',
    'dock.label': '详情面板',
    'dock.toggle': '切换详情面板',
    // ≤900px：dock 变右侧抽屉时，抽屉**自己的**出口（`>900px` 不出现，见 `app.css`）。
    'dock.closePanel': '关闭详情面板',
    'dock.membersTitle': '频道成员',
    'dock.membersRole':
      '本频道已加入的 Agent 与未加入者可加入的 Agent；状态点由后端事实推导：空闲 / 正在回复 / 出错 / 未启用',
    'dock.threadClose': '关闭任务线程',
    'dock.replyPlaceholder': '回复该任务线程…',
    'dock.replyInputLabel': '任务消息正文',
    'dock.replyHint': 'Enter 回复 · Shift+Enter 换行',
    'dock.replySend': '回复',
    'dock.openChannelFirst': '先打开一个频道。',
    'dock.joined': '已加入（{count}）',
    'dock.membersUnavailable': '暂时读不到该频道的成员表（后端请求失败）：恢复后重新打开面板即可。',
    'dock.membersLoading': '正在读取该频道的成员表…',
    'dock.noJoinedAgents': '这个频道还没有 Agent 成员：从下面「未加入」里点「加入」即可。',
    'dock.notJoined': '未加入本频道（{count}）',
    'dock.notJoinedUnavailable':
      '成员表读不到时暂不列「未加入」：否则会把已在频道里的 Agent 误导为未加入。',
    'dock.allJoined': '本工作区的 Agent 都已在这个频道里。',
    'dock.noAgents': '还没有 Agent：用左上角「Agent」按钮创建一个，再回到这里点「加入」。',
    'dock.noModel': '未配置模型',
    'dock.joining': '加入中…',
    'dock.join': '加入',
    'dock.joinAria': '把 {name} 加入当前频道',

    /* ─── 运行面板（#155 / 规格 §4.6；dock 第三种形态）────────────────── */
    'runPanel.entryTitle': '运行面板',
    'runPanel.taskEntryTitle': '任务运行面板',
    'runPanel.tag': '运行面板',
    'runPanel.sub': '成员过程 · 分派 · Plan · SOP（只读过程面）',
    'runPanel.refresh': '刷新快照',
    'runPanel.closeAria': '关闭运行面板',
    'runPanel.loading': '正在读取运行快照…',
    'runPanel.error': '运行快照读取失败：{reason}',
    'runPanel.retry': '重试',
    'runPanel.sectionRuns': '运行与分派',
    'runPanel.sectionEvents': '成员事件',
    'runPanel.sectionPlan': 'Plan',
    'runPanel.sectionSop': 'SOP',
    'runPanel.runsEmpty': '还没有 team 运行记录。',
    'runPanel.runsEmptyAutonomous': '自主模式频道没有队长，不产生 team 运行与分派。要看 team 运行，请在「频道设置 → 协作模式」把频道切到队长制。',
    'runPanel.noAssignments': '这次运行没有分派记录。',
    'runPanel.eventsEmpty': '还没有成员事件（仅直播，断开期间不补发）。',
    'runPanel.planEmpty': '还没有 Plan。',
    'runPanel.planEmptyAutonomous': '自主模式频道的对话不产生 Plan。任务运行的记录挂在任务上，请从任务线程头的「任务运行面板」查看。',
    'runPanel.sopEmpty': '还没有 SOP 运行。',
    'runPanel.taskRef': '任务 #{id}',
    'runPanel.replyRef': '回复 {id}',
    'runPanel.awaiting': '等待人类确认（回复 {reply}）',
    'runPanel.verdictPassed': '通过',
    'runPanel.verdictFailed': '未通过',
    'runPanel.stepIndex': '步骤 {index}',
    'runPanel.noSteps': '这次运行还没有步骤记录。',
    'runPanel.duration': '耗时 {seconds}s',
    'runPanel.inFlight': '进行中',
    'runPanel.state.started': '已开始',
    'runPanel.state.succeeded': '成功',
    'runPanel.state.failed': '失败',
    'runPanel.state.interrupted': '中断',
    'runPanel.state.budgetExhausted': '预算耗尽',
    'runPanel.state.running': '运行中',
    'runPanel.state.completed': '已完成',
    'runPanel.state.invalidLeader': '队长无效',
    'runPanel.state.pending': '待开始',
    'runPanel.state.inProgress': '进行中',
    'runPanel.state.awaiting': '等待确认',
    'runPanel.state.unknown': '未知',

    /* ─── 审批卡（#153 / 规格 §4.6 第 4 条）：HITL 的人类面动作 ─────────── */
    'approval.section': '审批',
    'approval.cardLabel': '待审批工具：{tools}',
    'approval.tag': '审批',
    'approval.pending': '待审批',
    'approval.approved': '已批准',
    'approval.denied': '已拒绝',
    'approval.expired': '已过期',
    'approval.stateUnknown': '状态未知',
    'approval.remaining': '剩余 {time}',
    'approval.allow': '允许',
    'approval.deny': '拒绝',
    'approval.submitting': '正在提交…',
    'approval.noInput': '（无参数）',
    'approval.unknownTool': '未知工具',
    'approval.submitFailed': '提交失败：{reason}',

    /* ─── 消息行 ───────────────────────────────────────────────────── */
    'msg.systemAuthor': '系统',
    'msg.tagEvent': '事件',
    'msg.tagAgent': 'Agent',
    'msg.pendingTag': '待确认',
    'msg.failedTag': '发送失败',
    'msg.notDelivered': '未送达：{reason}',
    'msg.sendFailed': '发送失败',
    'msg.retry': '重试发送',
    'msg.discard': '移除',
    'msg.taskRef': '任务 · {title}',
    'msg.taskRefAria': '打开任务 {title}',
    'msg.taskThreadEntry': '进入任务线程',
    'msg.taskThreadEntryUnread': '进入任务线程 · {count} 条未读',
    'msg.taskThreadEntryAria': '打开任务 {title} 的任务线程',
    'msg.taskThreadEntryAriaUnread': '打开任务 {title} 的任务线程，有 {count} 条未读',
    'msg.attachmentAria': '{name}（{size}），下载',
    'attach.fallbackName': '附件',

    /* ─── 弹窗 ─────────────────────────────────────────────────────── */
    'task.modalTitle': '新建任务',
    'task.modalSub': '任务创建后拥有独立线程；状态变化会记录在它的变更记录里。',
    'task.titleLabel': '任务标题',
    'task.titlePlaceholder': '写下一个新任务…',
    'task.channelLabel': '所属频道',
    'task.channelHint': '任务恰好属于一个频道；指派在任务线程里用「指派」chip 完成。',
    'task.create': '新建任务',
    'task.titleRequired': '任务标题不能为空。',
    'task.noChannel': '还没有频道：先新建一个频道再建任务。',
    'channel.modalTitle': '新建频道',
    'channel.modalSub': '频道创建后即可向它发送消息，无需刷新或重启。',
    'channel.nameLabel': '频道名称',
    'channel.namePlaceholder': '例如：移动端体验',
    'channel.descLabel': '描述',
    'channel.descPlaceholder': '这个频道讨论什么？',
    'channel.membersLabel': '初始成员',
    'channel.membersHint':
      '勾选的 Agent 会成为该频道的显式成员。还没有可勾选的对象时，用左上角「Agent」按钮创建一个。',
    'channel.create': '创建频道',
    'channel.pickEmpty': '还没有可勾选的 Agent：用左上角「Agent」按钮创建一个，回到这里勾选即可加入频道。',
    'agent.modalTitle': '新建 Agent',
    'agent.modalSub': '模型与系统提示保存为配置数据；换模型不需要改协作域代码。',
    'agent.nameLabel': '名称',
    'agent.namePlaceholder': '例如：小明',
    'agent.roleLabel': '角色',
    'agent.rolePlaceholder': '例如：前端工程师',
    'agent.providerLabel': '模型提供商',
    'agent.modelLabel': '模型名',
    'agent.modelPlaceholder': '例如：qwen-max / gpt-4.1 / qwen2.5',
    'agent.modelHintPrefix': '保存为 ',
    'agent.modelHintSuffix': ' 形式的模型标识。',
    'agent.promptLabel': '系统提示',
    'agent.promptPlaceholder': '这个 Agent 应该怎么工作？',
    'agent.remoteTitleStatic': '启用远端模型前需要你确认一次',
    'agent.remoteTitle': '启用 {provider} 前需要你确认一次',
    'agent.remoteBody':
      '启用后，以下数据会离开本机并发送给该模型提供商：{faces}。接收方是远端服务，不在你的机器上。',
    'agent.remoteHint': '本机存储不做应用层端到端加密，也不会声称端到端加密；后端默认只监听 loopback。',
    /* 数据面读不到时的一句话说清「为什么这里没有列举」：告知正文不能缺了数据面还说成完整，
       也不能把一个还没读到的空集合说成「没有数据会离开本机」。 */
    'agent.remoteBodyUnavailable': '还没读到会离开本机的数据面清单，确认键暂时不可用（重新打开弹窗会再读一次）。',
    'agent.remoteConfirm': '我已了解，确认发送',
    'agent.create': '创建 Agent',
    'agent.providerLocalHint': '本机模型：不离开本机，不需要数据流确认。',
    'agent.providerRemoteHint': '远端模型：启用前需要先确认数据会离开本机（见下方告知）。',
    'agent.autoClaimLabel': '自动认领任务',
    'agent.autoClaimOn': '开启',
    'agent.autoClaimOnHint': '新建任务后由编排接手',
    'agent.autoClaimOff': '关闭',
    'agent.autoClaimOffHint': '新建任务保持未认领',
    'agent.autoClaimNote':
      '开启后：频道内新建的任务由编排交给它（一次只认领一个）；认领会写一条可读的系统记录、唤醒它，并为该任务重置一次回应预算——每张任务至多一次。',
    // 「联网档」的 Agent 级入口（#202）：默认档「跟随频道（默认）」= 不写 Agent 级覆盖，选中它
    // 提交显式 null（清除覆盖）；私聊里没有频道，按默认档「只搜索」。其余三档与频道设置里的
    // 那三档同名同义；`fetch` 只在频道 / 任务线程装配（私聊的权限档固定 read-only）。
    'agent.webAccessLabel': '联网',
    'agent.webAccessFollow': '跟随频道（默认）',
    'agent.webAccessFollowHint': '清除 Agent 级覆盖；私聊按默认档',
    'agent.webAccessOff': '关闭',
    'agent.webAccessOffHint': '不挂任何联网工具',
    'agent.webAccessSearch': '只搜索',
    'agent.webAccessSearchHint': '挂 web_search；查询词会离开本机',
    'agent.webAccessSearchFetch': '搜索+抓取',
    'agent.webAccessSearchFetchHint': '额外挂 fetch；开启后不再逐次审批，可按 host 配 deny / ask 规则收紧',
    'agent.webAccessNote': '跟随频道 = 不写 Agent 级覆盖（提交 null）；私聊里按默认档「只搜索」，且私聊不装配 fetch。',

    /* ─── Agent 详情（主线视图 `state.mode === 'agent'`）：三个页签 + 就地编辑 ──
       详情不是 dock 的形态（用户 2026-10-22 裁定）：它占据整条右侧，dock 让位。
       正文是**三个页签**（`CONTEXT.md` **Agent 详情页签**）：基础信息和配置 → skills → mcp，
       顺序固定、默认停在第一页、不落库也不写 URL（`tabsLabel` / `tabBase` / `tabSkills` /
       `tabMcp`；旧名 `tabOverview` / `tabRuntime` 不复用）。统计的每个数字都由**后端既有事实**
       推导（`stats`：`round_outcome` 聚合 + 任务行）；模型标识 / 提供商 / 是否远端 / 远端数据流
       告知不再在详情页重复一份只读读数（用户 2026-10-24 裁定），它们可编辑的那一份长在「配置」
       区块里（词条见 `agent.*`）。上下文条数 / 压缩阈值 / 迭代上限三格的**界面入口已撤除**
       （只服务它们的那四条 `agent.contextWindow` / `contextTrigger` / `maxIters` /
       `runtimeDefaultHint` 也随控件删除，库里的列与 `PATCH` 字段面原样）。
       原型的内存 / 延迟 / 工具权限 / 温度 / Token 消耗 / 上下文占用在本仓没有数据面，
       因此这里也没有它们的词条。 */
    'agentDetail.sendMessage': '发送消息',
    'agentDetail.tabsLabel': 'Agent 详情页签',
    'agentDetail.tabBase': '基础信息和配置',
    'agentDetail.tabSkills': 'skills',
    'agentDetail.tabMcp': 'mcp',
    'agentDetail.mcpFileTitle': '家目录 mcp.json',
    // mcp 页签下块（#284 / D7，口径更新于 #275）：文本域直接接家目录文件的**原文**，两块各标注
    // 数据来源。文件**不再是装配层**：它是导入源，运行期装配只看库行。
    'agentDetail.mcpFileSource':
      '来自这个 Agent 家目录的 mcp.json（文件原文）；它是导入源，**不参与运行时装配**——'
      + '要让它生效，请把它导入库行（`python -m alibot migrate-mcp`）。',
    'agentDetail.mcpFileLoading': '正在读取家目录 mcp.json…',
    'agentDetail.mcpFileMissing':
      '这个 Agent 的家目录里还没有 mcp.json（不是空文件）：保存一次会按你写的内容新建它。',
    'agentDetail.mcpFileServers': '文件里的条目数：{count}',
    'agentDetail.mcpFileSave': '保存 mcp.json',
    'agentDetail.mcpFileLoadFailed': '家目录 mcp.json 读取失败：{reason}',
    'agentDetail.mcpFileSaveFailed': '家目录 mcp.json 保存失败：{reason}',
    'agentDetail.openAria': '查看 {name} 的详情',
    'agentDetail.sectionFacts': '统计',
    'agentDetail.statChannels': '所属频道数',
    'agentDetail.statChannelsHint': '来源：它是成员的频道数（后端频道成员表）。',
    'agentDetail.statRounds': '参与轮次数',
    'agentDetail.statRoundsHint': '来源：它参与过的轮次结果条数（round_outcome，跨频道与任务）。',
    'agentDetail.statTasksDone': '完成任务数',
    'agentDetail.statTasksDoneHint': '来源：指派给它且状态为「完成」的任务行。',
    'agentDetail.statSuccess': '成功率',
    'agentDetail.statSuccessHint':
      '来源：回复的轮次数 ÷（回复 + 自判忽略 + 失败）的轮次数；没有样本时显示「—」。',
    'agentDetail.noSamples': '—',
    'agentDetail.sectionConfig': '配置',
    'agentDetail.pause': '暂停',
    'agentDetail.resume': '恢复',
    'agentDetail.kvPrompt': '系统提示',
    // 联网档（#202 / 2026-10-21 修订）：读数缺失（`null`）= 没有 Agent 级覆盖 = 跟随频道，
    // 它是**一个读数**而不是空字段；有覆盖时显示档位。不在详情页里替人推断私聊的生效档。
    // 全局 MCP（#236 / ADR-0031 D8）：条目视图**只读**（它住在 mcp 页签的下块里，与旁边的
    // mcp.json 文本域是同一份数据的两种视图）。条目视图自身不渲染编辑 / 启停 / 删除——要改原文
    // 走上方的文本域；它只对远端未确认条目给一个确认动作。
    'agentDetail.globalMcpTitle': '全局 MCP（只读）',
    'agentDetail.globalMcpEmpty': '“全局mcp.json”里没有声明MCP服务',
    'agentDetail.globalMcpRemote': '远端（数据会离开本机）',
    'agentDetail.globalMcpLocal': '本机',
    'agentDetail.globalMcpConsented': '已确认',
    'agentDetail.globalMcpConsentPending': '未确认（确认前不连接）',
    'agentDetail.globalMcpNoConsent': '本机条目：不需要确认',
    'agentDetail.globalMcpConfirm': '确认使用',
    'agentDetail.globalMcpConfirmed': '已确认：{name}',
    'agentDetail.globalMcpRuntimeIdle': '尚无运行期读数',
    'agentDetail.globalMcpRuntimeConnected': '运行期已连接',
    // 危险操作（本票）：暂停 / 删除搬进页面最后一个区块。说明必须点明两者的后果；
    // 删除走**确认弹窗**，标题 + 确认文案带 Agent 名称与「不可撤销」，人在点确认前就能读到代价。
    'agentDetail.sectionDanger': '危险操作',
    'agentDetail.dangerHint':
      '危险：暂停只停止产出回复（它仍会被唤醒并留下一条可读原因的失败轮次结果）；删除会把这个 Agent 从 Agent 列表与全部频道中移除，私信、历史消息与任务保留。以下动作请确认后再执行。',
    'agentDetail.delete': '删除',
    'agentDetail.deleteTitle': '删除 Agent',
    'agentDetail.deleteConfirm':
      '确认删除「{name}」？它会立即从 Agent 列表与全部频道中移除并关闭详情；私信、历史消息与任务保留，但这个 Agent 无法恢复，操作不可撤销。',
    'agentDetail.deleteConfirmAction': '确认删除',
    'agentDetail.deleteCancel': '取消',
    'agentDetail.save': '保存',
    'agentDetail.discard': '放弃',
    'agentDetail.unsaved': '有未保存的改动',

    /* ─── 能力配置（#273 / ADR-0031 §二 #270 修订） ────────────────────────
       归口只有这一处：Agent 详情里的 MCP 声明与技能。作用域恒为该成员
       （`scope_kind=agent&scope_id=<member>`），没有 scope 选择器；频道级旧行保留在库里但
       不展示、不装配。控件只作用于当前成员（#281 撤掉了「复制到其他成员」这条跨成员通道）；
       #280 把这两块拆进 skills / mcp 两个页签，跨两 kind 的总标题因此没有消费点、已随票删除。 */
    'agentCapabilities.scopeNote':
      '这些能力只属于这个 Agent；技能字节在 ~/.alibot/agents/{member}/skills/（公共库 ~/.alibot/skills/ 只读）。',
    'agentCapabilities.mcpTitle': 'MCP 声明',
    'agentCapabilities.skillTitle': '技能',

    /* ─── 提醒（ADR-0012：库内对象；到点产物见 ADR-0017） ──────────────────
       两个取值只说**存在性与持久性**：提醒存在后端、跨刷新与重启仍在。到点口径是另一件事，
       别在这里笼统替它承诺——有归属的提醒到点确实落一条 `system` 消息进该频道消息流
       （ADR-0017 §一），但到点**不唤醒**任何人、不消耗回应预算、不计未读。**不落消息**有
       两支：**不归属**任何频道的提醒无处可落；有归属但距到期**超过 24 小时**才被发现的那些
       不补投（ADR-0017 §三）——两支都只记已到点（`CONTEXT.md` **到点**）。 */
    'reminder.modalTitle': '提醒',
    'reminder.modalSub': '提醒保存在本机后端，刷新或重启后仍在。',
    'reminder.closeAria': '关闭提醒',
    'reminder.emptyTitle': '还没有提醒',
    'reminder.emptyBody': '提醒保存在后端，不依赖这台浏览器。',
    'reminder.newLabel': '新提醒',
    'reminder.textLabel': '提醒内容',
    'reminder.dateLabel': '提醒日期',
    'reminder.timeLabel': '提醒时间',
    'reminder.stateFired': '已提醒',
    'reminder.stateMissed': '已错过',
    'reminder.add': '添加提醒',
    'reminder.deleteAria': '删除提醒',
    'reminder.saveFailed': '提醒未保存：{reason}',
    'reminder.deleteFailed': '删除提醒失败：{reason}',
    'reminder.loadFailed': '读取提醒失败：{reason}',

    /* ─── 成员名册（频道成员的**只读**全名单，`CONTEXT.md` **成员名册**） ────
       入口在频道头部；弹窗只回答「这个频道里有谁」，**唯一动作是关闭**——
       加入 / 未加入的管理动作是 **成员面板** 的面，不在这里出现。 */
    'roster.entryTitle': '频道成员',
    'roster.title': '#{name} 成员',
    'roster.sub': '{count} 位成员',
    'roster.loading': '正在读取该频道的成员表…',
    'roster.unavailable': '暂时读不到该频道的成员表（后端请求失败）：恢复后重新打开名册即可。',
    'roster.empty': '这个频道还没有成员。',
    'roster.roleFallback': '成员',
    // 能力摘要（#273）：只读两个数字，来源是 agent scope 的两条清单。读不到时只占位，
    // 不阻塞名册的其它行，也不把「读不到」说成 0。
    'roster.capabilities': 'MCP {mcp} / 技能 {skills}',
    'roster.capabilitiesLoading': 'MCP … / 技能 …',
    'roster.capabilitiesUnavailable': '能力摘要暂不可用',
    'roster.closeAria': '关闭成员名册',

    /* ─── 频道设置：回应预算（#54 / ADR-0007 / 规格 #31 §2） ──────────────────
       入口在频道头；弹窗把**当前人类世代的有效上界**与**下一世代待生效值**分开说清楚，
       并只提供固定档位 3 / 5 / 10。术语沿用 `CONTEXT.md` 的 `## Language` 词条：
       人类世代 / 回应预算 / 跳 / 预算耗尽 / 预算表——不复述成别的说法。 */
    'budget.entryTitle': '回应预算',
    'budget.title': '回应预算',
    'budget.sub': '#{name} · 上界由你设置；已用跳数由后端维护',
    'budget.closeAria': '关闭回应预算设置',
    'budget.loading': '正在读取该频道的预算读数…',
    'budget.loadFailed': '暂时读不到该频道的预算读数（后端请求失败）：{reason}',
    'budget.sectionCurrent': '当前世代',
    'budget.used': '已用',
    'budget.remaining': '剩余',
    'budget.limit': '有效上界',
    'budget.source': '来源',
    'budget.source.channel-override': '频道覆盖',
    'budget.source.built-in-default': '内置默认',
    'budget.state': '状态',
    'budget.exhausted': '预算耗尽',
    'budget.exhaustedNote': '本轮零投递：Agent 消息照常落库，只是不再唤醒任何人（不是出错）。',
    'budget.sectionNext': '下一个人类世代',
    'budget.pending': '已保存上界',
    'budget.pendingEffective': '这是已保存的档位，从下一个人类世代生效；当前世代仍按上面的有效上界算。',
    'budget.pendingSame': '与当前上界相同，没有待生效改动。',
    'budget.tierLabel': '上界档位',
    'budget.tierUnit': '跳',
    'budget.tierDefaultUnit': '跳（默认）',
    'budget.tierHint': '只有这三档；其它取值后端会整体拒绝，不会留下半成品。',
    'budget.save': '保存上界',
    'budget.saved': '已保存 {limit} 跳的上界；从下一个人类世代生效。',
    'budget.saveFailed': '上界未保存：{reason}',

    /* ─── 频道设置：协作拓扑 / 队长 / 项目根 / 后端 / 权限档（#154） ─────────
       规格 §4.6 的第 1 张面。术语沿用 `CONTEXT.md` 与 ADR-0018 / ADR-0021：
       自主 / 队长制 / 队长 / 项目根 / 后端 / 权限档。`team_scope` 是**后端的只读事实**，
       文案只解释它的含义，不在前端拼取值。 */
    'channelConfig.entryTitle': '频道设置',
    'channelConfig.title': '频道设置',
    'channelConfig.sub': '#{name} · 协作模式、队长与工作区默认值',
    'channelConfig.closeAria': '关闭频道设置',
    'channelConfig.loading': '正在读取该频道的配置…',
    'channelConfig.loadFailed': '暂时读不到该频道的配置（后端请求失败）：{reason}',
    'channelConfig.sectionMode': '协作模式',
    'channelConfig.mode.autonomous': '自主',
    'channelConfig.mode.autonomousNote': '成员各自判断是否回应',
    'channelConfig.mode.team': '队长制',
    'channelConfig.mode.teamNote': '队长收口，成员产出走运行记录',
    'channelConfig.modeBadge.title': '协作模式：{mode}',
    'channelConfig.modeBadge.teamWithLeader': '队长制 · {leader}',
    'channelConfig.sectionScope': '生效范围',
    'channelConfig.teamScope': 'team_scope',
    'channelConfig.teamScopeNote': 'team 覆盖该频道与其全部任务线程（channel+task）。',
    'channelConfig.sectionLeader': '队长',
    'channelConfig.leaderSelectLabel': '队长成员',
    'channelConfig.leaderAvailability': '队长可用性',
    'channelConfig.leaderNone': '未选择队长',
    'channelConfig.leaderAvailable': '队长可用',
    'channelConfig.leaderUnavailable': '队长不可用',
    'channelConfig.leaderReason': '原因：{reason}',
    'channelConfig.leaderRequiredHint': 'team 模式必须选择一个当前频道的 Agent 成员作为队长。',
    'channelConfig.membersUnavailable': '频道成员表暂时读不到，队长候选可能不完整。',
    'channelConfig.teamNoticeAutoClaim': '成员 auto_claim 已停用',
    'channelConfig.teamNoticeWake': '任务线程只唤醒队长',
    'channelConfig.sectionWorkspace': '项目根与默认后端',
    'channelConfig.workspaceRootLabel': '项目根路径',
    'channelConfig.workspaceRootPlaceholder': '例如 /Users/you/project',
    'channelConfig.workspaceRootHint': '必须是绝对路径；留空表示未配置（回到默认行为）。',
    'channelConfig.workspaceBackend': '默认后端',
    'channelConfig.backend.local': '本地',
    'channelConfig.backend.localNote': '受限文件工具；Bash 默认关闭',
    'channelConfig.backend.docker': 'Docker',
    'channelConfig.backend.dockerNote': '每 Agent / 会话一个容器',
    'channelConfig.sectionPermission': '默认权限档',
    'channelConfig.permission.read-only': '只读',
    'channelConfig.permission.read-onlyNote': '只允许只读工具与只读命令',
    'channelConfig.permission.ask-writes': '写/执行需审批',
    'channelConfig.permission.ask-writesNote': '读允许；写与执行逐次审批',
    'channelConfig.permission.trusted-writes': '工作目录内自动写',
    'channelConfig.permission.trusted-writesNote': '越界写仍需审批',
    'channelConfig.save': '保存配置',
    'channelConfig.saved': '频道配置已保存。',
    'channelConfig.saveFailed': '配置未保存：{reason}',

    /* ─── 联网（#199）：档位三选 + provider 凭据（只回掩码） ───────────────── */
    'channelConfig.sectionWeb': '联网',
    'channelConfig.webAccess.off': '关闭',
    'channelConfig.webAccess.offNote': '不挂任何联网工具',
    'channelConfig.webAccess.search': '只搜索',
    'channelConfig.webAccess.searchNote': '挂 web_search；查询词会离开本机',
    'channelConfig.webAccess.search+fetch': '搜索+抓取',
    'channelConfig.webAccess.search+fetchNote': '额外挂 fetch；开启后不再逐次审批，可按 host 配 deny / ask 规则收紧',
    'channelConfig.webAccessHint': '未配置时默认「只搜索」；只有「关闭」会移除联网工具。',
    'webProviders.loadFailed': '暂时读不到联网 provider 状态：{reason}',
    'webProviders.active': '当前 provider',
    'webProviders.activeValue': '{provider}（来源：{source}）',
    'webProviders.none': '未确定',
    'webProviders.source.env': '环境变量',
    'webProviders.source.ui': '界面配置',
    'webProviders.source.fallback': '免密钥回退',
    'webProviders.source.invalid': '环境变量取值无效',
    'webProviders.provider.brave': 'Brave',
    'webProviders.provider.tavily': 'Tavily',
    'webProviders.provider.perplexity': 'Perplexity',
    'webProviders.provider.jina': 'Jina',
    'webProviders.provider.exa': 'Exa',
    'webProviders.provider.fallback': 'Exa MCP（免密钥）',
    'webProviders.configuredWithMask': '已配置 · {masked}',
    'webProviders.configuredFromEnv': '由环境变量提供',
    'webProviders.notConfigured': '未配置',
    'webProviders.defaultBadge': '默认',
    'webProviders.keyLabel': '{provider} API key',
    'webProviders.keyPlaceholder': '粘贴这一家的 API key',
    'webProviders.dataNotice': '保存后，查询词会发送给该服务。',
    'webProviders.save': '保存凭据',
    'webProviders.makeDefault': '设为默认',
    'webProviders.clear': '清除',
    'webProviders.keyRequired': '先输入一个非空的 key。',
    'webProviders.saved': '{provider} 的凭据已保存。',
    'webProviders.defaultSet': '默认 provider 已切换为 {provider}。',
    'webProviders.cleared': '{provider} 的凭据已清除。',
    'webProviders.saveFailed': '凭据未保存：{reason}',
    'webProviders.defaultFailed': '默认 provider 未切换：{reason}',
    'webProviders.clearFailed': '凭据未清除：{reason}',

    /* ─── 资源管理（#156 / 规格 §4.6）：Workspace 状态 / MCP / Skill ───────────
       MCP 只出现**变量名**（`mcp.envNames*`）；变量值没有词条，也没有输入口。 */
    'workspaceMgmt.section': '工作区与数据面',
    'workspaceMgmt.root': '已落库的项目根',
    'workspaceMgmt.rootNone': '未配置（默认行为：不挂文件工具）',
    'workspaceMgmt.backend': '已落库的后端',
    'workspaceMgmt.dockerStatus': 'Docker 状态',
    'workspaceMgmt.dockerUnavailableAgent': 'Docker 不可用（{name}）：{reason}',
    'workspaceMgmt.dockerReported': '最近一轮未报告故障（只有容器启动失败时后端才记录原因）',
    'workspaceMgmt.dockerUnused': '当前后端是本地，不启动容器',
    'workspaceMgmt.noteTools': '项目根生效后 Agent 才获得受限文件工具（Read / Write / Edit / Glob / Grep），Bash 默认关闭。',
    'workspaceMgmt.noteLocal': '本地后端：工具直接作用于本机项目根，路径必须落在根内。',
    'workspaceMgmt.noteDocker': 'Docker 后端：每个 Agent / 会话一个容器，只承诺容器与主机的隔离；远端镜像需要先确认数据面告知。',
    'workspaceMgmt.noteConfig': '项目根与后端在上方「项目根与默认后端」中修改；非法路径会被后端整体拒绝，保存处显示可读原因。',
    'mcp.scopeNote': '只显示环境变量名；变量值由服务进程的环境提供，界面与后端响应都不保存值。',
    'mcp.sourceDb': '来自库行 mcp_server（scope_kind=agent）：装配正本，改这里会改变装配结果。',
    'mcp.loading': '正在读取 MCP 声明…',
    'mcp.loadFailed': 'MCP 声明读取失败：{reason}',
    'mcp.empty': '这个 Agent 还没有 MCP 声明。',
    'mcp.name': '名称',
    'mcp.namePlaceholder': '例如 filesystem',
    'mcp.transport': '传输',
    'mcp.transport.stdio': 'stdio（本机进程）',
    'mcp.transport.http': 'http（远端）',
    'mcp.transport.sse': 'sse（远端）',
    'mcp.command': '命令',
    'mcp.commandPlaceholder': '例如 npx',
    'mcp.args': '参数',
    'mcp.argsPlaceholder': '空白分隔，例如 -y @modelcontextprotocol/server-filesystem /tmp',
    'mcp.url': '地址',
    'mcp.urlPlaceholder': '例如 https://example.com/mcp',
    'mcp.envNames': '环境变量名',
    'mcp.envNamesPlaceholder': '逗号分隔，例如 GITHUB_TOKEN, LOG_LEVEL',
    'mcp.envNone': '没有环境变量',
    'mcp.envLabel': '环境变量名：{names}',
    'mcp.envValuesOmitted': '这里只收变量名；变量值不显示、不保存。',
    'mcp.enabledLabel': '启用',
    'mcp.enabled': '已启用',
    'mcp.disabled': '已停用',
    'mcp.enable': '启用',
    'mcp.disable': '停用',
    'mcp.edit': '编辑',
    'mcp.delete': '删除',
    'mcp.consent': '确认数据面',
    'mcp.consentRequired': '远端 MCP 未确认：确认前不会建立连接。',
    'mcp.consented': '数据面确认：已确认',
    'mcp.consentedAt': '数据面确认：已确认于 {time}',
    'mcp.consentDone': '已记录 MCP 数据面确认。',
    'mcp.consentFailed': '确认失败：{reason}',
    'mcp.add': '添加 MCP',
    'mcp.save': '保存修改',
    'mcp.cancel': '取消编辑',
    'mcp.addTitle': '新增 MCP 声明',
    'mcp.editTitle': '编辑 MCP 声明',
    'mcp.created': 'MCP 声明已添加。',
    'mcp.updated': 'MCP 声明已保存。',
    'mcp.deleted': 'MCP 声明已删除。',
    'mcp.saveFailed': '保存失败：{reason}',
    'mcp.deleteFailed': '删除失败：{reason}',
    'mcp.runtimeUnknown': '运行读数：尚未装配（进程重启后读数清空）',
    'mcp.runtimeConnected': '运行读数：已连接 · {count} 个工具',
    'mcp.runtimeFailed': '运行读数：连接失败：{reason}',
    'mcp.isRemote': '远端',
    'mcp.isLocal': '本机',
    'skill.scopeNote': '安装在成员的全局家目录；启用后才会装配进这个 Agent 的工具集。',
    'skill.loading': '正在读取技能列表…',
    'skill.loadFailed': '技能列表读取失败：{reason}',
    'skill.empty': '这个 Agent 还没有安装技能。',
    'skill.hashLabel': 'hash',
    'skill.hashTitle': '完整内容 hash：{hash}',
    'skill.enabled': '已启用',
    'skill.disabled': '已停用',
    'skill.toggleLabel': '启用或停用「{name}」',
    'skill.delete': '删除',
    'skill.installed': '技能已安装：{name}',
    'skill.installFailed': '安装失败：{reason}',
    'skill.toggleFailed': '启停失败：{reason}',
    'skill.deleted': '技能已删除。',
    'skill.deleteFailed': '删除失败：{reason}',
    /* ─── 从本机导入（#277 / ADR-0036；#282 收成一个下拉框） ─────────────
       命令本身是 ASCII 常量（`app.js` 的 `SKILL_INSTALL_COMMAND`），不进词条值；
       这里只说它做什么。三组标题只用于下拉框的 `<optgroup>`，状态后缀用于选项文本。 */
    'skill.importTitle': '从本机导入',
    'skill.importCommandHint':
      '先在本机终端运行上面的命令：-g 装到全局，-a codex 指向 ~/.agents/skills（codex / cline / pi 共用的全局技能目录）。应用只读这个目录，不代为执行；装好之后在下面的下拉框里选它。',
    'skill.importScanButton': '刷新候选',
    'skill.importPickLabel': '从本机导入',
    'skill.importPickPlaceholder': '选择一个技能导入…',
    'skill.importGroupReady': '可选',
    'skill.importGroupInstalled': '已安装',
    'skill.importGroupBlocked': '不可导入',
    'skill.importLoading': '正在扫描 ~/.agents/skills…',
    'skill.importScanFailed': '候选扫描失败：{reason}',
    'skill.importUnavailable': '没有找到 {source}：先运行上面的命令装一个技能，再回来刷新候选。',
    'skill.importEmpty': '这个目录里还没有技能：先运行上面的命令，再回来刷新候选。',
    'skill.importStatusInstalled': '已安装',
    'skill.importStatusBlocked': '不可导入：{reason}',
    'skill.importBusy': '正在导入…',
    'skill.importResultDone': '{name}：已导入',
    'skill.importResultSkipped': '{name}：已安装，已跳过',
    'skill.importResultFailed': '{name}：导入失败：{reason}',
    /* ── skills 页签的右栏详情（#283 / D5）──
       五项元数据 + 两个动作 + SKILL.md 正文（纯文本）。正文的四种状态各有一句话：
       `missing`（字节缺席）/ `invalid`（路径越界）/ `unreadable`（读不出来）互不相同，
       因为用户要靠这句话知道自己该去修什么。 */
    'skill.detailTitle': '技能详情',
    'skill.detailName': '名称',
    'skill.detailDescription': '描述',
    'skill.detailSource': '来源路径',
    'skill.detailHash': '内容 hash',
    'skill.detailState': '启停态',
    'skill.detailEmpty': '这个 Agent 还没有技能：先从上面的「从本机导入」装一个，再回来看它的详情。',
    'skill.detailBodyTitle': 'SKILL.md 正文',
    'skill.detailBodyLoading': '正在读取 SKILL.md…',
    'skill.detailBodyFailed': 'SKILL.md 读取失败：{reason}',
    'skill.detailMissing': '这个技能的字节不在家目录（技能目录或 SKILL.md 缺失）：元数据还在，正文读不到。',
    'skill.detailInvalid': 'SKILL.md 的路径越出全局根（根外 symlink）：为安全起见不显示正文。',
    'skill.detailUnreadable': 'SKILL.md 读不出来（权限或非 UTF-8）：元数据还在，正文读不到。',

    /* ─── 工作区设置（本票只有语言偏好与消息密度两档） ───────────────── */
    'settings.title': '设置',
    'settings.sub': '工作区偏好',
    'settings.closeAria': '关闭设置',
    'settings.languageLabel': '语言',
    'settings.languageZh': '简体中文',
    'settings.languageEn': 'English',
    'settings.densityLabel': '消息密度',
    'settings.densityComfortable': '舒适',
    'settings.densityComfortableHint': '默认',
    'settings.densityCompact': '紧凑',
    'settings.densityCompactHint': '更多消息',
    'settings.themeLabel': '主题',
    'settings.themeLight': '浅色',
    'settings.themeLightHint': '始终浅色',
    'settings.themeDark': '深色',
    'settings.themeDarkHint': '始终深色',
    'settings.themeSystem': '跟随系统',
    'settings.themeSystemHint': '随系统外观切换',
    'settings.saveFailed': '设置未保存：{reason}',
    'settings.done': '完成',

    /* ─── 提示（toast） ─────────────────────────────────────────────── */
    'toast.taskCreated': '已创建任务「{title}」。',
    'toast.titleUpdateFailed': '更新标题失败：{reason}',
    'toast.statusUpdateFailed': '更新状态失败：{reason}',
    'toast.transitionInvalid': '不能从「{from}」移到「{to}」——不是合法迁移',
    'toast.assigneeUpdateFailed': '更新指派失败：{reason}',
    'toast.taskMessageFailed': '任务消息未发送：{reason}',
    'toast.openTaskFailed': '打开任务失败：{reason}',
    'toast.taskReadFailed': '任务标读失败：{reason}',
    'toast.readFailed': '标读失败：{reason}',
    'toast.sendBlockedOffline': '后端不可达，发送已禁止；消息没有被送出。',
    'toast.sendMessageLost': '后端不可达：这条消息没有送达，也没有留在本地队列里。',
    'toast.sendRejected': '发送被拒绝：{reason}',
    'toast.attachFailed': '附件上传失败：{reason}',
    'toast.attachPending': '还有 {count} 个附件在上传，等它落地再发送（否则这条消息会漏掉它）。',
    'toast.sessionLoadFailed': '读取会话失败：{reason}',
    'toast.memberFallback': '成员',
    'toast.joinedChannel': '已把 {name} 加入 #{channel}，现在可以在 composer 里用 @ 提到它。',
    'toast.alreadyMember': '{name} 本来就在 #{channel} 里。',
    'toast.joinFailed': '加入失败：{reason}',
    'toast.channelCreated': '已创建频道 #{name}，可以直接发送第一条消息。',
    'toast.dmCreated': '已创建与 {name} 的私信。',
    'toast.dmOpened': '已打开与 {name} 的现有私信。',
    'toast.dmOpenFailed': '打开私信失败：{reason}',
    'toast.agentCreated':
      '已创建 Agent {name}，在右侧「频道成员」面板的「未加入」一段点「加入」即可让它进入当前频道。',
    'toast.remoteAck': '已记录数据流告知确认：{provider}。',
    'toast.remoteAckFailed': '确认失败：{reason}',
    'toast.agentUpdated': '已保存 {name} 的配置。',
    'toast.agentDeleted': '已删除 Agent「{name}」。',
    'toast.agentDeleteFailed': '删除 Agent 失败：{reason}',
    'toast.agentDetailFailed': '读取 Agent 详情失败：{reason}',
    'toast.globalMcpConsentFailed': '确认全局 MCP 条目失败：{reason}',
  },

  en: {
    /* ─── App shell ────────────────────────────────────────────────── */
    'app.title': 'alibot · collaboration workspace',
    'app.brandSub': 'LOCAL · this machine',
    'app.notMigrated': 'This entry is not in the Vue workspace yet — use /studio/',
    'identity.human': 'Human · this machine',
    'identity.me': 'Me',

    /* ─── Mobile shell (≤900px): the topbar carries a single control ── */
    'mobile.railToggle': 'Open navigation',

    /* ─── Left rail ────────────────────────────────────────────────── */
    'rail.nav': 'Workspace navigation',
    'rail.newChannel': 'New channel',
    'rail.searchTitle': 'Search the workspace',
    'rail.searchPlaceholder': 'Search messages, tasks…',
    'rail.newAgent': 'New agent',
    'rail.newAgentTitle': 'New agent',
    'rail.groupChannels': 'Channels',
    'rail.groupDms': 'Direct messages',
    'rail.groupArchivedDms': 'Archived',
    'rail.groupAgents': 'Local agents',
    'rail.emptyChannels': 'No channels yet. Create one to start recording.',
    'rail.emptyDms': 'No direct messages yet. Press “Send message” on an agent detail page to start one.',
    'rail.emptyAgents': 'No agents yet.',
    'rail.openDmAria': 'Open the direct message with {name}',
    'settings.open': 'Settings',
    'rail.reminders': 'Reminders',

    /* ─── Status dots and task statuses ────────────────────────────── */
    'status.online': 'Idle',
    'status.working': 'Replying',
    'status.error': 'Error',
    'status.offline': 'Disabled',
    'task.status.todo': 'Unclaimed',
    'task.status.in_progress': 'In progress',
    'task.status.in_review': 'In review',
    'task.status.done': 'Done',
    'task.status.closed': 'Closed',
    'task.transition.in_progress': 'Move to in progress',
    'task.transition.in_review': 'Submit for review',
    'task.transition.done': 'Mark done',
    'task.transition.todo': 'Send back to unclaimed',
    'task.transition.closed': 'Close task',

    /* ─── The four normal round outcomes ───────────────────────────── */
    'outcome.ignored.label': 'Ignored by choice',
    'outcome.ignored.note': 'Ignored by choice (a normal protocol decision, not a failure)',
    'outcome.budget-exhausted.label': 'Budget exhausted',
    'outcome.budget-exhausted.note':
      'Budget used up, nothing delivered this round (not an error; the member is still idle)',
    'outcome.merged.label': 'Merged',
    'outcome.merged.note':
      'A duplicate wake-up was merged into the round already running (not a failure)',
    /* `silent` (#58 / ADR-0008): the model declared silence through the **structured channel**
       (the `declare_silence` tool). Not a failure, and not `ignored` (the existing self-judged
       ignore, which carries no structured declaration). */
    'outcome.silent.label': 'Silence declared',
    'outcome.silent.note':
      'The model declared silence for this round through the structured channel and produced no body '
      + '(not a failure, and not an ignored round)',
    'outcome.summaryAria': '{names} did not reply: {count} round outcomes',
    'outcome.collapse': 'Hide reasons',
    'outcome.expand': 'No reply · show reasons',
    'outcome.lineReasonEmpty': 'The backend left no reason.',
    'outcome.failedAria': '{name} did not reply: {reason}',
    'outcome.tag': 'No reply',
    'outcome.stateDisabled': 'Disabled',
    'outcome.stateFailed': 'This round failed',
    'outcome.inspect': 'View member status',
    'outcome.disabledNote':
      '{name} is disabled right now, so this round got no reply. The backend gives this reason:',

    /* ─── Silent narration (a message's **presentation downgrade**, not an
       outcome status). It is still a real message: it keeps its own `seq` and
       counts as unread. Only how that one line reads, plus the wording of the
       expandable "related round outcome reason" block. ─────────────────── */
    'narration.tag': 'Aside',
    'narration.expand': 'Show this round’s result',
    'narration.collapse': 'Hide this round’s result',
    'narration.toggleAria': 'Aside from {name}: {action}',
    'narration.reasonMissing': 'This round’s outcome is not available right now.',

    /* ─── Model providers ──────────────────────────────────────────── */
    'provider.dashscope': 'DashScope (remote)',
    'provider.openai': 'OpenAI (remote)',
    'provider.openai-response': 'OpenAI Responses (remote)',
    'provider.anthropic': 'Anthropic (remote)',
    'provider.gemini': 'Gemini (remote)',
    'provider.deepseek': 'DeepSeek (remote)',
    'provider.moonshot': 'Moonshot (remote)',
    'provider.minimax': 'MiniMax (remote)',
    'provider.volcengine': 'Volcengine (remote)',
    'provider.xai': 'xAI (remote)',
    'provider.ollama': 'Ollama (local)',

    /* ─── Shared ───────────────────────────────────────────────────── */
    'common.cancel': 'Cancel',
    'common.close': 'Close',
    'common.nameListSep': ', ',
    'common.colonSep': ': ',
    'common.nameListMore': '{names} and {count} more',
    'common.clauseSep': '; ',
    'time.today': 'Today',
    'time.unknown': 'Unknown time',
    'error.badRequestBody': 'Invalid request body',
    'error.httpStatus': 'Backend returned {status}',

    /* ─── Search (main-line view: four facets + three hit kinds) ───── */
    'search.fieldPlaceholder': 'Search messages, tasks, members…',
    'search.fieldLabel': 'Search the workspace',
    'search.facetsLabel': 'Search scope',
    'search.facetAll': 'All',
    'search.facetMessage': 'Messages',
    'search.facetTask': 'Tasks',
    'search.facetAgent': 'Agents',
    'search.sourceAgent': 'Members',
    'search.targetChannel': 'Channel · #{name}',
    'search.targetDm': 'Direct message · {name}',
    'search.targetTask': 'Task · {name}',
    'search.unknownTarget': 'Unknown target',
    'search.unknownAuthor': 'Unknown author',
    'search.authorDeleted': 'Author deleted',
    'search.snipAuthor': 'From {name}',
    'search.snipTask': '#{id} · {status}',
    'search.snipAgent': '{status} · {model}',
    'search.searching': 'Searching…',
    'search.idleTitle': 'Search the whole workspace',
    'search.idleBody': 'Messages cannot be edited or deleted; search covers messages, tasks and agents.',
    'search.emptyTitle': 'Nothing matches “{query}”',
    'search.emptyBody': 'Try another keyword, or check the spelling.',
    'search.offline': 'Backend unreachable: search cannot run.',
    'search.failed': 'Search failed: the backend is unreachable or the request did not finish ({reason}).',

    /* ─── ⌘K command palette (only genuinely bound shortcuts, see D10) ── */
    'cmd.ariaLabel': 'Command palette',
    'cmd.placeholder': 'Type a command or search…',
    'cmd.empty': 'No matching command',
    'cmd.groupJump': 'Go to',
    'cmd.groupAction': 'Actions',
    'cmd.groupChannels': 'Channels',
    'cmd.messages': 'Message view',
    'cmd.board': 'Task board',

    /* ─── Connection and channel chrome ────────────────────────────── */
    'conn.banner':
      'Backend unreachable: showing the last successfully read state and sending is disabled.',
    'conn.retry': 'Retry connection',
    'conn.reconnecting':
      'Reconnecting the event stream: missed messages are backfilled automatically once it recovers.',
    'load.workspaceFailed': 'Failed to read the backend: {reason}',
    'tabs.viewLabel': 'Channel views',
    'tabs.messages': 'Messages',
    'tabs.tasks': 'Tasks',
    'tabs.tasksApprovalDot': '{count} tasks in this channel are waiting for approval',
    'empty.noChannelTitle': 'No channels yet',
    'empty.noChannelBody': 'Create a channel to write the first permanent record.',
    'empty.noMessageTitle': 'No messages in this channel yet',
    'empty.noMessageBody':
      'Messages here cannot be edited or deleted — they are permanent records.',
    'header.dmDesc': 'Two-way direct message with {name}',
    'header.dmPlaceholder': 'Message @{name} — you can still @ someone to reach them',
    'header.dmArchivedDesc': 'This agent was deleted; this direct message is kept as a read-only archive.',
    'header.dmArchivedPlaceholder': 'This agent was deleted; this direct message is archive-only',
    'header.channelPlaceholder': 'Message #{name} — type @ to mention an agent',
    'typing.working': '{name} is replying…',
    'typing.nameSeparator': ', ',
    'typing.stopNamedShort': 'Stop {name}',
    'typing.stopNamed': 'Stop the current round for {name}',
    /* ─── Composer and @ completion ────────────────────────────────── */
    'composer.mentionLabel': 'Agent members of this channel that can be mentioned',
    'composer.placeholder': 'Message #channel-name',
    'composer.inputLabel': 'Message body',
    'composer.mentionAction': 'Mention an agent',
    'composer.attachAction': 'Attach a file',
    'composer.attachRemove': 'Remove attachment {name}',
    'composer.attaching': 'Uploading {count} attachment(s)…',
    'composer.attachTooLarge': '{name} exceeds the per-file limit ({limit})',
    'composer.asTaskAction': 'Send as task',
    'composer.hint': 'Enter to send · Shift+Enter for a new line',
    'composer.archivedHint': 'Archived · read-only, sending is disabled',
    'composer.send': 'Send',
    'composer.note':
      'Messages cannot be edited or deleted — permanent records · single user on this machine, the backend only listens on loopback · remote models are announced before they are enabled',
    'mention.hintPick': '↑↓ to choose · Enter to select · Esc to close',
    'mention.hintDismiss': 'Enter to send · Esc to close the list',
    'mention.unavailable':
      'The channel member list is not readable right now (the backend request failed): press @ again once it recovers.',
    'mention.empty':
      'This channel has no agent members yet: use “Join” in the “Not in this channel” group of the “Channel members” panel, or create an agent first.',
    'mention.cannotMention': 'Cannot be @-mentioned',
    'mention.quoteUnsupported':
      '“{name}” contains all three closing quotes (" , ” , ’), so the mention parser has no quoting form for this name — nothing was inserted.',

    /* ─── Task board and task thread ───────────────────────────────── */
    'board.viewLabel': 'Task views',
    'board.board': 'Board',
    'board.list': 'List',
    'board.filtersLabel': 'Task filters',
    'board.newTask': 'New task',
    'board.mine': 'My tasks',
    'board.allChannels': 'All channels',
    'board.emptyColumn': 'No tasks in this column yet',
    'board.noMatchTitle': 'No matching tasks',
    'board.noMatchBody': 'Try clearing the filters, or create a task.',
    'board.openThread': 'Open thread →',
    'board.cardAria': '{title}, {status} — open the task thread',
    'board.cardApprovalAria': '{title}, {status} — {count} pending approvals, open the task thread',
    'board.unreadAria': '{count} unread task messages',
    'board.approvalBadge': '{count} pending',
    'board.approvalBadgeAria': '“{title}” has {count} pending approvals',
    'board.cardMenuAria': 'Transition menu for “{title}”',
    'board.cardMenuLabel': 'Task transitions',
    'task.selectChannel': 'Choose a channel',
    'task.createChannelFirst': 'Create a channel first',
    'task.threadTag': 'Task thread',
    'task.teamNotice': 'Task threads only wake the team leader',
    'task.threadHeadSub': '#{channel} · {count} discussion messages',
    'task.threadUnread': ' · {count} unread',
    'task.noTransitions': 'Closed — no transitions available',
    'task.discussionCount': 'Discussion · {count}',
    'task.repliesEmpty': 'No discussion yet — write the first reply',
    'task.historyEmpty': 'No change history yet',
    'task.unassigned': 'Unclaimed',
    // Task history still points at the original member id after a delete; when the member table
    // no longer has it, show this readable fallback instead of the raw id.
    'task.deletedAssignee': 'Deleted member',
    'task.unassignedPill': 'Unclaimed · click to assign',
    'task.reassignHint': '· reassign',
    'task.assignUnavailable': 'The channel member list is not readable right now: no assignee candidates.',
    'task.assignLoading': 'Reading the channel members…',
    'task.assignNoAgents':
      'This channel has no agent members yet: add one from the “Channel members” panel on the right.',
    'task.clearAssign': 'Clear assignee',
    'dock.move': 'Move',
    'dock.assign': 'Assign',
    'dock.history': 'Change history',
    'dock.label': 'Detail panel',
    'dock.toggle': 'Toggle detail panel',
    // ≤900px: the dock's own way out once it is a right-hand drawer (never shown above 900px, see `app.css`).
    'dock.closePanel': 'Close the details panel',
    'dock.membersTitle': 'Channel members',
    'dock.membersRole':
      'Agents already in this channel and agents that can still be added; the status dot is derived from backend facts: idle / replying / error / disabled',
    'dock.threadClose': 'Close the task thread',
    'dock.replyPlaceholder': 'Reply to this task thread…',
    'dock.replyInputLabel': 'Task message body',
    'dock.replyHint': 'Enter to reply · Shift+Enter for a new line',
    'dock.replySend': 'Reply',
    'dock.openChannelFirst': 'Open a channel first.',
    'dock.joined': 'Joined ({count})',
    'dock.membersUnavailable':
      'The channel member list is not readable right now (the backend request failed): reopen the panel once it recovers.',
    'dock.membersLoading': 'Reading the channel member list…',
    'dock.noJoinedAgents': 'This channel has no agent members yet: press “Join” on an agent below.',
    'dock.notJoined': 'Not in this channel ({count})',
    'dock.notJoinedUnavailable':
      'While the member list is unreadable, agents are not listed as “not in this channel” — otherwise agents already here would look absent.',
    'dock.allJoined': 'Every agent in this workspace is already in this channel.',
    'dock.noAgents':
      'No agents yet: create one with the “Agent” button at the top left, then come back and press “Join”.',
    'dock.noModel': 'No model configured',
    'dock.joining': 'Joining…',
    'dock.join': 'Join',
    'dock.joinAria': 'Add {name} to the current channel',

    /* ─── Run panel (#155 / spec §4.6; dock's third form) ──────────── */
    'runPanel.entryTitle': 'Run panel',
    'runPanel.taskEntryTitle': 'Task run panel',
    'runPanel.tag': 'Run panel',
    'runPanel.sub': 'Member events · assignments · Plan · SOP (read-only)',
    'runPanel.refresh': 'Refresh snapshot',
    'runPanel.closeAria': 'Close the run panel',
    'runPanel.loading': 'Loading the run snapshot…',
    'runPanel.error': 'Could not load the run snapshot: {reason}',
    'runPanel.retry': 'Retry',
    'runPanel.sectionRuns': 'Runs and assignments',
    'runPanel.sectionEvents': 'Member events',
    'runPanel.sectionPlan': 'Plan',
    'runPanel.sectionSop': 'SOP',
    'runPanel.runsEmpty': 'No team runs yet.',
    'runPanel.runsEmptyAutonomous': 'Autonomous channels have no leader, so they do not produce team runs or assignments. To see team runs, switch the channel to Team under “Channel settings → Collaboration mode”.',
    'runPanel.noAssignments': 'This run has no assignment records.',
    'runPanel.eventsEmpty': 'No member events yet (live-only; not replayed while disconnected).',
    'runPanel.planEmpty': 'No Plan yet.',
    'runPanel.planEmptyAutonomous': 'Conversations in autonomous channels do not produce a Plan. Task run records live on the task itself; open the Task run panel from the task thread header.',
    'runPanel.sopEmpty': 'No SOP runs yet.',
    'runPanel.taskRef': 'Task #{id}',
    'runPanel.replyRef': 'Reply {id}',
    'runPanel.awaiting': 'Awaiting human confirmation (reply {reply})',
    'runPanel.verdictPassed': 'Passed',
    'runPanel.verdictFailed': 'Not passed',
    'runPanel.stepIndex': 'Step {index}',
    'runPanel.noSteps': 'This run has no step records yet.',
    'runPanel.duration': 'Took {seconds}s',
    'runPanel.inFlight': 'In progress',
    'runPanel.state.started': 'Started',
    'runPanel.state.succeeded': 'Succeeded',
    'runPanel.state.failed': 'Failed',
    'runPanel.state.interrupted': 'Interrupted',
    'runPanel.state.budgetExhausted': 'Budget exhausted',
    'runPanel.state.running': 'Running',
    'runPanel.state.completed': 'Completed',
    'runPanel.state.invalidLeader': 'Invalid leader',
    'runPanel.state.pending': 'Pending',
    'runPanel.state.inProgress': 'In progress',
    'runPanel.state.awaiting': 'Awaiting',
    'runPanel.state.unknown': 'Unknown',

    /* ─── Approval card (#153 / spec §4.6 item 4): the human-facing HITL action ─── */
    'approval.section': 'Approvals',
    'approval.cardLabel': 'Tools awaiting approval: {tools}',
    'approval.tag': 'Approval',
    'approval.pending': 'Pending',
    'approval.approved': 'Approved',
    'approval.denied': 'Denied',
    'approval.expired': 'Expired',
    'approval.stateUnknown': 'Unknown state',
    'approval.remaining': '{time} left',
    'approval.allow': 'Allow',
    'approval.deny': 'Deny',
    'approval.submitting': 'Submitting…',
    'approval.noInput': '(no arguments)',
    'approval.unknownTool': 'Unknown tool',
    'approval.submitFailed': 'Could not submit: {reason}',

    /* ─── Message rows ─────────────────────────────────────────────── */
    'msg.systemAuthor': 'System',
    'msg.tagEvent': 'Event',
    'msg.tagAgent': 'Agent',
    'msg.pendingTag': 'Pending',
    'msg.failedTag': 'Failed',
    'msg.notDelivered': 'Not delivered: {reason}',
    'msg.sendFailed': 'Send failed',
    'msg.retry': 'Retry',
    'msg.discard': 'Remove',
    'msg.taskRef': 'Task · {title}',
    'msg.taskRefAria': 'Open task {title}',
    'msg.taskThreadEntry': 'Enter task thread',
    'msg.taskThreadEntryUnread': 'Enter task thread · {count} unread',
    'msg.taskThreadEntryAria': 'Open the task thread for {title}',
    'msg.taskThreadEntryAriaUnread': 'Open the task thread for {title} — {count} unread',
    'msg.attachmentAria': '{name} ({size}) — download',
    'attach.fallbackName': 'Attachment',

    /* ─── Modals ───────────────────────────────────────────────────── */
    'task.modalTitle': 'New task',
    'task.modalSub': 'A task gets its own thread; status changes are recorded in its change history.',
    'task.titleLabel': 'Task title',
    'task.titlePlaceholder': 'Write a new task…',
    'task.channelLabel': 'Channel',
    'task.channelHint':
      'A task belongs to exactly one channel; assign it with the “Assign” chips inside the task thread.',
    'task.create': 'Create task',
    'task.titleRequired': 'The task title cannot be empty.',
    'task.noChannel': 'No channels yet: create a channel before creating a task.',
    'channel.modalTitle': 'New channel',
    'channel.modalSub': 'Messages can be sent to it right after creation — no refresh or restart.',
    'channel.nameLabel': 'Channel name',
    'channel.namePlaceholder': 'e.g. mobile experience',
    'channel.descLabel': 'Description',
    'channel.descPlaceholder': 'What is this channel about?',
    'channel.membersLabel': 'Initial members',
    'channel.membersHint':
      'Ticked agents become explicit members of the channel. If there is nothing to tick yet, create one with the “Agent” button at the top left.',
    'channel.create': 'Create channel',
    'channel.pickEmpty':
      'No agents to pick yet: create one with the “Agent” button at the top left, then tick it here to add it to the channel.',
    'agent.modalTitle': 'New agent',
    'agent.modalSub':
      'The model and system prompt are stored as configuration data; changing the model needs no code change.',
    'agent.nameLabel': 'Name',
    'agent.namePlaceholder': 'e.g. Ming',
    'agent.roleLabel': 'Role',
    'agent.rolePlaceholder': 'e.g. frontend engineer',
    'agent.providerLabel': 'Model provider',
    'agent.modelLabel': 'Model name',
    'agent.modelPlaceholder': 'e.g. qwen-max / gpt-4.1 / qwen2.5',
    'agent.modelHintPrefix': 'Saved as ',
    'agent.modelHintSuffix': ' model identifiers.',
    'agent.promptLabel': 'System prompt',
    'agent.promptPlaceholder': 'How should this agent work?',
    'agent.remoteTitleStatic': 'Confirm once before enabling a remote model',
    'agent.remoteTitle': 'Confirm once before enabling {provider}',
    'agent.remoteBody':
      'Once enabled, this data leaves your machine and goes to that model provider: {faces}. The receiver is a remote service, not your machine.',
    'agent.remoteHint':
      'Local storage does no application-level end-to-end encryption, and none is claimed; the backend only listens on loopback by default.',
    'agent.remoteBodyUnavailable':
      'The list of data faces that leave this machine has not loaded yet, so the confirmation is unavailable for now (reopening the dialog will retry).',
    'agent.remoteConfirm': 'I understand — confirm sending',
    'agent.create': 'Create agent',
    'agent.providerLocalHint': 'Local model: it never leaves this machine and needs no data-flow consent.',
    'agent.providerRemoteHint':
      'Remote model: enabling it first requires confirming that data leaves this machine (see the notice below).',
    'agent.autoClaimLabel': 'Auto-claim tasks',
    'agent.autoClaimOn': 'On',
    'agent.autoClaimOnHint': 'The orchestrator takes new tasks',
    'agent.autoClaimOff': 'Off',
    'agent.autoClaimOffHint': 'New tasks stay unclaimed',
    'agent.autoClaimNote':
      'When on: the orchestrator hands new tasks in this Agent\'s channels to it (one claimant at a time); a claim writes a readable system record, wakes the Agent, and resets that task\'s response budget once — at most once per task.',
    // Agent-level web access entry (#202 / 2026-10-21 revision): the default level is "follow
    // channel" — it writes no agent-level override, and picking it submits an explicit null
    // (clearing the override). A direct message has no channel, so it falls back to the default
    // "search only" level. The other three levels share names and meaning with the channel
    // settings; `fetch` mounts only in channels / task threads (a DM has no approval surface).
    'agent.webAccessLabel': 'Web access',
    'agent.webAccessFollow': 'Follow channel (default)',
    'agent.webAccessFollowHint': 'Clear the agent-level override; DMs use the default level',
    'agent.webAccessOff': 'Off',
    'agent.webAccessOffHint': 'Mount no web tool',
    'agent.webAccessSearch': 'Search only',
    'agent.webAccessSearchHint': 'Mount web_search; queries leave this machine',
    'agent.webAccessSearchFetch': 'Search + fetch',
    'agent.webAccessSearchFetchHint': 'Also mount fetch; it no longer asks every time — narrow it per host with deny / ask rules',
    'agent.webAccessNote':
      'Follow channel = no agent-level override (submit null); a direct message uses the default "search only" level and never mounts fetch.',

    /* ─── Agent detail (main view `state.mode === 'agent'`): three tabs, edited in place ──
       The detail is not a dock form (user decision 2026-10-22): it takes the whole right side and
       the dock steps aside. The page holds **three tabs** (`CONTEXT.md`, Agent Detail Tab):
       Basics and configuration → skills → mcp, in that fixed order, defaulting to the first tab and
       stored nowhere (`tabsLabel` / `tabBase` / `tabSkills` / `tabMcp`; the old `tabOverview` /
       `tabRuntime` names are not reused). Every number is derived from **backend facts**
       (`stats`: aggregates over `round_outcome` plus task rows). The model id / provider / remote
       flag / remote data-flow notice are no longer repeated as a read-only block (decision
       2026-10-24): their editable form lives in the configuration section (see the `agent.*` keys).
       The three context numbers (window / compression threshold / max iterations) no longer have a
       UI entry point, so the four keys that served them only (`agent.contextWindow` /
       `contextTrigger` / `maxIters` / `runtimeDefaultHint`) are gone too — the columns and the
       PATCH field set stay as they were. Memory / latency / tool permissions / temperature /
       token usage / context usage have no data surface in this repo, so there are no strings for
       them here. */
    'agentDetail.sendMessage': 'Send message',
    'agentDetail.tabsLabel': 'Agent detail tabs',
    'agentDetail.tabBase': 'Basics and configuration',
    'agentDetail.tabSkills': 'skills',
    'agentDetail.tabMcp': 'mcp',
    'agentDetail.mcpFileTitle': 'Home-directory mcp.json',
    // Bottom block of the mcp tab (#284 / D7): the textarea edits the file's raw text, and each
    // block labels its data source.
    'agentDetail.mcpFileSource':
      'From the mcp.json in this agent’s home directory (the file’s raw text); it is an import '
      + 'source and is NOT assembled at runtime — import it into database rows to make it take '
      + 'effect (`python -m alibot migrate-mcp`).',
    'agentDetail.mcpFileLoading': 'Reading the home-directory mcp.json…',
    'agentDetail.mcpFileMissing':
      'This agent’s home directory has no mcp.json yet (that is not an empty file): saving once creates it from what you write.',
    'agentDetail.mcpFileServers': 'Entries in the file: {count}',
    'agentDetail.mcpFileSave': 'Save mcp.json',
    'agentDetail.mcpFileLoadFailed': 'Could not read the home-directory mcp.json: {reason}',
    'agentDetail.mcpFileSaveFailed': 'Could not save the home-directory mcp.json: {reason}',
    'agentDetail.openAria': 'Open the details of {name}',
    'agentDetail.sectionFacts': 'Stats',
    'agentDetail.statChannels': 'Channel memberships',
    'agentDetail.statChannelsHint':
      'Source: how many channels it is a member of (the backend channel member list).',
    'agentDetail.statRounds': 'Rounds taken part in',
    'agentDetail.statRoundsHint':
      'Source: how many round outcomes it took part in (round_outcome, across channels and tasks).',
    'agentDetail.statTasksDone': 'Tasks completed',
    'agentDetail.statTasksDoneHint': 'Source: task rows assigned to it whose status is “Done”.',
    'agentDetail.statSuccess': 'Success rate',
    'agentDetail.statSuccessHint':
      'Source: replied rounds ÷ (replied + deliberately ignored + failed) rounds; shows “—” with no samples.',
    'agentDetail.noSamples': '—',
    'agentDetail.sectionConfig': 'Configuration',
    'agentDetail.pause': 'Pause',
    'agentDetail.resume': 'Resume',
    'agentDetail.kvPrompt': 'System prompt',
    // Web access (#202 / 2026-10-21 revision): a missing value (`null`) means there is no
    // agent-level override, i.e. "follow channel" — a reading, not an empty field; an override
    // shows its level. The detail page never derives the level that is live in a DM.
    // Global MCP (#236 / ADR-0031 D8): the entry view is read-only (it lives in the lower block of
    // the mcp tab, next to the mcp.json textarea -- two views of the same data). The entry view itself
    // renders no edit / enable / delete; changing the source text goes through the textarea above.
    // Only an unacknowledged remote entry gets an acknowledge action.
    'agentDetail.globalMcpTitle': 'Global MCP (read-only)',
    'agentDetail.globalMcpEmpty': 'No MCP servers are declared in the global mcp.json.',
    'agentDetail.globalMcpRemote': 'Remote (data leaves this machine)',
    'agentDetail.globalMcpLocal': 'Local',
    'agentDetail.globalMcpConsented': 'Acknowledged',
    'agentDetail.globalMcpConsentPending': 'Not acknowledged (not connected yet)',
    'agentDetail.globalMcpNoConsent': 'Local entry: no acknowledgement needed',
    'agentDetail.globalMcpConfirm': 'Acknowledge',
    'agentDetail.globalMcpConfirmed': 'Acknowledged: {name}',
    'agentDetail.globalMcpRuntimeIdle': 'No runtime reading yet',
    'agentDetail.globalMcpRuntimeConnected': 'Connected at runtime',
    // Danger zone (this ticket): pause / delete moved to the last section of the page. The note
    // spells out both consequences; delete goes through a confirmation modal whose title and body
    // carry the agent name and "cannot be undone", so the cost is readable before the click.
    'agentDetail.sectionDanger': 'Danger zone',
    'agentDetail.dangerHint':
      'Danger: pausing only stops replies (the agent is still woken and leaves a failed round outcome with a readable reason); deleting removes this agent from the agent list and every channel, while DMs, message history and tasks are kept. Confirm before taking either action.',
    'agentDetail.delete': 'Delete',
    'agentDetail.deleteTitle': 'Delete agent',
    'agentDetail.deleteConfirm':
      'Delete “{name}”? It will be removed from the agent list and every channel immediately and this detail view will close. DMs, message history and tasks are kept, but the agent cannot be restored — this cannot be undone.',
    'agentDetail.deleteConfirmAction': 'Delete permanently',
    'agentDetail.deleteCancel': 'Cancel',
    'agentDetail.save': 'Save',
    'agentDetail.discard': 'Discard',
    'agentDetail.unsaved': 'Unsaved changes',

    /* ─── Capabilities (#273 / ADR-0031 §2 as revised by #270) ───
       One place only: the MCP declarations and skills inside the agent detail page. The scope is
       always this member (`scope_kind=agent&scope_id=<member>`) with no scope picker; legacy
       channel-level rows stay in the database but are neither shown nor assembled. Every control
       acts on this member only (#281 removed the cross-member "copy to other members" path); #280
       split these two blocks into the skills / mcp tabs, so the shared title has no consumer. */
    'agentCapabilities.scopeNote':
      'These capabilities belong to this agent only; skill bytes live in ~/.alibot/agents/{member}/skills/ (the shared library ~/.alibot/skills/ is read-only).',
    'agentCapabilities.mcpTitle': 'MCP declarations',
    'agentCapabilities.skillTitle': 'Skills',

    /* ─── Reminders (ADR-0012: backend objects; due-time output in ADR-0017) ───
       Both values state existence and persistence only: reminders live in the backend and
       survive a refresh or a restart. Due time is a separate truth, so do not promise it from
       here: a reminder that belongs to a channel does drop one `system` message into that
       channel's stream (ADR-0017 §1), but due time wakes nobody, consumes no reply budget and
       never counts as unread. Two cases drop no message: a reminder with no channel has
       nowhere to land, and one that is only noticed more than 24 hours late is not back-filled
       (ADR-0017 §3) — both are marked as fired only (CONTEXT.md **到点**). */
    'reminder.modalTitle': 'Reminders',
    'reminder.modalSub':
      'Reminders are stored in the local backend; they survive a refresh or a restart.',
    'reminder.closeAria': 'Close reminders',
    'reminder.emptyTitle': 'No reminders yet',
    'reminder.emptyBody': 'Reminders live in the backend, not in this browser.',
    'reminder.newLabel': 'New reminder',
    'reminder.textLabel': 'Reminder text',
    'reminder.dateLabel': 'Reminder date',
    'reminder.timeLabel': 'Reminder time',
    'reminder.stateFired': 'Reminded',
    'reminder.stateMissed': 'Missed',
    'reminder.add': 'Add reminder',
    'reminder.deleteAria': 'Delete reminder',
    'reminder.saveFailed': 'Reminder not saved: {reason}',
    'reminder.deleteFailed': 'Could not delete the reminder: {reason}',
    'reminder.loadFailed': 'Could not read reminders: {reason}',

    /* ─── Member roster (the read-only full list of a channel's members) ─── */
    'roster.entryTitle': 'Channel members',
    'roster.title': '#{name} members',
    'roster.sub': '{count} members',
    'roster.loading': 'Reading the channel member list…',
    'roster.unavailable':
      'The channel member list is not readable right now (the backend request failed): reopen the roster once it recovers.',
    'roster.empty': 'This channel has no members yet.',
    'roster.roleFallback': 'member',
    // Capability summary (#273): two read-only numbers from the two agent-scoped lists. A failed
    // read only leaves a placeholder on that row; it never blocks the rest of the roster and it
    // never reports "could not read" as 0.
    'roster.capabilities': 'MCP {mcp} / Skills {skills}',
    'roster.capabilitiesLoading': 'MCP … / Skills …',
    'roster.capabilitiesUnavailable': 'Capability summary unavailable',
    'roster.closeAria': 'Close the member roster',

    /* ─── Channel settings: response budget (#54 / ADR-0007 / spec #31 §2) ────
       The entry lives in the channel header. The popup keeps the **effective bound of the
       current human epoch** and the **next-epoch pending value** apart, and offers only the
       fixed tiers 3 / 5 / 10. Vocabulary follows the `CONTEXT.md` `## Language` entries:
       human epoch / response budget / jump / budget-exhausted / budget table. */
    'budget.entryTitle': 'Response budget',
    'budget.title': 'Response budget',
    'budget.sub': '#{name} · You set the upper bound; used jumps stay backend-owned',
    'budget.closeAria': 'Close the response-budget settings',
    'budget.loading': 'Reading this channel’s budget…',
    'budget.loadFailed':
      'This channel’s budget is not readable right now (the backend request failed): {reason}',
    'budget.sectionCurrent': 'Current epoch',
    'budget.used': 'Used',
    'budget.remaining': 'Remaining',
    'budget.limit': 'Effective bound',
    'budget.source': 'Source',
    'budget.source.channel-override': 'Channel override',
    'budget.source.built-in-default': 'Built-in default',
    'budget.state': 'State',
    'budget.exhausted': 'Budget exhausted',
    'budget.exhaustedNote':
      'Zero deliveries in this epoch: Agent messages are still stored, nobody is woken (this is not an error).',
    'budget.sectionNext': 'Next human epoch',
    'budget.pending': 'Saved bound',
    'budget.pendingEffective':
      'This saved tier takes effect at the next human epoch; the current epoch keeps the effective bound above.',
    'budget.pendingSame': 'Same as the current bound; nothing is pending.',
    'budget.tierLabel': 'Upper-bound tier',
    'budget.tierUnit': 'jumps',
    'budget.tierDefaultUnit': 'jumps (default)',
    'budget.tierHint':
      'Only these three tiers; the backend rejects anything else outright and saves nothing.',
    'budget.save': 'Save upper bound',
    'budget.saved': 'Saved the {limit}-jump upper bound; it applies from the next human epoch.',
    'budget.saveFailed': 'The upper bound was not saved: {reason}',

    /* ─── Channel settings: topology / leader / project root / backend / permission (#154) ──
       The first surface in spec §4.6. Vocabulary follows `CONTEXT.md` and ADR-0018 /
       ADR-0021: autonomous / team / leader / project root / backend / permission mode.
       `team_scope` is a **read-only backend fact**; the copy only explains it. */
    'channelConfig.entryTitle': 'Channel settings',
    'channelConfig.title': 'Channel settings',
    'channelConfig.sub': '#{name} · Collaboration mode, leader, and workspace defaults',
    'channelConfig.closeAria': 'Close channel settings',
    'channelConfig.loading': 'Reading this channel’s configuration…',
    'channelConfig.loadFailed':
      'This channel’s configuration is not readable right now (the backend request failed): {reason}',
    'channelConfig.sectionMode': 'Collaboration mode',
    'channelConfig.mode.autonomous': 'Autonomous',
    'channelConfig.mode.autonomousNote': 'Each member decides whether to reply',
    'channelConfig.mode.team': 'Team',
    'channelConfig.mode.teamNote': 'The leader collects replies; member output stays in run records',
    'channelConfig.modeBadge.title': 'Collaboration mode: {mode}',
    'channelConfig.modeBadge.teamWithLeader': 'Team · {leader}',
    'channelConfig.sectionScope': 'Scope',
    'channelConfig.teamScope': 'team_scope',
    'channelConfig.teamScopeNote': 'Team covers this channel and all of its task threads (channel+task).',
    'channelConfig.sectionLeader': 'Team leader',
    'channelConfig.leaderSelectLabel': 'Leader member',
    'channelConfig.leaderAvailability': 'Leader availability',
    'channelConfig.leaderNone': 'No leader selected',
    'channelConfig.leaderAvailable': 'Leader available',
    'channelConfig.leaderUnavailable': 'Leader unavailable',
    'channelConfig.leaderReason': 'Reason: {reason}',
    'channelConfig.leaderRequiredHint':
      'Team mode requires an Agent member of the current channel as the leader.',
    'channelConfig.membersUnavailable':
      'The channel member list is not readable right now; leader candidates may be incomplete.',
    'channelConfig.teamNoticeAutoClaim': 'Member auto_claim is disabled',
    'channelConfig.teamNoticeWake': 'Task threads only wake the team leader',
    'channelConfig.sectionWorkspace': 'Project root and default backend',
    'channelConfig.workspaceRootLabel': 'Project root path',
    'channelConfig.workspaceRootPlaceholder': 'For example /Users/you/project',
    'channelConfig.workspaceRootHint':
      'Must be an absolute path; leave it empty for “not configured” (the default behavior).',
    'channelConfig.workspaceBackend': 'Default backend',
    'channelConfig.backend.local': 'Local',
    'channelConfig.backend.localNote': 'Restricted file tools; Bash off by default',
    'channelConfig.backend.docker': 'Docker',
    'channelConfig.backend.dockerNote': 'One container per Agent or session',
    'channelConfig.sectionPermission': 'Default permission mode',
    'channelConfig.permission.read-only': 'Read only',
    'channelConfig.permission.read-onlyNote': 'Only read-only tools and commands',
    'channelConfig.permission.ask-writes': 'Ask before writes',
    'channelConfig.permission.ask-writesNote': 'Reads are allowed; writes and execution ask each time',
    'channelConfig.permission.trusted-writes': 'Write inside workdir',
    'channelConfig.permission.trusted-writesNote': 'Writes outside the workdir still ask',
    'channelConfig.save': 'Save configuration',
    'channelConfig.saved': 'Channel configuration saved.',
    'channelConfig.saveFailed': 'Configuration not saved: {reason}',

    /* ─── Web access (#199): three levels + provider credentials (masked reads only) ── */
    'channelConfig.sectionWeb': 'Web access',
    'channelConfig.webAccess.off': 'Off',
    'channelConfig.webAccess.offNote': 'Mount no web tool',
    'channelConfig.webAccess.search': 'Search only',
    'channelConfig.webAccess.searchNote': 'Mount web_search; queries leave this machine',
    'channelConfig.webAccess.search+fetch': 'Search + fetch',
    'channelConfig.webAccess.search+fetchNote': 'Also mount fetch; it no longer asks every time — narrow it per host with deny / ask rules',
    'channelConfig.webAccessHint': 'Unset means "search only"; only "off" removes the web tools.',
    'webProviders.loadFailed': 'Could not read web provider status: {reason}',
    'webProviders.active': 'Active provider',
    'webProviders.activeValue': '{provider} (source: {source})',
    'webProviders.none': 'Undetermined',
    'webProviders.source.env': 'environment variable',
    'webProviders.source.ui': 'UI configuration',
    'webProviders.source.fallback': 'keyless fallback',
    'webProviders.source.invalid': 'invalid environment value',
    'webProviders.provider.brave': 'Brave',
    'webProviders.provider.tavily': 'Tavily',
    'webProviders.provider.perplexity': 'Perplexity',
    'webProviders.provider.jina': 'Jina',
    'webProviders.provider.exa': 'Exa',
    'webProviders.provider.fallback': 'Exa MCP (keyless)',
    'webProviders.configuredWithMask': 'Configured · {masked}',
    'webProviders.configuredFromEnv': 'Provided by an environment variable',
    'webProviders.notConfigured': 'Not configured',
    'webProviders.defaultBadge': 'Default',
    'webProviders.keyLabel': '{provider} API key',
    'webProviders.keyPlaceholder': 'Paste this provider’s API key',
    'webProviders.dataNotice': 'After saving, queries are sent to this service.',
    'webProviders.save': 'Save credential',
    'webProviders.makeDefault': 'Make default',
    'webProviders.clear': 'Clear',
    'webProviders.keyRequired': 'Enter a non-empty key first.',
    'webProviders.saved': 'Credential saved for {provider}.',
    'webProviders.defaultSet': 'Default provider switched to {provider}.',
    'webProviders.cleared': 'Credential cleared for {provider}.',
    'webProviders.saveFailed': 'Credential not saved: {reason}',
    'webProviders.defaultFailed': 'Default provider not switched: {reason}',
    'webProviders.clearFailed': 'Credential not cleared: {reason}',

    /* ─── Resource management (#156 / spec §4.6): workspace status / MCP / Skill ──
       MCP shows **variable names only** (`mcp.envNames*`); there is no catalog entry
       and no input for values. */
    'workspaceMgmt.section': 'Workspace and data plane',
    'workspaceMgmt.root': 'Persisted project root',
    'workspaceMgmt.rootNone': 'Not configured (default: no file tools)',
    'workspaceMgmt.backend': 'Persisted backend',
    'workspaceMgmt.dockerStatus': 'Docker status',
    'workspaceMgmt.dockerUnavailableAgent': 'Docker unavailable ({name}): {reason}',
    'workspaceMgmt.dockerReported':
      'No failure reported in the last round (the backend records a reason only after a container fails to start)',
    'workspaceMgmt.dockerUnused': 'The current backend is local; no container is started',
    'workspaceMgmt.noteTools':
      'Only after a project root is set does an Agent get the restricted file tools (Read / Write / Edit / Glob / Grep); Bash stays off by default.',
    'workspaceMgmt.noteLocal':
      'Local backend: tools act on the host project root, and every path must stay inside it.',
    'workspaceMgmt.noteDocker':
      'Docker backend: one container per Agent or session; it only isolates container from host, and a remote image needs the data-flow acknowledgement first.',
    'workspaceMgmt.noteConfig':
      'Edit the project root and backend in “Project root and default backend” above; an illegal path is rejected as a whole and the save line shows the readable reason.',
    'mcp.scopeNote':
      'Variable names only; values come from the server process environment, and neither the UI nor API responses store them.',
    // Source label of the top block (#284 / D7): it is the assembly authority, a different dataset
    // from the home-directory file in the bottom block.
    'mcp.sourceDb':
      'From the database rows in mcp_server (scope_kind=agent): the assembly authority; editing here changes what gets assembled.',
    'mcp.loading': 'Reading MCP declarations…',
    'mcp.loadFailed': 'Could not read the MCP declarations: {reason}',
    'mcp.empty': 'This agent has no MCP declarations yet.',
    'mcp.name': 'Name',
    'mcp.namePlaceholder': 'For example filesystem',
    'mcp.transport': 'Transport',
    'mcp.transport.stdio': 'stdio (local process)',
    'mcp.transport.http': 'http (remote)',
    'mcp.transport.sse': 'sse (remote)',
    'mcp.command': 'Command',
    'mcp.commandPlaceholder': 'For example npx',
    'mcp.args': 'Arguments',
    'mcp.argsPlaceholder':
      'Whitespace separated, for example -y @modelcontextprotocol/server-filesystem /tmp',
    'mcp.url': 'URL',
    'mcp.urlPlaceholder': 'For example https://example.com/mcp',
    'mcp.envNames': 'Environment variable names',
    'mcp.envNamesPlaceholder': 'Comma separated, for example GITHUB_TOKEN, LOG_LEVEL',
    'mcp.envNone': 'No environment variables',
    'mcp.envLabel': 'Environment variable names: {names}',
    'mcp.envValuesOmitted': 'Names only; values are neither shown nor stored.',
    'mcp.enabledLabel': 'Enabled',
    'mcp.enabled': 'Enabled',
    'mcp.disabled': 'Disabled',
    'mcp.enable': 'Enable',
    'mcp.disable': 'Disable',
    'mcp.edit': 'Edit',
    'mcp.delete': 'Delete',
    'mcp.consent': 'Acknowledge data plane',
    'mcp.consentRequired': 'Remote MCP not acknowledged: no connection is made before you acknowledge.',
    'mcp.consented': 'Data-plane acknowledgement: acknowledged',
    'mcp.consentedAt': 'Data-plane acknowledgement: acknowledged at {time}',
    'mcp.consentDone': 'Recorded the MCP data-plane acknowledgement.',
    'mcp.consentFailed': 'Acknowledgement failed: {reason}',
    'mcp.add': 'Add MCP',
    'mcp.save': 'Save changes',
    'mcp.cancel': 'Cancel editing',
    'mcp.addTitle': 'New MCP declaration',
    'mcp.editTitle': 'Edit MCP declaration',
    'mcp.created': 'MCP declaration added.',
    'mcp.updated': 'MCP declaration saved.',
    'mcp.deleted': 'MCP declaration deleted.',
    'mcp.saveFailed': 'Not saved: {reason}',
    'mcp.deleteFailed': 'Not deleted: {reason}',
    'mcp.runtimeUnknown': 'Runtime reading: not assembled yet (readings reset when the process restarts)',
    'mcp.runtimeConnected': 'Runtime reading: connected · {count} tool(s)',
    'mcp.runtimeFailed': 'Runtime reading: connection failed: {reason}',
    'mcp.isRemote': 'Remote',
    'mcp.isLocal': 'Local',
    'skill.scopeNote':
      'Installed into the member home; a skill reaches this agent’s toolkit only while enabled.',
    'skill.loading': 'Reading the skill list…',
    'skill.loadFailed': 'Could not read the skill list: {reason}',
    'skill.empty': 'This agent has no installed skills yet.',
    'skill.hashLabel': 'hash',
    'skill.hashTitle': 'Full content hash: {hash}',
    'skill.enabled': 'Enabled',
    'skill.disabled': 'Disabled',
    'skill.toggleLabel': 'Enable or disable {name}',
    'skill.delete': 'Delete',
    'skill.installed': 'Skill installed: {name}',
    'skill.installFailed': 'Install failed: {reason}',
    'skill.toggleFailed': 'Could not toggle the skill: {reason}',
    'skill.deleted': 'Skill deleted.',
    'skill.deleteFailed': 'Not deleted: {reason}',
    'skill.importTitle': 'Import from this machine',
    'skill.importCommandHint':
      'Run the command above in a local terminal first: -g installs globally, -a codex targets ~/.agents/skills (the global skill directory shared by codex / cline / pi). The app only reads that directory and never runs it for you; once it is installed, pick it in the dropdown below.',
    'skill.importScanButton': 'Refresh candidates',
    'skill.importPickLabel': 'Import from this machine',
    'skill.importPickPlaceholder': 'Pick one skill to import…',
    'skill.importGroupReady': 'Available',
    'skill.importGroupInstalled': 'Already installed',
    'skill.importGroupBlocked': 'Cannot import',
    'skill.importLoading': 'Scanning ~/.agents/skills…',
    'skill.importScanFailed': 'Could not scan the candidates: {reason}',
    'skill.importUnavailable':
      'No {source} found: run the command above to install a skill, then refresh the candidates.',
    'skill.importEmpty': 'That directory holds no skills yet: run the command above, then refresh the candidates.',
    'skill.importStatusInstalled': 'Already installed',
    'skill.importStatusBlocked': 'Cannot import: {reason}',
    'skill.importBusy': 'Importing…',
    'skill.importResultDone': '{name}: imported',
    'skill.importResultSkipped': '{name}: already installed, skipped',
    'skill.importResultFailed': '{name}: import failed: {reason}',
    /* ── Skills tab, right pane (#283 / D5) ──
       Five metadata cells + two actions + the SKILL.md body as plain text. Each of the three
       unreadable states gets its own sentence: the user needs to know what to go and fix. */
    'skill.detailTitle': 'Skill detail',
    'skill.detailName': 'Name',
    'skill.detailDescription': 'Description',
    'skill.detailSource': 'Source path',
    'skill.detailHash': 'Content hash',
    'skill.detailState': 'Enablement',
    'skill.detailEmpty': 'This agent has no skills yet: import one above, then come back to read its details.',
    'skill.detailBodyTitle': 'SKILL.md body',
    'skill.detailBodyLoading': 'Reading SKILL.md…',
    'skill.detailBodyFailed': 'Could not read SKILL.md: {reason}',
    'skill.detailMissing':
      'This skill’s bytes are gone from the member home (skill directory or SKILL.md missing): the metadata is still here, the body is not.',
    'skill.detailInvalid':
      'The SKILL.md path escapes the global root (symlink pointing outside): the body is withheld for safety.',
    'skill.detailUnreadable':
      'SKILL.md cannot be read (permissions or non-UTF-8): the metadata is still here, the body is not.',

    /* ─── Workspace settings (this ticket: locale + message density only) ── */
    'settings.title': 'Settings',
    'settings.sub': 'Workspace preferences',
    'settings.closeAria': 'Close settings',
    'settings.languageLabel': 'Language',
    'settings.languageZh': 'Chinese (Simplified)',
    'settings.languageEn': 'English',
    'settings.densityLabel': 'Message density',
    'settings.densityComfortable': 'Comfortable',
    'settings.densityComfortableHint': 'Default',
    'settings.densityCompact': 'Compact',
    'settings.densityCompactHint': 'More messages',
    'settings.themeLabel': 'Theme',
    'settings.themeLight': 'Light',
    'settings.themeLightHint': 'Always light',
    'settings.themeDark': 'Dark',
    'settings.themeDarkHint': 'Always dark',
    'settings.themeSystem': 'Follow system',
    'settings.themeSystemHint': 'Match the system appearance',
    'settings.saveFailed': 'Settings were not saved: {reason}',
    'settings.done': 'Done',

    /* ─── Toasts ───────────────────────────────────────────────────── */
    'toast.taskCreated': 'Created task “{title}”.',
    'toast.titleUpdateFailed': 'Could not update the title: {reason}',
    'toast.statusUpdateFailed': 'Could not update the status: {reason}',
    'toast.transitionInvalid': 'Cannot move from “{from}” to “{to}” — not a legal transition',
    'toast.assigneeUpdateFailed': 'Could not update the assignee: {reason}',
    'toast.taskMessageFailed': 'Task message not sent: {reason}',
    'toast.openTaskFailed': 'Could not open the task: {reason}',
    'toast.taskReadFailed': 'Could not mark the task read: {reason}',
    'toast.readFailed': 'Could not mark as read: {reason}',
    'toast.sendBlockedOffline':
      'Backend unreachable: sending is disabled and the message was not sent.',
    'toast.sendMessageLost':
      'Backend unreachable: this message was not delivered and is not queued locally.',
    'toast.sendRejected': 'Send rejected: {reason}',
    'toast.attachFailed': 'Attachment upload failed: {reason}',
    'toast.attachPending':
      '{count} attachment(s) are still uploading — wait for them before sending, or this message will miss them.',
    'toast.sessionLoadFailed': 'Could not read the conversation: {reason}',
    'toast.memberFallback': 'member',
    'toast.joinedChannel': 'Added {name} to #{channel}; you can now @-mention it from the composer.',
    'toast.alreadyMember': '{name} is already in #{channel}.',
    'toast.joinFailed': 'Could not join: {reason}',
    'toast.channelCreated': 'Created channel #{name}; you can send the first message right away.',
    'toast.dmCreated': 'Created a direct message with {name}.',
    'toast.dmOpened': 'Opened the existing direct message with {name}.',
    'toast.dmOpenFailed': 'Could not open the direct message: {reason}',
    'toast.agentCreated':
      'Created agent {name}; press “Join” in the “Not in this channel” group of the “Channel members” panel to add it here.',
    'toast.remoteAck': 'Recorded the data-flow acknowledgement for {provider}.',
    'toast.remoteAckFailed': 'Acknowledgement failed: {reason}',
    'toast.agentUpdated': 'Saved the configuration of {name}.',
    'toast.agentDeleted': 'Deleted the agent “{name}”.',
    'toast.agentDeleteFailed': 'Could not delete the agent: {reason}',
    'toast.agentDetailFailed': 'Could not read the agent details: {reason}',
    'toast.globalMcpConsentFailed': 'Could not acknowledge the global MCP entry: {reason}',
  },
};

/** 当前**语言偏好**（内存态）——它是「工作台当前的语言」也是「后端产出文案的语言」这**同一个**
    取值；**正本**是后端的工作区设置，启动时读回、切换时写回。 */
let i18nLocale = I18N_SOURCE_LOCALE;

function i18nRaw(locale, key) {
  const catalog = I18N_CATALOGS[locale];
  if (!catalog) return null;
  return Object.prototype.hasOwnProperty.call(catalog, key) ? catalog[key] : null;
}

/** 某个键在**当前语言**里存不存在（用于「有词条就用词条、没有就回退到原值」的分支）。 */
function hasKey(key) {
  return i18nRaw(i18nLocale, key) !== null;
}

/** 取词条：当前语言 → 源语言 → 键名本身（缺词条时不静默成空串，好让判据看得见）。 */
function t(key, params) {
  const value = i18nRaw(i18nLocale, key) ?? i18nRaw(I18N_SOURCE_LOCALE, key) ?? key;
  if (!params) return value;
  return value.replace(/\{(\w+)\}/g, (whole, name) =>
    params[name] === undefined || params[name] === null ? whole : String(params[name]),
  );
}

function currentLocale() {
  return i18nLocale;
}

/** 切语言：未知语言回退源语言；`<html lang>` 与语言**同一个取值**（浏览器与辅助技术读它）。 */
function setLocale(locale) {
  i18nLocale = I18N_LOCALES.includes(locale) ? locale : I18N_SOURCE_LOCALE;
  if (typeof document !== 'undefined' && document.documentElement) {
    document.documentElement.lang = i18nLocale;
  }
  return i18nLocale;
}

/**
 * 把 `index.html` 里的**静态**文案按词条铺一遍。
 *
 * - `data-i18n="key"` → 该节点的 `textContent`；
 * - `data-i18n-attr="attr:key"`（多个用 `,` 分隔）→ 该节点的属性（`aria-label` / `title` /
 *   `placeholder` / `alt` 这类可本地化属性）。
 *
 * 它只负责静态节点；由 `app.js` 渲染出来的动态文案走 `t()`，两者不重不漏。
 */
function applyStaticI18n(root) {
  const scope = root || (typeof document !== 'undefined' ? document : null);
  if (!scope || typeof scope.querySelectorAll !== 'function') return;
  Array.prototype.forEach.call(scope.querySelectorAll('[data-i18n]'), (node) => {
    node.textContent = t(node.getAttribute('data-i18n'));
  });
  Array.prototype.forEach.call(scope.querySelectorAll('[data-i18n-attr]'), (node) => {
    String(node.getAttribute('data-i18n-attr'))
      .split(',')
      .forEach((pair) => {
        const [attribute, key] = pair.split(':');
        if (attribute && key) node.setAttribute(attribute.trim(), t(key.trim()));
      });
  });
}

/* 用 `var` 而不是 `const`：classic script 的顶层 `var` 会成为全局对象的属性，于是
   （a）浏览器里是 `window.AlibotI18n`，（b）`node --test` 的 `vm` 装载里也能读到同一份表——
   两条接缝读的是**同一个**对象，不各拿一份副本。 */
var AlibotI18n = {
  source: I18N_SOURCE_LOCALE,
  locales: I18N_LOCALES,
  catalogs: I18N_CATALOGS,
  t,
  hasKey,
  setLocale,
  currentLocale,
  applyStaticI18n,
};

if (typeof window !== 'undefined') window.AlibotI18n = AlibotI18n;
