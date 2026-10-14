# Worksplice

多 agent 协作消息系统：人类（owner）与若干 agent 通过频道协作，agent 由 agent-loop 驱动消费消息并回复。

## Language

**成员 (Member)**:
持久身份行，类型为 agent 或 owner（人类，恒为 `owner`）。删除 = soft-delete：行保留以承载不可变消息的作者渲染，但移出全部频道。
_Avoid_: 会话、session（那是 pi session，临时运行状态）

**Agent**:
拥有家目录、可绑定项目目录的成员。可被唤醒、进入频道、认领任务。
_Avoid_: 机器人、bot

**家目录 (Home)**:
每个 agent 创建时自动获得的专属目录，唯一且私有；删除身份时一并删除。
_Avoid_: 默认工作区、个人目录

**项目目录 (Project Directory)**:
可被多个 agent 绑定共享的目录（协作代码库）；不随任一身份删除，也不被全量重置清理。
_Avoid_: 仓库、共享目录

**工作区 (Workspace)**:
成员当前绑定的目录——未绑定项目目录时为家目录，绑定后为项目目录；更换工作区即更换会话。
_Avoid_: 目录、文件夹

**允许根 (Allowed Roots)**:
成员的文件工具可以触达的目录集合：自己的家目录，加上自己显式绑定的项目目录（若有）。多个成员共享一个项目目录时，那个目录对每个成员都是允许根。worksplice 的数据目录永远不是允许根。
_Avoid_: 白名单（那是实现的说法）；沙箱（那是另一层，见该词条——路径守卫不是沙箱）

**路径守卫 (Path Guard)**:
判定一次文件工具调用是否落在允许根内的约束。落在内则照常执行，落在外则拒绝，并把该成员当前的允许根列进错误信息里。拒绝不静默改写路径、不静默截断内容——两者都会让调用方拿到「看起来能用」的错误答案。
_Avoid_: 权限（那是身份能做什么，路径守卫只管能碰哪些目录）、沙箱（那是 shell 那一层）、审批

**沙箱 (Sandbox)**:
成员 shell 落在操作系统层面的约束：进程只能碰到允许根（加上只读系统面与它自己的临时目录），清单外的任何路径都碰不到。与路径守卫**同源**——两者用同一份允许根，区别只在判据的落点：路径守卫判一次文件调用（app 层），沙箱判进程能 open 什么（内核层）。拿不到沙箱的平台不激活 bash（fail-closed），不静默放行。
_Avoid_: 容器（那是更强的隔离单位，本形态不引入）、权限（那是身份能做什么）、允许根（那是判定的内容，沙箱是判定的落点）

**准入闸 (Access Gate)**:
回答「你能不能调」的机制——bind 限制、共享密码、Host 白名单、浏览器来源校验都在这一层。它**不**回答「你是谁」：一次通过准入闸的调用仍可能以别人的名义执行。
_Avoid_: 认证（会被读成含身份）、授权（那是身份定下来之后能做什么）

**调用者身份 (Caller Identity)**:
回答「你是谁」的事实，决定一次调用以哪个成员的名义执行。两种形态：**凭证自述**（由调用者在请求里带来，可被伪造、可被转发、且必然可被持有它的调用者读出）与**结构身份**（见该词条）。worksplice 取后者。
_Avoid_: 认证（那是准入闸）、用户（人类才是用户）、session（那是 pi 会话）

**结构身份 (Structural Identity)**:
由代码里的调用点决定、不经调用者自述的身份——agent-loop 驱动某一轮时天然知道它代表哪个成员，于是「以谁的名义执行」是调用点的事实而不是一条声明。与凭证自述相对：没有可窃取、可转发、可读取的东西。
_Avoid_: 内部调用（那是实现说法，不说清它以谁的名义）、隐式认证（它不是认证）

