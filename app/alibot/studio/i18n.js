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

   本票（#61）只落**语言偏好**与**消息密度**两档设置：**主题**是第三档、由 #71 追加，
   因此这份文件里既没有主题的键，也没有主题的档位文案。
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
    'identity.human': '人类 · 本机',
    'identity.me': '我',

    /* ─── 左栏 ─────────────────────────────────────────────────────── */
    'rail.nav': '工作区导航',
    'rail.newChannel': '新建频道',
    'rail.newDm': '私信',
    'rail.newDmTitle': '新建私信',
    'rail.search': '搜索',
    'rail.searchTitle': '搜索工作台',
    'rail.newAgent': 'Agent',
    'rail.newAgentTitle': '新建 Agent',
    'rail.unread': '工作区未读',
    'rail.unreadCount': '工作区未读 {count} 条',
    'rail.groupChannels': '频道',
    'rail.groupDms': '私信',
    'rail.groupAgents': '本机 Agent',
    'rail.emptyChannels': '还没有频道。新建一个频道就能开始记录。',
    'rail.emptyDms': '还没有私信。新建私信后会在这里显示。',
    'rail.emptyAgents': '还没有 Agent。',
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

    /* ─── 轮次结果的三种「正常选择」（`failed` 单独成行，`replied` 由回复承载） ── */
    'outcome.ignored.label': '自判忽略',
    'outcome.ignored.note': '自判忽略（正常的协议选择，不是失败）',
    'outcome.budget-exhausted.label': '预算耗尽',
    'outcome.budget-exhausted.note': '预算已用尽，本轮零投递（不是出错，成员状态点仍空闲）',
    'outcome.merged.label': '被合并',
    'outcome.merged.note': '重复唤醒被合并进正在进行的那一轮（不是失败）',
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
    'empty.noChannelTitle': '还没有频道',
    'empty.noChannelBody': '新建一个频道，就能发出第一条永久记录。',
    'empty.noMessageTitle': '该频道还没有消息',
    'empty.noMessageBody': '这里的消息不可编辑、不可删除，是永久记录。',
    'header.dmDesc': '与 {name} 的两方私信',
    'header.dmPlaceholder': '发消息给 @{name} —— 可手动 @ 送达其他 Agent',
    'header.channelPlaceholder': '发消息到 #{name} —— 输入 @ 提及 Agent',
    'typing.working': '{name} 正在回复…',

    /* ─── composer 与 @ 补全 ────────────────────────────────────────── */
    'composer.mentionLabel': '可提及的当前频道 Agent 成员',
    'composer.placeholder': '发消息到 #频道名',
    'composer.inputLabel': '消息正文',
    'composer.mentionAction': '提及 Agent',
    'composer.asTaskAction': '作为任务发送',
    'composer.hint': 'Enter 发送 · Shift+Enter 换行',
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
    'board.unreadAria': '{count} 条未读任务消息',
    'task.selectChannel': '选择所属频道',
    'task.createChannelFirst': '请先创建频道',
    'task.threadTag': '任务线程',
    'task.threadHeadSub': '#{channel} · 关联消息 {count} 条',
    'task.threadUnread': ' · {count} 条未读',
    'task.noTransitions': '已关闭，无可用推进',
    'task.linkedEmpty': '该任务还没有关联消息',
    'task.discussionCount': '讨论 · {count} 条',
    'task.repliesEmpty': '还没有讨论，来写第一条回复',
    'task.historyEmpty': '暂无变更记录',
    'task.unassigned': '未认领',
    'task.unassignedPill': '未认领 · 点击指派',
    'task.reassignHint': '· 改派',
    'task.assignUnavailable': '暂时读不到该频道的成员表：指派候选不可用。',
    'task.assignLoading': '正在读取该频道的成员…',
    'task.assignNoAgents': '该频道还没有 Agent 成员：先在右侧「频道成员」面板把 Agent 加入这个频道。',
    'task.clearAssign': '取消指派',
    'dock.move': '推进',
    'dock.assign': '指派',
    'dock.linked': '关联消息',
    'dock.history': '状态变更记录',
    'dock.label': '详情面板',
    'dock.toggle': '切换详情面板',
    'dock.membersTitle': '频道成员',
    'dock.membersRole':
      '本频道已加入的 Agent 与未加入者可加入的 Agent；状态点由后端事实推导：空闲 / 正在回复 / 出错 / 未启用',
    'dock.threadClose': '关闭任务线程',
    'dock.replyPlaceholder': '回复该任务线程…',
    'dock.replyInputLabel': '任务消息正文',
    'dock.replyHint': 'Enter 回复 · Shift+Enter 换行',
    'dock.replySend': '回复',
    'dock.dmNoJoin': '私信固定只有 owner 与对方 Agent，不提供加入或离开操作。',
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
    'dm.modalTitle': '新建私信',
    'dm.modalSub': '选择一个 Agent；每个 Agent 只有一条私信，重复创建会复用。',
    'dm.peerLabel': '私信对象',
    'dm.hint': '私信始终只有 owner 与所选 Agent；不能加入或离开。',
    'dm.create': '创建私信',
    'dm.noAgents': '还没有 Agent：先用左上角「Agent」按钮创建一个。',
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
    'agent.remoteConfirm': '我已了解，确认发送',
    'agent.create': '创建 Agent',
    'agent.providerLocalHint': '本机模型：不离开本机，不需要数据流确认。',
    'agent.providerRemoteHint': '远端模型：启用前需要先确认数据会离开本机（见下方告知）。',

    /* ─── 提醒（ADR-0012：库内对象） ─────────────────────────────────
       两句话都只说**这条交付里真成立的事**：提醒存在后端、跨刷新与重启仍在。
       **不**写「到点会发一条 ⏰ 系统消息」——到期触发的唤醒不在本票授权范围内
       （规格未授权、原型也没有实现），写上去就是界面在声称一个不存在的行为。 */
    'reminder.modalTitle': '提醒',
    'reminder.modalSub': '提醒保存在本机后端，刷新或重启后仍在。',
    'reminder.closeAria': '关闭提醒',
    'reminder.emptyTitle': '还没有提醒',
    'reminder.emptyBody': '提醒保存在后端，不依赖这台浏览器。',
    'reminder.newLabel': '新提醒',
    'reminder.textLabel': '提醒内容',
    'reminder.timeLabel': '提醒时间',
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
    'roster.closeAria': '关闭成员名册',

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
    'settings.saveFailed': '设置未保存：{reason}',
    'settings.done': '完成',

    /* ─── 提示（toast） ─────────────────────────────────────────────── */
    'toast.taskCreated': '已创建任务「{title}」。',
    'toast.titleUpdateFailed': '更新标题失败：{reason}',
    'toast.statusUpdateFailed': '更新状态失败：{reason}',
    'toast.assigneeUpdateFailed': '更新指派失败：{reason}',
    'toast.taskMessageFailed': '任务消息未发送：{reason}',
    'toast.openTaskFailed': '打开任务失败：{reason}',
    'toast.taskReadFailed': '任务标读失败：{reason}',
    'toast.readFailed': '标读失败：{reason}',
    'toast.sendBlockedOffline': '后端不可达，发送已禁止；消息没有被送出。',
    'toast.sendMessageLost': '后端不可达：这条消息没有送达，也没有留在本地队列里。',
    'toast.sendRejected': '发送被拒绝：{reason}',
    'toast.sessionLoadFailed': '读取会话失败：{reason}',
    'toast.memberFallback': '成员',
    'toast.joinedChannel': '已把 {name} 加入 #{channel}，现在可以在 composer 里用 @ 提到它。',
    'toast.alreadyMember': '{name} 本来就在 #{channel} 里。',
    'toast.joinFailed': '加入失败：{reason}',
    'toast.channelCreated': '已创建频道 #{name}，可以直接发送第一条消息。',
    'toast.dmCreated': '已创建与 {name} 的私信。',
    'toast.dmOpened': '已打开与 {name} 的现有私信。',
    'toast.agentCreated':
      '已创建 Agent {name}，在右侧「频道成员」面板的「未加入」一段点「加入」即可让它进入当前频道。',
    'toast.remoteAck': '已记录数据流告知确认：{provider}。',
    'toast.remoteAckFailed': '确认失败：{reason}',
  },

  en: {
    /* ─── App shell ────────────────────────────────────────────────── */
    'app.title': 'alibot · collaboration workspace',
    'app.brandSub': 'LOCAL · this machine',
    'identity.human': 'Human · this machine',
    'identity.me': 'Me',

    /* ─── Left rail ────────────────────────────────────────────────── */
    'rail.nav': 'Workspace navigation',
    'rail.newChannel': 'New channel',
    'rail.newDm': 'Direct message',
    'rail.newDmTitle': 'New direct message',
    'rail.search': 'Search',
    'rail.searchTitle': 'Search the workspace',
    'rail.newAgent': 'Agent',
    'rail.newAgentTitle': 'New agent',
    'rail.unread': 'Workspace unread',
    'rail.unreadCount': '{count} unread in the workspace',
    'rail.groupChannels': 'Channels',
    'rail.groupDms': 'Direct messages',
    'rail.groupAgents': 'Local agents',
    'rail.emptyChannels': 'No channels yet. Create one to start recording.',
    'rail.emptyDms': 'No direct messages yet. New ones show up here.',
    'rail.emptyAgents': 'No agents yet.',
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

    /* ─── The three normal round outcomes ──────────────────────────── */
    'outcome.ignored.label': 'Ignored by choice',
    'outcome.ignored.note': 'Ignored by choice (a normal protocol decision, not a failure)',
    'outcome.budget-exhausted.label': 'Budget exhausted',
    'outcome.budget-exhausted.note':
      'Budget used up, nothing delivered this round (not an error; the member is still idle)',
    'outcome.merged.label': 'Merged',
    'outcome.merged.note':
      'A duplicate wake-up was merged into the round already running (not a failure)',
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
    'empty.noChannelTitle': 'No channels yet',
    'empty.noChannelBody': 'Create a channel to write the first permanent record.',
    'empty.noMessageTitle': 'No messages in this channel yet',
    'empty.noMessageBody':
      'Messages here cannot be edited or deleted — they are permanent records.',
    'header.dmDesc': 'Two-way direct message with {name}',
    'header.dmPlaceholder': 'Message @{name} — you can still @ someone to reach them',
    'header.channelPlaceholder': 'Message #{name} — type @ to mention an agent',
    'typing.working': '{name} is replying…',

    /* ─── Composer and @ completion ────────────────────────────────── */
    'composer.mentionLabel': 'Agent members of this channel that can be mentioned',
    'composer.placeholder': 'Message #channel-name',
    'composer.inputLabel': 'Message body',
    'composer.mentionAction': 'Mention an agent',
    'composer.asTaskAction': 'Send as task',
    'composer.hint': 'Enter to send · Shift+Enter for a new line',
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
    'board.unreadAria': '{count} unread task messages',
    'task.selectChannel': 'Choose a channel',
    'task.createChannelFirst': 'Create a channel first',
    'task.threadTag': 'Task thread',
    'task.threadHeadSub': '#{channel} · {count} linked messages',
    'task.threadUnread': ' · {count} unread',
    'task.noTransitions': 'Closed — no transitions available',
    'task.linkedEmpty': 'No linked messages for this task yet',
    'task.discussionCount': 'Discussion · {count}',
    'task.repliesEmpty': 'No discussion yet — write the first reply',
    'task.historyEmpty': 'No change history yet',
    'task.unassigned': 'Unclaimed',
    'task.unassignedPill': 'Unclaimed · click to assign',
    'task.reassignHint': '· reassign',
    'task.assignUnavailable': 'The channel member list is not readable right now: no assignee candidates.',
    'task.assignLoading': 'Reading the channel members…',
    'task.assignNoAgents':
      'This channel has no agent members yet: add one from the “Channel members” panel on the right.',
    'task.clearAssign': 'Clear assignee',
    'dock.move': 'Move',
    'dock.assign': 'Assign',
    'dock.linked': 'Linked messages',
    'dock.history': 'Change history',
    'dock.label': 'Detail panel',
    'dock.toggle': 'Toggle detail panel',
    'dock.membersTitle': 'Channel members',
    'dock.membersRole':
      'Agents already in this channel and agents that can still be added; the status dot is derived from backend facts: idle / replying / error / disabled',
    'dock.threadClose': 'Close the task thread',
    'dock.replyPlaceholder': 'Reply to this task thread…',
    'dock.replyInputLabel': 'Task message body',
    'dock.replyHint': 'Enter to reply · Shift+Enter for a new line',
    'dock.replySend': 'Reply',
    'dock.dmNoJoin':
      'A direct message always holds exactly the owner and the peer agent; there is no join or leave action.',
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
    'dm.modalTitle': 'New direct message',
    'dm.modalSub':
      'Pick one agent; each agent has a single direct message and creating it again reuses it.',
    'dm.peerLabel': 'Direct message peer',
    'dm.hint': 'A direct message always holds only the owner and the chosen agent; nobody can join or leave.',
    'dm.create': 'Create direct message',
    'dm.noAgents': 'No agents yet: create one with the “Agent” button at the top left.',
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
    'agent.remoteConfirm': 'I understand — confirm sending',
    'agent.create': 'Create agent',
    'agent.providerLocalHint': 'Local model: it never leaves this machine and needs no data-flow consent.',
    'agent.providerRemoteHint':
      'Remote model: enabling it first requires confirming that data leaves this machine (see the notice below).',

    /* ─── Reminders (ADR-0012: backend objects) ──────────────────────
       Both sentences claim only what this delivery actually does: reminders live in the
       backend and survive a refresh or a restart. No "a ⏰ system message will appear when
       it is due" — due-time wake-up is outside this ticket, so that copy would be a lie. */
    'reminder.modalTitle': 'Reminders',
    'reminder.modalSub':
      'Reminders are stored in the local backend; they survive a refresh or a restart.',
    'reminder.closeAria': 'Close reminders',
    'reminder.emptyTitle': 'No reminders yet',
    'reminder.emptyBody': 'Reminders live in the backend, not in this browser.',
    'reminder.newLabel': 'New reminder',
    'reminder.textLabel': 'Reminder text',
    'reminder.timeLabel': 'Reminder time',
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
    'roster.closeAria': 'Close the member roster',

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
    'settings.saveFailed': 'Settings were not saved: {reason}',
    'settings.done': 'Done',

    /* ─── Toasts ───────────────────────────────────────────────────── */
    'toast.taskCreated': 'Created task “{title}”.',
    'toast.titleUpdateFailed': 'Could not update the title: {reason}',
    'toast.statusUpdateFailed': 'Could not update the status: {reason}',
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
    'toast.sessionLoadFailed': 'Could not read the conversation: {reason}',
    'toast.memberFallback': 'member',
    'toast.joinedChannel': 'Added {name} to #{channel}; you can now @-mention it from the composer.',
    'toast.alreadyMember': '{name} is already in #{channel}.',
    'toast.joinFailed': 'Could not join: {reason}',
    'toast.channelCreated': 'Created channel #{name}; you can send the first message right away.',
    'toast.dmCreated': 'Created a direct message with {name}.',
    'toast.dmOpened': 'Opened the existing direct message with {name}.',
    'toast.agentCreated':
      'Created agent {name}; press “Join” in the “Not in this channel” group of the “Channel members” panel to add it here.',
    'toast.remoteAck': 'Recorded the data-flow acknowledgement for {provider}.',
    'toast.remoteAckFailed': 'Acknowledgement failed: {reason}',
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
