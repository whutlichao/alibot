# Issue #154 验收 —— 频道设置 UI：team / 队长 / 项目根 / 后端 / 权限档

> 票面：GitHub [#154](https://github.com/whutlichao/alibot/issues/154)（impl，G-impl / G-ui）。
> 规格：`app/specs/agentscope-2-alignment.md` §4.6（前端新视图）与 §4.4（`GET/PATCH /api/channels/{id}/config`）。
> 裁定：ADR-0018（team 是频道拓扑）、ADR-0021（项目根 / 后端 / 权限档）。
> 分支：`issue-154-impl`；worktree `/tmp/alibot-wt/issue-154-impl`；基线 `87ac3ab`（集成分支 tip）。

| 项 | 值 |
| --- | --- |
| 后端改动 | **零**（端点 #142 / #148 / #149 已在基线） |
| 前端落点 | `app/alibot/studio/{index.html,app.js,app.css,i18n.js}` |
| 新增用例 | `app/tests/channel_config_ui.test.cjs`（9 条 DOM 用例） |
| 浏览器证据 | `app/specs/issue-154-evidence/*.png`（真实 Chromium / ego-browser） |

---

## 0. 交付概览

| 落点 | 动作 |
| --- | --- |
| `index.html` | 频道头新增 `#btnChannelSettings`（DM 下隐藏）；新增 `#channelConfigOverlay` 弹窗骨架（正文 / 错误行 / 关闭）；任务线程摘要区新增 `#ttTeamNotice`。 |
| `app.js` | 新增一段**独立的频道设置模块**（状态镜像 + 读 / 写 / 渲染 / 委派绑定）；`renderHeader` 在 DM 下隐藏入口；`renderTaskThread` 渲染 team 提示；`openTask` 读取所属频道配置。 |
| `app.css` | 新增 `.team-notice` 一条（warn 族色位，两处消费；不新造色位）。 |
| `i18n.js` | 新增 `channelConfig.*` 与 `task.teamNotice` 两语文案（键集两侧一致，由 `i18n_ui.test.cjs` 兜住）。 |
| `channel_config_ui.test.cjs` | 9 条：读数 / 只读 `team_scope` / 队长可用性与原因 / team 提示 / 保存读回 / 清空项目根 / 非法路径 / 无队长拒绝 / 任务视图读取配置 / 词条。 |

**未改**：后端 `.py`、D1–D23、既有 ADR 原文、设计系统目录；未跑 `ruff format`。

---

## 1. 票面 5 条验收判据

### 1.1 设置页可读写 `GET/PATCH /api/channels/{id}/config`；team 无合法队长时拒绝并说明；`team_scope` 展示为 channel+task

| 命令 / 操作 | 读数 |
| --- | --- |
| `node --test app/tests/channel_config_ui.test.cjs` | **9 pass / 0 fail**（含「入口打开设置面：全部读数来自后端」「team 没有合法队长时整体拒绝，并显示后端原因」） |
| 浏览器：打开 `#btnChannelSettings` | `title=频道设置`；`scope=channel+task`；GET `/api/channels/{id}/config` 发出 |
| 浏览器：无队长频道 `#空频道（无队长）` 选 team 后保存 | 后端 400：「配置未保存：频道配置校验未通过，未改动任何字段：team 模式必须指定一个队长」；`/config` 读回仍是 `mode=autonomous`、`team_leader_member_id=null` |
| 浏览器：`leader_reason` | 无队长频道显示「队长不可用 / 原因：该频道未设置队长」 |

### 1.2 任务视图读取所属频道配置，显示「任务线程只唤醒队长」

| 命令 / 操作 | 读数 |
| --- | --- |
| `node --test app/tests/channel_config_ui.test.cjs` | 「任务视图读取所属频道配置并显示「任务线程只唤醒队长」」pass；断言 `GET /api/channels/ch_1/config` 在 `openTask` 时发出 |
| 浏览器：打开任务卡 | `#ttTeamNotice.hidden=false`、`textContent="任务线程只唤醒队长"`（三档视口一致，见 §2） |

### 1.3 `leader_available` / `leader_reason` 可见；team 下显示成员 auto_claim 已停用

| 命令 / 操作 | 读数 |
| --- | --- |
| `node --test app/tests/channel_config_ui.test.cjs` | 「team 模式下显示成员 auto_claim 已停用与任务线程只唤醒队长；切回 autonomous 后消失」pass |
| 浏览器：demo 频道（team / 队长 Ada） | `队长可用`；提示区文本 `成员 auto_claim 已停用任务线程只唤醒队长` |
| 浏览器：无队长频道 | `队长不可用`；`原因：该频道未设置队长` |

### 1.4 保存后读回一致；非法路径 / 档位整体拒绝且错误可读

| 操作 | 读数（真实浏览器 + 真实 uvicorn） |
| --- | --- |
| 填 `relative/path` 保存 | 错误行可见：「配置未保存：频道配置校验未通过，未改动任何字段：项目根必须是绝对路径：relative/path」；输入框保留未落库的草稿 |
| 合法保存（`/tmp/issue154-project` + Ada + `trusted-writes`） | toast「频道配置已保存。」；错误行隐藏；`GET /config` 读回 `workspace_root=/tmp/issue154-project`、`permission_mode=trusted-writes`、`team_leader_member_id=mem_fa344efdad26` |
| 档位控件 | 只有后端词表的三档（`read-only` / `ask-writes` / `trusted-writes`）与两档后端（`local` / `docker`）；没有可输入任意值的控件 |

### 1.5 DOM 用例 + ego-browser 三档视口证据

| 命令 | 读数 |
| --- | --- |
| `node --test app/tests/*.test.cjs` | **145 pass / 0 fail**（新增 9 条；既有 136 条零回归） |
| `ego-browser nodejs`（真实 Chromium） | 设置面 / 任务线程各三档视口截图，见 §2 |

---

## 2. 真实浏览器证据（ego-browser / 真实 Chromium）

**装置**：

```sh
# 独立数据目录 + 真实 uvicorn（baseline `87ac3ab` + 本票工作区文件）
PYTHONPATH=<worktree>/app .venv/bin/python -m alibot --port 8797 --data-dir /tmp/issue154-data

# 种子（真实 HTTP 写路径）
POST /api/agents   ×2（Ada / Bob，ollama:llama3）
POST /api/channels ×2（「设计系统重构」含两名成员；「空频道（无队长）」）
PATCH /api/channels/ch_9f64b197d10c/config → mode=team, leader=Ada, root=/tmp/issue154-project, backend=docker
POST /api/channels/ch_9f64b197d10c/tasks
```

**视口与读数**：

| 视口 | 设置面 | 任务线程 |
| --- | --- | --- |
| 1440×900 @dpr1 | `settings-desktop-1440.png`（顶部）/ `settings-desktop-1440-bottom.png`（权限档 + 保存） | `task-team-desktop-1440.png`：`#ttTeamNotice` 可见 |
| 1024×768 @dpr1 | `settings-tablet-1024.png` | `task-team-tablet-1024.png`：dock 宽 339，提示可见 |
| 430×900 @dpr2（≤900 移动壳） | `settings-mobile-430.png` | `task-team-mobile-430.png`：dock 抽屉 `is-open=true`，宽 380，提示可见 |

**逐项读数**（桌面 1440 视口，同一次会话）：

```text
title        "频道设置"
scope        "channel+task"                       ← 后端只读事实，不是前端拼的
availability "队长可用"
notices      "成员 auto_claim 已停用任务线程只唤醒队长"
leader       "mem_fa344efdad26" (Ada)
root         "/tmp/issue154-project"
backend      [["local","false"],["docker","true"]]
permission   [["read-only","false"],["ask-writes","true"],["trusted-writes","false"]]
```

保存 / 拒绝的真实读数见 §1.4；无队长拒绝见 §1.1。

**服务端字节自证**（浏览器实际拿到的文件 = 本 worktree 文件）：

```text
$ curl -s http://127.0.0.1:8797/studio/app.js | md5 -q ; md5 -q app/alibot/studio/app.js
c02e3b3bd8ea5dd6e703b41d7100dfdd
c02e3b3bd8ea5dd6e703b41d7100dfdd      # 浏览器拿到的字节 = 本 worktree 的文件

$ curl -s http://127.0.0.1:8797/studio/index.html | md5 -q ; md5 -q app/alibot/studio/index.html
93f4b53747eeed5a92d5e4a8a9b4317a
93f4b53747eeed5a92d5e4a8a9b4317a
```

---

## 3. 测试与门禁

| 命令 | 读数 |
| --- | --- |
| `node --test app/tests/channel_config_ui.test.cjs` | 9 pass / 0 fail |
| `node --test app/tests/*.test.cjs` | 145 pass / 0 fail |
| `scripts/check.sh`（全量，最终文件） | **门禁全绿**（exit 0）：pytest `791 passed, 2 skipped in 342.18s`；ruff check `All checks passed!`；ruff format `--check` `188 files already formatted`；mypy `Success: no issues found in 79 source files`；pyright `0 errors, 0 warnings, 0 informations`；node `tests 145 / pass 145 / fail 0` |
| `scripts/check.sh --fast`（迭代读数） | `All checks passed!` / `Success: no issues found in 79 source files` / `0 errors, 0 warnings, 0 informations` / `门禁全绿` |

---

## 4. 残余与说明

- 队长**不可清空**：后端 `ChannelConfigUpdate` 对 `team_leader_member_id` 的显式 `null` 整体拒绝
  （`error.field.channel_config_field_null`），空串也不是合法队长；因此设置页不提供「清除队长」，
  只能改选为当前频道的其他 Agent。`team` 无队长的拒绝路径仍在（新建频道 / 未配置队长时选 team）。
- `team_scope` 不在可写字段里（后端 `extra="forbid"`），界面只显示、不提交。
- 任务视图缓存 `state.channelConfigs[channelId]`：设置页保存后同步覆盖；别处改配置时不主动轮询
  （下次打开任务线程仍读同一端点，符合「读所属频道同一配置」）。
- 截图目录只含本票证据；未修改设计系统与既有 ADR。