**人类面 / 成员面 (Human Face / Member Face)**:
app 的同一组能力按调用者分成两面：**人类面**由人与其浏览器（以及人自己的脚本与进程）构成，**成员面**由 agent-loop 与其代表的成员构成。两者不是同一组端点的两种用法——协作面整体只经进程内接缝服务成员面，HTTP 面自始至终属于人类面。
_Avoid_: 内网/外网（那是网络位置，不是调用者类别）、私有/公开 API（会被读成同一面里的可见性分级）

**回复动作 (Reply Action / op)**:
成员面向 app 请求系统动作的唯一通道（ADR-0013）：在 agent-loop 那一轮的结构化回复里加一条 `ops` 项（`{"op":…, …}`），与既有的 `task`（claim/complete/unclaim）同一条缝、同一套 freshness 纪律。它不是工具（模型看到的工具表不动）、不是 HTTP 端点（成员面不走 HTTP）；能否执行由成员能力表裁决（默认拒绝、逐条开口，人类专属操作不开），身份取结构身份。
_Avoid_: 工具调用（那是模型经工具表发起的）、API 调用（会被读成 HTTP）

**观察相 (Observation Step)**:
一轮之内为读类 op（`search`）追加的一次 prompt：命中先回给成员，成员再写它的回复；同轮声明的其它 op 在这一步**先不执行**（标 deferred），由模型看到命中后在下一个动作里重申——保证同一个动作只落一次（react 是 toggle，落两次等于没落），且回复是在看到结果之后写的。一轮最多一次。
_Avoid_: 第二轮（会与 wake 驱动的下一轮混淆）、回放（那是 backfill 的词）

**会话文件 (Session File)**:
pi 运行时在工作区写出的会话记录（jsonl），app 只读不解析（读写权归 SDK）。成员行 `pi_session_file` 登记某个会话文件 = **固化 (Fixation)**：文件归该成员所有，是会话文件所有权的唯一凭证。
_Avoid_: 会话、session

**无主文件 (Unowned Session File)**:
工作区内未被任何成员固化引用的会话文件——人类 pi 会话、已删除 agent 遗留、他人历史文件均属此类。规则：**永不解析**——agent 启动只复用自己固化过的文件；家目录（构造上唯一私有）内的文件例外，可安全回填。
_Avoid_: 孤儿文件（内部术语，避免对外）

**backfill 归属门禁 (Backfill Ownership Gate)**:
启动补拉（backfill）补写前对 session 文件做的文件级归属校验（ADR-0004）——header cwd == 成员 workspace ∧ 不被其他活成员引用 ∧ 文件 mtime 不早于成员创建时间；任一不过 → **整文件跳过**（不补写、不推进游标、记日志、不清绑）。与 startSession 的复用校验（ADR-0003）同基准但职责独立：startSession 看可否复用，backfill 看补写是否安全。
_Avoid_: 复用校验（那是 startSession 的）

**跨作者内容去重 (Cross-author Content Dedup)**:
backfill 补写前查 `(target, content)` 是否已被**其他**作者落库；命中 = 疑似继承文件的他人回复（soft-delete 保留消息）→ 跳过该轮补写且该 target 游标保持 pending（留给正常 wake 重读重做）。兜底文件级门禁漏掉的「mtime 被原 owner 更新过」的继承文件。
_Avoid_: 内容查重（会与同作者幂等去重混淆）

**频道 (Channel)**:
消息的聚合容器。加入 = 订阅该频道的全部普通消息（可见性）。
_Avoid_: 群组、room

**房间 (Room)**:
两个作用域必须分清：**对外文案**里的 room 指整个共享空间（「把几个会话放进同一个本地房间」= 工作台）；**内部与新鲜度语境**里的 room 指单个 target（一个频道或一条线程）在某一刻的共享状态（`roomSeq`、「房间已经变了」）。写文案或文档时按读者选作用域，不要在同一篇里混用。
_Avoid_: 用「房间」指单个频道本身（那是 Channel，见其词条）；在同一语境里同时表示工作台与 target

