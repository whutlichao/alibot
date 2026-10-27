# 前端用 Vue 重构：分阶段迁移、Vite 构建产物入库、正本资产不复制

用户于 2026-10-26 人定：**用 Vue 重构前端**。当时的前端是 `app/alibot/studio/` 下的四个手写文件
（`app.js` 10,634 行 / `index.html` 880 行 / `app.css` 1,429 行 / `i18n.js` 1,809 行），零依赖、
零构建，由 24 个 `*.test.cjs`（389 个用例，`vm` + DOM 替身直接跑 `app.js`）覆盖。

本 ADR 只记录这个决定与它的边界，不记录实施步骤与逐条代码。

## 决策记录

- **Context**：三条既有事实构成约束。
  ① `app.js` 已经是 10k 行单文件，每张功能票都在同一份 `state` 与同一套「整体 `render()`」上叠加，
  改一处要读半页注释才能确认不会撞到别处；
  ② 「零第三方依赖 + 零构建」（ADR-0037 §二）曾是刻意选择，但它换来的代价是**每张票都要手写 DOM**，
  而手写 DOM 的呈现逻辑无法被复用、也无法被单测按组件切开；
  ③ 仓库的**视觉契约**只有一个正本（`alibot-design-system/` 的 token + `studio/app.css` 的类名），
  重构不得顺手改掉任何一处色位或尺寸。
- **Decision**：用 **Vue 3 SFC + Vite** 重构，但**分阶段 strangler 迁移**，不做一次性大爆炸：
  1. 新建 `app/frontend/`（Vite 7 + Vue 3.5 + Vitest 4 + @vue/test-utils 2），构建产物**入库**到
     `app/alibot/studio/v2/`，页面入口是 `/studio/v2/`；
  2. **正本资产不复制**：`i18n.js` 与 `markdown.js` 仍由 `studio/` 提供、由 v2 页面用同一批
     `<script src>` 加载；`app.css` / `product-tokens.css` / 设计系统 token 也照旧引用同一份文件；
     组件里**不出现第二份词条表、不出现第二份 Markdown 渲染器**；
  3. 第一阶段只迁**主链路**（外壳 / 左栏 / 频道头 / 消息流 / composer / `@` 补全 / 全局 SSE / 标读 /
     乐观发送），其余面（任务看板、任务线程、运行面板、审批卡、Agent 详情、搜索、各类弹窗）
     仍由旧页面 `/studio/` 承载，**逐票**迁移；
  4. 前端用例从 `node --test` 迁到 **Vitest**（`app/frontend/tests/*.test.js`），但**旧用例族不删**：
     它们守的是旧页面，旧页面在迁完之前仍是可用入口。
- **Consequences**：仓库第一次有了 `package.json` / `node_modules` / 构建步骤，因此三条既有纪律
  必须显式修订，见下面四节。

## 一、被修订的纪律：从「零依赖零构建」到「构建产物入库」

ADR-0037 §二 写着「仓库没有构建步骤」，那是当时的**事实陈述**，也是当时拒绝第三方 Markdown 库的
理由之一。本 ADR 把那条理由改成一条**更窄的约束**：

- **运行时**依旧零第三方依赖：v2 页面加载的是已经构建好的静态文件，后端 `pip install` 与
  `python -m alibot` 的启动路径**完全不需要 Node**。这条是硬约束——「装好 Python 就能跑」不能被破坏。
- **构建期**允许有依赖：`app/frontend/` 是一个私有 npm 包（`"private": true`），它的依赖只影响
  改前端的人，不影响跑产品的人。
- 因此 `app/alibot/studio/v2/` **入库**（`assets/*.js`、`*.js.map`、`index.html`、`build-info.json`）。
  代价是仓库多一份产物，收益是「克隆 + `pip install -e app` + 起服务」这条路径一个字都不变。

**产物新鲜度是机械判据，不是自觉**：`app/frontend/build-info.js` 把「决定产物的那些文件」的
内容摘要写进 `build-info.json`（不含时间戳，因此构建可复现），`app/frontend/tests/build-guard.test.js`
用**同一个函数**复算并比对。改了 `src/` 没重新构建 ⇒ 门禁红。

## 二、被修订的纪律：「不用 innerHTML」不变，但它换了守法的落点

旧纪律是「DOM 一律 `createElement` / `textContent` 构建，不用 `innerHTML`」，由
`agent_capabilities_ui.test.cjs` 与 `markdown_render_ui.test.cjs` 断言成契约。Vue 版**继续遵守**它，
只是守法变了：

- 组件里**不出现 `v-html`**（它等价于 `innerHTML`）；
- Markdown 正文仍由 `markdown.js` 产出**节点树**，`MarkdownBody.vue` 只做 `appendChild` 挂载；
- 守卫同时扫旧文件与 Vue 组件（剥掉三种注释后不含 `innerHTML` / `v-html`）。

**没有**采用的替代路径：把渲染器改成产出 HTML 字符串再 `v-html`。那会把「转义」与「协议白名单」
两件事挪到字符串层，同时让 ADR-0037 §三 的「先建树、再把后端区间映射到文本节点」失去落点。

