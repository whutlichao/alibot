# Studio 已归档私信 + Agent 详情「发送消息」验收

- 基线：`19d4524a898dba237d8cb63f2a628b639712e213`（PR #256 合并点）
- 分支：`studio-dm-archive`
- 面：`app/alibot/studio/**`（零构建，由后端静态托管）+ `app/tests/*.test.cjs`
- 无后端 / API / schema / 迁移改动：归档判据只消费 `GET /api/dms` 已有的 `peer.deleted_at`。

## 判据 → 落点 → 命令与读数

| # | 判据 | 落点 | 命令 / 读数 |
| --- | --- | --- | --- |
| 1 | 已删除 peer 的 DM 只在「已归档」段；正常 DM 只在「私信」段 | `renderDms()` / `dmIsArchived()` / `#dmArchiveGroup` | `node --test app/tests/rail_identity.test.cjs`：`已删除 peer 的私信进「已归档」段…` 通过 |
| 2 | 没有已归档 DM 时整段隐藏；全部归档时「私信」段显示空态 | `renderDms()` 的 `hidden` 裁决 | 同上：`没有已归档私信时整段隐藏…` 通过 |
| 3 | DM 行不再画状态点（两段都不画） | `dmRowNode()` / `paintDmRow()` 删除 `presenceNode` / `syncPresenceCell` | 同上：`presenceOf(normalRow) === null`、`presenceOf(archivedRow) === null` |
| 4 | 未读徽标保留；同一 id 在两次 render 之间是同一节点且零写入 | 两段共用 `syncRailRows` + `syncUnreadBadge` | 同上：徽标文本 `2` / `3`；`harness.mutations(archiveList) === []` |
| 5 | 点「已归档」行仍能打开该 DM 读历史 | `#dmArchiveList` 的 click 委派 → `selectTarget()` | 同上：`点「已归档」里的行仍能打开该私信读历史` 通过 |
| 6 | 「私信」分组 `+`、`#dmOverlay`、`state.dmSelectedAgentId`、弹窗函数与专属词条全部删除 | `index.html` / `app.js` / `i18n.js` | 同上：`新建私信弹窗与分组 + 一起删掉…` 通过（两段文案都无 `dm.*` / `rail.newDmTitle`） |
| 7 | Agent 详情头部有关闭控件旁的 `#btnAgentSendMessage`，文案来自 `agentDetail.sendMessage` | `#viewAgent > .agent-page-head` | `node --test app/tests/agent_detail_ui.test.cjs`：`详情头部有「发送消息」按钮…` 通过 |
| 8 | 点击发出 `POST /api/dms`，body `{"agent_id": …}`；复用已有 DM 与新建（`created: true`）都进入该 DM、离开 `mode === 'agent'`、聚焦 composer、成功 toast 借用 `toast.dmOpened` / `toast.dmCreated` | `messageActiveAgent()` → `selectTarget()` | 同上：两条路径用例通过；`posts[0].body` deepEqual `{agent_id: "mem-1"}` |
| 9 | 请求在途时按钮禁用、重复点击不发第二个请求 | `agentDmInFlight` + `syncAgentSendButton()` | 同上：`请求在途时「发送消息」禁用…` 通过 |
| 10 | 归档 DM 的 composer 只读：`#composerInput.disabled`、附件 / @提及 / 作为任务发送 / 发送禁用、hint / desc / placeholder 用归档词条、`sendMessage()` 有显式闸门 | `activeArchivedDm()` + `syncComposerLock()` + `syncSendButton()` + `sendMessage()` | 同上：`已归档私信只读…` 通过（直接调 `sendMessage()` 也不产生请求） |
| 11 | 切回普通频道 / 正常 DM 后控件与文案恢复 | 同一 `syncComposerLock()` 落点 | 同上：`切到普通频道或正常私信后，输入区控件与文案全部恢复` 通过 |
| 12 | 新文案进两份词条；`index.html` 中文全部挂 `data-i18n`；无裸中文 | `i18n.js` / static guards | `node --test app/tests/i18n_ui.test.cjs`（83 项中的键集 / 裸中文用例）通过；`scripts/check.sh --studio` 全绿 |
| 13 | 词条表 `## Language` 记录「已归档私信」，不是新 target kind | `CONTEXT.md` §Language | 人工核对：新增词条紧邻「私信 (DM)」 |
| 14 | 真实浏览器冒烟（隔离端口 + 隔离数据目录） | 见下节 | 见下节 |

## 红证据（隔离基线）

```sh
scripts/red-evidence.sh 19d4524a898dba237d8cb63f2a628b639712e213 app/tests/rail_identity.test.cjs
scripts/red-evidence.sh 19d4524a898dba237d8cb63f2a628b639712e213 app/tests/agent_detail_ui.test.cjs
```