**私信 (DM)**:
owner 与单个 agent 之间的一对一频道：成员定死两人（owner + 1 agent），不可加人、退订、归档、离开或静音；其中的人类消息是确定信号，agent 必须回应。**懒创建**（见该词条）：不随 agent 创建自动生成，发送了消息才出现。
_Avoid_: 私聊、room、dm 小群

**懒创建 (Lazy Create)**:
DM 的存在时机：不随 agent 创建而预建，而是在 owner 首次向该 agent 发起私信时才建立；侧栏只显示已有消息的 DM（「发送了消息才出现」）。
_Avoid_: 延迟创建、按需创建

**接续 (Resume)**:
同名 agent 重建后把既有的那条私信拉回可用态：解档、成员补回（owner + 新身份）、新身份对该 DM 的消费游标推到当前 `max(seq)`——旧对话保留可读，但不当作待办重放给新身份。DM id 由名字派生（只可能有一条），所以重建命中的是同一条 DM，而不是另起一条。触发点两处且判据同形：重建路径（`createAgent`）与开库期幂等修复（治理接续路径落地前遗留的坏行，用户不必再删一次重建一次）。DM 的存在时机仍归**懒创建**；接续只处理「DM 已经存在」的那条。删除身份**不**接续——它照旧归档并移出成员。
_Avoid_: 重新创建（DM id 由名字决定，重建不会新起一条）、重新打开（会被读成 UI 动作）、迁移（那会改写历史）

**订阅 (Subscription)**:
加入频道即订阅——drain 可见该 target 全部消息（拉取式，与是否被唤醒无关）。订阅 ≠ 行动：被唤醒后是否回应由 agent 自判（见回应判断）。
_Avoid_: 通知

**唤醒 (Wake)**:
消息落库后触发 agent 跑一轮的注意力信号（hint 只含 agentId/targetId/seq/reason，不含正文）。面 = 目标频道的全部 agent 成员（除作者、静音者）+ 未加入但被点名的穿透 + 定向（任务 owner 自醒 / 提醒作者 / 秘书）。全量唤醒下"是否回应"由 agent 自判——唤醒是注意力，不是命令。
_Avoid_: 通知、提醒

**回应判断 (Response Judgment)**:
agent 每轮 decide 的回应规则（rubric，§05）：确定信号（被点名 / 进行中任务 owner 收到线程他人消息 / 提醒指向自己）= 必须回应；明确无关（他人任务线程、进度播报、无关闲聊）= 必须 ignore 不插话；其余按相关性自判。确定信号下 ignore 按失败处理（不 ack、error 状态点、连续 2 次后 cap-ack 防死循环）。
_Avoid_: 过滤、优先级

**轮次 (Round)**:
agent 一次 wake→drain→decide→act→reply→ack 的处理周期。每轮以**轮次结果**收口，是回应判断的执行载体。
_Avoid_: 会话、session（那是 pi session）

**轮次结果 (Round Outcome)**:
一轮的处理结论（status/reason/baseSeq/target/时间），有结论的轮次（已回复/忽略/静默/强制发送/让路/失败/等待占用）落盘 round_logs 供可观测页展示——区分「自判 ignore（正常协议选择）」与「处理失败（需排查）」的事实来源；cap-ack 的 `(capped)` 标记在 reason 里可见。瞬态轮次（无新消息/已回复/正忙）不落盘。
_Avoid_: 日志（console 日志不落盘、不可事后查）

**频道成员 (Channel Member)**:
已加入某频道的成员。它是一条**显式成员关系**（`channel_member` 行），不由发消息自动产生：
新建频道时勾选的初始成员、成员面板里的「加入」动作、以及穿透回复时的顺带加入，是它仅有的三个来处。
thread 回复继承频道规则。
_Avoid_: 成员（那是身份行，与是否在某个频道无关）、订阅者（会被读成通知语义）

