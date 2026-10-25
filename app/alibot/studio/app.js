/* ============================================================================
   alibot 工作台前端
   业务状态的唯一正本是后端 HTTP 面 + 事件流：频道、消息、已读位置、Agent 与状态点都只从后端读。
   本文件不使用 localStorage 保存任何业务状态（唯一允许的本地记忆是「上次打开的频道 id」）。

   本票新增：
   - SSE 订阅：按 seq 追加渲染，Agent 的完整回复不刷新即出现；断线显示连接状态，重连带
     `last_seq` 补拉（补拉由后端读库完成，前端只负责带上游标与去重）。
   - 提及高亮：消费**消息落库时的提及快照**（不自己再实现一遍解析）。
   - 四态状态点：`status_of` 由后端事实推导，前端只渲染 `online/working/error/offline`。
   - 新建 Agent 与远端数据流告知的显式确认。
   - `@` 补全：composer 里输入 `@` 弹出**当前频道的 Agent 成员**清单；前端不解析提及，只按
     `mentions.py` 的引号规则拼出要插入的字符串（「是不是提及」始终由后端在消息落库时裁决）。

   本票（issue #8）新增：
   - 轮次失败的可见呈现：消费只读端点 `GET /channels/{id}/round-outcomes` 的**既有事实**
     （`status` / `reason` / `base_seq`），在消息流里按 `base_seq` 挂到**触发它的那条消息**下面。
     轮次结果不是消息：它不占 `seq`、不改写任何消息，也不伪造「它答应了」。
   - 「未启用」与「这一轮失败」在呈现上区分开：`--offline` 是成员状态（侧栏/dock 状态点），
     `--error` 是这一轮的结论（消息流里的失败行）。

   本票（issue #18）新增：
   - 轮次结果**全态可见**（#58 起六态），但**每一条消息下面至多一行**：同一条消息（同一 `base_seq`）下
     若有多条**非 `failed`** 的轮次结果（`ignored` / `budget-exhausted` / `merged` / `silent`），收成
     **一行默认折叠的摘要行**（成员名按后端返回序逐个列出，超过 2 人写成「甲、乙 等 N 人」）；
     展开后逐成员一行给出该成员的 status 中文说法与后端 `reason` 原文（不重新措辞、不截断）。
   - `failed` 保持**直接展开**（不藏在折叠后面），与摘要行同挂一条消息、互不吞并。
     「它为什么没说话」的四种**正常协议选择**（自判忽略 / 预算已用尽 / 被合并 / 结构化声明沉默）从静默
     变成可读，但不得淹没对话，也不得与「它坏了」混为一谈（`silent` 不是失败，也不是 `ignored`）。
   - 折叠/展开是本浏览器页面的呈现状态（内存里的 `state.expandedOutcomes`），不进 localStorage：
     本文件依旧不用浏览器存储保存任何业务状态。

   DOM 一律用 createElement/textContent 构建，不用 innerHTML（模板与数据分离）。

   本票（issue #14）新增：
   - 右侧「频道成员」面板名实相符：按**当前频道的成员表**（`state.channelMembers`，来源
     `GET /api/channels/{id}/members`）渲染，并分「已加入 / 未加入」两段；未加入的 Agent
     在面板内直接有「加入」控件（人类裁定 2 选 a），点击调用 `POST /api/channels/{id}/members`。
   - 加入成功后**不刷新页面**：用响应里的最新成员表覆盖缓存，`@` 补全立刻能选到它。
     左栏「本机 Agent」（`renderMembers()`）仍是全工作区名单，语义与「频道成员」不再冲突。

   本票（issue #68）新增：
   - **提醒**：`reminder` 是**库内对象**（ADR-0012），后端是唯一正本。左栏 rail 的入口旁的圆点
     只在**有提醒**时出现（原型 `alibot-app.html:511-514` / `:1886`）；弹窗按后端读数渲染列表
     （时间 / 文本 / 归属频道 / 删除）、空态与新增控件（文本 + 时间）。
   - 增删提醒与改设置同一条纪律：**不落消息、不唤醒、不消耗预算**，也不新增 target、不计未读。
     写路径一律以**响应里的最新提醒表**覆盖本地镜像（不本地拼一条假行），因此列表永远等于后端说
     的那一份；本文件依旧不用任何浏览器存储保存业务状态。

   本票（issue #114）新增 —— 上一段 #68 里**两处读数已被本票取代**（原文保留作交付历史）：
   - 圆点的判据从「有提醒」改成「**还有未到点的提醒**」（`!state.reminders.some(r => !r.fired_at)`）：
     已响 / 已错过不等待任何事。
   - 新增控件从「文本 + 时间」改成「文本 + **日期 + 钟点**两个原生输入」，提交时拼成一个
     `YYYY-MM-DD HH:MM` 串交给后端（ADR-0017 的时间语义）；列表行改成
     「文本 · 归属频道 · [到期时刻 | 「已提醒」 | 「已错过」] · 删除」：到期时刻并进状态后缀
     （用户裁定，见 :func:`reminderStateNode`），只消费后端给的 `state`，前端不做任何判定。

   本票（issue #54）新增 —— 频道设置里的**回应预算**（ADR-0007 / 规格 #31 §2）：
   - 频道头新增预算入口，打开一张**频道设置**弹窗（工作区设置弹窗的语义一个字不改）；
   - 弹窗只读四项后端读数（已用 / 剩余 / **有效上界** / **来源**）与**下一世代待生效值**，两者分开
     呈现；预算耗尽写成一条明确事实，不进错误区；
   - 写控件只有固定档位 3 / 5 / 10（默认 3），提交 `{budget_limit}`；保存后按响应里的新读数重铺，
     并提示「从下一个人类世代生效」。用量（`used`）前端既不算也不提交——它是后端的正本。

   本票（issue #154）新增 —— 频道设置面（规格 §4.6 / ADR-0018 / ADR-0021）：
   - 频道头新增设置入口，打开一张弹窗：mode（autonomous / team）、队长、项目根、默认后端、
     默认权限档；`team_scope` 是后端只读事实，固定展示 `channel+task`；
   - `leader_available` / `leader_reason` 与频道静默时的裁决读同一份后端事实；team 模式下
     显示「成员 auto_claim 已停用」「任务线程只唤醒队长」；
   - 保存提交 `PATCH /api/channels/{id}/config`，成功后按响应重铺；非法字段只显示后端可读原因
     （整体拒绝、不留下半成品）；任务视图读取所属频道的同一份配置并显示同一条唤醒提示。
   ========================================================================== */
'use strict';

const OWNER_FALLBACK = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };

/** 第三方 CLI 的安装命令（ADR-0036）：**ASCII 常量**，不进词条表——它是要被原样复制到终端
    的命令，不是界面文案。`-g` 装到全局、`-a codex` 指向 `~/.agents/skills`（本应用只读那里）。 */
const SKILL_INSTALL_COMMAND = 'npx skills add <owner/repo> -g -a codex -y';

/** 三个页签的名字与顺序（`CONTEXT.md` **Agent 详情页签** / D1）：顺序固定，不开第四个。 */
const AGENT_DETAIL_TABS = ['base', 'skills', 'mcp'];
const AGENT_DETAIL_DEFAULT_TAB = 'base';

/* ─── 界面文案一律按**词条**取值（`i18n.js` 的 `t(key, params)`） ────────────
   本文件里不再有裸中文文案：结构化状态词（四态状态点 / 任务状态 / 轮次结果状态）后端只给**词**，
   这里只把词翻译成界面说法；措辞全部住在词条表的两份取值里。

   `hasKey` 的分支是「有词条就用词条、没有就回退到后端原词」：回退不是为了兜住漏网文案，
   而是因为状态词是**后端给的集合**（例如未来的新状态），前端不该伪造一个说法给它。 */

/** 四态状态点的界面说法（键名与后端 `status_of` 的四态同名）。 */
function statusLabel(status) {
  const key = 'status.' + status;
  return hasKey(key) ? t(key) : status;
}

/** 任务状态的界面说法（键名与 `TASK_STATUSES` 同名）。 */
function taskStatusLabel(status) {
  const key = 'task.status.' + status;
  return hasKey(key) ? t(key) : status;
}

/** 状态迁移 chip 上的**动词**文案（原型 `THREAD_TRANSITION_LABEL`）。
    按钮一律动宾式（`SKILL.md` §6）；`closed` 是终态，因此它没有目标描述符，
    那张表里也不会有 `closed → *` 的条目（不是「漏了一格」，是这一格不存在）。 */
function taskTransitionLabel(target) {
  const key = 'task.transition.' + target;
  return hasKey(key) ? t(key) : taskStatusLabel(target);
}

const TASK_STATUS_ORDER = ['todo', 'in_progress', 'in_review', 'done', 'closed'];

/** 一张任务**当前态**的全部合法迁移目标（#191 D2）。

    集合的正本在后端：任务载荷的 `allowed_transitions` 由 `TASK_STATUS_TRANSITIONS` 派生。
    前端**不持有**迁移表副本——没有字段就是空集合（一个目标都不给），而不是回退到一份本地表；
    写路径（`PATCH /api/tasks/{id}/status`）仍是唯一权威，这里只决定「呈现什么」。 */
function taskAllowedTransitions(task) {
  return Array.isArray(task && task.allowed_transitions) ? task.allowed_transitions : [];
}

/** 模型提供商的界面说法（远端 / 本机是**事实**，不是档位）。 */
function providerLabel(provider) {
  const key = 'provider.' + provider;
  return hasKey(key) ? t(key) : provider;
}

/* 轮次结果在**摘要行**里的呈现：一个 status 一行，三张字段不再各占一张平行表
   （加一态只需改一处，也不会出现「某张表漏了一态」的漂移）。

   键集是 `CONTEXT.md` `## Language` 词表里、**不进摘要行**的那四个：`ignored`（自判忽略）、
   `budget-exhausted`（预算耗尽）、`merged`（被合并）、`silent`（模型经结构化通道声明沉默）。
   它们**不得混为一谈**：`ignored` 是正常协议选择、`budget-exhausted` 不是出错、`merged` 不是失败、
   `silent` 是模型**显式声明**的沉默（不是失败，也不是既有的自判忽略）。

   `replied` 与 `failed` 不在表里：`replied` 的可见事实就是那条回复消息本身（不为它挂行）；
   `failed` 直接展开（状态词要在「成员此刻未启用」与「这一轮失败」之间区分，写在 `roundOutcomeNode()` 里）。
   不给它们留查不到的死词条。

   `note` 是折叠态那一句「为什么」——四态各自的后端事实不同，说法就应当不同。 */
const SUMMARY_STATUSES = ['ignored', 'budget-exhausted', 'merged', 'silent'];

/** 取一个 status 的中文说法；不在摘要词表里就原样回退（不伪造一个说法）。 */
function outcomeLabel(status) {
  const key = 'outcome.' + status + '.label';
  return hasKey(key) ? t(key) : status;
}

/** 折叠行那一句「为什么」（四态各自的后端事实说法；与 `label` 一样只在词条表里）。 */
function outcomeNote(status) {
  const key = 'outcome.' + status + '.note';
  return hasKey(key) ? t(key) : status;
}

// 工作台快照到齐之前的占位视图。它**不是** provider 清单的正本（正本只有后端 `/api/workspace`
// 的 `model_providers`），只为了刷新后马上点「Agent」时不至于给一个空选择器。
const PROVIDER_PLACEHOLDER = ['ollama', 'dashscope', 'openai'];

/* ─── 主题三档（#71）：取值与后端 `THEMES` 同名同序 ──────────────────────────
   `system` 是**缺省**档：它不在这里解析媒体查询——「跟随系统」由 CSS 的
   `@media (prefers-color-scheme: dark)` 裁决，前端只把档位本身写到 `<html data-theme>` 上，
   于是显式两档走属性选择器、跟随系统走媒体查询，两条分支各有一个落点。
   标签文案住在词条表（`settings.themeLight` / `settings.themeDark` / `settings.themeSystem`）。 */
const THEMES = ['light', 'dark', 'system'];
const DEFAULT_THEME = 'system';

const state = {
  ready: false,
  mode: 'channel',
  online: true,
  streamState: 'connecting',
  member: OWNER_FALLBACK,
  channels: [],
  tasks: [],
  boardTasks: [],
  // 看板的呈现状态（不是业务状态）：`.seg` 的两半、频道筛选（`''` = 全工作区）、
  // 「我的任务」。「我的任务」由 owner 与任务指派者（或未指派）的直接比较决定。
  taskView: 'board',
  taskFilterChannelId: '',
  taskFilterMine: false,
  // 任务线程现在是右侧 dock 的**第二种形态**（`dockMode`），不再是主线上的一个视图。
  dockMode: 'agent',
  // 「人把 dock 收起来了」——与「dock 里有没有内容」是两件事。`setDockOpen(false)` 置位，
  // 显式打开（卡片 / Agent 行 / 频道头的开关）清位；`setDockMode()` 只在没有这一位时才
  // 自动显示，否则下一次整体重绘会把「人刚按下的收起」推翻（收起会变成一次性的假动作）。
  dockDismissed: false,
  // Agent 详情是**主线视图**（`state.mode === 'agent'`），不再是 dock 的形态：
  // `activeAgentId` 是「打开着哪一个成员」的唯一正本；`agentReturn` 记住进入前的视图与
  // dock 开合，关闭详情时按同一份判据恢复（`openAgentDetail()` / `closeAgentDetail()`）。
  activeAgentId: null,
  agentReturn: { mode: 'channel', dockOpen: false },
  // 详情页当前页签（呈现状态，不是业务正本）：`'base' | 'skills' | 'mcp'`，顺序固定。
  // 不落库、不写 URL、不记忆上次值：打开详情与切换成员都回到 `'base'`（D1）。
  agentDetailTab: AGENT_DETAIL_DEFAULT_TAB,
  // 详情「配置」区块的**草稿**（呈现状态，不是业务正本）：`memberId` 是它属于谁，
  // `draft` 是还没提交的字段值，`error` 是后端拒绝时那句原文。与后端读数不一致时
  // 页面底部出现保存条；关闭详情 / 切换成员 / 切换主线视图时整体作废（不弹二次确认）。
  agentEdit: emptyAgentEditDraft(),
  // 危险操作（本票）：删除走**确认弹窗**——`memberId` 是弹窗里等着被删的那个成员，
  // `busy` 在请求进行中禁用按钮。它是呈现状态，不是业务正本（正本在后端软删除）。
  agentDelete: emptyAgentDeleteState(),
  // Agent 详情里的**全局 MCP 只读小节**（#236）：列表是 `GET
  // /api/agents/{id}/global-mcp-servers` 的镜像；`error` 只用于把读取失败讲人话。
  // 它不是业务正本（真值在成员家目录的 mcp.json + 确认行），切换成员时整体作废。
  globalMcpServers: [],
  globalMcpError: '',
  // 成员名册（频道成员的**只读**全名单）当前锁定的频道 id；`null` = 弹窗关着。
  // 名册回答「打开它的那个频道里有谁」，因此它在打开时锁定频道，不跟着激活频道跑。
  rosterChannelId: null,
  // 频道回应预算弹窗（#54）：`budget` 是 `GET/PATCH /api/channels/{id}/budget` 载荷的镜像，
  // `selected` 是**还没提交**的档位选择（呈现状态）。两者都不是业务正本——正本在后端；
  // 关闭时整个镜像作废，于是「拿旧读数当现状」这个中间态不存在。
  channelBudget: { channelId: null, budget: null, selected: null, loading: false, error: '' },
  // 频道设置面（#154）：`config` 是 `GET /api/channels/{id}/config` 载荷的镜像，`draft` 是
  // **还没提交**的呈现状态；两者都不是业务正本——正本在后端，保存后按响应重铺。
  channelConfig: {
    channelId: null,
    config: null,
    members: [],
    membersFailed: false,
    loading: false,
    error: '',
    draft: {
      mode: 'autonomous',
      leader: '',
      workspaceRoot: '',
      backend: 'local',
      permission: 'ask-writes',
      webAccess: 'search',
    },
  },
  // 任务视图要读所属频道的同一份配置（#154 / 规格 §4.6）：按 channel id 缓存
  // `GET /api/channels/{id}/config` 的载荷，正本仍是后端。
  channelConfigs: {},
  // 能力配置（#273 / ADR-0031 §二）：MCP 声明与技能**只有一个归口**——Agent 详情。作用域恒为
  // 该成员（`scope_kind=agent&scope_id=<member>`），因此这里按成员缓存：`mcp` / `skills` 是
  // `GET /api/mcp-servers` / `GET /api/skills` 的镜像，`mcpDraft` 是**还没提交**的表单状态。
  // 清单里只有 `env_names`（变量名）——值从来不进这条通道。
  agentCapabilities: {},
  // 名册的能力摘要（#273）：按成员缓存两个只读数字（`failed` 只影响那一行的占位），
  // 打开名册时整体作废重读，于是「读不到」不会被留成永久结论。
  rosterCapabilities: {},
  // 联网 provider 的掩码读数（#199）：`GET /api/web-providers` 的镜像。`draftKeys` 只放**还没
  // 提交**的输入值（password 框），绝不回填已保存的 key；`errors` 按 provider 记写失败原因。
  webProviders: emptyWebProvidersView(),
  // 运行面板（#155 / 规格 §4.6）：dock 的**第三种形态**（与频道成员 / 任务线程共用容器），
  // 频道与任务线程都能打开。
  // 它是**独立面**——成员过程、分派、Plan、SOP 只在这里呈现，不进消息流 / 未读 / 搜索。
  // `runs` / `plan` / `sops` 是三个只读快照（`GET /team-runs` / `GET /plan` /
  // `GET /sop-runs/{id}`）的镜像；live 帧（`state.liveEvents`）在渲染时按 id 合并，
  // 于是同一分派不会因为「快照里也有一条」而出现两行。`sopIds` 只记录已经见过的
  // `sop_run_id`——后端没有「按 target 列 SOP run」的读面，重连兜底只能按已知 id 刷新。
  runPanel: {
    open: false,
    targetId: null,
    channelId: null,
    taskId: null,
    loading: false,
    error: '',
    runs: [],
    plan: [],
    sops: [],
    sopIds: [],
    // SOP run 的 state 只有步骤下标 / phase / verifications，没有 subject（AgentScope 的
    // 运行态不在记录里重复定义）。步骤名从 `GET /api/sops/{definition_id}` 的定义读，
    // 按定义 id 缓存。
    sopDefs: {},
    // 快照完成那一刻 live 队列里的最后一条事件。Plan / SOP 的 live 覆盖只应用它**之后**
    // 的帧：快照已经包含更早的变化，重连后再把旧帧叠上去会把状态拉回去。
    overlayMarker: null,
  },
  // 审批卡（#153 / 规格 §4.6 第 4 条 / ADR-0021 §四）：按 target 缓存
  // `GET /api/approvals?state=pending` 的镜像，再叠加本次会话的 live `approval_*` 帧。
  // `approvalInFlight` 是提交中的幂等闸门（按钮 disabled 的同一来源）；
  // `approvalErrors` 只存本次会话的提交错误，不写任何浏览器存储。
  approvals: new Map(),
  approvalInFlight: new Set(),
  approvalErrors: new Map(),
  activeTaskId: null,
  activeTask: null,
  taskMessages: [],
  taskOutcomes: {},
  taskSeenSeqs: new Set(),
  taskStream: null,
  taskStreamTimer: null,
  dms: [],
  // 「已归档」私信段的展开态：**会话内的呈现状态**（不是业务正本，不写任何浏览器存储）。
  // 默认折叠；用户点标题才置位。`renderDmArchiveSection()` 是它唯一的裁决点。
  dmArchiveExpanded: false,
  // 主 composer 里还没发送的附件草稿（上传成功后拿到标识与元数据；随下一条消息绑定）。
  // 它**不是**业务正本：正本是库里的附件行（+ 数据目录里的字节）。
  composerAttachments: [],
  agents: [],
  providers: [],
  localProviders: [],
  consents: [],
  dataFaces: [],
  activeChannelId: null,
  messages: [],
  // 轮次结果按「触发它的那条消息的 seq」分组（后端事实的镜像，不是本地推断）。
  outcomes: {},
  // 摘要行的展开/折叠：**会话内的呈现状态**，不是业务状态（不进任何浏览器存储）。
  // 键 = 触发它的那条消息的 seq。
  expandedOutcomes: {},
  // 轮次结果的第二份索引：按**回复消息**的 `reply_seq` 直取那一条轮次结果。
  // 旁白行要展开看「关联轮次结果的 reason」，而 `outcomes` 是按**触发消息**的 `base_seq`
  // 分组的——两份索引都从同一批后端行来、在 `applyOutcomes` 一处建，因此不会各自漂。
  replyOutcomes: {},
  // 旁白行的展开/折叠：同样是**会话内的呈现状态**。键 = 旁白那条消息自己的 `seq`
  // （与 `expandedOutcomes` 不共用：那一个键是**触发**消息的 seq，两者会撞号）。
  expandedNarrations: {},
  pending: [],
  members: [],
  channelMembers: {},
  statuses: {},
  // 整页**一条**全局事件流（`GET /api/events`）的承载对象：`{ source, timer, rotating }`。
  // 连接数与 target 数因此彻底解耦（浏览器对单一 origin 的 HTTP/1.1 并发连接只有 6 条）。
  stream: null,
  seenSeqs: new Map(),
  // live-only 运行事件（ADR-0020 / 规格 §4.5）：按 **target_id** 归属，只活在本会话内存里。
  // 它们不是消息正本：不进 `messages` / 未读 / 搜索 / seq 游标。运行面板（#155）从这里取；
  // 本层只做「消费 + 有界保留 + 未知 type 忽略」。
  liveEvents: new Map(),
  typing: {},
  activeRounds: {},
  // 工作区设置（**语言偏好** / **消息密度** / **主题**）：正本是后端 `GET /api/settings`，
  // 前端只镜像它；切换时写回后端，不落浏览器存储。
  settings: {
    locale: I18N_SOURCE_LOCALE,
    message_density: 'comfortable',
    theme: DEFAULT_THEME,
  },
  // 提醒（`CONTEXT.md` **提醒**，ADR-0012）：同样是**后端对象的镜像**，正本是 `GET /api/reminders`
  // （`reminder` 表）。增删都以响应里的最新列表覆盖它，本地不造行、不落浏览器存储。
  reminders: [],
  // 搜索是纯呈现状态：命中、来源和高亮区间都来自后端，不在浏览器保存或重算。
  // `facet` 是**后端的过滤参数**（原型的四档），前端只记「选的是哪一档」。
  search: { query: '', facet: 'all', hits: [], loading: false, error: '' },
};

let searchRequestVersion = 0;
let remoteFlow = { statement: '', data_faces: [], local_providers: [] };

/* ─── 工具 ──────────────────────────────────────────────────────────── */
const $ = (selector) => document.querySelector(selector);

function avatarClass(slot) {
  const index = Number.isInteger(slot) ? slot : 0;
  return 'av-' + (((index % 5) + 5) % 5);
}

function initial(name) {
  const text = String(name || '?').trim();
  return text ? Array.from(text)[0].toUpperCase() : '?';
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

/** 头像的 class 串：左栏的就地更新（`syncAvatarCell`）与新建节点（`avatarNode`）共用同一个
    写法，免得改名 / 换色位时两处漂成两套。 */
function avatarClassName(size, slot) {
  return 'avatar ' + (size ? size + ' ' : '') + avatarClass(slot);
}

function avatarNode(name, slot, size) {
  return el('div', avatarClassName(size, slot), initial(name));
}

function activeChannel() {
  return state.channels.find((channel) => channel.id === state.activeChannelId) || null;
}

function activeTarget() {
  return activeChannel() || state.dms.find((dm) => dm.id === state.activeChannelId) || null;
}

/** 当前 target 是否为已归档私信：peer 被软删除后 DM target 与历史消息都保留，人类面只读。
    判据只有后端事实 `peer.deleted_at`（与 rail 的「已归档」段同一个谓词 `dmIsArchived`）。 */
function activeArchivedDm() {
  const target = activeTarget();
  return Boolean(target && target.kind === 'dm' && dmIsArchived(target));
}

function setActiveChannelId(channelId) {
  state.activeChannelId = channelId;
  // 只记住「上次打开的频道」，这不是业务正本。
  try {
    if (channelId) localStorage.setItem('alibot.activeChannel', channelId);
    else localStorage.removeItem('alibot.activeChannel');
  } catch {
    // 隐私模式下 localStorage 不可用：忽略，业务状态本来也不在这里。
  }
}

function readRememberedChannelId() {
  try {
    return localStorage.getItem('alibot.activeChannel');
  } catch {
    return null;
  }
}

function toast(message, kind) {
  const node = el('div', 'toast' + (kind === 'err' ? ' err' : ''));
  node.appendChild(el('span', 'dot'));
  node.appendChild(document.createTextNode(message));
  $('#toastWrap').appendChild(node);
  setTimeout(() => {
    node.style.transition = 'opacity .2s';
    node.style.opacity = '0';
    setTimeout(() => node.remove(), 220);
  }, 3200);
}

/* ─── HTTP（唯一读写通道） ───────────────────────────────────────────── */
async function api(path, options) {
  const config = options || {};
  const response = await fetch(path, {
    method: config.method || 'GET',
    headers: config.body ? { 'Content-Type': 'application/json' } : undefined,
    body: config.body ? JSON.stringify(config.body) : undefined,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null; // 非 JSON 响应（例如代理错误页）：正文留给下面的错误映射。
  }
  if (!response.ok) {
    const error = new Error(readError(payload, response));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function readError(payload, response) {
  if (payload && payload.detail && typeof payload.detail === 'string') return payload.detail;
  if (payload && payload.detail && Array.isArray(payload.detail)) {
    return payload.detail.map((item) => item.msg || t('error.badRequestBody')).join(t('common.clauseSep'));
  }
  if (payload && payload.error) {
    const fields = payload.error.fields || {};
    const details = Object.keys(fields)
      .map((key) => String(fields[key]))
      // 字段级文案常与总文案重合，别把同一句说两遍。
      .filter((detail) => detail && !String(payload.error.message).includes(detail));
    return details.length
      ? payload.error.message + t('common.colonSep') + details.join(t('common.clauseSep'))
      : payload.error.message;
  }
  return t('error.httpStatus', { status: response.status });
}

function channelPath(channelId, suffix) {
  return '/api/channels/' + encodeURIComponent(channelId) + (suffix || '');
}

function taskPath(taskId, suffix) {
  return '/api/tasks/' + encodeURIComponent(taskId) + (suffix || '');
}

function dmPath(dmId, suffix) {
  return '/api/dms/' + encodeURIComponent(dmId) + (suffix || '');
}

function targetPath(targetId, suffix) {
  return state.dms.some((dm) => dm.id === targetId)
    ? dmPath(targetId, suffix)
    : channelPath(targetId, suffix);
}

/* ─── 提及：消费后端落库的提及快照（前端不自己解析一遍） ─────────────────── */
function mentionSpans(message) {
  // 只认「有 start/end 的成员快照」：这是后端在同一事务里随消息落库的解析结果。
  const snapshot = Array.isArray(message.mention_snapshot) ? message.mention_snapshot : [];
  return snapshot
    .filter((span) => span && Number.isInteger(span.start) && Number.isInteger(span.end))
    .slice()
    .sort((a, b) => a.start - b.start);
}

function messageText(message) {
  const body = String(message.body == null ? '' : message.body);
  const spans = mentionSpans(message);
  const container = el('div', 'msg-text');
  let cursor = 0;
  spans.forEach((span) => {
    // 快照与正文不一致时（理论上不可能，后端同时写入）宁可退化为纯文本，也不切错字符。
    if (span.start < cursor || span.end > body.length) return;
    if (span.start > cursor) container.appendChild(document.createTextNode(body.slice(cursor, span.start)));
    const mention = el('span', 'mention', body.slice(span.start, span.end));
    mention.dataset.member = span.member_id;
    container.appendChild(mention);
    cursor = span.end;
  });
  if (cursor < body.length) container.appendChild(document.createTextNode(body.slice(cursor)));
  return container;
}

/* ─── 工作台搜索：只消费后端命中与区间，不在前端重新判定正文 ───────────────
   三类命中（消息 / 任务 / Agent）共用同一套呈现：可呈现文本 + 后端给的区间 + 来源 + 类型。
   前端既不算命中、也不在文本里找位置（`CONTEXT.md` 搜索命中 / 高亮区间两条硬纪律）。 */
function searchTargetLabel(target) {
  const name = target && target.display_name ? target.display_name : t('search.unknownTarget');
  if (!target) return name;
  if (target.kind === 'channel') return t('search.targetChannel', { name });
  if (target.kind === 'dm') return t('search.targetDm', { name });
  if (target.kind === 'task') return t('search.targetTask', { name });
  return name;
}

/** 结果行 `.r-top` 的「来源」一格：命中所属 target；Agent 不挂 target，因此给它的集合名。 */
function searchSourceLabel(hit) {
  return hit.target ? searchTargetLabel(hit.target) : t('search.sourceAgent');
}

/** 结果行 `.r-top` 的命中类型标签（与四档 facet 同名：`消息` / `任务` / `Agent`）。 */
function searchHitTypeLabel(hit) {
  const key = { message: 'search.facetMessage', task: 'search.facetTask', agent: 'search.facetAgent' }[
    hit.kind
  ];
  return key ? t(key) : String(hit.kind || '');
}

/** `.r-title`：**只**按后端给的区间切分文本，区间缺失时整段呈现（绝不在前端找位置）。 */
function searchHitText(hit) {
  const text = String(hit && hit.text == null ? '' : hit.text);
  const ranges = Array.isArray(hit && hit.highlight_ranges) ? hit.highlight_ranges : [];
  const container = el('div', 'r-title');
  let cursor = 0;
  ranges.forEach((range) => {
    if (!range || !Number.isInteger(range.start) || !Number.isInteger(range.end)) return;
    if (range.start < cursor || range.end <= range.start || range.end > text.length) return;
    if (range.start > cursor) container.appendChild(document.createTextNode(text.slice(cursor, range.start)));
    container.appendChild(el('mark', null, text.slice(range.start, range.end)));
    cursor = range.end;
  });
  if (cursor < text.length) container.appendChild(document.createTextNode(text.slice(cursor)));
  return container;
}

/** `.r-snip`：命中本体之外的**一行**辅助事实（消息给作者、任务给序号与状态、Agent 给状态与模型）。 */
function searchSnipNode(hit) {
  const snip = el('div', 'r-snip');
  if (hit.kind === 'message') {
    const author = (hit.message && hit.message.author) || {};
    snip.appendChild(
      document.createTextNode(
        t('search.snipAuthor', { name: author.display_name || t('search.unknownAuthor') }),
      ),
    );
    if (author.deleted) snip.appendChild(el('span', 'search-author-deleted', t('search.authorDeleted')));
    return snip;
  }
  if (hit.kind === 'task') {
    const task = hit.task || {};
    snip.textContent = t('search.snipTask', {
      id: String(task.id || '').slice(-6),
      status: taskStatusLabel(task.status),
    });
    return snip;
  }
  const agent = hit.agent || {};
  snip.textContent = t('search.snipAgent', {
    status: statusLabel(agent.status),
    model: agent.model_id || '',
  });
  return snip;
}

/** 一条结果行（`.result` / `.r-top` / `.r-title` / `.r-snip`）。

    今天唯一能落地的命中是**任务**（点开右侧任务线程，原型 `data-opentask` 同构），判据是
    **载荷里有没有可打开的任务标识**——不按 `kind` 再分派一次；消息命中的滚动定位与 Agent 详情面
    都还没有交付（Agent 详情是 #64），因此那两类是**非交互**行，不做「点了没反应」的按钮。 */
function searchResultNode(hit) {
  const taskId = hit.task && hit.task.id ? hit.task.id : null;
  const node = el(taskId ? 'button' : 'div', 'result');
  if (taskId) {
    node.type = 'button';
    node.dataset.opentask = taskId;
    node.setAttribute('data-od-id', 'search-task');
  }
  const top = el('div', 'r-top');
  top.appendChild(el('span', 'hash', searchSourceLabel(hit)));
  top.appendChild(el('span', 'card-tag', searchHitTypeLabel(hit)));
  node.appendChild(top);
  node.appendChild(searchHitText(hit));
  node.appendChild(searchSnipNode(hit));
  return node;
}

function searchEmptyState(titleKey, bodyKey, params) {
  const empty = el('div', 'empty');
  empty.appendChild(el('b', null, t(titleKey, params)));
  empty.appendChild(el('p', null, t(bodyKey)));
  return empty;
}

function renderSearchResults() {
  const results = $('#searchResults');
  const error = $('#searchError');
  const search = state.search;
  error.hidden = !search.error;
  error.textContent = search.error;
  if (search.error) {
    replaceChildren(results, []);
    return;
  }
  if (search.loading) {
    replaceChildren(results, [el('div', 'empty', t('search.searching'))]);
    return;
  }
  if (!search.query) {
    replaceChildren(results, [searchEmptyState('search.idleTitle', 'search.idleBody')]);
    return;
  }
  if (!search.hits.length) {
    replaceChildren(results, [
      searchEmptyState('search.emptyTitle', 'search.emptyBody', { query: search.query }),
    ]);
    return;
  }
  replaceChildren(results, search.hits.map(searchResultNode));
}

/** 四档 facet 的选中态（`.filter-chip.is-on`，原型 `:631-635` 的取值与顺序）。 */
function renderSearchFacets() {
  const row = $('#searchFacets');
  if (!row) return;
  Array.prototype.forEach.call(row.querySelectorAll('[data-facet]'), (chip) => {
    const on = chip.dataset.facet === state.search.facet;
    chip.classList.toggle('is-on', on);
    chip.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

// 输入时每次按键都打后端是浪费：把「落笔之后的一次请求」延后一拍，facet 切换与 Enter 立即执行。
const SEARCH_DEBOUNCE_MS = 120;
let searchTimer = null;

function cancelPendingSearch() {
  if (searchTimer === null) return;
  clearTimeout(searchTimer);
  searchTimer = null;
}

function scheduleSearch() {
  cancelPendingSearch();
  searchTimer = setTimeout(() => {
    searchTimer = null;
    runSearch();
  }, SEARCH_DEBOUNCE_MS);
}

/** 「搜索」入口：搜索是**主线视图**（占消息流那块位置），不是盖住界面的弹层。 */
function showSearchView() {
  closeTaskStream();
  closeTaskDock();
  leaveAgentView();
  state.mode = 'search';
  render();
  const input = $('#searchInput');
  input.value = state.search.query;
  input.focus();
}

function setSearchFacet(facet) {
  if (state.search.facet === facet) return;
  state.search.facet = facet;
  cancelPendingSearch();
  renderSearchFacets();
  if (state.search.query) runSearch();
  else renderSearchResults();
}

async function runSearch() {
  const input = $('#searchInput');
  const search = state.search;
  const requestVersion = ++searchRequestVersion;
  // 空格也是字面子串的一部分，不能像一般表单字段那样静默去掉首尾空白。
  search.query = input.value;
  search.error = '';
  search.hits = [];
  search.loading = false;
  if (!search.query) {
    renderSearchResults();
    return;
  }
  if (!state.online) {
    search.error = t('search.offline');
    renderSearchResults();
    return;
  }
  search.loading = true;
  renderSearchResults();
  // facet 是**后端的过滤参数**：切换档位就是换一次请求，不在前端过滤结果集。
  const params = new URLSearchParams({ q: search.query, facet: search.facet });
  try {
    const payload = await api('/api/search?' + params.toString());
    if (requestVersion !== searchRequestVersion) return;
    search.hits = Array.isArray(payload.hits) ? payload.hits : [];
  } catch (err) {
    if (requestVersion !== searchRequestVersion) return;
    // 搜索失败与零命中是两种事实：错误必须留在结果面里，不能只清空列表。
    search.error = t('search.failed', { reason: err.message });
  } finally {
    if (requestVersion === searchRequestVersion) {
      search.loading = false;
      renderSearchResults();
    }
  }
}

/* ─── ⌘K 命令面板（规格 D10） ──────────────────────────────────────────────
   命令集 = 跳转（消息视图 / 任务看板）+ 动作（新建频道 / 新建任务 / 切换详情面板）
   + **动态**频道组（当前工作台的频道，不是原型里写死的种子名）。D10 枚举的命令集里**没有**
   原型 `CMDS` 的「搜索」一条，`⌘K` 真绑定的是**打开命令面板**本身（不是搜索视图），
   因此这里也不给它挂 `⌘K` 标签——界面不承诺一个按键做不到的事。
   面板里只呈现**真绑定**的快捷键：`M` 消息视图、`T` 任务看板（`ESC` 的提示在面板自己的字段上）。
   原型的 `N` / `⇧N` / `D` 三个 kbd 标签并没有绑定任何按键，因此不出现。 */
function commandList() {
  return [
    { id: 'view-messages', group: 'cmd.groupJump', label: t('cmd.messages'), kbd: 'M', run: showMessagesView },
    { id: 'view-board', group: 'cmd.groupJump', label: t('cmd.board'), kbd: 'T', run: showTaskBoard },
    { id: 'new-channel', group: 'cmd.groupAction', label: t('rail.newChannel'), run: openChannelModal },
    { id: 'new-task', group: 'cmd.groupAction', label: t('board.newTask'), run: () => openTaskModal() },
    // 私信下 dock 没有可显示的形态（见 `dockAvailable()`），命令面板里就不列这条命令——
    // 与频道头入口同一条判据，不留一个按下去什么都不发生的条目。
    ...(dockAvailable()
      ? [{ id: 'toggle-dock', group: 'cmd.groupAction', label: t('dock.toggle'), run: toggleDock }]
      : []),
    ...state.channels.map((channel) => ({
      id: 'channel:' + channel.id,
      group: 'cmd.groupChannels',
      label: '#' + channel.name,
      run: () => selectTarget(channel.id),
    })),
  ];
}

function renderCmd(filter) {
  const query = String(filter || '').toLowerCase();
  const commands = commandList().filter((command) => command.label.toLowerCase().includes(query));
  const list = $('#cmdList');
  if (!commands.length) {
    replaceChildren(list, [el('div', 'cmd-group', t('cmd.empty'))]);
    return;
  }
  const nodes = [];
  let group = null;
  let first = true;
  commands.forEach((command) => {
    if (command.group !== group) {
      group = command.group;
      nodes.push(el('div', 'cmd-group', t(group)));
    }
    const item = el('button', 'cmd-item' + (first ? ' is-active' : ''));
    item.type = 'button';
    // 用**稳定 id** 而不是标签做归属：频道名可能与某条命令同名，标签不是键。
    item.dataset.cmd = command.id;
    item.appendChild(el('span', 'grow', command.label));
    if (command.kbd) item.appendChild(el('span', 'kbd', command.kbd));
    nodes.push(item);
    first = false;
  });
  replaceChildren(list, nodes);
}

function openCmd() {
  $('#cmdOverlay').hidden = false;
  const input = $('#cmdInput');
  input.value = '';
  renderCmd('');
  input.focus();
}

function closeCmd() {
  $('#cmdOverlay').hidden = true;
}

function runCmd(commandId) {
  const command = commandList().find((item) => item.id === commandId);
  if (!command) return;
  closeCmd();
  command.run();
}

/* ─── 工作区设置（语言偏好 / 消息密度 / 主题）：后端是唯一正本 ───────────── */

/** 把一份设置铺到界面上：`<html lang>` 与语言同一个取值、密度与主题两个档位写到根元素、
    静态文案重铺。

    它是「读回」与「写回」两条路径的**唯一**落点：启动读回、切档位写回后都走它，
    因此不存在「内存里变了但界面没变」的中间态。 */
function applySettings(settings) {
  const next = settings || {};
  state.settings = {
    locale: I18N_LOCALES.includes(next.locale) ? next.locale : I18N_SOURCE_LOCALE,
    message_density: next.message_density === 'compact' ? 'compact' : 'comfortable',
    theme: THEMES.includes(next.theme) ? next.theme : DEFAULT_THEME,
  };
  setLocale(state.settings.locale);
  // 密度档位挂在根元素上（CSS 只认 `html[data-density]` 两个取值）。
  document.documentElement.dataset.density = state.settings.message_density;
  // 主题档位同样挂在根元素上，且**三档都写**：显式 `light` / `dark` 由
  // `:root[data-theme='dark']` 命中，`system` 交给设计系统包里的
  // `@media (prefers-color-scheme: dark)`——这里不解析媒体查询，两条分支各一个落点。
  document.documentElement.dataset.theme = state.settings.theme;
  applyStaticI18n(document);
  renderSettingsModal();
  // 提醒弹窗的文案（空态、删除键的可访问名）也是词条：切语言时要与其它静态文案一起重铺，
  // 否则它会留在上一个语言。渲染只读 `state.reminders`，不发请求。
  renderRemindersModal();
  // 名册的动态文案（标题 / 副标题 / 行内角色与状态说法）由 `t()` 渲染，因此切语言时
  // 要跟设置弹窗一样重铺一遍（静态的关闭按钮由上面那句 `applyStaticI18n` 覆盖）。
  if (state.rosterChannelId) renderRoster();
  // 预算弹窗的读数标签、来源说法与写控件文案同样由 `t()` 渲染：开着就按同一份读数重铺，
  // 于是它不会留在上一门语言（读数本身是后端事实，重铺不发请求）。
  if (state.channelBudget.channelId) renderBudgetModal();
  return state.settings;
}

async function loadSettings() {
  const payload = await api('/api/settings');
  return applySettings(payload.settings);
}

/** 写一个档位：先写后端（正本），成功后按返回的设置重铺界面。

    失败时**不**改本地镜像——界面不能声称一个没落库的取值（改设置不落消息、不唤醒、
    不消耗预算，但「已保存」仍必须是后端说的）。 */
async function saveSettings(patch) {
  const box = $('#settingsFormError');
  try {
    const payload = await api('/api/settings', { method: 'PATCH', body: patch });
    if (box) box.hidden = true;
    applySettings(payload.settings);
    render();
  } catch (err) {
    if (box) {
      box.textContent = t('settings.saveFailed', { reason: err.message });
      box.hidden = false;
    }
    renderSettingsModal();
  }
}

function openSettingsModal() {
  const box = $('#settingsFormError');
  if (box) box.hidden = true;
  renderSettingsModal();
  $('#settingsOverlay').hidden = false;
  const select = $('#settingsLocaleSelect');
  if (select) select.focus();
}

function closeSettingsModal() {
  $('#settingsOverlay').hidden = true;
}

/** 三个档位的选中态与 `<select>` 的当前值都从这里同步（打开与写回后都走它，不各写一份）。 */
function renderSettingsModal() {
  const select = $('#settingsLocaleSelect');
  if (select) {
    const options = I18N_LOCALES.map((locale) => {
      const option = el('option', null, t(locale === 'en' ? 'settings.languageEn' : 'settings.languageZh'));
      option.value = locale;
      return option;
    });
    replaceChildren(select, options);
    select.value = state.settings.locale;
  }
  const group = $('#settingsDensity');
  if (group) {
    Array.prototype.forEach.call(group.querySelectorAll('[data-density]'), (card) => {
      const on = card.dataset.density === state.settings.message_density;
      card.classList.toggle('is-on', on);
      card.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
  // 主题三档（浅色 / 深色 / 跟随系统）：与密度同一套 `.radio-card` 选中态。
  const themes = $('#settingsTheme');
  if (themes) {
    Array.prototype.forEach.call(themes.querySelectorAll('[data-theme]'), (card) => {
      const on = card.dataset.theme === state.settings.theme;
      card.classList.toggle('is-on', on);
      card.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
}

/* ─── 提醒（ADR-0012：库内对象，后端是唯一正本） ────────────────────────────
   列表 / 空态 / 圆点全部按**后端读数**渲染，本地不拼行、不落浏览器存储。

   四处刻意的**偏离原型**（每一条都记账）：
   1. 圆点的判据从原型 `:1886` 的「有提醒就亮」改成「**还有未到点的提醒**才亮」（C17）：
      已响 / 已错过不等待任何事，圆点不能退化成历史计数器。
   2. 新增控件是**两个原生输入**（日期 + 钟点，C15）——原型 `:1579` 只有 `type="time"`。
   3. 行形态把原型的 `.time`（到期时刻列）并进**状态后缀**（理由见 :func:`reminderStateNode`）。
   4. 删除控件用 `×` 而不是原型那个 `plus` 图标 —— 原型 `:1570` 在一个 `aria-label="删除提醒"`
      的控件里复用了「新增」的加号图标（图标与它的可访问名互相矛盾）。可视文案与可访问名不一致
      会让图标/读屏用户读到相反的动作，因此这里按本仓的关闭控件形状用 `×`；
      本仓已有先例：指派 pill 用 `<span>` 而不是原型的 `<button>`（那也是一个按下去不发生的站）。 */

/** 读提醒（正本在后端）：刷新本地镜像、圆点与弹窗列表。

    落点与写路径共用 `applyReminderPayload`——「拿到一份提醒表之后怎么铺界面」只有一处实现，
    读与写不会各自演化（读成功同样要把上一次的错误话收回去：那份状态已经不成立了）。 */
async function loadReminders() {
  applyReminderPayload(await api('/api/reminders'));
  return state.reminders;
}

/** 写路径（新增 / 删除）与读路径的**唯一落点**：用响应里的**最新**提醒表覆盖镜像，再重绘圆点与列表。

    不在本地拼一条「看起来成功了」的行：界面只能显示后端说过的那一份。 */
function applyReminderPayload(payload) {
  state.reminders = (payload && payload.reminders) || [];
  hideReminderError();
  renderReminderDot();
  renderRemindersModal();
}

function showReminderError(message) {
  const box = $('#reminderFormError');
  if (!box) return;
  box.textContent = message;
  box.hidden = false;
}

function hideReminderError() {
  const box = $('#reminderFormError');
  if (box) box.hidden = true;
}

/** 圆点**只在「还有未到点的提醒」时**出现（C17）：已响 / 已错过不等待任何事。

    判据是后端给的「已投递」标记 `fired_at`（C20），**不是条数**——只剩已到点的提醒时圆点
    必须灭掉，否则它就从「还有事等着」退化成历史计数器。这里只**消费**这个标记：`fired_at`
    的含义、以及「到没到点」的判定都在后端，前端不比对时钟（C20 把判定放在后端）。 */
function renderReminderDot() {
  const dot = $('#reminderDot');
  if (!dot) return;
  dot.hidden = !state.reminders.some((reminder) => !reminder.fired_at);
}

/** 本机当天日期（`YYYY-MM-DD`）：日期格的默认值（C15 判据 2）。

    只取「年 / 月 / 日」三段拼串，不做任何时区换算——到期时刻按**本机本地时间**解释是后端的
    口径（C7）；前端这里只是把「今天」填进原生控件，不需要建立一个第二时间正本。 */
function todayDateValue() {
  return localDateString(new Date());
}

function openRemindersModal() {
  hideReminderError();
  renderRemindersModal();
  $('#remindersOverlay').hidden = false;
  // 日期格默认**今天**：打开弹窗就能直接填文本提交，不用先手填日期（C15 判据 2）。
  // 每次打开都回到今天——这是「新提醒」的默认语义，不是记住上一次的取值。
  const dateInput = $('#reminderDate');
  if (dateInput) dateInput.value = todayDateValue();
  const input = $('#reminderText');
  if (input) input.focus();
  // 打开时按**后端正本**再读一次：别的浏览器或上一次会话设的提醒在这里出现。
  // 读不到不关弹窗，把话留在弹窗里（正本读不到时界面不能假装列表是完整的）。
  loadReminders().catch((err) => showReminderError(t('reminder.loadFailed', { reason: err.message })));
}

function closeRemindersModal() {
  $('#remindersOverlay').hidden = true;
}

/** 新增一条提醒：文本 + **日期与钟点两个原生输入**拼成的到期时刻（C15）。

    拼成 `YYYY-MM-DD HH:MM` **一个串**交给后端（ADR-0017 §二把时间语义升成「日期 + 钟点」）。
    合法形状由后端裁决（`db.py` 的写路径正则），前端不替后端猜：空的那一半不补默认值，
    形状不对就由后端回 `validation_failed`，界面把后端的话留在弹窗里。

    归属 = **当前打开的频道**（原型 `:1775` 用当前频道）；DM 视图或空工作台里没有频道可归属，
    于是显式传 `null`——后端把它读作「不归属任何频道」，与设置面那条「显式 null 整体拒绝」不同
    （档位没有「不归属」这个合法状态，提醒有）。 */
async function submitReminder() {
  const textInput = $('#reminderText');
  const dateInput = $('#reminderDate');
  const timeInput = $('#reminderTime');
  const channel = activeChannel();
  const due = [dateInput ? dateInput.value : '', timeInput ? timeInput.value : ''].join(' ').trim();
  try {
    const payload = await api('/api/reminders', {
      method: 'POST',
      body: {
        text: textInput ? textInput.value : '',
        time: due,
        channel_id: channel ? channel.id : null,
      },
    });
    applyReminderPayload(payload);
    // 只清正文：日期格回到今天、钟点格保留当前取值（原型 `#rmTime` 的默认值就是 09:00，
    // 连设两条不用重填）。
    if (textInput) textInput.value = '';
    if (dateInput) dateInput.value = todayDateValue();
  } catch (err) {
    showReminderError(t('reminder.saveFailed', { reason: err.message }));
  }
}

async function deleteReminder(reminderId) {
  try {
    const payload = await api('/api/reminders/' + encodeURIComponent(reminderId), { method: 'DELETE' });
    applyReminderPayload(payload);
  } catch (err) {
    showReminderError(t('reminder.deleteFailed', { reason: err.message }));
  }
}

/** 弹窗内容的两态：有条目就是列表，没有就是空态（照原型 `:1567` 的那条 `|| 空态`）。 */
function renderRemindersModal() {
  const list = $('#reminderList');
  if (!list) return;
  if (!state.reminders.length) {
    const empty = el('div', 'empty');
    empty.appendChild(el('b', null, t('reminder.emptyTitle')));
    empty.appendChild(el('p', null, t('reminder.emptyBody')));
    replaceChildren(list, [empty]);
    return;
  }
  replaceChildren(list, state.reminders.map(reminderRowNode));
}

/** 两个状态词条键（`pending` 没有标签键：它的后缀就是到期时刻本身，C16）。模块级常量：
    键名由 C18 钉死，不随渲染重建，也不拼成 `prefix.value` 那种动态键。 */
const REMINDER_STATE_LABEL_KEYS = { fired: 'reminder.stateFired', missed: 'reminder.stateMissed' };

/** 一条提醒的**状态后缀**节点（C16）：`pending` 时它兼作这一行的到期时刻呈现（完整后端串、
    不切片），`fired` / `missed` 时它显示前端词条文案。

    行形态的正本在这里：**到期时刻与状态后缀是同一件事的两种说法**（等哪会儿 / 已经过去了），
    所以时刻不再单立一个 `.time` 列（原型 `alibot-app.html:1567` 是分开的）——单立会让
    `pending` 行把同一时刻写两遍。这是用户 2026-10-17 对规格 C16 行形态的裁定（B：时刻只出现
    一次），属**显式偏离**，不是实现随差；偏差登记见 PR #122 正文。

    只**消费**后端给的 `state`（C20 的只读派生值），前端自己不比对时钟、不算 `fired_at − time`；
    认不出的取值（更老的后端、测试替身）落回到期时刻那一档：**不猜测**第三种语义。
    `data-state` 让 CSS 按既有档位取色（只用既有 token，不新增色位名）。 */
function reminderStateNode(reminder) {
  const stateValue = reminder.state || 'pending';
  const labelKey = Object.prototype.hasOwnProperty.call(REMINDER_STATE_LABEL_KEYS, stateValue)
    ? REMINDER_STATE_LABEL_KEYS[stateValue]
    : null;
  const node = el('span', 'state', labelKey ? t(labelKey) : reminder.time);
  node.dataset.state = stateValue;
  return node;
}

/** 一行提醒：文本（+ 归属频道）/ **状态后缀**（到期时刻 | 已提醒 | 已错过）/ 删除。
    行形态为何比原型少了行首那个 `.time`：见 :func:`reminderStateNode`。 */
function reminderRowNode(reminder) {
  const row = el('div', 'reminder-row');
  row.dataset.reminder = reminder.id;
  const grow = el('span', 'grow');
  // 正文用文本节点挂在 `.grow` 上，因为后面还要往里追加归属频道那一段
  // （`el(tag, class, text)` 是「只有一段文字」时用的；`toast()` 对同一种形态也是这么拼的）。
  grow.appendChild(document.createTextNode(reminder.text));
  if (reminder.channel_id) {
    // `· #频道名` 与原型同一形状；**不归属**时这一段不出现（可空列的正本事实）。
    grow.appendChild(el('span', 'ch', ' · #' + (reminder.channel_name || reminder.channel_id)));
  }
  row.appendChild(grow);
  row.appendChild(reminderStateNode(reminder));
  const remove = el('button', 'icon-btn', '×');
  remove.type = 'button';
  remove.dataset.deleteReminder = reminder.id;
  remove.setAttribute('aria-label', t('reminder.deleteAria'));
  remove.setAttribute('data-od-id', 'reminder-delete');
  row.appendChild(remove);
  return row;
}

/* ─── 后端状态 → 前端 ───────────────────────────────────────────────── */
// 工作台是否已**成功**载入过一轮。不能用 `state.ready`：它在失败分支也会置 true，
// 而「首载失败 → 重连成功」的那一次仍属于票面说的「首次成功载入」，必须贴底。
let workspaceLoaded = false;

async function loadWorkspace(options) {
  const settings = options || {};
  // 只有首次成功载入才把消息流停在最新一条；之后的重载（断线重连、`{ silent: true,
  // keepActive: true }` 的静默刷新）不得强制滚动——用户可能正停在历史的某个位置。
  const firstSuccessfulLoad = !workspaceLoaded;
  try {
    // 设置面先读：语言偏好与密度档位要在第一帧就是用户选的那一档。
    try {
      await loadSettings();
    } catch {
      // 设置读不到不影响工作台：保留当前镜像（源语言 + 舒适）并照常渲染主流程——
      // 「后端是否可达」由下面那条快照请求判定，不在这里抢先下结论。
    }
    const snapshot = await api('/api/workspace');
    state.member = snapshot.member;
    state.members = snapshot.members;
    state.channels = snapshot.channels;
    state.dms = snapshot.dms || [];
    state.agents = snapshot.agents || [];
    state.providers = snapshot.model_providers || [];
    state.localProviders = snapshot.local_providers || [];
    state.consents = snapshot.remote_consents || [];
    state.online = true;
    state.ready = true;
    // 清单已就位就立刻刷已打开的弹窗：下面每一步都是 `await`，任何一步抛错都会跳到 catch，
    // 而已经拿到的 provider 快照不该因为「其它请求挂了」而白白丢掉。
    syncOpenAgentModal();
    await refreshTasks();
    loadRemoteFlow();
    // 提醒的读取不阻塞主线：读不到不影响工作台（与 `loadRemoteFlow` 同一条，打开弹窗时会再试一次
    // 并在弹窗里说明），读到了圆点与列表按后端说话。
    loadReminders().catch(() => {});
    const known = state.channels.some((channel) => channel.id === state.activeChannelId)
      || state.dms.some((dm) => dm.id === state.activeChannelId);
    if (!known) {
      if (settings.keepActive === false) {
        setActiveChannelId(null);
      } else {
        const remembered = readRememberedChannelId();
        const target = state.channels.find((channel) => channel.id === remembered)
          || state.dms.find((dm) => dm.id === remembered);
        setActiveChannelId(target ? target.id : null);
      }
    }
    await refreshMessages();
    await refreshOutcomes();
    await refreshChannelMembers();
    // 频道头 pill 的读数（与任务线程同一份配置）：载入工作台时读一次，切 target 时再读一次。
    await loadActiveChannelMode();
    await refreshStatuses();
    syncStreams();
    // 审批卡的恢复与消息流同一拍：进入工作区 / 断线重连都读一次**全工作区** pending
    // （#209；全量读数已覆盖当前频道，不再另外按 target 读一次——live 帧不补发）。
    // `render: true`：刚进入工作区时消息面可能刚重建，无条件铺一次。
    await loadPendingApprovals({ render: true });
    // 整轮读数都落地了才算「成功载入」：中途任何一步抛错都会走 catch，重连那一次才补得上贴底。
    workspaceLoaded = true;
  } catch (err) {
    state.online = false;
    state.ready = true;
    applyOutcomes([]);
    state.expandedOutcomes = {};
    if (!settings.silent) toast(t('load.workspaceFailed', { reason: err.message }), 'err');
    // 后端不可达不是终点：自动重试，于是「重连后恢复」不需要人点按钮。
    // 这是唯一的本地重试定时器，且它只重读后端状态，不排队任何业务消息。
    scheduleReconnect();
  }
  render();
  // 首次成功载入：消息面刚重建，贴到最新一条。
  if (firstSuccessfulLoad && workspaceLoaded) scrollToBottom();
  // 正在查看的频道：读完快照后立即标读（刷新/重开浏览器走的就是这条路）。
  markCurrentRead();
}

let reconnectTimer = null;

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    loadWorkspace({ silent: true, keepActive: true });
  }, 1500);
}

async function refreshMessages() {
  const target = activeTarget();
  if (!target) {
    state.messages = [];
    return;
  }
  const payload = await api(targetPath(target.id, '/messages'));
  state.messages = payload.messages;
  replaceTarget(payload.dm || payload.channel);
  seenSeqsFor(target.id).clear();
  payload.messages.forEach((message) => seenSeqsFor(target.id).add(message.seq));
}

async function refreshOutcomes() {
  const channel = activeChannel();
  if (!channel) {
    applyOutcomes([]);
    return;
  }
  try {
    const payload = await api(channelPath(channel.id, '/round-outcomes'));
    applyOutcomes(payload.round_outcomes || []);
  } catch {
    // 轮次结果读不到不影响消息流（与 refreshStatuses 同一条纪律）：保留上一次已知值，
    // 也不把整个工作台判成「后端不可达」。
  }
}

// 轮次结果的两份索引，都从**同一批**后端行建、在**同一处**换，免得两份各自漂：
//   - `byBase`：按**触发它的**那条消息的 `base_seq` + 成员分组（摘要行与失败行按它挂）；
//   - `byReply`：按**它产出的**那条回复消息的 `reply_seq` 直取（旁白行展开看 reason 按它查）。
// `reply_seq` 是后端写进 `round_outcome` 的取证字段（`replied` 之外为 `null`），不是前端推断。
function applyOutcomes(records) {
  const byBase = {};
  const byReply = {};
  records.forEach((record) => {
    if (!record) return;
    if (Number.isInteger(record.base_seq)) {
      if (!byBase[record.base_seq]) byBase[record.base_seq] = {};
      const byMember = byBase[record.base_seq];
      if (!byMember[record.member_id]) byMember[record.member_id] = [];
      byMember[record.member_id].push(record);
    }
    if (Number.isInteger(record.reply_seq)) byReply[record.reply_seq] = record;
  });
  state.outcomes = byBase;
  state.replyOutcomes = byReply;
}

async function refreshStatuses() {
  try {
    const payload = await api('/api/member-statuses');
    state.statuses = payload.statuses || {};
    state.agents = state.agents.map((agent) => ({
      ...agent,
      status: state.statuses[agent.id] || agent.status,
    }));
  } catch {
    // 状态点读不到不影响消息流：保持上一次已知值，不伪造。
  }
}

function replaceChannel(channel) {
  const index = state.channels.findIndex((item) => item.id === channel.id);
  if (index >= 0) state.channels[index] = channel;
  else state.channels.push(channel);
}

/* ─── 任务看板与任务线程 ───────────────────────────────────────────────
   看板是**全工作区视图**（`CONTEXT.md` **任务看板**：可按频道筛选）——「任务恰好属于一个频道」
   是归属事实，筛选是呈现，两者不是一回事，所以「全部频道」是一个显式选项。
   频道页签是其中一个入口：从它进来时默认筛到**当前频道**（左栏视角仍是全部）。
   任务线程挂在**右侧 dock**（`dockMode`），与频道成员面板共用那个 380px 容器。 */
async function refreshTasks() {
  try {
    const query = state.taskFilterChannelId
      ? '?channel_id=' + encodeURIComponent(state.taskFilterChannelId)
      : '';
    const payload = await api('/api/tasks' + query);
    state.boardTasks = payload.tasks || [];
    // `state.tasks` 是「未筛选的全量」：频道页签计数、卡片查名、以及刷新分派的默认落点都用它。
    // 筛选态下它就是另一个查询，所以再读一次（图省一次请求而拿筛选后的清单冒充全量，
    // 会让未筛选的频道计数偏低）。
    if (state.taskFilterChannelId) {
      const all = await api('/api/tasks');
      state.tasks = all.tasks || [];
    } else {
      state.tasks = state.boardTasks.slice();
    }
    if (state.activeTaskId) {
      const current = state.tasks.find((task) => task.id === state.activeTaskId);
      if (current) state.activeTask = current;
    }
  } catch {
    // 任务面读失败不影响频道消息与发送；保留最后一次成功快照。
  }
  renderTaskBoard();
}

/** 「我的任务」：指派给我（`assignee_member_id === owner`）或还没人认领的任务。
    它**不是**「我创建的任务」——任务不记录创建者归属。 */
function isMyTask(task) {
  return !task.assignee_member_id || task.assignee_member_id === state.member.id;
}

function visibleBoardTasks() {
  return state.taskFilterMine ? state.boardTasks.filter(isMyTask) : state.boardTasks;
}

function taskMemberName(memberId) {
  const member = state.members.find((item) => item.id === memberId);
  if (member) return member.display_name;
  // 删除不给任务改历史：指派者可能已不在成员表里。判据是 `workspaceLoaded`——工作台（含成员表）
  // **成功**载入过一轮（见 `loadWorkspace`）才把「查不到」解释成「已删除」；还没成功载入时用中性的
  // 「成员」，不把仍然存在的人误标成已删除。**不能**用 `state.ready`：它在失败分支也会置 true。
  return workspaceLoaded ? t('task.deletedAssignee') : t('toast.memberFallback');
}

/** 任务在成员表里的下标（决定 `--av-*` 色位；与消息流同一个头像色位规则）。 */
function memberSlotOf(memberId) {
  const member = state.members.find((item) => item.id === memberId);
  return member ? member.avatar_slot : 0;
}

function taskById(taskId) {
  return (state.activeTask && state.activeTask.id === taskId ? state.activeTask : null) ||
    state.tasks.find((task) => task.id === taskId) ||
    state.boardTasks.find((task) => task.id === taskId) || null;
}

function renderTabs() {
  const target = activeTarget();
  const isDm = Boolean(target) && target.kind === 'dm';
  const messagesTab = $('#tabMessages');
  const tasksTab = $('#tabTasks');
  if (!messagesTab || !tasksTab) return;
  // DM 里没有任务板这个概念（任务恰好属于一个频道）：整个页签不出现，而不是点开看空态。
  tasksTab.hidden = isDm;
  // #209：当前频道下有任务 target pending ⇒ 页签旁的小圆点（aria 名由它自己承担）。
  renderTaskApprovalDot(isDm);
  const selected = state.mode === 'board' ? 'tasks' : 'messages';
  [messagesTab, tasksTab].forEach((tab) => {
    if (tab.hidden) return;
    const active = tab.dataset.tab === selected;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  // 「消息」页签计数的**唯一**写入点（#225）：派生自当前 target 的 `unread_count`（后端未读口径，
  // 与左栏未读徽标 / 任务指针 chip 同一份事实），不是消息流条数。没有 target 时按 0 写，
  // 与「任务」页签同一形态；未读为 0 也照写 0，不隐藏计数节点。
  $('#msgCount').textContent = target ? target.unread_count : 0;
  renderTaskCount();
}

/** 「任务」页签计数的**唯一**写入点。

    计数不是页签的属性，而是 `state.tasks`（未筛选的全量快照）的派生值：快照对齐了真实任务
    集合，计数就得跟着变，与「谁顺带重渲了页签」无关。因此它由 `renderTabs()`（页签重渲）
    与任务快照的渲染路径（`renderTaskBoard()` / `openTask()`）共同调用——别处不再写
    `#taskCount`，否则「任务刷新了但计数没刷新」会再一次从缝里漏出来（#177）。 */
function renderTaskCount() {
  const node = $('#taskCount');
  if (!node) return;
  const target = activeTarget();
  // DM 里没有任务板这个概念（任务恰好属于一个频道）：整个页签不出现，计数按 0 写。
  node.textContent = target && target.kind === 'dm' ? 0 : channelTaskCount();
}

/** 当前频道的任务数（该频道 = 当前 target；与看板的频道筛选无关）。 */
function channelTaskCount() {
  const channelId = state.activeChannelId;
  if (!channelId) return 0;
  return state.tasks.filter((task) => task.channel_id === channelId).length;
}

function fillTaskChannelSelect(select, includeAll, selected) {
  const options = [];
  if (includeAll) {
    const all = el('option', null, t('board.allChannels'));
    all.value = '';
    options.push(all);
  } else {
    const prompt = el('option', null, state.channels.length ? t('task.selectChannel') : t('task.createChannelFirst'));
    prompt.value = '';
    options.push(prompt);
  }
  state.channels.forEach((channel) => {
    const option = el('option', null, '#' + channel.name);
    option.value = channel.id;
    options.push(option);
  });
  replaceChildren(select, options);
  select.value = selected || '';
  if (!includeAll) select.disabled = state.channels.length === 0;
}

/** 一个筛选 chip（原型 `#filterMine` 的形态）：`.is-on` 只用 `--selected*` 族。 */
function filterChip(label, on, dataset) {
  const chip = el('button', 'filter-chip' + (on ? ' is-on' : ''));
  chip.type = 'button';
  chip.setAttribute('aria-pressed', on ? 'true' : 'false');
  Object.keys(dataset || {}).forEach((key) => { chip.dataset[key] = dataset[key]; });
  chip.textContent = label;
  return chip;
}

function renderBoardToolbar() {
  const filters = $('#boardFilters');
  if (!filters) return;
  const nodes = [
    // 「我的任务」= 指派给我或未认领（任务不记录创建者归属，所以这不是「我建的」）。
    filterChip(t('board.mine'), state.taskFilterMine, { boardMine: '1', odId: 'filter-mine' }),
    el('span', 'filter-sep'),
    filterChip(t('board.allChannels'), state.taskFilterChannelId === '', { boardChannel: '', odId: 'filter-channel-all' }),
  ];
  state.channels.forEach((channel) => {
    nodes.push(
      filterChip(
        '#' + channel.name,
        state.taskFilterChannelId === channel.id,
        { boardChannel: channel.id, odId: 'filter-channel-' + channel.id },
      ),
    );
  });
  replaceChildren(filters, nodes);
}

/** 「激活」键：Enter 与 Space（`Spacebar` 是旧版 Edge/IE 的写法）。
    卡片按钮、卡片菜单触发器、菜单项三处共用一条判据，免得三份 `key !== …` 漂移。 */
function isActivationKey(event) {
  return event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar';
}

/** 「更多」图标（lucide `more-horizontal` 同族的三点几何）：卡片流转菜单的触发器是图标按钮，
    因此图标本身不承担文案（可访问名在 `aria-label` 上）。DOM 替身没有命名空间工厂时退化成一个
    同形的点阵字符——用例断言的是行为，不是画笔。 */
function moreHorizontalIcon() {
  if (typeof document.createElementNS !== 'function') return el('span', 'card-menu-glyph', '\u00b7\u00b7\u00b7');
  const namespace = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(namespace, 'svg');
  svg.setAttribute('width', '14');
  svg.setAttribute('height', '14');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  [5, 12, 19].forEach((cx) => {
    const dot = document.createElementNS(namespace, 'circle');
    dot.setAttribute('cx', String(cx));
    dot.setAttribute('cy', '12');
    dot.setAttribute('r', '1');
    svg.appendChild(dot);
  });
  return svg;
}

/** 一张看板卡片（原型 `cardHtml()`）：标题 + mono 序号 + 频道 tag + 指派者 / 未认领。

    根节点是 `<div role="button" tabindex="0">`（#191）：它同时承载「拖拽源」与卡片内右上角的
    「流转」菜单按钮，而 `<button>` 里不能再嵌按钮。`draggable` 只在**有合法目标**时挂上——
    终态（`closed`）没有出边，摆一个拖不动的拖拽源同样是「不实现就不摆样子」。
    Enter / Space 仍打开线程（`#taskColumns` 的 keydown 委派），点击语义不变。 */
function taskCardNode(task) {
  const targets = taskAllowedTransitions(task);
  const card = el('div', targets.length ? 'card has-menu' : 'card');
  card.setAttribute('role', 'button');
  card.setAttribute('tabindex', '0');
  if (targets.length) card.setAttribute('draggable', 'true');
  card.dataset.task = task.id;
  card.dataset.status = task.status;
  card.setAttribute('data-od-id', 'task-card-' + task.id);
  // #209：卡片根是 `role="button"`，它的可访问名会**覆盖**子树；有 pending 时把条数并进
  // 根节点的 aria 名，屏幕阅读器才读得到（角标自己的 aria 名仍保留给可定位的语义面）。
  const approvalCount = taskPendingApprovalCount(task);
  card.setAttribute(
    'aria-label',
    approvalCount
      ? t('board.cardApprovalAria', {
        title: task.title,
        status: taskStatusLabel(task.status),
        count: approvalCount,
      })
      : t('board.cardAria', { title: task.title, status: taskStatusLabel(task.status) }),
  );
  card.appendChild(el('div', 'card-title', task.title));
  const meta = el('div', 'card-meta');
  meta.appendChild(el('span', 'card-num', '#' + String(task.id).slice(-6)));
  meta.appendChild(el('span', 'card-tag', '#' + task.channel_name));
  // #209：该任务 target 的 pending 条数（没有就不渲染空元素）。
  const approvalBadge = taskApprovalBadgeNode(task);
  if (approvalBadge) meta.appendChild(approvalBadge);
  if (task.assignee_member_id) {
    const owner = el('span', 'card-owner');
    owner.appendChild(avatarNode(taskMemberName(task.assignee_member_id), memberSlotOf(task.assignee_member_id), 'sm'));
    owner.appendChild(document.createTextNode(taskMemberName(task.assignee_member_id)));
    meta.appendChild(owner);
  } else {
    meta.appendChild(el('span', 'card-owner unassigned', t('task.unassigned')));
  }
  card.appendChild(meta);
  if (task.unread_count > 0) {
    const unread = el('span', 'card-unread', task.unread_count > 99 ? '99+' : task.unread_count);
    unread.setAttribute('aria-label', t('board.unreadAria', { count: task.unread_count }));
    meta.appendChild(unread);
  }
  // 终态（`closed`）没有出边 ⇒ 不摆一个点不出东西的入口（#191 D5）。
  if (targets.length) card.appendChild(taskMoveButton(task));
  return card;
}

/** 卡片右上角的「流转」图标按钮：打开共享菜单（`#taskMoveMenu`）。

    点击必须 `stopPropagation`：看板的点击委派会把 `[data-task]` 解释成「打开线程」，
    而点菜单不该连带打开线程（委派处也先判 `[data-task-menu]`，两道保险）。 */
function taskMoveButton(task) {
  const button = el('button', 'card-menu-btn');
  button.type = 'button';
  button.dataset.taskMenu = task.id;
  button.setAttribute('aria-haspopup', 'menu');
  button.setAttribute('aria-expanded', 'false');
  button.setAttribute('aria-controls', 'taskMoveMenu');
  button.setAttribute('aria-label', t('board.cardMenuAria', { title: task.title }));
  // 离线纪律与线程 chips 一致：不发得出去的写，就不摆一个能按的入口。
  button.disabled = !state.online;
  button.appendChild(moreHorizontalIcon());
  button.addEventListener('click', (event) => {
    if (event.stopPropagation) event.stopPropagation();
    openTaskMoveMenu(task.id, button);
  });
  button.addEventListener('keydown', (event) => {
    if (!isActivationKey(event)) return;
    if (event.preventDefault) event.preventDefault();
    if (event.stopPropagation) event.stopPropagation();
    openTaskMoveMenu(task.id, button);
  });
  return button;
}

/* ─── 看板卡片的「流转」菜单（#191）──────────────────────────────────────
   它是**一个共享浮层**：`position: fixed` 按触发器 rect 定位，于是不被 `.board` 的
   `overflow: auto` 裁切。菜单项**只**来自该任务的载荷 `allowed_transitions`，顺序即载荷顺序；
   文案复用既有动词词条 `task.transition.<target>`（与线程 chips 同一套说法）。 */
let taskMoveMenu = { taskId: null, trigger: null };

function taskMoveMenuNode() {
  return $('#taskMoveMenu');
}

function taskMoveMenuItems(menu) {
  // `Array.from`：`querySelectorAll` 给的是 NodeList（有 `forEach`、没有 `indexOf`），
  // 键盘面要按索引取项、判当前焦点，必须先在**一份快照数组**上做。
  return menu && menu.querySelectorAll ? Array.from(menu.querySelectorAll('[role="menuitem"]')) : [];
}

/** 把焦点放到第 `index` 项（负数与越界都按环绕取模：↑ 从第一项回到最后一项）。 */
function focusTaskMoveItem(menu, index) {
  const items = taskMoveMenuItems(menu);
  if (!items.length) return;
  const node = items[((index % items.length) + items.length) % items.length];
  if (node && node.focus) node.focus();
}

function closeTaskMoveMenu(restoreFocus) {
  const trigger = taskMoveMenu.trigger;
  taskMoveMenu = { taskId: null, trigger: null };
  const menu = taskMoveMenuNode();
  if (menu) {
    menu.hidden = true;
    if (menu.replaceChildren) replaceChildren(menu, []);
    if (menu.dataset) delete menu.dataset.task;
  }
  if (trigger) {
    trigger.setAttribute('aria-expanded', 'false');
    if (restoreFocus && trigger.focus) trigger.focus();
  }
}

/** 按触发器 rect 摆菜单：下方优先，空间不足翻到上方；右缘对齐触发器右缘。 */
function positionTaskMoveMenu(menu, trigger) {
  if (!trigger.getBoundingClientRect) return;
  const rect = trigger.getBoundingClientRect();
  if (!rect) return;
  const gap = 6;
  const viewportHeight = (window && window.innerHeight) || 0;
  const menuHeight = menu.getBoundingClientRect ? menu.getBoundingClientRect().height : 0;
  const flip = viewportHeight > 0 && menuHeight > 0 && rect.bottom + gap + menuHeight > viewportHeight;
  menu.style.position = 'fixed';
  menu.style.top = (flip ? Math.max(gap, rect.top - gap - menuHeight) : rect.bottom + gap) + 'px';
  menu.style.right = ((window && window.innerWidth ? window.innerWidth : rect.right) - rect.right) + 'px';
  menu.style.left = 'auto';
}

function openTaskMoveMenu(taskId, trigger) {
  const task = taskById(taskId);
  const targets = taskAllowedTransitions(task);
  const menu = taskMoveMenuNode();
  if (!task || !targets.length || !state.online || !menu) return;
  closeTaskMoveMenu(false);
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', t('board.cardMenuLabel'));
  replaceChildren(menu, targets.map((target) => {
    const item = el('div', 'card-menu-item', taskTransitionLabel(target));
    item.setAttribute('role', 'menuitem');
    item.setAttribute('tabindex', '-1');
    item.dataset.moveTarget = target;
    item.addEventListener('click', (event) => {
      if (event.stopPropagation) event.stopPropagation();
      closeTaskMoveMenu(false);
      changeTaskStatus(taskId, target);
    });
    return item;
  }));
  menu.dataset.task = taskId;
  menu.hidden = false;
  taskMoveMenu = { taskId, trigger };
  if (trigger) trigger.setAttribute('aria-expanded', 'true');
  positionTaskMoveMenu(menu, trigger);
  focusTaskMoveItem(menu, 0);
}

/** 菜单的键盘面：↑/↓ 循环、Esc 关闭并还焦点、Enter / Space 激活当前项。
    菜单项是 `<div role="menuitem">`（不是 `<button>`）：于是**只有**这一条激活路径，
    不会出现「键盘一次、原生 click 再一次」的双写。 */
function onTaskMoveMenuKeydown(event) {
  const menu = taskMoveMenuNode();
  if (!menu || menu.hidden) return;
  const items = taskMoveMenuItems(menu);
  const active = document.activeElement;
  const current = items.indexOf(active);
  if (event.key === 'Escape') {
    if (event.preventDefault) event.preventDefault();
    closeTaskMoveMenu(true);
    return;
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    if (event.preventDefault) event.preventDefault();
    focusTaskMoveItem(menu, current + (event.key === 'ArrowDown' ? 1 : -1));
    return;
  }
  if (!isActivationKey(event)) return;
  const item = current >= 0 ? items[current] : null;
  if (!item) return;
  if (event.preventDefault) event.preventDefault();
  const target = item.dataset.moveTarget;
  const taskId = menu.dataset.task;
  closeTaskMoveMenu(false);
  changeTaskStatus(taskId, target);
}

/** 点外部 / 点看板其它地方关闭：触发器与菜单自身不算「外部」。 */
function onDocumentClickCloseTaskMoveMenu(event) {
  if (!taskMoveMenu.taskId) return;
  const target = event.target;
  if (target && target.closest && (target.closest('#taskMoveMenu') || target.closest('[data-task-menu]'))) return;
  closeTaskMoveMenu(false);
}

/* ─── 看板拖拽（#191 D3/D4）：拖拽服务指针设备，卡片菜单服务触屏与键盘，
   两者命中同一条受守卫的写路径（`PATCH /api/tasks/{id}/status`），不做第二套语义。
   落点判定**只读**载荷 `allowed_transitions`；非法松手零请求。

   `suppressClickUntil`：拖拽结束后浏览器仍可能补一个 `click`（不同内核时机不同），
   于是「拖完顺手打开了线程」。给点击委派一个短窗口，让「拖拽不触发点击」成为显式事实。 */
const boardDrag = { taskId: null, suppressClickUntil: 0 };

function boardColumnsWithState() {
  const columns = $('#taskColumns');
  return columns && columns.querySelectorAll ? columns.querySelectorAll('.col') : [];
}

function clearBoardDropStates() {
  boardColumnsWithState().forEach((column) => {
    column.classList.remove('drag-over');
    column.classList.remove('invalid-over');
  });
  const columns = $('#taskColumns');
  const dragging = columns && columns.querySelectorAll ? columns.querySelectorAll('.dragging') : [];
  dragging.forEach((card) => card.classList.remove('dragging'));
}

function onBoardDragStart(event) {
  const card = event.target && event.target.closest ? event.target.closest('[data-task]') : null;
  if (!card) return;
  const task = taskById(card.dataset.task);
  // 没有合法目标（终态）的卡片不是拖拽源：它连 `draggable` 都不挂，这里再守一次。
  if (!task || !state.online || !taskAllowedTransitions(task).length) return;
  closeTaskMoveMenu(false);
  boardDrag.taskId = task.id;
  boardDrag.suppressClickUntil = 0;
  card.classList.add('dragging');
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move';
    if (event.dataTransfer.setData) event.dataTransfer.setData('text/plain', task.id);
  }
}

function onBoardDragOver(event) {
  const column = event.target && event.target.closest ? event.target.closest('.col') : null;
  if (!column || !boardDrag.taskId) return;
  const task = taskById(boardDrag.taskId);
  if (!task) return;
  // 允许 drop：非法落点也要收到 drop，才能给「不是合法迁移」的说明（零请求）。
  if (event.preventDefault) event.preventDefault();
  const target = column.dataset.status;
  const sameColumn = target === task.status;
  const legal = !sameColumn && taskAllowedTransitions(task).includes(target);
  boardColumnsWithState().forEach((other) => {
    const on = other === column;
    other.classList.toggle('drag-over', on && legal);
    other.classList.toggle('invalid-over', on && !sameColumn && !legal);
  });
}

/** 指针离开看板（不是在看板内部换列）时清掉落点态：否则最后一列会一直亮着，
    直到有人松手或拖拽结束——那是一个没有行为支撑的「落点」。 */
function onBoardDragLeave(event) {
  const columns = $('#taskColumns');
  const next = event.relatedTarget;
  if (next && columns) {
    if (columns.contains && columns.contains(next)) return;
    if (!columns.contains && next.closest && next.closest('#taskColumns')) return;
  }
  boardColumnsWithState().forEach((column) => {
    column.classList.remove('drag-over');
    column.classList.remove('invalid-over');
  });
}

function onBoardDrop(event) {
  const column = event.target && event.target.closest ? event.target.closest('.col') : null;
  const fromTransfer = event.dataTransfer && event.dataTransfer.getData ? event.dataTransfer.getData('text/plain') : '';
  const taskId = boardDrag.taskId || fromTransfer || '';
  if (!column || !taskId) return;
  if (event.preventDefault) event.preventDefault();
  const task = taskById(taskId);
  clearBoardDropStates();
  boardDrag.taskId = null;
  // drop 之后内核可能补一个 click：从现在起短窗口内不把点击当成「打开线程」。
  boardDrag.suppressClickUntil = Date.now() + 250;
  if (!task || !state.online) return;
  const target = column.dataset.status;
  // 拖回原列：无动作（既不是迁移，也不该给一句「非法」的指责）。
  if (!target || target === task.status) return;
  if (!taskAllowedTransitions(task).includes(target)) {
    // D4：非法落点**零写入**，只说明事实。
    toast(t('toast.transitionInvalid', {
      from: taskStatusLabel(task.status),
      to: taskStatusLabel(target),
    }), 'err');
    return;
  }
  changeTaskStatus(task.id, target);
}

function onBoardDragEnd() {
  clearBoardDropStates();
  boardDrag.taskId = null;
  boardDrag.suppressClickUntil = Date.now() + 250;
}

/** 看板列（`TASK_STATUS_ORDER` 五态）：固定 `--board-col-w`(236px) → 超出时容器横向滚动。 */
function boardColumns() {
  const tasks = visibleBoardTasks();
  return TASK_STATUS_ORDER.map((status) => {
    const items = tasks.filter((task) => task.status === status);
    const column = el('section', 'col');
    column.dataset.status = status;
    column.setAttribute('data-od-id', 'task-column-' + status);
    const head = el('div', 'col-head');
    head.appendChild(el('span', 'st-dot'));
    head.appendChild(el('b', null, taskStatusLabel(status)));
    head.appendChild(el('span', 'col-count', String(items.length)));
    column.appendChild(head);
    const body = el('div', 'col-body');
    if (items.length) items.forEach((task) => body.appendChild(taskCardNode(task)));
    // 空态只陈述事实（「这一列还没有任务」）：拖拽入口由卡片承担，落点提示不是必需文案。
    else body.appendChild(el('div', 'drop-hint', t('board.emptyColumn')));
    column.appendChild(body);
    return column;
  });
}

/** 列表视图：`.seg` 的另一半，`max-width:880px` 居中（原型 `#taskListWrap`）。 */
function boardList() {
  const tasks = visibleBoardTasks();
  if (!tasks.length) {
    const empty = el('div', 'empty');
    empty.appendChild(el('b', null, t('board.noMatchTitle')));
    empty.appendChild(el('p', null, t('board.noMatchBody')));
    return empty;
  }
  const wrap = el('div');
  tasks.forEach((task) => {
    const row = el('button', 'result');
    row.type = 'button';
    row.dataset.task = task.id;
    row.setAttribute('data-od-id', 'task-row-' + task.id);
    const rowApprovalCount = taskPendingApprovalCount(task);
    row.setAttribute(
      'aria-label',
      rowApprovalCount
        ? t('board.cardApprovalAria', {
          title: task.title,
          status: taskStatusLabel(task.status),
          count: rowApprovalCount,
        })
        : t('board.cardAria', { title: task.title, status: taskStatusLabel(task.status) }),
    );
    const top = el('div', 'r-top');
    top.appendChild(el('span', 'st-dot'));
    top.appendChild(el('span', null, taskStatusLabel(task.status)));
    top.appendChild(el('span', 'hash', '#' + String(task.id).slice(-6)));
    top.appendChild(el('span', 'card-tag', '#' + task.channel_name));
    top.appendChild(el('span', 'r-open', t('board.openThread')));
    row.appendChild(top);
    row.appendChild(el('div', 'r-title', task.title));
    const snip = el('div', 'r-snip');
    if (task.assignee_member_id) {
      snip.appendChild(avatarNode(taskMemberName(task.assignee_member_id), memberSlotOf(task.assignee_member_id), 'sm'));
      snip.appendChild(document.createTextNode(taskMemberName(task.assignee_member_id)));
    } else {
      snip.appendChild(el('span', 'card-owner unassigned', t('task.unassigned')));
    }
    // #209：列表行与卡片同一个角标（归属只看 task.target_id）。
    const approvalBadge = taskApprovalBadgeNode(task);
    if (approvalBadge) snip.appendChild(approvalBadge);
    if (task.unread_count > 0) {
      const unread = el('span', 'card-unread', task.unread_count);
      unread.setAttribute('aria-label', t('board.unreadAria', { count: task.unread_count }));
      snip.appendChild(unread);
    }
    row.appendChild(snip);
    row.dataset.status = task.status;
    wrap.appendChild(row);
  });
  return wrap;
}

function renderTaskBoard() {
  // 浮层指向的是**这一版**卡片的 rect 与目标集合：重铺看板前先收起菜单（#191 约束 6），
  // 于是 SSE / 刷新造成的重渲染不会留下一个对不上任何卡片的旧菜单。
  closeTaskMoveMenu(false);
  // 看板与页签计数读的是**同一份**全量快照：重铺看板的地方顺带把计数重算一遍，
  // 于是 `state.tasks` 的就地替换路径（`markTaskRead()` / `refreshTaskDetails()`）
  // 不必各自再记得写一次 `#taskCount`。
  renderTaskCount();
  const columns = $('#taskColumns');
  const list = $('#taskListWrap');
  if (!columns || !list) return;
  renderBoardToolbar();
  const creating = $('#taskCreateSubmit');
  if (creating) creating.disabled = !state.online || state.channels.length === 0;
  const createChannel = $('#taskCreateChannel');
  if (createChannel) {
    // 新建任务的默认频道：当前频道优先（人在这个频道里按「新建任务」，就该建在这里）。
    const selected = createChannel.value || state.activeChannelId || '';
    fillTaskChannelSelect(createChannel, false, selected);
  }
  const isList = state.taskView === 'list';
  columns.hidden = isList;
  list.hidden = !isList;
  if (isList) {
    replaceChildren(list, [boardList()]);
    return;
  }
  replaceChildren(columns, boardColumns());
}

/** `.seg` 的选中态与 `aria-selected` 只在**一处**同步（切换与重渲染都走它）。 */
function syncTaskViewSeg() {
  document.querySelectorAll('.seg [data-taskview]').forEach((button) => {
    const on = button.dataset.taskview === state.taskView;
    button.classList.toggle('is-active', on);
    button.setAttribute('aria-selected', on ? 'true' : 'false');
  });
}

/** 任务线程（dock 形态 B，原型的 `.tt-*`）。

    「讨论」与「状态变更记录」是**同一个数据源的两个视图**：后端把每次任务变更
    （创建 / 标题 / 状态 / 指派）追加成一条 `kind='system'` 消息进该 task 的 target 消息流
    （`CONTEXT.md` **任务**、ADR-0006）。`kind='system'` 的那些行进「状态变更记录」，
    其余（人类 / Agent 主动发起的）全部进「讨论」；带 `task_ref` 的指针消息就在讨论里
    以 `.task-ref-chip` 就地显示（`CONTEXT.md` **指针消息**），不另开一段重复的正文。

    #221：线程输入框发送时必然带 `task_ref`，所以「带 `task_ref` 的非 system 消息」
    恒为「讨论」的子集，不可能承载独有信息——原先那一段「关联消息」因此去掉，
    头部副行的条数改用「讨论」的计数（与 `#ttReplyTitle` 同一份）。

    原型那个伪造的 `TASK_HISTORY` 常量不移植：历史就是这一条流。 */
function renderTaskThread() {
  const task = state.activeTask;
  if (!task) return;
  const channelName = task.channel_name;
  const history = state.taskMessages.filter((message) => message.kind === 'system');
  const systemIds = new Set(history.map((message) => message.id));
  const replies = state.taskMessages.filter((message) => !systemIds.has(message.id));

  // ── dock 头部：「任务线程」tag + mono #id + 「#频道名 · 讨论 N 条」──
  $('#ttHeadId').textContent = '#' + String(task.id).slice(-6);
  const unread = Number(task.unread_count) || 0;
  $('#ttHeadSub').textContent = t('task.threadHeadSub', { channel: channelName, count: replies.length })
    + (unread ? t('task.threadUnread', { count: unread }) : '');

  // ── 摘要：标题（就地可编辑）+ 状态 pill + 指派 pill ──
  const titleInput = $('#ttTitleInput');
  if (document.activeElement !== titleInput) titleInput.value = task.title;
  const status = $('#ttStatus');
  // 状态色位由 CSS 的 `.tt-status[data-status=…]` 表驱动（与 `.col` / `.card` 共用同一张表），
  // 这里只声明状态，不内联色位：同一个映射存在两份就会漂移。
  status.dataset.status = task.status;
  $('#ttStatusText').textContent = taskStatusLabel(task.status);
  renderAssigneePill(task);

  // ── team 频道的事实提示（#154 / ADR-0018）：任务线程只唤醒队长。
  //    配置来自 `state.channelConfigs`（`loadTaskChannelConfig` 写入，正本是后端）；
  //    autonomous 下整块隐藏，不用一句「和平时一样」占位。
  const teamNotice = $('#ttTeamNotice');
  if (teamNotice) {
    const channelConfig = state.channelConfigs[task.channel_id];
    const team = Boolean(channelConfig && channelConfig.mode === 'team');
    teamNotice.hidden = !team;
    teamNotice.textContent = team ? t('task.teamNotice') : '';
  }

  // ── 推进 chips：**只**出现载荷 `allowed_transitions` 里的目标（`closed` 是终态 ⇒ 一个都不出）──
  const transitions = taskAllowedTransitions(task);
  if (transitions.length) {
    replaceChildren($('#ttTransitions'), transitions.map((target) => {
      const chip = filterChip(
        taskTransitionLabel(target),
        false,
        { ttMove: target, odId: 'thread-move-' + target },
      );
      chip.disabled = !state.online;
      return chip;
    }));
  } else {
    // 原型那句「已关闭，无可用推进」在这里是**真话**（`closed` 确实是终态）。
    replaceChildren($('#ttTransitions'), [el('span', 'tt-note', t('task.noTransitions'))]);
  }

  // ── 指派 chips：该频道成员里的 Agent（见 `renderAssignChips` 的候选来源）──
  renderAssignChips(task);

  // ── 讨论 · N 条 ──
  $('#ttReplyTitle').textContent = t('task.discussionCount', { count: replies.length });
  replaceChildren(
    $('#ttReplies'),
    replies.length
      ? replies.map((message) => {
        const row = el('div', 'tt-reply-row');
        const author = message.author || state.member;
        row.appendChild(avatarNode(author.display_name, author.avatar_slot, 'sm'));
        const body = el('div', 'msg-body');
        const head = el('div', 'msg-head');
        head.appendChild(el('span', 'msg-author' + (author.kind === 'agent' ? ' is-agent' : ''), author.display_name));
        if (author.kind === 'agent') head.appendChild(el('span', 'msg-tag', 'Agent'));
        head.appendChild(el('span', 'msg-time', timeLabel(message.created_at)));
        body.appendChild(head);
        // 正文直接用消息流同一套渲染（`.msg-text`，ADR-0016 的 14px / 68ch）：
        // 提及高亮、换行与量例都与主线一致，不另造一套（也不多套一层壳）。
        body.appendChild(messageText(message));
        finishedOutcomeNodes(Object.values(state.taskOutcomes).filter((record) => record.base_seq === message.seq))
          .forEach((node) => body.appendChild(node));
        appendAttachmentList(body, message);
        appendTaskRefChip(body, message);
        row.appendChild(body);
        return row;
      })
      : [el('div', 'tt-empty', t('task.repliesEmpty'))],
  );

  // ── 状态变更记录：该流的 `system` 行（不另行伪造历史）──
  const log = $('#ttHistory');
  replaceChildren(
    log,
    history.length
      ? history.map((message) => {
        const li = el('li');
        li.appendChild(document.createTextNode((message.author ? message.author.display_name + ' ' : '') + message.body));
        li.appendChild(el('span', 'ts', timeLabel(message.created_at)));
        finishedOutcomeNodes(Object.values(state.taskOutcomes).filter((record) => record.base_seq === message.seq))
          .forEach((node) => li.appendChild(node));
        return li;
      })
      : [el('div', 'tt-empty', t('task.historyEmpty'))],
  );
  // 最近一次变更用 graphic 档的点标出来（原型 `.is-now`；不是新色位）。
  const last = log.lastElementChild;
  if (last && last.tagName === 'LI') last.classList.add('is-now');
  // 任务线程的审批卡按 task target 铺；频道那份不会串进来。
  renderTaskApprovals();
  renderTyping();
}

/** 指派 pill：点击展开 / 收起下方的指派 chips（原型：pill 只提示，chips 才是入口）。 */
function renderAssigneePill(task) {
  const pill = $('#ttAssignee');
  const assigned = task.assignee_member_id;
  if (!assigned) {
    pill.className = 'assignee unassigned';
    pill.textContent = t('task.unassignedPill');
    return;
  }
  pill.className = 'assignee';
  replaceChildren(pill, []);
  pill.appendChild(avatarNode(taskMemberName(assigned), memberSlotOf(assigned), 'sm'));
  pill.appendChild(document.createTextNode(taskMemberName(assigned)));
  pill.appendChild(el('span', 'assignee-hint', t('task.reassignHint')));
}

/** 指派 chips：**该任务所属频道的 Agent 成员**。

    为什么不拿 `GET /api/tasks/{id}/members`（target 成员）当候选：那张表 = {owner} ∪ {当前指派者}
    （`create_task` 只写入 owner，`update_task_assignee` 随后把被指派者加进去）——
    拿它当候选就永远只能「取消指派」，换不了人。票面写的就是「指派 chip 用该频道成员里的 Agent」。
    后端写路径不限制候选必须已在频道里（只校「未删除的 Agent」），所以频道成员是
    这里的正确候选面；线程成员表仍用于判断「该 target 现有哪些 Agent」。 */
function renderAssignChips(task) {
  const channelAgents = channelAgentMembers(task.channel_id);
  const box = $('#ttAssign');
  if (channelAgents === null) {
    replaceChildren(box, [
      el('span', 'tt-note', channelMembersFailed.has(task.channel_id)
        ? t('task.assignUnavailable')
        : t('task.assignLoading')),
    ]);
    return;
  }
  if (!channelAgents.length) {
    replaceChildren(box, [
      el('span', 'tt-note', t('task.assignNoAgents')),
    ]);
    return;
  }
  const chips = channelAgents.map((member) => {
    const chip = filterChip(member.display_name, task.assignee_member_id === member.id, {
      ttAssign: member.id,
      odId: 'thread-assign-' + member.id,
    });
    chip.insertBefore(avatarNode(member.display_name, member.avatar_slot, 'sm'), chip.firstChild);
    chip.disabled = !state.online;
    return chip;
  });
  if (task.assignee_member_id) {
    const clear = filterChip(t('task.clearAssign'), false, { ttAssign: '', odId: 'thread-unassign' });
    clear.disabled = !state.online;
    chips.push(clear);
  }
  replaceChildren(box, chips);
}

/** dock 形态切换：频道成员（`agent`）↔ 任务线程（`task`）↔ 运行面板（`run`，#155）。

    与消息流不在同一个维度：看板仍在主线（`state.mode === 'board'`），
    而任务线程 / 运行面板只是 dock 里显示什么。三种形态共用同一个 380px
    容器，因此「同时显示两件东西」在构造上不可能发生（一次只有一种为可见）。

    Agent 详情**不是** dock 的形态（用户 2026-10-22 裁定）：它占据整条右侧，是主线视图
    `state.mode === 'agent'`，dock 在那种视图下让位（见 `setViewMode()`）。

    形态本身**不单独存一份**：它由「现在打开着哪一个对象」推导（`runPanel.open` 优先于
    `activeTaskId`，再回落到频道成员），于是「关闭了但 dock 还停在旧形态」这类
    漂移没有落脚点。

    `reveal` = 这一次是**人显式的打开**（点看板卡片）：它清掉
    `dockDismissed`。整体重绘（`render()`）走默认的 `false`，于是人按过的收起不会被下一次
    重绘推翻 —— 「看起来收起了、下一帧又回来」等于没有出口。 */
function setDockMode(mode, reveal) {
  state.dockMode = mode;
  if (reveal) state.dockDismissed = false;
  const isTask = mode === 'task' && state.activeTaskId;
  const isRun = mode === 'run' && state.runPanel.open;
  $('#dockAgent').hidden = Boolean(isTask || isRun);
  $('#dockTask').hidden = !isTask;
  $('#dockRun').hidden = !isRun;
  // 滑入放在**形态定下来之后**：≤900px 打开抽屉要把焦点送进第一个可聚焦控件，
  // 而那一步要跳过 `hidden` 子树 —— 先开抽屉会把焦点送给一个还在 `hidden` 里的控件。
  if ((isTask || isRun) && !state.dockDismissed) setDockOpen(true);
}

/** 主线视图的唯一裁决点：消息流 / 看板 / 搜索 / **Agent 详情**四选一。

    Agent 详情是第四种主线视图（不是 dock 的形态）：它在时整条右侧归它，dock 隐藏；
    离开它走左栏行或键盘 M / T（`leaveAgentView()`，不恢复 dock 开合）；只有删除成功那条
    `closeAgentDetail()` 会按 `agentReturn` 恢复进入前的视图与 dock 开合。 */
function setViewMode(mode) {
  state.mode = mode;
  syncChromeVisibility(mode);
  $('#viewMessages').hidden = mode !== 'channel';
  // 搜索是**主线视图**，与消息流 / 看板共用同一块内容区（原型 `renderViews()` 的三选一）。
  $('#viewSearch').hidden = mode !== 'search';
  // 看板是**唯一**的任务主线视图：任务线程不再占用主线（它搬进了 dock）。
  // dock 的形态与主线视图是两个维度（原型 `renderDock()` 同样独立于 `renderViews()`）：
  // 从频道消息里的任务 chip 打开线程时，主线仍是消息流，dock 里就是那条线程。
  $('#taskBoard').hidden = mode !== 'board';
  // Agent 详情占据整条右侧：它可见时 dock 让位（两种「占右侧」的呈现不同时出现）。
  // `hidden` 与 `.is-open` 同进同出，因此这里走 `setDockOpen()` 而不是直接写 `hidden`；
  // 进入前的开合由 `state.agentReturn.dockOpen` 记住，关闭详情时按它恢复。
  $('#viewAgent').hidden = mode !== 'agent';
  if (mode === 'agent') setDockOpen(false);
  renderTabs();
}

/** 频道 header（含「消息 / 任务」页签）与 composer 的可见性。

    两个页签在**同一内容区**切换，因此 header 在消息页与任务页**都**可见——原型
    `renderViews()` 写的是 `$('#chanHead').hidden = search;`，只在搜索视图隐藏它，
    任务视图下 header 与页签本身就是可见的。收起 header 等于把页签一起收走。
    composer 只属于消息页（原型同理：`$('#composer').hidden = search || tasks`）。
    搜索与 Agent 详情都不是「某个频道 / DM 的内容」：前者自己铺满，后者占整条右侧，
    频道头（标题 + 页签）在这两种视图下一起让位。
    两处调用（`setViewMode` 与 `renderHeader`）共用这一条规则，不各写一遍。 */
function syncChromeVisibility(mode) {
  const hasTarget = Boolean(activeTarget());
  // Agent 详情占据整条右侧：频道头（含消息 / 任务页签）与 composer 一起让位，
  // 否则「整页详情」下面还压着一条别的对象的标题栏。
  $('#chanHead').hidden = !hasTarget || mode === 'search' || mode === 'agent';
  // composer 只在消息页出现；搜索、看板、Agent 详情都不带输入框。
  $('#composer').hidden = !hasTarget || mode !== 'channel';
}

/** 「任务」页签：看板（全工作区视图 + 频道筛选，见 #44 重做其结构）。

    从频道页签进来默认筛到**当前频道**（人在 #甲 的任务里，就该先看 #甲 的任务）。
    看板本身仍是全工作区视图：工具栏上的「全部频道」chip 回到全部任务，不靠入口区分。 */
async function showTaskBoard() {
  closeTaskStream();
  closeTaskDock();
  // 切主线视图即离开详情（丢弃草稿，不弹二次确认）：详情是主线视图的第四种。
  leaveAgentView();
  state.mode = 'board';
  state.taskFilterChannelId = state.activeChannelId || '';
  setViewMode('board');
  render();
  await refreshTasks();
}

/** 「消息」页签：消息流 + composer（同一条流的既有渲染，不重取—— SSE 一直在线跟踪）。
    它也是搜索视图的退出口（`ESC` 与命令面板的「消息视图」都落到这里）。 */
function showMessagesView() {
  closeTaskStream();
  closeTaskDock();
  leaveAgentView();
  state.mode = 'channel';
  setViewMode('channel');
  render();
  markCurrentRead();
}

/* ─── 移动端抽屉壳（≤900px）：左抽屉（rail）/ 遮罩 / 右抽屉（dock） ──────────
   类名与断点照原型 `alibot-app.html:459-495` 与切片 `screens/mobile-shell.html:14-18`，
   规格决策 D11 把 `.mobile-topbar` / `.rail-scrim` / `.is-open` 三个名字定为契约。

   呈现态只有一个正本：`.is-open`（CSS 在 ≤900px 认它）。`hidden` 保留它原本的语义
   ——`rail` 从来没有这个属性，`dock` 的 `hidden` 是「这里没有内容 / 人把它收起来了」；
   ≤900px 下它由 CSS 改写成「离屏 + 不可见」，于是开着没有只看那一个类。

   键盘可达：抽屉在 ≤900px 关着时是 `visibility: hidden` 的离屏件，Tab 命不中它；
   打开时把焦点送进抽屉的第一个控件、收起时还给触发它的控件，于是焦点不会停在
   一个屏幕外、看不见的控件上。这一条只在移动壳里做（`mobileShell()`），
   桌面的焦点行为一个字不动。 */

/** 移动壳是否生效。这里是 JS 侧唯一知道那个断点的地方，取值必须与 `app.css` 的
    `@media (max-width: 900px)` 一致 —— 它只用来决定「要不要替抽屉搬焦点」。
    （节点本身在不在屏幕上、可不可见，一律由 CSS 裁决，JS 不镜像一套。） */
const MOBILE_SHELL_QUERY = '(max-width: 900px)';

function mobileShell() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return Boolean(window.matchMedia(MOBILE_SHELL_QUERY).matches);
}

function focusNode(node) {
  if (node && typeof node.focus === 'function') node.focus();
}

/** 抽屉里第一个能接焦点的控件（跳过 `hidden` 子树与禁用件）。 */
function firstFocusable(root) {
  if (!root || typeof root.querySelectorAll !== 'function') return null;
  const nodes = root.querySelectorAll('button, a[href], input, select, textarea, [tabindex]');
  for (const node of nodes) {
    if (node.disabled) continue;
    if (typeof node.closest === 'function' && node.closest('[hidden]')) continue;
    return node;
  }
  return null;
}

/** 焦点现在在不在这个容器里（收起抽屉时据此决定要不要把焦点还回去）。 */
function focusInside(container) {
  const active = typeof document !== 'undefined' ? document.activeElement : null;
  if (!active || !container || typeof container.contains !== 'function') return false;
  return container.contains(active);
}

function railOpen() {
  const rail = $('#rail');
  return Boolean(rail && rail.classList.contains('is-open'));
}

/** 开左抽屉：`.is-open` + 遮罩出 + `aria-expanded=true`（三者同步，不靠人记）。 */
function openRail() {
  const rail = $('#rail');
  if (!rail) return;
  rail.classList.add('is-open');
  const scrim = $('#railScrim');
  if (scrim) scrim.hidden = false;
  const toggle = $('#railToggle');
  if (toggle) toggle.setAttribute('aria-expanded', 'true');
  if (mobileShell()) focusNode(firstFocusable(rail));
}

/** 收左抽屉（scrim 点击 / ESC / 选中一个导航行共用它）。 */
function closeRail() {
  const rail = $('#rail');
  if (!rail) return;
  rail.classList.remove('is-open');
  const scrim = $('#railScrim');
  if (scrim) scrim.hidden = true;
  const toggle = $('#railToggle');
  if (toggle) toggle.setAttribute('aria-expanded', 'false');
  if (mobileShell()) focusNode(toggle);
}

/** dock 的开合：`hidden` 与 `.is-open` 必须同进同出（票面：状态与类名同步），
    因此所有写 `dock.hidden` 的地方都走这里，不各写一遍。
    「人收起来了」这一位也在这里落 —— 它是 `setDockMode()` 自动显示时要看的那一位。 */
function setDockOpen(open) {
  const dock = $('#dock');
  if (!dock) return;
  const wasOpen = !dock.hidden;
  dock.hidden = !open;
  dock.classList.toggle('is-open', open);
  state.dockDismissed = !open;
  const toggle = $('#btnDockToggle');
  if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (!mobileShell()) return;
  if (open && !wasOpen) focusNode(firstFocusable(dock));
  else if (!open && wasOpen && focusInside(dock)) focusNode(toggle);
}

/** 详情面板的开合（rail 图标与命令面板里的「切换详情面板」共用同一个动作，不各写一遍）。 */
function toggleDock() {
  // 频道头开关 / 命令面板 / 移动端抽屉共用同一个动作：没有可显示的形态（私信）时无效。
  if (!dockAvailable()) return;
  const dock = $('#dock');
  if (dock) setDockOpen(dock.hidden);
}

/** 关闭 dock 里的某一个形态之后**落到哪里**——`closeTaskDock()` / `closeRunPanel()` 共用这一份判据。

    顺序：仍打开着任务线程 → 任务线程形态；当前有打开的频道 → 频道成员形态；否则 → `null`（收起 dock）。

    「当前有打开的频道」的唯一判据是既有的 `activeChannel()`：`state.channels` 里 id 等于
    `state.activeChannelId` 的那一项。于是「没有任何 target」「只打开着 DM」「`activeChannelId`
    指向一个已经不存在的频道」这三种情况**同样**落到 `null` —— 没有频道就没有「频道成员」形态，
    不把成员面板留在右侧（用户 2026-10-16 的裁定）。

    Agent 详情不再是 dock 的形态（用户 2026-10-22 裁定），因此这里看不到 `activeAgentId`：
    打开详情时 dock 整个让位，关闭详情由 `closeAgentDetail()` 恢复进入前的开合。 */
function dockFallbackMode() {
  if (state.activeTaskId) return 'task';
  if (activeChannel()) return 'agent';
  return null;
}

/** dock 当前有没有**可显示的形态**。判据是形态而不是 `isDm` 一刀切：
    「成员」形态只属于频道；任务线程 / 运行面板是独立形态，私信下仍算可用，
    因此线程 / 运行面板开着时入口还必须留着。私信且两者都没有 → 没有可显示的东西。 */
function dockAvailable() {
  return Boolean(state.runPanel.open) || dockFallbackMode() !== null;
}

/** 两条关闭路径共用的收尾：有落点就切到那个形态，没有落点就收起 dock。
    收起时**形态仍落回「频道成员」**，于是人下一次显式点开开关（`toggleDock()`）看到的是
    `renderDock()` 的空态提示，而不是一帧过期的任务线程 / 运行面板。 */
function settleDockFallback() {
  const mode = dockFallbackMode();
  setDockMode(mode || 'agent');
  if (!mode) setDockOpen(false);
}

/** 关闭 dock 的任务线程，回到**另一个仍打开着**的形态（卡片与线程都留在看板上）。

    落点判据与关闭运行面板共用（`settleDockFallback()`），但**只在这次确实在关一条线程时**才走它：
    切搜索 / 切看板 / 切消息流（ESC / M / T / 命令面板）也调这个函数，那些调用点只是「顺手确保
    线程关着」，并没有人在关 dock 里的东西 —— 它们的既有行为是**不碰 dock 的开合**（桌面 dock 是
    常驻列，ESC 不管它），因此这里原样保留。

    于是「有频道 → 频道成员；否则收起」这条规则只落在**真的**在关闭的入口上：
    `#btnThreadClose`（以及 `openTask()` 的失败回退）与 `#btnRunPanelClose`。 */
function closeTaskDock() {
  closeTaskStream();
  // 运行面板若是从任务线程打开的，它的 target 随线程一起失效（频道面板不受影响）。
  if (state.runPanel.open && state.runPanel.taskId) resetRunPanelState();
  const hadThread = Boolean(state.activeTaskId);
  state.activeTaskId = null;
  state.activeTask = null;
  state.taskMessages = [];
  state.taskOutcomes = {};
  state.taskSeenSeqs = new Set();
  if (hadThread) settleDockFallback();
  else setDockMode('agent');
}

function taskMaxSeq() {
  return state.taskMessages.reduce((max, message) => Math.max(max, Number(message.seq) || 0), 0);
}

/** 点看板卡片：在**右侧 dock** 开任务线程（主线仍留在看板上）。

    打开线程会**关掉** Agent 详情：dock 一次只显示一种形态，两者同时「打开着」会让关闭时的
    落点变成猜谜。 */
async function openTask(taskId) {
  closeTaskStream();
  // 另一条任务的运行面板不属于这条线程：打开线程时一并作废。
  resetRunPanelState();
  // 打开任务线程即离开 Agent 详情（同一条「切走就丢草稿」）：只清 `activeAgentId` 会把
  // 详情页的草稿、`#adBody` 与保存条留成一份没人拥有的残留。
  if (state.activeAgentId) leaveAgentView();
  state.activeAgentId = null;
  state.activeTaskId = taskId;
  state.activeTask = taskById(taskId);
  state.taskMessages = [];
  state.taskOutcomes = {};
  state.taskSeenSeqs = new Set();
  // `reveal`：点卡片是**人显式的打开**，于是它压过「上一次人把 dock 收起来了」这一位。
  setDockMode('task', true);
  renderTaskThread();
  try {
    // 任务线程成员表（`GET /api/tasks/{id}/members`）**不取**：指派 chip 的候选是**频道**的
    // Agent 成员（见 `renderAssignChips`），target 成员表没有消费者，取它只是多一次请求。
    const thread = await api(taskPath(taskId, '/messages'));
    if (state.activeTaskId !== taskId) return;
    state.activeTask = thread.task;
    state.taskMessages = thread.messages || [];
    state.taskSeenSeqs = new Set(state.taskMessages.map((message) => message.seq));
    // 看板是全工作区视图，任务所属频道可能并非当前频道：按需单独拉该频道的成员表。
    if (channelAgentMembers(thread.task.channel_id) === null) {
      await refreshChannelMembers(thread.task.channel_id);
    }
    // 任务视图读取所属频道的**同一份**配置（#154 / 规格 §4.6）：team 频道下任务线程
    // 只唤醒队长，这条事实与频道设置页读的是同一个端点、同一份后端正本。
    await loadTaskChannelConfig(thread.task.channel_id);
    // 任务线程有自己的 target：审批卡按 task target 恢复，不借用频道那份。
    await loadApprovalsFor(thread.task.target_id);
    const index = state.tasks.findIndex((task) => task.id === taskId);
    if (index >= 0) state.tasks[index] = thread.task;
    else state.tasks.push(thread.task);
    // 打开的任务可能原先不在快照里（搜索）或快照已过期（卡片）：补进 `state.tasks` 之后
    // 页签计数立刻跟着变。这里**不**重铺看板（主线还停在它上面，重铺会丢掉人的滚动与焦点），
    // 所以计数不能指望 `renderTaskBoard()` 顺带，必须自己写一次（#177）。
    renderTaskCount();
    setDockMode('task');
    renderTaskThread();
    connectTaskStream(taskId, taskMaxSeq());
    await markTaskRead(taskId, taskMaxSeq());
    $('#ttScroll').scrollTop = $('#ttScroll').scrollHeight;
  } catch (err) {
    // 落点交给 `closeTaskDock()` 的共用判据（有频道 → 成员面板；否则收起 dock），
    // 这里不再另写一份 `setDockMode('agent')`——那会把「没有频道就没有成员面板」又推翻一次。
    closeTaskDock();
    renderTaskBoard();
    toast(t('toast.openTaskFailed', { reason: err.message }), 'err');
  }
}

async function markTaskRead(taskId, seq) {
  if (!seq) return;
  try {
    const payload = await api(taskPath(taskId, '/read'), { method: 'POST', body: { seq } });
    if (state.activeTaskId === taskId) state.activeTask = payload.task;
    const index = state.tasks.findIndex((task) => task.id === taskId);
    if (index >= 0) state.tasks[index] = payload.task;
    // #223：频道流里指向同一任务的 chip 跟着变成最新未读数（通常是 0）——就地写文本与 aria，
    // 不重铺消息流（滚动位置、焦点与正在点的节点都不动），也不重新拉频道消息。
    syncTaskPointerChips(payload.task);
    renderTaskThread();
    renderTaskBoard();
  } catch (err) {
    toast(t('toast.taskReadFailed', { reason: err.message }), 'err');
  }
}

function absorbTaskMessage(message) {
  if (!message || state.taskSeenSeqs.has(message.seq)) return false;
  state.taskSeenSeqs.add(message.seq);
  state.taskMessages.push(message);
  state.taskMessages.sort((a, b) => a.seq - b.seq);
  renderTaskThread();
  const scroll = $('#ttScroll');
  if (scroll) scroll.scrollTop = scroll.scrollHeight;
  if (state.activeTaskId) markTaskRead(state.activeTaskId, message.seq);
  return true;
}

function connectTaskStream(taskId, cursor) {
  if (state.activeTaskId !== taskId) return;
  const source = new EventSource(taskPath(taskId, '/events') + '?after_seq=' + cursor);
  const entry = { source, timer: null, taskId };
  state.taskStream = entry;
  source.addEventListener('rotate', () => {
    source.close();
    if (state.taskStream !== entry) return;
    entry.timer = setTimeout(() => connectTaskStream(taskId, taskMaxSeq()), 250);
  });
  ['message', 'member_status', 'typing', 'round_outcome', ...LIVE_EVENT_TYPES].forEach((type) => {
    source.addEventListener(type, (event) => handleTaskStreamEvent(taskId, event, type));
  });
  source.onopen = () => {
    // 任务 target 的 live 事件同样不补拉：重连后按运行面板快照兜底。
    if (state.activeTask && state.activeTask.id === taskId) {
      refreshRunPanelFor(state.activeTask.target_id);
    }
  };
  source.onerror = () => {
    source.close();
    if (state.taskStream !== entry) return;
    entry.timer = setTimeout(() => connectTaskStream(taskId, taskMaxSeq()), 1500);
  };
}

/* 任务线程的帧消费：message 仍进线程消息流（按 seq 去重），live-only 运行事件按 target 记录，
   typing / member_status / round_outcome 照既有语义处理。未知 type 忽略。 */
function handleTaskStreamEvent(taskId, event, type) {
  let payload;
  try {
    payload = JSON.parse(event.data);
  } catch {
    return;
  }
  if (type === 'message') {
    if (payload.message && state.activeTaskId === taskId) {
      absorbTaskMessage(payload.message);
      refreshTaskDetails(taskId);
    }
    return;
  }
  if (isLiveEventType(type)) {
    const targetId = payload.target_id
      || (state.activeTask && state.activeTask.target_id)
      || taskId;
    recordLiveEvent(targetId, type, payload);
    return;
  }
  const targetId = payload.target_id
    || (state.activeTask && state.activeTask.target_id)
    || taskId;
  if (type === 'typing') onStreamTyping(targetId, payload);
  else if (type === 'member_status') onStreamStatus(targetId, payload);
  else if (type === 'round_outcome') onStreamOutcome(targetId, payload);
  // 未知 type：忽略（不报错、不推进任何游标）。
}

function closeTaskStream() {
  const entry = state.taskStream;
  state.taskStream = null;
  if (!entry) return;
  if (entry.timer) clearTimeout(entry.timer);
  if (entry.source) entry.source.close();
  if (state.taskStreamTimer) clearTimeout(state.taskStreamTimer);
  state.taskStreamTimer = null;
}

/* ─── 运行面板（#155 / 规格 §4.6；dock 第三种形态）──────────────────────
   它是**独立面**（ADR-0020 / ADR-0023）：`agent_event` / `team_assignment` /
   `plan_update` / `sop_event` 只在这里呈现，绝不进消息流 / 未读 / 搜索 / 看板。
   两种数据来源严格分层：

   - **live 帧**（`state.liveEvents`，按 target 归属）只活在本会话内存里，断线不补；
   - **快照**（`GET /targets/{id}/team-runs`、`/plan`、`/sop-runs/{id}`）在打开面板与
     重连成功后重新读取，是断线期间变化的唯一兜底。两者按后端 id 合并：分派按
     `assignment_id` 去重，Plan 任务按 `(plan_run_id, task.id)` 去重，SOP 按
     `sop_run_id` + `step_index` 合并。live 帧因此不会把快照里的同一行再插一遍。
*/

const RUN_PANEL_RUN_LIMIT = 20;
const RUN_PANEL_EVENT_LIMIT = 80;
// 分派状态的推进顺序：live 帧只能把行往前推，不能把快照里的终态降回 started
// （重连后「先刷新快照、后收到队列里的旧帧」不能倒退）。
const RUN_PANEL_ASSIGNMENT_RANK = { started: 0, succeeded: 1, failed: 1, interrupted: 1, 'budget-exhausted': 1 };
const RUN_PANEL_STATE_KEYS = {
  started: 'runPanel.state.started',
  succeeded: 'runPanel.state.succeeded',
  failed: 'runPanel.state.failed',
  interrupted: 'runPanel.state.interrupted',
  'budget-exhausted': 'runPanel.state.budgetExhausted',
  running: 'runPanel.state.running',
  completed: 'runPanel.state.completed',
  'invalid-leader': 'runPanel.state.invalidLeader',
  pending: 'runPanel.state.pending',
  in_progress: 'runPanel.state.inProgress',
  awaiting: 'runPanel.state.awaiting',
};
let runPanelRequestVersion = 0;

function emptyRunPanel() {
  return {
    open: false,
    targetId: null,
    channelId: null,
    taskId: null,
    loading: false,
    error: '',
    runs: [],
    plan: [],
    sops: [],
    sopIds: [],
    sopDefs: {},
    overlayMarker: null,
  };
}

/** 换 target / 关任务线程时把运行面板整个作废：它的快照与 live 归属都只属于旧 target。 */
function resetRunPanelState() {
  state.runPanel = emptyRunPanel();
}

function runPanelStateLabel(value) {
  const key = RUN_PANEL_STATE_KEYS[value];
  return key ? t(key) : t('runPanel.state.unknown');
}

function runPanelStatePill(value) {
  const pill = el('span', 'rp-state', runPanelStateLabel(value));
  pill.dataset.state = String(value || '');
  return pill;
}

function runPanelMemberRecord(memberId) {
  const id = String(memberId || '');
  const pools = [state.members, state.agents];
  if (state.runPanel.channelId) pools.push(state.channelMembers[state.runPanel.channelId] || []);
  for (const pool of pools) {
    const found = (pool || []).find((member) => member && String(member.id) === id);
    if (found) return found;
  }
  return null;
}

function runPanelMemberName(memberId) {
  const member = runPanelMemberRecord(memberId);
  return member ? member.display_name : String(memberId || '');
}

function runPanelMemberSlot(memberId) {
  const member = runPanelMemberRecord(memberId);
  return member ? member.avatar_slot : 0;
}

function runPanelMemberHead(memberId, count) {
  const row = el('div', 'rp-member-head');
  const name = runPanelMemberName(memberId);
  row.appendChild(avatarNode(name, runPanelMemberSlot(memberId), 'sm'));
  row.appendChild(el('span', 'rp-member-name', name));
  if (count !== undefined) row.appendChild(el('span', 'rp-count', String(count)));
  return row;
}

/** 分派耗时：两个时间戳都来自后端；`finished_at` 为空是「还在跑」，不拿本地时钟假装读数。 */
function runPanelDuration(item) {
  if (!item.started_at) return '';
  if (!item.finished_at) return t('runPanel.inFlight');
  const from = Date.parse(item.started_at);
  const to = Date.parse(item.finished_at);
  if (!Number.isFinite(from) || !Number.isFinite(to) || to < from) return '';
  const seconds = (to - from) / 1000;
  const value = seconds >= 10 ? String(Math.round(seconds)) : seconds.toFixed(1);
  return t('runPanel.duration', { seconds: value });
}

/** 成员事件的摘要只取白名单 payload 的**数据字段**，不在这里造句子。 */
function runPanelEventSummary(event) {
  const payload = event.payload || {};
  const clip = (text) => {
    const value = String(text || '').replace(/\s+/g, ' ').trim();
    return value.length > 120 ? value.slice(0, 120) + '…' : value;
  };
  if (payload.tool_call_name) return clip(payload.tool_call_name);
  if (typeof payload.delta === 'string' && payload.delta.trim()) return clip(payload.delta);
  if (typeof payload.text === 'string' && payload.text.trim()) return clip(payload.text);
  if (payload.state) return String(payload.state);
  if (payload.finished_reason) return String(payload.finished_reason);
  if (payload.error && payload.error.message) return clip(payload.error.message);
  if (payload.hint) return clip(typeof payload.hint === 'string' ? payload.hint : JSON.stringify(payload.hint));
  if (payload.name) return String(payload.name);
  return '';
}

function runPanelLiveEvents() {
  const panel = state.runPanel;
  if (!panel.targetId) return [];
  return (state.liveEvents.get(panel.targetId) || []).filter(
    (event) => event.type === 'agent_event'
      || event.type === 'team_assignment'
      || event.type === 'plan_update'
      || event.type === 'sop_event',
  );
}

/** 只保留**快照之后**到达的 Plan / SOP live 帧。

    快照是它们更早变化的合并结果；把队列里的旧帧再叠一遍会让「重连刷新」把状态拉回
    断线前。分派不在这里排除（它有状态推进的秩守卫，且 `started` → 终态是合法的直播
    过程）；`agent_event` 本来就只活在 live 层，全部保留。 */
function runPanelOverlayEvents(live) {
  const marker = state.runPanel.overlayMarker;
  if (!marker) return live;
  const index = live.indexOf(marker);
  if (index < 0) return live; // 有界保留把标记挤掉了：宁可全部应用，也别丢新帧
  return live.slice(index + 1);
}

/** 快照分派 + live 分派按 `assignment_id` 合并：同一分派只有一行。 */
function runPanelAssignments(run, live) {
  const rows = (run.assignments || []).map((item) => ({ ...item }));
  const byId = new Map(rows.map((item) => [String(item.id), item]));
  live.forEach((frame) => {
    if (frame.type !== 'team_assignment' || frame.team_run_id !== run.id) return;
    const key = String(frame.assignment_id || '');
    if (!key) return;
    const existing = byId.get(key);
    if (!existing) {
      const row = {
        id: key,
        member_id: frame.member_id,
        task_id: frame.task_id,
        reply_id: frame.reply_id,
        state: frame.state,
        started_at: null,
        finished_at: null,
        reply_excerpt: null,
        error: null,
      };
      byId.set(key, row);
      rows.push(row);
      return;
    }
    const currentRank = RUN_PANEL_ASSIGNMENT_RANK[existing.state] ?? 0;
    const frameRank = RUN_PANEL_ASSIGNMENT_RANK[frame.state] ?? 0;
    if (frameRank < currentRank) return; // 不把终态降回 started
    existing.state = frame.state;
    if (frame.member_id) existing.member_id = frame.member_id;
    if (frame.reply_id) existing.reply_id = frame.reply_id;
    if (frame.task_id) existing.task_id = frame.task_id;
  });
  return rows;
}

function runPanelAssignmentRow(item) {
  const row = el('div', 'rp-assign');
  row.dataset.assignment = String(item.id || '');
  row.dataset.state = String(item.state || '');
  const top = el('div', 'rp-assign-top');
  top.appendChild(runPanelStatePill(item.state));
  const duration = runPanelDuration(item);
  if (duration) top.appendChild(el('span', 'rp-duration', duration));
  row.appendChild(top);
  if (item.task_id) {
    row.appendChild(el('div', 'rp-meta', t('runPanel.taskRef', { id: String(item.task_id).slice(-6) })));
  }
  if (item.reply_excerpt) row.appendChild(el('div', 'rp-excerpt', item.reply_excerpt));
  if (item.error) row.appendChild(el('div', 'rp-error-line', String(item.error)));
  return row;
}

function runPanelRunCard(run, live) {
  const card = el('div', 'rp-run');
  card.dataset.odId = 'rp-run-' + run.id;
  card.dataset.run = String(run.id);
  const head = el('div', 'rp-run-head');
  const leaderName = runPanelMemberName(run.leader_member_id);
  head.appendChild(avatarNode(leaderName, runPanelMemberSlot(run.leader_member_id), 'sm'));
  head.appendChild(el('span', 'rp-run-title', leaderName));
  head.appendChild(runPanelStatePill(run.status));
  if (run.started_at) head.appendChild(el('span', 'rp-time', timeLabel(run.started_at)));
  head.appendChild(el('span', 'rp-id', '#' + String(run.id).slice(-6)));
  card.appendChild(head);

  const assignments = runPanelAssignments(run, live);
  if (!assignments.length) {
    card.appendChild(el('div', 'tt-note', t('runPanel.noAssignments')));
    return card;
  }
  const groups = new Map();
  assignments.forEach((item) => {
    const memberId = String(item.member_id || '');
    if (!groups.has(memberId)) groups.set(memberId, []);
    groups.get(memberId).push(item);
  });
  groups.forEach((items, memberId) => {
    const group = el('div', 'rp-member');
    group.dataset.odId = 'rp-run-' + run.id + '-member-' + memberId;
    group.appendChild(runPanelMemberHead(memberId, items.length));
    items.forEach((item) => group.appendChild(runPanelAssignmentRow(item)));
    card.appendChild(group);
  });
  return card;
}

function runPanelEventsSection(live) {
  const section = el('div', 'd-sec');
  section.dataset.odId = 'rp-events';
  section.appendChild(el('div', 'd-sec-title', t('runPanel.sectionEvents')));
  const events = live.filter((frame) => frame.type === 'agent_event').slice(-RUN_PANEL_EVENT_LIMIT);
  if (!events.length) {
    section.appendChild(el('div', 'tt-empty', t('runPanel.eventsEmpty')));
    return section;
  }
  const groups = new Map();
  events.forEach((frame) => {
    const memberId = String(frame.member_id || '');
    if (!groups.has(memberId)) groups.set(memberId, new Map());
    const replies = groups.get(memberId);
    const replyId = String(frame.reply_id || '');
    if (!replies.has(replyId)) replies.set(replyId, []);
    replies.get(replyId).push(frame);
  });
  groups.forEach((replies, memberId) => {
    const group = el('div', 'rp-member');
    group.dataset.odId = 'rp-events-member-' + memberId;
    group.dataset.member = memberId;
    group.appendChild(runPanelMemberHead(memberId, replyCountLabel(replies.size)));
    replies.forEach((frames, replyId) => {
      const reply = el('div', 'rp-reply');
      reply.dataset.reply = replyId;
      reply.appendChild(el('div', 'rp-reply-id', t('runPanel.replyRef', { id: replyId.slice(-6) })));
      frames.slice(-20).forEach((frame) => {
        const row = el('div', 'rp-event');
        row.dataset.eventType = String(frame.event_type || '');
        row.appendChild(el('span', 'rp-event-type', String(frame.event_type || '')));
        const summary = runPanelEventSummary(frame);
        if (summary) row.appendChild(el('span', 'rp-event-summary', summary));
        reply.appendChild(row);
      });
      group.appendChild(reply);
    });
    section.appendChild(group);
  });
  return section;
}

/** 一个回复内的事件条数是**计数**，进 mono 档；不把「3 条事件」写成一句话。 */
function replyCountLabel(count) {
  return count ? '×' + String(count) : '';
}

/** Plan：快照里每个成员最近一个 run 的清单 + live `plan_update` 覆盖（按 task id 去重）。 */
function runPanelPlanGroups(live) {
  const groups = new Map();
  (state.runPanel.plan || []).forEach((member) => {
    const latest = (member.runs || [])[0];
    if (!latest) return;
    groups.set(String(member.member_id), {
      memberId: String(member.member_id),
      runId: String(latest.id),
      tasks: (latest.tasks || []).map((task) => ({ ...task })),
    });
  });
  live.filter((frame) => frame.type === 'plan_update').forEach((frame) => {
    const memberId = String(frame.member_id || '');
    let group = groups.get(memberId);
    if (!group) {
      group = { memberId, runId: String(frame.plan_run_id || ''), tasks: [] };
      groups.set(memberId, group);
    }
    if (group.runId !== String(frame.plan_run_id || '')) {
      group.runId = String(frame.plan_run_id || '');
      group.tasks = [];
    }
    const task = frame.task || {};
    const index = group.tasks.findIndex((item) => String(item.id) === String(task.id));
    if (task.deleted_at) {
      if (index >= 0) group.tasks.splice(index, 1);
      return;
    }
    if (index >= 0) group.tasks[index] = { ...group.tasks[index], ...task };
    else group.tasks.push({ ...task });
  });
  return [...groups.values()];
}

/** 频道运行面板的协作模式：只读 `state.channelConfigs` 的缓存（正本在后端）。
    `null` = 任务面板 / 没有频道 / 配置还没读回——前端不推算模式，读不到就按通用空态铺。 */
function runPanelChannelMode() {
  const panel = state.runPanel;
  if (panel.taskId || !panel.channelId) return null;
  const config = state.channelConfigs[panel.channelId];
  return config ? config.mode : null;
}

function runPanelPlanSection(live, autonomousChannel) {
  const section = el('div', 'd-sec');
  section.dataset.odId = 'rp-plan';
  section.appendChild(el('div', 'd-sec-title', t('runPanel.sectionPlan')));
  const groups = runPanelPlanGroups(live)
    .map((group) => ({ ...group, tasks: group.tasks.filter((task) => !task.deleted_at) }))
    .filter((group) => group.tasks.length);
  if (!groups.length) {
    const emptyKey = autonomousChannel ? 'runPanel.planEmptyAutonomous' : 'runPanel.planEmpty';
    section.appendChild(el('div', 'tt-empty', t(emptyKey)));
    return section;
  }
  groups.forEach((group) => {
    const card = el('div', 'rp-member rp-plan-group');
    card.dataset.odId = 'rp-plan-member-' + group.memberId;
    card.dataset.member = group.memberId;
    card.appendChild(runPanelMemberHead(group.memberId, group.tasks.length));
    group.tasks.forEach((task) => {
      const row = el('div', 'rp-plan-task');
      row.dataset.planTask = String(task.id || '');
      row.dataset.state = String(task.state || '');
      row.appendChild(el('span', 'rp-plan-subject', String(task.subject || task.description || task.id || '')));
      row.appendChild(runPanelStatePill(task.state));
      card.appendChild(row);
    });
    section.appendChild(card);
  });
  return section;
}

/** `completed` / `failed` 是 SOP 的终态：快照已经收口的运行不会被迟到的 live 帧拉回。 */
function runPanelTerminalPhase(phase) {
  return phase === 'completed' || phase === 'failed';
}

/** SOP：快照 run 视图 + live `sop_event` 覆盖（按 run id + step_index 合并）。 */
function runPanelSopViews(live) {
  const views = new Map();
  (state.runPanel.sops || []).forEach((run) => {
    views.set(String(run.id), {
      id: String(run.id),
      definitionId: String(run.sop_definition_id || ''),
      phase: run.phase,
      started_at: run.started_at,
      awaiting: run.awaiting || null,
      steps: ((run.state && run.state.steps) || []).map((step, index) => ({
        index,
        subject: step.subject || '',
        phase: step.phase,
        verdicts: (step.verifications || []).slice(),
      })),
    });
  });
  live.filter((frame) => frame.type === 'sop_event').forEach((frame) => {
    const runId = String(frame.sop_run_id || '');
    if (!runId) return;
    let view = views.get(runId);
    if (!view) {
      view = { id: runId, definitionId: '', phase: 'pending', started_at: null, awaiting: null, steps: [] };
      views.set(runId, view);
    }
    const payload = frame.payload || {};
    if (payload.run_phase && !runPanelTerminalPhase(view.phase)) view.phase = frame.phase;
    if (payload.reply_id) view.awaiting = { reply_id: payload.reply_id };
    const index = Number(frame.step_index) || 0;
    let step = view.steps[index];
    if (!step) {
      step = { index, subject: payload.step || '', phase: 'pending', verdicts: [] };
      view.steps[index] = step;
    }
    const nextPhase = payload.phase || frame.phase;
    if (nextPhase && (!runPanelTerminalPhase(step.phase) || runPanelTerminalPhase(nextPhase))) {
      step.phase = nextPhase;
    }
    if (payload.verdict) step.verdicts = step.verdicts.concat([payload.verdict]);
  });
  return [...views.values()];
}

function runPanelSopSection(live) {
  const section = el('div', 'd-sec');
  section.dataset.odId = 'rp-sop';
  section.appendChild(el('div', 'd-sec-title', t('runPanel.sectionSop')));
  const views = runPanelSopViews(live);
  if (!views.length) {
    section.appendChild(el('div', 'tt-empty', t('runPanel.sopEmpty')));
    return section;
  }
  views.forEach((run) => {
    const card = el('div', 'rp-run rp-sop');
    card.dataset.odId = 'rp-sop-' + run.id;
    card.dataset.sopRun = run.id;
    const head = el('div', 'rp-run-head');
    head.appendChild(el('span', 'rp-run-title', '#' + run.id.slice(-6)));
    head.appendChild(runPanelStatePill(run.phase));
    if (run.started_at) head.appendChild(el('span', 'rp-time', timeLabel(run.started_at)));
    card.appendChild(head);
    if (run.awaiting && run.awaiting.reply_id) {
      card.appendChild(
        el('div', 'team-notice', t('runPanel.awaiting', { reply: String(run.awaiting.reply_id).slice(-6) })),
      );
    }
    const steps = run.steps.slice().sort((a, b) => a.index - b.index);
    const definitionSteps = (state.runPanel.sopDefs[run.definitionId] || {}).steps || [];
    if (!steps.length) {
      card.appendChild(el('div', 'tt-note', t('runPanel.noSteps')));
    } else {
      steps.forEach((step) => {
        const row = el('div', 'rp-step');
        row.dataset.step = String(step.index);
        row.dataset.state = String(step.phase || '');
        const top = el('div', 'rp-step-top');
        const definitionStep = definitionSteps[step.index];
        const subject = step.subject
          || (definitionStep && definitionStep.subject)
          || t('runPanel.stepIndex', { index: step.index + 1 });
        top.appendChild(el('span', 'rp-step-name', subject));
        top.appendChild(runPanelStatePill(step.phase));
        row.appendChild(top);
        if (step.verdicts.length) {
          const verdict = step.verdicts[step.verdicts.length - 1];
          const line = el('div', verdict.passed ? 'rp-verdict is-pass' : 'rp-verdict is-fail');
          line.dataset.passed = verdict.passed ? 'true' : 'false';
          line.appendChild(el('span', 'rp-verdict-state',
            verdict.passed ? t('runPanel.verdictPassed') : t('runPanel.verdictFailed')));
          if (verdict.message) line.appendChild(el('span', 'rp-verdict-message', String(verdict.message)));
          row.appendChild(line);
        }
        card.appendChild(row);
      });
    }
    section.appendChild(card);
  });
  return section;
}

function renderRunPanel() {
  const panel = state.runPanel;
  const title = $('#rpTitle');
  if (title) {
    if (panel.taskId) {
      const task = taskById(panel.taskId);
      title.textContent = task ? task.title : '#' + String(panel.taskId).slice(-6);
    } else {
      const channel = state.channels.find((item) => item.id === panel.channelId);
      title.textContent = channel ? channel.name : String(panel.channelId || '');
    }
  }
  const sub = $('#rpSub');
  if (sub) sub.textContent = t('runPanel.sub');
  const body = $('#rpBody');
  if (!body) return;
  if (!panel.open) {
    replaceChildren(body, []);
    return;
  }
  if (panel.error) {
    const box = el('div', 'rp-error');
    box.setAttribute('data-od-id', 'rp-error');
    box.appendChild(el('div', 'rp-error-text', t('runPanel.error', { reason: panel.error })));
    const retry = el('button', 'btn btn-sm', t('runPanel.retry'));
    retry.type = 'button';
    retry.dataset.runRetry = '1';
    retry.setAttribute('data-od-id', 'rp-retry');
    box.appendChild(retry);
    replaceChildren(body, [box]);
    return;
  }
  if (panel.loading && !panel.runs.length && !panel.plan.length && !panel.sops.length) {
    replaceChildren(body, [el('div', 'tt-empty', t('runPanel.loading'))]);
    return;
  }
  const live = runPanelLiveEvents();
  const overlay = runPanelOverlayEvents(live);
  // 自主模式频道没有队长 ⇒ 不会产生 team 运行 / 分派 / Plan；空态要把这件事和「去哪看」讲清楚。
  // 只认后端读回的缓存（见 `runPanelChannelMode`），任务面板与其它模式都沿用原空态。
  const autonomousChannel = runPanelChannelMode() === 'autonomous';
  const runsSection = el('div', 'd-sec');
  runsSection.dataset.odId = 'rp-runs';
  runsSection.appendChild(el('div', 'd-sec-title', t('runPanel.sectionRuns')));
  if (!panel.runs.length) {
    const emptyKey = autonomousChannel ? 'runPanel.runsEmptyAutonomous' : 'runPanel.runsEmpty';
    runsSection.appendChild(el('div', 'tt-empty', t(emptyKey)));
  } else {
    panel.runs.forEach((run) => runsSection.appendChild(runPanelRunCard(run, live)));
  }
  replaceChildren(body, [
    runsSection,
    runPanelEventsSection(live),
    runPanelPlanSection(overlay, autonomousChannel),
    runPanelSopSection(overlay),
  ]);
}

/** 打开运行面板：入口在频道头（target = 频道）与任务线程头（target = 任务 target，
    另带 `task_id` 过滤）。`reveal` 表示这是人显式的打开，压过「上次收起」那一
    位（与 `openTask` 同一条规则）。 */
function openRunPanel(options) {
  const panel = state.runPanel;
  panel.open = true;
  panel.targetId = options.targetId;
  panel.channelId = options.channelId || null;
  panel.taskId = options.taskId || null;
  panel.loading = true;
  panel.error = '';
  panel.runs = [];
  panel.plan = [];
  panel.sops = [];
  panel.sopDefs = {};
  panel.overlayMarker = null;
  // 本次会话里已经见过的 SOP run：面板重开后仍要能按 id 拉快照（后端没有按 target
  // 列 SOP run 的读面，live 帧是 id 的唯一来源）。
  panel.sopIds = (state.liveEvents.get(panel.targetId) || [])
    .filter((event) => event.type === 'sop_event' && event.sop_run_id)
    .map((event) => String(event.sop_run_id))
    .filter((id, index, ids) => ids.indexOf(id) === index);
  setDockMode('run', true);
  renderRunPanel();
  if (panel.channelId && channelAgentMembers(panel.channelId) === null) {
    refreshChannelMembers(panel.channelId).then(() => {
      if (state.runPanel.open && state.runPanel.channelId === panel.channelId) renderRunPanel();
    });
  }
  // 频道面板的空态解释依赖协作模式：缓存未命中时按既有读取路径读一次（同一频道的在飞
  // 读取会复用），读回后若面板仍开在同一个频道就重绘。读失败返回 null ⇒ 保持未缓存、
  // 继续铺通用空态，**不**把读不到误报成自主模式。
  if (panel.channelId && !panel.taskId && !state.channelConfigs[panel.channelId]) {
    const channelId = panel.channelId;
    loadTaskChannelConfig(channelId).then(() => {
      if (!state.runPanel.open || state.runPanel.taskId) return;
      if (state.runPanel.channelId !== channelId) return;
      renderRunPanel();
    });
  }
  loadRunPanelSnapshot();
}

function openRunPanelForChannel() {
  const channel = activeChannel();
  if (!channel) return;
  openRunPanel({ channelId: channel.id, targetId: channel.id, taskId: null });
}

function openRunPanelForTask() {
  const task = state.activeTask;
  if (!task) return;
  openRunPanel({ channelId: task.channel_id, targetId: task.target_id, taskId: task.id });
}

/** 关闭运行面板：落点与其它 dock 形态共用同一份判据（从任务线程打开的回任务线程）。 */
function closeRunPanel() {
  const panel = state.runPanel;
  const fromTask = Boolean(panel.taskId);
  panel.open = false;
  panel.loading = false;
  panel.error = '';
  if (fromTask && state.activeTaskId) setDockMode('task');
  else settleDockFallback();
}

/** `sop_event` 是新 run 的唯一线索：记住 id，下一次快照刷新按 id 拉 `GET /sop-runs/{id}`。 */
function noteRunPanelSopId(targetId, event) {
  const panel = state.runPanel;
  if (!panel.open || panel.targetId !== targetId) return;
  const runId = String(event.sop_run_id || '');
  if (runId && panel.sopIds.indexOf(runId) === -1) panel.sopIds.push(runId);
}

async function loadRunPanelSnapshot() {
  const panel = state.runPanel;
  if (!panel.open || !panel.targetId) return;
  const version = ++runPanelRequestVersion;
  const targetId = panel.targetId;
  const taskId = panel.taskId;
  const sopIds = panel.sopIds.slice();
  panel.loading = true;
  panel.error = '';
  renderRunPanel();
  let runsUrl = '/api/targets/' + encodeURIComponent(targetId) + '/team-runs?limit=' + RUN_PANEL_RUN_LIMIT;
  if (taskId) runsUrl += '&task_id=' + encodeURIComponent(taskId);
  try {
    const [runsPayload, planPayload] = await Promise.all([
      api(runsUrl),
      api('/api/targets/' + encodeURIComponent(targetId) + '/plan'),
    ]);
    const sopPayloads = await Promise.all(
      sopIds.map((runId) => api('/api/sop-runs/' + encodeURIComponent(runId)).catch(() => null)),
    );
    // 步骤名在定义里（run state 只存 phase / verdict）：按 run 的 definition id 读定义。
    const definitionIds = sopPayloads
      .map((payload) => payload && payload.run && payload.run.sop_definition_id)
      .filter(Boolean)
      .filter((id, index, ids) => ids.indexOf(id) === index);
    const definitionPayloads = await Promise.all(
      definitionIds.map((id) => api('/api/sops/' + encodeURIComponent(id)).catch(() => null)),
    );
    if (version !== runPanelRequestVersion || !panel.open || panel.targetId !== targetId) return;
    panel.runs = runsPayload.runs || [];
    panel.plan = (planPayload.plan && planPayload.plan.members) || [];
    panel.sops = sopPayloads
      .filter((payload) => payload && payload.run)
      .map((payload) => payload.run);
    const defs = {};
    definitionPayloads.forEach((payload) => {
      if (payload && payload.sop && payload.sop.id) defs[payload.sop.id] = payload.sop;
    });
    panel.sopDefs = defs;
    // 快照已经合并了此刻之前的所有变化：Plan / SOP 的 live 覆盖只应用它之后的帧。
    const events = state.liveEvents.get(targetId) || [];
    panel.overlayMarker = events.length ? events[events.length - 1] : null;
    panel.loading = false;
    panel.error = '';
  } catch (err) {
    if (version !== runPanelRequestVersion || !panel.open || panel.targetId !== targetId) return;
    panel.loading = false;
    panel.error = err.message;
  }
  renderRunPanel();
}

/** 重连成功后刷新：只有面板开着、且这个 target 正是面板的 target 时才读。 */
function refreshRunPanelFor(targetId) {
  const panel = state.runPanel;
  if (!panel.open || !panel.targetId) return;
  if (targetId && panel.targetId !== targetId) return;
  loadRunPanelSnapshot();
}

/* ─── 审批卡（#153 / 规格 §4.6 第 4 条 / ADR-0021 §四）───────────────────
   人类面的 HITL：卡片只呈现后端已经收窄的 `{id, name, input}`（不存 env / 凭证），
   允许 / 拒绝走 `POST /api/approvals/{reply_id}`（pending-only、幂等、相反结论 409）。
   恢复路径是 `GET /api/approvals?target_id=&state=pending`；live 帧只补「本次会话内的
   变化」，断线不补发（ADR-0020），重连后按快照兜底。
   倒计时只由 `requested_at + 600s` 推导——这是后端的等待上界，也是 displayed `expired`
   的唯一判据。倒计时归零只改呈现（后端会在同一上界把记录标 expired）。 */

const APPROVAL_TIMEOUT_SECONDS = 600;
/* #209：线程关着时，任务看板 / 列表行 / 任务页签也要看得见 pending。读的是后端已有的
   全工作区读数 `GET /api/approvals?state=pending`（不带 target_id），并按有界周期刷新：
   10–15s 单例定时器；页面隐藏（`document.hidden`）或离线时跳过本轮；同一时刻最多一个
   在途请求（不叠加）；失败静默——角标是提示，不是阻塞界面的一等公民。 */
const APPROVAL_REFRESH_MS = 12000;
const APPROVAL_REDACTED = '••••';
// 只用于「input 里可能出现的敏感键」：凭证、令牌、口令与 cookie 一律掩码。
const APPROVAL_SENSITIVE_KEY =
  /(?:pass(?:word|wd)?|secret|token|api[-_]?key|authorization|cookie|credential|private[-_]?key|access[-_]?key|session[-_]?id)/i;

const APPROVAL_STATE_KEYS = {
  pending: 'approval.pending',
  approved: 'approval.approved',
  denied: 'approval.denied',
  expired: 'approval.expired',
};

let approvalTicker = null;
// 全量读数的单例定时器 / 在途闸门 / 「target→pending 条数」这一派生集合的上一版签名。
let approvalRefreshTimer = null;
let approvalRefreshInFlight = false;
let approvalBadgeSignature = '';

function approvalMapFor(targetId) {
  let map = state.approvals.get(targetId);
  if (!map) {
    map = new Map();
    state.approvals.set(targetId, map);
  }
  return map;
}

/** 某个 target 的审批记录：pending 在前，其余按请求时间倒序（本次会话内的显示顺序）。 */
function approvalRecordsFor(targetId) {
  const map = state.approvals.get(targetId);
  if (!map) return [];
  const rank = (record) => (approvalDisplayState(record) === 'pending' ? 0 : 1);
  return Array.from(map.values()).sort(
    (left, right) =>
      rank(left) - rank(right) ||
      String(right.requested_at || '').localeCompare(String(left.requested_at || '')),
  );
}

/** 剩余秒数（向上取整）；`requested_at` 不可解析时返回 `null`（不伪造倒计时）。 */
function approvalRemainingSeconds(record, now) {
  const requested = Date.parse(record && record.requested_at);
  if (!Number.isFinite(requested)) return null;
  const at = now === undefined ? Date.now() : now;
  return Math.max(0, Math.ceil((requested + APPROVAL_TIMEOUT_SECONDS * 1000 - at) / 1000));
}

/** 呈现态：后端的终态优先；仍 pending 但已越过 600s 上界时显示 expired。 */
function approvalDisplayState(record) {
  if (!record) return 'expired';
  if (record.state && record.state !== 'pending') return String(record.state);
  const remaining = approvalRemainingSeconds(record, Date.now());
  if (remaining !== null && remaining <= 0) return 'expired';
  return 'pending';
}

function approvalStateLabel(value) {
  const key = APPROVAL_STATE_KEYS[value];
  return key ? t(key) : String(value || t('approval.stateUnknown'));
}

function approvalClockText(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  return String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0');
}

function approvalRemainingText(record, displayState) {
  if (displayState !== 'pending') return '';
  const remaining = approvalRemainingSeconds(record, Date.now());
  return t('approval.remaining', { time: remaining === null ? '--:--' : approvalClockText(remaining) });
}

function approvalMemberRecord(memberId) {
  const id = String(memberId || '');
  const pools = [state.members, state.agents, ...Object.values(state.channelMembers || {})];
  for (const pool of pools) {
    const found = (pool || []).find((member) => member && String(member.id) === id);
    if (found) return found;
  }
  return null;
}

/** 递归掩码：敏感键的值一律替换为 `••••`；对象最多展开三层，避免把大载荷铺进 DOM。 */
function approvalSanitizeValue(value, key, depth) {
  if (key && APPROVAL_SENSITIVE_KEY.test(String(key))) return APPROVAL_REDACTED;
  if (depth > 3) return '…';
  if (Array.isArray(value)) {
    return value.map((item) => approvalSanitizeValue(item, '', depth + 1));
  }
  if (value && typeof value === 'object') {
    const out = {};
    Object.keys(value).forEach((name) => {
      out[name] = approvalSanitizeValue(value[name], name, depth + 1);
    });
    return out;
  }
  if (typeof value === 'string' && value.length > 120) return value.slice(0, 117) + '…';
  return value;
}

function approvalValueText(value) {
  if (typeof value === 'string') return value;
  if (value === null || value === undefined) return String(value);
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** 参数摘要：只读 `input`，敏感键掩码；总长有界，不把整份文件内容铺开。 */
function approvalInputSummary(input) {
  if (input === null || input === undefined) return t('approval.noInput');
  if (typeof input !== 'object') {
    return approvalValueText(approvalSanitizeValue(input, '', 0));
  }
  const keys = Object.keys(input);
  if (!keys.length) return t('approval.noInput');
  const text = keys
    .map((key) => key + '=' + approvalValueText(approvalSanitizeValue(input[key], key, 0)))
    .join(' · ');
  return text.length > 240 ? text.slice(0, 237) + '…' : text;
}

function approvalToolNode(call) {
  const item = el('div', 'approval-tool');
  item.appendChild(
    el('div', 'approval-tool-name', String((call && call.name) || t('approval.unknownTool'))),
  );
  item.appendChild(
    el('div', 'approval-tool-input', approvalInputSummary(call ? call.input : null)),
  );
  return item;
}

function approvalActionButton(targetId, replyId, action, inFlight) {
  const allow = action === 'allow';
  const button = el('button', 'btn btn-sm' + (allow ? ' btn-primary' : ''), t(allow ? 'approval.allow' : 'approval.deny'));
  button.type = 'button';
  button.disabled = inFlight || !state.online;
  button.dataset.approvalAction = action;
  button.dataset.approvalReply = replyId;
  button.dataset.approvalTarget = String(targetId);
  return button;
}

/** 还没被决策的调用（#215）：``reply_id`` 是回复级的，一条记录里会同时躺着**已决策的旧调用**
    与**新调用**（快照读的是整条记录）。卡片与提交都只看这一份——否则对第二批点「拒绝」会把
    旧调用的相反结论一起带上，被服务端按 409 整批拒掉。只读，不改 record。 */
function approvalPendingToolCalls(record) {
  const decided = new Set(
    ((record && record.decisions) || []).map((item) => String((item && item.tool_call_id) || '')),
  );
  return ((record && record.tool_calls) || []).filter(
    (call) => !decided.has(String((call && call.id) || '')),
  );
}

/** 一组工具调用的 id 集合（空 id 也收，与后端 `_call_key` 同口径）。 */
function approvalCallIds(calls) {
  return new Set((calls || []).map((call) => String((call && call.id) || '')));
}

function approvalCardNode(targetId, record) {
  const replyId = String(record.reply_id || '');
  const displayState = approvalDisplayState(record);
  const card = el('div', 'approval-card');
  card.dataset.approvalId = replyId;
  card.dataset.approvalTarget = String(targetId);
  card.dataset.state = displayState;
  card.setAttribute('role', 'group');
  // 待审批时只问**未决策**的调用（#215）；已收口时回看这一整条记录的全部调用（决策是历史）。
  const calls =
    displayState === 'pending'
      ? approvalPendingToolCalls(record)
      : (record.tool_calls || []).slice();
  const toolNames = calls
    .map((call) => String((call && call.name) || ''))
    .filter(Boolean)
    .join(', ');
  card.setAttribute('aria-label', t('approval.cardLabel', { tools: toolNames }));

  const head = el('div', 'approval-head');
  head.appendChild(el('span', 'approval-tag', t('approval.tag')));
  const member = approvalMemberRecord(record.member_id);
  const memberName = member ? member.display_name : String(record.member_id || '');
  head.appendChild(avatarNode(memberName, member ? member.avatar_slot : 0, 'sm'));
  head.appendChild(el('span', 'approval-member', memberName));
  const statePill = el('span', 'approval-state', approvalStateLabel(displayState));
  statePill.dataset.state = displayState;
  head.appendChild(statePill);
  const remaining = el('span', 'approval-remaining', approvalRemainingText(record, displayState));
  const remainingSeconds = approvalRemainingSeconds(record, Date.now());
  if (displayState === 'pending' && remainingSeconds !== null) {
    remaining.dataset.urgent = remainingSeconds <= 60 ? '1' : '0';
  }
  head.appendChild(remaining);
  card.appendChild(head);

  if (calls.length) {
    const tools = el('div', 'approval-tools');
    calls.forEach((call) => tools.appendChild(approvalToolNode(call)));
    card.appendChild(tools);
  }

  if (displayState === 'pending') {
    const inFlight = state.approvalInFlight.has(replyId);
    const actions = el('div', 'approval-actions');
    actions.appendChild(approvalActionButton(targetId, replyId, 'allow', inFlight));
    actions.appendChild(approvalActionButton(targetId, replyId, 'deny', inFlight));
    card.appendChild(actions);
    if (inFlight) card.appendChild(el('div', 'approval-note', t('approval.submitting')));
  }

  const error = state.approvalErrors.get(replyId);
  if (error) {
    card.appendChild(el('div', 'approval-error', t('approval.submitFailed', { reason: error })));
  }
  return card;
}

function renderChannelApprovals(records, targetId) {
  const board = $('#approvalBoard');
  if (!board) return;
  const id = targetId === undefined ? state.activeChannelId : targetId;
  const target = activeTarget();
  const visible = Boolean(target && id && target.id === id);
  const list = records || (visible ? approvalRecordsFor(id) : []);
  board.hidden = !list.length;
  if (!list.length) {
    replaceChildren(board, []);
    return;
  }
  const title = el('div', 'd-sec-title', t('approval.section'));
  replaceChildren(board, [title, ...list.map((record) => approvalCardNode(id, record))]);
}

function renderTaskApprovals() {
  const section = $('#ttApprovalSection');
  const list = $('#ttApprovals');
  if (!section || !list) return;
  const task = state.activeTask;
  const targetId = task && task.target_id;
  const records = targetId ? approvalRecordsFor(targetId) : [];
  section.hidden = !records.length;
  replaceChildren(list, records.map((record) => approvalCardNode(targetId, record)));
}

/* ─── #209：线程关着时的待审批外显（卡片 / 列表行角标 + 任务页签圆点）──────
   读数来自 `state.approvals`（全量快照 + live 帧的同一份镜像），归属**只**按
   `task.target_id`：频道 target 的记录不在任何任务卡片上出现，反向亦然。
   「N」按 `approvalDisplayState()` 判定——本地 600s 上界过期后它立刻不再计数。 */

/** 某个 target 当前 pending 的条数（呈现态判据与审批卡一致）。 */
function pendingApprovalCountForTarget(targetId) {
  const map = state.approvals.get(targetId);
  if (!map) return 0;
  let count = 0;
  map.forEach((record) => {
    if (approvalDisplayState(record) === 'pending') count += 1;
  });
  return count;
}

function taskPendingApprovalCount(task) {
  return task && task.target_id ? pendingApprovalCountForTarget(task.target_id) : 0;
}

/** 当前频道下「有任务在等审批」的任务数（页签圆点的 aria 名里的 N）。 */
function channelPendingTaskCount(channelId) {
  if (!channelId) return 0;
  return state.tasks.filter(
    (task) => task.channel_id === channelId && taskPendingApprovalCount(task) > 0,
  ).length;
}

/** 「待审批 N」角标本体（任务卡片 / 列表行 / 频道指针消息行共用同一个节点与同一对词条）。

    `#217`：指针消息行要的是**同一套视觉**，因此这里返回的是同一个 `.card-approval` 节点。
    `odId` 必填，由调用方给：卡片前缀 `task-approval-badge-`、消息行前缀 `message-approval-badge-`
    —— 两个前缀本身保证不会撞成同一个 `data-od-id`（浏览器探针据此唯一定位）。
    没有 pending 时返回 `null`（不渲染空元素）。 */
function approvalBadgeNode(task, odId) {
  const count = taskPendingApprovalCount(task);
  if (!count) return null;
  const badge = el('span', 'card-approval', t('board.approvalBadge', { count }));
  badge.setAttribute('data-od-id', odId);
  badge.setAttribute('aria-label', t('board.approvalBadgeAria', { title: task.title, count }));
  return badge;
}

/** 任务卡片 / 列表行的角标；没有 pending 时返回 `null`（不渲染空元素）。 */
function taskApprovalBadgeNode(task) {
  return approvalBadgeNode(task, 'task-approval-badge-' + task.id);
}

/** 页签圆点只画当前频道：DM 里任务页签整个不出现，圆点自然也不出现。 */
function renderTaskApprovalDot(isDm) {
  const dot = $('#taskApprovalDot');
  if (!dot) return;
  const count = isDm ? 0 : channelPendingTaskCount(state.activeChannelId);
  dot.hidden = count === 0;
  // 隐藏时写空串而不是删属性：DOM 替身只保证 setAttribute（与既有用例族的替身同一条）。
  dot.setAttribute('aria-label', count ? t('tabs.tasksApprovalDot', { count }) : '');
}

/** 「target→pending 条数」这一派生集合的签名。

    读域与 `taskById()` 对齐：`activeTask` + `state.tasks` + `state.boardTasks`。只遍历
    `state.tasks` 会漏掉「任务只出现在看板筛选结果 / 刚打开的线程里」这两种快照形态——那时
    `taskById()` 能读到任务、签名却不含它的 target，消息流就不会跟着重铺（#217 review 抓到）。
    按 target 去重；频道 target 不进签名（它不进看板 / 页签 / 指针消息行）。 */
function taskApprovalSignature() {
  const seen = new Set();
  const parts = [];
  [state.activeTask, ...state.tasks, ...state.boardTasks].forEach((task) => {
    const targetId = task && task.target_id;
    if (!targetId || seen.has(targetId)) return;
    seen.add(targetId);
    const count = pendingApprovalCountForTarget(targetId);
    if (count > 0) parts.push(targetId + ':' + count);
  });
  return parts.sort().join('|');
}

/** 只在派生集合变化时重铺看板 / 页签 / 消息流里的角标：未变化时不重建 DOM
    （不收起流转菜单、不丢滚动或焦点）。 */
function syncApprovalBadges() {
  const signature = taskApprovalSignature();
  if (signature === approvalBadgeSignature) return;
  approvalBadgeSignature = signature;
  renderTaskBoard();
  // #217：频道指针消息行上的角标读的是**同一个**派生集合（任务 target → pending 条数），
  // 因此它挂在这一个出口上重铺，不另开第二条路径。纪律不变：`signature` 未变化时上面就
  // 返回了，秒级 tick（只改倒计时文本）与未变化的 12s 轮询都不会重建消息行——重建会把正在
  // 点的控件换掉，也会让滚动位置跳动。
  // 两条收窄，都是为了不把「重铺消息流」变成一次多余的写：
  //   - `approvals: false`：`renderApprovalSurfaces()` 在调用本函数**之前**已经铺过频道审批板；
  //     让 `renderMessages()` 再铺一次会在同一拍里把审批卡整板重建两遍（按钮被换成新节点）。
  //   - 只在消息视图中重铺：看板视图里消息流整个是 `hidden` 的，重铺只会清空它；切回消息视图
  //     时 `render()` 会照常重铺（读数与这一拍同一个来源）。
  if (state.mode === 'channel') renderMessages({ approvals: false });
  else renderTabs();
}

function hasPendingApprovals() {
  for (const targetId of state.approvals.keys()) {
    if (pendingApprovalCountForTarget(targetId) > 0) return true;
  }
  return false;
}

/** 秒级刷新只改倒计时文本，不重建卡片：重建会让正在点的按钮被换掉（点击落空）。
    只有呈现态变化（pending → expired）时才重铺那一面。 */
function refreshApprovalCountdowns() {
  const surfaces = ['#approvalBoard', '#ttApprovals'];
  let needsRender = false;
  surfaces.forEach((selector) => {
    const root = $(selector);
    if (!root) return;
    root.querySelectorAll('.approval-card').forEach((card) => {
      const targetId = card.dataset.approvalTarget;
      const record = approvalMapFor(targetId).get(card.dataset.approvalId);
      if (!record) {
        needsRender = true;
        return;
      }
      const displayState = approvalDisplayState(record);
      if (displayState !== card.dataset.state) {
        needsRender = true;
        return;
      }
      const remaining = card.querySelector('.approval-remaining');
      if (remaining) remaining.textContent = approvalRemainingText(record, displayState);
    });
  });
  // 线程关着时两张审批列表里都没有卡：本地 600s 过期只能从**派生集合**（角标/圆点）看出来，
  // 因此除了逐卡比对，还要比对签名，让「角标到时消失」不依赖下一轮 12s 全量轮询。
  if (!needsRender && taskApprovalSignature() !== approvalBadgeSignature) needsRender = true;
  if (needsRender) {
    // 呈现态变化（pending → expired）同时是角标 / 圆点的输入：走同一个出口，
    // 不让「卡变灰了、角标还要等下一轮 12s 轮询」留下不一致窗口。
    renderApprovalSurfaces();
  }
}

/** 倒计时是一个「只在有 pending 时存在」的秒级刷新：归零那一刻换成 expired 并停表。 */
function syncApprovalTicker() {
  if (!hasPendingApprovals()) {
    if (approvalTicker !== null && typeof clearInterval === 'function') clearInterval(approvalTicker);
    approvalTicker = null;
    return;
  }
  if (approvalTicker !== null || typeof setInterval !== 'function') return;
  approvalTicker = setInterval(() => {
    refreshApprovalCountdowns();
    if (!hasPendingApprovals()) {
      if (typeof clearInterval === 'function') clearInterval(approvalTicker);
      approvalTicker = null;
    }
  }, 1000);
}

function renderApprovalSurfaces() {
  renderChannelApprovals();
  renderTaskApprovals();
  // #209：live 帧 / 快照 / 本地过期的同一条出口——角标与圆点只在派生集合变化时重铺。
  syncApprovalBadges();
  syncApprovalTicker();
}

function upsertApproval(targetId, record) {
  if (!targetId || !record || !record.reply_id) return;
  const replyId = String(record.reply_id);
  approvalMapFor(targetId).set(replyId, { ...record, target_id: record.target_id || targetId });
  renderApprovalSurfaces();
}

/** live `approval_required`：同一个 reply_id 的新帧只更新待办调用。

    #215：`reply_id` 是**一条回复**的标识，一条回复可以有多批确认——记录已经收口后，同一个
    `reply_id` 还会再来一帧，帧里只含这一批**新**调用。因此终态帧只在「没有新调用」时忽略
    （与后端 register 的幂等边界同形）；有新调用时按新帧重开，`decisions` 作为历史保留，
    `tool_calls` 与后端一样是**累积全量**（呈现与提交各自用 `approvalPendingToolCalls` 过滤），
    倒计时从这一批重新起算。 */
function absorbApprovalRequired(targetId, payload) {
  const replyId = String((payload && payload.reply_id) || '');
  if (!targetId || !replyId) return;
  const map = approvalMapFor(targetId);
  const existing = map.get(replyId);
  const incoming = Array.isArray(payload && payload.tool_calls) ? payload.tool_calls : [];
  const known = approvalCallIds((existing && existing.tool_calls) || []);
  const fresh = incoming.filter((call) => !known.has(String((call && call.id) || '')));
  if (existing && approvalDisplayState(existing) !== 'pending' && !fresh.length) return;
  const reopened = Boolean(existing) && approvalDisplayState(existing) !== 'pending';
  const record = {
    reply_id: replyId,
    target_id: targetId,
    member_id: String((payload && payload.member_id) || (existing && existing.member_id) || ''),
    // 与后端同形：镜像保存**累积**的调用，已决策的那些靠 decisions 过滤掉。
    tool_calls: reopened
      ? [...((existing && existing.tool_calls) || []), ...fresh]
      : incoming.length
        ? incoming
        : (existing && existing.tool_calls) || [],
    state: 'pending',
    decisions: existing ? existing.decisions : [],
    // 重开是**新的一批**：倒计时从这一批重新起算（后端同时刷新了 requested_at）。
    requested_at: reopened
      ? new Date().toISOString()
      : existing && existing.requested_at
        ? existing.requested_at
        : new Date().toISOString(),
    resolved_at: null,
  };
  map.set(replyId, record);
  renderApprovalSurfaces();
}

/** live `approval_resolved`：只更新已经出现在本会话里的卡；没有卡就不伪造一条。 */
function absorbApprovalResolved(targetId, payload) {
  const replyId = String((payload && payload.reply_id) || '');
  if (!targetId || !replyId) return;
  const map = approvalMapFor(targetId);
  const existing = map.get(replyId);
  if (!existing) return;
  map.set(replyId, {
    ...existing,
    state: String((payload && payload.state) || 'resolved'),
    decisions: Array.isArray(payload && payload.decisions) ? payload.decisions : existing.decisions,
    resolved_at: existing.resolved_at || new Date().toISOString(),
  });
  renderApprovalSurfaces();
}

/** 镜像里所有仍 pending 的记录，按 target 分组（供快照对账；`onlyTargetId` 用于单 target 读数）。 */
function pendingApprovalIndex(onlyTargetId) {
  const known = new Map();
  state.approvals.forEach((map, targetId) => {
    if (onlyTargetId && targetId !== onlyTargetId) return;
    map.forEach((record, replyId) => {
      if (approvalDisplayState(record) !== 'pending') return;
      if (!known.has(targetId)) known.set(targetId, new Set());
      known.get(targetId).add(replyId);
    });
  });
  return known;
}

/** 审批记录的呈现指纹：只覆盖会进入 DOM / 角标的字段，用来判断一次快照是否真的带来变化。 */
function approvalRecordFingerprint(record) {
  return JSON.stringify([
    record && record.target_id,
    record && record.state,
    record && record.member_id,
    record && record.requested_at,
    record && record.resolved_at,
    record && record.tool_calls,
    record && record.decisions,
  ]);
}

/** 把一份 pending 快照并入 `state.approvals` 的**唯一**写入口：按 `record.target_id`
    （单 target 读数可回退到 `fallbackTargetId`）归属；`known` 里消失的 pending 标
    `expired`（不伪造记录，与 D19 的进程重启语义一致）。两种读数（按 target / 全工作区）
    共用这一段，避免「合并 + 消失判定」各自漂。返回快照是否真的改变了镜像——周期刷新据此
    在「没有任何变化」时不重铺审批卡（按钮不会被无谓换掉）。 */
function applyApprovalSnapshot(records, known, fallbackTargetId) {
  let changed = false;
  const seen = new Map();
  records.forEach((record) => {
    if (!record || !record.reply_id) return;
    const targetId = String(record.target_id || fallbackTargetId || '');
    if (!targetId) return;
    const replyId = String(record.reply_id);
    if (!seen.has(targetId)) seen.set(targetId, new Set());
    seen.get(targetId).add(replyId);
    const map = approvalMapFor(targetId);
    const next = { ...record, target_id: targetId };
    if (approvalRecordFingerprint(map.get(replyId)) !== approvalRecordFingerprint(next)) {
      map.set(replyId, next);
      changed = true;
    }
  });
  known.forEach((replyIds, targetId) => {
    const present = seen.get(targetId) || new Set();
    const map = approvalMapFor(targetId);
    replyIds.forEach((replyId) => {
      if (present.has(replyId)) return;
      const record = map.get(replyId);
      if (record && approvalDisplayState(record) === 'pending') {
        map.set(replyId, { ...record, state: 'expired', resolved_at: new Date().toISOString() });
        changed = true;
      }
    });
  });
  return changed;
}

/** 打开视图 / 重连后的恢复：只读 pending 快照；本次已知但快照里消失的 pending 视为已过期
    （进程重启会把 pending 标 expired——D19）。fetch 失败不阻塞页面打开。 */
async function loadApprovalsFor(targetId) {
  if (!targetId) return;
  // 「已知」在请求发出前抓取：在途期间到达的 live 帧不会被这一轮误标过期。
  const known = pendingApprovalIndex(targetId);
  let payload;
  try {
    payload = await api('/api/approvals?target_id=' + encodeURIComponent(targetId) + '&state=pending');
  } catch {
    return;
  }
  const records = Array.isArray(payload && payload.approvals) ? payload.approvals : [];
  applyApprovalSnapshot(records, known, targetId);
  renderApprovalSurfaces();
}

/** #209：全工作区 pending 读数（不带 `target_id`）。进入工作区 / 重连后读一次，
    页面可见且在线时由 `startApprovalRefresh()` 有界周期刷新。

    - 记录按 `record.target_id` 归属到既有的 `state.approvals`（与 `loadApprovalsFor` 同一个写入口）；
    - 快照里消失的 pending 沿用同一语义标 `expired`（不伪造记录）；
    - 「已知」集合在请求发出**之前**抓取：在途期间到达的 live 帧不会被这一轮误标过期；
    - 同一时刻最多一个在途请求；失败静默返回（角标不阻塞界面、不 toast）。 */
async function loadPendingApprovals(options) {
  if (approvalRefreshInFlight) return;
  approvalRefreshInFlight = true;
  const known = pendingApprovalIndex();
  let payload = null;
  try {
    payload = await api('/api/approvals?state=pending');
  } catch {
    return;
  } finally {
    approvalRefreshInFlight = false;
  }
  const records = Array.isArray(payload && payload.approvals) ? payload.approvals : [];
  const changed = applyApprovalSnapshot(records, known);
  // 入口 / 重连那一次无条件重铺（消息面可能刚切过）；周期刷新只在快照真的变了才重铺，
  // 否则每 12s 换掉一次审批卡按钮会把「正在点的按钮」弄丢。
  if (changed || (options && options.render)) renderApprovalSurfaces();
}

/** 周期刷新的单例定时器：页面隐藏或离线时整轮跳过；在途闸门在 `loadPendingApprovals` 里。 */
function startApprovalRefresh() {
  if (approvalRefreshTimer !== null || typeof setInterval !== 'function') return;
  approvalRefreshTimer = setInterval(() => {
    if (typeof document !== 'undefined' && document.hidden) return;
    if (!state.online) return;
    loadPendingApprovals();
  }, APPROVAL_REFRESH_MS);
}

/** 提交失败（例如 409：另一处已决策 / 已过期）时按 target 重读真实状态，不猜。 */
async function reconcileApproval(targetId, replyId) {
  if (!targetId || !replyId) return;
  try {
    const payload = await api('/api/approvals?target_id=' + encodeURIComponent(targetId));
    const records = Array.isArray(payload && payload.approvals) ? payload.approvals : [];
    const found = records.find((record) => String(record.reply_id) === String(replyId));
    const map = approvalMapFor(targetId);
    if (found) {
      map.set(String(replyId), { ...found, target_id: found.target_id || targetId });
    } else {
      const record = map.get(String(replyId));
      if (record && approvalDisplayState(record) === 'pending') {
        map.set(String(replyId), { ...record, state: 'expired', resolved_at: new Date().toISOString() });
      }
    }
  } catch {
    // 读不到就保留本地状态；错误行已经显示在卡片上。
  }
  renderApprovalSurfaces();
}

async function submitApproval(targetId, replyId, confirmed) {
  const id = String(replyId || '');
  if (!id || state.approvalInFlight.has(id)) return;
  const record = approvalMapFor(targetId).get(id);
  if (!record || approvalDisplayState(record) !== 'pending') return;
  state.approvalInFlight.add(id);
  state.approvalErrors.delete(id);
  renderApprovalSurfaces();
  try {
    // 只提交尚未决策的调用（#215）：已决策的旧调用不能再带上——相反结论会整批 409。
    const decisions = approvalPendingToolCalls(record)
      .map((call) => ({
        tool_call_id: String((call && call.id) || ''),
        confirmed: Boolean(confirmed),
      }))
      .filter((decision) => decision.tool_call_id);
    const payload = await api('/api/approvals/' + encodeURIComponent(id), {
      method: 'POST',
      body: { decisions },
    });
    if (payload && payload.approval) upsertApproval(targetId, payload.approval);
  } catch (err) {
    state.approvalErrors.set(id, err.message);
    await reconcileApproval(targetId, id);
  } finally {
    state.approvalInFlight.delete(id);
    renderApprovalSurfaces();
  }
}

async function refreshTaskDetails(taskId) {
  try {
    const payload = await api(taskPath(taskId));
    if (state.activeTaskId !== taskId) return;
    state.activeTask = payload.task;
    const index = state.tasks.findIndex((task) => task.id === taskId);
    if (index >= 0) state.tasks[index] = payload.task;
    renderTaskThread();
    renderTaskBoard();
  } catch {
    // 线程已可见时保留上一份快照；下一条事件或手动重开会再同步。
  }
}

/** 新建任务：弹窗形态（原型 `#btnNewTask` → `openNewTask()`），字段收窄为
    「标题 + 所属频道」——与搬过来的内联表单采集的字段等价，不借机新增输入路径。
    这里的频道下拉是**所属频道**（任务的归属），不是看板的筛选。

    `prefillTitle` 只有「作为任务发送」那条路径会传（规格决策 D6）：它同时充当「任务建成后是否
    清空 composer」的标记——看板工具栏的 `#btnNewTask` 不带正文，因此不会清空输入框。 */
function openTaskModal(prefillTitle) {
  forTaskFromComposer = typeof prefillTitle === 'string';
  $('#taskFormError').hidden = true;
  $('#taskCreateTitle').value = forTaskFromComposer ? prefillTitle : '';
  const select = $('#taskCreateChannel');
  fillTaskChannelSelect(select, false, state.activeChannelId || '');
  $('#taskCreateSubmit').disabled = !state.online || state.channels.length === 0;
  $('#taskOverlay').hidden = false;
  $('#taskCreateTitle').focus();
}

/** 「作为任务发送」（工具行）是否正带着 composer 正文：任务建成后要清空 composer（D6）。 */
let forTaskFromComposer = false;

/** 工具行「作为任务发送」：打开**既有**新建任务弹窗，把 composer 正文预填为标题、当前频道预选。
    确认走既有的 `POST /api/channels/{id}/tasks`（`submitTask`），取消则 composer 一字不动。 */
function openTaskFromComposer() {
  openTaskModal($('#composerInput').value);
}

function closeTaskModal() {
  $('#taskOverlay').hidden = true;
}

async function submitTask() {
  const channelId = $('#taskCreateChannel').value;
  const title = $('#taskCreateTitle').value.trim();
  const box = $('#taskFormError');
  if (!title) {
    // 弹窗不再是 `<form>`（按钮是 type=button），因此没有浏览器自带的 required 提示：
    // 空标题要自己说出来，不能默默什么都不发生。
    box.textContent = state.channels.length ? t('task.titleRequired') : t('task.noChannel');
    box.hidden = false;
    $('#taskCreateTitle').focus();
    return;
  }
  const button = $('#taskCreateSubmit');
  button.disabled = true;
  box.hidden = true;
  try {
    const payload = await api(channelPath(channelId, '/tasks'), { method: 'POST', body: { title } });
    closeTaskModal();
    // 建任务在频道流里留下一条 system 指针消息（#198）：把它当作一条到达的消息吸收，
    // 于是消息流立刻出现该行，人不用等下一次整页刷新（system 不计未读，页签读数不变）。
    // 其它频道只对齐频道列表读数。
    // 任务先补进快照：chip 的未读数读 `taskById(...).unread_count`（#223），快照没到就按 0 处理，
    // 不显示计数段。
    if (payload.message) {
      const index = state.tasks.findIndex((task) => task.id === payload.task.id);
      if (index >= 0) state.tasks[index] = payload.task;
      else state.tasks.push(payload.task);
      absorbMessage(channelId, payload.message);
      if (state.activeChannelId === channelId) {
        renderMessages();
        scrollToBottom();
      } else {
        await refreshChannelList();
      }
    }
    if (forTaskFromComposer) {
      forTaskFromComposer = false;
      resetComposerInput(); // D6：正文已变成任务标题，成功后不再留在输入框里。
    }
    // 新任务要能看见：把筛选放到「能让它可见」的那一档，而不是让它落在一个被筛掉的频道之外。
    if (!state.taskFilterChannelId || state.taskFilterChannelId === channelId) {
      await refreshTasks();
    } else {
      state.taskFilterChannelId = channelId;
      await refreshTasks();
    }
    renderTaskBoard();
    toast(t('toast.taskCreated', { title: payload.task.title }));
  } catch (err) {
    box.textContent = err.message;
    box.hidden = false;
  } finally {
    button.disabled = !state.online || state.channels.length === 0;
  }
}

/** 标题就地保存：`PATCH /api/tasks/{id}/title`（`CONTEXT.md` **任务**：任务行是可变的）。
    保存成功后后端会追加一条 `system` 消息，因此「状态变更记录」里会出现这一行。 */
async function saveTaskTitle() {
  const task = state.activeTask;
  if (!task) return;
  const input = $('#ttTitleInput');
  const title = input.value.trim();
  if (!title || title === task.title) {
    input.value = task.title;
    return;
  }
  try {
    const payload = await api(taskPath(task.id, '/title'), { method: 'PATCH', body: { title } });
    state.activeTask = payload.task;
    // 变更记录是本票要展示的事实：把后端返回的那条 system 消息立即收进线程。
    if (payload.message) absorbTaskMessage(payload.message);
    await refreshTasks();
    renderTaskThread();
  } catch (err) {
    toast(t('toast.titleUpdateFailed', { reason: err.message }), 'err');
    renderTaskThread();
  }
}

/** 改状态：看板拖拽、卡片「流转」菜单、线程 chips 三条入口都走这一条函数，
    命中同一个受守卫的 `PATCH /api/tasks/{id}/status`（#191 D3）——不另立第二套语义。

    前端守卫只读载荷：`allowed_transitions` 不含目标就不发请求；但**不做乐观更新**——
    成功后按响应与重读刷新，失败时用户看到的是后端事实（D4）。 */
async function changeTaskStatus(taskId, target) {
  const task = taskById(taskId);
  if (!task || target === task.status) return;
  // chip / 菜单目标都是呈现，不变量在写路径。这里读的是**当前载荷**：若目标已不在
  // `allowed_transitions` 里（菜单/拖拽打开期间状态被他处改过），不发那次注定失败的请求，
  // 但仍把「不是合法迁移」这条事实说出来——静默什么都不发生，人只会以为界面坏了。
  if (!taskAllowedTransitions(task).includes(target)) {
    toast(t('toast.transitionInvalid', {
      from: taskStatusLabel(task.status),
      to: taskStatusLabel(target),
    }), 'err');
    return;
  }
  const isOpenThread = state.activeTaskId === taskId;
  try {
    const payload = await api(taskPath(taskId, '/status'), { method: 'PATCH', body: { status: target } });
    if (isOpenThread) {
      state.activeTask = payload.task;
      if (payload.message) absorbTaskMessage(payload.message);
    }
    await refreshTasks();
    if (state.activeTaskId === taskId) renderTaskThread();
  } catch (err) {
    toast(t('toast.statusUpdateFailed', { reason: err.message }), 'err');
    // 失败路径也回到后端事实：先重读看板，线程仍开着才重铺线程。
    await refreshTasks();
    if (state.activeTaskId === taskId) renderTaskThread();
  }
}

async function changeTaskAssignee(memberId) {
  const task = state.activeTask;
  if (!task) return;
  try {
    const payload = await api(taskPath(task.id, '/assignee'), {
      method: 'PATCH', body: { member_id: memberId || null },
    });
    state.activeTask = payload.task;
    if (payload.message) absorbTaskMessage(payload.message);
    // 指派写路径会改该 target 的成员行；chip 候选来自**频道**成员表（未变），
    // 因此这里只需重读任务面与线程，不必再拉成员表。
    await refreshTasks();
    renderTaskThread();
  } catch (err) {
    toast(t('toast.assigneeUpdateFailed', { reason: err.message }), 'err');
    renderTaskThread();
  }
}

async function sendTaskMessage() {
  const task = state.activeTask;
  const input = $('#taskComposerInput');
  const body = input.value.trim();
  if (!task || !body || !state.online) return;
  $('#taskSend').disabled = true;
  try {
    const payload = await api(taskPath(task.id, '/messages'), {
      method: 'POST', body: { body, task_ref: task.id },
    });
    input.value = '';
    input.style.height = 'auto';
    absorbTaskMessage(payload.message);
    await markTaskRead(task.id, payload.message.seq);
    await refreshTasks();
  } catch (err) {
    toast(t('toast.taskMessageFailed', { reason: err.message }), 'err');
  } finally {
    $('#taskSend').disabled = !state.online || !input.value.trim();
  }
}

function replaceDm(dm) {
  const index = state.dms.findIndex((item) => item.id === dm.id);
  if (index >= 0) state.dms[index] = dm;
  else state.dms.push(dm);
}

function replaceTarget(target) {
  if (!target) return;
  if (target.kind === 'dm') replaceDm(target);
  else replaceChannel(target);
}

function seenSeqsFor(targetId) {
  if (!state.seenSeqs.has(targetId)) state.seenSeqs.set(targetId, new Set());
  return state.seenSeqs.get(targetId);
}

function lastSeqOf(targetId) {
  const target = state.channels.find((item) => item.id === targetId)
    || state.dms.find((item) => item.id === targetId);
  const known = seenSeqsFor(targetId);
  let max = target ? Number(target.last_seq) || 0 : 0;
  known.forEach((seq) => { if (seq > max) max = seq; });
  return max;
}

/* ─── 标读：切入频道、以及正在查看时到达的新消息都立即已读 ─────────────── */
// 每个频道各持一个待发标读定时器：离开 A 去 B 时，A 的标读不能被 B 的覆盖掉
// （共用一个定时器会把前一个 clearTimeout 掉，那个频道的已读推进就被静默丢弃）。
const readTimers = new Map();

function markCurrentRead() {
  const target = activeTarget();
  if (!target) return;
  const rendered = renderedMessages();
  // 「看到哪一条」的唯一表达式是 `lastSeqOf`：后端 `last_seq` 与本地已见集合的较大值。
  // 全局流没有 per-target 游标，但这份读数仍是标读的上界（到达即已读）。
  const maxSeq = Math.max(lastSeqOf(target.id), ...rendered.filter((m) => m.seq).map((m) => m.seq), 0);
  if (maxSeq <= target.last_read_seq) return;
  scheduleRead(target.id, maxSeq);
}

function scheduleRead(targetId, seq) {
  const existing = readTimers.get(targetId);
  if (existing) clearTimeout(existing);
  readTimers.set(
    targetId,
    setTimeout(async () => {
      readTimers.delete(targetId);
      try {
        const payload = await api(targetPath(targetId, '/read'), { method: 'POST', body: { seq } });
        if (payload.dm) {
          state.dms = payload.dms;
          replaceDm(payload.dm);
        } else {
          replaceChannel(payload.channel);
          state.channels = payload.channels;
          const active = activeChannel();
          if (active) replaceChannel(active);
        }
        render();
      } catch (err) {
        toast(t('toast.readFailed', { reason: err.message }), 'err');
      }
    }, 120),
  );
}

/* ─── 乐观发送：pending → 确认 seq 或显式失败 ─────────────────────────── */
function renderedMessages() {
  const channelId = state.activeChannelId;
  if (!channelId) return [];
  // 只渲染当前频道的 pending（含已失败但仍保留在本地的项）：失败态不得串到别的频道，
  // 也不得把别的频道的内容算进本频道的顺序里。
  const pending = state.pending.filter((item) => item.target_id === channelId);
  return state.messages.concat(pending);
}

function sendMessage() {
  const input = $('#composerInput');
  const body = input.value.trim();
  const target = activeTarget();
  if (!body || !target) return;
  // 已归档私信只读：输入框与发送键都已经禁用，这里仍是 Enter / 程序化调用的**唯一**闸门。
  if (activeArchivedDm()) return;
  if (!state.online) {
    toast(t('toast.sendBlockedOffline'), 'err');
    return;
  }
  // 上传还在飞时**不发**：这条消息会漏掉那个人刚选好、还没拿到标识的文件。
  // 发送键在 `syncSendButton()` 里也是这个理由被禁用；但 Enter 走的是这条路径，
  // 所以闸门必须在这里也有一道（否则键盘用户能绕过禁用态，静默漏掉附件）。
  if (attachingCount > 0) {
    toast(t('toast.attachPending', { count: attachingCount }), 'err');
    return;
  }
  // 草稿随这条消息一起走：它们既不留在 composer（已经属于这条 pending），也在失败时随 pending
  // 一起保留（重试会带上同一批标识）。
  const attachments = state.composerAttachments.slice();
  const pending = {
    clientId: 'pending-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7),
    target_id: target.id,
    body,
    status: 'pending',
    created_at: new Date().toISOString(),
    author: state.member,
    attachments,
  };
  state.pending.push(pending);
  resetComposerInput();
  clearComposerAttachments();
  renderMessages();
  scrollToBottom();
  deliver(pending);
}

async function deliver(pending) {
  try {
    const payload = await api(targetPath(pending.target_id, '/messages'), {
      method: 'POST',
      body: {
        body: pending.body,
        attachment_ids: (pending.attachments || []).map((item) => item.id),
      },
    });
    removePending(pending.clientId);
    // 消息本身由事件流按 seq 追加（同一帧可能已经到达），这里只保证它一定在。
    absorbMessage(pending.target_id, payload.message);
    state.online = true;
    renderMessages();
    scrollToBottom();
    await loadWorkspace({ silent: true, keepActive: true });
    // 正在查看该频道时到达的新消息也立即已读。
    markCurrentRead();
  } catch (err) {
    failPending(pending.clientId, err.message);
    if (!err.status) {
      state.online = false;
      toast(t('toast.sendMessageLost'), 'err');
    } else {
      toast(t('toast.sendRejected', { reason: err.message }), 'err');
    }
    render();
  }
}

/* ─── 事件流（SSE）：整页一条全局流；断线后带最新事实重开 ──────────────────
   为什么是**一条全局流**：浏览器对单一 origin 的 HTTP/1.1 并发连接上限是 6 条，而 uvicorn 没有
   多路复用。按 target 各开一条常驻 `EventSource`（2 频道 + 4 私信就是 6 条）会把连接池占满，
   之后任何 `fetch`（发消息、切频道、刷新列表）与 F5 的文档请求都在排队——页面渲染出来了但
   完全不响应。全局流把「连接数」与「target 数」解耦成两件事。

   重连策略不变：断线时**主动关掉连接、稍后重开**，而不是依赖浏览器带 `Last-Event-ID` 自动重连
   ——全局流不补拉（它没有单一 target 的游标），断线期间的变化由重连成功后的
   `loadWorkspace({ silent: true, keepActive: true })` 全量重取补齐。
*/
const STREAM_RETRY_MS = 1500;
// 整页唯一的全局事件流端点：任意 target 的事件都从这里来，`target_id` 是帧内的归属正本。
const GLOBAL_EVENT_PATH = '/api/events';

/* live-only 运行事件（规格 §4.5 / ADR-0020）：`agent_event` / `team_assignment` / `plan_update` /
   `sop_event` / `approval_*`。它们没有可补拉的 seq，因此：
   - **不**写 `seenSeqs`（那份集合同时是重连游标的上界）；
   - **不**进消息流 / 未读 / 搜索；
   - 由 `recordLiveEvent` 按 `target_id` 有界保留，运行面板（#155）从这里取；
   - 未知 `type` 一律忽略：后端词表以后可能变大，旧前端不能因此报错或推进游标。 */
const LIVE_EVENT_TYPES = [
  'agent_event',
  'team_assignment',
  'plan_update',
  'sop_event',
  'approval_required',
  'approval_resolved',
  'round.interrupted',
  'round.active',
];
const LIVE_EVENTS_LIMIT = 256;

function isLiveEventType(type) {
  return LIVE_EVENT_TYPES.indexOf(type) !== -1;
}

function recordLiveEvent(targetId, type, payload) {
  if (!isLiveEventType(type)) return null;
  const key = targetId || payload.target_id;
  if (!key) return null;
  const events = state.liveEvents.get(key) || [];
  events.push(payload);
  // 有界保留：live-only 事件不会补拉，保留旧事件只为本次会话的运行面板。
  if (events.length > LIVE_EVENTS_LIMIT) events.splice(0, events.length - LIVE_EVENTS_LIMIT);
  state.liveEvents.set(key, events);
  // SOP 没有「按 target 列 run」的读面：只有 live 帧见过 run id 之后，重连兜底才能
  // 逐个 `GET /api/sop-runs/{id}` 刷新。这里只记 id，不触发写路径。
  if (type === 'sop_event') noteRunPanelSopId(key, payload);
  // 审批卡（#153）：live 帧是「本次会话内的变化」，快照兜底由 `loadApprovalsFor` 负责。
  if (type === 'approval_required') absorbApprovalRequired(key, payload);
  else if (type === 'approval_resolved') absorbApprovalResolved(key, payload);
  if (type === 'agent_event' && payload.reply_id) {
    if (payload.event_type === 'REPLY_START') {
      const rounds = state.activeRounds[key] || (state.activeRounds[key] = {});
      rounds[payload.reply_id] = payload.member_id;
      showTypingFor(key, payload.member_id);
    } else if (payload.event_type === 'REPLY_END') finishRoundFor(key, payload.reply_id);
  } else if (type === 'round.interrupted') {
    (payload.reply_ids || (payload.reply_id ? [payload.reply_id] : [])).forEach((replyId) => finishRoundFor(key, replyId));
  } else if (type === 'round.active') {
    state.activeRounds[key] = payload.rounds || {};
    showTypingFor(key, Object.values(state.activeRounds[key])[0] || null);
  }
  return payload;
}

// 是否因为后端不可达而断开（决定重连成功后要不要做一次全量重取）。
let wasOffline = false;

function syncStreams() {
  // 整页**至多一条**流：target 集合的增减不再开关连接（连接数与 target 数解耦）。
  // 全局流收到任意 target 的事件，归属由帧里的 `target_id` 给出（见 `handleGlobalStreamEvent`）。
  if (typeof EventSource === 'undefined') return; // 测试替身 / 极旧浏览器：没有流也不抛
  if (state.stream) return;
  const entry = { source: null, timer: null, rotating: false };
  state.stream = entry;
  connectStream(entry);
}

function connectStream(entry) {
  if (typeof EventSource === 'undefined') return;
  // 全局流不带 target、不带游标：它不补拉（重连补齐由 `loadWorkspace` 的全量重取承担）。
  const source = new EventSource(GLOBAL_EVENT_PATH);
  entry.source = source;
  // 服务端的例行轮换（event: rotate）不算掉线：它只是要求重新连一次。
  entry.rotating = false;
  source.addEventListener('rotate', () => {
    entry.rotating = true;
    source.close();
    entry.timer = setTimeout(() => connectStream(entry), 250);
  });
  source.onopen = () => {
    const recovered = state.streamState !== 'open';
    state.streamState = 'open';
    // 重连成功 = 后端回来了：先把「不可达」这个状态清掉，再从前端重取一次快照，
    // 保证断线期间的变化都被补齐（不依赖「只补消息」这一件事）。
    if (state.online === false) state.online = true;
    renderConnection();
    if (recovered && wasOffline) {
      wasOffline = false;
      loadWorkspace({ silent: true, keepActive: true });
    }
    // 运行面板的 live 事件不补拉（ADR-0020）：重连成功后按快照兜底，把断线期间
    // 的分派 / Plan / SOP 变化读回来。面板没开时是空操作。
    if (recovered) {
      refreshRunPanelFor();
      // 审批同理：审批帧不补发。全局流一断开就是**所有** target 都漏了帧，
      // 因此按全工作区 pending 兜底（与 `loadWorkspace` 同一个写入口），不猜是哪一个 target。
      loadPendingApprovals({ render: true });
    }
  };
  source.onerror = () => {
    if (entry.rotating) return; // 我们自己关的：不是错误
    // 事件流断开是「后端不可达」最直接的证据：它就是后端的连接。因此这里把线上态置为不可达，
    // 发送被禁、横幅显示，并启动自动重试 —— 不需要人去点重试按钮。
    state.streamState = 'reconnecting';
    state.online = false;
    wasOffline = true;
    renderConnection();
    render();
    // 断开期间不可能有 in-flight 事实需要显示：清掉「正在回复」，等重连后的状态快照说实话。
    // 全局流断了就是**所有** target 都断了；唯一例外是任务 dock 自己那条流（`connectTaskStream`），
    // 它还活着，它的「正在回复」不该被这里抹掉。
    const taskTargetId = state.activeTask ? state.activeTask.target_id : null;
    Object.keys(state.activeRounds).forEach((key) => {
      if (key !== taskTargetId) delete state.activeRounds[key];
    });
    Object.keys(state.typing).forEach((key) => {
      if (key !== taskTargetId) delete state.typing[key];
    });
    renderTyping();
    source.close();
    entry.timer = setTimeout(() => connectStream(entry), STREAM_RETRY_MS);
  };
  ['message', 'member_status', 'typing', 'round_outcome', ...LIVE_EVENT_TYPES].forEach((type) => {
    source.addEventListener(type, (event) => handleGlobalStreamEvent(event, type));
  });
}

/** 全局流的帧分流：`payload.target_id` 是归属正本，然后完全复用既有的按 target 消费。

    缺 `target_id` 的帧**丢弃**：不猜归属，也不写任何 target 的 `seenSeqs`——那份集合同时是
    「我已经看到哪一条」的游标（`lastSeqOf`），错记一次就会让全量重取的兜底少一条。
*/
function handleGlobalStreamEvent(event, type) {
  let payload = null;
  try {
    payload = JSON.parse(event.data);
  } catch {
    return; // 解不开的帧直接丢弃：它不可能是后端发出的
  }
  const targetId = payload && payload.target_id;
  if (!targetId) return;
  handleStreamEvent(targetId, event, type);
}

function handleStreamEvent(targetId, event, type) {
  let payload = null;
  try {
    payload = JSON.parse(event.data);
  } catch {
    return; // 解不开的帧直接丢弃：它不可能是后端发出的
  }
  // live-only 运行事件：按 target 归属记录，绝不写消息流 / 游标。
  if (isLiveEventType(type)) {
    recordLiveEvent(payload.target_id || targetId, type, payload);
    return;
  }
  // 未知 type（词表以后可能变大）：忽略且不报错，也**不**把它记进 `seenSeqs`——
  // 那份集合同时决定重连游标，未知事件没有可补拉的正本。
  if (type !== 'message' && type !== 'member_status' && type !== 'typing' && type !== 'round_outcome') return;
  if (type === 'message') {
    if (!payload.message) return;
    // 消息的去重正本在 absorbMessage 里（按 seq 认一条消息），这里不另做一套。
    onStreamMessage(targetId, payload);
    return;
  }
  const seq = Number(payload.seq) || 0;
  if (seq) seenSeqsFor(targetId).add(seq);
  if (type === 'member_status') onStreamStatus(targetId, payload);
  else if (type === 'typing') onStreamTyping(targetId, payload);
  else if (type === 'round_outcome') onStreamOutcome(targetId, payload);
}

function absorbMessage(targetId, message) {
  const known = seenSeqsFor(targetId);
  if (known.has(message.seq)) return false;
  known.add(message.seq);
  const target = state.channels.find((item) => item.id === targetId)
    || state.dms.find((item) => item.id === targetId);
  if (target) target.last_seq = Math.max(Number(target.last_seq) || 0, message.seq);
  if (state.activeChannelId === targetId) {
    state.messages.push(message);
    state.messages.sort((a, b) => a.seq - b.seq);
  }
  return true;
}

function onStreamMessage(targetId, payload) {
  const added = absorbMessage(targetId, payload.message);
  if (!added) return;
  if (state.activeChannelId === targetId) {
    renderMessages();
    scrollToBottom();
    // 正在查看该 target：到达即已读。
    markCurrentRead();
  }
  // 其它 target 的未读徽标也要跟着动：从后端重新取计数（未读的正本在库）。
  if (state.dms.some((dm) => dm.id === targetId)) refreshDmList();
  else refreshChannelList();
}

async function refreshDmList() {
  try {
    const payload = await api('/api/dms');
    // 只认形状正确的读数：拿不到 `dms` 数组时保留旧镜像（与读失败同一条语义），
    // 不让一次畸形应答把整份镜像换成 `undefined`。
    if (!payload || !Array.isArray(payload.dms)) return;
    state.dms = payload.dms;
    renderDms();
  } catch {
    // 保留上一次已知的计数：宁可略陈旧，也不伪造。
  }
}

async function refreshChannelList() {
  try {
    const payload = await api('/api/channels');
    state.channels = payload.channels;
    const active = activeChannel();
    if (active) replaceChannel(active);
    renderChannels();
  } catch {
    // 保留上一次已知的计数：宁可略陈旧，也不伪造。
  }
}

function onStreamStatus(targetId, payload) {
  const previous = state.statuses[payload.member_id];
  state.statuses[payload.member_id] = payload.status;
  state.agents = state.agents.map((agent) =>
    agent.id === payload.member_id ? { ...agent, status: payload.status } : agent,
  );
  if (payload.status !== 'working') showTypingFor(targetId, null);
  renderMembers();
  renderDms();
  renderDock();
  renderRoster(); // 名册若开着，它的状态点也必须跟着后端事实走（关着时是空操作）
  // 详情面板开着时它的状态点同样只能来自后端事实（关着时是空操作）。
  if (state.activeAgentId) renderAgentDetail();
  // 已呈现的轮次结果行里的状态描述（「未启用」/「这一轮失败」）必须跟着后端事实走，
  // 不能停在一句已经不成立的话上。没呈现过该成员就不重建消息流。
  if (state.activeChannelId === targetId && previous !== payload.status && outcomeMembers().has(payload.member_id)) {
    renderMessages();
  }
}

function onStreamTyping(targetId, payload) {
  if (payload.active) showTypingFor(targetId, payload.member_id);
  else {
    const rounds = state.activeRounds[targetId] || {};
    Object.keys(rounds).forEach((replyId) => {
      if (rounds[replyId] === payload.member_id) delete rounds[replyId];
    });
    showTypingFor(targetId, Object.values(rounds)[0] || null);
  }
  if (payload.active) {
    state.statuses[payload.member_id] = 'working';
  } else if (state.statuses[payload.member_id] === 'working') {
    state.statuses[payload.member_id] = 'online';
  }
  renderMembers();
  renderDock();
  renderRoster(); // 名册若开着，它的状态点也必须跟着后端事实走（关着时是空操作）
  if (state.activeAgentId) renderAgentDetail();
}

function showTypingFor(targetId, memberId) {
  if (memberId) state.typing[targetId] = memberId;
  else delete state.typing[targetId];
  if (state.activeChannelId === targetId || (state.activeTask && state.activeTask.target_id === targetId)) renderTyping();
}

function onStreamOutcome(targetId, payload) {
  const outcome = payload.outcome;
  if (!outcome) return;
  if (outcome.status === 'failed') state.statuses[outcome.member_id] = 'error';
  else if (state.statuses[outcome.member_id] === 'working') state.statuses[outcome.member_id] = 'online';
  let added = false;
  if (state.activeChannelId === targetId && Number.isInteger(outcome.base_seq)) {
    const byMember = state.outcomes[outcome.base_seq] || (state.outcomes[outcome.base_seq] = {});
    const records = byMember[outcome.member_id] || (byMember[outcome.member_id] = []);
    // 同一 id 只留一条：实时推送与断线补拉说的是同一件事，不重复呈现。
    if (!records.some((record) => record.id === outcome.id)) {
      records.push(outcome);
      added = true;
    }
    // 回复序号的索引也要跟着更新：刚到达的这条结果可能是某条旁白消息**自己那一轮**的结论，
    // 而那条消息可能先于它到达（`replied` 的消息先落库、结果后收口）。
    if (Number.isInteger(outcome.reply_seq)) state.replyOutcomes[outcome.reply_seq] = outcome;
  }
  renderMembers();
  renderDock();
  renderRoster(); // 名册若开着，它的状态点也必须跟着后端事实走（关着时是空操作）
  // 详情面板开着时：这个成员自己刚落了一条轮次结果 ⇒ 概览的四个数字（库里聚合而来）要重读一次；
  // 别人的轮次结果只可能改变状态点，重绘即可。
  if (state.activeAgentId === outcome.member_id) refreshAgentDetail(outcome.member_id);
  else if (state.activeAgentId) renderAgentDetail();
  // 五种结果都要重建消息流：只有 `failed` 会被状态点推导拦住（`ignored` 等三态**不是**失败，
  // 状态点仍空闲），但它们的摘要行同样要在不刷新页面的情况下出现。
  if (state.activeChannelId === targetId && added) renderMessages();
  if (state.activeTask && state.activeTask.target_id === targetId) {
    state.taskOutcomes[outcome.id] = outcome;
    renderTaskThread();
  }
}

// 哪些成员在消息流里有轮次结果呈现（用于状态点变化时只重渲染该重的那部分）。
function outcomeMembers() {
  const ids = new Set();
  Object.keys(state.outcomes).forEach((seq) => {
    Object.keys(state.outcomes[seq]).forEach((memberId) => ids.add(memberId));
  });
  return ids;
}

function removePending(clientId) {
  state.pending = state.pending.filter((item) => item.clientId !== clientId);
}

function failPending(clientId, reason) {
  const pending = state.pending.find((item) => item.clientId === clientId);
  if (!pending) return;
  pending.status = 'failed';
  pending.reason = reason;
}

function discardPending(clientId) {
  removePending(clientId);
  renderMessages();
}

/* ─── 渲染 ──────────────────────────────────────────────────────────── */
function render() {
  renderIdentity();
  renderReminderDot();
  renderChannels();
  renderDms();
  renderMembers();
  renderTaskBoard();
  // dock 的三种形态共用同一个 380px 容器：每次整体重绘都把形态重新同步一遍（形态由
  // 「现在打开着哪一个对象」推导，见 `setDockMode()`），否则某次局部重建会把形态留在上一帧。
  // 运行面板是显式打开的面：它开着时压过任务线程，关闭时再落回它。
  // Agent 详情不在这条推导里：它是主线视图（`state.mode === 'agent'`），dock 在那种视图下让位。
  setDockMode(
    state.runPanel.open ? 'run' : (state.activeTaskId ? 'task' : 'agent'),
  );
  // 「私信下不把面板留在右侧」的唯一落点：切 target 与重载工作台都经过 `render()`，
  // 因此不靠每个入口各关一次。有任务线程 / 运行面板时 `dockAvailable()` 为真，
  // 这条不会误关一个独立形态。
  if (!dockAvailable() && $('#dock') && !$('#dock').hidden) setDockOpen(false);
  if (state.activeTask) renderTaskThread();
  syncTaskViewSeg();
  renderHeader();
  setViewMode(state.mode);
  renderMessages();
  // 顶部栏（≤900px）显示**当前 target 的标题**：它跟着 target 走，因此和 header 一起刷。
  renderMobileTopbar();
  // 搜索面的文案（facet / 结果行 / 空态）都按词条取值，因此它和别的面一样随整体重绘刷新——
  // 切语言后搜索结果不会留在上一门语言里。
  renderSearchFacets();
  renderSearchResults();
  renderConnection();
  renderDock();
  // 运行面板的成员名 / 头像色位来自成员表；整体重绘时按当前语言与成员表重建一次，
  // 于是切语言、成员改名都不会留在旧读数上（面板关着时是空操作）。
  renderRunPanel();
  // Agent 详情开着时它的读数（状态点 / 跑动的数字）也要跟着整体重绘刷新：它的每个数字都是
  // 后端事实的镜像，因此重建它和重建消息流是同一件事。
  if (state.activeAgentId) renderAgentDetail();
  renderTyping();
  syncSendButton();
}

function renderIdentity() {
  const member = state.member || OWNER_FALLBACK;
  $('#meName').textContent = member.display_name;
  $('#meSub').textContent = member.kind === 'owner' ? t('identity.human') : member.kind;
  const avatar = $('#meAvatar');
  avatar.className = 'avatar ' + avatarClass(member.avatar_slot);
  avatar.textContent = initial(member.display_name);
}

function replaceChildren(container, nodes) {
  while (container.firstChild) container.removeChild(container.firstChild);
  nodes.forEach((node) => container.appendChild(node));
}

/* ─── 左栏三份名单：就地更新，不整表换节点 ──────────────────────────────

   左栏行（`.nav-row`）带 background/color 过渡，状态点带 `pulse` 动画。整表
   `replaceChildren` 会把行节点连同过渡与动画一起换掉：Agent 每回复一条消息，回复路径上
   都会先后走到「重取列表 → 三份名单重绘」与 `scheduleRead` → `render()`，于是左栏看起来
   闪一下并刷新。因此这三份名单改成**按后端 id 认领已有行**：成员集合不变就沿用原节点，
   只有真的新增 / 删除 / 换序才动 DOM 结构；行内部的可见变化（当前行、名字、未读徽标、
   状态点、无障碍名）就地改那一个子节点，且值没变时不写。 */

/** 行内某个 class 的直接子节点（每一行里同一 class 至多一个）。 */
function railCell(row, className) {
  return Array.from(row.children).find(
    (node) => node.classList && node.classList.contains(className),
  ) || null;
}

/** 就地写一格文本：文本没变就不写——对真实 DOM 来说，写一次 `textContent` 会换掉文本节点。 */
function setRailCellText(row, className, text) {
  const cell = railCell(row, className);
  if (!cell) return;
  const value = String(text);
  if (cell.textContent !== value) cell.textContent = value;
}

/** 就地写「当前行」：`is-active` 与 `aria-current` 是同一条事实的两处呈现，一起对齐。 */
function syncActiveRow(row, active) {
  if (row.classList.contains('is-active') !== active) row.classList.toggle('is-active', active);
  const current = active ? 'true' : 'false';
  if (row.getAttribute('aria-current') !== current) row.setAttribute('aria-current', current);
}

/** 就地写未读徽标：计数 `>0` 时有一个徽标节点，`0` 时没有；数字变了只改文本，不换节点。 */
function syncUnreadBadge(row, count, odId) {
  const total = Number(count) || 0;
  const badge = railCell(row, 'badge');
  if (total <= 0) {
    if (badge) badge.remove();
    return;
  }
  if (!badge) {
    const node = el('span', 'badge', total);
    node.setAttribute('data-od-id', odId);
    row.appendChild(node);
    return;
  }
  if (badge.textContent !== String(total)) badge.textContent = String(total);
}

/** 就地写头像（首字母 + 色位）：改名或换头像后，左栏那一格要跟着后端事实走。 */
function syncAvatarCell(row, name, slot, size) {
  const avatar = railCell(row, 'avatar');
  if (!avatar) return;
  const text = initial(name);
  if (avatar.textContent !== text) avatar.textContent = text;
  const className = avatarClassName(size, slot);
  if (avatar.className !== className) avatar.className = className;
}

/** 就地写状态点：四态只表达后端事实（空闲 / 正在回复 / 出错 / 未启用），不从会话存活推导。

    只改那一个子节点的 class / dataset / 无障碍名，行节点本身不动（`.presence.working`
    的 pulse 动画因此在「已经是在回复」时不会重头开始）。每一行在 `build` 时就已经带上
    状态点，所以这里不需要再造一个。

    切语言的守卫：`title` / `aria-label` 是 `statusLabel()` 在当前 locale 下的取值，
    只看 `dataset.status` 会让「状态没变、语言变了」的切语言路径整格跳过，把无障碍名
    留在上一门语言里——因此把词条文本也当成同一条事实的一部分一起比对。 */
function syncPresenceCell(row, status) {
  const dot = railCell(row, 'presence');
  if (!dot) return;
  const label = statusLabel(status);
  if (dot.dataset.status !== status
    || dot.getAttribute('title') !== label
    || dot.getAttribute('aria-label') !== label) {
    applyPresence(dot, status);
  }
}

/** 三份名单共用的就地更新：同一份成员集合下保留已有行节点。

    `items` 是后端事实的镜像（频道 / 私信 / Agent 列表），行的身份取自后端 id
    （`data-channel` / `data-dm` / `data-member`），因此每一趟都取回同一个节点。
    `build(item)` 只在后端出现了新的 id 时调用（挂上固定的结构：图标位 / 名字格 / 状态点）；
    `paint(row, item)` 每趟都调，负责把行内的可见事实对齐到当前值——它必须只改真的变了的
    子节点（见上面几个 `sync*` 助手）。 */
function syncRailRows(container, items, key, build, paint) {
  if (!container) return;
  const existing = new Map();
  Array.from(container.children).forEach((node) => {
    const id = node.dataset ? node.dataset[key] : undefined;
    if (id !== undefined && !existing.has(id)) existing.set(id, node);
  });
  const rows = items.map((item) => {
    // 复用的行从索引里取走：后端若意外给了重复 id，第二行必须新造，不能把同一个节点挂两次。
    const id = String(item.id);
    const row = existing.get(id) || build(item);
    existing.delete(id);
    paint(row, item);
    return row;
  });
  // 只摘掉后端已经不再返回的行（以及没有 id 的杂物）。
  Array.from(container.children).forEach((node) => {
    if (!rows.includes(node)) node.remove();
  });
  // 位置已经对了就**一个 DOM 操作都不做**——未变的行不 remove 再 insert，它们的过渡与动画
  // 因此不会重头开始。真的换序时才重排，且**只动错位的那一段**：前缀位置正确的行一律不碰，
  // 从第一个错位的行开始按目标顺序重挂（`appendChild` 挂已在容器里的节点就是把它移到末尾）。
  const orderChanged = container.children.length !== rows.length
    || rows.some((row, index) => container.children[index] !== row);
  if (orderChanged) {
    let start = 0;
    while (start < rows.length && container.children[start] === rows[start]) start += 1;
    rows.slice(start).forEach((row) => container.appendChild(row));
  }
}

/** 新建一行频道：只在后端出现了新的频道 id 时调用，之后每一趟都是就地改写这一行。 */
function channelRowNode(channel) {
  const row = el('button', 'nav-row');
  row.dataset.channel = channel.id;
  row.setAttribute('data-od-id', 'channel-' + channel.id);
  row.appendChild(el('span', 'hash', '#'));
  row.appendChild(el('span', 'grow', channel.name));
  return row;
}

/** 频道 / 私信行是否是当前行：Agent 详情开着时整份 rail 只有 Agent 行算当前行
    （`activeAgentId` 是「打开着哪一个成员」的唯一正本），这两段让位；关闭详情后
    它们按 `activeChannelId` 恢复原判据。整份 rail 因此同一时刻至多一行当前行。 */
function targetRowIsCurrent(targetId) {
  return !state.activeAgentId && targetId === state.activeChannelId;
}

/** 就地改写一行频道：当前行、名字、未读徽标。 */
function paintChannelRow(row, channel) {
  syncActiveRow(row, targetRowIsCurrent(channel.id));
  setRailCellText(row, 'grow', channel.name);
  syncUnreadBadge(row, channel.unread_count, 'unread-' + channel.id);
}

function renderChannels() {
  syncRailRows($('#channelList'), state.channels, 'channel', channelRowNode, paintChannelRow);
  $('#channelListEmpty').hidden = state.channels.length > 0;
}

/** 私信行是否属于「已归档」段：判据只有后端事实 `peer.deleted_at`——Agent 软删除后 DM target、
    历史消息与未读都保留，人类面把它们单独成段并只读呈现。这不是新的 target kind。 */
function dmIsArchived(dm) {
  return Boolean(dm && dm.peer && dm.peer.deleted_at);
}

/** 新建一行私信：结构与改前一致（头像 / 名字），**不再有状态点**——私信行不是在线指示器。 */
function dmRowNode(dm) {
  const peer = dm.peer;
  const row = el('button', 'nav-row');
  row.type = 'button';
  row.dataset.dm = dm.id;
  row.setAttribute('data-od-id', 'dm-' + dm.id);
  row.appendChild(avatarNode(peer.display_name, peer.avatar_slot, 'sm'));
  row.appendChild(el('span', 'grow', peer.display_name));
  return row;
}

/** 就地改写一行私信：当前行、头像与名字、未读徽标。 */
function paintDmRow(row, dm) {
  const peer = dm.peer;
  syncActiveRow(row, targetRowIsCurrent(dm.id));
  syncAvatarCell(row, peer.display_name, peer.avatar_slot, 'sm');
  setRailCellText(row, 'grow', peer.display_name);
  syncUnreadBadge(row, dm.unread_count, 'unread-' + dm.id);
}

/** 「已归档」段的呈现裁决（**唯一**落点）：组显隐、列表显隐、标题按钮的展开态。

    `hasArchived` 由调用方从 `state.dms` 用同一个 `dmIsArchived()` 判据数出来——组显隐与
    行归属因此不会各自漂。展开态住 `state.dmArchiveExpanded`（默认 `false`：首次加载、刷新、
    从别的视图回到工作台都折叠，除非用户本次会话点开）。折叠只调 `hidden`，**不清空行**：
    已读 / 未读与行节点在展开 / 收起之间原样保留。属性只在真的变了时才写，于是后端推送
    触发的整体重绘不为同一份事实重写同一批属性。 */
function renderDmArchiveSection(hasArchived) {
  const group = $('#dmArchiveGroup');
  if (group) group.hidden = !hasArchived;
  const expanded = Boolean(state.dmArchiveExpanded);
  const list = $('#dmArchiveList');
  if (list) list.hidden = !expanded;
  const toggle = $('#btnDmArchiveToggle');
  if (toggle) {
    const value = expanded ? 'true' : 'false';
    if (toggle.getAttribute('aria-expanded') !== value) toggle.setAttribute('aria-expanded', value);
  }
}

/** 两段私信各自沿用同一套就地更新契约（节点身份取自 `data-dm`）。
    已归档段只在后端真的给出 `peer.deleted_at` 时出现——空段整块隐藏，不留一条空气分组；
    出现时仍默认折叠（`renderDmArchiveSection()` 裁决）。 */
function renderDms() {
  const active = state.dms.filter((dm) => !dmIsArchived(dm));
  const archived = state.dms.filter(dmIsArchived);
  syncRailRows($('#dmList'), active, 'dm', dmRowNode, paintDmRow);
  syncRailRows($('#dmArchiveList'), archived, 'dm', dmRowNode, paintDmRow);
  $('#dmListEmpty').hidden = active.length > 0;
  renderDmArchiveSection(archived.length > 0);
}

/** 新建一行本机 Agent：左栏每一行都是**打开 Agent 详情**的入口，与频道 / 私信两段同形
    （`<button class="nav-row">`），因此键盘可达、可聚焦，不是「看着能点、按下去什么都不发生」的装饰行。 */
function memberRowNode(member, status) {
  const row = el('button', 'nav-row');
  row.type = 'button';
  row.dataset.member = member.id;
  row.setAttribute('data-od-id', 'agent-row-' + member.id);
  row.setAttribute('aria-label', t('agentDetail.openAria', { name: member.display_name }));
  row.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
  row.appendChild(el('span', 'grow', member.display_name));
  row.appendChild(presenceNode(status));
  return row;
}

/** 就地改写一行 Agent：当前行、头像与名字、无障碍名（切语言后跟着词条走）、状态点。 */
function paintMemberRow(row, member, status) {
  syncActiveRow(row, member.id === state.activeAgentId);
  syncAvatarCell(row, member.display_name, member.avatar_slot, 'sm');
  setRailCellText(row, 'grow', member.display_name);
  const label = t('agentDetail.openAria', { name: member.display_name });
  if (row.getAttribute('aria-label') !== label) row.setAttribute('aria-label', label);
  syncPresenceCell(row, status);
}

function renderMembers() {
  const byId = new Map(state.agents.map((agent) => [agent.id, agent]));
  const agents = state.members.filter((member) => member.kind === 'agent');
  const statusOfMember = (member) => statusOf(byId.get(member.id));
  syncRailRows(
    $('#memberList'),
    agents,
    'member',
    (member) => memberRowNode(member, statusOfMember(member)),
    (row, member) => paintMemberRow(row, member, statusOfMember(member)),
  );
  $('#memberListEmpty').hidden = agents.length > 0;
}

/** 左栏三段的同一次收敛：`activeAgentId` / `activeChannelId` 变了之后，「当前行」必须
    **立刻**在三段里各就各位（整份 rail 至多一行），而不是等下一次整体 `render()`。
    打开 / 关闭 Agent 详情走的是局部重绘路径（`openAgentDetail()` / `leaveAgentView()`），
    只重绘 Agent 段会让频道 / 私信行停在上一帧的当前行上。 */
function renderRailRows() {
  renderChannels();
  renderDms();
  renderMembers();
}

function statusOf(agent) {
  if (agent && typeof agent.status === 'string') return agent.status;
  return state.statuses[agent && agent.id] || 'offline';
}

/** 把四态写到一个状态点节点上（class / title / aria-label / dataset 一起），返回同一个节点。

    它是状态点的**唯一**写法：左栏频道的 presence 格、dock 成员卡与名册都走它，于是「同一个
    事实在多处呈现」不会漂移成两套 class 与两套无障碍文案。Agent 详情头部不再呈现状态点
    （用户 2026-10-24 反馈：详情头部只留名称与「发送消息」）。 */
function applyPresence(node, status) {
  node.className = 'presence ' + status;
  node.dataset.status = status;
  node.setAttribute('title', statusLabel(status));
  node.setAttribute('aria-label', statusLabel(status));
  return node;
}

function presenceNode(status) {
  return applyPresence(el('span', 'presence'), status);
}

/** 该频道的 Agent 成员（已加入）+ 本工作区里未加入的 Agent。

    「已加入」的**唯一来源**是该频道的成员表（`GET /api/channels/{id}/members`），与面板标题
    「频道成员」一致；「未加入」= 全工作区 Agent 名单（`state.members`）减去已加入者。
    两者都是后端事实的镜像，前端不自己推断频道关系。

    未加入的 Agent 呈现在**面板内**的「未加入」一段（人类裁定 2 选 a）：入口就在成员面板里，
    人看到「它不在这个频道」的那一眼就同时看到了「加入」按钮。 */
function dockGroups(channelId) {
  // 已加入的 Agent 与 @ 补全列表同一个来源（同一个函数），不在这里重写一遍过滤规则。
  const joined = channelAgentMembers(channelId);
  const joinedIds = new Set((joined || []).map((member) => member.id));
  const outsiders = state.members.filter(
    (member) => member.kind === 'agent' && !joinedIds.has(member.id),
  );
  return { loaded: joined !== null, joined: joined || [], outsiders };
}

function renderDock() {
  const channel = activeChannel();
  const container = $('#dockMembers');
  if (!container) return;
  if (!channel) {
    // 私信下 dock 不可能可见（`render()` 会收起它），因此这里只剩「没有频道」这一种空态；
    // 不再按 `target.kind === 'dm'` 分叉出一句私信专用文案（那是死代码）。
    replaceChildren(container, [el('div', 'rail-empty', t('dock.openChannelFirst'))]);
    return;
  }
  const groups = dockGroups(channel.id);
  const agentsById = new Map(state.agents.map((agent) => [agent.id, agent]));
  const nodes = [];

  // ── 已加入：本频道的 Agent 成员 ──────────────────────────────────────
  const joinedSection = el('section', 'dock-group');
  joinedSection.dataset.odId = 'dock-joined';
  const joinedLabel = el('div', 'dock-group-label', t('dock.joined', { count: groups.joined.length }));
  joinedSection.appendChild(joinedLabel);
  if (!groups.loaded) {
    // 「读不到 / 还没读到」与「真的没有成员」是两件事：不把前者说成后者（与 `@` 清单的
    // `unavailable` 分档同一纪律）。读失败时也不把已加入的 Agent 列进「未加入」——
    // 那会伪造一个「它不在这个频道」的事实。
    joinedSection.appendChild(
      el(
        'div',
        'rail-empty',
        channelMembersFailed.has(channel.id)
          ? t('dock.membersUnavailable')
          : t('dock.membersLoading'),
      ),
    );
  } else if (!groups.joined.length) {
    joinedSection.appendChild(
      el('div', 'rail-empty', t('dock.noJoinedAgents')),
    );
  } else {
    groups.joined.forEach((member) => joinedSection.appendChild(memberCard(member, agentsById)));
  }
  nodes.push(joinedSection);

  // ── 未加入：本工作区的 Agent，点「加入」进入该频道 ────────────────────
  // 它是**候选池**而不是名单：整段是 `--panel` 井、标题在井内、行是平铺行（见 app.css）。
  // 已加入的卡片仍是透明底 + 描边——默认态不该比候选池更响。
  const outsideSection = el('section', 'dock-group is-pool');
  outsideSection.dataset.odId = 'dock-not-joined';
  outsideSection.appendChild(
    el('div', 'dock-group-label', t('dock.notJoined', { count: groups.outsiders.length })),
  );
  if (!groups.loaded) {
    outsideSection.appendChild(
      el(
        'div',
        'rail-empty',
        channelMembersFailed.has(channel.id)
          ? t('dock.notJoinedUnavailable')
          : t('dock.membersLoading'),
      ),
    );
  } else if (!groups.outsiders.length) {
    outsideSection.appendChild(
      el(
        'div',
        'rail-empty',
        state.members.some((member) => member.kind === 'agent')
          ? t('dock.allJoined')
          : t('dock.noAgents'),
      ),
    );
  } else {
    groups.outsiders.forEach((member) => outsideSection.appendChild(memberCard(member, agentsById, true)));
  }
  nodes.push(outsideSection);
  replaceChildren(container, nodes);
}

/** 成员面板的一行。`canJoin` 为真 = **候选行**（候选池里还没加入本频道的 Agent），此时
    附上「加入」控件，且只留**选人需要的事实**：名字 / 角色 / 状态点 /（若有）不可用原因。

    `model_id` 与状态**文案**只在已加入的卡片上出现：它们是成员在这个频道里的运行时读数，
    对「要不要把它加进来」不提供判据；状态点仍带四态 `title` / `aria-label`，信息不丢。
    行骨架两段共用，因此两段的差异只来自区块化与密度，而不是换了一种行。

    控件语汇沿用 `.btn-sm`，不新造色位——`.btn` 的底是 `--surface`，在 `--panel` 井里
    天然是该行最亮的东西，「加入」因此不需要 `btn-primary` 才是强调元素。 */
function memberCard(member, agentsById, canJoin) {
  const agent = agentsById.get(member.id) || {};
  const card = el('div', 'dock-member' + (canJoin ? ' is-candidate' : ''));
  card.dataset.member = member.id;
  card.setAttribute('data-od-id', (canJoin ? 'dock-candidate-' : 'dock-member-') + member.id);
  const head = el('div', 'dock-member-head');
  head.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
  // 名字本身是**打开 Agent 详情**的控件：卡片里已经有「加入」按钮（未加入那一段），
  // 嵌套按钮在 HTML 里是无效结构，因此入口落在名字上，而不是把整张卡片做成按钮。
  const nameButton = el('button', 'dock-member-name', member.display_name);
  nameButton.type = 'button';
  nameButton.dataset.agentOpen = member.id;
  nameButton.setAttribute('data-od-id', 'dock-open-' + member.id);
  nameButton.setAttribute('aria-label', t('agentDetail.openAria', { name: member.display_name }));
  head.appendChild(nameButton);
  if (agent.role) head.appendChild(el('span', 'msg-tag', agent.role));
  head.appendChild(presenceNode(statusOf(agent)));
  card.appendChild(head);
  if (!canJoin) {
    const meta = el('div', 'dock-member-meta mono', agent.model_id || t('dock.noModel'));
    card.appendChild(meta);
    const status = statusOf(agent);
    const line = el('div', 'dock-member-status', statusLabel(status));
    line.dataset.status = status;
    card.appendChild(line);
  }
  if (agent.enabled_reason) {
    const reason = el('div', 'dock-member-reason', agent.enabled_reason);
    reason.dataset.odId = 'agent-reason-' + member.id;
    card.appendChild(reason);
  }
  if (canJoin) {
    const foot = el('div', 'dock-member-foot');
    const button = el('button', 'btn btn-sm', joining.has(member.id) ? t('dock.joining') : t('dock.join'));
    button.type = 'button';
    button.dataset.join = member.id;
    button.setAttribute('data-od-id', 'dock-join-' + member.id);
    button.setAttribute('aria-label', t('dock.joinAria', { name: member.display_name }));
    button.disabled = joining.has(member.id);
    foot.appendChild(button);
    card.appendChild(foot);
  }
  return card;
}

/* ─── Agent 详情（主线视图）：三个页签 + 就地编辑 ─────────────────────────
   `CONTEXT.md` **Agent** 与 ADR-0011：详情页是**人类面**，可编辑字段全部落在同一个写路径
   `PATCH /api/agents/{id}` 上，后端零改动。

   三条纪律：

   - **它是主线视图，不是 dock 的形态**（用户 2026-10-22 裁定）：`state.mode === 'agent'` 时
     `#viewAgent` 占据整条右侧，dock 让位；关闭时回到进入前的视图与 dock 开合。
   - **正文是三个页签**（`CONTEXT.md` **Agent 详情页签**）：基础信息和配置 → skills → mcp，
     顺序固定、默认停在第一页、不落库也不写 URL；切页签不丢草稿（`state.agentDetailTab`）。
     基础信息和配置页签内仍是统计 → 系统提示 → 配置 → 危险操作（系统提示紧贴统计之下，
     用户 2026-10-24 裁定：它是这页上最常改的一格）。
   - **上下文三项的界面入口已撤除**（D2）：上下文条数 / 压缩阈值 / 迭代上限三格不再渲染，
     草稿不产出、保存载荷也不提交那三个键（`PATCH` 是部分更新，不提交 ⇒ 库里既有值保持）。
     那三列与 `PATCH` 的字段面在后端原样保留。
   - **统计只呈现有真实来源的读数**：四个数字全部来自后端 `GET /api/agents/{id}` 的 `stats`
     （`round_outcome` 聚合 + 任务行 + 频道成员表）。前端**不**自己算，也不为填满界面编数值。
     模型标识 / 提供商 / 是否远端 / 远端数据流告知不再在详情页重复一份只读读数：模型标识与
     提供商可编辑，它们长在「配置」区块里；原型的内存 / 延迟 / 工具权限 / 温度 / Token 消耗 /
     上下文占用在本仓没有数据面，因此这里既不渲染也不留占位。

   编辑的形态：控件只写**草稿**（`state.agentEdit`，呈现状态），不直接发 PATCH；草稿与后端
   读数不一致时页面底部出现固定保存条。「保存」= 一次性提交整份字段（沿用既有语义：任一字段
   不合法时后端整体不写，错误原文就地显示在保存条里，输入不丢）；「放弃」= 丢弃草稿、按后端
   读数重铺。关闭详情 / 切换成员 / 切换主线视图时草稿作废（与既有「关闭弹窗即放弃」同一条，
   不弹二次确认）。 */

/** 草稿的空白形状（呈现状态，不是业务正本）。 */
function emptyAgentEditDraft() {
  return { memberId: null, draft: null, base: null, error: '' };
}

/** 危险区的呈现状态：弹窗为谁打开着、请求是否在飞。关闭 / 切换成员时整体作废。 */
function emptyAgentDeleteState() {
  return { memberId: '', busy: false };
}

/** 收起删除确认弹窗并作废它的呈现状态（零请求）。打开详情 / 关闭详情 / 删除成功
    以及用户自己的出口（取消 / 关闭图标 / 点遮罩 / Escape）都走这一条。 */
function closeAgentDeleteModal() {
  state.agentDelete = emptyAgentDeleteState();
  const overlay = $('#agentDeleteOverlay');
  if (overlay) overlay.hidden = true;
  const text = $('#agentDeleteText');
  if (text) text.textContent = '';
  paintAgentDeleteControls();
}

/** 打开删除确认弹窗（危险区「删除」的唯一动作）：只铺文案与按钮状态，不发请求。
    文案仍是既有的 `agentDetail.deleteConfirm`（带成员名称与不可逆后果）；焦点落在
    「取消」上，回车不会误触删除。 */
function openAgentDeleteModal() {
  const memberId = state.activeAgentId;
  const agent = memberId ? agentById(memberId) : null;
  if (!agent || !state.online) return;
  state.agentDelete = { memberId, busy: false };
  const text = $('#agentDeleteText');
  if (text) text.textContent = t('agentDetail.deleteConfirm', { name: agent.display_name });
  paintAgentDeleteControls();
  $('#agentDeleteOverlay').hidden = false;
  const cancel = $('#agentDeleteCancel');
  if (cancel) cancel.focus();
}

/** 把后端读数铺成一份草稿：`draft` 是控件里的值，`base` 是它派生自的那份读数。
    「有没有未保存的改动」= `draft` 与 `base` 不一致，因此后端读数刷新不会把人的输入冲掉。 */
function agentDraftFrom(agent) {
  const modelId = String((agent && agent.model_id) || '');
  const cut = modelId.indexOf(':');
  return {
    display_name: agent.display_name || '',
    role: agent.role || '',
    // `model_id` 是 `provider:model`：界面把它拆成两个控件（与新建同形），保存时再拼回去。
    provider: cut >= 0 ? modelId.slice(0, cut) : (agent.provider || ''),
    model: cut >= 0 ? modelId.slice(cut + 1) : modelId,
    system_prompt: agent.system_prompt || '',
    enabled: agent.enabled !== false,
    auto_claim: agent.auto_claim === true,
    // 联网档（#202）：`null` = 没有 Agent 级覆盖 = 「跟随频道」。四档之外的值一律落回它，
    // 不猜一个档出来（`submitAgentEdit()` 提交时也只把 `follow` 翻成 `null`，不推断生效档）。
    web_access: WEB_ACCESS_LEVELS.includes(agent.web_access) ? agent.web_access : WEB_ACCESS_FOLLOW,
  };
}

function sameDraft(left, right) {
  if (!left || !right) return left === right;
  const keys = Object.keys(left);
  if (keys.length !== Object.keys(right).length) return false;
  return keys.every((key) => String(left[key]) === String(right[key]));
}

/** 草稿有没有未保存的改动（保存条的唯一判据）。 */
function agentDraftDirty() {
  const edit = state.agentEdit;
  return Boolean(edit.draft && edit.base && !sameDraft(edit.draft, edit.base));
}

/** 草稿按「当前成员的后端读数」重置（放弃 / 关闭详情 / 切换成员都走它）。
    没有打开的成员时落回空白草稿——那是这些调用点唯一用到的分支。 */
function resetAgentEditDraft() {
  const target = state.activeAgentId ? agentById(state.activeAgentId) : null;
  state.agentEdit = target
    ? { memberId: target.id, draft: agentDraftFrom(target), base: agentDraftFrom(target), error: '' }
    : emptyAgentEditDraft();
}

/** 草稿跟随后端读数：人没动过（`draft` 仍等于上一次的 `base`）时跟着新读数走，动过就保留输入。 */
function syncAgentEditDraft(agent) {
  const edit = state.agentEdit;
  const fresh = agentDraftFrom(agent);
  if (edit.memberId !== agent.id) {
    // `draft` 与 `base` 必须是**两个**对象：同一个引用会让「改草稿」顺带改掉基线，
    // 保存条于是永远不出现（人改了半天，界面说他什么都没改）。
    state.agentEdit = { memberId: agent.id, draft: agentDraftFrom(agent), base: fresh, error: '' };
    return;
  }
  if (!sameDraft(edit.draft, edit.base)) return;
  edit.draft = agentDraftFrom(agent);
  edit.base = fresh;
}

/** 写一个草稿字段（控件事件唯一的落点）。它**不重铺** `#adBody`：重铺会把正在输入的控件
    换成新节点，焦点与光标位置一起丢；需要跟着变的那几处由调用方就地重绘。 */
function setAgentDraft(name, value) {
  const edit = state.agentEdit;
  if (!edit.draft) return;
  edit.draft[name] = value;
  edit.error = '';
  renderAgentSaveBar();
}

/** 底部保存条的**唯一**渲染落点：可见性、错误原文与保存键的可用性。 */
function renderAgentSaveBar() {
  const bar = $('#adSaveBar');
  if (!bar) return;
  bar.hidden = !agentDraftDirty();
  const error = $('#adSaveError');
  const message = state.agentEdit.error || '';
  if (error) {
    error.textContent = message;
    error.hidden = !message;
  }
  const save = $('#adSave');
  if (save) save.disabled = !agentDraftDirty() || !state.online;
}

/** 取一个成员在 `state` 里的**最新**读数：`state.agents`（含配置与 stats）优先，
    回退到 `state.members`（只有身份行）。两者都是后端读数的镜像，不在这里合成新事实。 */
function agentById(memberId) {
  return state.agents.find((agent) => agent.id === memberId)
    || state.members.find((member) => member.id === memberId)
    || null;
}

/** 用后端刚返回的那一份覆盖本地镜像（新增与覆盖一条路径，不写两份）。 */
function upsertAgent(agent) {
  if (!agent || !agent.id) return;
  const index = state.agents.findIndex((item) => item.id === agent.id);
  if (index >= 0) state.agents[index] = agent;
  else state.agents.push(agent);
}

/** 打开 Agent 详情（入口：左栏 Agent 行 / 成员面板里的名字 / 失败块的「看成员状态」）。

    它是**主线视图**：进入时记住原来的视图与 dock 开合（`state.agentReturn`），关闭时按同一份
    判据恢复。运行面板若开着先收掉——否则下一次整体重绘会把它盖回来。 */
async function openAgentDetail(memberId) {
  if (!memberId) return;
  // 进入前的落点只在**第一次**进入详情时记录：切换成员时主线已经是 agent 视图，不该覆盖它。
  if (state.mode !== 'agent') {
    state.agentReturn = {
      mode: state.mode,
      dockOpen: !$('#dock').hidden,
    };
  }
  // 运行面板是显式打开的面：它开着时压过一切 dock 形态，不先收掉会被整体重绘盖回来。
  if (state.runPanel.open) closeRunPanel();
  // dock 一次只显示一种形态：打开详情就关掉任务线程（两者都是「占右侧」的呈现）。
  closeTaskStream();
  state.activeTaskId = null;
  state.activeTask = null;
  state.taskMessages = [];
  state.taskOutcomes = {};
  state.taskSeenSeqs = new Set();
  // 换人时草稿整体作废（与「关闭即放弃」同一条）；全局 MCP 条目是**这个成员**的文件、
  // 能力清单（MCP 声明 / 技能）也只属于这个成员，一并按新成员重置。
  state.activeAgentId = memberId;
  state.agentEdit = emptyAgentEditDraft();
  closeAgentDeleteModal();
  state.globalMcpServers = [];
  state.globalMcpError = '';
  resetAgentCapabilities(memberId);
  // 页签回到默认那一页：新成员不继承上一个成员的页签位置（D1）。
  state.agentDetailTab = AGENT_DETAIL_DEFAULT_TAB;
  setViewMode('agent');
  // 详情一打开，频道 / 私信行就得让位：这条路径不经过整体 `render()`，只重绘 Agent 段
  // 会把频道行留在「当前行」上（两行同时高亮）。
  renderRailRows();
  renderAgentDetail();
  await refreshAgentDetail(memberId);
  // 能力清单与配置读数分开读：两条清单失败只落在能力段自己的错误行，不拖垮整页。
  await loadAgentCapabilities(memberId);
}

/** 关闭详情：回到进入前的视图；dock 按进入前的开合恢复（落点判据与关闭线程共用）。 */
function closeAgentDetail() {
  const back = state.agentReturn || { mode: 'channel', dockOpen: false };
  state.activeAgentId = null;
  resetAgentEditDraft();
  closeAgentDeleteModal();
  clearAgentDetail();
  state.mode = back.mode && back.mode !== 'agent' ? back.mode : 'channel';
  setViewMode(state.mode);
  // 形态落到共用判据上：打开详情时任务线程与运行面板都被主动关掉了（「dock 一次只显示
  // 一种形态」），因此这里能恢复的形态只有「频道成员」；开合才是进入前要记住的那一位。
  // `render()` 已经收起了没有形态的 dock（私信），这里不得再把它重新打开——否则会留下
  // 一个开关已隐藏、只能按 ESC 关掉的空面板。
  setDockMode(dockFallbackMode() || 'agent');
  renderMembers();
  render();
  setDockOpen(Boolean(back.dockOpen) && dockAvailable());
}

/** 切主线视图时离开详情（导航动作的公共出口）：丢弃草稿、清掉 `activeAgentId`。
    与关闭详情同一条语义（不弹二次确认），但**不**恢复 dock 的开合——导航动作自己决定。
    离开后左栏三段的当前行立刻按 `activeChannelId` 重新落位：不经过整体 `render()` 的
    出口（例如打开任务线程）否则会把 Agent 行留在当前行上。 */
function leaveAgentView() {
  if (!state.activeAgentId) return;
  state.activeAgentId = null;
  resetAgentEditDraft();
  closeAgentDeleteModal();
  clearAgentDetail();
  renderRailRows();
}

/** 「发送消息」请求是否在途：唯一呈现状态，只控制头部按钮的禁用（不写业务正本）。 */
let agentDmInFlight = false;

/** 头部「发送消息」的可用性（打开 / 关闭详情、请求开始 / 结束、离线与重连都走这一条）。
    离线纪律与页面其它写入口一致：不发得出去的写，就不摆一个能按的入口。 */
function syncAgentSendButton() {
  const button = $('#btnAgentSendMessage');
  if (button) button.disabled = agentDmInFlight || !state.online;
}

/** Agent 详情头部的「发送消息」：DM 创建能力的**唯一**人类入口（私信分组原来的 `+` 与弹窗已删除）。

    选中一个成员就同时表达了「和谁私信」，因此这里直接走幂等的 `POST /api/dms`：
    已有 DM 复用、没有则新建（后端以 `created` 区分）。两条路径落到同一条「离开详情 →
    进入该 DM → 聚焦 composer」；失败用词条 toast 说明，不静默。 */
async function messageActiveAgent() {
  const memberId = state.activeAgentId;
  if (!memberId || agentDmInFlight) return;
  agentDmInFlight = true;
  syncAgentSendButton();
  try {
    const payload = await api('/api/dms', { method: 'POST', body: { agent_id: memberId } });
    replaceDm(payload.dm);
    // `selectTarget` 是进入一个 target 的既有唯一路径：它自己会离开 Agent 详情、切回消息视图，
    // 并按新 target 重读消息 / 轮次结果 / 审批，最后落行与滚动。
    await selectTarget(payload.dm.id);
    $('#composerInput').focus();
    // `selectTarget()` 自己吞掉**进入**时的读失败（toast + `state.online = false`）后正常返回：
    // 那种情况已经有一条错误 toast，不再叠一条「已打开」的成功说法。
    if (!state.online) return;
    toast(payload.created
      ? t('toast.dmCreated', { name: payload.dm.peer.display_name })
      : t('toast.dmOpened', { name: payload.dm.peer.display_name }));
  } catch (err) {
    toast(t('toast.dmOpenFailed', { reason: err.message }), 'err');
  } finally {
    agentDmInFlight = false;
    syncAgentSendButton();
  }
}

/** 清掉详情页的内容与保存条（关闭 / 切换视图时）：隐藏的节点也不留一帧过期读数。
    面板容器本身是 `index.html` 的**静态骨架**（`aria-controls` 要有落点），因此只清空每个面板的子节点。 */
function clearAgentDetail() {
  AGENT_DETAIL_TABS.forEach((name) => renderAgentTabPanel(name, []));
  agentNoticeNodes = null;
  const name = $('#adName');
  if (name) name.textContent = '';
  const bar = $('#adSaveBar');
  if (bar) bar.hidden = true;
  const error = $('#adSaveError');
  if (error) {
    error.textContent = '';
    error.hidden = true;
  }
}

/** 从后端读**这一个**成员的最新读数（配置 + 状态点 + 概览数字）。
    工作台快照里的那份可能已经旧了（消息处理数与完成任务数会变），因此打开时读一次正本。 */
async function refreshAgentDetail(memberId) {
  try {
    const payload = await api('/api/agents/' + encodeURIComponent(memberId));
    if (state.activeAgentId !== memberId) return; // 已经切到别人：不把旧读数铺到新面板上
    upsertAgent(payload.agent);
    renderMembers();
    renderDock();
    renderAgentDetail();
    await loadGlobalMcpServers(memberId);
  } catch (err) {
    toast(t('toast.agentDetailFailed', { reason: err.message }), 'err');
  }
}

/** 读**这一个**成员的全局 `mcp.json` 条目（只读列表 + 运行期读数）。
    列表失败只影响这一小节：详情其余读数照常可用；未知响应（测试替身默认 `{}`）按空列表处理。 */
async function loadGlobalMcpServers(memberId) {
  try {
    const payload = await api('/api/agents/' + encodeURIComponent(memberId) + '/global-mcp-servers');
    if (state.activeAgentId !== memberId) return; // 已经切到别人：不把旧读数铺到新面板上
    state.globalMcpServers = Array.isArray(payload && payload.global_mcp_servers)
      ? payload.global_mcp_servers
      : [];
    state.globalMcpError = '';
  } catch (err) {
    if (state.activeAgentId !== memberId) return;
    state.globalMcpServers = [];
    state.globalMcpError = err.message;
  }
  if (state.activeAgentId === memberId) renderAgentDetail();
}

/** 按「成员 + 名字」确认一条全局远端声明；确认后按后端读数重铺列表。 */
async function confirmGlobalMcpServer(name) {
  const memberId = state.activeAgentId;
  if (!memberId) return;
  try {
    await api(
      '/api/agents/' + encodeURIComponent(memberId) + '/global-mcp-servers/'
        + encodeURIComponent(name) + '/consent',
      { method: 'POST' },
    );
    await loadGlobalMcpServers(memberId);
    toast(t('agentDetail.globalMcpConfirmed', { name }));
  } catch (err) {
    toast(t('toast.globalMcpConsentFailed', { reason: err.message }), 'err');
  }
}

/** 详情页的**唯一**渲染落点（打开、状态流推送、配置保存后都走它）。
    草稿脏时**不重铺** `#adBody`：那会把正在输入的控件换成新节点（焦点与光标一起丢），
    而人的输入比一次后台刷新更值得保留；标题与保存条仍照常刷新。 */
function renderAgentDetail() {
  const agent = state.activeAgentId ? agentById(state.activeAgentId) : null;
  if (!agent) return;
  syncAgentEditDraft(agent);
  $('#adName').textContent = agent.display_name;
  syncAgentSendButton();
  if (agentDraftDirty() || capabilityDraftDirty()) {
    // 草稿脏时不重铺整页，但**只读**与**只吃后端读数**的那几块仍要跟着后端读数走：
    // 全局 MCP 的确认动作正是「点完要立刻变成已确认」，而它不过草稿。
    refreshGlobalMcpSection();
    // 能力段里有未提交输入时不碰它（后台重绘不该换掉持焦控件）；显式动作自己会重铺。
    if (!capabilityDraftDirty()) refreshAgentCapabilitiesPanels();
  } else {
    renderAgentTabPanels(agent);
  }
  paintAgentDangerControls();
  paintAgentDeleteControls();
  renderAgentSaveBar();
}

/** 就地换掉「全局 MCP」小节（草稿脏时 `renderAgentDetail()` 不重铺面板，但这一节是只读的，
    它的读数变了就得跟着变）。找不到旧节点（还没铺过）时什么都不做。

    走 `replaceChildren` 换整份子节点列表、而不是 `Node.replaceWith()`：`el()` 建出来的节点
    与测试替身都只保证前者，少一条浏览器专有 API 就少一条替身要追的差异。 */
function refreshGlobalMcpSection() {
  replaceSectionNode('agent-detail-section-global-mcp', globalMcpSection());
}

/** 把某个区块整段换成新节点（标识挂在区块自己的第一个子标题上）。找不到旧节点时什么都不做。
    走 `replaceChildren` 换整份子节点列表、而不是 `Node.replaceWith()`：`el()` 建出来的节点
    与测试替身都只保证前者。 */
function replaceSectionNode(odId, replacement) {
  const panel = agentTabPanel('mcp');
  if (!panel || !panel.children) return;
  const hasOdId = (node) => node.getAttribute && node.getAttribute('data-od-id') === odId;
  const nodes = Array.prototype.slice.call(panel.children);
  const index = nodes.findIndex((node) => hasOdId(node)
    || (node.querySelectorAll && Array.prototype.some.call(node.querySelectorAll('[data-od-id]'), hasOdId)));
  if (index < 0) return;
  nodes[index] = replacement;
  replaceChildren(panel, nodes);
}

/** 详情页的分区标题：文案走词条，`data-od-id` 是 `alibot-design-system/SKILL.md` 对
    「regions / headings」的要求（这类标题是运行时生成的，因此标识也在这里给）。 */
function sectionTitle(key, odId) {
  const node = el('div', 'd-sec-title', t(key));
  if (odId) node.setAttribute('data-od-id', odId);
  return node;
}

/** 页签的 DOM id（与 `index.html` 的静态骨架同一份拼法：按钮 `#adTabBase`、面板 `#adPanelBase`）。 */
function agentTabId(prefix, name) {
  return prefix + name.charAt(0).toUpperCase() + name.slice(1);
}

/** 切页签：只改呈现状态与两个节点的属性，**不重铺**面板内容。

    它不碰 `state.agentEdit.draft`——切页签不丢未提交的草稿是 D1 的硬约束，而面板重铺会把
    正在输入的控件换成新节点（焦点与光标位置一起丢）。因此这里只对齐 `aria-selected`、`.is-active`
    与面板的 `hidden`。 */
function setAgentDetailTab(name) {
  const tab = AGENT_DETAIL_TABS.indexOf(name) >= 0 ? name : AGENT_DETAIL_DEFAULT_TAB;
  state.agentDetailTab = tab;
  AGENT_DETAIL_TABS.forEach((item) => {
    const on = item === tab;
    const button = $('#' + agentTabId('adTab', item));
    if (button) {
      button.setAttribute('aria-selected', on ? 'true' : 'false');
      button.classList.toggle('is-active', on);
    }
    const panel = $('#' + agentTabId('adPanel', item));
    if (panel) panel.hidden = !on;
  });
}

/** 一个页签的面板节点（`#adPanelBase` 等）。它是 `index.html` 的静态骨架（`aria-controls` 要有落点）。 */
function agentTabPanel(name) {
  return $('#' + agentTabId('adPanel', name));
}

/** 往一个页签里铺它自己的区块（只换面板的子节点，不碰其它页签与保存条）。 */
function renderAgentTabPanel(name, nodes) {
  const panel = agentTabPanel(name);
  if (panel) replaceChildren(panel, nodes);
}

/** **基础信息和配置**页签的区块，按顺序：统计 → 系统提示 → 配置 → 危险操作
    （危险操作仍是最后一块——误触面离配置字段最远）。 */
function agentBaseTabNodes(agent) {
  return [
    agentFactsSection(agent),
    agentPromptSection(agent),
    agentConfigSection(agent),
    agentDangerSection(agent),
  ];
}

/** **skills** 页签：左栏技能列表（含启停 / 删除）+ 右栏选中技能的详情位置。

    右栏本轮只给占位说明：选中态、元数据与 SKILL.md 正文由后续票补（D5）；
    「从本机导入」的下拉框同样由后续票补，因此安装面仍保留既有两条入口（本票只搬不删）。 */
function agentSkillsTabNodes(agent) {
  const view = capabilitiesView((agent && agent.id) || state.activeAgentId || '');
  const section = el('section', 'd-sec');
  section.appendChild(sectionTitle('agentCapabilities.skillTitle', 'agent-capabilities-skill'));
  section.appendChild(
    resourceNote(
      'agentCapabilities.scopeNote',
      'agent-capabilities-scope',
      'is-faint',
      { member: view.memberId },
    ),
  );

  const columns = el('div', 'agent-skill-columns');
  const left = el('div', 'agent-skill-left');
  if (view.skillError) {
    left.appendChild(resourceErrorNode(view.skillError, 'skill-error'));
  }
  if (view.skillLoading) {
    left.appendChild(el('div', 'resource-empty', t('skill.loading')));
  } else if (!view.skills.length) {
    left.appendChild(el('div', 'resource-empty', t('skill.empty')));
  } else {
    const list = el('div');
    list.setAttribute('data-od-id', 'skill-list');
    view.skills.forEach((skill) => list.appendChild(skillRow(skill)));
    left.appendChild(list);
  }
  columns.appendChild(left);

  const right = el('div', 'agent-skill-right');
  const detail = el('div', 'resource-empty', t('agentDetail.skillsDetailPlaceholder'));
  detail.setAttribute('data-od-id', 'agent-skill-detail');
  right.appendChild(detail);
  columns.appendChild(right);
  section.appendChild(columns);

  // 安装面：既有两条入口（本机目录 / 上传 zip）与导入区块本票**原样搬过来**，
  // 「从本机导入」的下拉框由后续票在原位替换它们（D3 / D4）。
  section.appendChild(skillInstallForm(view));
  section.appendChild(skillImportForm(view));
  return [section];
}

/** **mcp** 页签：上块是库行表单（**装配正本**），下块是家目录 `mcp.json` 的位置。

    下块本轮只给占位说明：文本域与它的两条端点由后续票补（D7）。原「全局 MCP」只读小节
    并在这里（同一个 `data-od-id`），不再单独成段。 */
function agentMcpTabNodes(agent) {
  const memberId = (agent && agent.id) || state.activeAgentId || '';
  const view = capabilitiesView(memberId);
  const section = el('section', 'd-sec');
  section.appendChild(sectionTitle('agentCapabilities.mcpTitle', 'agent-capabilities-mcp'));
  section.appendChild(resourceNote('mcp.scopeNote', 'mcp-scope-note', 'is-faint'));
  if (view.mcpError) {
    section.appendChild(resourceErrorNode(view.mcpError, 'mcp-error'));
  }
  if (view.mcpLoading) {
    section.appendChild(el('div', 'resource-empty', t('mcp.loading')));
  } else if (!view.mcp.length) {
    section.appendChild(el('div', 'resource-empty', t('mcp.empty')));
  } else {
    const list = el('div');
    list.setAttribute('data-od-id', 'mcp-list');
    view.mcp.forEach((server) => list.appendChild(mcpRow(server, view)));
    section.appendChild(list);
  }
  section.appendChild(mcpForm(view));

  // 原「全局 MCP」只读小节：与库行不是同一份数据（那是家目录文件里的条目）。
  section.appendChild(globalMcpSection());

  // 下块的位置：文本域由后续票补，本轮给占位说明。
  const fileBox = el('section', 'd-sec');
  fileBox.appendChild(sectionTitle('agentDetail.mcpFileTitle', 'agent-detail-section-mcp-file'));
  fileBox.appendChild(
    resourceNote('agentDetail.mcpFilePlaceholder', 'agent-detail-mcp-file-hint', 'is-faint'),
  );
  section.appendChild(fileBox);
  return [section];
}

/** 把三个页签的内容按当前成员重铺（切成员 / 读路径 / 显式动作都走它）。 */
function renderAgentTabPanels(agent) {
  renderAgentTabPanel('base', agentBaseTabNodes(agent));
  renderAgentTabPanel('skills', agentSkillsTabNodes(agent));
  renderAgentTabPanel('mcp', agentMcpTabNodes(agent));

  setAgentDetailTab(state.agentDetailTab);
}

/** 统计：四个数字（同一个 `stats` 来源）。 */
function agentFactsSection(agent) {
  const stats = agent.stats || {};
  const facts = el('section', 'd-sec');
  facts.appendChild(sectionTitle('agentDetail.sectionFacts', 'agent-detail-section-facts'));
  const grid = el('div', 'stat-grid');
  [
    ['agentDetail.statChannels', stats.channel_count, 'agentDetail.statChannelsHint', 'agent-stat-channels'],
    ['agentDetail.statRounds', stats.round_count, 'agentDetail.statRoundsHint', 'agent-stat-rounds'],
    ['agentDetail.statTasksDone', stats.completed_task_count, 'agentDetail.statTasksDoneHint',
      'agent-stat-completed-tasks'],
  ].forEach(([labelKey, value, hintKey, odId]) => {
    grid.appendChild(statCell(t(labelKey), t(hintKey), String(Number(value) || 0), odId));
  });
  // 成功率：没有样本时**不编** 0%——「一条都没有」与「全部失败」是两件事。
  const rate = stats.success_rate;
  const rateText = rate === null || rate === undefined
    ? t('agentDetail.noSamples')
    : Math.round(Number(rate) * 100) + '%';
  grid.appendChild(statCell(t('agentDetail.statSuccess'), t('agentDetail.statSuccessHint'), rateText,
    'agent-stat-success-rate'));
  facts.appendChild(grid);
  return facts;
}

/* ─── 配置区块：可编辑字段直接长在这里（全部写 `state.agentEdit.draft`） ──── */

/** 远端数据流告知块的节点引用：它由 `agentConfigSection()` 每次重建时登记，
    切 provider 时就地重绘（不按 id 查询——节点在 `#adBody` 里，不在文档顶层）。 */
let agentNoticeNodes = null;

/** 一个带标签的字段：`control` 是控件本身，`hint` 是它下面的说明（可选）。

    标签必须真的指向控件：单控件用 `<label for>`；档位组（`role="group"` 的按钮组）没有
    「一个」可聚焦控件，改用 `aria-labelledby` 指向同一个 `<label>` 节点。 */
function agentFieldRow(labelKey, control, hintKey) {
  const field = el('div', 'field');
  const label = el('label', null, t(labelKey));
  label.id = 'agentLabel-' + labelKey.replace(/\./g, '-');
  if (control.id) label.setAttribute('for', control.id);
  else if (control.getAttribute('role') === 'group') control.setAttribute('aria-labelledby', label.id);
  field.appendChild(label);
  field.appendChild(control);
  if (hintKey) field.appendChild(el('div', 'hint', t(hintKey)));
  return field;
}

/** 一个文本 / 数字输入控件：`data-agent-field` 的取值与 PATCH 字段同名（委派据此认字段）。 */
function agentInput(name, value, options) {
  const config = options || {};
  const input = el('input', 'input');
  input.type = config.type || 'text';
  input.autocomplete = 'off';
  // `id` 是 `<label for>` 的落点：没有它，点标签不会把焦点送进控件，读屏也念不出字段名。
  input.id = 'agentField-' + name;
  input.dataset.agentField = name;
  input.value = value === undefined || value === null ? '' : String(value);
  input.setAttribute('data-od-id', 'agent-field-' + name);
  if (config.min !== undefined) input.setAttribute('min', String(config.min));
  if (config.max !== undefined) input.setAttribute('max', String(config.max));
  if (config.step !== undefined) input.setAttribute('step', String(config.step));
  if (config.placeholder) input.setAttribute('placeholder', config.placeholder);
  return input;
}

/** 一组档位卡片（`.radio-card` 控件族）：卡片自己带状态，应用里不另存一个布尔。
    `datasetKey` 是组内卡片的取值键（`tierValue` / `agentAccess`）。 */
function agentTierGroup(odId, datasetKey, options, selected) {
  const group = el('div', 'radio-row');
  group.setAttribute('role', 'group');
  group.setAttribute('data-od-id', odId);
  options.forEach(([value, labelKey, hintKey]) => {
    const card = el('button', 'radio-card');
    card.type = 'button';
    card.dataset[datasetKey] = value;
    const on = value === selected;
    card.classList.toggle('is-on', on);
    card.setAttribute('aria-pressed', on ? 'true' : 'false');
    card.appendChild(el('b', null, t(labelKey)));
    card.appendChild(el('span', null, t(hintKey)));
    group.appendChild(card);
  });
  return group;
}

/** 模型提供商下拉：候选来自 `state.providers`（`fillProviderSelect()`）；草稿里的 provider
    若不在清单里（例如远端快照还没到）仍要出现在选项里——否则保存会静默改掉模型标识。 */
function fillAgentProviderSelect(select, provider) {
  fillProviderSelect(select, false);
  const values = Array.prototype.map.call(select.children, (option) => option.value);
  if (provider && values.indexOf(provider) === -1) {
    const option = el('option', null, providerLabel(provider));
    option.value = provider;
    select.appendChild(option);
  }
  if (provider) select.value = provider;
  return select.value;
}

/** 配置区块：显示名 / 角色 / 模型 / 自动认领 / 上下文三项 / 联网档，
    外加远端数据流告知块。「暂停 / 恢复」与删除已搬到页面最后一个区块（危险操作）；
    `enabled` 的**唯一**界面入口就是危险区那个按钮，配置区不再有「启用状态」档位。 */
function agentConfigSection(agent) {
  const draft = state.agentEdit.draft || agentDraftFrom(agent);
  const section = el('section', 'd-sec');
  section.appendChild(sectionTitle('agentDetail.sectionConfig', 'agent-detail-section-config'));

  section.appendChild(agentFieldRow('agent.nameLabel', agentInput('display_name', draft.display_name)));
  section.appendChild(agentFieldRow('agent.roleLabel', agentInput('role', draft.role)));

  const provider = el('select', 'input');
  provider.id = 'agentField-provider';
  provider.dataset.agentField = 'provider';
  provider.setAttribute('data-od-id', 'agent-field-provider');
  fillAgentProviderSelect(provider, draft.provider);
  section.appendChild(agentFieldRow('agent.providerLabel', provider));
  const providerHint = el('div', 'hint', providerHintText(provider.value));
  providerHint.setAttribute('data-agent-provider-hint', '');
  section.appendChild(providerHint);

  const modelField = el('div', 'field');
  const modelLabel = el('label', null, t('agent.modelLabel'));
  modelLabel.setAttribute('for', 'agentField-model');
  modelField.appendChild(modelLabel);
  const model = agentInput('model', draft.model);
  modelField.appendChild(model);
  const modelHint = el('div', 'hint');
  modelHint.appendChild(document.createTextNode(t('agent.modelHintPrefix')));
  modelHint.appendChild(el('span', 'mono', 'provider:model'));
  modelHint.appendChild(document.createTextNode(t('agent.modelHintSuffix')));
  modelField.appendChild(modelHint);
  section.appendChild(modelField);

  const claimGroup = agentTierGroup(
    'agent-config-auto-claim',
    'tierValue',
    [
      ['on', 'agent.autoClaimOn', 'agent.autoClaimOnHint'],
      ['off', 'agent.autoClaimOff', 'agent.autoClaimOffHint'],
    ],
    draft.auto_claim ? 'on' : 'off',
  );
  claimGroup.dataset.agentTier = 'auto_claim';
  section.appendChild(agentFieldRow('agent.autoClaimLabel', claimGroup, 'agent.autoClaimNote'));

  // 联网档（#202 / 2026-10-21 修订）：Agent 级入口。「跟随频道（默认）」= 不写 Agent 级覆盖，
  // 选中它保存时提交 `web_access: null`（库内列回到 NULL）；其余三档与频道设置里的同名同义。
  const accessGroup = agentTierGroup(
    'agent-config-web-access',
    'agentAccess',
    [
      [WEB_ACCESS_FOLLOW, 'agent.webAccessFollow', 'agent.webAccessFollowHint'],
      ['off', 'agent.webAccessOff', 'agent.webAccessOffHint'],
      ['search', 'agent.webAccessSearch', 'agent.webAccessSearchHint'],
      ['search+fetch', 'agent.webAccessSearchFetch', 'agent.webAccessSearchFetchHint'],
    ],
    draft.web_access,
  );
  accessGroup.dataset.agentAccessGroup = '';
  section.appendChild(agentFieldRow('agent.webAccessLabel', accessGroup, 'agent.webAccessNote'));

  // 远端数据流告知块：判定与文案只有一份实现（`renderRemoteNoticeBlock()`）。节点在 `#adBody`
  // 里，因此这里登记直接引用，切 provider 时就地重绘，不按 id 去文档顶层查。
  section.appendChild(agentRemoteNoticeNode(draft.provider));
  return section;
}

/** 危险操作（页面最后一个区块）：暂停 / 恢复与删除。

    - 暂停 / 恢复仍只写草稿里的 `enabled`（语义不变；`paintAgentDraftControls()` 就地更新说法）；
    - 删除**只打开确认弹窗**（零请求）；弹窗里的「确认删除」才发 `DELETE`（见 `openAgentDeleteModal()`）；
    - `state.online === false` 时暂停与删除都禁用；请求进行中禁用按钮。
*/
function agentDangerSection(agent) {
  const draft = state.agentEdit.draft || agentDraftFrom(agent);
  const busy = state.agentDelete.memberId === agent.id && state.agentDelete.busy;
  const section = el('section', 'd-sec danger-zone');
  section.appendChild(sectionTitle('agentDetail.sectionDanger', 'agent-detail-section-danger'));
  section.appendChild(el('div', 'danger-hint', t('agentDetail.dangerHint')));

  const actions = el('div', 'agent-actions danger-actions');
  const toggle = el('button', 'btn btn-sm', t(draft.enabled ? 'agentDetail.pause' : 'agentDetail.resume'));
  toggle.type = 'button';
  toggle.dataset.agentToggle = agent.id;
  toggle.setAttribute('data-od-id', 'agent-toggle-enabled');
  toggle.disabled = !state.online || busy;
  actions.appendChild(toggle);

  const remove = el('button', 'btn btn-sm btn-danger', t('agentDetail.delete'));
  remove.type = 'button';
  remove.dataset.agentDelete = agent.id;
  remove.setAttribute('data-od-id', 'agent-delete');
  remove.disabled = !state.online || busy;
  actions.appendChild(remove);
  section.appendChild(actions);
  return section;
}

/** 远端数据流告知块的骨架（详情页这一份）。节点引用登记在 `agentNoticeNodes`。 */
function agentRemoteNoticeNode(provider) {
  const box = el('div', 'remote-notice');
  box.setAttribute('data-od-id', 'agent-edit-remote-notice');
  const head = el('div', 'remote-notice-head');
  head.appendChild(el('span', 'dot'));
  const title = el('b', null, t('agent.remoteTitleStatic'));
  head.appendChild(title);
  box.appendChild(head);
  const body = el('p', 'remote-notice-body');
  box.appendChild(body);
  const foot = el('div', 'remote-notice-foot');
  const hint = el('span', 'hint');
  foot.appendChild(hint);
  const confirm = el('button', 'btn btn-sm btn-primary', t('agent.remoteConfirm'));
  confirm.type = 'button';
  confirm.dataset.agentRemoteConfirm = '';
  confirm.setAttribute('data-od-id', 'edit-remote-ack');
  foot.appendChild(confirm);
  box.appendChild(foot);
  agentNoticeNodes = { box, title, body, hint, confirm };
  renderAgentEditNotice(provider);
  return box;
}

/** 按当前草稿的 provider 重绘告知块（打开详情、切 provider、确认后都走它）。 */
function renderAgentEditNotice(provider) {
  if (!agentNoticeNodes) return;
  renderRemoteNoticeBlock(agentNoticeNodes, provider);
}

/** 切 provider 时就地刷新它下面的提示行（本机 / 远端两种说法，与新建弹窗同一份词条）。 */
function syncAgentProviderHint(provider) {
  const body = $('#adBody');
  if (!body) return;
  const hint = body.querySelector('[data-agent-provider-hint]');
  if (hint) hint.textContent = providerHintText(provider);
}

/** 详情页里的告知确认：与新建走**同一个**端点与同一套镜像更新（不写第二套）。 */
async function confirmAgentEditRemoteFlow() {
  const draft = state.agentEdit.draft;
  if (!draft || !agentNoticeNodes) return;
  await confirmRemoteFlow(draft.provider, agentNoticeNodes.confirm, () => renderAgentEditNotice(draft.provider));
}

/** 系统提示：可编辑的原文（不是被改写的一句话）。 */
function agentPromptSection(agent) {
  const draft = state.agentEdit.draft || agentDraftFrom(agent);
  const section = el('section', 'd-sec');
  section.appendChild(sectionTitle('agentDetail.kvPrompt', 'agent-detail-section-prompt'));
  const textarea = el('textarea', 'textarea');
  textarea.id = 'agentFieldPrompt';
  textarea.dataset.agentField = 'system_prompt';
  textarea.value = draft.system_prompt;
  textarea.setAttribute('placeholder', t('agent.promptPlaceholder'));
  textarea.setAttribute('aria-label', t('agentDetail.kvPrompt'));
  textarea.setAttribute('data-od-id', 'agent-detail-prompt');
  section.appendChild(textarea);
  return section;
}

/** 全局 MCP 小节（#236）：**只读**展示成员家目录 `mcp.json` 里的真实条目。
    全局层由人维护，因此这里没有编辑 / 启停 / 删除；只对远端未确认条目给一个确认动作。 */
function globalMcpSection() {
  const section = el('section', 'd-sec');
  section.appendChild(sectionTitle('agentDetail.globalMcpTitle', 'agent-detail-section-global-mcp'));
  if (state.globalMcpError) {
    section.appendChild(el('div', 'hint', state.globalMcpError));
  } else if (!state.globalMcpServers.length) {
    section.appendChild(el('div', 'hint', t('agentDetail.globalMcpEmpty')));
  } else {
    state.globalMcpServers.forEach((server) => section.appendChild(globalMcpRowNode(server)));
  }
  return section;
}

/** 一行全局条目：名字 + transport / 是否远端 / 是否已确认 / 运行期读数（+ 远端确认按钮）。 */
function globalMcpRowNode(server) {
  const row = el('div', 'kv global-mcp-row');
  row.dataset.globalMcpServer = server.name;
  row.setAttribute('data-od-id', 'global-mcp-' + server.name);
  row.appendChild(el('div', 'k', server.name));
  const value = el('div', 'v');
  const facts = [
    server.transport,
    server.is_remote ? t('agentDetail.globalMcpRemote') : t('agentDetail.globalMcpLocal'),
    server.is_remote
      ? (server.consented
        ? t('agentDetail.globalMcpConsented')
        : t('agentDetail.globalMcpConsentPending'))
      : t('agentDetail.globalMcpNoConsent'),
    globalMcpRuntimeText(server.runtime),
  ];
  value.appendChild(document.createTextNode(facts.join(' · ')));
  if (server.is_remote && !server.consented) {
    const button = el('button', 'btn btn-sm', t('agentDetail.globalMcpConfirm'));
    button.type = 'button';
    button.dataset.globalMcpConsent = server.name;
    button.setAttribute('data-od-id', 'global-mcp-consent');
    value.appendChild(button);
  }
  row.appendChild(value);
  return row;
}

/** 运行期读数的说法：`null` = 还没装配过；连接失败时直接呈现后端那句可读原因。 */
function globalMcpRuntimeText(runtime) {
  if (!runtime) return t('agentDetail.globalMcpRuntimeIdle');
  if (runtime.connected) return t('agentDetail.globalMcpRuntimeConnected');
  return runtime.error || t('agentDetail.globalMcpRuntimeIdle');
}

/** 一个概览数字：`title` 与 `aria-label` 都写明**来源**（数字本身不带来源就是不可核对的读数）。 */
function statCell(label, hint, value, odId) {
  const cell = el('div', 'stat');
  cell.title = hint;
  cell.setAttribute('aria-label', label + t('common.colonSep') + value + t('common.clauseSep') + hint);
  const number = el('div', 'n', value);
  number.setAttribute('data-od-id', odId);
  cell.appendChild(number);
  cell.appendChild(el('div', 'l', label));
  return cell;
}

/** 一行「键 : 值」。值用 `<span>` 装：`.kv` 的排版靠它。 */
function kvRow(key, value, odId) {
  const row = el('div', 'kv');
  row.appendChild(el('span', 'k', key));
  const cell = el('span', 'v', value);
  if (odId) cell.setAttribute('data-od-id', odId);
  row.appendChild(cell);
  return row;
}

/** 把一次写路径的响应铺回界面：成员行、左栏、成员面板与详情页读的是同一份后端读数。 */
function applyAgentUpdate(payload) {
  if (payload.agents) state.agents = payload.agents;
  if (payload.members) state.members = payload.members;
  upsertAgent(payload.agent);
  renderMembers();
  renderDock();
  renderAgentDetail();
}

/** 保存：**整份字段一起提交**（部分更新的语义在后端，前端不做「哪些改过」的差量）——
    任一字段不合法时后端整体不写，错误原文照后端给的那句话显示在保存条里，输入不丢。 */
async function submitAgentEdit() {
  const edit = state.agentEdit;
  const memberId = edit.memberId || state.activeAgentId;
  if (!memberId || !edit.draft) return;
  const draft = edit.draft;
  // 载荷只含界面真的有的字段（D2）：上下文条数 / 压缩阈值 / 迭代上限三格已从界面撤除，
  // 因此**不提交**那三个键。`PATCH /api/agents/{id}` 是部分更新，不出现的字段不写入——
  // 这是「撤界面不破坏库里既有值」的机械保证；反过来提交 `null` 会一次清空那三列。
  const payload = {
    display_name: String(draft.display_name || '').trim(),
    role: String(draft.role || '').trim(),
    model_id: draft.provider + ':' + String(draft.model || '').trim(),
    system_prompt: String(draft.system_prompt || '').trim(),
    enabled: Boolean(draft.enabled),
    auto_claim: Boolean(draft.auto_claim),
    // 联网档（#202）：`follow` 是界面上的「跟随频道」档，提交 **`web_access: null`**——后端把它
    // 解释成「清除 Agent 级覆盖」（写库 NULL），所以从具体档位切回跟随频道真的会清掉覆盖，
    // 而不是只改界面（ADR-0026 §十五）。
    web_access: draft.web_access === WEB_ACCESS_FOLLOW ? null : draft.web_access,
  };
  const save = $('#adSave');
  if (save) save.disabled = true;
  try {
    const result = await api('/api/agents/' + encodeURIComponent(memberId), {
      method: 'PATCH',
      body: payload,
    });
    // 保存成功：草稿按响应重铺（保存条随之消失），左栏 / 成员面板 / 详情一起刷新。
    state.agentEdit = { memberId, draft: agentDraftFrom(result.agent), base: agentDraftFrom(result.agent), error: '' };
    applyAgentUpdate(result);
    toast(t('toast.agentUpdated', { name: result.agent.display_name }));
  } catch (err) {
    state.agentEdit.error = err.message;
    renderAgentSaveBar();
  } finally {
    renderAgentSaveBar();
  }
}

/** 放弃：丢弃草稿、按后端读数重铺（保存条随之消失）。 */
function discardAgentEdit() {
  resetAgentEditDraft();
  renderAgentDetail();
}

/** 就地重绘一组卡片（点卡片 / 点暂停按钮之后）：只改卡片自己的状态，不重建 `#adBody`。
    `attribute` 是组内卡片的取值键（`data-tier-value` / `data-agent-access`），`value` 是
    草稿里这一组当前的值。 */
function paintCardGroup(group, attribute, value) {
  Array.prototype.forEach.call(group.querySelectorAll('[' + attribute + ']'), (card) => {
    const on = card.getAttribute(attribute) === value;
    card.classList.toggle('is-on', on);
    card.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

/** 草稿变了之后跟着变的那几处（档位卡片 / 暂停按钮的说法）：就地重绘，不重建控件。 */
function paintAgentDraftControls() {
  const body = $('#adBody');
  const draft = state.agentEdit.draft;
  if (!body || !draft) return;
  // 档位组的取值字段与组名同名（点击时也是 `setAgentDraft(group.dataset.agentTier, …)`）：
  // 两边共用同一份知识，这里不再按字段名写第二套判断。
  Array.prototype.forEach.call(body.querySelectorAll('[data-agent-tier]'), (group) => {
    paintCardGroup(group, 'data-tier-value', draft[group.dataset.agentTier] ? 'on' : 'off');
  });
  Array.prototype.forEach.call(body.querySelectorAll('[data-agent-access-group]'), (group) => {
    paintCardGroup(group, 'data-agent-access', draft.web_access);
  });
  const toggle = body.querySelector('[data-agent-toggle]');
  if (toggle) toggle.textContent = t(draft.enabled ? 'agentDetail.pause' : 'agentDetail.resume');
}

/** 危险区控件的可用性：离线或请求进行中时禁用，不重建节点。

    它单独成一处，是因为草稿脏时 `renderAgentDetail()` 不重铺整页——离线 / 请求状态仍要
    落到已经存在的按钮上（暂停、删除）。删除弹窗里的三个控件由 `paintAgentDeleteControls()`
    负责（它们在 `#adBody` 之外）。 */
function paintAgentDangerControls() {
  const body = $('#adBody');
  if (!body || typeof body.querySelectorAll !== 'function') return;
  const pending = state.agentDelete.memberId === state.activeAgentId;
  const busy = pending && state.agentDelete.busy;
  ['[data-agent-toggle]', '[data-agent-delete]'].forEach((selector) => {
    Array.prototype.forEach.call(body.querySelectorAll(selector), (node) => {
      node.disabled = busy || !state.online;
    });
  });
}

/** 删除确认弹窗控件的可用性：确认受「请求中 / 离线」约束；取消与关闭只受「请求中」约束
    （离线下仍要能把弹窗收起来）。弹窗节点住在 `index.html`，不随 `#adBody` 重铺。 */
function paintAgentDeleteControls() {
  const busy = state.agentDelete.busy;
  const controls = [
    ['#agentDeleteConfirm', busy || !state.online],
    ['#agentDeleteCancel', busy],
    ['#agentDeleteClose', busy],
  ];
  controls.forEach(([selector, disabled]) => {
    const node = $(selector);
    if (node) node.disabled = Boolean(disabled);
  });
}

/** 删除一个 Agent（弹窗里点了「确认删除」之后才调用）：后端软删除是唯一正本。

    成功后用响应里的最新 `agents` / `members` 覆盖本地镜像（并再过滤一次，保证移除），
    收起弹窗、关闭详情、刷新左栏 / 成员面板，最后给一条 toast。失败时弹窗保持打开、
    按钮恢复可用，错误原因按后端那一句显示——不静默吞掉。 */
async function deleteAgent(memberId) {
  if (!state.online || !memberId) return;
  if (state.agentDelete.busy) return;
  const agent = agentById(memberId);
  const name = (agent && agent.display_name) || t('toast.memberFallback');
  state.agentDelete = { memberId, busy: true };
  paintAgentDangerControls();
  paintAgentDeleteControls();
  try {
    const payload = await api('/api/agents/' + encodeURIComponent(memberId), { method: 'DELETE' });
    if (payload && Array.isArray(payload.agents)) state.agents = payload.agents;
    if (payload && Array.isArray(payload.members)) state.members = payload.members;
    state.agents = state.agents.filter((item) => item.id !== memberId);
    state.members = state.members.filter((item) => item.id !== memberId);
    // 频道成员缓存也是后端读数的镜像（后端已把它移出全部 target_member）：一并摘掉，
    // 否则成员面板 / @ 补全 / 名册会在下一次重读之前继续显示一个已删除的成员。
    Object.keys(state.channelMembers || {}).forEach((channelId) => {
      const members = state.channelMembers[channelId];
      if (Array.isArray(members)) {
        state.channelMembers[channelId] = members.filter((item) => item.id !== memberId);
      }
    });
    delete state.statuses[memberId];
    closeAgentDeleteModal();
    // 私信是否归入「已归档」只由后端事实 `peer.deleted_at` 裁决（`dmIsArchived()`），而 DELETE
    // 响应按既有纪律只带 `deleted` / `agents` / `members`——不含 dms。于是删除成功后重读一次
    // 正本私信列表（`GET /api/dms`）再渲染：否则本地镜像里的 peer 仍没有 `deleted_at`，该行要等
    // 整页重载才挪段（#265）。读失败时 `refreshDmList()` 保留旧镜像并照常返回，删除成功路径与
    // toast 不会被打断（成功落位与 toast 最多晚一次 RTT）。
    await refreshDmList();
    if (state.activeAgentId === memberId) closeAgentDetail();
    else {
      renderMembers();
      renderDock();
      render();
    }
    toast(t('toast.agentDeleted', { name }));
  } catch (err) {
    // 留在当前页：弹窗保持打开、按钮恢复可用，原因照后端给的那一句显示。
    state.agentDelete = { memberId, busy: false };
    paintAgentDangerControls();
    paintAgentDeleteControls();
    toast(t('toast.agentDeleteFailed', { reason: err.message }), 'err');
  }
}

/* ─── 成员名册（频道成员的**只读**全名单） ────────────────────────────────
   `CONTEXT.md` **成员名册**：内容 = 该频道后端成员表**逐条**（`GET /api/channels/{id}/members`，
   含 owner），每条带**状态点**（`GET /api/member-statuses` 的四态，取值走与**成员面板**同一个
   `statusOf()` / `presenceNode()`）。它与**成员面板**的分工是「动作」与「范围」两件事：
   名册**唯一动作是关闭**——加入 / 移除 / 编辑都不是它的面，因此这里没有那些控件，
   也不列「未加入」一段。 */

/** 该频道的**完整**成员表（含 owner）；`null` = 还没读到 / 读不到——与「真的没有成员」
    是两件事（与 `channelAgentMembers()` 同一条纪律，读的是同一份缓存，只是不按 kind 过滤）。 */
function channelMemberRows(channelId) {
  const members = state.channelMembers[channelId];
  return members || null;
}

/** 打开名册（入口 = 频道头部的成员按钮，原型 `#btnChannelMembers`）：锁定当前频道、
    按需读一次它的成员表，然后渲染。名册只对**频道**存在，DM 下入口隐藏（见 `renderHeader()`）。 */
function openRosterModal() {
  const channel = activeChannel();
  if (!channel) return;
  state.rosterChannelId = channel.id;
  // 能力摘要按成员缓存，但缓存只在一次打开期间有效：重新打开就重读，于是「上次读不到」
  // 不会被留成永久结论（计数会随能力配置变化）。
  state.rosterCapabilities = {};
  $('#rosterOverlay').hidden = false;
  renderRoster();
  const close = $('#rosterClose');
  if (close) close.focus();
  // 名册的内容是后端成员表的读数：打开时读一次，读回后按**同一个频道**再渲染一遍
  // （成员在别处长出来后重新打开名册就能看到；弹窗关掉或换过频道就不动它）。
  refreshChannelMembers(channel.id).then(() => {
    if (state.rosterChannelId === channel.id) renderRoster();
  });
}

function closeRosterModal() {
  state.rosterChannelId = null;
  $('#rosterOverlay').hidden = true;
}

/** 名册的**唯一**渲染落点：打开、成员表读回、切语言三处都走它，于是「后端读到的成员表」
    与「名册上列出的成员」不可能各说一份（不在这里另写一遍过滤或排序规则）。 */
function renderRoster() {
  const channelId = state.rosterChannelId;
  if (!channelId) return;
  const channel = state.channels.find((item) => item.id === channelId) || null;
  const members = channelMemberRows(channelId);
  const agentsById = new Map(state.agents.map((agent) => [agent.id, agent]));
  const title = $('#rosterTitle');
  if (title) title.textContent = t('roster.title', { name: channel ? channel.name : '' });
  const sub = $('#rosterSub');
  // 副标题的数字就是成员表的**条数**；读不到时不写 0（那会把「读不到」说成「一个都没有」）。
  const loaded = !channelMembersFailed.has(channelId) && Boolean(members);
  if (sub) sub.textContent = loaded ? t('roster.sub', { count: members.length }) : '';
  const body = $('#rosterBody');
  if (!body) return;
  // 「读不到」先判：读失败时缓存会被置成空表，若不先看这个标记，下面那一步就会把
  // 「暂时读不到」说成「这个频道还没有成员」（与面板同一条纪律，只是名册连旧表也不列）。
  if (channelMembersFailed.has(channelId)) {
    replaceChildren(body, [el('div', 'rail-empty', t('roster.unavailable'))]);
    return;
  }
  if (!members) {
    replaceChildren(body, [el('div', 'rail-empty', t('roster.loading'))]);
    return;
  }
  if (!members.length) {
    replaceChildren(body, [el('div', 'rail-empty', t('roster.empty'))]);
    return;
  }
  replaceChildren(body, members.map((member) => rosterRow(member, agentsById)));
  // 行先铺出来，两个数字随后异步补上：读一条清单慢不阻塞名册的其它行。
  void refreshRosterCapabilities(members);
}

/** 名册行右侧的能力摘要（只读两个数字；读不到只这一行占位，不阻塞其它行）。 */
function rosterCapabilitiesNode(memberId) {
  const node = el('span', 'roster-capabilities');
  node.dataset.rosterCapabilities = memberId;
  node.setAttribute('data-od-id', 'roster-capabilities-' + memberId);
  node.textContent = rosterCapabilitiesText(memberId);
  return node;
}

function rosterCapabilitiesText(memberId) {
  const cache = state.rosterCapabilities[memberId];
  if (!cache) return t('roster.capabilitiesLoading');
  if (cache.failed) return t('roster.capabilitiesUnavailable');
  return t('roster.capabilities', { mcp: cache.mcp, skills: cache.skills });
}

/** 读一个成员的两个计数（并行）；任一失败只把这一条摘要标成不可用，不外溢。 */
async function readMemberCapabilityCounts(memberId) {
  try {
    const [mcp, skills] = await Promise.all([
      api('/api/mcp-servers?scope_kind=agent&scope_id=' + encodeURIComponent(memberId)),
      api('/api/skills?scope_kind=agent&scope_id=' + encodeURIComponent(memberId)),
    ]);
    return {
      memberId,
      mcp: (mcp && Array.isArray(mcp.mcp_servers) ? mcp.mcp_servers : []).length,
      skills: (skills && Array.isArray(skills.skills) ? skills.skills : []).length,
      failed: false,
    };
  } catch {
    return { memberId, mcp: 0, skills: 0, failed: true };
  }
}

/** 按需读取还缺的成员摘要，读回后就地改写已有的行（不整份重铺名册）。 */
async function refreshRosterCapabilities(members) {
  const channelId = state.rosterChannelId;
  if (!channelId) return;
  const pending = (members || [])
    .filter((member) => member.kind === 'agent' && !state.rosterCapabilities[member.id])
    .map((member) => member.id);
  if (!pending.length) return;
  const counts = await Promise.all(pending.map((memberId) => readMemberCapabilityCounts(memberId)));
  if (state.rosterChannelId !== channelId) return; // 已经关掉或换了频道：不把旧读数铺上去
  counts.forEach((item) => {
    state.rosterCapabilities[item.memberId] = { mcp: item.mcp, skills: item.skills, failed: item.failed };
  });
  paintRosterCapabilities();
}

/** 就地改写摘要文本（节点按 `data-roster-capabilities` 找；名册已关时是空操作）。 */
function paintRosterCapabilities() {
  const body = $('#rosterBody');
  if (!body || !body.querySelectorAll) return;
  body.querySelectorAll('[data-roster-capabilities]').forEach((node) => {
    const memberId = node.dataset.rosterCapabilities;
    if (memberId) node.textContent = rosterCapabilitiesText(memberId);
  });
}

/** 名册的一行：头像 + 姓名 / 角色，右侧是状态点。owner 那行右侧是「我」——原型 `:1601-1629`
    与切片对 owner 的呈现就是「我」；`GET /api/member-statuses` 只覆盖 Agent，
    给 owner 造一个状态点等于伪造后端没有的事实。 */
function rosterRow(member, agentsById) {
  const agent = agentsById.get(member.id) || null;
  const row = el('div', 'roster-row');
  row.dataset.member = member.id;
  row.dataset.kind = member.kind;
  row.setAttribute('data-od-id', 'roster-member-' + member.id);
  const who = el('div', 'roster-who');
  who.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
  const text = el('div', 'roster-who-text');
  text.appendChild(el('b', 'roster-name', member.display_name));
  text.appendChild(el('span', 'roster-role', (agent && agent.role) || t('roster.roleFallback')));
  who.appendChild(text);
  row.appendChild(who);
  if (member.kind === 'owner') {
    row.appendChild(el('span', 'roster-me', t('identity.me')));
  } else {
    // 能力摘要（#273）：只读两个数字，来自 agent scope 的两条清单；没有按钮（名册唯一动作是关闭）。
    row.appendChild(rosterCapabilitiesNode(member.id));
    // 状态点按成员 id 查（Agent 配置快照里的 `status` 是后端 `member-statuses` 的回写；
    // 快照里没有这个 Agent 时直接查状态表，而不是凭缺失下一句「未启用」的结论）。
    row.appendChild(presenceNode(statusOf(agent || { id: member.id })));
  }
  return row;
}

function renderTyping() {
  renderTargetTyping($('#typingLine'), activeTarget());
  renderTargetTyping($('#taskTypingLine'), state.activeTask ? { id: state.activeTask.target_id } : null);
}

function renderTargetTyping(container, target) {
  if (!container) return;
  const rounds = target ? (state.activeRounds[target.id] || {}) : {};
  // 正在回复的成员按 `activeRounds` 的插入顺序去重：同一成员有多条在飞轮次时名字只说一次，
  // 停止按钮仍按轮次各一个（按钮挂在 `reply_id` 上，不是挂在成员上）。
  const memberIds = [];
  Object.values(rounds).forEach((memberId) => {
    if (memberId && memberIds.indexOf(memberId) === -1) memberIds.push(memberId);
  });
  // 一轮刚起步、`REPLY_START` 还没到的窗口里 `activeRounds` 还是空的，但 `typing` 事件
  // 已经说了是谁；这一格退回那一个名字，好过让指示条闪一下消失。
  const typingMemberId = target ? state.typing[target.id] : null;
  if (!memberIds.length && typingMemberId) memberIds.push(typingMemberId);
  container.hidden = memberIds.length === 0;
  container.textContent = '';
  if (!memberIds.length) return;
  const names = memberIds.map((memberId) => outcomeMemberName(memberId));
  const text = t('typing.working', { name: names.join(t('typing.nameSeparator')) });
  // 用文本节点而不是 `container.textContent = ...`：下面还要 append 按钮，
  // 文本节点会让「名字 + 按钮」在真实 DOM 与测试替身里都是同一份可观察文本。
  container.appendChild(document.createTextNode(text));
  if (!target) return;
  Object.entries(rounds).forEach(([replyId, activeMemberId]) => {
    const name = outcomeMemberName(activeMemberId);
    const button = el('button', 'btn small', t('typing.stopNamedShort', { name }));
    button.type = 'button';
    button.dataset.replyId = replyId;
    button.setAttribute('aria-label', t('typing.stopNamed', { name }));
    button.disabled = !state.online;
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await api('/api/targets/' + encodeURIComponent(target.id) + '/rounds/' +
          encodeURIComponent(replyId) + '/interrupt', { method: 'POST' });
        finishRoundFor(target.id, replyId);
      } catch (err) {
        if (err.status === 409) finishRoundFor(target.id, replyId);
        else {
          button.disabled = false;
          toast(err.message, 'err');
        }
      }
    });
    container.appendChild(button);
  });
}

function finishRoundFor(targetId, replyId) {
  const rounds = state.activeRounds[targetId] || {};
  delete rounds[replyId];
  const remaining = Object.values(rounds);
  if (!remaining.length) delete state.activeRounds[targetId];
  showTypingFor(targetId, remaining[0] || null);
}

/* ─── 频道回应预算（#54 / ADR-0007 / 规格 #31 §2）：读数是后端事实的镜像，写控件只表达**上界** ──

   `CONTEXT.md` **回应预算**：每个 target 每个**人类世代**最多可发生的 Agent→Agent 跳数。上界是
   **库内逐频道配置**（ADR-0007），人类面只能改它；用量（`used`）是后端的正本——后端在人类消息
   落库开世代时重置、并只在一次**实际投递**的跳上 +1。因此这张频道设置弹窗里：

   - 只读的四项读数（已用 / 剩余 / 有效上界 / **来源**）与待生效值全部来自
     `GET /api/channels/{id}/budget`，前端不计算、不推断、也不保存跨会话的旧读数；
   - 写控件只有固定档位 3 / 5 / 10（默认 3），提交 `{budget_limit: <档位>}`，**绝不提交**任何用量
     字段（后端也会整体拒绝：用量不可写，`error.budget_used_read_only`）；
   - 保存后按**响应里的新读数**重铺（一次调用即刷新），并明确提示「从下一个人类世代生效」——
     当前世代的 `limit` / `used` / `remaining` 不变由后端保证，前端不乐观改写本地读数。 */

/** 固定档位（与后端 `BUDGET_LIMIT_TIERS` 同序同名）；默认档位是后端内置默认 3（ADR-0002 未修订）。 */
const BUDGET_LIMIT_TIERS = [3, 5, 10];
const BUDGET_DEFAULT_LIMIT = 3;

/** 来源取值的界面说法（键名与后端 `source` 的取值同名；未知取值原样回退，不伪造一个说法）。 */
function budgetSourceLabel(source) {
  const key = 'budget.source.' + source;
  return hasKey(key) ? t(key) : source;
}

/** 弹窗里「已保存的档位」：待生效值优先；否则「当前有效上界就是频道覆盖」那份；再否则内置默认。 */
function savedBudgetTier(budget) {
  if (!budget) return BUDGET_DEFAULT_LIMIT;
  const pending = budget.pending_limit;
  if (pending !== null && pending !== undefined) return Number(pending);
  return budget.source === 'channel-override' ? Number(budget.limit) : BUDGET_DEFAULT_LIMIT;
}

/** 弹窗的空镜像（打开 / 关闭都用它，于是「缓存了过期读数」这个中间态不存在）。 */
function emptyBudgetView() {
  return { channelId: null, budget: null, selected: null, loading: false, error: '' };
}

/** 打开弹窗（入口 = 频道头的预算按钮）：锁定当前频道、先显示「读取中」，随后按后端读数渲染。 */
async function openBudgetModal() {
  const channel = activeChannel();
  if (!channel) return;
  state.channelBudget = {
    channelId: channel.id,
    budget: null,
    selected: null,
    loading: true,
    error: '',
  };
  $('#budgetOverlay').hidden = false;
  const box = $('#budgetFormError');
  if (box) box.hidden = true;
  renderBudgetModal();
  await loadChannelBudget(channel.id);
}

/** 读一次该频道的预算读数（正本是后端）。读回后按**同一个频道**重铺——关掉 / 换过频道就不动它。 */
async function loadChannelBudget(channelId) {
  try {
    const payload = await api(channelPath(channelId, '/budget'));
    if (state.channelBudget.channelId !== channelId) return;
    state.channelBudget.budget = payload.budget;
    state.channelBudget.selected = savedBudgetTier(payload.budget);
    state.channelBudget.loading = false;
    state.channelBudget.error = '';
  } catch (err) {
    if (state.channelBudget.channelId !== channelId) return;
    state.channelBudget.loading = false;
    state.channelBudget.error = err.message;
  }
  renderBudgetModal();
}

function closeBudgetModal() {
  state.channelBudget = emptyBudgetView();
  $('#budgetOverlay').hidden = true;
  const box = $('#budgetFormError');
  if (box) box.hidden = true;
}

/** 选中一个档位（**还没提交**的呈现状态）。不在固定档位里的取值连选中都不成立。 */
function selectBudgetTier(tier) {
  const value = Number(tier);
  if (!BUDGET_LIMIT_TIERS.includes(value)) return;
  if (!state.channelBudget.channelId) return;
  state.channelBudget.selected = value;
  renderBudgetModal();
}

/** 保存选中的档位：写后端（正本），成功后按响应里的新读数重铺并提示下个世代生效。

    失败时**不**改本地读数——界面不能声称一个没落库的档位（与 `saveSettings` 同一条纪律）。 */
async function submitBudgetTier() {
  const view = state.channelBudget;
  const channelId = view.channelId;
  const tier = view.selected;
  if (!channelId || tier === null || !BUDGET_LIMIT_TIERS.includes(Number(tier))) return;
  const box = $('#budgetFormError');
  if (box) box.hidden = true;
  try {
    const payload = await api(channelPath(channelId, '/budget'), {
      method: 'PATCH',
      body: { budget_limit: Number(tier) },
    });
    if (state.channelBudget.channelId !== channelId) return;
    state.channelBudget.budget = payload.budget;
    state.channelBudget.selected = savedBudgetTier(payload.budget);
    state.channelBudget.error = '';
    state.channelBudget.loading = false;
    renderBudgetModal();
    toast(t('budget.saved', { limit: Number(tier) }));
  } catch (err) {
    if (state.channelBudget.channelId !== channelId) return;
    if (box) {
      box.textContent = t('budget.saveFailed', { reason: err.message });
      box.hidden = false;
    }
    renderBudgetModal();
  }
}

/** 一行说明（`.field .hint` 那一档的排版，不新造样式）。 */
function budgetHint(key, odId) {
  const field = el('div', 'field');
  const hint = el('div', 'hint', t(key));
  if (odId) hint.setAttribute('data-od-id', odId);
  field.appendChild(hint);
  return field;
}

/** 弹窗的唯一渲染落点：读数 / 待生效 / 写控件都从这里出，于是「读数」与「可写档位」不会各说一份。 */
function renderBudgetModal() {
  const view = state.channelBudget;
  const body = $('#budgetBody');
  if (!body) return;
  const channel = view.channelId ? state.channels.find((item) => item.id === view.channelId) : null;
  const title = $('#budgetTitle');
  if (title) title.textContent = t('budget.title');
  const sub = $('#budgetSub');
  if (sub) sub.textContent = t('budget.sub', { name: channel ? channel.name : '' });
  if (!view.channelId) {
    replaceChildren(body, []);
    return;
  }
  if (view.loading) {
    replaceChildren(body, [el('div', 'rail-empty', t('budget.loading'))]);
    return;
  }
  if (view.error) {
    replaceChildren(body, [el('div', 'rail-empty', t('budget.loadFailed', { reason: view.error }))]);
    return;
  }
  const budget = view.budget;
  if (!budget) {
    replaceChildren(body, []);
    return;
  }

  const nodes = [];

  // ① 当前人类世代：四项读数，`remaining` 是后端按**当前有效上界**算好的那一份。
  const current = el('section', 'd-sec');
  current.appendChild(sectionTitle('budget.sectionCurrent', 'budget-section-current'));
  current.appendChild(kvRow(t('budget.used'), String(budget.used), 'budget-used'));
  current.appendChild(kvRow(t('budget.remaining'), String(budget.remaining), 'budget-remaining'));
  current.appendChild(kvRow(t('budget.limit'), String(budget.limit), 'budget-limit'));
  current.appendChild(kvRow(t('budget.source'), budgetSourceLabel(budget.source), 'budget-source'));
  if (Number(budget.remaining) === 0) {
    // 预算是**耗尽**，不是出错：它和轮次结果里的 `budget-exhausted` 是同一条事实
    // （消息照常落库、零投递），因此**不进错误区**，只在读数里说清楚。
    current.appendChild(kvRow(t('budget.state'), t('budget.exhausted'), 'budget-exhausted'));
    current.appendChild(budgetHint('budget.exhaustedNote', 'budget-exhausted-note'));
  }
  nodes.push(current);

  // ② 下一个人类世代：待生效值与当前有效上界**分开**一行，避免把待生效配置误读成当前额度。
  const next = el('section', 'd-sec');
  next.appendChild(sectionTitle('budget.sectionNext', 'budget-section-next'));
  const pending = budget.pending_limit;
  const hasPending = pending !== null && pending !== undefined;
  next.appendChild(
    kvRow(t('budget.pending'), String(hasPending ? pending : budget.limit), 'budget-pending'),
  );
  next.appendChild(
    budgetHint(
      hasPending ? 'budget.pendingEffective' : 'budget.pendingSame',
      'budget-pending-note',
    ),
  );
  nodes.push(next);

  // ③ 写控件：只有固定三档（没有可输入任意数字的控件 ⇒ 非法值不会被提交）。
  const tiers = el('section', 'd-sec');
  tiers.appendChild(sectionTitle('budget.tierLabel', 'budget-section-tiers'));
  const row = el('div', 'radio-row');
  BUDGET_LIMIT_TIERS.forEach((tier) => {
    const on = Number(view.selected) === tier;
    const card = el('button', 'radio-card');
    card.type = 'button';
    card.dataset.budgetTier = String(tier);
    card.setAttribute('data-od-id', 'budget-tier-' + tier);
    card.setAttribute('aria-pressed', on ? 'true' : 'false');
    card.classList.toggle('is-on', on);
    card.appendChild(el('b', null, String(tier)));
    card.appendChild(
      el(
        'span',
        null,
        tier === BUDGET_DEFAULT_LIMIT ? t('budget.tierDefaultUnit') : t('budget.tierUnit'),
      ),
    );
    row.appendChild(card);
  });
  tiers.appendChild(row);
  tiers.appendChild(budgetHint('budget.tierHint', 'budget-tier-hint'));
  const actions = el('div', 'agent-actions');
  const save = el('button', 'btn btn-primary', t('budget.save'));
  save.type = 'button';
  save.dataset.budgetSave = '1';
  save.setAttribute('data-od-id', 'budget-save');
  // 「与已保存的档位相同」时没有可提交的改动：按钮禁用，而不是发一个空转的 PATCH。
  save.disabled =
    !state.online || view.selected === null || Number(view.selected) === savedBudgetTier(budget);
  actions.appendChild(save);
  tiers.appendChild(actions);
  nodes.push(tiers);

  replaceChildren(body, nodes);
}


/* ─── 频道设置（#154 / 规格 §4.6 / ADR-0018 / ADR-0021）────────────────────────

   频道头入口打开一张弹窗：mode（autonomous / team）、`team_scope`（**只读**，后端固定
   `channel+task`）、队长、项目根、默认后端、默认权限档。纪律与回应预算 / 工作区设置一致：

   - 读数全部来自 `GET /api/channels/{id}/config`，前端不推算 `leader_available`——
     它是**编排按后端事实**算出来的（`team.leader_*` 文案也就是频道沉默时的同一份原因）；
   - 保存提交 `PATCH /api/channels/{id}/config`，成功按**响应里的新配置**重铺；
     失败只显示后端可读原因，不把没落库的值写回界面；
   - 「任一字段不合法整体拒绝」由后端保证（DB `update_channel_config` 校验先于写入）；
     前端不做第二套校验，也就不可能出现「前端放行、后端半写」的中间态；
   - `team_scope` 不在可写字段里（后端 `extra="forbid"`），这里只显示，不提交。

   任务视图消费同一个端点（见 `loadTaskChannelConfig`）：team 频道下任务线程只唤醒队长。 */

/** 与后端 `CHANNEL_MODES` / `WORKSPACE_BACKENDS` / `PERMISSION_MODES` 同序同名。 */
const CHANNEL_MODES = ['autonomous', 'team'];
const WORKSPACE_BACKENDS = ['local', 'docker'];
const PERMISSION_MODES = ['read-only', 'ask-writes', 'trusted-writes'];
// 联网档（三态）：`off` 是显式关闭，`search` 是默认档；后端仍有库级 CHECK，前端只挡词表外的值。
const WEB_ACCESS_LEVELS = ['off', 'search', 'search+fetch'];
// Agent 编辑面的第四个选项：**不是**后端档位，是「没有 Agent 级覆盖」的界面 token。
// 选中它提交 `web_access: null`（= 清除覆盖），读回 `null` 时也选中它（ADR-0026 §十五）。
const WEB_ACCESS_FOLLOW = 'follow';

/** 弹窗的空镜像（打开 / 关闭都用它，于是「缓存了过期配置」这个中间态不存在）。 */
function emptyChannelConfigView() {
  return {
    channelId: null,
    config: null,
    members: [],
    membersFailed: false,
    loading: false,
    error: '',
    draft: {
      mode: 'autonomous',
      leader: '',
      workspaceRoot: '',
      backend: 'local',
      permission: 'ask-writes',
      webAccess: 'search',
    },
  };
}

/** 把后端配置铺成**未提交的呈现状态**；`workspace_root=null` 在输入框里就是空串。 */
function channelConfigDraftFrom(config) {
  return {
    mode: config.mode,
    leader: config.team_leader_member_id || '',
    workspaceRoot: config.workspace_root || '',
    backend: config.workspace_backend,
    permission: config.permission_mode,
    // `null` = 未配置；读路径回退默认档，界面因此按 `search` 选中（保存后成为显式值）。
    webAccess: WEB_ACCESS_LEVELS.includes(config.web_access) ? config.web_access : 'search',
  };
}

/* ─── 频道头的**协作模式 pill** ──────────────────────────────────────────

   人打开一个频道就得看出它是**自主**还是 **team 模式（队长制）**，不必先打开设置面。纪律：

   - **数据正本只有一处**：`GET /api/channels/{id}/config` 的 `mode` / `team_leader_member_id`，
     缓存沿用 `state.channelConfigs`（`loadTaskChannelConfig` 是同一条读取路径）；前端不推算、
     不自己判断词表以外的东西；
   - 队长显示名取该频道成员表（`GET /api/channels/{id}/members`）里对应成员的 `display_name`；
     该成员读不到就只显示模式名，**不编一个人名**出来；
   - 读不到 / 还没读回 ⇒ pill `hidden`（沿用 `loadTaskChannelConfig` 的既有纪律：不伪造
     team / autonomous 结论，下次切 target 或重载工作台再试）；
   - 私信没有频道 mode，`dm` 型 target 下同样隐藏。

   读取点只有两处（
   `loadWorkspace` 载入工作台、`selectTarget` 切 target）；`renderHeader()` 只画缓存，不发请求，
   而设置面保存成功后由 `cacheChannelConfig()` 就地重铺——于是「保存后立即更新」不需要刷新页面。 */

/** 在飞的频道配置读取：**同一个频道复用同一个 promise**（与 `channelMembersInFlight` 同一条纪律：
    早退会让频道头的 pill 与任务线程各发一次请求）。 */
const channelConfigsInFlight = new Map();

/** 频道配置缓存的**唯一写入口**：写下就按它重铺频道头的 pill（读数正本仍是后端响应）。 */
function cacheChannelConfig(channelId, config) {
  state.channelConfigs[channelId] = config;
  if (state.activeChannelId === channelId) renderChannelModePill();
}

/** pill 的读数：`null` = 现在不该显示（私信 / 没有频道 / 配置还没读回 / 后端给了词表外的值）。 */
function channelModeBadge(config) {
  if (!config) return null;
  if (config.mode === 'autonomous') {
    const text = t('channelConfig.mode.autonomous');
    return { text, label: t('channelConfig.modeBadge.title', { mode: text }) };
  }
  if (config.mode !== 'team') return null; // 后端词表外的取值：不猜，pill 保持隐藏。
  const leader = channelModeLeaderName(config.team_leader_member_id);
  const text = leader
    ? t('channelConfig.modeBadge.teamWithLeader', { leader })
    : t('channelConfig.mode.team');
  return { text, label: t('channelConfig.modeBadge.title', { mode: text }) };
}

/** 队长显示名。成员表还没读回（与「读到了但没有这个人」是两件事）时给空串：先用模式名，
    读到后重绘，不留占位名。 */
function channelModeLeaderName(memberId) {
  if (!memberId) return '';
  const members = state.channelMembers[state.activeChannelId];
  if (!members) return '';
  const member = members.find((item) => item.id === memberId);
  return (member && member.display_name) || '';
}

/** 按缓存（= 后端读数）重铺频道头的 pill。**不发请求**：读不回来就 hidden。 */
function renderChannelModePill() {
  const pill = $('#chanModePill');
  if (!pill) return;
  const channel = activeChannel();
  // 私信 / 没有 target 时 `activeChannel()` 为 null，pill 随之隐藏（DM 没有频道 mode）。
  const badge = channel ? channelModeBadge(state.channelConfigs[channel.id]) : null;
  pill.hidden = !badge;
  pill.textContent = badge ? badge.text : '';
  // 无障碍：说明它是「协作模式」及当前值（`title` 与 `aria-label` 同一句）。
  // 隐藏态写**空串**而不是摘除属性：空串在真实 DOM 里就是「没有这个名字 / 没有提示」
  // （`aria-label=""` 落在 accname 的下一层，`title=""` 不弹提示），而且这条渲染路径只依赖
  // `setAttribute` 一种写法——`removeAttribute` 是可选方法，缺位时抛出的异常会被
  // `loadWorkspace()` 的 catch 当成「后端不可达」，进而排上下一次重连定时器。
  pill.setAttribute('title', badge ? badge.label : '');
  pill.setAttribute('aria-label', badge ? badge.label : '');
}

/** pill 的**读数入口**：把当前频道要的两份读数（配置 + 队长所在的成员表）读到，再铺 pill。

    除了「载入工作台」与「切 target」两处，别处不发起读取（`renderChannelModePill()` 只画）。
    次序是先配置后成员：先知道 mode，才知道要不要为了一个队长名去读成员表。 */
async function loadActiveChannelMode() {
  const channel = activeChannel();
  if (!channel) {
    renderChannelModePill(); // 私信 / 没 target：顺手把 pill 收回隐藏态
    return null;
  }
  const config = await loadTaskChannelConfig(channel.id);
  if (state.activeChannelId !== channel.id) return config; // 已经切走了：不用旧频道读数铺屏
  // 队长显示名在成员表里：还没读回就补读一次（读到了但没这个人 = 只显示模式名，不编人名）。
  if (
    config
    && config.mode === 'team'
    && config.team_leader_member_id
    && state.channelMembers[channel.id] === undefined
  ) {
    await refreshChannelMembers(channel.id);
  }
  if (state.activeChannelId === channel.id) renderChannelModePill();
  return config;
}

/** 打开频道设置面（入口 = 频道头的设置按钮）：锁定当前频道，先显示「读取中」。 */
async function openChannelConfigModal() {
  const channel = activeChannel();
  if (!channel) return;
  state.channelConfig = emptyChannelConfigView();
  state.channelConfig.channelId = channel.id;
  state.channelConfig.loading = true;
  state.webProviders = emptyWebProvidersView();
  $('#channelConfigOverlay').hidden = false;
  const box = $('#channelConfigFormError');
  if (box) box.hidden = true;
  renderChannelConfigModal();
  await Promise.all([loadChannelConfig(channel.id), loadWebProviders()]);
}

/** 读一次该频道的配置 + 成员表（队长候选）。读回后按**同一个频道**重铺。 */
async function loadChannelConfig(channelId) {
  const view = state.channelConfig;
  try {
    const payload = await api(channelPath(channelId, '/config'));
    const members = await refreshChannelMembers(channelId);
    if (state.channelConfig.channelId !== channelId) return;
    view.config = payload.config;
    cacheChannelConfig(channelId, payload.config);
    view.members = (members || []).filter((member) => member.kind === 'agent');
    view.membersFailed = channelMembersFailed.has(channelId);
    view.draft = channelConfigDraftFrom(payload.config);
    view.loading = false;
    view.error = '';
  } catch (err) {
    if (state.channelConfig.channelId !== channelId) return;
    view.loading = false;
    view.error = err.message;
  }
  renderChannelConfigModal();
}

function closeChannelConfigModal() {
  state.channelConfig = emptyChannelConfigView();
  $('#channelConfigOverlay').hidden = true;
  const box = $('#channelConfigFormError');
  if (box) box.hidden = true;
}

/** 任务视图与频道头 pill 读取**同一份**配置；已缓存时直接复用（正本仍是后端）。
    同一频道的在飞读取复用同一个 promise（见 `channelConfigsInFlight`）。 */
async function loadTaskChannelConfig(channelId) {
  if (!channelId) return null;
  if (state.channelConfigs[channelId]) return state.channelConfigs[channelId];
  const inFlight = channelConfigsInFlight.get(channelId);
  if (inFlight) return inFlight;
  const read = (async () => {
    try {
      const payload = await api(channelPath(channelId, '/config'));
      cacheChannelConfig(channelId, payload.config);
      if (state.activeTask && state.activeTask.channel_id === channelId) renderTaskThread();
      return payload.config;
    } catch {
      // 读不到就保持未缓存：任务线程与频道头的 pill 都不伪造 team / autonomous 结论，下次再试。
      return null;
    }
  })();
  channelConfigsInFlight.set(channelId, read);
  try {
    return await read;
  } finally {
    channelConfigsInFlight.delete(channelId);
  }
}

/** 档位选择（都是**还没提交**的呈现状态；不在后端词表里的取值连选中都不成立）。 */
function selectChannelConfigMode(mode) {
  if (!CHANNEL_MODES.includes(mode) || !state.channelConfig.channelId) return;
  state.channelConfig.draft.mode = mode;
  renderChannelConfigModal();
}

function selectChannelConfigBackend(backend) {
  if (!WORKSPACE_BACKENDS.includes(backend) || !state.channelConfig.channelId) return;
  state.channelConfig.draft.backend = backend;
  renderChannelConfigModal();
}

function selectChannelConfigPermission(permission) {
  if (!PERMISSION_MODES.includes(permission) || !state.channelConfig.channelId) return;
  state.channelConfig.draft.permission = permission;
  renderChannelConfigModal();
}

function selectChannelConfigWebAccess(level) {
  if (!WEB_ACCESS_LEVELS.includes(level) || !state.channelConfig.channelId) return;
  state.channelConfig.draft.webAccess = level;
  renderChannelConfigModal();
}

/** provider 的显示名（字面键；动态取值不拼词条键，词条守卫才能看见全部键）。 */
function webProviderLabel(provider) {
  if (provider === 'brave') return t('webProviders.provider.brave');
  if (provider === 'tavily') return t('webProviders.provider.tavily');
  if (provider === 'perplexity') return t('webProviders.provider.perplexity');
  if (provider === 'jina') return t('webProviders.provider.jina');
  if (provider === 'exa') return t('webProviders.provider.exa');
  return t('webProviders.provider.fallback');
}

function webProviderSourceLabel(source) {
  if (source === 'env') return t('webProviders.source.env');
  if (source === 'ui') return t('webProviders.source.ui');
  if (source === 'invalid') return t('webProviders.source.invalid');
  return t('webProviders.source.fallback');
}

/** 联网 provider 的空镜像（打开 / 关闭弹窗都用它；不含任何已保存的 key）。 */
function emptyWebProvidersView() {
  return {
    loading: false,
    error: '',
    providers: [],
    active: null,
    draftKeys: {},
    errors: {},
  };
}

/** 读五家的掩码状态与当前生效 provider；失败只落在联网这一段自己的错误行。 */
async function loadWebProviders() {
  const view = state.webProviders;
  const channelId = state.channelConfig.channelId;
  view.loading = true;
  view.error = '';
  if (channelId) renderChannelConfigModal();
  try {
    const payload = await api('/api/web-providers');
    if (state.channelConfig.channelId !== channelId) return;
    view.providers = payload.providers || [];
    view.active = payload.active || null;
  } catch (err) {
    if (state.channelConfig.channelId !== channelId) return;
    view.error = t('webProviders.loadFailed', { reason: err.message });
  }
  if (state.channelConfig.channelId !== channelId) return;
  view.loading = false;
  renderChannelConfigModal();
}

function webProviderStatusText(item) {
  if (item.masked) return t('webProviders.configuredWithMask', { masked: item.masked });
  if (item.env_configured) return t('webProviders.configuredFromEnv');
  return t('webProviders.notConfigured');
}

/** 保存一家 provider 的 key：请求里只出现刚输入的明文，响应只回掩码。 */
async function saveWebProviderKey(provider) {
  const view = state.webProviders;
  const key = String(view.draftKeys[provider] || '').trim();
  if (!key) {
    view.errors[provider] = t('webProviders.keyRequired');
    renderChannelConfigModal();
    return;
  }
  view.errors[provider] = '';
  try {
    await api('/api/web-providers/' + encodeURIComponent(provider) + '/credential', {
      method: 'PUT',
      body: { api_key: key },
    });
    delete view.draftKeys[provider];
    await loadWebProviders();
    toast(t('webProviders.saved', { provider: webProviderLabel(provider) }));
  } catch (err) {
    view.errors[provider] = t('webProviders.saveFailed', { reason: err.message });
    renderChannelConfigModal();
  }
}

/** 切换默认 provider：不带 key，只改默认标记。 */
async function setDefaultWebProvider(provider) {
  const view = state.webProviders;
  view.errors[provider] = '';
  try {
    await api('/api/web-providers/' + encodeURIComponent(provider) + '/credential', {
      method: 'PUT',
      body: { is_default: true },
    });
    await loadWebProviders();
    toast(t('webProviders.defaultSet', { provider: webProviderLabel(provider) }));
  } catch (err) {
    view.errors[provider] = t('webProviders.defaultFailed', { reason: err.message });
    renderChannelConfigModal();
  }
}

/** 清除一家 provider 的凭据（连同默认标记）；确认行保留。 */
async function clearWebProvider(provider) {
  const view = state.webProviders;
  view.errors[provider] = '';
  try {
    await api('/api/web-providers/' + encodeURIComponent(provider) + '/credential', {
      method: 'DELETE',
    });
    delete view.draftKeys[provider];
    await loadWebProviders();
    toast(t('webProviders.cleared', { provider: webProviderLabel(provider) }));
  } catch (err) {
    view.errors[provider] = t('webProviders.clearFailed', { reason: err.message });
    renderChannelConfigModal();
  }
}

/** 一家 provider 的一行：掩码状态 + password 输入 + 保存 / 默认 / 清除。 */
function webProviderRow(item) {
  const view = state.webProviders;
  const row = el('div', 'web-provider-row');
  row.setAttribute('data-od-id', 'web-provider-' + item.provider);
  const head = el('div', 'web-provider-head');
  head.appendChild(el('b', null, webProviderLabel(item.provider)));
  const status = el(
    'span',
    'resource-meta' + (item.configured ? '' : ' is-faint'),
    webProviderStatusText(item),
  );
  status.setAttribute('data-od-id', 'web-provider-status-' + item.provider);
  head.appendChild(status);
  if (item.is_default) {
    const badge = el('span', 'web-provider-badge', t('webProviders.defaultBadge'));
    badge.setAttribute('data-od-id', 'web-provider-default-' + item.provider);
    head.appendChild(badge);
  }
  row.appendChild(head);

  const controls = el('div', 'resource-form-row');
  const field = el('div', 'field');
  const label = el('label', null, t('webProviders.keyLabel', { provider: webProviderLabel(item.provider) }));
  label.setAttribute('for', 'web-provider-key-' + item.provider);
  field.appendChild(label);
  const input = el('input', 'input');
  input.type = 'password';
  input.autocomplete = 'new-password';
  input.id = 'web-provider-key-' + item.provider;
  input.dataset.webKey = item.provider;
  input.setAttribute('data-od-id', 'web-provider-key-' + item.provider);
  input.placeholder = item.masked || t('webProviders.keyPlaceholder');
  input.value = view.draftKeys[item.provider] || '';
  field.appendChild(input);
  controls.appendChild(field);

  const actions = el('div', 'agent-actions');
  const save = el('button', 'btn btn-primary', t('webProviders.save'));
  save.type = 'button';
  save.dataset.webSave = item.provider;
  save.setAttribute('data-od-id', 'web-provider-save-' + item.provider);
  save.disabled = !state.online;
  actions.appendChild(save);

  const makeDefault = el('button', 'btn', t('webProviders.makeDefault'));
  makeDefault.type = 'button';
  makeDefault.dataset.webDefault = item.provider;
  makeDefault.setAttribute('data-od-id', 'web-provider-make-default-' + item.provider);
  makeDefault.disabled = !state.online || !item.configured || item.is_default;
  actions.appendChild(makeDefault);

  const clear = el('button', 'btn', t('webProviders.clear'));
  clear.type = 'button';
  clear.dataset.webClear = item.provider;
  clear.setAttribute('data-od-id', 'web-provider-clear-' + item.provider);
  clear.disabled = !state.online || !item.configured;
  actions.appendChild(clear);
  controls.appendChild(actions);
  row.appendChild(controls);

  row.appendChild(el('div', 'resource-meta is-faint', t('webProviders.dataNotice')));
  if (view.errors[item.provider]) {
    row.appendChild(resourceErrorNode(view.errors[item.provider], 'web-provider-error-' + item.provider));
  }
  return row;
}

/** 联网区块：档位三选 + 当前 provider / 来源 + 五家的凭据行。 */
function webAccessSection(view) {
  const section = el('section', 'd-sec');
  section.appendChild(sectionTitle('channelConfig.sectionWeb', 'channel-config-section-web'));
  section.appendChild(
    channelConfigRadioRow(
      WEB_ACCESS_LEVELS,
      view.draft.webAccess,
      'configWebAccess',
      'channelConfig.webAccess.',
      'channelConfig.webAccess.',
    ),
  );
  section.appendChild(channelConfigHint('channelConfig.webAccessHint', 'channel-config-web-access-hint'));

  const providers = state.webProviders;
  if (providers.error) {
    section.appendChild(resourceErrorNode(providers.error, 'channel-config-web-providers-error'));
    return section;
  }
  if (providers.active) {
    const provider = providers.active.provider
      ? webProviderLabel(providers.active.provider)
      : t('webProviders.none');
    const line = kvRow(
      t('webProviders.active'),
      t('webProviders.activeValue', {
        provider,
        source: webProviderSourceLabel(providers.active.source),
      }),
      'channel-config-web-active',
    );
    section.appendChild(line);
    if (providers.active.reason) {
      const reason = el('div', 'dock-member-reason', providers.active.reason);
      reason.setAttribute('data-od-id', 'channel-config-web-active-reason');
      section.appendChild(reason);
    }
  }
  const list = el('div', 'web-provider-list');
  list.setAttribute('data-od-id', 'channel-config-web-providers');
  (providers.providers || []).forEach((item) => list.appendChild(webProviderRow(item)));
  section.appendChild(list);
  return section;
}

/** 一行说明（`.field .hint` 那一档的排版，不新造样式）。 */
function channelConfigHint(key, odId) {
  const field = el('div', 'field');
  const hint = el('div', 'hint', t(key));
  if (odId) hint.setAttribute('data-od-id', odId);
  field.appendChild(hint);
  return field;
}

/** 一组单档位卡片：值 + 主文案 + 说明。点哪张写哪张（提交仍由保存按钮表达）。 */
function channelConfigRadioRow(values, selected, dataKey, labelPrefix, notePrefix) {
  const row = el('div', 'radio-row');
  values.forEach((value) => {
    const on = selected === value;
    const card = el('button', 'radio-card');
    card.type = 'button';
    card.dataset[dataKey] = value;
    card.setAttribute(
      'data-od-id',
      dataKey.replace(/[A-Z]/g, (letter) => '-' + letter.toLowerCase()) + '-' + value,
    );
    card.setAttribute('aria-pressed', on ? 'true' : 'false');
    card.classList.toggle('is-on', on);
    card.appendChild(el('b', null, t(labelPrefix + value)));
    card.appendChild(el('span', null, t(notePrefix + value + 'Note')));
    row.appendChild(card);
  });
  return row;
}

/** 队长的候选：该频道的 Agent 成员；已保存但不在成员表里的队长也保留一个选项，
    以免下拉框看起来「选中的是别人」。可用性始终由后端读数说明。 */
function channelConfigLeaderOptions(view) {
  const options = view.members.slice();
  const leader = view.draft.leader;
  if (leader && !options.some((member) => member.id === leader)) {
    const agent = state.agents.find((item) => item.id === leader);
    options.push(agent || { id: leader, display_name: leader });
  }
  return options;
}

/** 弹窗的唯一渲染落点：读数与写控件都从这里出，于是两者不会各说一份。
    重铺正文会移除持焦的档位按钮，Chromium 会因此把滚动容器同步拉回顶部（ego 实测，不是
    应用里的显式赋值）；所以铺前记下 `#channelConfigOverlay.scrollTop`、铺后写回。新内容更短时
    由浏览器按上限自然截断；overlay 不存在时安全跳过。 */
function renderChannelConfigModal() {
  const overlay = $('#channelConfigOverlay');
  const scrollTop = overlay ? overlay.scrollTop : 0;
  renderChannelConfigModalContent();
  if (overlay) overlay.scrollTop = scrollTop;
}

/** 正文重建；滚动位置由 `renderChannelConfigModal()` 在铺前铺后统一保持。 */
function renderChannelConfigModalContent() {
  const view = state.channelConfig;
  const body = $('#channelConfigBody');
  if (!body) return;
  const channel = view.channelId
    ? state.channels.find((item) => item.id === view.channelId)
    : null;
  const title = $('#channelConfigTitle');
  if (title) title.textContent = t('channelConfig.title');
  const sub = $('#channelConfigSub');
  if (sub) sub.textContent = t('channelConfig.sub', { name: channel ? channel.name : '' });
  if (!view.channelId) {
    replaceChildren(body, []);
    return;
  }
  if (view.loading) {
    replaceChildren(body, [el('div', 'rail-empty', t('channelConfig.loading'))]);
    return;
  }
  if (view.error) {
    replaceChildren(body, [
      el('div', 'rail-empty', t('channelConfig.loadFailed', { reason: view.error })),
    ]);
    return;
  }
  const config = view.config;
  if (!config) {
    replaceChildren(body, []);
    return;
  }

  const nodes = [];

  // ① 协作模式。
  const mode = el('section', 'd-sec');
  mode.appendChild(sectionTitle('channelConfig.sectionMode', 'channel-config-section-mode'));
  mode.appendChild(
    channelConfigRadioRow(CHANNEL_MODES, view.draft.mode, 'configMode', 'channelConfig.mode.', 'channelConfig.mode.'),
  );
  nodes.push(mode);

  // ② team 的生效范围：后端只读事实，前端只解释它。
  const scope = el('section', 'd-sec');
  scope.appendChild(sectionTitle('channelConfig.sectionScope', 'channel-config-section-scope'));
  scope.appendChild(kvRow(t('channelConfig.teamScope'), config.team_scope, 'channel-config-team-scope'));
  scope.appendChild(channelConfigHint('channelConfig.teamScopeNote', 'channel-config-team-scope-note'));
  nodes.push(scope);

  // ③ 队长：候选是当前频道的 Agent 成员；可用性与原因都是后端事实。
  const leader = el('section', 'd-sec');
  leader.appendChild(sectionTitle('channelConfig.sectionLeader', 'channel-config-section-leader'));
  const leaderField = el('div', 'field');
  leaderField.appendChild(el('label', null, t('channelConfig.leaderSelectLabel')));
  const select = el('select', 'input');
  select.dataset.configLeader = '1';
  select.setAttribute('data-od-id', 'channel-config-leader');
  const none = el('option', null, t('channelConfig.leaderNone'));
  none.value = '';
  select.appendChild(none);
  channelConfigLeaderOptions(view).forEach((member) => {
    const option = el('option', null, member.display_name);
    option.value = member.id;
    select.appendChild(option);
  });
  select.value = view.draft.leader;
  leaderField.appendChild(select);
  leader.appendChild(leaderField);
  if (view.membersFailed) {
    leader.appendChild(channelConfigHint('channelConfig.membersUnavailable', 'channel-config-members-failed'));
  }
  leader.appendChild(
    kvRow(
      t('channelConfig.leaderAvailability'),
      config.leader_available
        ? t('channelConfig.leaderAvailable')
        : t('channelConfig.leaderUnavailable'),
      'channel-config-leader-availability',
    ),
  );
  if (config.leader_reason) {
    const reason = el(
      'div',
      'dock-member-reason',
      t('channelConfig.leaderReason', { reason: config.leader_reason }),
    );
    reason.setAttribute('data-od-id', 'channel-config-leader-reason');
    leader.appendChild(reason);
  }
  if (view.draft.mode === 'team' && !view.draft.leader) {
    leader.appendChild(channelConfigHint('channelConfig.leaderRequiredHint', 'channel-config-leader-hint'));
  }

  // ④ team 模式的两条停用事实（ADR-0018：成员 auto_claim / 任务线程唤醒面）。
  if (view.draft.mode === 'team') {
    const noticesBox = el('div');
    noticesBox.setAttribute('data-od-id', 'channel-config-team-notices');
    noticesBox.appendChild(el('div', 'team-notice', t('channelConfig.teamNoticeAutoClaim')));
    noticesBox.appendChild(el('div', 'team-notice', t('channelConfig.teamNoticeWake')));
    leader.appendChild(noticesBox);
  }
  nodes.push(leader);

  // ⑤ 项目根与默认后端。
  const workspace = el('section', 'd-sec');
  workspace.appendChild(sectionTitle('channelConfig.sectionWorkspace', 'channel-config-section-workspace'));
  const rootField = el('div', 'field');
  rootField.appendChild(el('label', null, t('channelConfig.workspaceRootLabel')));
  const root = el('input', 'input');
  root.type = 'text';
  root.dataset.configWorkspaceRoot = '1';
  root.setAttribute('data-od-id', 'channel-config-workspace-root');
  root.placeholder = t('channelConfig.workspaceRootPlaceholder');
  root.value = view.draft.workspaceRoot;
  rootField.appendChild(root);
  workspace.appendChild(rootField);
  workspace.appendChild(channelConfigHint('channelConfig.workspaceRootHint', 'channel-config-workspace-root-hint'));
  const backendField = el('div', 'field');
  backendField.appendChild(el('label', null, t('channelConfig.workspaceBackend')));
  backendField.appendChild(
    channelConfigRadioRow(
      WORKSPACE_BACKENDS,
      view.draft.backend,
      'configBackend',
      'channelConfig.backend.',
      'channelConfig.backend.',
    ),
  );
  workspace.appendChild(backendField);
  nodes.push(workspace);

  // ⑥ 默认权限档（三档；不暴露 BYPASS）。
  const permission = el('section', 'd-sec');
  permission.appendChild(sectionTitle('channelConfig.sectionPermission', 'channel-config-section-permission'));
  permission.appendChild(
    channelConfigRadioRow(
      PERMISSION_MODES,
      view.draft.permission,
      'configPermission',
      'channelConfig.permission.',
      'channelConfig.permission.',
    ),
  );
  nodes.push(permission);

  // ⑦ 联网（#199）：档位三选 + provider 状态与凭据。凭据行只出现掩码 / 未配置。
  nodes.push(webAccessSection(view));

  // ⑦ 保存。失败原因写在弹窗自带的 `#channelConfigFormError` 里（重铺不会清掉它）。
  const actions = el('div', 'agent-actions');
  const save = el('button', 'btn btn-primary', t('channelConfig.save'));
  save.type = 'button';
  save.dataset.configSave = '1';
  save.setAttribute('data-od-id', 'channel-config-save');
  save.disabled = !state.online;
  actions.appendChild(save);
  nodes.push(actions);

  // ⑧ 工作区与数据面（#156 的只读读数段）：项目根 / 后端 / Docker 状态。
  // MCP 与技能**不在**这里（#273）：它们是 Agent 级能力，归口在 Agent 详情的能力段；
  // 频道级旧行仍留在库里（弃用不删），但配置面不展示、也不做「已弃用」提示（ADR-0031 §二）。
  nodes.push(workspaceManagementSection(view));

  replaceChildren(body, nodes);
}

/* ─── 能力配置（#273 / ADR-0031 §二 #270 修订）──────────────────────────────

   MCP 声明与技能**只有一个归口**：Agent 详情的能力段（同屏两个 kind，作用域恒为该成员
   `scope_kind=agent&scope_id=<member>`，没有 scope 选择器）。

   ① **频道配置只保留工作区与数据面的只读读数**（项目根 / 后端 / Docker 状态，均来自
      `view.config` 与 Agent 的 `enabled_reason`）；频道级的 MCP / 技能段已删除，旧行留在库里
      但不展示、不装配、也不做「已弃用」提示（归属由 ADR 说明）。

   两条硬边界：

   - **只出现变量名**：MCP 载荷只有 `env_names`；表单也只收变量名，值没有输入口，
     因此界面不存在「把值带进请求」的形状。
   - **配置动作不碰消息面**：增删改 / 启停 / 确认之后只重读各自的清单，不刷新消息、
     不模拟唤醒。后端本来就不落消息（#151 / #152），前端也不制造「好像发生了什么」的假象。 */

const MCP_TRANSPORTS = ['stdio', 'http', 'sse'];
const MCP_REMOTE_TRANSPORTS = ['http', 'sse'];

function emptyMcpDraft() {
  return {
    name: '',
    transport: 'stdio',
    command: '',
    args: '',
    url: '',
    envNames: '',
    enabled: true,
  };
}

/** 一个成员的能力镜像（首次打开该成员时建）；`mcpDraft` 里没有任何凭据。 */
function emptyCapabilitiesView(memberId) {
  return {
    memberId: memberId || null,
    mcpLoading: Boolean(memberId),
    mcpError: '',
    mcp: [],
    mcpDraft: emptyMcpDraft(),
    mcpEditingId: null,
    mcpConsentTimes: {},
    skillLoading: Boolean(memberId),
    skillError: '',
    skillInstalling: false,
    skills: [],
    skillSourcePath: '',
    // 「从 ~/.agents/skills 导入」区块（#277 / ADR-0036）：候选是后端只读读数，
    // `skillImportPicks` 只是**未提交**的勾选（默认 = 可导入且未安装），`skillImportResults`
    // 是逐条导入结果（留到下一次扫描）。
    skillImportLoading: false,
    skillImportError: '',
    skillImportSource: '',
    skillImportAvailable: false,
    skillImportCandidates: [],
    skillImportPicks: [],
    skillImportResults: [],
    skillImportBusy: false,
  };
}

/** 某个成员的能力镜像（按成员缓存：详情页与写路径读的是同一份）。 */
function capabilitiesView(memberId) {
  if (!memberId) return emptyCapabilitiesView(null);
  if (!state.agentCapabilities[memberId]) {
    state.agentCapabilities[memberId] = emptyCapabilitiesView(memberId);
  }
  return state.agentCapabilities[memberId];
}

/** 当前详情页正在看的能力镜像（没有活动成员时 `null`——调用方一律先判）。 */
function activeCapabilitiesView() {
  return state.activeAgentId ? capabilitiesView(state.activeAgentId) : null;
}

/** 打开 / 切换成员时丢掉上一位的草稿与弹层（清单由读路径重新填）。 */
function resetAgentCapabilities(memberId) {
  if (!memberId) return;
  state.agentCapabilities[memberId] = emptyCapabilitiesView(memberId);
}

/** 能力段的表单里有没有**未提交**的输入：有就不整段重铺（那会换掉持焦控件）。 */
function capabilityDraftDirty() {
  const view = activeCapabilitiesView();
  if (!view) return false;
  if (view.mcpEditingId) return true;
  const draft = view.mcpDraft;
  const typed = ['name', 'command', 'args', 'url', 'envNames'].some((key) => String(draft[key] || '').trim());
  return typed || Boolean(String(view.skillSourcePath || '').trim());
}

function isRemoteTransport(transport) {
  return MCP_REMOTE_TRANSPORTS.includes(transport);
}

/** 表单里的变量名：逗号 / 空白分隔，去重去空。**只收名字**。 */
function parseEnvNames(raw) {
  const names = String(raw || '')
    .split(/[\s,]+/)
    .map((name) => name.trim())
    .filter(Boolean);
  return Array.from(new Set(names));
}

/** 参数按空白分隔（与后端「逐个字符串」的 `args` 对齐；不解析引号，也没有 shell 展开）。 */
function parseArgs(raw) {
  const text = String(raw || '').trim();
  return text ? text.split(/\s+/).filter(Boolean) : [];
}

/** 一条声明的变量名读数（值永远不显示、不保存）。 */
function mcpEnvNamesText(server) {
  const names = server.env_names || [];
  return names.length ? names.join(', ') : t('mcp.envNone');
}

/** 进程内装配读数 → 一句可读状态（`runtime=null` = 本进程还没装配过）。 */
function mcpRuntimeText(server) {
  const runtime = server.runtime;
  if (!runtime) return t('mcp.runtimeUnknown');
  if (runtime.error) return t('mcp.runtimeFailed', { reason: runtime.error });
  return t('mcp.runtimeConnected', { count: (runtime.tools || []).length });
}

/** 传输方式的词条（动态取值也必须落到一个**字面**键上，词条守卫才看得见）。 */
function mcpTransportLabel(transport) {
  if (transport === 'http') return t('mcp.transport.http');
  if (transport === 'sse') return t('mcp.transport.sse');
  return t('mcp.transport.stdio');
}

/** 后端词条（同上：字面键，不做字符串拼接）。 */
function workspaceBackendLabel(backend) {
  return backend === 'docker' ? t('channelConfig.backend.docker') : t('channelConfig.backend.local');
}

function mcpDraftFrom(server) {
  return {
    name: server.name || '',
    transport: server.transport || 'stdio',
    command: server.command || '',
    args: (server.args || []).join(' '),
    url: server.url || '',
    envNames: (server.env_names || []).join(', '),
    enabled: Boolean(server.enabled),
  };
}

/** 频道内 Agent 的 availability 读数（`enabled_reason` 带 Docker 失败原因时就是不可用）。 */
function channelAgentAvailability(view) {
  const ids = new Set((view.members || []).map((member) => member.id));
  return state.agents.filter((agent) => ids.has(agent.id));
}

/** Docker 状态只陈述**后端事实**：有失败原因就报不可用；否则按当前后端说「未报告故障」，
    不做 daemon 探活，也不假装「可用」。 */
function dockerStatusOf(agents, backend) {
  const failed = (agents || []).find((agent) =>
    String(agent.enabled_reason || '').includes('Docker'),
  );
  if (failed) {
    return {
      state: 'unavailable',
      text: t('workspaceMgmt.dockerUnavailableAgent', {
        name: failed.display_name || failed.id || '',
        reason: failed.enabled_reason,
      }),
    };
  }
  if (backend === 'docker') return { state: 'reported', text: t('workspaceMgmt.dockerReported') };
  return { state: 'unused', text: t('workspaceMgmt.dockerUnused') };
}

/** 一个成员的两条能力清单 + 导入候选（并行读；各段自己的错误行，一段读不到不牵连另一段）。 */
async function loadAgentCapabilities(memberId) {
  if (!memberId) return;
  await Promise.all([
    loadAgentMcpServers(memberId),
    loadAgentSkills(memberId),
    loadSkillImportCandidates(memberId),
  ]);
}

/** 读 MCP 声明（agent scope）；失败只落在 MCP 段自己的错误行。 */
async function loadAgentMcpServers(memberId) {
  const view = capabilitiesView(memberId);
  view.mcpLoading = true;
  view.mcpError = '';
  paintAgentCapabilities();
  try {
    const payload = await api(
      '/api/mcp-servers?scope_kind=agent&scope_id=' + encodeURIComponent(memberId),
    );
    if (state.activeAgentId !== memberId) return;
    view.mcp = payload.mcp_servers || [];
  } catch (err) {
    if (state.activeAgentId !== memberId) return;
    view.mcpError = t('mcp.loadFailed', { reason: err.message });
  }
  if (state.activeAgentId !== memberId) return;
  view.mcpLoading = false;
  paintAgentCapabilities();
}

/** 读技能列表（agent scope；只含元数据与 hash，不含字节）。 */
async function loadAgentSkills(memberId) {
  const view = capabilitiesView(memberId);
  view.skillLoading = true;
  view.skillError = '';
  paintAgentCapabilities();
  try {
    const payload = await api(
      '/api/skills?scope_kind=agent&scope_id=' + encodeURIComponent(memberId),
    );
    if (state.activeAgentId !== memberId) return;
    view.skills = payload.skills || [];
  } catch (err) {
    if (state.activeAgentId !== memberId) return;
    view.skillError = t('skill.loadFailed', { reason: err.message });
  }
  if (state.activeAgentId !== memberId) return;
  view.skillLoading = false;
  paintAgentCapabilities();
}

/** 读外部 CLI 落点 ``~/.agents/skills`` 的候选（只读端点；失败只落在导入区块自己的错误行）。
    默认勾选「可导入且未安装」的项：那是这个动作的常见意图，已安装 / 不可导入的项不预选。 */
async function loadSkillImportCandidates(memberId) {
  const view = capabilitiesView(memberId);
  view.skillImportLoading = true;
  view.skillImportError = '';
  paintAgentCapabilities();
  try {
    const payload = await api(
      '/api/skills/candidates?scope_kind=agent&scope_id=' + encodeURIComponent(memberId),
    );
    if (state.activeAgentId !== memberId) return;
    view.skillImportSource = payload.source || '';
    view.skillImportAvailable = Boolean(payload.available);
    view.skillImportCandidates = payload.candidates || [];
    view.skillImportPicks = view.skillImportCandidates
      .filter((candidate) => candidate.importable && !candidate.installed)
      .map((candidate) => candidate.name);
    view.skillImportResults = [];
  } catch (err) {
    if (state.activeAgentId !== memberId) return;
    view.skillImportError = t('skill.importScanFailed', { reason: err.message });
  }
  if (state.activeAgentId !== memberId) return;
  view.skillImportLoading = false;
  paintAgentCapabilities();
}

/** 候选行的状态后缀：可导入 / 已安装 / 不可导入：<reason>（三种互斥，按优先级取一个）。 */
function skillImportStatusKey(candidate) {
  if (candidate.installed) return 'skill.importStatusInstalled';
  if (candidate.importable) return 'skill.importStatusReady';
  return 'skill.importStatusBlocked';
}

/** 一条候选是否可勾选：不可导入与已安装都渲染成禁用复选框（不许提交一个必然失败的导入）。 */
function skillImportSelectable(candidate) {
  return Boolean(candidate.importable) && !candidate.installed;
}

function toggleSkillImportPick(input) {
  const view = activeCapabilitiesView();
  if (!view) return;
  const name = input.dataset.skillImportPick;
  if (!name) return;
  const selected = Boolean(input.checked);
  const index = view.skillImportPicks.indexOf(name);
  if (selected && index < 0) view.skillImportPicks.push(name);
  if (!selected && index >= 0) view.skillImportPicks.splice(index, 1);
  view.skillImportError = '';
  // 「导入选中」的可用性跟着勾选走（一个都没勾就该禁用），因此这里重铺这一段。
  paintAgentCapabilities();
}

/** 「从 ~/.agents/skills 导入」区块：命令提示 + 刷新 + 多选 + 逐条结果。
    命令是 ASCII 常量（不放进词条值）；说明文字走词条表。 */
function skillImportForm(view) {
  const box = el('div', 'resource-add');
  box.setAttribute('data-od-id', 'skill-import');

  box.appendChild(el('div', 'd-sec-title', t('skill.importTitle')));
  const command = el('div', 'resource-command');
  const code = el('code', null, SKILL_INSTALL_COMMAND);
  code.setAttribute('data-od-id', 'skill-import-command');
  command.appendChild(code);
  box.appendChild(command);
  box.appendChild(
    resourceNote('skill.importCommandHint', 'skill-import-command-hint', 'is-faint'),
  );

  const actions = el('div', 'agent-actions resource-import-actions');
  const scan = resourceButton('skill.importScanButton', 'skillImportScan', '1', 'skill-import-scan');
  scan.disabled = scan.disabled || view.skillImportLoading || view.skillImportBusy;
  actions.appendChild(scan);
  const run = resourceButton('skill.importRunButton', 'skillImportRun', '1', 'skill-import-run');
  run.disabled = run.disabled || view.skillImportBusy || !view.skillImportPicks.length;
  actions.appendChild(run);
  box.appendChild(actions);

  if (view.skillImportLoading) {
    box.appendChild(el('div', 'resource-empty', t('skill.importLoading')));
  }
  if (view.skillImportError) {
    box.appendChild(resourceErrorNode(view.skillImportError, 'skill-import-error'));
  }
  if (!view.skillImportLoading) {
    if (view.skillImportCandidates.length) {
      const list = el('div');
      list.setAttribute('data-od-id', 'skill-import-candidates');
      view.skillImportCandidates.forEach((candidate) => {
        list.appendChild(skillImportRow(candidate, view));
      });
      box.appendChild(list);
    } else if (!view.skillImportError) {
      // 没有候选、也没有读失败：来源缺席与「来源在但还没装技能」是两种不同的空态。
      box.appendChild(
        el(
          'div',
          'resource-empty',
          view.skillImportAvailable
            ? t('skill.importEmpty')
            : t('skill.importUnavailable', { source: view.skillImportSource || '~/.agents/skills' }),
        ),
      );
    }
  }

  if (view.skillImportResults.length) {
    const results = el('div');
    results.setAttribute('data-od-id', 'skill-import-status');
    results.dataset.skillImportStatus = '1';
    view.skillImportResults.forEach((result) => {
      const line = el('div', 'resource-meta import-result', skillImportResultText(result));
      line.dataset.importStatus = result.status;
      results.appendChild(line);
    });
    box.appendChild(results);
  }
  if (view.skillImportBusy) {
    box.appendChild(el('div', 'resource-meta is-faint', t('skill.importBusy')));
  }
  return box;
}

/** 导入结果的一行：`imported` / `skipped`（409 同名已存在）/ `failed`（其余可读原因）。
    文案在**渲染时**取词条：切语言后已导入的行跟着换说法。 */
function skillImportResultText(result) {
  if (result.status === 'imported') return t('skill.importResultDone', { name: result.name });
  if (result.status === 'skipped') return t('skill.importResultSkipped', { name: result.name });
  return t('skill.importResultFailed', { name: result.name, reason: result.reason || '' });
}

/** 一个候选：复选框 + 名字 + 描述 + 状态后缀（已安装 / 不可导入的项禁用）。 */
function skillImportRow(candidate, view) {
  const row = el('div', 'resource-row');
  row.setAttribute('data-od-id', 'skill-import-row');
  row.dataset.skillImportCandidate = candidate.name;
  const main = el('div', 'resource-main');
  const title = el('div', 'resource-title');
  const label = el('label');
  const checkbox = el('input');
  checkbox.type = 'checkbox';
  checkbox.dataset.skillImportPick = candidate.name;
  checkbox.setAttribute('data-od-id', 'skill-import-pick-' + candidate.name);
  checkbox.checked = view.skillImportPicks.indexOf(candidate.name) >= 0;
  checkbox.disabled = !skillImportSelectable(candidate);
  label.appendChild(checkbox);
  label.appendChild(document.createTextNode(' ' + candidate.name));
  title.appendChild(label);
  main.appendChild(title);
  if (candidate.description) main.appendChild(el('div', 'resource-meta', candidate.description));
  const statusKey = skillImportStatusKey(candidate);
  main.appendChild(
    el(
      'div',
      'resource-meta is-faint',
      candidate.importable || candidate.installed
        ? t(statusKey)
        : t(statusKey, { reason: candidate.reason || '' }),
    ),
  );
  row.appendChild(main);
  return row;
}

function resourceNote(key, odId, className, params) {
  const node = el('div', 'resource-meta' + (className ? ' ' + className : ''), t(key, params));
  if (odId) node.setAttribute('data-od-id', odId);
  return node;
}

function resourceErrorNode(text, odId) {
  const node = el('div', 'form-error', text);
  node.setAttribute('role', 'alert');
  node.setAttribute('data-od-id', odId);
  return node;
}

function resourceButton(labelKey, datasetKey, datasetValue, odId) {
  const button = el('button', 'btn btn-sm', t(labelKey));
  button.type = 'button';
  if (datasetKey) button.dataset[datasetKey] = datasetValue;
  if (odId) button.setAttribute('data-od-id', odId);
  button.disabled = !state.online;
  return button;
}

/** 标题 + 输入控件（`.field` 那一档）；`datasetKey` 是委派用的 `data-mcp-field`。 */
function resourceTextField(labelKey, odId, datasetValue, value, placeholderKey) {
  const field = el('div', 'field');
  field.appendChild(el('label', null, t(labelKey)));
  const input = el('input', 'input');
  input.type = 'text';
  input.dataset.mcpField = datasetValue;
  input.setAttribute('data-od-id', odId);
  if (placeholderKey) input.placeholder = t(placeholderKey);
  input.value = value;
  field.appendChild(input);
  return field;
}

function resourceField(labelKey, control, odId) {
  const field = el('div', 'field');
  field.appendChild(el('label', null, t(labelKey)));
  field.appendChild(control);
  if (odId) field.setAttribute('data-od-id', odId);
  return field;
}

/* ── ① 工作区与数据面（只读状态 + 说明；写控件仍在上面的频道配置段） ─────── */

function workspaceManagementSection(view) {
  const section = el('section', 'd-sec');
  section.appendChild(
    sectionTitle('workspaceMgmt.section', 'channel-config-section-workspace-mgmt'),
  );
  const config = view.config;
  section.appendChild(
    kvRow(
      t('workspaceMgmt.root'),
      config.workspace_root || t('workspaceMgmt.rootNone'),
      'workspace-mgmt-root',
    ),
  );
  section.appendChild(
    kvRow(
      t('workspaceMgmt.backend'),
      workspaceBackendLabel(config.workspace_backend),
      'workspace-mgmt-backend',
    ),
  );
  const docker = dockerStatusOf(channelAgentAvailability(view), config.workspace_backend);
  const row = el('div', 'kv');
  row.appendChild(el('span', 'k', t('workspaceMgmt.dockerStatus')));
  const cell = el('span', 'v', docker.text);
  cell.dataset.dockerState = docker.state;
  cell.setAttribute('data-od-id', 'workspace-mgmt-docker');
  row.appendChild(cell);
  section.appendChild(row);

  const notes = el('div');
  notes.setAttribute('data-od-id', 'workspace-mgmt-notes');
  [
    'workspaceMgmt.noteTools',
    'workspaceMgmt.noteLocal',
    'workspaceMgmt.noteDocker',
    'workspaceMgmt.noteConfig',
  ].forEach((key) => notes.appendChild(resourceNote(key, null, 'is-faint')));
  section.appendChild(notes);
  return section;
}

/* ── ② MCP 声明（列表 / 增删改 / 远端确认 / 运行读数） ─────────────────── */

function mcpRow(server, view) {
  const row = el('div', 'resource-row');
  row.dataset.mcpServer = server.id;
  row.setAttribute('data-od-id', 'capability-mcp-row');
  const main = el('div', 'resource-main');
  const title = el('div', 'resource-title');
  title.appendChild(el('b', null, server.name));
  title.appendChild(el('span', 'badge', server.transport));
  title.appendChild(el('span', 'badge', t(server.enabled ? 'mcp.enabled' : 'mcp.disabled')));
  title.appendChild(el('span', 'badge', t(server.is_remote ? 'mcp.isRemote' : 'mcp.isLocal')));
  main.appendChild(title);

  const target = server.is_remote
    ? server.url
    : [server.command].concat(server.args || []).filter(Boolean).join(' ');
  if (target) main.appendChild(el('div', 'resource-meta is-faint', target));
  main.appendChild(
    el('div', 'resource-meta', t('mcp.envLabel', { names: mcpEnvNamesText(server) })),
  );

  const runtime = server.runtime;
  const runtimeText = el('div', 'resource-meta' + (runtime && runtime.error ? ' is-error' : ' is-faint'));
  runtimeText.dataset.mcpRuntime = server.id;
  runtimeText.textContent = mcpRuntimeText(server);
  main.appendChild(runtimeText);

  if (server.is_remote) {
    if (server.consented) {
      const acknowledged = view.mcpConsentTimes[server.id];
      main.appendChild(
        el(
          'div',
          'resource-meta is-faint',
          acknowledged
            ? t('mcp.consentedAt', { time: acknowledged })
            : t('mcp.consented'),
        ),
      );
    } else {
      main.appendChild(resourceNote('mcp.consentRequired', null, 'is-error'));
    }
  }

  const actions = el('div', 'resource-actions');
  actions.appendChild(
    resourceButton(
      server.enabled ? 'mcp.disable' : 'mcp.enable',
      'mcpToggle',
      server.id,
      'mcp-toggle',
    ),
  );
  if (server.consent_required && !server.consented) {
    actions.appendChild(resourceButton('mcp.consent', 'mcpConsent', server.id, 'mcp-consent'));
  }
  actions.appendChild(resourceButton('mcp.edit', 'mcpEdit', server.id, 'mcp-edit'));
  actions.appendChild(resourceButton('mcp.delete', 'mcpDelete', server.id, 'mcp-delete'));
  row.appendChild(main);
  row.appendChild(actions);
  return row;
}

function mcpForm(view) {
  const editing = view.mcpEditingId
    ? view.mcp.find((server) => server.id === view.mcpEditingId)
    : null;
  const draft = view.mcpDraft;
  const box = el('div', 'resource-add');
  box.setAttribute('data-od-id', 'mcp-form');
  box.appendChild(el('div', 'd-sec-title', t(editing ? 'mcp.editTitle' : 'mcp.addTitle')));

  const first = el('div', 'resource-form-row');
  first.appendChild(
    resourceTextField('mcp.name', 'mcp-name', 'name', draft.name, 'mcp.namePlaceholder'),
  );
  const transport = el('select', 'input');
  transport.dataset.mcpField = 'transport';
  transport.setAttribute('data-od-id', 'mcp-transport');
  MCP_TRANSPORTS.forEach((value) => {
    const option = el('option', null, mcpTransportLabel(value));
    option.value = value;
    transport.appendChild(option);
  });
  transport.value = draft.transport;
  first.appendChild(resourceField('mcp.transport', transport, 'mcp-transport-field'));
  box.appendChild(first);

  const second = el('div', 'resource-form-row');
  if (isRemoteTransport(draft.transport)) {
    second.appendChild(resourceTextField('mcp.url', 'mcp-url', 'url', draft.url, 'mcp.urlPlaceholder'));
  } else {
    second.appendChild(
      resourceTextField('mcp.command', 'mcp-command', 'command', draft.command, 'mcp.commandPlaceholder'),
    );
    second.appendChild(
      resourceTextField('mcp.args', 'mcp-args', 'args', draft.args, 'mcp.argsPlaceholder'),
    );
  }
  box.appendChild(second);

  const third = el('div', 'resource-form-row');
  third.appendChild(
    resourceTextField(
      'mcp.envNames',
      'mcp-env-names',
      'envNames',
      draft.envNames,
      'mcp.envNamesPlaceholder',
    ),
  );
  const enabledField = el('div', 'field');
  const enabledLabel = el('label');
  const enabled = el('input');
  enabled.type = 'checkbox';
  enabled.dataset.mcpEnabled = '1';
  enabled.setAttribute('data-od-id', 'mcp-enabled');
  enabled.checked = Boolean(draft.enabled);
  enabledLabel.appendChild(enabled);
  enabledLabel.appendChild(document.createTextNode(' ' + t('mcp.enabledLabel')));
  enabledField.appendChild(enabledLabel);
  third.appendChild(enabledField);
  box.appendChild(third);
  box.appendChild(resourceNote('mcp.envValuesOmitted', 'mcp-env-note', 'is-faint'));

  const actions = el('div', 'agent-actions');
  actions.appendChild(
    resourceButton(editing ? 'mcp.save' : 'mcp.add', 'mcpSave', '1', 'mcp-save'),
  );
  if (editing) {
    actions.appendChild(resourceButton('mcp.cancel', 'mcpCancelEdit', '1', 'mcp-cancel-edit'));
  }
  box.appendChild(actions);
  return box;
}

/* ── 能力段的读路径（两个 kind 各在自己的页签里，作用域恒为该成员） ────────── */

/** 重铺能力相关的那两个页签：读路径与**显式动作**（编辑 / 安装 / 复制 / 启停 / 删除）都走它。

    轮次状态这类后台重绘走 `renderAgentDetail()`，那一支在表单脏时不碰它们（见那里的注释）——
    后台重绘不该把持焦控件换成新节点。 */
function paintAgentCapabilities() {
  if (!state.activeAgentId) return;
  const agent = agentById(state.activeAgentId);
  if (!agent) return;
  renderAgentTabPanel('skills', agentSkillsTabNodes(agent));
  renderAgentTabPanel('mcp', agentMcpTabNodes(agent));
}

/** 草稿脏时 `renderAgentDetail()` 只换这两块（读数是后端事实，换了就得跟着换）。 */
function refreshAgentCapabilitiesPanels() {
  paintAgentCapabilities();
}

/* ── ③ 技能（列表 / 本机目录 / zip / 启停 / 删除） ─────────────────────── */

function skillRow(skill) {
  const row = el('div', 'resource-row');
  row.dataset.skill = skill.id;
  row.setAttribute('data-od-id', 'capability-skill-row');
  const main = el('div', 'resource-main');
  const title = el('div', 'resource-title');
  title.appendChild(el('b', null, skill.name));
  title.appendChild(el('span', 'badge', t(skill.enabled ? 'skill.enabled' : 'skill.disabled')));
  main.appendChild(title);
  if (skill.description) main.appendChild(el('div', 'resource-meta', skill.description));
  const hash = el('div', 'resource-meta is-faint');
  hash.appendChild(el('span', null, t('skill.hashLabel') + ' '));
  const short = el('span', 'resource-hash', String(skill.content_hash || '').slice(0, 12));
  short.setAttribute('title', t('skill.hashTitle', { hash: skill.content_hash || '' }));
  hash.appendChild(short);
  main.appendChild(hash);

  const actions = el('div', 'resource-actions');
  actions.appendChild(
    resourceButton(
      skill.enabled ? 'skill.disable' : 'skill.enable',
      'skillToggle',
      skill.id,
      'skill-toggle',
    ),
  );
  actions.appendChild(resourceButton('skill.delete', 'skillDelete', skill.id, 'skill-delete'));
  row.appendChild(main);
  row.appendChild(actions);
  return row;
}

/** 安装表单：本机目录 + zip 两条既有路径（元数据进正文 / query，字节走正文）。 */
function skillInstallForm(view) {
  const install = el('div', 'resource-add');
  install.setAttribute('data-od-id', 'skill-install');
  install.appendChild(el('div', 'd-sec-title', t('skill.installTitle')));

  const dirRow = el('div', 'resource-form-row');
  const dirField = el('div', 'field');
  dirField.appendChild(el('label', null, t('skill.installDir')));
  const dir = el('input', 'input');
  dir.type = 'text';
  dir.dataset.skillSource = '1';
  dir.setAttribute('data-od-id', 'skill-source');
  dir.placeholder = t('skill.installDirPlaceholder');
  dir.value = view.skillSourcePath;
  dirField.appendChild(dir);
  dirRow.appendChild(dirField);
  const dirActions = el('div', 'agent-actions');
  dirActions.appendChild(resourceButton('skill.installDirButton', 'skillInstallDir', '1', 'skill-install-dir'));
  dirRow.appendChild(dirActions);
  install.appendChild(dirRow);
  install.appendChild(resourceNote('skill.installDirHint', 'skill-install-dir-hint', 'is-faint'));

  const zipRow = el('div', 'resource-form-row');
  const zipField = el('div', 'field');
  zipField.appendChild(el('label', null, t('skill.installZip')));
  const zip = el('input', 'input');
  zip.type = 'file';
  zip.accept = '.zip,application/zip';
  zip.dataset.skillZip = '1';
  zip.setAttribute('data-od-id', 'skill-zip');
  zipField.appendChild(zip);
  zipRow.appendChild(zipField);
  install.appendChild(zipRow);
  install.appendChild(resourceNote('skill.installZipHint', 'skill-install-zip-hint', 'is-faint'));
  if (view.skillInstalling) {
    install.appendChild(el('div', 'resource-meta is-faint', t('skill.installing')));
  }
  return install;
}

/* ── 写路径（全部是配置动作：只重读各自的清单，不碰消息面） ─────────────── */

/** 写路径的公共前置：当前详情页锁定的成员就是这次写的目标（切走了就什么都不写）。 */
function capabilityTarget() {
  const view = activeCapabilitiesView();
  if (!view || !view.memberId || !state.online) return null;
  return view;
}

function resetMcpDraft(view) {
  view.mcpDraft = emptyMcpDraft();
  view.mcpEditingId = null;
}

function beginMcpEdit(serverId) {
  const view = activeCapabilitiesView();
  if (!view) return;
  const server = view.mcp.find((item) => item.id === serverId);
  if (!server) return;
  view.mcpEditingId = server.id;
  view.mcpDraft = mcpDraftFrom(server);
  view.mcpError = '';
  paintAgentCapabilities();
}

function cancelMcpEdit() {
  const view = activeCapabilitiesView();
  if (!view) return;
  view.mcpEditingId = null;
  view.mcpDraft = emptyMcpDraft();
  view.mcpError = '';
  paintAgentCapabilities();
}

/** 表单字段只在**未提交**的镜像上改；transport 切换会重铺（command / url 二选一）。 */
function updateMcpDraftField(field) {
  const view = activeCapabilitiesView();
  if (!view) return;
  const key = field.dataset.mcpField;
  if (!key || !Object.prototype.hasOwnProperty.call(view.mcpDraft, key)) return;
  view.mcpDraft[key] = field.value;
  if (key === 'transport') paintAgentCapabilities();
}

/** 表单 → 请求体（远端只留 url，本机只留 command / args；变量值从来没有输入口）。 */
function mcpFieldsFromDraft(draft) {
  const remote = isRemoteTransport(draft.transport);
  return {
    name: String(draft.name || '').trim(),
    transport: draft.transport,
    command: remote ? '' : String(draft.command || '').trim(),
    args: remote ? [] : parseArgs(draft.args),
    url: remote ? String(draft.url || '').trim() : '',
    env_names: parseEnvNames(draft.envNames),
    enabled: Boolean(draft.enabled),
  };
}

async function submitMcpDraft() {
  const view = capabilityTarget();
  if (!view) return;
  const memberId = view.memberId;
  const fields = mcpFieldsFromDraft(view.mcpDraft);
  const editingId = view.mcpEditingId;
  view.mcpError = '';
  try {
    if (editingId) {
      await api('/api/mcp-servers/' + encodeURIComponent(editingId), { method: 'PATCH', body: fields });
    } else {
      await api('/api/mcp-servers', {
        method: 'POST',
        body: Object.assign({ scope_kind: 'agent', scope_id: memberId }, fields),
      });
    }
    if (state.activeAgentId !== memberId) return;
    resetMcpDraft(view);
    toast(t(editingId ? 'mcp.updated' : 'mcp.created'));
    await loadAgentMcpServers(memberId);
  } catch (err) {
    if (state.activeAgentId !== memberId) return;
    view.mcpError = t('mcp.saveFailed', { reason: err.message });
    paintAgentCapabilities();
  }
}

async function toggleMcpServer(serverId) {
  const view = capabilityTarget();
  if (!view) return;
  const server = view.mcp.find((item) => item.id === serverId);
  if (!server) return;
  const memberId = view.memberId;
  view.mcpError = '';
  try {
    await api('/api/mcp-servers/' + encodeURIComponent(serverId), {
      method: 'PATCH',
      body: { enabled: !server.enabled },
    });
    if (state.activeAgentId !== memberId) return;
    await loadAgentMcpServers(memberId);
  } catch (err) {
    if (state.activeAgentId !== memberId) return;
    view.mcpError = t('mcp.saveFailed', { reason: err.message });
    paintAgentCapabilities();
  }
}

async function deleteMcpServer(serverId) {
  const view = capabilityTarget();
  if (!view) return;
  const memberId = view.memberId;
  view.mcpError = '';
  try {
    await api('/api/mcp-servers/' + encodeURIComponent(serverId), { method: 'DELETE' });
    if (state.activeAgentId !== memberId) return;
    if (view.mcpEditingId === serverId) resetMcpDraft(view);
    toast(t('mcp.deleted'));
    await loadAgentMcpServers(memberId);
  } catch (err) {
    if (state.activeAgentId !== memberId) return;
    view.mcpError = t('mcp.deleteFailed', { reason: err.message });
    paintAgentCapabilities();
  }
}

/** 远端 MCP 的数据面确认（**幂等**）：后端重复调用回同一个 `acknowledged_at`。 */
async function consentMcpServer(serverId) {
  const view = capabilityTarget();
  if (!view) return;
  const memberId = view.memberId;
  view.mcpError = '';
  try {
    const payload = await api(
      '/api/mcp-servers/' + encodeURIComponent(serverId) + '/consent',
      { method: 'POST' },
    );
    if (state.activeAgentId !== memberId) return;
    const acknowledged = (payload.consent || {}).acknowledged_at || '';
    if (acknowledged) view.mcpConsentTimes[serverId] = acknowledged;
    toast(t('mcp.consentDone'));
    await loadAgentMcpServers(memberId);
  } catch (err) {
    if (state.activeAgentId !== memberId) return;
    view.mcpError = t('mcp.consentFailed', { reason: err.message });
    paintAgentCapabilities();
  }
}

async function submitSkillDirectory() {
  const view = capabilityTarget();
  if (!view) return;
  const memberId = view.memberId;
  const sourcePath = String(view.skillSourcePath || '').trim();
  if (!sourcePath) {
    view.skillError = t('skill.sourceRequired');
    paintAgentCapabilities();
    return;
  }
  view.skillError = '';
  view.skillInstalling = true;
  paintAgentCapabilities();
  try {
    const payload = await api('/api/skills', {
      method: 'POST',
      body: { scope_kind: 'agent', scope_id: memberId, source_path: sourcePath },
    });
    if (state.activeAgentId !== memberId) return;
    view.skillSourcePath = '';
    toast(t('skill.installed', { name: (payload.skill || {}).name || '' }));
    await loadAgentSkills(memberId);
  } catch (err) {
    if (state.activeAgentId !== memberId) return;
    view.skillError = t('skill.installFailed', { reason: err.message });
  }
  if (state.activeAgentId !== memberId) return;
  view.skillInstalling = false;
  paintAgentCapabilities();
}

/** zip 安装：原始字节走正文、元数据走 query（与后端契约一致，不用 multipart）。 */
async function uploadSkillZip(files) {
  const file = files && files[0];
  const view = capabilityTarget();
  if (!file || !view) return;
  const memberId = view.memberId;
  view.skillError = '';
  view.skillInstalling = true;
  paintAgentCapabilities();
  try {
    const query =
      '?scope_kind=agent&scope_id=' +
      encodeURIComponent(memberId) +
      '&filename=' +
      encodeURIComponent(file.name || 'upload.zip');
    const response = await fetch('/api/skills' + query, {
      method: 'POST',
      headers: { 'Content-Type': 'application/zip' },
      body: file,
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const error = new Error(readError(payload, response));
      error.status = response.status;
      throw error;
    }
    if (state.activeAgentId !== memberId) return;
    toast(t('skill.installed', { name: (payload.skill || {}).name || '' }));
    await loadAgentSkills(memberId);
  } catch (err) {
    if (state.activeAgentId !== memberId) return;
    view.skillError = t('skill.installFailed', { reason: err.message });
  }
  if (state.activeAgentId !== memberId) return;
  view.skillInstalling = false;
  paintAgentCapabilities();
}

/** 逐条导入选中项：写路径**复用** `POST /api/skills`（`source_path` = 候选 path）。
    `409` 记「已存在，已跳过」（不覆盖）；其他失败记可读原因；全部结束后重读技能列表。 */
async function submitSkillImport() {
  const view = capabilityTarget();
  if (!view || view.skillImportBusy) return;
  const memberId = view.memberId;
  const picked = view.skillImportCandidates.filter(
    (candidate) =>
      view.skillImportPicks.indexOf(candidate.name) >= 0 && skillImportSelectable(candidate),
  );
  if (!picked.length) {
    view.skillImportError = t('skill.importPickRequired');
    paintAgentCapabilities();
    return;
  }
  view.skillImportError = '';
  view.skillImportBusy = true;
  view.skillImportResults = [];
  paintAgentCapabilities();
  for (const candidate of picked) {
    try {
      await api('/api/skills', {
        method: 'POST',
        body: { scope_kind: 'agent', scope_id: memberId, source_path: candidate.path },
      });
      view.skillImportResults.push({ name: candidate.name, status: 'imported', reason: '' });
    } catch (err) {
      view.skillImportResults.push({
        name: candidate.name,
        status: err.status === 409 ? 'skipped' : 'failed',
        reason: err.message,
      });
    }
  }
  if (state.activeAgentId !== memberId) return;
  view.skillImportBusy = false;
  // 导入成功的名字从此「已安装」：清掉勾选，下一次扫描会把它们渲染成禁用的「已安装」。
  const imported = new Set(
    view.skillImportResults.filter((result) => result.status !== 'failed').map((result) => result.name),
  );
  view.skillImportPicks = view.skillImportPicks.filter((name) => !imported.has(name));
  const count = (status) => view.skillImportResults.filter((item) => item.status === status).length;
  toast(
    t('skill.importSummary', {
      imported: count('imported'),
      skipped: count('skipped'),
      failed: count('failed'),
    }),
  );
  await loadAgentSkills(memberId);
  if (state.activeAgentId !== memberId) return;
  paintAgentCapabilities();
}

async function toggleSkill(skillId) {
  const view = capabilityTarget();
  if (!view) return;
  const skill = view.skills.find((item) => item.id === skillId);
  if (!skill) return;
  const memberId = view.memberId;
  view.skillError = '';
  try {
    await api('/api/skills/' + encodeURIComponent(skillId), {
      method: 'PATCH',
      body: { enabled: !skill.enabled },
    });
    if (state.activeAgentId !== memberId) return;
    await loadAgentSkills(memberId);
  } catch (err) {
    if (state.activeAgentId !== memberId) return;
    view.skillError = t('skill.toggleFailed', { reason: err.message });
    paintAgentCapabilities();
  }
}

async function deleteSkill(skillId) {
  const view = capabilityTarget();
  if (!view) return;
  const memberId = view.memberId;
  view.skillError = '';
  try {
    await api('/api/skills/' + encodeURIComponent(skillId), { method: 'DELETE' });
    if (state.activeAgentId !== memberId) return;
    toast(t('skill.deleted'));
    await loadAgentSkills(memberId);
  } catch (err) {
    if (state.activeAgentId !== memberId) return;
    view.skillError = t('skill.deleteFailed', { reason: err.message });
    paintAgentCapabilities();
  }
}

/** 保存：提交全部可写字段（`team_scope` 不在可写面上）；成功按响应重铺，失败只报可读原因。 */
async function submitChannelConfig() {
  const view = state.channelConfig;
  const channelId = view.channelId;
  if (!channelId || !view.config) return;
  const box = $('#channelConfigFormError');
  if (box) box.hidden = true;
  const root = String(view.draft.workspaceRoot || '').trim();
  const body = {
    mode: view.draft.mode,
    workspace_root: root || null,
    workspace_backend: view.draft.backend,
    permission_mode: view.draft.permission,
    web_access: view.draft.webAccess,
  };
  if (view.draft.leader) body.team_leader_member_id = view.draft.leader;
  try {
    const payload = await api(channelPath(channelId, '/config'), { method: 'PATCH', body });
    if (state.channelConfig.channelId !== channelId) return;
    view.config = payload.config;
    cacheChannelConfig(channelId, payload.config);
    view.draft = channelConfigDraftFrom(payload.config);
    view.error = '';
    view.loading = false;
    renderChannelConfigModal();
    toast(t('channelConfig.saved'));
  } catch (err) {
    if (state.channelConfig.channelId !== channelId) return;
    if (box) {
      box.textContent = t('channelConfig.saveFailed', { reason: err.message });
      box.hidden = false;
    }
    renderChannelConfigModal();
  }
}


function renderHeader() {
  const target = activeTarget();
  const hasTarget = Boolean(target);
  syncChromeVisibility(state.mode);
  if (!hasTarget) {
    closeMentionPicker();
    syncComposerLock(false);
    return;
  }
  const isDm = target.kind === 'dm';
  const isArchivedDm = isDm && dmIsArchived(target);
  const name = isDm ? target.peer.display_name : target.name;
  // 名册是**频道**成员的只读全名单（`CONTEXT.md` **成员名册**）：DM 下没有这条面，
  // 入口随之隐藏——不留一个按下去什么都不发生的按钮。
  $('#btnChannelMembers').hidden = isDm;
  // 回应预算同样是**频道**配置（库内逐频道项，ADR-0007）：DM / 任务型 target 没有这条设置面，
  // 入口随之隐藏（预算读数的路径 `/api/channels/{id}/budget` 只认频道）。
  $('#btnChannelBudget').hidden = isDm;
  // 频道设置（#154：模式 / 队长 / 项目根 / 后端 / 权限档）同样是**频道**配置
  // （ADR-0018 / ADR-0021）：DM 没有这条面，入口随之隐藏。
  $('#btnChannelSettings').hidden = isDm;
  // 运行面板（#155）属于协作运行的频道 / 任务线程：私信没有 team 运行，入口随之隐藏。
  $('#btnRunPanel').hidden = isDm;
  // 详情面板的开关同理：私信下没有成员面板（任务线程 / 运行面板开着时 dock 仍可用），
  // 没有可显示的形态就不留一个按下去什么都不发生的按钮。
  $('#btnDockToggle').hidden = !dockAvailable();
  // DM 的标题带 `@` 前缀（原型 `renderHeader()` 的 DM 分支写的就是 `'@' + m.name`）。
  $('#chanHash').hidden = isDm;
  $('#chanTitle').textContent = isDm ? '@' + name : name;
  // 标题右侧的**协作模式 pill**（#178）：自主 / 队长制 · 队长名。它只读缓存（不在这里发请求），
  // 私信下与「配置还没读回」时隐藏；落点在 `.chan-title` 之外，不被标题的 ellipsis 截断。
  renderChannelModePill();
  // desc 保持现状的写法：原型那句「懒创建会话」与 `CONTEXT.md` **私信**词条的
  // `_Avoid_`（明确否定「懒创建」）冲突，是原型的过时文案，不照抄。
  // 已归档私信（peer 软删除）不留可写错觉：desc 与 placeholder 都说明「只读归档」。
  $('#chanDesc').textContent = isArchivedDm
    ? t('header.dmArchivedDesc')
    : isDm ? t('header.dmDesc', { name }) : target.description || '';
  $('#composerInput').placeholder = isArchivedDm
    ? t('header.dmArchivedPlaceholder')
    : isDm
      ? t('header.dmPlaceholder', { name })
      : t('header.channelPlaceholder', { name });
  syncComposerLock(isArchivedDm);
}

/** 移动端顶部栏的标题（≤900px）：**当前 target 名**，形态跟频道头一致 ——
    频道是 `#名`、私信是 `@名`（原型 `alibot-app.html:1647,1655` 对 `#mtTitle` 的两处赋值）。
    `>900px` 时顶栏 `display:none`，这次赋值没有可见效果 —— 但它与断点无关地保持正确，
    窗口宽度在两边来回时不会留下一条旧标题。没有当前 target 时退回品牌名：
    顶栏是常显件，不留一条空标题。（`alibot` 是本页的品牌名，`index.html` 的品牌块同样
    直接写它、不进词条表。） */
function renderMobileTopbar() {
  const title = $('#mtTitle');
  if (!title) return;
  const target = activeTarget();
  if (!target) {
    title.textContent = 'alibot';
    return;
  }
  title.textContent = target.kind === 'dm' ? '@' + target.peer.display_name : '#' + target.name;
}

function renderConnection() {
  $('#connBanner').hidden = state.online;
  $('#composer').classList.toggle('is-disabled', !state.online);
  const stream = $('#streamState');
  if (stream) {
    const reconnecting = state.online && state.streamState !== 'open';
    stream.hidden = !reconnecting;
    stream.textContent = reconnecting ? t('conn.reconnecting') : '';
  }
  const taskInput = $('#taskComposerInput');
  const taskSend = $('#taskSend');
  if (taskInput && taskSend) taskSend.disabled = !state.online || !taskInput.value.trim();
}

/** 铺消息流。

    `options.approvals === false` 表示调用方**已经**铺过频道审批板（`renderApprovalSurfaces()`
    在调 `syncApprovalBadges()` 之前就铺了），因此这一轮不再重复重建那一块——#217 的重铺出口
    复用它时带上这个开关，审批板在同一拍里只被铺一次（否则正在点的审批按钮会被换掉）。
    其余调用方不传，行为逐字不变。 */
function renderMessages(options) {
  const target = activeTarget();
  const targetMode = state.mode === 'channel';
  $('#emptyNoChannel').hidden = !targetMode || Boolean(target);
  const messages = targetMode && target ? renderedMessages() : [];
  // 审批卡不是消息：它不占 seq / 未读，也不进搜索；但「流里只有一张审批卡」时
  // 不该同时留着「该频道还没有消息」的空态。
  const approvalTargetId = state.activeChannelId;
  const approvals = targetMode && target && approvalTargetId ? approvalRecordsFor(approvalTargetId) : [];
  $('#emptyNoMessage').hidden = !target || messages.length > 0 || approvals.length > 0;

  const nodes = [];
  let lastDay = null;
  messages.forEach((message) => {
    const day = dayLabel(message.created_at);
    if (day !== lastDay) {
      const separator = el('div', 'day-sep');
      separator.appendChild(el('span', null, day));
      nodes.push(separator);
      lastDay = day;
    }
    nodes.push(messageNode(message));
    // 轮次结果挂在**触发它的那条消息**下面（按 base_seq 绑定），不是与消息无关的横幅。
    // 同一条消息下**至多**两块：一块默认折叠的摘要行 + 每个 `failed` 各一块（直接展开）。
    if (message.seq) {
      const byMember = state.outcomes[message.seq];
      if (byMember) {
        const records = Object.keys(byMember).flatMap((memberId) => byMember[memberId]);
        const summary = roundOutcomeSummaryNode(records);
        if (summary) nodes.push(summary);
        records
          .filter((record) => record.status === 'failed')
          .forEach((record) => nodes.push(roundOutcomeNode(record)));
        nodes.push(...finishedOutcomeNodes(records));
      }
    }
  });
  replaceChildren($('#messageList'), nodes);
  if (!(options && options.approvals === false)) renderChannelApprovals(approvals, approvalTargetId);
  // 页签计数跟着消息面重铺一起刷新（计数本身读 target 的未读数，见 `renderTabs()`）。
  renderTabs();
}

function finishedOutcomeNodes(records) {
  return records
    .filter((record) => record.status === 'replied' &&
      ['interrupted', 'exceed_max_iters'].includes(record.finished_reason))
    .map(roundOutcomeSummaryLine);
}

/** 摘要行要覆盖的四种轮次结果（`failed` 直接展开，`replied` 由它自己的回复消息承载）。 */
function summarizableOutcomes(records) {
  return records.filter((record) => SUMMARY_STATUSES.includes(record.status));
}

/**
 * 把同一条消息（同一 `base_seq`）下的**非 `failed`** 轮次结果收成**一行**可折叠摘要（或零行）。
 *
 * 为什么收成一行：`ignored` 在当前唤醒面（频道内全部 agent 成员）下会按成员各有一条，
 * 那是 append-only 的取证面、**不得**在后端过滤掉；但逐成员铺在消息流里会淹没对话。
 * 因此只在**呈现**层合并：默认折叠（一行说清「谁没有回应、为什么是正常选择」），
 * 展开后才逐成员给出 `status` 中文说法 + 后端 `reason` 原文。
 *
 * 它**不是消息**：不占 `seq`（刻意用 `data-base-seq`）、没有头像、不参与「数几条消息」的统计。
 */
function roundOutcomeSummaryNode(records) {
  const items = summarizableOutcomes(records);
  if (!items.length) return null;
  const baseSeq = items[0].base_seq;
  const expanded = Boolean(state.expandedOutcomes[baseSeq]);

  const row = el('article', 'msg outcome-summary');
  row.dataset.baseSeq = baseSeq;
  // 与失败行同一个理由：这一行不是一条消息，不能被数进「几条消息」。
  row.setAttribute('role', 'status');
  row.setAttribute('aria-label', t('outcome.summaryAria', { names: summarizeNames(items), count: items.length }));

  const body = el('div', 'msg-body');
  const head = el('div', 'msg-head');

  // 折叠控件是**原生 `<button>`**：它天然可 Tab 聚焦、可 Enter/Space 激活，且 `aria-expanded`
  // 让展开态对屏幕阅读器可感知。不复用 `msg-outcome-act`（那是失败行里的「看成员状态」，
  // 动词开头的一条动作链接），它是另一件事。
  const toggle = el('button', 'msg-outcome-summary-toggle');
  toggle.dataset.summaryToggle = String(baseSeq);
  toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  toggle.setAttribute('aria-controls', 'outcome-summary-' + baseSeq);
  toggle.appendChild(el('span', 'msg-outcome-summary-mark', expanded ? '\u25be' : '\u25b8'));
  toggle.appendChild(el('span', 'msg-outcome-summary-names', summarizeNames(items)));
  toggle.appendChild(el('span', 'msg-outcome-summary-text', expanded ? t('outcome.collapse') : t('outcome.expand')));
  head.appendChild(toggle);
  head.appendChild(el('span', 'msg-time', timeLabel(items[0].finished_at)));
  body.appendChild(head);

  // 折叠态：一行说清「为什么」——四态的**后端事实不同，说法就不同**，不得混为一谈。
  // 只有一种状态时不重复上一行的名单（名单已经在折叠控件里）；混合状态时才逐组点名，
  // 否则读不出「是谁因为哪条原因」。
  body.appendChild(el('div', 'msg-outcome-summary-why', summarizeWhy(items)));

  // 逐成员明细**恒常渲染**、用 `hidden` 切可见性（而不是折叠时不存在）：
  // `aria-controls` 指向的 id 必须在任何时刻都能解析到，否则折叠态的 `aria-controls`
  // 就是一个悬空引用（无障碍与被自动化取证都会踩到）。
  const details = el('div', 'msg-outcome-details');
  details.id = 'outcome-summary-' + baseSeq;
  details.hidden = !expanded;
  items.forEach((record) => details.appendChild(roundOutcomeSummaryLine(record)));
  body.appendChild(details);

  row.appendChild(body);
  return row;
}

/** 摘要行的成员名单：按后端返回序（`finished_at, id` 升序）逐个列出，超过 2 人写成「等 N 人」。 */
function summarizeNames(items) {
  return joinNames(items.map((record) => outcomeMemberName(record.member_id)));
}

/** 名单的**唯一**拼法：≤2 个逐个列出，>2 个写「甲、乙 等 N 人」（摘要行与折叠说明共用）。 */
function joinNames(names) {
  if (names.length <= 2) return names.join(t('common.nameListSep'));
  const head = names[0] + t('common.nameListSep') + names[1];
  return t('common.nameListMore', { names: head, count: names.length });
}

/**
 * 折叠行那一句「为什么」：把成员按 status 归组，每组用自己的后端事实说法（不把三态混为一谈）。
 *
 * 例：「阿墨、云舟：自判忽略（正常的协议选择，不是失败）；风间：预算已用尽，本轮零投递……」。
 */
function summarizeWhy(items) {
  const order = [];
  const groups = new Map();
  items.forEach((record) => {
    // 按 status 归组（不是按 label）：同一状态的成员聚在一起，各自用自己的后端事实说法。
    if (!groups.has(record.status)) {
      groups.set(record.status, []);
      order.push(record.status);
    }
    groups.get(record.status).push(outcomeMemberName(record.member_id));
  });
  const mixed = order.length > 1;
  return order
    .map((status) => {
      const text = outcomeNote(status);
      // 单一状态：名单已在折叠控件上，这里只陈述「为什么」（不重复一遍名字）。
      if (!mixed) return text;
      return joinNames(groups.get(status)) + t('common.colonSep') + text;
    })
    .join(t('common.clauseSep'));
}

/**
 * 展开后的一行：一个成员的 `status` 中文说法 + 后端给出的 `reason` **原文**。
 *
 * `reason` 已是可读中文，因此直接呈现——不重新措辞、不截断成省略号（票面硬要求）。
 * 成员名与状态词用 `--faint`/`--muted` 一档：这四态都**不是失败**，不得使用 `--error`。
 */
function roundOutcomeSummaryLine(record) {
  const line = el('div', 'msg-outcome-line');
  line.dataset.outcome = record.id;
  line.dataset.member = record.member_id;
  line.dataset.status = record.status;
  const head = el('div', 'msg-outcome-line-head');
  head.appendChild(el('span', 'msg-outcome-line-author', outcomeMemberName(record.member_id)));
  head.appendChild(el('span', 'msg-outcome-line-state', outcomeLabel(record.status)));
  head.appendChild(el('span', 'msg-time', timeLabel(record.finished_at)));
  line.appendChild(head);
  const reason = el('div', 'msg-outcome-line-reason', record.reason || t('outcome.lineReasonEmpty'));
  // 与失败行的取证 id 同一前缀（`round-reason-<id>`）：同一字段两个前缀只会让取证混淆。
  reason.dataset.odId = 'round-reason-' + record.id;
  line.appendChild(reason);
  return line;
}

/** 成员显示名：拿不到成员表时退回 id（与失败行同一个纪律，不伪造名字）。 */
function outcomeMemberName(memberId) {
  const member = state.members.find((item) => item.id === memberId);
  return member ? member.display_name : memberId;
}

/**
 * 把**一条** `failed` 轮次结果渲染成消息流里直接展开的一块（非 `failed` 返回 `null`）。
 *
 * 失败是用户主诉的缺口（含「被点名却不回应」），因此它**不藏在折叠后面**：既有的呈现
 * （「未回应」tag、`这一轮失败`/`没启用` 状态词、`看成员状态`、reason 正文）逐字保留，
 * 只把它并入与摘要行同一个消息锚点（同一 `base_seq`）下。三者由 `renderMessages()` 调度：
 * 摘要行在前、failed 块在后（失败文本更重，放最后离触发消息更近）。
 */
function roundOutcomeNode(record) {
  if (!record || record.status !== 'failed') return null;
  const member = state.members.find((item) => item.id === record.member_id);
  const agent = state.agents.find((item) => item.id === record.member_id);
  // 状态未知时**不声明**任何成员状态：宁可不提，也不能把「我不知道」说成「未启用」。
  const status = agent ? statusOf(agent) : null;
  const disabled = status === 'offline';
  const name = member ? member.display_name : record.member_id;

  const row = el('article', 'msg outcome');
  row.dataset.outcome = record.id;
  row.dataset.member = record.member_id;
  // 刻意**不**用 `data-seq`：那个属性在本文件里一直是「这一行是 seq=N 的消息」的意思。
  // 轮次结果不是消息，它只是被那条消息触发，所以用不同的名字。
  row.dataset.baseSeq = record.base_seq;
  // 这一行不是消息（不占 seq、不是作者说的话），因此标记成 status 而不是 message：
  // 屏幕阅读器与自动化取证都不该把它当成「又一条消息」。
  row.setAttribute('role', 'status');
  row.setAttribute('aria-label', t('outcome.failedAria', { name, reason: record.reason || t('outcome.lineReasonEmpty') }));

  const body = el('div', 'msg-body');
  const head = el('div', 'msg-head');
  // 作者名用**自己的**类名而不是 `is-agent`：这一行不是一条 Agent 消息，
  // 任何「数几条 Agent 消息」的统计都不该把它数进去。
  head.appendChild(el('span', 'msg-author msg-outcome-author', name));
  head.appendChild(el('span', 'msg-tag', t('outcome.tag')));
  if (status) head.appendChild(el('span', 'msg-outcome-state', disabled ? t('outcome.stateDisabled') : t('outcome.stateFailed')));
  if (!disabled && agent) {
    // 从失败现场直接打开成员详情，不用人先找到右上角那个图标。
    // 成员 id 由按钮**自己**携带：点击委托据此打开这一个成员（不写死成员、不按名字匹配）。
    const inspect = el('button', 'msg-outcome-act', t('outcome.inspect'));
    inspect.dataset.inspectMember = record.member_id;
    head.appendChild(inspect);
  }
  head.appendChild(el('span', 'msg-time', timeLabel(record.finished_at)));
  body.appendChild(head);

  // 「未启用」是成员**此刻**的状态，「这一轮失败」是**库里那一次**轮次的结论。
  // 两者会同时为真，因此分开陈述，而不是把后者说成前者、也不把前者冒充后者。
  if (disabled) {
    body.appendChild(el('div', 'msg-outcome-note', t('outcome.disabledNote', { name })));
  }
  const reason = el('div', 'msg-outcome-reason', record.reason || t('outcome.lineReasonEmpty'));
  reason.dataset.odId = 'round-reason-' + record.id;
  body.appendChild(reason);

  row.appendChild(body);
  return row;
}

/* ─── 旁白式交代的**呈现降档**（#57；#35 的 D1 判据-4 + D4 形态-a）───────────

   旁白式交代 = Agent 把「我不需要说话」当成正文说出来（「（本轮消息与我无关，无需回应）」）。
   它**仍是**一条真实 Agent 消息：占自己的 `seq`、计未读、照旧唤醒与花预算——这些数据面后果按
   #35 登记为**已知代价**，本票不改，也不在这里补。本票只把它的**呈现**降一档。

   判据是**形态**，不是措辞表：整条正文被**同一对圆括号**从头包到尾、只有一行、而且很短。
   因此这里既没有正则词表、也没有「无需回应」这类字样清单——换措辞、换语言都不会让判据失效。
   **失效方向可放松**：判据拿不准（多行、超长、中途就闭合、方括号/书名号、不是 Agent 发的）时
   一律按普通消息呈现，宁可少降档，不可误吞真实回复。

   长度上限取 24 个字符：整条括注式交代是一句自我说明（「（本轮消息与我无关，无需回应）」是 15 个
   字符），超过这个长度的括注更可能是真的在说一件事（引用、长句说明），按真实回复对待。 */
const NARRATION_MAX_LENGTH = 24;
/** 成对的圆括号：只认圆括号，不认方括号 / 书名号（那些是标题式强调，可能就是真实回复）。 */
const NARRATION_PAREN_PAIRS = { '\uFF08': '\uFF09', '(': ')' };

/** 这一条消息是不是旁白式交代；是就返回它降档后要显示的正文（已去首尾空白），否则 `null`。 */
function narrationOf(message) {
  // 只判**已落库的 Agent 消息**：还没落库的乐观发送项没有 seq，也不该在这里被判定。
  if (!message || message.kind !== 'agent') return null;
  if (!Number.isInteger(message.seq) || message.seq <= 0) return null;
  const body = typeof message.body === 'string' ? message.body.trim() : '';
  if (!body) return null;
  // 按**码点**数长度（`body.length` 数的是 UTF-16 单元，会把汉字算成 1、生僻字算成 2）。
  const chars = Array.from(body);
  if (chars.length > NARRATION_MAX_LENGTH) return null;
  // 多行的括注更可能是真内容（清单 / 引用），不降档。
  if (/[\r\n]/.test(body)) return null;
  const close = NARRATION_PAREN_PAIRS[chars[0]];
  if (!close || chars[chars.length - 1] !== close) return null;
  let depth = 0;
  for (let index = 0; index < chars.length; index += 1) {
    if (chars[index] === chars[0]) depth += 1;
    else if (chars[index] === close) {
      depth -= 1;
      // 括号在**中途**就回到 0，说明它不是「整条被一对括住」（例如「（一）（二）」）。
      if (depth === 0 && index !== chars.length - 1) return null;
    }
  }
  return depth === 0 ? body : null;
}

/**
 * 把一条旁白式交代渲染成**一行**、低权重、可展开的消息行（`D4` 形态-a）。
 *
 * 形态-a 的三条硬约束都在这里：
 * 1. **它仍是消息**：占自己的 `seq`（`data-seq`）、有作者与时间、`role` 保持默认（不是摘要行的
 *    `role="status"`）。因此「数几条消息」的口径一个字都不用改。
 * 2. **一行**：正文与作者名同处 `.msg-head` 这一行（不再有独立的 `.msg-text` 段落），折叠态就是一行。
 * 3. **可展开看关联轮次结果的 `reason`**：这一行按钮展开的是**它自己那一轮**的结论——按这条消息
 *    的 `seq` 去 `state.replyOutcomes`（`round_outcome.reply_seq`）直取，不跨数据面拼装、也不并入
 *    摘要行。查不到时如实说「读不到」，不编造理由。
 */
function narrationNode(message, body) {
  const author = message.author || state.member;
  const seq = String(message.seq);
  const expanded = Boolean(state.expandedNarrations[seq]);
  const record = state.replyOutcomes[message.seq] || null;

  const article = el('article', 'msg narration');
  article.dataset.seq = message.seq;
  // 头像降一档（`sm`）：它与正文一起构成「低权重」，但作者身份照旧是它自己的色位。
  article.appendChild(avatarNode(author.display_name, author.avatar_slot, 'sm'));
  const bodyNode = el('div', 'msg-body');
  const line = el('div', 'msg-head');
  line.appendChild(el('span', 'msg-author is-agent', author.display_name));
  line.appendChild(el('span', 'msg-tag', t('narration.tag')));
  line.appendChild(el('span', 'msg-narration-text', body));

  const toggle = el('button', 'msg-narration-toggle');
  toggle.dataset.narrationToggle = seq;
  toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  toggle.setAttribute('aria-controls', 'narration-reason-' + seq);
  const action = expanded ? t('narration.collapse') : t('narration.expand');
  toggle.setAttribute('aria-label', t('narration.toggleAria', { name: author.display_name, action }));
  toggle.appendChild(el('span', 'msg-narration-mark', expanded ? '\u25be' : '\u25b8'));
  toggle.appendChild(el('span', 'msg-narration-toggle-text', action));
  line.appendChild(toggle);
  line.appendChild(el('span', 'msg-time', timeLabel(message.created_at)));
  bodyNode.appendChild(line);

  // 明细**恒常渲染**、用 `hidden` 切可见性（与摘要行同一条理由）：`aria-controls` 指向的 id 在
  // 任何时刻都要能解析到，否则折叠态就是一个悬空引用。`reason` 是后端原文，不重新措辞、不截断。
  const details = el('div', 'msg-narration-reason');
  details.id = 'narration-reason-' + seq;
  details.hidden = !expanded;
  if (record) {
    details.dataset.odId = 'round-reason-' + record.id;
    details.textContent = record.reason || t('outcome.lineReasonEmpty');
  } else {
    details.textContent = t('narration.reasonMissing');
  }
  bodyNode.appendChild(details);

  article.appendChild(bodyNode);
  return article;
}

function messageNode(message) {
  // 旁白式交代先判：它是最省的一种呈现，且必须**永远**保留消息身份（seq / 作者 / 时间）。
  const narration = narrationOf(message);
  if (narration !== null) return narrationNode(message, narration);
  if (message.kind === 'system') {
    // 系统事件消息（穿透加入）：沿用既有消息行形态——方形头像色位 + 作者名 + 时间。
    // 不新造色位：作者名 + 头像用**该成员自己的** --av-* 色位（与他说话时同一个），
    // 正文降一档到 --muted 以便与人类/Agent 正文区分。
    const author = message.author || {};
    const article = el('article', 'msg sys');
    article.dataset.seq = message.seq;
    article.appendChild(avatarNode(author.display_name, author.avatar_slot, 'sm'));
    const body = el('div', 'msg-body');
    const head = el('div', 'msg-head');
    head.appendChild(el('span', 'msg-author', author.display_name || t('msg.systemAuthor')));
    head.appendChild(el('span', 'msg-tag', t('msg.tagEvent')));
    head.appendChild(el('span', 'msg-time', timeLabel(message.created_at)));
    body.appendChild(head);
    body.appendChild(el('div', 'msg-text', message.body));
    appendAttachmentList(body, message);
    appendTaskRefChip(body, message);
    article.appendChild(body);
    return article;
  }

  const failed = message.status === 'failed';
  // 失败的消息还没有 seq，但它不是「待确认」——两个状态不能同时挂在一个节点上。
  const pending = !message.seq && !failed;
  const author = message.author || state.member;
  const article = el('article', 'msg' + (pending ? ' pending' : '') + (failed ? ' failed' : ''));
  if (pending) article.dataset.pending = message.clientId;
  else article.dataset.seq = message.seq;

  article.appendChild(avatarNode(author.display_name, author.avatar_slot));
  const body = el('div', 'msg-body');
  const head = el('div', 'msg-head');
  head.appendChild(el('span', 'msg-author' + (author.kind === 'agent' ? ' is-agent' : ''), author.display_name));
  if (author.kind === 'agent') head.appendChild(el('span', 'msg-tag', t('msg.tagAgent')));
  head.appendChild(el('span', 'msg-time', timeLabel(message.created_at)));
  // 两个 capsule 的字面量走 CSS 的 `content: attr(data-state-label)`：语言取值只在这一处，
  // CSS 里不放任何语言文案（否则切语言后它们会留在上一个语言）。
  if (pending) head.dataset.stateLabel = t('msg.pendingTag');
  if (failed) head.dataset.stateLabel = t('msg.failedTag');
  body.appendChild(head);
  body.appendChild(messageText(message));
  appendAttachmentList(body, message);
  appendTaskRefChip(body, message);

  if (failed) {
    const retry = el('div', 'msg-retry');
    retry.appendChild(el('span', null, t('msg.notDelivered', { reason: message.reason || t('msg.sendFailed') })));
    const retryBtn = el('button', 'btn btn-sm', t('msg.retry'));
    retryBtn.dataset.retry = message.clientId;
    const discardBtn = el('button', 'btn btn-sm', t('msg.discard'));
    discardBtn.dataset.discard = message.clientId;
    retry.appendChild(retryBtn);
    retry.appendChild(discardBtn);
    body.appendChild(retry);
  }

  article.appendChild(body);
  return article;
}

/** 指针 chip 的可见文案（#223）：未读为 0 时不显示数字与「条」。 */
function taskPointerChipText(unread) {
  return unread > 0 ? t('msg.taskThreadEntryUnread', { count: unread }) : t('msg.taskThreadEntry');
}

/** 指针 chip 的 aria 名：与可见文案同源，0 未读时不提计数（#223）。 */
function taskPointerChipAria(title, unread) {
  return unread > 0
    ? t('msg.taskThreadEntryAriaUnread', { title, count: unread })
    : t('msg.taskThreadEntryAria', { title });
}

/** 任务已读后，把频道流里指向**同一任务**的指针 chip 定点改写成最新未读数（通常是 0）。

    #223：`markTaskRead()` 成功后调用。只改既有 chip 的文本与 aria——**不**重铺
    `renderMessages()`，因此消息流的滚动位置、焦点与正在点的节点都不动，也不重新拉频道消息。
    选择子用 `data-task-pointer`：它只挂在指针 chip 上；`task_ref` 的引用 chip 同样是
    `.task-ref-chip` + `data-open-task`，按后者扫会误伤同 target 的引用行。 */
function syncTaskPointerChips(task) {
  if (!task || !task.id) return;
  const list = $('#messageList');
  if (!list) return;
  const unread = Number(task.unread_count) || 0;
  const title = task.title || task.id;
  list.querySelectorAll('[data-task-pointer]').forEach((chip) => {
    if (chip.dataset.taskPointer !== task.id) return;
    chip.textContent = taskPointerChipText(unread);
    chip.setAttribute('aria-label', taskPointerChipAria(title, unread));
  });
}

function appendTaskRefChip(body, message) {
  if (message.task_ref) {
    const referencedTask = taskById(message.task_ref);
    const title = referencedTask ? referencedTask.title : message.task_ref;
    const chip = el('button', 'task-ref-chip', t('msg.taskRef', { title }));
    chip.type = 'button';
    chip.dataset.openTask = message.task_ref;
    chip.setAttribute('aria-label', t('msg.taskRefAria', { title }));
    body.appendChild(chip);
  }
  // 频道指针消息（#198）：`task_pointer` 是**频道 system 消息 → 本频道任务**这条跨 target 指向，
  // 与同 target 的 `task_ref` 是两列、两条语义（见 `CONTEXT.md` **指针消息**）。计数来自任务载荷的
  // `unread_count`（#223）——已读位置之后、作者非本人的 human / agent 消息；system 状态变更记录与
  // 自己的发言都不计入。未读为 0 时只写「进入任务线程」，不显示计数段；任务不在快照里时按未读 0
  // 处理（不编造读数）。`data-task-pointer` 供 `syncTaskPointerChips()` 定点刷新。
  if (!message.task_pointer) return;
  const pointedTask = taskById(message.task_pointer);
  const pointedTitle = pointedTask ? pointedTask.title : message.task_pointer;
  const unread = pointedTask ? Number(pointedTask.unread_count) || 0 : 0;
  const entry = el('button', 'task-ref-chip', taskPointerChipText(unread));
  entry.type = 'button';
  entry.dataset.openTask = message.task_pointer;
  entry.dataset.taskPointer = message.task_pointer;
  entry.setAttribute('aria-label', taskPointerChipAria(pointedTitle, unread));
  body.appendChild(entry);
  // #217：该任务 target 有 pending 时，这一行显示与任务卡片**同款**的「待审批 N」角标
  // （同一个节点工厂、同一对词条、同一个 `.card-approval` 类名）。归属只按
  // `pointedTask.target_id`——频道 target 的 pending、别的任务的 pending 都不出现在这一行；
  // 任务不在快照里（`pointedTask` 为 null）就没有读数，不画。
  // 它是**纯呈现**：`<span>`，无 `data-open-task` / `role="button"` / `tabindex`，点击继续
  // 冒泡到既有 chip 与 `#messageList` 的委托路径——整行的可点区域一个字都不变。
  const badge = approvalBadgeNode(pointedTask, 'message-approval-badge-' + message.id);
  if (badge) body.appendChild(badge);
}

function dayLabel(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return t('time.unknown');
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return t('time.today');
  return localDateString(date);
}

/** 本机日期的 `YYYY-MM-DD` 串（`dayLabel` 与提醒日期格的默认值共用同一个拼法）。 */
function localDateString(date) {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

function timeLabel(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '--:--';
  return String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0');
}

function scrollToBottom() {
  const stream = $('#viewMessages');
  stream.scrollTop = stream.scrollHeight;
}

function autosizeComposer() {
  const input = $('#composerInput');
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 160) + 'px';
}

function syncSendButton() {
  $('#btnSend').disabled =
    !$('#composerInput').value.trim() ||
    !state.online ||
    !activeTarget() ||
    state.mode !== 'channel' ||
    activeArchivedDm() ||
    // 上传还没落地时不能发：那会把还没拿到标识的文件丢在这条消息之外。
    attachingCount > 0;
}

/** composer 的只读锁（已归档私信）：正文、附件、@提及、作为任务发送一起禁用；
    hint 由 `syncMentionHint()` 统一裁决（归档时它优先于 @ 补全的提示）。

    这是输入区可用性的**唯一**落点：打开 target（`renderHeader()`）时按当前 target 判一次，
    切回普通频道 / 正常私信时同一行代码把全部控件恢复，不各写一份。 */
function syncComposerLock(archived) {
  const locked = archived === undefined ? activeArchivedDm() : Boolean(archived);
  const input = $('#composerInput');
  if (input) input.disabled = locked;
  ['#btnAttach', '#btnMention', '#btnAsTask', '#attachInput'].forEach((selector) => {
    const node = $(selector);
    if (node) node.disabled = locked;
  });
  syncMentionHint();
}

/** 清空主 composer 的输入区：正文、自适应高度、发送键、候选清单一起收。
    两条路径共用同一件事「正文已经不在输入框里了」：消息送出（`sendMessage`）与
    「作为任务发送」成功把正文变成任务标题（D6）。

    **不**清附件草稿：这条路径只说「正文不在输入框里了」。消息送出会另调
    `clearComposerAttachments()` 把草稿交给那条消息；「作为任务发送」则**留着**草稿——
    任务不需要附件（D15），静默丢掉人选好的文件才是更难解释的行为。 */
function resetComposerInput() {
  $('#composerInput').value = '';
  autosizeComposer();
  syncSendButton();
  // 清单描述的是刚消失的那段前缀，不能留在屏上。
  closeMentionPicker();
}

/* ─── 附件（#67 / ADR-0014）：两步上传 —— 先拿标识，随消息提交时绑定 ──────────
   前端只做两件机械事：把选中的文件按**字节**POST 成一个草稿、把后端返回的标识攒在手上。
   「绑定」发生在发送那条消息的**同一个请求**里（后端在同一事务内完成），前端不另开一条路径，
   也不提前把草稿画成「已发送」——未绑定的草稿在读取面里不存在，前端也就不该假装它存在。 */

/** 正在上传的个数：上传期间发送键禁用（否则会把还没拿到标识的文件丢在消息之外）。 */
let attachingCount = 0;

/** 附件体积的界面说法。单位符号与语言无关，因此不进词条表（词条表里没有裸符号的位置）。 */
function formatBytes(size) {
  const bytes = Number(size) || 0;
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

/** 上传一个文件成**草稿**，返回附件元数据（标识 / 原名 / 大小 / MIME / 内容 url）。

    请求体是文件的原始字节、`Content-Type` 是声明的 MIME、文件名在 query 上：与后端
    `POST /api/attachments` 的契约一致（那个端点因此不需要 multipart 解析）。
    拒绝原因由后端按**语言偏好**渲染，这里只把它读出来给调用方。 */
async function uploadAttachment(file) {
  const response = await fetch('/api/attachments?name=' + encodeURIComponent(file.name || ''), {
    method: 'POST',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const error = new Error(readError(payload, response));
    error.status = response.status;
    throw error;
  }
  return payload.attachment;
}

/** 选中的文件逐个上传成草稿；单个失败只影响它自己（可读原因进 toast），已成功的留在手上。 */
async function attachFiles(files) {
  const list = Array.from(files || []);
  if (!list.length) return;
  attachingCount += list.length;
  renderComposerState();
  for (const item of list) {
    try {
      state.composerAttachments.push(await uploadAttachment(item));
    } catch (err) {
      toast(t('toast.attachFailed', { reason: err.message }), 'err');
    } finally {
      attachingCount -= 1;
      renderComposerState();
    }
  }
}

/** 移除一个还没发送的草稿（只从手上移走；库里的草稿行与字节仍在——没有删除端点）。 */
function removeComposerAttachment(attachmentId) {
  state.composerAttachments = state.composerAttachments.filter((item) => item.id !== attachmentId);
  renderComposerState();
}

/** 草稿交给某条消息之后清空手上的清单（发送成功与乐观发送都走它）。 */
function clearComposerAttachments() {
  state.composerAttachments = [];
  renderComposerState();
}

/** 附件区与发送键是同一份状态（手上的草稿 + 在飞的上传）的两个出口：一起刷。
    分成两次调用迟早会漏掉一处，于是发送键会对着「还有草稿没落地」的状态说「可以发」。 */
function renderComposerState() {
  renderComposerAttachments();
  syncSendButton();
}

/**
 * 一块附件元素：草稿态（composer 里，可移除）与已发送态（消息里，可下载）共用同一块形状。
 *
 * 已发送态用**原生 `<a download>`**：内容端点给出 `Content-Disposition` 与 MIME，浏览器据此
 * 落到本地文件；「预览」是同一个端点的 `disposition=inline`，本票只做消息里的下载入口
 * （富媒体预览是 Out of Scope）。
 *
 * **只有已绑定**（`message_id` 非空）的附件才是可点的链接：乐观发送中的消息与失败留下的消息
 * 带的仍是**草稿**，而内容端点对草稿回 404 —— 渲染成一个点下去必然 404 的链接是骗人的。
 */
function attachmentNode(attachment, options) {
  const opts = options || {};
  const name = attachment.name || t('attach.fallbackName');
  const size = formatBytes(attachment.size);
  const link = Boolean(opts.link) && Boolean(attachment.message_id);
  const node = el(link ? 'a' : 'span', 'attach' + (link ? ' is-link' : ''));
  node.dataset.attachment = attachment.id;
  if (link) {
    node.setAttribute('href', attachment.url);
    node.setAttribute('download', name);
    node.setAttribute('aria-label', t('msg.attachmentAria', { name, size }));
  }
  node.appendChild(el('span', 'attach-name', name));
  node.appendChild(el('span', 'attach-size', size));
  if (opts.removable) {
    const remove = el('button', 'attach-remove', '×');
    remove.type = 'button';
    remove.dataset.removeAttachment = attachment.id;
    remove.setAttribute('aria-label', t('composer.attachRemove', { name }));
    node.appendChild(remove);
  }
  return node;
}

/** 把待发送的草稿与「正在上传」的占位画进 composer 的附件区（空的时候整块隐藏）。 */
function renderComposerAttachments() {
  const box = $('#composerAttachments');
  if (!box) return;
  const nodes = state.composerAttachments.map((attachment) =>
    attachmentNode(attachment, { removable: true }),
  );
  if (attachingCount > 0) {
    nodes.push(el('span', 'attach is-pending', t('composer.attaching', { count: attachingCount })));
  }
  box.hidden = nodes.length === 0;
  replaceChildren(box, nodes);
}

/** 消息里的附件：一块可点开（下载）的元素。没有附件时什么都不渲染。 */
function appendAttachmentList(body, message) {
  const attachments = message.attachments || [];
  if (!attachments.length) return;
  const list = el('div', 'msg-attachments');
  attachments.forEach((attachment) => list.appendChild(attachmentNode(attachment, { link: true })));
  body.appendChild(list);
}

/* ─── @ 补全：清单只找候选 + 按解析器的引号规则拼字符串（前端不解析提及） ──────
   分工：候选来自后端的频道成员表（`GET /api/channels/{id}/members`），插入的字符串按
   `alibot/mentions.py` 的规则拼；正文到底是不是提及**只由后端**裁决（`mention_snapshot`
   随消息落库），前端既不另写一套解析规则、也不预测解析结果。

   从后端借来的两条判据（只决定「要不要弹」与「怎么拼」，不决定「是不是提及」）：
   1. `@` 的左边界：前面是 ASCII 字母或数字时不算提及（邮箱式）——对应 `_left_boundary_ok`。
      这类位置**不弹清单**，否则插进去的文本必然不被解析（违背票面硬约束）。
   2. 裸形式的候选串遇到分隔符即止（正本 `_TOKEN_STOPS`：空白、各类标点、`@`、`\` 等）——
      名字里含其中任一字符时只能写引号形式（空格是最常见的一种）。引号字符本身也在分隔符里，
      于是一名叫 `a"b` 的成员必须写引号形式；而正本认**三种**引号对（`"…"` / `“…”` / `‘…’`），
      所以选“**闭引号不在名字里**”的那一对（正本用 `text.find(closer)`，闭引号若出现在名字中间，
      名字就会被截断）。只有当名字同时含 `"`、`”`、`’` 三个闭引号时，**没有**任何合法插入形态：
      选中它时给可读提示、不插入无效文本。

   过滤只用于把候选缩小（包含匹配、ASCII 大小写不敏感），插入的始终是**库里的原名**：
   于是「最左最长」这类解析语义不会因为前端放宽过滤而改变。 */
const MENTION_TOKEN_STOPS =
  " \t\r\n@,，。、;；:：!！?？'\"“”‘’()（）[]【】{}「」<>《》/\\|~#$%^&*=";
// 与正本 `mentions.py._QUOTE_PAIRS` 同源：开引号 → 闭引号。顺序 = 优先用哪种引号。
const MENTION_QUOTE_PAIRS = [
  ['"', '"'],
  ['“', '”'],
  ['‘', '’'],
];

// 补全清单的运行时状态。它不是业务正本：候选只来自后端返回的频道成员表。
const mentionPicker = {
  open: false,
  empty: null, // null = 有候选；'no-members' = 频道没有 Agent 成员；'unavailable' = 成员清单读不到
  items: [],
  activeIndex: 0,
  trigger: null, // {at, query, quote}：@ 的位置、已输入的前缀、引号形式时的开引号
  signature: '',
  dismissed: '', // 被 Esc 关掉 / 刚插入完的触发点签名：同一个触发点不再自动弹出
};
// 频道成员清单的读取状态：每个频道各自一份；失败只记事实，不伪造成员。
const channelMembersFailed = new Set();
// 在飞的读取：**同一个频道复用同一个 promise**，于是调用方 `await` 之后拿到的就是
// 「这一次读取已经落地」的状态。早退会给调用方一个「读完了」的假信号——名册打开的那一瞬
// 撞上在飞读取（切频道、composer 获得焦点都会发起）时，它就会用旧缓存渲染，而且再没有
// 第二次重绘的机会；`openTask` / `selectTarget` 的 `await` 也依赖同一条语义。
const channelMembersInFlight = new Map();
// 正在「加入频道」的成员 id（按下到后端答复之间禁用那一个控件，防重复提交）。
const joining = new Set();

function containsAny(text, chars) {
  for (const char of String(text == null ? '' : text)) {
    if (chars.includes(char)) return true;
  }
  return false;
}

function isAsciiAlnum(char) {
  return char !== undefined && ((char >= '0' && char <= '9') || (char >= 'A' && char <= 'Z') || (char >= 'a' && char <= 'z'));
}

/** 该名字要插入的字符串；`null` = 这个名字没有任何能被解析器识别的插入形态。

    名字不含分隔符 ⇒ 裸形式 `@名字`；否则走引号形式，引号对取「闭引号不在名字里」的第一个
    （正本的 `find(closer)` 会在名字中间的闭引号处截断）。*/
function mentionText(name) {
  const text = String(name == null ? '' : name);
  if (!containsAny(text, MENTION_TOKEN_STOPS)) return '@' + text;
  const pair = MENTION_QUOTE_PAIRS.find(([, closer]) => !text.includes(closer));
  return pair ? '@' + pair[0] + text + pair[1] : null;
}

/** 该名字有没有能被解析器识别的插入形态（三个闭引号都在名字里时没有）。 */
function mentionInsertable(name) {
  return mentionText(name) !== null;
}

/** 光标处是否正在写一个 `@提及`；是则给出被替换的起点与已输入的前缀。 */
function mentionTriggerAt(input) {
  const caret = input.selectionStart;
  if (caret === null || caret !== input.selectionEnd) return null; // 有选区时不猜位置
  const before = input.value.slice(0, caret);
  const at = before.lastIndexOf('@');
  if (at < 0) return null;
  // 左边界（与 _left_boundary_ok 对齐）：邮箱式文本不弹清单。
  if (at > 0 && isAsciiAlnum(before[at - 1])) return null;
  const after = before.slice(at + 1);
  const opener = after.slice(0, 1);
  const closer = MENTION_QUOTE_PAIRS.reduce((found, [open, close]) => (open === opener ? close : found), null);
  if (closer) {
    const body = after.slice(1);
    // 已经写上了闭合引号 = 这段提及写完了，不再提示。
    if (body.includes(closer)) return null;
    return { at, query: body, quote: opener };
  }
  // 裸形式：候选串遇到分隔符即止（已经离开这个候选串，或它已不可能再被补全）。
  if (containsAny(after, MENTION_TOKEN_STOPS)) return null;
  return { at, query: after, quote: null };
}

function mentionTriggerSignature(input) {
  const trigger = mentionTriggerAt(input);
  return trigger ? trigger.at + ':' + trigger.query : '';
}

function channelAgentMembers(channelId) {
  const members = state.channelMembers[channelId];
  // null 与「读到空列表」是两件事：前者是还没读到（去读一次），后者才是真的没有成员。
  return members ? members.filter((member) => member.kind === 'agent') : null;
}

/** 读一次该频道的成员表并落进缓存；**同一频道的在飞读取复用同一个 promise**
    （`await` 它拿到的就是「这一次读取已落地」的状态，见 `channelMembersInFlight`）。 */
async function refreshChannelMembers(channelId) {
  const id = channelId || state.activeChannelId;
  const channel = state.channels.find((item) => item.id === id);
  if (!channel) return null;
  const inFlight = channelMembersInFlight.get(id);
  if (inFlight) return inFlight;
  const read = (async () => {
    try {
      const payload = await api(channelPath(id, '/members'));
      state.channelMembers[id] = payload.members || [];
      channelMembersFailed.delete(id);
    } catch {
      // 读不到就如实记下：不静默清空已读到的候选，也不伪造成员。
      if (!state.channelMembers[id]) state.channelMembers[id] = [];
      channelMembersFailed.add(id);
    }
    return state.channelMembers[id];
  })();
  channelMembersInFlight.set(id, read);
  try {
    return await read;
  } finally {
    channelMembersInFlight.delete(id);
  }
}

/** 把一个 Agent 加入当前频道，然后**不刷新页面**就地刷新成员表与 `@` 补全候选。

    校验全在后端：失败时只呈现后端的可读原因，不在前端建半成品状态。响应自带该频道最新成员表，
    直接覆盖本地缓存（覆盖而不是「先加再读」，中间没有可读到旧成员表的窗口）。
    刷新后重判一次补全：清单正开着时，新加入的成员**立刻**可选。 */
async function joinChannelMember(memberId) {
  const channel = activeChannel();
  if (!channel || joining.has(memberId)) return;
  joining.add(memberId);
  renderDock();
  let joined = false;
  try {
    const payload = await api(channelPath(channel.id, '/members'), {
      method: 'POST',
      body: { member_id: memberId },
    });
    state.channelMembers[channel.id] = payload.members || state.channelMembers[channel.id];
    channelMembersFailed.delete(channel.id);
    if (payload.channel) replaceChannel(payload.channel);
    const name = (payload.member && payload.member.display_name) || t('toast.memberFallback');
    toast(
      payload.joined
        ? t('toast.joinedChannel', { name, channel: channel.name })
        : t('toast.alreadyMember', { name, channel: channel.name }),
    );
    updateMentionPicker();
    joined = true;
  } catch (err) {
    toast(t('toast.joinFailed', { reason: err.message }), 'err');
  } finally {
    joining.delete(memberId);
    renderDock();
    // 落点：卡片已经从候选井挪到「已加入」段，但 380px 列里那一段常常在视口外——
    // 只看到一行消失 + toast，人会不知道它去了哪。把落点滚进视野，并把焦点交给新卡片的
    // 名字控件（刚被点的「加入」按钮在重渲染里已经不存在，焦点本来会落回 body）。
    if (joined) revealJoinedMember(memberId);
  }
}

/** 把「加入」的落点（已加入段里那张卡）滚进视野并接住焦点。

    焦点落在**名字**上而不是整张卡：名字是那里唯一可聚焦的控件（打开详情），
    于是键盘用户在按过「加入」之后可以直接继续按 Enter 看详情，而不是从文档开头 Tab 回来。 */
function revealJoinedMember(memberId) {
  const card = $('#dockMembers') && $('#dockMembers').querySelector(
    '[data-od-id="dock-member-' + memberId + '"]',
  );
  if (!card) return;
  card.scrollIntoView({ block: 'nearest' });
  const name = card.querySelector('.dock-member-name');
  if (name) name.focus({ preventScroll: true });
}

function syncMentionHint() {
  const hint = $('#composerHint');
  if (!hint) return;
  // 归档私信优先：输入区不可用，此时提示 @ 选择器怎么操作是错的。
  if (activeArchivedDm()) {
    hint.textContent = t('composer.archivedHint');
    return;
  }
  if (!mentionPicker.open) {
    hint.textContent = t('composer.hint');
  } else if (mentionPicker.items.length) {
    hint.textContent = t('mention.hintPick');
  } else {
    // 空态没有可选项：Enter 仍是发送（键盘契约不变），只说明怎么关掉它。
    hint.textContent = t('mention.hintDismiss');
  }
}

function renderMentionPicker() {
  const panel = $('#composerMention');
  if (!panel) return;
  if (!mentionPicker.open) {
    panel.hidden = true;
    replaceChildren(panel, []);
    return;
  }
  panel.hidden = false;
  if (mentionPicker.empty) {
    const text = mentionPicker.empty === 'unavailable' ? t('mention.unavailable') : t('mention.empty');
    const empty = el('div', 'member-pick-empty mention-empty', text);
    empty.setAttribute('data-od-id', 'mention-picker-empty');
    replaceChildren(panel, [empty]);
    return;
  }
  const rows = mentionPicker.items.map((member, index) => {
    const active = index === mentionPicker.activeIndex;
    const row = el('button', 'member-opt mention-opt' + (active ? ' is-active' : ''));
    row.type = 'button';
    row.dataset.mention = member.id;
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', active ? 'true' : 'false');
    row.setAttribute('data-od-id', 'mention-option-' + member.id);
    row.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
    row.appendChild(el('span', 'mention-name', member.display_name));
    if (!mentionInsertable(member.display_name)) {
      // 不静默：它在清单里，但明说这个名字无法被 @ 到。
      row.appendChild(el('span', 'mention-note', t('mention.cannotMention')));
    }
    return row;
  });
  replaceChildren(panel, rows);
  const active = panel.querySelector('.mention-opt.is-active');
  if (active) active.scrollIntoView({ block: 'nearest' });
}

function closeMentionPicker() {
  mentionPicker.open = false;
  mentionPicker.empty = null;
  mentionPicker.items = [];
  mentionPicker.activeIndex = 0;
  mentionPicker.trigger = null;
  mentionPicker.signature = '';
  renderMentionPicker();
  syncMentionHint();
}

/** 按光标处重判触发点：弹清单、换候选、或让它消失。 */
function updateMentionPicker() {
  const input = $('#composerInput');
  const channel = activeChannel();
  if (!input || !channel) {
    closeMentionPicker();
    return;
  }
  const trigger = mentionTriggerAt(input);
  const signature = mentionTriggerSignature(input);
  // 触发点变了就撤掉上一次的「已关闭」记录：关掉的只是那一个触发点，不是整个能力。
  if (mentionPicker.dismissed && mentionPicker.dismissed !== signature) mentionPicker.dismissed = '';
  if (!trigger || signature === mentionPicker.dismissed) {
    closeMentionPicker();
    return;
  }
  const agents = channelAgentMembers(channel.id);
  if (agents === null) {
    // 候选来源还没读到：先去读一次，回来再判（读失败则落到明确空态，不再重试）。
    closeMentionPicker();
    refreshChannelMembers().then(updateMentionPicker);
    return;
  }
  const needle = trigger.query.toLowerCase();
  const items = agents.filter((member) => String(member.display_name || '').toLowerCase().includes(needle));
  if (agents.length && !items.length) {
    // 过滤无匹配：清单消失，且不打断正常输入。
    closeMentionPicker();
    return;
  }
  const sameTrigger = mentionPicker.open && mentionPicker.signature === signature;
  mentionPicker.trigger = trigger;
  mentionPicker.signature = signature;
  mentionPicker.items = items;
  if (agents.length) mentionPicker.empty = null;
  else if (channelMembersFailed.has(channel.id)) mentionPicker.empty = 'unavailable';
  else mentionPicker.empty = 'no-members';
  mentionPicker.open = true;
  // 同一个触发点上重判（例如光标动了）不重置高亮，否则方向键一按高亮就弹回第一项。
  mentionPicker.activeIndex = sameTrigger ? Math.min(mentionPicker.activeIndex, Math.max(items.length - 1, 0)) : 0;
  renderMentionPicker();
  syncMentionHint();
}

function moveMentionHighlight(delta) {
  const total = mentionPicker.items.length;
  if (!total) return;
  mentionPicker.activeIndex = Math.max(0, Math.min(total - 1, mentionPicker.activeIndex + delta));
  renderMentionPicker();
}

function dismissMentionPicker() {
  // Esc：只关清单、不改正文，并把这个触发点记为已关闭（不然下一次 input 立刻又弹出来）。
  mentionPicker.dismissed = mentionTriggerSignature($('#composerInput'));
  closeMentionPicker();
}

function insertMention(name) {
  const input = $('#composerInput');
  const trigger = mentionPicker.trigger;
  if (!trigger) return;
  const insert = mentionText(name);
  if (insert === null) return; // 调用方已拦（chooseMention），这里只保证不插入 null
  const caret = input.selectionStart;
  const value = input.value;
  const after = value.slice(caret);
  // 光标处的 `@前缀`（引号形式时含开引号）正好是 [trigger.at, caret)：整段替换掉。
  const tail = /^\s/.test(after) ? insert : insert + ' ';
  input.value = value.slice(0, trigger.at) + tail + after;
  const nextCaret = trigger.at + tail.length;
  input.setSelectionRange(nextCaret, nextCaret);
  autosizeComposer();
  syncSendButton();
  closeMentionPicker();
  mentionPicker.dismissed = mentionTriggerSignature(input);
  updateMentionPicker();
  input.focus();
}

function chooseMention(member) {
  if (!member) return;
  const name = String(member.display_name || '');
  if (!mentionInsertable(name)) {
    // 不静默、也不插入无效文本：这个名字没有能被解析器识别的插入形态。
    toast(t('mention.quoteUnsupported', { name }), 'err');
    return;
  }
  insertMention(name);
}

/** 工具行「@提及」：与原型 `alibot-app.html:1798` 同构——向输入框**追加** `@`、聚焦、激活发送键。
    随后弹出的候选仍是**既有**的 `@` 补全（`updateMentionPicker()` → `renderMentionPicker()`），
    这里不新写第二套候选逻辑，也不改候选集（仍只列当前 target 的 Agent 成员）。 */
function mentionFromToolbar() {
  const input = $('#composerInput');
  if (!input) return;
  input.value += '@';
  const caret = input.value.length;
  input.focus();
  input.setSelectionRange(caret, caret);
  autosizeComposer();
  syncSendButton();
  updateMentionPicker();
}

/* ─── 新建频道 ──────────────────────────────────────────────────────── */
function openChannelModal() {
  $('#channelFormError').hidden = true;
  $('#channelNameInput').value = '';
  $('#channelDescInput').value = '';
  renderMemberPicker();
  $('#channelOverlay').hidden = false;
  $('#channelNameInput').focus();
}

function closeChannelModal() {
  $('#channelOverlay').hidden = true;
}

/* ─── 新建私信 ──────────────────────────────────────────────────────── */
/* DM 创建能力仍在，入口是 Agent 详情头部的「发送消息」（`messageActiveAgent()`）：
   选中一个成员就同时表达了「和谁私信」，不再需要一个先选目标、再点创建的两步弹窗。 */

function renderMemberPicker() {
  const agents = state.members.filter((member) => member.kind === 'agent');
  const picker = $('#memberPicker');
  if (!agents.length) {
    // 无 Agent 时成员清单是空态，但控件（勾选容器）按规格存在。
    const empty = el('div', 'member-pick-empty', t('channel.pickEmpty'));
    empty.setAttribute('data-od-id', 'member-picker-empty');
    replaceChildren(picker, [empty]);
    return;
  }
  const options = agents.map((member) => {
    const option = el('button', 'member-opt');
    option.type = 'button';
    option.dataset.pick = member.id;
    option.setAttribute('aria-pressed', 'false');
    option.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
    option.appendChild(document.createTextNode(member.display_name));
    return option;
  });
  replaceChildren(picker, options);
}

async function submitChannel() {
  const name = $('#channelNameInput').value.trim();
  const description = $('#channelDescInput').value.trim();
  const memberIds = Array.prototype.map.call(
    $('#memberPicker').querySelectorAll('.member-opt.is-on'),
    (node) => node.dataset.pick,
  );
  const submit = $('#channelCreateSubmit');
  submit.disabled = true;
  try {
    // 校验交给后端：失败时整体不创建，前端只呈现错误、不建半成品。
    const payload = await api('/api/channels', { method: 'POST', body: { name, description, member_ids: memberIds } });
    state.channels = payload.channels;
    setActiveChannelId(payload.channel.id);
    closeChannelModal();
    await loadWorkspace({ silent: true, keepActive: true });
    scrollToBottom();
    $('#composerInput').focus();
    toast(t('toast.channelCreated', { name: payload.channel.name }));
  } catch (err) {
    const box = $('#channelFormError');
    box.textContent = err.message;
    box.hidden = false;
  } finally {
    submit.disabled = false;
  }
}

/* ─── 新建 Agent 与远端数据流告知 ───────────────────────────────────── */
// 「新建 Agent」表单里，选择器当前的提供商是否出自人的手（而不是 hydration 之前的占位默认值）。
let agentProviderTouched = false;

function markProviderTouched() {
  agentProviderTouched = true;
}

function openAgentModal() {
  $('#agentFormError').hidden = true;
  $('#agentNameInput').value = '';
  $('#agentRoleInput').value = '';
  $('#agentModelInput').value = '';
  $('#agentPromptInput').value = '';
  agentProviderTouched = false;
  renderProviderSelect();
  renderRemoteNotice();
  $('#agentOverlay').hidden = false;
  $('#agentNameInput').focus();
}

function closeAgentModal() {
  $('#agentOverlay').hidden = true;
}

/* 新建 Agent 弹窗的远端告知块节点。**判定与文案只有一份实现**（`renderRemoteNoticeBlock()`），
   这里只是「哪几个节点属于新建弹窗」的接线表；详情页那份由 `agentRemoteNoticeNode()` 直接登记
   节点引用（它在 `#adBody` 里，不在文档顶层，按 id 查不到）。 */
const AGENT_CREATE_NOTICE_IDS = {
  box: '#agentRemoteNotice',
  title: '#agentRemoteTitle',
  body: '#agentRemoteBody',
  hint: '#agentRemoteHint',
};

// 重建选项会清掉当前选择。`keep` 为真时保留它：只有确实出自人的手、且仍然在这份清单里的那一项
// 才保留；其余一律回到清单首项（provider 清单的正本只有后端 `/api/workspace` 一处）。
function fillProviderSelect(select, keep) {
  const previous = keep ? select.value : '';
  const providers = state.providers.length ? state.providers : PROVIDER_PLACEHOLDER;
  replaceChildren(select, providers.map((provider) => {
    const option = el('option', null, providerLabel(provider));
    option.value = provider;
    return option;
  }));
  select.value = providers.includes(previous) ? previous : providers[0];
  return select.value;
}

function renderProviderSelect(preserveUserPick) {
  fillProviderSelect($('#agentProviderSelect'), Boolean(preserveUserPick));
  updateProviderHint();
}

// 工作台快照可能晚于「新建 Agent」弹窗到达（刷新后马上点 Agent）：清单到齐时刷新已打开的弹窗，
// 于是人不必先关再开一次。provider 清单的正本只有后端 `/api/workspace` 一处。
// 只在人真的动过选择器时保留选择：hydration 之前那一项只是占位默认值，不是用户选择，
// 换完整清单后应回到清单首项（与常规加载先打开弹窗看到的一致）。
function syncOpenAgentModal() {
  if ($('#agentOverlay').hidden) return;
  renderProviderSelect(agentProviderTouched);
  renderRemoteNotice();
}

function currentProvider() {
  return $('#agentProviderSelect').value;
}

function isLocalProvider(provider) {
  return state.localProviders.indexOf(provider) >= 0;
}

function providerHintText(provider) {
  return isLocalProvider(provider) ? t('agent.providerLocalHint') : t('agent.providerRemoteHint');
}

function updateProviderHint() {
  $('#agentProviderHint').textContent = providerHintText(currentProvider());
}

function acknowledged(provider) {
  return state.consents.some((record) => record.provider === provider);
}

/** 远端数据流告知块的**唯一**渲染实现（新建与编辑两个弹窗共用）。

    返回真 = 这一块**可见**（当前 provider 是远端且还没确认过）。判定读的是库内 `remote_consent`
    的镜像（`state.consents`），因此「未确认」在界面上与后端的可用性判定说的是同一件事。 */
function renderRemoteNoticeBlock(ids, provider) {
  // `ids` 的每一格可以是选择器（静态弹窗）或节点本身（详情页里由 `agentRemoteNoticeNode()`
  // 现场建出来的那一份）——两种接线读的是同一份判定与文案，不各写一套。
  const node = (value) => (typeof value === 'string' ? $(value) : value);
  const box = node(ids.box);
  if (!box) return false;
  if (isLocalProvider(provider) || acknowledged(provider)) {
    box.hidden = true;
    return false;
  }
  box.hidden = false;
  const faces = (state.dataFaces.length ? state.dataFaces : remoteFlow.data_faces).join(t('common.nameListSep'));
  node(ids.title).textContent = t('agent.remoteTitle', { provider: providerLabel(provider) });
  node(ids.body).textContent = t('agent.remoteBody', { faces });
  node(ids.hint).textContent = t('agent.remoteHint');
  return true;
}

function renderRemoteNotice() {
  renderRemoteNoticeBlock(AGENT_CREATE_NOTICE_IDS, currentProvider());
}

/** 确认一次告知（幂等）。`provider` / `button` / `refreshNotice` 由调用点给：新建与编辑
    两个弹窗共用这一条路径与同一份本地镜像更新，不各写一套。 */
async function confirmRemoteFlow(provider, button, refreshNotice) {
  button.disabled = true;
  try {
    const payload = await api('/api/remote-data-flow/ack', { method: 'POST', body: { provider } });
    state.consents = payload.consents || [];
    state.agents = payload.agents || state.agents;
    refreshNotice();
    // 确认会改变成员的启用态：状态点必须从后端重取，不靠本地推断。
    await loadWorkspace({ silent: true, keepActive: true });
    toast(t('toast.remoteAck', { provider: providerLabel(provider) }));
  } catch (err) {
    toast(t('toast.remoteAckFailed', { reason: err.message }), 'err');
  } finally {
    button.disabled = false;
  }
}

async function submitAgent() {
  const provider = currentProvider();
  const model = $('#agentModelInput').value.trim();
  const payload = {
    name: $('#agentNameInput').value.trim(),
    role: $('#agentRoleInput').value.trim(),
    model_id: provider + ':' + model,
    system_prompt: $('#agentPromptInput').value.trim(),
  };
  const box = $('#agentFormError');
  const submit = $('#agentCreateSubmit');
  submit.disabled = true;
  try {
    // 校验交给后端：名称或模型留空时整体不创建，前端只呈现错误、不建半成品。
    const result = await api('/api/agents', { method: 'POST', body: payload });
    state.agents = result.agents || state.agents;
    state.members = result.members || state.members;
    closeAgentModal();
    await loadWorkspace({ silent: true, keepActive: true });
    toast(t('toast.agentCreated', { name: result.agent.display_name }));
  } catch (err) {
    box.textContent = err.message;
    box.hidden = false;
  } finally {
    submit.disabled = false;
  }
}

async function loadRemoteFlow() {
  try {
    remoteFlow = await api('/api/remote-data-flow');
    state.dataFaces = remoteFlow.data_faces || [];
    // 告知文案要用这份响应里的数据面来拼。响应晚到、而弹窗已经开着时，正文会一时停在空数据面
    // （这是异步加载本就存在的窗口）；响应一到就重绘，避免界面一直停在空正文。
    // 只重绘告知，不重建选择器、不动人的选择与同意语义。
    if (!$('#agentOverlay').hidden) renderRemoteNotice();
  } catch {
    // 告知文案读不到不影响主流程；下一次打开面板会重试。
  }
}

/* ─── 事件绑定 ──────────────────────────────────────────────────────── */
async function selectTarget(targetId) {
  const target = state.channels.find((item) => item.id === targetId)
    || state.dms.find((item) => item.id === targetId);
  if (!target) return;
  const previous = state.activeChannelId;
  if (previous && previous !== targetId) {
    // 离开旧 target 前把它推到当前标读；其它 target 的已读位置不受影响。
    const stale = state.channels.find((item) => item.id === previous)
      || state.dms.find((item) => item.id === previous);
    if (stale && stale.last_seq > stale.last_read_seq) scheduleRead(previous, stale.last_seq);
  }
  closeTaskStream();
  // 运行面板读的是**上一个 target** 的快照与 live 归属：换 target 时整个作废，
  // 不把旧频道的分派留在新频道旁边。
  resetRunPanelState();
  // 选一个频道 / 私信 = 回到消息流：详情视图与它的草稿一并作废。
  leaveAgentView();
  state.mode = 'channel';
  state.activeTaskId = null;
  state.activeTask = null;
  setActiveChannelId(targetId);
  state.pending = state.pending.filter((item) => item.target_id === targetId || item.status === 'failed');
  // 候选清单属于刚被切换掉的频道：进新 target 时先关掉，再按新频道的成员表重建。
  closeMentionPicker();
  state.outcomes = {};
  // 折叠态的键是「触发它的那条消息的 seq」，跨频道不通用：换频道时一并清掉。
  // 旁白行的折叠态键是旁白自己的 seq，同理只在当前 target 内有效。
  state.expandedOutcomes = {};
  state.replyOutcomes = {};
  state.expandedNarrations = {};
  render();
  try {
    await refreshMessages();
    await refreshOutcomes();
    await loadApprovalsFor(targetId);
    if (target.kind !== 'dm') await refreshChannelMembers();
    // 切频道时按**新频道**的读数重铺 pill（缓存命中则同步返回；没有就不下结论，pill 隐藏）。
    await loadActiveChannelMode();
    state.online = true;
  } catch (err) {
    state.online = false;
    toast(t('toast.sessionLoadFailed', { reason: err.message }), 'err');
  }
  render();
  markCurrentRead();
  scrollToBottom();
}

function bindSearchEvents() {
  const input = $('#searchInput');
  // 实时搜索（原型 `$('#searchInput').addEventListener('input', renderSearch)`）：
  // 落笔延时一拍只发一次请求，Enter 立即发。
  input.addEventListener('input', scheduleSearch);
  input.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    cancelPendingSearch();
    return runSearch();
  });
  $('#searchFacets').addEventListener('click', (event) => {
    const chip = event.target.closest('[data-facet]');
    if (chip) setSearchFacet(chip.dataset.facet);
  });
}

function bindCmdEvents() {
  const input = $('#cmdInput');
  input.addEventListener('input', (event) => renderCmd(event.target.value));
  input.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    // Enter 跑**第一条**（高亮的那一条，原型 `:1839` 同一行为）。
    const first = $('#cmdList').querySelector('[data-cmd]');
    if (first) runCmd(first.dataset.cmd);
  });
  $('#cmdList').addEventListener('click', (event) => {
    const item = event.target.closest('[data-cmd]');
    if (item) runCmd(item.dataset.cmd);
  });
  $('#cmdOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'cmdOverlay') closeCmd();
  });
}

function bindEvents() {
  // ── 工作区设置：rail **头部**入口（原型 `:515-518` 的第二枚 icon-btn）+ 弹窗（语言 / 消息密度 / 主题三档）──
  $('#btnSettings').addEventListener('click', openSettingsModal);
  $('#settingsModalClose').addEventListener('click', closeSettingsModal);
  $('#settingsDone').addEventListener('click', closeSettingsModal);
  $('#settingsOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'settingsOverlay') closeSettingsModal();
  });
  // 语言：`change` 一次写一档（后端是正本），渲染由 `applySettings` 统一铺。
  $('#settingsLocaleSelect').addEventListener('change', (event) => {
    saveSettings({ locale: event.target.value });
  });
  // 消息密度：两个 `.radio-card`（原型形态），点哪个写哪个。
  $('#settingsDensity').addEventListener('click', (event) => {
    const card = event.target.closest('[data-density]');
    if (!card) return;
    saveSettings({ message_density: card.dataset.density });
  });
  // 主题：三个 `.radio-card`（浅色 / 深色 / 跟随系统），点哪个写哪个。
  $('#settingsTheme').addEventListener('click', (event) => {
    const card = event.target.closest('[data-theme]');
    if (!card) return;
    saveSettings({ theme: card.dataset.theme });
  });
  // ── 提醒：rail 入口 + 弹窗（列表 / 空态 / 新增 / 删除）──
  $('#btnReminders').addEventListener('click', openRemindersModal);
  $('#remindersModalClose').addEventListener('click', closeRemindersModal);
  $('#remindersCancel').addEventListener('click', closeRemindersModal);
  $('#remindersOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'remindersOverlay') closeRemindersModal();
  });
  $('#reminderAddSubmit').addEventListener('click', submitReminder);
  // 与其它「一格正文 + 一个提交键」的弹窗同形：Enter 直接提交。
  $('#reminderText').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); submitReminder(); }
  });
  // 删除：一个控件一条提醒，点击委派到它所在的那一行。
  $('#reminderList').addEventListener('click', (event) => {
    const remove = event.target.closest('[data-delete-reminder]');
    if (remove) deleteReminder(remove.dataset.deleteReminder);
  });
  // 搜索现在是**主线视图**：rail 头下方的 `.search-shell` 入口与 `⌘K` 面板里的「搜索」都进同一个视图。
  $('#btnSearch').addEventListener('click', showSearchView);
  bindSearchEvents();
  bindCmdEvents();
  // ── 成员名册：频道头部成员入口 + 弹窗（**唯一动作是关闭**）──
  $('#btnChannelMembers').addEventListener('click', openRosterModal);
  $('#rosterClose').addEventListener('click', closeRosterModal);
  $('#rosterDone').addEventListener('click', closeRosterModal);
  $('#rosterOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'rosterOverlay') closeRosterModal();
  });
  // ── 频道回应预算（#54）：频道头入口 + 频道设置弹窗（只读读数 + 3 / 5 / 10 上界档位）──
  // 正文（读数 / 待生效 / 写控件）是**整段重铺**的，因此点击委派到 `#budgetBody` 上（一个监听器），
  // 与成员列表 / 提醒列表同一做法：重铺不会把监听器一起换掉。
  $('#btnChannelBudget').addEventListener('click', openBudgetModal);
  $('#budgetClose').addEventListener('click', closeBudgetModal);
  $('#budgetDone').addEventListener('click', closeBudgetModal);
  $('#budgetOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'budgetOverlay') closeBudgetModal();
  });
  $('#budgetBody').addEventListener('click', (event) => {
    const tier = event.target.closest('[data-budget-tier]');
    if (tier) {
      selectBudgetTier(tier.dataset.budgetTier);
      return;
    }
    if (event.target.closest('[data-budget-save]')) submitBudgetTier();
  });
  // ── 频道设置（#154）：频道头入口 + 弹窗（模式 / 队长 / 项目根 / 后端 / 权限档）──
  // 正文整段重铺，因此点击 / 选择 / 输入都委派到 `#channelConfigBody` 上（与预算 / 提醒同一做法）：
  // 重铺不会把监听器一起换掉。
  $('#btnChannelSettings').addEventListener('click', openChannelConfigModal);
  $('#channelConfigClose').addEventListener('click', closeChannelConfigModal);
  $('#channelConfigDone').addEventListener('click', closeChannelConfigModal);
  $('#channelConfigOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'channelConfigOverlay') closeChannelConfigModal();
  });
  $('#channelConfigBody').addEventListener('click', (event) => {
    const mode = event.target.closest('[data-config-mode]');
    if (mode) {
      selectChannelConfigMode(mode.dataset.configMode);
      return;
    }
    const backend = event.target.closest('[data-config-backend]');
    if (backend) {
      selectChannelConfigBackend(backend.dataset.configBackend);
      return;
    }
    const permission = event.target.closest('[data-config-permission]');
    if (permission) {
      selectChannelConfigPermission(permission.dataset.configPermission);
      return;
    }
    const webAccess = event.target.closest('[data-config-web-access]');
    if (webAccess) {
      selectChannelConfigWebAccess(webAccess.dataset.configWebAccess);
      return;
    }
    const webSave = event.target.closest('[data-web-save]');
    if (webSave) {
      saveWebProviderKey(webSave.dataset.webSave);
      return;
    }
    const webDefault = event.target.closest('[data-web-default]');
    if (webDefault) {
      setDefaultWebProvider(webDefault.dataset.webDefault);
      return;
    }
    const webClear = event.target.closest('[data-web-clear]');
    if (webClear) {
      clearWebProvider(webClear.dataset.webClear);
      return;
    }
    if (event.target.closest('[data-config-save]')) {
      submitChannelConfig();
      return;
    }
  });
  $('#channelConfigBody').addEventListener('change', (event) => {
    const leader = event.target.closest('[data-config-leader]');
    if (!leader) return;
    state.channelConfig.draft.leader = leader.value;
    renderChannelConfigModal();
  });
  // 项目根是自由输入：只更新**未提交的呈现状态**，不逐字读后端（提交时由后端整体校验）。
  $('#channelConfigBody').addEventListener('input', (event) => {
    const root = event.target.closest('[data-config-workspace-root]');
    if (root) {
      state.channelConfig.draft.workspaceRoot = root.value;
      return;
    }
    const key = event.target.closest('[data-web-key]');
    if (key) state.webProviders.draftKeys[key.dataset.webKey] = key.value;
  });
  // ── 运行面板（#155）：频道头入口 + 任务线程头入口 + 刷新 / 关闭 / 错误重试 ──
  // 正文整段重铺，因此重试按钮委派到 `#rpBody` 上（与预算 / 设置同一做法）。
  $('#btnRunPanel').addEventListener('click', openRunPanelForChannel);
  $('#btnThreadRunPanel').addEventListener('click', openRunPanelForTask);
  $('#rpRefresh').addEventListener('click', () => loadRunPanelSnapshot());
  $('#rpClose').addEventListener('click', closeRunPanel);
  $('#rpBody').addEventListener('click', (event) => {
    if (event.target.closest('[data-run-retry]')) loadRunPanelSnapshot();
  });
  // ── 审批卡（#153）：频道消息流与任务线程两个容器共用同一套动作委派 ──
  // 卡片每次重铺，监听器挂在容器上；动作只认 pending（`submitApproval` 里还有一道闸门）。
  ['#approvalBoard', '#ttApprovals'].forEach((selector) => {
    $(selector).addEventListener('click', (event) => {
      const action = event.target.closest('[data-approval-action]');
      if (!action) return;
      submitApproval(
        action.dataset.approvalTarget,
        action.dataset.approvalReply,
        action.dataset.approvalAction === 'allow',
      );
    });
  });
  $('#btnNewChannel').addEventListener('click', openChannelModal);
  $('#btnNewAgent').addEventListener('click', openAgentModal);
  $('#agentCancel').addEventListener('click', closeAgentModal);
  $('#agentModalClose').addEventListener('click', closeAgentModal);
  $('#agentCreateSubmit').addEventListener('click', submitAgent);
  $('#agentRemoteConfirm').addEventListener('click', () => {
    confirmRemoteFlow(currentProvider(), $('#agentRemoteConfirm'), renderRemoteNotice);
  });
  // 只监听 change 不够：重新点选「已经选中的那一项」不派发 change，而 hydration 期间那一项还只是
  // 占位默认值，清单到齐后的重建正好会把它换成完整清单首项 —— 静默把本机模型换成远端模型。
  // 因此指针按下与键盘操作也记为「选择出自人的手」。
  $('#agentProviderSelect').addEventListener('pointerdown', markProviderTouched);
  $('#agentProviderSelect').addEventListener('keydown', markProviderTouched);
  $('#agentProviderSelect').addEventListener('change', () => {
    markProviderTouched();
    updateProviderHint();
    renderRemoteNotice();
  });
  $('#agentOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'agentOverlay') closeAgentModal();
  });

  // 删除确认弹窗：只有「确认删除」发请求；取消 / 关闭图标 / 点遮罩 / Escape 只收起弹窗。
  // 请求在途时这三个出口都不动作（按钮同时被禁用），于是「失败时弹窗保持打开」不会被误关掉。
  const closeAgentDelete = () => {
    if (state.agentDelete.busy) return;
    closeAgentDeleteModal();
  };
  $('#agentDeleteConfirm').addEventListener('click', () => {
    if (!state.agentDelete.memberId) return;
    deleteAgent(state.agentDelete.memberId);
  });
  $('#agentDeleteCancel').addEventListener('click', closeAgentDelete);
  $('#agentDeleteClose').addEventListener('click', closeAgentDelete);
  $('#agentDeleteOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'agentDeleteOverlay') closeAgentDelete();
  });

  // ── Agent 详情（主线视图 `state.mode === 'agent'`）：就地编辑 + 底部保存条 ──
  // 可编辑字段由 `renderAgentDetail()` 每次重建，因此这里用**委派**而不是逐个绑事件。
  $('#adSave').addEventListener('click', submitAgentEdit);
  $('#adDiscard').addEventListener('click', discardAgentEdit);
  $('#adBody').addEventListener('input', (event) => {
    // 能力段的输入框：只写**未提交**的镜像（提交时才走后端）。
    const field = event.target.closest('[data-mcp-field]');
    if (field) {
      updateMcpDraftField(field);
      return;
    }
    const source = event.target.closest('[data-skill-source]');
    if (source) {
      const view = activeCapabilitiesView();
      if (view) view.skillSourcePath = source.value;
      return;
    }
    const control = event.target.closest('[data-agent-field]');
    if (!control) return;
    const name = control.dataset.agentField;
    const value = control.type === 'checkbox'
      ? (control.checked ? 'reme' : 'off')
      : control.value;
    setAgentDraft(name, value);
    paintAgentDraftControls();
    if (name === 'provider') syncAgentProviderHint(control.value);
  });
  $('#adBody').addEventListener('change', (event) => {
    // 能力段（#273）：两条清单的写动作与表单都委派到这里（正文整段由 `renderAgentDetail()`
    // 或 `paintAgentCapabilities()` 重铺，逐个绑事件会随节点一起丢）。
    const zip = event.target.closest('[data-skill-zip]');
    if (zip) {
      uploadSkillZip(zip.files);
      return;
    }
    const pick = event.target.closest('[data-skill-import-pick]');
    if (pick) {
      toggleSkillImportPick(pick);
      return;
    }
    const field = event.target.closest('[data-mcp-field]');
    if (field) {
      updateMcpDraftField(field);
      return;
    }
    const enabled = event.target.closest('[data-mcp-enabled]');
    if (enabled) {
      const view = activeCapabilitiesView();
      if (view) view.mcpDraft.enabled = Boolean(enabled.checked);
      return;
    }
    const control = event.target.closest('[data-agent-field]');
    if (!control) return;
    const name = control.dataset.agentField;
    const value = control.type === 'checkbox'
      ? (control.checked ? 'reme' : 'off')
      : control.value;
    setAgentDraft(name, value);
    paintAgentDraftControls();
    // 切 provider 时告知块要跟着重绘（远端未确认 ⇒ 可见）。
    if (name === 'provider') {
      syncAgentProviderHint(control.value);
      renderAgentEditNotice(control.value);
    }
  });
  // 页签：原生 <button> 的 click 就够了（键盘的 Tab / Enter 由浏览器自己的按钮行为提供，
  // 不自造方向键漫游）。切页签只改呈现状态与两个节点属性，**不重铺**面板、不丢草稿（D1）。
  $('#adTabs').addEventListener('click', (event) => {
    const tab = event.target.closest('[data-adtab]');
    if (tab) setAgentDetailTab(tab.dataset.adtab);
  });
  $('#adBody').addEventListener('click', (event) => {
    const toggle = event.target.closest('[data-agent-toggle]');
    if (toggle) {
      // 「暂停 / 恢复」是草稿里 `enabled` 的**唯一**界面入口：点它只改草稿，保存条提交。
      const draft = state.agentEdit.draft;
      if (draft) setAgentDraft('enabled', !draft.enabled);
      paintAgentDraftControls();
      return;
    }
    const remove = event.target.closest('[data-agent-delete]');
    if (remove) {
      // 第一次点击**只**打开确认弹窗：不发出任何请求，确认动作在弹窗里。
      if (!state.online || state.agentDelete.busy) return;
      openAgentDeleteModal();
      return;
    }
    const tier = event.target.closest('[data-tier-value]');
    if (tier) {
      const group = tier.closest('[data-agent-tier]');
      if (group) setAgentDraft(group.dataset.agentTier, tier.dataset.tierValue === 'on');
      paintAgentDraftControls();
      return;
    }
    const access = event.target.closest('[data-agent-access]');
    if (access) {
      setAgentDraft('web_access', access.dataset.agentAccess);
      paintAgentDraftControls();
      return;
    }
    const consent = event.target.closest('[data-global-mcp-consent]');
    if (consent) {
      confirmGlobalMcpServer(consent.dataset.globalMcpConsent);
      return;
    }
    // ── 能力段（#273）：MCP / 技能各自的写动作；每条只重读自己的清单 ──
    const mcpEdit = event.target.closest('[data-mcp-edit]');
    if (mcpEdit) {
      beginMcpEdit(mcpEdit.dataset.mcpEdit);
      return;
    }
    if (event.target.closest('[data-mcp-cancel-edit]')) {
      cancelMcpEdit();
      return;
    }
    const mcpToggle = event.target.closest('[data-mcp-toggle]');
    if (mcpToggle) {
      toggleMcpServer(mcpToggle.dataset.mcpToggle);
      return;
    }
    const mcpConsent = event.target.closest('[data-mcp-consent]');
    if (mcpConsent) {
      consentMcpServer(mcpConsent.dataset.mcpConsent);
      return;
    }
    const mcpDelete = event.target.closest('[data-mcp-delete]');
    if (mcpDelete) {
      deleteMcpServer(mcpDelete.dataset.mcpDelete);
      return;
    }
    if (event.target.closest('[data-mcp-save]')) {
      submitMcpDraft();
      return;
    }
    if (event.target.closest('[data-skill-install-dir]')) {
      submitSkillDirectory();
      return;
    }
    if (event.target.closest('[data-skill-import-scan]')) {
      const view = activeCapabilitiesView();
      if (view && !view.skillImportLoading) loadSkillImportCandidates(view.memberId);
      return;
    }
    if (event.target.closest('[data-skill-import-run]')) {
      submitSkillImport();
      return;
    }
    const skillToggle = event.target.closest('[data-skill-toggle]');
    if (skillToggle) {
      toggleSkill(skillToggle.dataset.skillToggle);
      return;
    }
    const skillDelete = event.target.closest('[data-skill-delete]');
    if (skillDelete) {
      deleteSkill(skillDelete.dataset.skillDelete);
      return;
    }
    if (event.target.closest('[data-agent-remote-confirm]')) {
      confirmAgentEditRemoteFlow();
    }
  });
  $('#channelCancel').addEventListener('click', closeChannelModal);
  $('#channelModalClose').addEventListener('click', closeChannelModal);
  $('#channelCreateSubmit').addEventListener('click', submitChannel);
  $('#channelOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'channelOverlay') closeChannelModal();
  });
  $('#channelNameInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); submitChannel(); }
  });
  $('#memberPicker').addEventListener('click', (event) => {
    const option = event.target.closest('.member-opt');
    if (!option) return;
    const on = option.classList.toggle('is-on');
    option.setAttribute('aria-pressed', on ? 'true' : 'false');
  });

  $('#channelList').addEventListener('click', (event) => {
    const row = event.target.closest('[data-channel]');
    if (!row) return;
    selectTarget(row.dataset.channel);
    // 挑完目标就把左抽屉收起来：抽屉盖着刚选中的那块内容，留着它等于把结果挡住
    // （桌面上这个动作是空转 —— 抽屉在那里根本不存在，`closeRail()` 是幂等的）。
    closeRail();
  });
  /** 两段私信共用同一条入口行为：行身份是 `data-dm`，归档只改变分段与可写性，不改变「点开读历史」。 */
  const openDmRow = (event) => {
    const row = event.target.closest('[data-dm]');
    if (!row) return;
    selectTarget(row.dataset.dm);
    closeRail();
  };
  $('#dmList').addEventListener('click', openDmRow);
  $('#dmArchiveList').addEventListener('click', openDmRow);
  // 「已归档」标题只切换**呈现状态**：展开 / 收起都不重取 `GET /api/dms`、不碰已有行节点
  // （同一 `data-dm` 行在两种状态之间始终是同一个节点）。
  const toggleDmArchive = $('#btnDmArchiveToggle');
  if (toggleDmArchive) {
    toggleDmArchive.addEventListener('click', () => {
      state.dmArchiveExpanded = !state.dmArchiveExpanded;
      renderDmArchiveSection(state.dms.some(dmIsArchived));
    });
  }
  // 左栏 Agent 行：打开**整页详情**（主线视图 `state.mode === 'agent'`）。行是 `<button>`，
  // 因此键盘也走同一条路径。
  $('#memberList').addEventListener('click', (event) => {
    const row = event.target.closest('[data-member]');
    if (row) openAgentDetail(row.dataset.member);
  });

  // 「发送消息」= DM 的创建 / 复用入口（分组 `+` 与弹窗删除后，这里是人能到达 DM 的入口）。
  $('#btnAgentSendMessage').addEventListener('click', messageActiveAgent);

  // 页签：两个页签在同一内容区切换（任务的唯一入口）。
  // 任务页签带 `fromChannelTab`：进入看板时默认筛到**当前频道**（左栏/全工作区视角仍看全部）。
  $('#tabMessages').addEventListener('click', showMessagesView);
  $('#tabTasks').addEventListener('click', showTaskBoard);

  // `.seg`：看板 / 列表（同一批任务的两种呈现，不重取）。
  document.querySelectorAll('.seg [data-taskview]').forEach((button) => {
    button.addEventListener('click', () => {
      state.taskView = button.dataset.taskview;
      syncTaskViewSeg();
      renderTaskBoard();
    });
  });
  // 看板筛选 chips（一次委派服务看板与列表两个视图）。
  $('#boardFilters').addEventListener('click', (event) => {
    const mine = event.target.closest('[data-board-mine]');
    if (mine) {
      state.taskFilterMine = !state.taskFilterMine;
      renderTaskBoard();
      return;
    }
    const channel = event.target.closest('[data-board-channel]');
    if (!channel) return;
    state.taskFilterChannelId = channel.dataset.boardChannel;
    refreshTasks();
  });
  // 卡片与列表行都打开同一个 dock 线程。卡片（#191）根节点是 `<div role="button">`：
  // ① 右上角的「流转」菜单按钮是卡片内控件，先于卡片判定，点它不打开线程；
  // ② 拖拽与菜单交互都不该被当成「点卡片」。
  ['#taskColumns', '#taskListWrap'].forEach((selector) => {
    $(selector).addEventListener('click', (event) => {
      // 刚结束一次拖拽：内核补发的那个 click 不是「打开线程」的意思。
      if (Date.now() < boardDrag.suppressClickUntil) return;
      if (event.target.closest('[data-task-menu]')) return;
      const card = event.target.closest('[data-task]');
      if (card) openTask(card.dataset.task);
    });
  });
  // 卡片是 div（不是原生 button）：Enter / Space 打开线程的键盘语义要显式补上。
  $('#taskColumns').addEventListener('keydown', (event) => {
    if (!isActivationKey(event)) return;
    const card = event.target.closest('[data-task]');
    if (!card || event.target.closest('[data-task-menu]')) return;
    if (event.preventDefault) event.preventDefault();
    openTask(card.dataset.task);
  });
  // 拖拽（#191）：五个事件都在看板容器上委派——列与卡片每次重铺都是新节点。
  const columns = $('#taskColumns');
  columns.addEventListener('dragstart', onBoardDragStart);
  columns.addEventListener('dragover', onBoardDragOver);
  columns.addEventListener('dragleave', onBoardDragLeave);
  columns.addEventListener('drop', onBoardDrop);
  columns.addEventListener('dragend', onBoardDragEnd);
  // 「流转」菜单：共享浮层自己的键盘面 + 点外部 / 滚看板时收起。
  $('#taskMoveMenu').addEventListener('keydown', onTaskMoveMenuKeydown);
  document.addEventListener('click', onDocumentClickCloseTaskMoveMenu);
  $('#boardScroll').addEventListener('scroll', () => closeTaskMoveMenu(false));

  // 新建任务：弹窗形态（标题 + 所属频道）。
  $('#btnNewTask').addEventListener('click', () => openTaskModal());
  $('#taskCreateCancel').addEventListener('click', closeTaskModal);
  $('#taskModalClose').addEventListener('click', closeTaskModal);
  $('#taskCreateSubmit').addEventListener('click', submitTask);
  $('#taskOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'taskOverlay') closeTaskModal();
  });
  $('#taskCreateTitle').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); submitTask(); }
  });

  // dock 任务线程：标题就地编辑（Enter 保存 / Esc 取消 / 失焦保存）、迁移与指派 chips、回复。
  const titleInput = $('#ttTitleInput');
  titleInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); titleInput.blur(); }
    if (event.key === 'Escape') {
      event.preventDefault();
      if (state.activeTask) titleInput.value = state.activeTask.title;
      titleInput.blur();
    }
  });
  titleInput.addEventListener('blur', () => { saveTaskTitle(); });
  $('#ttTransitions').addEventListener('click', (event) => {
    const chip = event.target.closest('[data-tt-move]');
    if (chip && state.activeTask) changeTaskStatus(state.activeTask.id, chip.dataset.ttMove);
  });
  $('#ttAssign').addEventListener('click', (event) => {
    const chip = event.target.closest('[data-tt-assign]');
    if (chip) changeTaskAssignee(chip.dataset.ttAssign);
  });
  $('#ttReplies').addEventListener('click', (event) => {
    const link = event.target.closest('[data-open-task]');
    if (link) openTask(link.dataset.openTask);
  });
  $('#btnThreadClose').addEventListener('click', () => {
    closeTaskDock();
    renderDock();
    renderTaskBoard();
    renderTabs();
  });

  $('#taskSend').addEventListener('click', sendTaskMessage);
  $('#taskComposerInput').addEventListener('input', () => {
    const input = $('#taskComposerInput');
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
    $('#taskSend').disabled = !input.value.trim() || !state.online;
  });
  $('#taskComposerInput').addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendTaskMessage();
    }
  });

  $('#composerInput').addEventListener('input', () => {
    autosizeComposer();
    syncSendButton();
    updateMentionPicker();
  });
  // 光标移动不产生 input 事件：点一下、按左右/Home/End 都要重判触发点。
  $('#composerInput').addEventListener('click', () => updateMentionPicker());
  $('#composerInput').addEventListener('keyup', (event) => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight' || event.key === 'Home' || event.key === 'End') {
      updateMentionPicker();
    }
  });
  $('#composerInput').addEventListener('blur', () => closeMentionPicker());
  // 候选清单是 composer 自己的关注点，因此它的刷新也挂在 composer 上：每次获得焦点重新读一遍
  // 该频道的成员表（穿透加入这类成员变化就会在下一次落笔前被看到），读完再按当前光标重判。
  $('#composerInput').addEventListener('focus', () => {
    refreshChannelMembers().then(updateMentionPicker);
  });
  $('#composerInput').addEventListener('keydown', (event) => {
    // 输入法组合中的按键一律不接管（含 Enter 不算发送）。
    if (event.isComposing) return;
    if (mentionPicker.open && mentionPicker.items.length) {
      // 清单打开且有候选：↑/↓ 移动高亮、Enter/Tab 选中。
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        moveMentionHighlight(event.key === 'ArrowDown' ? 1 : -1);
        return;
      }
      if ((event.key === 'Enter' && !event.shiftKey) || event.key === 'Tab') {
        event.preventDefault();
        chooseMention(mentionPicker.items[mentionPicker.activeIndex]);
        return;
      }
    }
    if (event.key === 'Escape' && mentionPicker.open) {
      event.preventDefault();
      dismissMentionPicker();
      return;
    }
    // Enter 发送、Shift+Enter 换行（清单关闭时与补全之前完全一致）。
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
  // 鼠标选择：mousedown 时拦一下，textarea 不失焦（插入要用它当前的选区）。
  $('#composerMention').addEventListener('mousedown', (event) => {
    const row = event.target.closest('[data-mention]');
    if (!row) return;
    event.preventDefault();
    const member = mentionPicker.items.find((item) => item.id === row.dataset.mention);
    chooseMention(member);
  });
  $('#btnSend').addEventListener('click', sendMessage);
  // composer 工具行：附件（两步上传）→ @提及（复用既有补全）→ 作为任务发送（D6）。
  $('#btnAttach').addEventListener('click', () => {
    const input = $('#attachInput');
    if (input && typeof input.click === 'function') input.click();
  });
  $('#attachInput').addEventListener('change', (event) => {
    const input = event.target;
    attachFiles(input && input.files);
    // 选完之后把控件清空：同一个文件再选一次也应当触发一次新的 `change`。
    if (input) input.value = '';
  });
  $('#composerAttachments').addEventListener('click', (event) => {
    const remove = event.target.closest('[data-remove-attachment]');
    if (remove) removeComposerAttachment(remove.dataset.removeAttachment);
  });
  $('#btnMention').addEventListener('click', mentionFromToolbar);
  $('#btnAsTask').addEventListener('click', openTaskFromComposer);

  // 成员面板里的「加入」：一个控件一个成员，点击后把它加入当前频道。
  $('#dockMembers').addEventListener('click', (event) => {
    const button = event.target.closest('[data-join]');
    if (button) {
      joinChannelMember(button.dataset.join);
      return;
    }
    // 成员面板里的 Agent 也是打开详情的入口（名字是控件；点卡片其余部分同样打开，
    // 但「加入」按钮先判——它自己的动作不能被详情吞掉）。
    const open = event.target.closest('[data-agent-open]') || event.target.closest('[data-member]');
    if (open) openAgentDetail(open.dataset.agentOpen || open.dataset.member);
  });
  // ≤900px 抽屉自己的出口（`>900px` 时那个控件 `display:none`，所以这里在桌面是空转）。
  $('#dock').addEventListener('click', (event) => {
    if (event.target.closest('[data-dock-close]')) setDockOpen(false);
  });

  $('#messageList').addEventListener('click', (event) => {
    const taskRef = event.target.closest('[data-open-task]');
    if (taskRef) {
      openTask(taskRef.dataset.openTask);
      return;
    }
    // 旁白行的折叠控件：与摘要行同一个纪律（只切**呈现状态**、重建后把焦点还回去），
    // 但键是旁白那条消息**自己的** seq（摘要行的键是触发消息的 base_seq，两者会撞号）。
    const narration = event.target.closest('[data-narration-toggle]');
    if (narration) {
      const seq = narration.dataset.narrationToggle;
      if (state.expandedNarrations[seq]) delete state.expandedNarrations[seq];
      else state.expandedNarrations[seq] = true;
      renderMessages();
      const next = $('#messageList').querySelector('[data-narration-toggle="' + seq + '"]');
      if (next) next.focus();
      return;
    }
    // 摘要行的折叠控件：切换的只是**呈现状态**（内存），后端事实一个字不动。
    const summary = event.target.closest('[data-summary-toggle]');
    if (summary) {
      const baseSeq = summary.dataset.summaryToggle;
      const wasExpanded = Boolean(state.expandedOutcomes[baseSeq]);
      if (wasExpanded) delete state.expandedOutcomes[baseSeq];
      else state.expandedOutcomes[baseSeq] = true;
      // 重建整条消息流（消息节点无状态，重建是既有做法）。重建会把刚被点击的按钮换成新节点，
      // 键盘焦点会落到 body；因此重建后把焦点还给**同一个控件**的新节点，否则「Tab 到它、
      // Enter 展开」之后就再也无法用键盘收起（焦点已丢）。
      renderMessages();
      const next = $('#messageList').querySelector('[data-summary-toggle="' + baseSeq + '"]');
      if (next) next.focus();
      return;
    }
    const inspect = event.target.closest('[data-inspect-member]');
    if (inspect) {
      // 从失败现场打开**该成员**的详情：dock 停在成员列表 / 别的成员详情 / 任务线程，
      // 或者已经收起，都切过去（`openAgentDetail` 负责收任务流、切形态并压过 dismissed）。
      // 运行面板不开在 `openAgentDetail` 的收口里，且 `render()` 的形态推导以它为先：
      // 不先收掉它，下一次整体重绘就会把刚打开的详情又盖回 run 面板。
      if (state.runPanel.open) closeRunPanel();
      openAgentDetail(inspect.dataset.inspectMember);
      return;
    }
    const retry = event.target.closest('[data-retry]');
    if (retry) {
      const pending = state.pending.find((item) => item.clientId === retry.dataset.retry);
      if (pending) {
        pending.status = 'pending';
        pending.reason = null;
        renderMessages();
        deliver(pending);
      }
      return;
    }
    const discard = event.target.closest('[data-discard]');
    if (discard) discardPending(discard.dataset.discard);
  });

  $('#btnDockToggle').addEventListener('click', toggleDock);
  // 移动端壳（≤900px）：顶部栏按钮开合左抽屉，点遮罩收起。
  // 同一个按钮承担开与合（`aria-expanded` 是它的状态），键盘用户因此有来有回。
  $('#railToggle').addEventListener('click', () => {
    if (railOpen()) closeRail();
    else openRail();
  });
  $('#railScrim').addEventListener('click', closeRail);
  $('#btnReconnect').addEventListener('click', () => loadWorkspace());

  // ── 全局键盘：⌘K / Ctrl+K 打开命令面板、ESC 关闭最上面那一层、M/T 两个单键跳转 ──
  // 顺序与原型 `:1847-1852` 一致：命令面板 → 弹窗 → 搜索视图；`M` / `T` 只在人不在输入框里时生效。
  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && String(event.key).toLowerCase() === 'k') {
      event.preventDefault();
      openCmd();
      return;
    }
    if (event.key === 'Escape') {
      if (!$('#cmdOverlay').hidden) {
        closeCmd();
        return;
      }
      // 删除确认弹窗是最上面的一层：请求在途时不收（与禁用按钮同一条判据）。
      if (!$('#agentDeleteOverlay').hidden) {
        if (state.agentDelete.busy) return;
        closeAgentDeleteModal();
        return;
      }
      if (!$('#settingsOverlay').hidden) closeSettingsModal();
      if (!$('#remindersOverlay').hidden) closeRemindersModal();
      if (!$('#rosterOverlay').hidden) closeRosterModal();
      if (!$('#budgetOverlay').hidden) closeBudgetModal();
      if (!$('#channelOverlay').hidden) closeChannelModal();
      if (!$('#agentOverlay').hidden) closeAgentModal();
      if (!$('#taskOverlay').hidden) closeTaskModal();
      // 移动端壳的左抽屉盖在内容上，因此 ESC 先收它（它比搜索视图更靠前），收到就到此为止。
      if (railOpen()) {
        closeRail();
        return;
      }
      // ≤900px 的右抽屉同样盖在内容上（它没有遮罩，因此 ESC 是它的键盘出口）。
      // 桌面 dock 是常驻列，ESC 不管它 —— 那会改掉既有行为。
      if (mobileShell() && !$('#dock').hidden) {
        setDockOpen(false);
        return;
      }
      if (state.mode === 'search') showMessagesView();
      return;
    }
    const target = event.target;
    if (target && typeof target.matches === 'function' && target.matches('input,textarea')) return;
    const key = String(event.key).toLowerCase();
    if (key === 'm') showMessagesView();
    if (key === 't') showTaskBoard();
  });

  window.addEventListener('online', () => loadWorkspace({ silent: true }));
  window.addEventListener('offline', () => { state.online = false; render(); });

  // #209：全量 pending 的周期刷新是单例定时器；只在页面可见且在线时发请求（见 startApprovalRefresh）。
  startApprovalRefresh();
}

/* ─── 启动 ──────────────────────────────────────────────────────────── */
// 静态文案先按**当前语言**铺一遍（此刻是源语言；`loadWorkspace` 读到后端偏好后会再铺一次），
// 于是「JS 还没跑完」的那一帧也不会有半份译文。
applyStaticI18n(document);
bindEvents();
loadWorkspace();
