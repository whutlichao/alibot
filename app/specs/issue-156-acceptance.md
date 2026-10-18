# Issue #156 验收 —— Workspace / MCP / Skill 管理 UI

> 票面：GitHub [#156](https://github.com/whutlichao/alibot/issues/156)（impl，G-impl / G-ui）。
> 规格：`app/specs/agentscope-2-alignment.md` §4.6 第 3 条（Workspace / MCP / Skill 管理）与 §4.4。
> 裁定：ADR-0021（项目根 / 后端 / 权限档 / 数据面）。
> 依赖：#151（MCP 端点与变量名纪律）、#152（Skill 安装 / 启停 / 分区）、#154（频道设置弹窗）。
> 分支：`issue-156-impl`；worktree `/tmp/alibot-wt/issue-156-impl`；基线 `44abd96`（集成分支 tip）。

| 项 | 值 |
| --- | --- |
| 后端改动 | **零**（`/api/mcp-servers*`、`/api/skills*`、`/api/channels/{id}/config` 均已在基线） |
| 前端落点 | `app/alibot/studio/{app.js,app.css,i18n.js}` |
| 新增用例 | `app/tests/channel_resources_ui.test.cjs`（16 条 DOM 用例） |
| 浏览器证据 | `app/specs/issue-156-evidence/*.png`（真实 Chromium / ego-browser，三档视口 + 错误态） |

---

## 0. 交付概览

| 落点 | 动作 |
| --- | --- |
| `app.js` | 新增**独立的资源管理模块**（状态镜像 + 读 / 写 / 渲染 / 委派绑定）：`state.resources`、`loadMcpServers` / `loadSkills`、MCP 与 Skill 的增删改 / 启停 / 远端确认、Docker 状态派生；三段追加到 `renderChannelConfigModal`，**不重写** #154 的频道配置段与保存路径。 |
| `app.css` | 新增 `.resource-*` 一族（行 / 读数 / 动作 / 表单），只消费既有 token 名（零裸色值、不新造色位）。 |
| `i18n.js` | 新增 `workspaceMgmt.*` / `mcp.*` / `skill.*` 两语文案（键集两侧一致，`i18n_ui.test.cjs` 与裸中文扫描兜住）。 |
| `channel_resources_ui.test.cjs` | 16 条：三段读取 / 变量名掩码 / 新增 / 编辑 / 删除 / 远端确认幂等 / 运行读数 / 启停 / 目录安装 / zip 安装 / 错误可读 / Docker 三态 / 不碰消息面 / 词条。 |

**未改**：后端 `.py`、D1–D23、既有 ADR 原文、`index.html`、设计系统目录；未跑 `ruff format`；未建 PR、未 close issue。

**两条硬边界（实现口径）**：

- **只出现变量名**：MCP 载荷只有 `env_names`（#151 的响应契约），表单也只收变量名；界面里没有值的输入口。
  浏览器读数中 `envInputs = 0`，POST 正文里没有 `env` / `env_values` 键。
- **配置动作不碰消息面**：写 MCP / Skill 后只重读各自清单，不刷新消息、不触发消息面请求；
  DOM 用例断言动作期间没有任何 `/messages` 或 `/events` 请求。

---

## 1. 票面 4 条验收判据

### 1.1 MCP 列表只显示 env 变量名；新增/编辑/删除/确认可用

| 命令 / 操作 | 读数 |
| --- | --- |
| `node --test app/tests/channel_resources_ui.test.cjs` | **16 pass / 0 fail**（含「MCP 列表只显示 env 变量名；表单没有值的输入口」「新增 MCP 提交 env_names 而不是 env 值，成功后按后端重新读回」「编辑 MCP 提交 PATCH，且不带 scope」「删除 MCP 提交 DELETE」「远端 MCP 未确认时给出告知与确认入口；确认动作幂等」） |
| 浏览器：远端 `remote-search` 点「确认数据面」 | 行文本变为 `数据面确认：已确认于 2026-10-18T07:46:17.356Z`；API 读回 `{"consented":true,"env_names":["SEARCH_TOKEN"],"hasEnvValues":false}` |
| 浏览器：新增 `browser-added`（stdio / `npx -y pkg /tmp` / 变量名 `BROWSER_TOKEN`） | API 读回 `{"id":"mcp_fa7f14849894","name":"browser-added","args":["-y","pkg","/tmp"],"env_names":["BROWSER_TOKEN"],"hasEnvValues":false}` |
| 浏览器：编辑为 `browser-edited` | API 读回 `name=browser-edited`（PATCH 只带可编辑字段，scope 不在正文里） |
| 浏览器：删除该声明 | 列表行消失，API 读回 `still_present=false` |
| 浏览器：`envInputs` 探针 | `document.querySelectorAll("[data-mcp-env-values]").length === 0` |

### 1.2 Skill 列表显示 name/description/hash/enabled；安装/启停/删除可用

| 命令 / 操作 | 读数 |
| --- | --- |
| `node --test app/tests/channel_resources_ui.test.cjs` | 「技能列表显示 name / description / hash / enabled；启停提交 PATCH {enabled}」「技能删除提交 DELETE」「本机目录安装提交 JSON；空路径不发请求且错误可读」「zip 安装把原始字节送进正文，元数据走 query」全部 pass |
| 浏览器：列表读数 | `review已启用提交前的结构化自审清单hash 2082bdc3032d`；`lint已停用跑一遍静态检查并汇总hash 61a067114f77` |
| 浏览器：本机目录安装 `/tmp/issue156-skill-extra` | `{"id":"sk_90f0c18ce46d","name":"extra","enabled":true,"hash":"b17929084f9a"}`；随后删除，行消失 |
| 浏览器：启停 | `review:false,lint:false` → `review:true,lint:false`（PATCH 只改启用态） |
| 浏览器：MCP 启停（同一套写路径） | `filesystem:true` → 再停用 → `filesystem:false` |

### 1.3 Docker/无 Docker 状态、路径错误、连接失败可读

| 场景 | 落点 / 读数 |
| --- | --- |
| Docker **有失败原因** | DOM 用例：Agent `enabled_reason` 含 Docker 原因 → `data-state=unavailable`，文本含 Agent 名与后端原因 |
| Docker **未报告故障** | 浏览器（后端 `docker`，无失败原因）：`DOCKER 状态 = 最近一轮未报告故障（只有容器启动失败时后端才记录原因）`，`dockerState=reported` |
| Docker **未使用** | DOM 用例：后端为 `local` 且无失败原因 → `当前后端是本地，不启动容器`，`dockerState=unused` |
| 非法路径 | 浏览器：项目根填 `relative/path` 保存 → `配置未保存：频道配置校验未通过，未改动任何字段：项目根必须是绝对路径：relative/path`；同时 `已落库的项目根` 仍是 `/tmp/issue156-project`，`GET /config` 读回同一值（整体拒绝、无半写） |
| 连接失败 | DOM 用例：`runtime.error` 渲染为 `运行读数：连接失败：Failed to start MCP 'broken': alibot-mcp-binary not found`，并使用 `--error` 档；`runtime=null` 渲染 `尚未装配`；连接成功渲染 `已连接 · N 个工具` |
| 安装 / 保存失败 | 浏览器：空命令新增 MCP → `保存失败：stdio 传输必须提供 command`，草稿保留（名称 / 变量名仍在输入框里）；DOM 用例另覆盖安装失败与启停失败的后端原因 |

> Docker 状态的口径：从 `GET /api/workspace` 的 Agent `enabled_reason`（#150 把容器失败原因
> 记进 availability）与频道配置的 `workspace_backend` 派生；**不做 daemon 探活**，因此
> “未报告故障”不等于“可用”。这是本票刻意保留的诚实口径，见 §5 残余。

### 1.4 DOM 用例 + ego-browser 证据

| 命令 | 读数 |
| --- | --- |
| `node --test app/tests/channel_resources_ui.test.cjs` | 16 pass / 0 fail |
| `node --test app/tests/*.test.cjs` | **173 pass / 0 fail**（新增 16 条；既有 157 条零回归） |
| ego-browser（真实 Chromium） | 三段 × 三档视口 + 错误态截图，见 §2 |

---

## 2. 真实浏览器证据（ego-browser / 真实 Chromium）

**装置**：

```sh
# 独立数据目录 + 真实 uvicorn（baseline `44abd96` + 本票工作区文件）
cd /tmp/alibot-wt/issue-156-impl
env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY \
  PYTHONPATH=/tmp/alibot-wt/issue-156-impl/app \
  .venv/bin/python -m alibot --port 8796 --data-dir /tmp/issue156-data
```

**种子（全部走真实 HTTP 写路径）**：

```text
POST /api/agents ×2（Ada / Bob，ollama:llama3）
POST /api/channels → ch_9f460c8cdfe5（「资源管理验收」，含两名成员）
PATCH /api/channels/ch_9f460c8cdfe5/config → workspace_root=/tmp/issue156-project,
      workspace_backend=docker, permission_mode=ask-writes, mode=autonomous
POST /api/mcp-servers ×3 → filesystem（stdio，GITHUB_TOKEN / LOG_LEVEL，停用）,
      remote-search（http，SEARCH_TOKEN，未确认）, docs（sse，已确认，停用）
POST /api/skills ×2 → review（启用，hash 2082bdc3032d）, lint（停用，hash 61a067114f77）
```

**视口与截图**：

| 视口 | Workspace | MCP | Skill |
| --- | --- | --- | --- |
| 1440×900 @dpr1 | `resources-workspace-desktop-1440.png` | `resources-mcp-desktop-1440.png` | `resources-skill-desktop-1440.png` |
| 1024×768 @dpr1 | `resources-workspace-tablet-1024.png` | `resources-mcp-tablet-1024.png` | `resources-skill-tablet-1024.png` |
| 430×900 @dpr2（≤900 移动壳） | `resources-workspace-mobile-430.png` | `resources-mcp-mobile-430.png` | `resources-skill-mobile-430.png` |

错误态：`resources-error-desktop-1440.png`（非法项目根的可读拒绝）。

**逐项读数**（桌面 1440 视口，同一次会话的 DOM 探针）：

```text
root        "/tmp/issue156-project"
backend     "Docker"
dockerState "reported"
docker      "最近一轮未报告故障（只有容器启动失败时后端才记录原因）"
mcpRows     filesystem    stdio 已停用 本机   环境变量名：GITHUB_TOKEN, LOG_LEVEL  运行读数：尚未装配
            remote-search http  已启用 远端   环境变量名：SEARCH_TOKEN              运行读数：尚未装配
            docs          sse   已停用 远端   环境变量名：没有环境变量              数据面确认：已确认
skillRows   review 已启用 提交前的结构化自审清单 hash 2082bdc3032d
            lint   已停用 跑一遍静态检查并汇总   hash 61a067114f77
envInputs   0
```

**服务端字节自证**（浏览器实际拿到的文件 = 本 worktree 文件）：

```text
$ curl -s http://127.0.0.1:8796/studio/app.js | md5 -q ; md5 -q app/alibot/studio/app.js
e68241a7c0a50ef192e20ae221ad38a1
e68241a7c0a50ef192e20ae221ad38a1      # 浏览器拿到的字节 = 本 worktree 的文件

$ curl -s http://127.0.0.1:8796/studio/i18n.js | md5 -q ; md5 -q app/alibot/studio/i18n.js
a6065d7fe0e668745d6a6a616a1461eb
a6065d7fe0e668745d6a6a616a1461eb

$ curl -s http://127.0.0.1:8796/studio/app.css | md5 -q ; md5 -q app/alibot/studio/app.css
22489e533dfaf7c939f803adf993a36c
22489e533dfaf7c939f803adf993a36c

$ curl -s http://127.0.0.1:8796/studio/index.html | md5 -q ; md5 -q app/alibot/studio/index.html
02a654980648b88719f74ed3e0961250
02a654980648b88719f74ed3e0961250
```

---

## 3. 测试与门禁

| 命令 | 读数 |
| --- | --- |
| `node --test app/tests/channel_resources_ui.test.cjs` | 16 pass / 0 fail |
| `node --test app/tests/*.test.cjs` | 173 pass / 0 fail |
| `scripts/check.sh --fast`（迭代读数） | 门禁全绿（exit 0）：`All checks passed!` / `Success: no issues found in 88 source files` / `0 errors, 0 warnings, 0 informations` / node `173 pass / 0 fail` |
| `scripts/check.sh`（全量，merge 前） | **门禁全绿**（exit 0）：pytest `825 passed, 9 skipped in 356.32s`；ruff check `All checks passed!`；ruff format `--check` `203 files already formatted`；mypy `Success: no issues found in 88 source files`；pyright `0 errors, 0 warnings, 0 informations`；node `tests 173 / pass 173 / fail 0` |

pytest 的 9 个 skip 是本机 Docker daemon 相关用例的既有 `skipif`（#151 标注的 MCP/Docker 成功路径），
与 #156 的前端用例无关。

---

## 4. 残余与说明

- **Docker 状态不是探活**：界面只陈述后端事实——Agent availability 里有 Docker 失败原因就报
  `不可用`，否则按当前后端说 `未报告故障` / `本地不使用容器`。规格 §5.1 把 Docker 真实启动列为
  人工验收（无 CI），本票不新增 daemon 探活端点（§4.4 未列，且票面要求后端零改动）。
- **确认时刻只对本次会话可见**：`GET /api/mcp-servers` 只回 `consented` 布尔值，不回
  `acknowledged_at`；界面因此对“已确认”如实显示，不伪造时间。本会话里点过确认的声明会显示
  后端返回的时刻（幂等：重复确认仍是同一个时刻）。
- **运行读数是进程内观察面**：MCP 的 `connected / tools / error` 不落库，进程重启后回到
  `尚未装配`；这是 #151 的既定口径。
- **MCP / Skill 只在频道 scope 管理**：本票的管理面挂在频道设置里，读写都带
  `scope_kind=channel&scope_id=<channel>`；Agent 级分区（#152）仍由 API / 既有装配路径支持，
  本票没有新增第二个管理入口。
- **参数按空白分隔**：`args` 输入框不做引号解析（与后端“逐个字符串”的契约对齐），带空格的
  单个参数需要后续在 API 层直接写；这是输入控件的已知简化。
- 未改 `index.html`：三段的正文完全由 `renderChannelConfigModal` 动态铺（与 #154 同一渲染落点）。