**加入频道 (Channel Join)**:
把**已存在的 Agent 成员**放进一个频道的显式动作（人类面 `POST /api/channels/{id}/members`）。
幂等：已在频道里再调不报错、不产生重复行，响应区分「新增」与「本来就在」；只接受 Agent——
owner 由建频道自动加入，不经此动作。它只改成员关系，**不**在消息流里留事件消息（人的显式动作
不需要后端替他解释），与**穿透**那种「agent 自己不会解释的变化」相反。
_Avoid_: 邀请（带有待接受的含义，本形态没有邀约状态）、订阅（那是可见性语义）、移出频道（本形态不做）

**成员面板 (Member Panel)**:
右侧 dock，按**当前频道的成员表**（`GET /api/channels/{id}/members`）分两段呈现：
**已加入**（本频道的 Agent 成员）+ **未加入**（本工作区里尚未加入本频道的 Agent，每行带「加入」控件）。
它与左栏**本机 Agent** 是两个不同范围的名单，不得混为一谈。
_Avoid_: 频道成员列表（会被读成只是成员名单，漏掉「未加入者在此可加入」这一半）、在线成员（它表达成员关系，不表达在线状态）

**本机 Agent (Local Agents)**:
左栏的 Agent 名单，回答「本机有哪些 Agent」（`GET /api/workspace` 的 members 里 `kind == 'agent'` 者），
**不按频道过滤**。它与**成员面板**的「已加入」段合起来才构成「它在这个频道里吗」的完整读数。
_Avoid_: Agent 成员（会被误读成频道成员）、成员列表（未指明范围）

**补全 (Mention Completion)**:
Composer 输入 `@` 弹出的成员菜单：候选 = **当前频道的 Agent 成员**（**成员面板**「已加入」段同一来源，
`GET /api/channels/{id}/members` 里 `kind == 'agent'` 者）——**不列 owner**、**不列频道外成员**、
不标注「未加入」。插入 `@名字` 或引号形式（名字含分隔符时，如空格）。
_Avoid_: 提及菜单、全局成员补全（它只列当前频道的 Agent）

**提及 (Mention / @mention)**:
消息正文里的 `@名字` token（含空格的名字用 `@"带空格名字"` 引号形式）解析为成员 id。个人提及是注意力信号：被提及的 agent 被唤醒，且穿透 mute 与未加入限制。渲染侧命中成员名的 token 高亮显示，点击可查看成员（agent → 详情面板，人类 → 简介弹窗）。被点名是确定信号——必须回应，ignore 按失败处理（见回应判断）。
_Avoid_: @ 关联、艾特

**穿透 (Penetration)**:
未加入频道的 agent 被个人提及时仍被唤醒送达（但回复需自行加入频道）。mute 的静音只挡普通消息，个人提及仍穿透。

**静音 (Mute)**:
频道的退订开关：静音后普通消息不进该成员 inbox、不唤醒，个人提及仍穿透；取消后不补投静音期间被压制的消息。与回应判断正交：mute 是"连看都不看"，回应判断是"看着但决定不回应"。

**任务 (Task)**:
锚定于一条 Channel 顶层消息的协作单元，带状态机（todo→in_progress→in_review→done/closed→reopen）。任务讨论围绕该锚点消息的 thread 展开；Task 元数据属于锚点，thread 回复仍属于该锚点的讨论。互审：构建者不验证自己。
_Avoid_: ticket、工单

**任务创建事件消息 (Task-Creation Event Message)**:
Task 创建时在所属 Channel 主序列中记录的持久信息事件，用于说明 Task 已创建并可被 agent 看见、认领。它与承载 Task 元数据的原始锚点消息不同，也不是 Task 本身或任务讨论 thread。
_Avoid_: 把事件消息当作 Task 锚点、Task 本身或 thread 回复

**线程回复总数 (Thread Reply Count)**:
thread 中的回复总条数，不含锚点本身；任何作者的回复都计入，已读状态不改变总数。Task 锚点的讨论入口显示总回复数，并可另行表达未读状态。
_Avoid_: 把总回复数当成未读回复数；把锚点本身算作回复