## 三、正本资产：一张表、一个渲染器、一套类名

迁移最容易犯的错是「新版本顺手复制一份」。本 ADR 把三件事钉成**唯一正本**：

| 资产 | 正本位置 | v2 怎么用 |
| --- | --- | --- |
| 词条表 | `app/alibot/studio/i18n.js` | 页面 `<script src>` 加载；`src/i18n/index.js` 只包一层响应式桥（`t()` 读一个 `locale` ref，切语言驱动重渲染），**不复制任何词条** |
| Markdown 渲染器 | `app/alibot/studio/markdown.js` | 同上；`src/lib/markdown.js` 只做「取全局函数 + 补空值守卫」 |
| 组件类名与样式 | `app/alibot/studio/app.css` + `product-tokens.css` + 设计系统 token | v2 页面用绝对路径引用同一批文件；Vue 组件消费**同名 class** |

为什么这两个 classic script 不能直接 `import`：它们顶层是 `var` / `function` 声明，加 `export`
会让旧页面的 `<script src>` 变成语法错误；改成 `type="module"` 又会打乱旧 `app.js`（classic、立即执行）
的加载顺序。两条页面共用一个文件，是「只有一份」的前提。

桥接层在拿不到全局对象时**抛一条能定位的错**，不静默退化（静默退化的后果是 Markdown 悄悄失效、
界面回退成键名本身——本仓为这两件事各吃过一次亏）。

## 四、测试：Vitest 与 `node --test` 并存，不是替换

- **旧用例族保留**：`app/tests/*.test.cjs`（389 个用例）守旧页面。旧页面在迁完之前仍是可用入口，
  删掉它们等于让那个入口失去保护。
- **新用例族**：`app/frontend/tests/*.test.js`（Vitest + jsdom + `@vue/test-utils`），守 v2 页面。
- 两族都**走 glob**，都在 `scripts/check.sh` 里跑。这条沿用 `docs/agents/implementation.md` 的
  「命令清单是缓存，缓存会漂移」——新增一个用例文件不需要改任何清单。
- 「不新开第二套前端测试框架」那条纪律**按页面范围**读：旧页面不新开第二套；v2 页面是 Vue 组件，
  用 Vue 的测试工具是**同一层**的选择，不是第二套框架。
- 测试里也读**正本**：`tests/helpers/studio-globals.js` 用间接 `eval` 把两个 classic script 装进
  测试环境（复现 `<script src>` 的全局语义），因此词条表与渲染器在测试里也只有一份。

## 五、迁移期间的两条入口与「不假装」

`/studio/`（旧）与 `/studio/v2/`（新）**并存**，后端静态挂载不需要改（v2 是 studio 的子目录）。
v2 页面只承诺**已迁完的那条链路**可用：

- 已迁：外壳、左栏三段名单、频道头与页签、消息流（Markdown / 提及 / 旁白降档 / 轮次结果 /
  **附件渲染** /「正在回复」/ 乐观发送与重试）、composer（Enter / Shift+Enter / `@` 补全 / 归档只读）、
  全局 SSE（一条流、断线重连、**重连后全量重取**、`rotate` 帧）、标读、首次成功载入的贴底判据、
  **运行面板**（dock 的第三种形态：运行与分派 / 成员事件 / Plan / SOP 四段；快照兜底 + live 合并；
  重连后按快照刷新）、**共享弹窗壳**（`ModalShell.vue`：遮罩 / ESC 只关最上层 / 焦点回收 / `busy`
  不收；但**四张弹窗的内容**仍归 T1–T3 与 ⌘K）、**dock 的三档形态判据**（`run` 有内容，
  `members` / `task` 是留给 T3 / T7 的具名插槽）、**整页的 ESC 层序表**（`App.vue` 的 `onEscape()`）、
  **三个新建表单**（票 #301：新建频道 `ChannelFormModal.vue` / 新建 Agent `AgentFormModal.vue`
  套 #300 的 `ModalShell`，新建私信走 rail 的 Agent 行 ⇒ 幂等 `POST /api/dms`；提交进 `busy`、
  错误留在弹窗内、建完频道自动切过去；同时 `btn-new-channel` / `btn-new-agent` 从「可见但禁用」**翻正**，
  rail 的 Agent 行由 `agentDetailAvailable` 一个开关裁决「详情 / 私信」）。
- 未迁：任务看板与任务线程（含**任务引用 chip / 频道指针 chip**——它们属于那个面）、
  审批卡、Agent 详情、搜索与命令面板、提醒 / 设置 / 频道配置 / 预算 / 名册等弹窗、**附件上传**、
  **任务运行面板**（它挂在任务线程头，随线程那张票）。

未迁入口**不做假按钮**，分两档处理：

1. **不渲染**（`btn-channel-members` / `btn-channel-settings` / `btn-channel-budget` /
   任务页签 / 搜索 / 提醒 / 设置 / 附件 / 作为任务发送 / 失败行的「看成员状态」）：
   它们只是把人带到另一处，带不到就不该出现。频道头的四个入口由 `channelToolsAvailable`
   （名册 / 配置 / 预算，同涨同落）与 `runPanelAvailable`（运行面板，已迁 ⇒ 默认开）两个显式开关控制；
   任务页签由 `tasksAvailable` 控制。迁完那个面只需翻对应的开关，组件不用改。