| 用例文件 | 基线 app.js md5 | 证据目录 | 读数 |
| --- | --- | --- | --- |
| `app/tests/rail_identity.test.cjs` | `d09a642f97f69b67fce011834e3e76b8` | `/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/alibot-red.UgwGmv` | 基线既有的 9 条全绿；新增 5 条全红（归档段不存在 / DM 行仍有状态点 / 归档段无节点） |
| `app/tests/agent_detail_ui.test.cjs` | `d09a642f97f69b67fce011834e3e76b8` | `/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/alibot-red.JpPAFD` | 基线既有的 42 条全绿；新增 6 条全红（头部无发送按钮 / 点击不发 `POST /api/dms` / 归档 composer 仍可输入） |

脚本单次只接受一个测试文件，因此按两个文件各跑一次（票面命令里的两个文件拆成两行）。

## 门禁读数

```sh
scripts/check.sh --studio
```

- pytest（Studio 窄档）：`20 passed in 2.57s`
- `ruff check`：`All checks passed!`
- `ruff format --check`：通过（未运行 `ruff format`，无整文件重排）
- `mypy`（仓库根）：通过（无输出）
- `pyright`：`0 errors, 0 warnings, 0 informations`
- `node --test app/tests/*.test.cjs`：`tests 306 / pass 306 / fail 0`
- 收尾：`门禁全绿`

## node 用例读数（本票新增断言）

```sh
node --test app/tests/rail_identity.test.cjs        # 14 条全绿（9 旧 + 5 新）
node --test app/tests/agent_detail_ui.test.cjs      # 48 条全绿（42 旧 + 6 新）
```

新增用例全部走既有 `vm` + DOM 替身接缝，不测私有函数：断言只落在行节点身份 / `data-dm` 容器归属 / `.presence` 缺失 / 未读徽标文本 / `fetch` 调用与 body / `disabled` / `textContent` / `placeholder` / toast 文案。

## 真实浏览器冒烟

- URL：`http://127.0.0.1:8799/studio/`
- 启动：`cd app && ../.venv/bin/python -m alibot --port 8799 --data-dir /tmp/alibot-dm-archive-smoke`
- 数据目录：`/tmp/alibot-dm-archive-smoke`（隔离，未使用 `8788` 与真实数据）
- 驱动：ego-browser（真实 Chromium）
- 截图：`/tmp/alibot-smoke-00.png` … `/tmp/alibot-smoke-06.png`

| 步骤 | 观察 |
| --- | --- |
| 新建 Agent「冒烟助手」与频道「冒烟频道」后打开详情 | 详情头部出现「发送消息」按钮（与关闭控件同一行）；左栏「私信」段没有 `+` |
| 点「发送消息」 | `POST /api/dms` 后进入 `@冒烟助手`；`#viewAgent.hidden === true`；composer 可见、未禁用、`document.activeElement.id === "composerInput"`；toast「已创建与 冒烟助手 的私信。」；`#dmList` 一行且 `.presence` 计数 0；`#dmArchiveGroup.hidden === true` |
| 返回详情再点一次 | `GET /api/dms` 仍只有 1 条；toast「已打开与 冒烟助手 的现有私信。」 |
| 通过 API 补一条历史消息后删除 Agent，刷新 | 左栏出现「已归档」段，标签「已归档」，里面是 `冒烟助手`；正常「私信」段 0 行；两段 `.presence` 计数均为 0；历史消息仍在消息流里 |
| 进入归档私信 | `#composerInput.disabled === true`；`#btnAttach/#btnMention/#btnAsTask/#btnSend` 全为 `true`；hint「已归档 · 只读，不能发送消息」；desc「该 Agent 已删除，这段私信保留为只读归档。」；placeholder「该 Agent 已删除，这段私信只读归档」 |
| 点「冒烟频道」 | `#composerInput.disabled === false`；三个工具键恢复；hint「Enter 发送 · Shift+Enter 换行」；placeholder「发消息到 #冒烟频道 —— 输入 @ 提及 Agent」 |
| 再点「已归档」里的行 | 重新进入 `@冒烟助手`，输入框再次只读，历史消息 1 条 |

## 两轴 code-review

- **Standards 轴**：rail 两段共用 `syncRailRows`，未变行零写入；composer 可用性只有 `syncComposerLock()` 一个落点；弹窗专用函数 / 状态字段 / CSS 规则 / i18n 键删净；两份词条键集一致（`i18n_ui.test.cjs` 机械判据）；`app.js` / `app.css` 无裸中文。
- **Spec 轴**：逐条对照本票 Change 1–5 与 Observable acceptance 1–6，全部落点存在；只删了本票点名的入口，未改后端契约与既有测试语义。
- 结论：两轴通过；遗留说明见 PR 正文。