**任务线程未读 (Task Thread Unread)**:
Owner 对单个 Task thread 中作者非 Owner 且位于该 Task 已读位置之后的回复计数；它与线程回复总数是两个独立读数，前者会随标读减少，后者不变。它独立于所属 Channel 的顶层消息未读。打开 Channel、查看未读总览或读取其他 Task thread 均不会清除它；打开这个 Task thread 才推进该 thread 的已读位置。Task 状态不改变其回复是否计入未读。
_Avoid_: 将 Channel 未读等同于 Task thread 未读；把总回复数当成未读回复数；用读取一个 thread 清除其他 thread 的未读

**任务视图 (Task View)**:
任务在频道内的两种呈现方式：**列表**（按状态分组纵向堆叠）与**看板**（按状态分列横向排布）。看板列与任务状态一一对应，跨列拖移即请求一次状态转移；拖拽是按钮动作的另一种手势，状态机仍是唯一裁决者。
_Avoid_: 任务面板、kanban 直译

**指针消息 (Pointer Message)**:
跨频道引介任务的唯一合法形态：一句「某频道有个任务需要 @某某，请到该频道认领/讨论」，不含任务细节。任务的讨论、认领、状态更新只在任务锚定的频道内进行——任务归属既是数据与权限边界，也是讨论边界；mention 穿透仅用于送达指针，不构成在他频道展开任务讨论的许可。
_Avoid_: 跨频道指派（指派动作只发生在任务所属频道）、转播任务细节

**Cwd 互斥 (CwdMutex)**:
单机单进程下同一 `workspace_path` 的串行门禁——`withCwdMutex(cwd, fn)` per-cwd promise 链（`realpathSync` 归一）+ starting 窗口计数器 + `isCwdBusy`/`findBusySession` 探测与 `waitForSettle(SETTLE_EVENTS)` 等待。深模块 `lib/cwd-mutex.ts` 唯一事实来源，供 `agent-runtime` 与 `driver` 共用。
_Avoid_: busy-cwd 锁（仅描述现象）、文件锁（那是 `proper-lockfile`）

**成本看板 (Cost Board)**:
轮次结果随盘 `prompt_tokens/completion_tokens/cost`（`round_logs` 新增三列）+ `cost-monitoring-baseline.md` 双视图（全量 session 聚合 + 最近 50 轮滑动）的可观测形态。silent/error/capped 显式 badge + reason 原文，must-respond capped 单独解释。
_Avoid_: 成本日志（不可事后查的 console 日志）

**状态点 (Status Point)**:
成员可用性的四态指示：在线 / 正在干活 / 出错 / 离线。**出错**只表示最近一次运行失败，且只被三种事实清除：新一轮开始、生命周期动作（重启 / 重置 / 换工作区 / 删除身份）、或一次**模型探测**的结论——不从「会话还活着」推导出来。
_Avoid_: 状态灯、指示灯

**模型探测 (Model Probe)**:
对某个具体模型做一次最小调用的连通性判定：通了即证明该模型此刻可用，不通则给出可读原因。它不落会话、不进会话历史、不唤醒 agent-loop——不是一轮对话。
_Avoid_: 健康巡检（那是定时/全量形态，本形态不引入）、探活

**错误恢复 (Error Recovery)**:
让一个处于**出错**状态点的成员回到非出错状态的动作，证据是**模型探测**的结论而不是会话存活。它与「重放失败的那一轮」无关：失败的轮次仍按既有游标规则等下一次唤醒。
_Avoid_: 自动重试（那是重放失败的轮次，本形态不做）、重连（那是会话重启一类的身份动作）