2. **可见但禁用**：**这一档现在空了**（票 #301）。它原本只装「新建频道 / 新建 Agent」——
   空工作台的引导文案就是「新建一个频道」，把按钮一起藏掉等于让人无路可走，禁用比消失诚实。
   两张表单迁过来之后两个按钮正常工作，这条兜底规则仍然成立：**将来再出现「面还没迁、
   但入口是空工作台唯一出路」的情况，按同一档处理**（可见 + 禁用 + `title` 用词条 `app.notMigrated`），
   而不是渲染一个按下去什么都不发生的按钮。

「看起来能点、点下去什么都不发生」比「这个功能还没过来」更难解释——这条在自查里抓到过一次：
第一阶段曾把 13 个入口渲染成会弹一句无意义 toast 的死按钮。

**按键层序现在有一份可复用的表**（票 #300）：`App.vue` 的 `onEscape()` 按「命令面板 > 模态栈最上层
（`busy` 时不收）> 移动左抽屉 > ≤900px 右抽屉 > 搜索视图」裁决，依据是旧页面
`app.js:10578-10620` 的逐行顺序；T4（#304）落地时**直接复用这个函数**，不另写第二份。
命令面板与搜索视图不套 `ModalShell`（类名与关闭语义不同：前者是 `.cmd`、后者是主线视图）。

**退路**：任何一阶段出问题，删掉 `app/alibot/studio/v2/` 与 `app/frontend/` 就回到重构前。
迁移方式是**新增**（不是就地改写），旧页面的逻辑一行没被改；旧页面目录里只有两处**纯新增**：
`i18n.js` 多两条词条（一份语言各一条，键集必须对齐；票 #301 又多了 `rail.openDmAria` 与
`agent.remoteBodyUnavailable` 两条，同样一份语言各一条）、`app.css` 多一条 `.nav-row.is-static`
（只读行的样式落点，旧页面不使用这个类）。运行面板那一票**没有再碰**旧页面目录：它的词条与类名
早就为这个面定义好了，v2 只是把它们用在同一个落点上。

**dock 的三档形态判据已立起来，只有一档有内容**（票 #300）：`DockPanel.vue` 的 `mode` 由父组件
传的 `dockTarget`（`{ kind: 'run' | 'members' | 'task' }`）**推导**，`null` ⇒ 整栏不渲染。
`run` 档接 `RunPanel.vue`；`members` / `task` 两档是本票留的具名插槽（`<slot name="members">` /
`<slot name="task">`），内容分别归 T3 与 T7——形态判据与几何（380 宽 / `.dock.is-open` /
≤900px 右抽屉）不必让那两张票各改一遍外壳。空插槽**不会出现在产品里**：`App.vue` 目前只写
`kind: 'run'`，私信下 `dockTarget` 恒为 `null`（整栏消失）。

**弹窗的外壳与关闭语义只有一份**（票 #300）：`components/ModalShell.vue` 铺旧页面的
`.overlay` / `.modal` / `.modal-head` / `.modal-body` / `.modal-foot`（`app.css` 零改动），
`open` 驱动根节点的 `hidden` 属性而**不卸载内容**，`busy` 时 ESC / 遮罩 / 关闭键都不动作；
ESC 的层序由 `lib/modal-stack.js` 的模块级栈裁决（**只关最上面那一层**，是有意改进——旧页面
`app.js:10578-10620` 是无栈、一次全关）。四张弹窗票（T1–T3 与 ⌘K）从此只写自己的内容。

## 六、被否的方案

- **一次性重写全部面**：单次 diff 会是上万行，门禁长期红，且迁移期间没有人能验收任何一个面。
- **零构建 + `vue.global.js`（运行时带编译器）**：省掉构建，但模板变成字符串（`innerHTML` 的变体）、
  运行时多 150KB 编译器，且 SFC 的类型与作用域隔离全部失去。
- **Vite 构建但不入库（CI / 安装时构建）**：`pip install -e app` 不会跑 npm，于是「克隆就能跑」
  这条路径会断；把 npm 塞进 Python 安装路径是跨层。
- **把旧 `app.js` 逐步改成 Vue**（就地改写）：`app.js` 的 `render()` 与全局 `state` 与 Vue 的
  响应式模型是两套心智，混在一个文件里会让每次改动都要同时考虑两种生命周期。
- **给 pytest 开「读 Studio 源码」的例外来守产物新鲜度**：pytest 只断言外部可观察行为
  （`test_test_layering_guard.py` 是机械判据，唯一例外是词条表那份数据契约）。产物指纹属于前端资产，
  判据归前端用例族。
- **产物里带构建时间戳**：每次构建都会让 `build-info.json` 变化，仓库里就永远有一份「看起来刚改过」
  的产物，diff 噪声会掩盖真正的改动。指纹只由内容决定。