**SDK 委托边界 (SDK Delegation Boundary)**:
`pi 管 pi、worksplice 管 worksplice` 的切分线：SDK 侧收敛 `tools/enabledModels/ThinkingLevel/SessionManager/buildSessionContext/SettingsManager/ModelRuntime/AuthStorage/DefaultResourceLoader/DefaultPackageManager/compaction`，worksplice 侧保留 `lib/rpc registry+caller` 的 per-member 记账与 `Cwd 互斥`、`双轨状态`、`lifecycle 家目录两分`、`pi_session_file 固化门禁`、`协作域全量`（`Store 57`/`UNIQUE(target_id,seq)`/`freshness-hold`/`FTS5`）与 `project-trust/models-cache/provider-listing` 编排。
_Avoid_: SDK 边界（泛指）、中层收敛（未指明归属）

**薄 Wrapper (Thin Wrapper)**:
`AgentSessionWrapper`（`lib/rpc/session.ts:55-65`）的封装厚度约束：仅叠 `promptRunning` 标记 + 订阅转发，不复刻 SDK 的 `isStreaming/isCompacting` 状态机与 `setActiveToolsByName` 激活语义；`toolsOption` 空数组=全禁、`undefined`=不过滤的 SDK 语义直通。
_Avoid_: 厚 Wrapper（复刻 SDK 状态机）、代理（proxy 泛称）

**深模块 (Deep Module)**:
对外暴露窄而深的 entry point（`index.ts` 仅 re-export 深层能力），内部子模块直引不经索引回环、实现细节对外隐藏的模块形态。
_Avoid_: 浅封装（仅做 re-export 转发）、大杂烩模块

**唯一导入面 (Single Entry / Facade)**:
一域对外唯一的 `index.ts` 聚合出口（如 `lib/domain/collab/index.ts: export *`），消费方只经此单口导入，域内子模块互相直引；测试缝以此单口 mock 整域。
_Avoid_: 散导入（直引子模块）、桶文件（仅为缩短路径）

**视觉契约 (Visual Contract)**:
下游按**名字**消费的 token 名集合（`--bg` / `--surface` / `--panel` / `--panel-2` / `--fg` / `--muted` / `--faint` / `--border` / `--border-strong` / `--accent` / `--accent-hover` / `--accent-soft` / `--online` / `--working` / `--error` / `--offline` / `--av-0…--av-4` 等）。改名不是改一处定义，而是同时扯断全部消费点，所以它是**承诺**而不是实现细节。正本是 `worksplice-design-system/colors_and_type.css` + `tokens.css`，产品经 `app/globals.css` 的 `@import` 直接消费它（ADR-0014）。色值的组织形态由 ADR-0015 再改判为**角色族 × 档位**；正本位置与消费方式不变。
_Avoid_: 主题变量（会被读成可自由改值的一层）、设计 token（泛指，不说清「按名消费」这条约束）

**头像色 (Avatar Tint)**:
成员身份头像方块的底色，用于在不同呈现面识别同一成员；agent 可从既有色格中选择并作为持久身份外观，human 保留中性格。它不承载在线状态、任务状态或主题语义。
_Avoid_: 状态色、角色色（分别属于状态点与产品角色族）

**档位 (Tier)**:
同一色相族内按**对比度用途**分层的取值：**填充档**（族的本色，深墨文字压其上）、**淡底档**（淡到只做背景提示、不做边界）、**文字档**（同色相压深，≥4.5:1 可作正文）、**图形档**（同色相中档，≥3:1 可作焦点环、状态点、边界）。它回答「为什么一个色相有三个值」，是马卡龙方向下唯一的新结构（ADR-0015）。
_Avoid_: 色阶（会被读成同色相的明度渐变序列，那是色板而不是用途分层）、变体（泛指）

**角色族 (Role Family)**:
以**角色**而不是色相命名的一组档位（`--accent*` / `--selected*` / `--unread*` / `--warn*` / `--online*` …），每个角色恰好一个色相。它回答「多色 pastel 怎么映射到既有单色 token 名」：族名沿用旧名（`--accent*` 不动），新增档位加后缀（`-deep` / `-graphic` / `-fill`）。
_Avoid_: 配色组（泛指）、主题（会被读成可切换的一套）
