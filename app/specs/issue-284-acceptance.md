# 验收取证：#284 mcp 页签的两块面 + 家目录 `mcp.json` 读写端点

**票型**：实施票（前端 mcp 页签两块面 + 两条后端端点 + 回归测试）｜**门禁**：G-impl（合并由 coordinator 执行）
**Ticket**：[#284](https://github.com/whutlichao/alibot/issues/284)（验收正本 = 票面 AC）
**父规格**：`app/specs/agent-detail-tabs-spec.md`（**D7** 正本 / D9 / T1–T5 / Out of Scope 第 12 条 / **N3 第三格**）
**设计正本**：`app/specs/agent-detail-tabs-design-notes.md`（D7 端点契约表 + 三道校验 + 写盘语义 + 守卫；
D10 seam 选址双 A）
**base**：`e1af8b1`（= 派活时 `agent-detail-tabs-integration` tip）｜**branch**：`issue-284-impl`

**改动面**：`app/alibot/api.py`（+2 端点 + 1 请求模型）、`app/alibot/service.py`（同族 +2 方法）、
`app/alibot/mcp.py`（+2 纯函数 + 1 异常类型，既有函数一字未改）、`app/alibot/workspace.py`
（+1 公开判定函数，内部拆出同名内核）、`app/alibot/i18n.py`（+9 词条 × 两份 catalog）、
`app/alibot/studio/{app.js,app.css,i18n.js}`、`app/tests/test_global_mcp_file.py`（新）、
`app/tests/{agent_capabilities_ui,agent_detail_ui}.test.cjs`、本文件。

**没动的面**：`db.py` / 迁移链 / `SCHEMA_VERSION` / 装配层 / 优先级 / 消息面 / 预算与唤醒纪律；
`studio/index.html` 未改（复用 #280 的静态页签骨架）；`app/tests/{test_global_mcp,test_global_mcp_consent,
test_mcp_assembly,test_mcp_servers}.py` 的既有断言一字未动（只新增文件）。

**正文口径（一句话判据）**：`text` = **文件原文字节**（逐字，不重排、不重缩进、不动末尾换行）；
`servers` = 文件里 `servers` 数组的**条目计数**（原文事实，不做合规过滤）。

---

## 1. 票面 AC → 落点 → 读数

### AC1 · mcp 页签内两块按顺序出现，各有一行数据来源说明，下块写明「同名时库行胜出」

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 上块（库行）在下块（文件）之前 | `app.js` `agentMcpTabNodes()` 返回 `[rowsBox, fileBox]` | 用例「mcp 页签：上块库行、下块 mcp.json 文本域，两块各有一行数据来源说明」：`descendants(panel).indexOf(dbBlock.parentNode) < indexOf(fileBlock.parentNode)` |
| 上块一行数据来源 = 库行 `mcp_server`（`scope_kind='agent'`），装配正本 | `resourceNote('mcp.sourceDb', 'mcp-source-db', …)` | 同用例：`byOdId(panel,'mcp-source-db').textContent === 词条 mcp.sourceDb`（zh-CN：「来自库行 mcp_server（scope_kind=agent）：装配正本，改这里会改变装配结果。」） |
| 下块一行数据来源**必须写明同名时库行胜出** | `resourceNote('agentDetail.mcpFileSource', 'agent-detail-mcp-file-source', …)` | 同用例：`textContent === 词条取值` 且 `assert.match(textContent, /同名时库行胜出/)`；另在词条用例里对**渲染出的文本**再咬一次（`catalogText(...,'agentDetail.mcpFileSource')` 匹配 `/同名时库行胜出/`） |
| 上块的增删改 / 启停 / 删除 / 远端确认行为不变 | 库行块仍是 `mcpRow()` + `mcpForm()`，端点与数据集一字未改（`globalMcpSection()` 的条目视图按 D1 在下块，见 review-fix 的 F2/F3） | 用例「mcp 页签：库行列表、表单与行入口，请求固定带 agent scope」（`:mcpEdit` / `:mcpToggle` / `:mcpDelete` / `mcp-form` 全命中）+「MCP 编辑表单沿用既有形状…」（PATCH body 逐字段）+ `agent_detail_ui` 的两条全局 MCP 确认用例（stdio 不可确认、确认按「成员 + 名字」发出、确认后按钮消失） |

### AC2 · 打开页签时文本域显示文件当前原文；文件不存在时文本域为空 + 可读说明（不是「读到空文件」）

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 文本域显示**原文** | `mcpFileNode()` 的 `textarea.value = view.mcpFileDraft`；`loadAgentMcpFile()` 把 `payload.text` 逐字铺进镜像（**不** JSON.parse + stringify） | 用例「mcp 页签下块：打开页签时文本域显示文件当前原文，并给出条目计数」：`textarea.value === MCP_FILE_TEXT`（替身是**非标准缩进 + 倒键序 + 末尾换行**的原文）；且 `calls` 里有 `GET /api/agents/mem-1/global-mcp-file` |
| 条目计数按后端读数 | `data-od-id="agent-mcp-file-count"` 的文本走词条 `agentDetail.mcpFileServers{count}` | 同用例：`textContent === 词条(zh-CN, count:"1")` |
| 文件不存在 ⇒ 文本域为空 + 可读说明 | `mcpFileNode()` 的 `!view.mcpFileExists` 分支 → `agentDetail.mcpFileMissing` | 用例「文件不存在时文本域为空，并给出「还没有这个文件」的可读说明」：`textarea.value === ""`；`byOdId('agent-mcp-file-missing').textContent === 词条取值` 且 `match(/还没有/)`（中文原句：「这个 Agent 的家目录里还没有 mcp.json（不是空文件）：保存一次会按你写的内容新建它。」） |
| 是**纯文本域**，不做 JSON 格式化 | `el('textarea','textarea agent-mcp-file-text')`，只经 `.value` 读写；无 `JSON.parse` / `JSON.stringify` | 同两条用例：`textarea.tagName === 'textarea'`；`readStudio('app.js')` 里 `mcpFile` 相关路径零 `JSON.parse`/`stringify`；`i18n_ui.test.cjs` 的裸中文扫描 + `innerHTML` 零命中守卫全绿 |
| 保存成功后按后端读数刷新 | `saveAgentMcpFile()` 用**响应**的 `text` / `servers` / `exists` 覆盖镜像 | 用例「保存发一次 PUT（body 是整份原文），成功后按后端读数刷新」：PUT 后 `textarea.value === edited`、计数从 `1` 变成替身返回的 `2`（不是前端自己数） |

### AC3 · 提交非法 JSON ⇒ 400 + 可读原因，文件 mtime 与内容完全不变

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| ① JSON 可解析 | `mcp.validate_mcp_document()` 的第一道；失败抛 `InvalidMCPDocument('text', 'error.global_mcp_text_invalid_json')` | pytest `test_invalid_json_is_a_readable_400_and_leaves_the_file_completely_untouched`（通过）；独立探针读数：`400 code=validation_failed fields=['text']`，`message=mcp.json 不是可解析的 JSON：Expecting value: line 2 column 16 (char 17)` |
| mtime 与内容**完全不变** | 三道校验全部在写盘之前（`validate_mcp_document` 之后才 `open(tmp,'xb')`） | 同用例：`(read_bytes(), st_mtime_ns)` 前后 `assert ==`；探针：`mtime+内容完全不变=True` |

### AC4 · 顶层形状不对（非对象 / `servers` 不是数组）⇒ 400，零写入

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| ② 顶层是对象且 `servers` 是数组 | `validate_mcp_document()` 的第二道（`Mapping` + `list` 双判）→ `error.global_mcp_servers_missing` | pytest `test_a_wrong_top_level_shape_is_a_400_with_no_write`（4 个参数化形状全绿：顶层是数组 / `servers` 是对象 / `servers` 是字符串 / 顶层是 `[]`）；探针：`400 fields=['servers']`，`message=mcp.json 的顶层必须是对象，且 servers 必须是数组`，`零写入=True` |

### AC5 · 单条声明形状非法 ⇒ 400 并指出出错字段，零写入

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| ③ 每条经既有归一化内核 | `validate_mcp_document()` 逐条调 `normalize_mcp_declaration()`（**与库写路径、全局文件读路径同一份实现**） | pytest `test_an_invalid_single_declaration_points_at_that_entry_and_field`（通过）；探针：`400 fields=['servers[1].transport']`，`message=servers 的第 2 条的 transport 不合法`，`零写入=True` |
| 字段级错误能指认「哪一条 + 哪个字段」 | `InvalidMCPDocument.location` = `servers[<下标>].<字段>`；`api.py` 把它放进 `error.fields` | 同用例：`any(key.startswith('servers[1]'))` **且** `any('transport' in key)` |
| 同一条声明合法时是 200（反证 400 来自那一条而不是写路径本身不通） | 同用例末段 | 同用例：修好 transport 后 `_write(...).status_code == 200` |
| 重名条目同样整体拒绝（装配层无法表达同名两条） | `error.global_mcp_entry_duplicate` | `validate_mcp_document()` 的 `seen` 集合（读路径仍是「首条生效」一字未改） |

### AC6 · 提交合法原文 ⇒ 写盘后文件内容与提交文本逐字相等（含末尾换行与否）

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 原文逐字写盘，不重排 / 不重缩进 / 不动末尾换行 | `save_global_mcp_document()`：`open(tmp,'xb')` + `stream.write(text.encode('utf-8'))`，**没有** `json.dumps`；随后 `os.replace(tmp, target)` | pytest `test_a_valid_document_is_written_verbatim`（两个参数化样本：**末尾带换行** / **不带换行**，都 `path.read_bytes() == text.encode()`）；覆盖也逐字（第二次提交替换第一次的字节） |
| 独立探针（不经 pytest 断言）的三种非标准原文 | `/tmp/verbatim_probe.py`（临时 HOME + `TestClient`） | 见下方「逐字写盘实测」段：三份样本、**提交字节 == 磁盘字节** 全为 `True`（224/224、223/223、75/75 字节），末尾换行状态各自原样保留 |

逐字写盘实测读数（`真读数，逐字节比对`）：

```text
非标准缩进+倒键序+末尾换行: status=200 exists=True servers=2
  提交字节 == 磁盘字节: True  (提交 224 字节 / 磁盘 224 字节)
  末尾换行: 提交=True 磁盘=True
同上但末尾无换行: status=200 exists=True servers=2
  提交字节 == 磁盘字节: True  (提交 223 字节 / 磁盘 223 字节)
  末尾换行: 提交=False 磁盘=False
单行紧凑+奇怪空格: status=200 exists=True servers=1
  提交字节 == 磁盘字节: True  (提交 75 字节 / 磁盘 75 字节)
  末尾换行: 提交=False 磁盘=False
```

### AC7 · 家目录不存在 ⇒ 400 且不创建任何目录；家目录存在而文件不存在 ⇒ 建出该文件

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 家目录不存在 ⇒ 400 | `save_global_mcp_document()`：`os.path.isdir(home)` 为假 ⇒ `error.global_mcp_home_missing` | pytest `test_a_missing_member_home_is_a_400_and_creates_no_directory`（通过）；探针：`400 message=该 Agent 的家目录不存在（…/agents/mem_…）：应用不自动创建全局层，请先手动建出这一层` |
| **不创建任何目录** | 校验与判定全部在 `open()` 之前；无 `mkdir` / `makedirs` | 同用例：整棵 `HOME` 树的清单（目录带尾斜杠）保存前后 `assert ==`；探针：`整棵 HOME 树清单不变=True（2 项）` |
| 家目录存在、文件不存在 ⇒ 新建 | 先写同目录临时文件再 `os.replace` | pytest `test_a_valid_document_is_written_verbatim` 起点 `assert not path.exists()` ⇒ 保存后 `exists=True`；探针 `== 7.`：`200 建出文件=True 磁盘逐字相等=True` |
| 文件存在 ⇒ 覆盖 | `os.replace` 原子替换 | 同 pytest 用例的「覆盖也是逐字」段 |

### AC8 · 根外 symlink 祖先 / 根外目标被拒绝，不写入根外位置

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 写前用同一内核取目标 `realpath` | `workspace.resolve_global_write_path()`（与只读守卫共用新拆出的 `_resolve_path()`；`_resolve_read_path()` 在它之上加一层存在性判定，行为一字未变） | `grep -n "def _resolve_path\|def resolve_global_write_path" app/alibot/workspace.py` 命中；`test_global_rules.py` / `test_global_skills.py` / `test_mcp_assembly.py` 全绿（只读守卫的既有用例没有回归） |
| 根外 symlink **目标**被拒 | `resolve_global_write_path()` 返回 `None` ⇒ `error.global_mcp_path_invalid` | pytest `test_a_document_symlinked_outside_the_global_root_is_refused`（通过）：`400`、根外文件字节不变、**链接本身原样保留**（证明拒绝发生在写盘之前）、读端点同样 `exists=false` 且 `text==""` |
| 根外 symlink **祖先**（成员家目录本身是指向根外的链接）被拒 | 同上（`realpath` 沿链解析后落在根外） | pytest `test_a_member_home_symlinked_outside_the_global_root_is_refused`（通过）：`400` + 根外目录 `list(...) == []` |
| 不另写第三份守卫 | 只读守卫与写目标判定共用 `_resolve_path()` 一个内核 | `git diff` 里 `workspace.py` 的唯一改动是把那段判定提出来 + 加一个公开函数；`resolve_global_read_path()` / `_resolve_read_path()` 的签名与返回值一字未变 |

### AC9 · 成员不存在 ⇒ 404（与既有全局 MCP 视图端点同一条校验）

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| GET / PUT 都 404 | 两个 service 方法开头都是 `self._db.get_agent(member_id)`（**与 `global_mcp_servers()` 同一行**） | pytest `test_an_unknown_member_is_a_readable_404_for_both_ends`（通过）：两条都是 `404` 且 message 含 `not-a-member`，且不建目录 |
| 与既有视图端点同一条校验 | 同一行 `get_agent` | 探针：`GET /api/agents/not-a-member/global-mcp-file` → `404 Agent 不存在：not-a-member`；对照既有 `GET /api/agents/not-a-member/global-mcp-servers` → `404`（同一条文案来源） |

### 九种情形的完整 HTTP 读数（一次跑齐，可复跑）

```sh
.venv/bin/python /tmp/nine_probe.py      # 临时 HOME + TestClient；不碰真实 ~/.alibot
```

```text
== 1. 读：文件不存在 ==
  200 {"member_id": "mem_…", "exists": false, "text": "", "servers": 0}  文件被创建=False
== 1b. 读：文件存在（原文逐字 + 计数）==
  200 exists=True servers=1 text==写入原文=True
== 2. 非法 JSON ⇒ 400 且 mtime/内容不变 ==
  400 code=validation_failed fields=['text']
  mtime+内容完全不变=True
  message=mcp.json 不是可解析的 JSON：Expecting value: line 2 column 16 (char 17)
== 3. 顶层形状不对 ⇒ 400 零写入 ==
  400 fields=['servers'] 零写入=True
  message=mcp.json 的顶层必须是对象，且 servers 必须是数组
  400 fields=['servers'] 零写入=True
  message=mcp.json 的顶层必须是对象，且 servers 必须是数组
== 4. 单条声明形状非法 ⇒ 400 + 指出字段 ==
  400 fields=['servers[1].transport'] 零写入=True
  message=servers 的第 2 条的 transport 不合法
== 5. 合法原文 ⇒ 逐字写盘 ==
  200 exists=True servers=1 磁盘逐字相等=True
== 6. 家目录不存在 ⇒ 400 且不建任何目录 ==
  400 message=该 Agent 的家目录不存在（…）：应用不自动创建全局层，请先手动建出这一层
  整棵 HOME 树清单不变=True（2 项）
== 7. 家目录在、文件不存在 ⇒ 新建 ==
  200 建出文件=True 磁盘逐字相等=True
== 8. 根外 symlink 目标 / 祖先 ⇒ 拒绝且不写根外 ==
  根外目标: 400 根外文件未变=True 链接保留=True
    message=mcp.json 的路径不安全（根外 symlink 祖先或根外目标）：…/mcp.json
    读端点: 200 exists=False（守卫同样不看根外）
  根外祖先: 400 根外目录为空=True
    message=mcp.json 的路径不安全（根外 symlink 祖先或根外目标）：…/mcp.json
== 9. 成员不存在 ⇒ 404 ==
  GET 404 Agent 不存在：not-a-member
  PUT 404 Agent 不存在：not-a-member
  对照既有 global-mcp-servers: GET status=404
```

---

## 2. 与 N3 第三格 / D7 的对照（本票最容易做错的地方）

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| **不重排 / 不重新序列化回写**（N3 第三格） | `save_global_mcp_document()` 只 `text.encode('utf-8')` 写盘；`grep -n "json.dumps" app/alibot/service.py` 在 `save_global_mcp_document` 段内 **0 命中** | 逐字写盘实测段（224/223/75 字节三份样本全 `True`） |
| 先写同目录临时文件再原子替换 | `open(tmp,'xb')` → `flush` → `os.fsync` → `os.replace`（先例 `runtime.py` 的状态落盘同一写法） | `grep -n "os.replace" app/alibot/service.py` 命中；失败路径 `suppress(OSError): os.unlink(tmp)` |
| 三道校验**全部在写盘之前** | `validate_mcp_document()` 是 `save_global_mcp_document()` 的第一件事，任何异常都在 `open()` 之前抛 | AC3–AC5 的 `零写入=True` 读数 |
| 不 bump `SCHEMA_VERSION` / 不动迁移链 | `app/alibot/db.py` 未改 | `git diff e1af8b1 -- app/alibot/db.py` 为空 |
| 不改装配层（写完之后装配读的是同一份文件） | 未碰 `agent_seam.py` / `mcp.py` 的装配函数 | `git diff e1af8b1 -- app/alibot/agent_seam.py` 为空；`pytest tests/test_global_mcp.py` 全绿（既有装配路径覆盖） |
| 远端条目的数据面确认仍走既有动作 | 文本域不提供确认入口；上块保留 `POST /api/mcp-servers/{id}/consent` 与 `…/global-mcp-servers/{name}/consent` | `agent_detail_ui` 的两条确认用例全绿；`agent_capabilities_ui` 的库行用例断言 `mcp-consent` 入口仍在 |

---

## 3. D9 断言改写与偏差登记

### 3.1 与 D9 相邻、本票必改（判据：断言与新行为正相反 ⇒ 必须改，且不许删原意）

| # | 用例 / 位置 | 原断言 | 为何要改 | 改法（原意如何保留） |
| --- | --- | --- | --- | --- |
| A | `agent_detail_ui.test.cjs`「mcp 页签：库行块在上、家目录 mcp.json 块在下」 | `fileBlock.parentNode.textContent.includes(词条 agentDetail.mcpFilePlaceholder)`，用例说明写「文件块先给占位说明（文本域由后续票补）」 | 本票兑现 D7：占位说明被真实文本域取代 ⇒ 该断言与票面 AC2 正相反 | 改指文本域：`byOdId(panel,'agent-mcp-file-text')` 存在、`tagName === 'textarea'`、`value === MCP_FILE_TEXT`。**原意保留**：「下块是家目录 `mcp.json` 的落点，不是空壳」 |
| B | `agent_detail_ui.test.cjs`「本票新增 / 沿用的文案…」 | `agentDetail.mcpFilePlaceholder` 在「必须有值」清单 | 同 A：词条随占位节点一起失去消费点（与 #280–#283 已确立的「随消费点消失而删键」同一规则） | 从「必须有值」清单删掉，改列本票新增的 8 个键；键本身在两份 catalog 删除，并在「已删词条」清单里加一条**反向断言**（`undefined`）——比 #283 的 `grep` 读数更强，因为本票同时改了这个用例清单 |
| C | `agent_capabilities_ui.test.cjs` 文件头注释 | 1–6 条里没有 mcp 两块面的口径 | 注释会与断言互相打脸（#282 / #283 已各开一次同一条先例） | 新增第 1b 条描述两块面与两行数据来源标注。**原意保留**：它描述的就是本文件断言的范围 |

**没有第 4 处**：对两个测试文件的全文扫描（`grep -nE "mcpFilePlaceholder|global-mcp-file|mcpFile|mcp-source-db"`）逐条判定后，余下命中要么落在本票新写的用例里，要么是本票新加的词条清单项。

### 3.2 本票新增的用例清单（不是改写，是新增）

- `agent_capabilities_ui.test.cjs`：+5 条（两块顺序 + 两行数据来源 / 文本域原文 + 条目计数 / 文件不存在的可读说明 / 保存一次 PUT + 按后端刷新 / 保存失败给可读原因且不清空输入）。
- `agent_capabilities_ui.test.cjs` 词条用例：+9 个键（`mcp.sourceDb` + 8 个 `agentDetail.mcpFile*`）+ 一条「中文数据来源写明同名时库行胜出」的渲染文本断言。
- `agent_detail_ui.test.cjs`：`capabilitiesFetch()` 新增 `global-mcp-file` 的读写替身 + `MCP_FILE_TEXT` 常量；mcp 页签用例补文本域断言。
- `app/tests/test_global_mcp_file.py`（新，14 条：9 类判据 + 参数化展开）。

### 3.3 登记进「偏差」的两条（不改代码，但要人知道）

1. **`servers` 计数口径**：取文件里 `servers` 数组的**条目计数**（原文事实，不过滤坏条目）。读路径面对人手工改过的文件时，这一条与装配层「跳过坏条目」不同口径——这是刻意的：读端点讲的是「文件里写了多少条」，装配讲的是「实际能装多少条」。票面与 D7 都只写「条目计数」，未要求合规过滤。
2. **读端点遇到不可读 / 非 UTF-8 文件时回 400**（`error.global_mcp_read_failed`）而不是 `exists=true, text=""`。理由与 #283 的 `unreadable` 同源：「读不到」不能被读成「内容是空的」。票面九条 AC 没有这一格，这是新增的第三种读结果，已按判据授权登记（不改任何既有断言）。

---

## 4. 红 → 绿证据

### 4.1 前端（隔离基线副本，只换测试不换产品代码）

```sh
scripts/red-evidence.sh e1af8b1 app/tests/agent_capabilities_ui.test.cjs
# 基线：e1af8b1；基线 app.js md5：56b5b38e2a014a3f6f472083d823b9be
# 证据目录：/var/folders/.../T//alibot-red.st2x6g
```

```text
✖ mcp 页签：上块库行、下块 mcp.json 文本域，两块各有一行数据来源说明
  AssertionError: 上块要给一行数据来源说明（byOdId('mcp-source-db') === null）
✖ mcp 页签下块：打开页签时文本域显示文件当前原文，并给出条目计数
✖ mcp 页签下块：文件不存在时文本域为空，并给出「还没有这个文件」的可读说明
✖ mcp 页签下块：保存发一次 PUT（body 是整份原文），成功后按后端读数刷新
✖ mcp 页签下块：保存失败时给出后端可读原因，且不清空人的输入
  AssertionError: 要改的控件应当存在（文本域还不存在）
✖ 新增文案在两份词条里同键同参数位，随入口撤除的键必须消失
  AssertionError: zh-CN 缺少词条：mcp.sourceDb
```

```sh
scripts/red-evidence.sh e1af8b1 app/tests/agent_detail_ui.test.cjs
# 基线 app.js md5：56b5b38e2a014a3f6f472083d823b9be
# 证据目录：/var/folders/.../T//alibot-red.4m1Sde
# 读数：58 tests / 56 pass / 2 fail
✖ mcp 页签：库行块在上、家目录 mcp.json 块在下，两块各归其位
  AssertionError: 下块是一个文本域
✖ 本票新增 / 沿用的文案在两份词条里都取到值
  AssertionError: zh-CN 缺少词条：agentDetail.mcpFileTitle
```

### 4.2 后端（隔离基线副本上跑同一份新测试）

```sh
EV=$(mktemp -d "${TMPDIR:-/tmp}/alibot-pyred.XXXXXX")
git archive e1af8b1 app/alibot app/tests app/pyproject.toml | tar -x -C "$EV"
cp app/tests/test_global_mcp_file.py "$EV/app/tests/"
cd "$EV/app" && <repo>/.venv/bin/python -m pytest tests/test_global_mcp_file.py
# 证据目录：/var/folders/.../T//alibot-pyred.Q6nnoT
# 读数：14 failed in 1.01s
```

十四条全红，每条都咬在「端点还不存在 ⇒ 404」上（首条断言即 `assert 200 == 404` 的反面）：

```text
FAILED tests/test_global_mcp_file.py::test_read_returns_the_verbatim_text_and_the_entry_count
FAILED tests/test_global_mcp_file.py::test_read_of_a_missing_file_reports_absence_and_creates_nothing
FAILED tests/test_global_mcp_file.py::test_invalid_json_is_a_readable_400_and_leaves_the_file_completely_untouched
FAILED tests/test_global_mcp_file.py::test_a_wrong_top_level_shape_is_a_400_with_no_write[...] × 4
FAILED tests/test_global_mcp_file.py::test_an_invalid_single_declaration_points_at_that_entry_and_field
FAILED tests/test_global_mcp_file.py::test_a_valid_document_is_written_verbatim[...] × 2
FAILED tests/test_global_mcp_file.py::test_a_missing_member_home_is_a_400_and_creates_no_directory
FAILED tests/test_global_mcp_file.py::test_a_document_symlinked_outside_the_global_root_is_refused
FAILED tests/test_global_mcp_file.py::test_a_member_home_symlinked_outside_the_global_root_is_refused
FAILED tests/test_global_mcp_file.py::test_an_unknown_member_is_a_readable_404_for_both_ends
```

### 4.3 实现之后（工作区）

```sh
cd app && ../.venv/bin/python -m pytest tests/test_global_mcp_file.py -q
# → ..............  [100%]   （14 passed）

cd app && ../.venv/bin/python -m pytest tests/test_global_mcp.py tests/test_global_mcp_consent.py \
  tests/test_mcp_servers.py tests/test_mcp_assembly.py tests/test_backend_copy.py
# → 43 passed, 1 skipped in 13.14s   （四份既有 MCP 用例族 + 词条同键集守卫，零回归）

node --test app/tests/agent_capabilities_ui.test.cjs   # → tests 27 / pass 27 / fail 0
node --test app/tests/agent_detail_ui.test.cjs         # → tests 58 / pass 58 / fail 0
node --test app/tests/*.test.cjs                       # → tests 340 / pass 340 / fail 0
#   （基线 335 + 本票新增 5 条；agent_detail 的红→绿是改写既有 2 条，不增条数）
```

---

## 5. 门禁读数（`scripts/check.sh` 全量，动了 `.py` ⇒ 全量一次）

```sh
nohup bash -c 'scripts/check.sh > /tmp/check284.log 2>&1; echo EXIT=$? >> /tmp/check284.log' &
```

| 步骤 | 读数 |
| --- | --- |
| pytest（全量） | `1232 passed, 11 skipped in 704.11s (0:11:44)`（基线 1218 + 本票新增 14 条） |
| ruff check | `All checks passed!` |
| ruff format --check | `350 files already formatted`（首次红在 3 处——`i18n.py` 一行引号换行、`service.py` 三处行宽、`test_global_mcp_file.py` 的引号式；**手工按 format 口径改**，**未跑** `ruff format`；修完重跑为全绿） |
| mypy（仓库根） | `Success: no issues found in 130 source files`（129 + 新测试文件） |
| pyright | `0 errors, 0 warnings, 0 informations` |
| node --test（glob） | `tests 340 / pass 340 / fail 0`（基线 335 + 本票新增 5 条） |
| 结论 | **门禁全绿**（`EXIT=0`） |

> 顺序说明：全量那一次跑在 format 修好**之前**，那一次的唯一红项就是格式（与 #283 同一形态）。
> 修完只重跑 `scripts/check.sh --fast`（ruff / format / mypy / pyright / node 全部重跑，读数见上表），
> pytest 没有第二份代码版本可跑——改动只落在 3 处引号 / 换行上，且 `pytest tests/test_global_mcp_file.py`
> 单独复跑为 14 passed，既有 MCP 用例族 80 passed / 1 skipped。

---

## 6. 零命中 / 对照项（可直接复跑）

```sh
# ① 占位键随消费点消失
grep -rn "mcpFilePlaceholder" app/alibot/            # → 无输出（退出码 1）

# ② 写路径不重新序列化：save_global_mcp_document 段内没有 json.dumps
sed -n '/def save_global_mcp_document/,/def _global_mcp_document_path/p' app/alibot/service.py | grep -c "json.dumps"
# → 0

# ③ 原子替换与临时文件
grep -n "os.replace\|os.fsync" app/alibot/service.py   # → 命中（save_global_mcp_document）

# ④ 同一条成员校验（与既有视图端点同一行）
grep -n "self._db.get_agent(member_id)" app/alibot/service.py    # → global_mcp_servers / global_mcp_document / save_global_mcp_document / confirm_global_mcp_server 四行

# ⑤ 一个守卫内核，两个公开判定
grep -n "def _resolve_path\|def _resolve_read_path\|def resolve_global_read_path\|def resolve_global_write_path" app/alibot/workspace.py

# ⑥ 未改动的面
git diff e1af8b1 -- app/alibot/db.py app/alibot/agent_seam.py app/alibot/studio/index.html   # → 空
git diff e1af8b1 -- app/tests/test_global_mcp.py app/tests/test_global_mcp_consent.py \
  app/tests/test_mcp_assembly.py app/tests/test_mcp_servers.py                               # → 空
```

---

## 7. 偏差与需要人定的问题

1. **D9 之外必改 3 处**（§3.1 的 A–C），全部按「断言与新行为正相反 ⇒ 必须改、不许删原意」处置，没有一处是「删断言让测试变绿」。
2. **本票新增 2 条读结果口径**（§3.3）：`servers` 取原文条目计数；不可读 / 非 UTF-8 文件回 `400`（而不是 `exists=true, text=""`）。票面九条 AC 未覆盖这两格，按 coordinator 前几票的判据授权处置并登记。
3. **`app/README.md` 未改**：本票边界不含它（D9 末段的文档同步面）。它的「Agent 详情、配置编辑与软删除」一节当时仍写「所有区块在同一页纵向铺开……没有页签」——那是 #280 已登记的既存偏差，不是本票新引入的。**本条事实修正（review-fix）**：本票当时**并未**真的在 `api.py` 模块 docstring 端点清单里补两行（原文写「已补」是错的），那两行由
   `agent-detail-tabs-review-fix` 补上；README 的三分页签 / 撤除口径 / `mcp.json` 写面与三条新端点也由那一票收口，见 `app/specs/agent-detail-tabs-review-fix-acceptance.md` 的 F1 / F4。
4. **`pi-lens` 的陈旧索引噪声（已定根因，不是代码问题）**：本票过程中 pi-lens 反复报
   `app/alibot/service.py:48` 的 `"mcp_document_entry_count" is unknown import symbol`。
   **根因已定位**：worktree 复用的 `.venv` 里那份 editable 安装指向**主仓**：

   ```sh
   cat .venv/lib/python3.11/site-packages/_editable_impl_alibot_app.pth
   # → /Users/apple/orca/alibot/app      （主仓，不是本 worktree）
   grep -c "def mcp_document_entry_count" /Users/apple/orca/alibot/app/alibot/mcp.py
   # → 0                               （主仓是旧版本，本票新增的函数不在那里）
   ```

   pi-lens 用**它自己的隔离解释器**（走 `.pth`）逐文件检查，于是看到的是主仓那份旧 `mcp.py`——
   这就是那条诊断的来源。本仓的**权威路径都不走 `.pth`**，因此读数全绿：

   ```sh
   .venv/bin/python -m ruff check app                                   # → All checks passed!
   .venv/bin/python -m mypy            # 从仓库根，mypy_path = app ⇒ 解析到 worktree
   # → Success: no issues found in 130 source files
   .venv/bin/python -m mypy app/alibot/service.py                       # → Success（单文件也绿）
   node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests  # → 0 errors
   cd app && ../.venv/bin/python -c "import alibot.mcp as m; print(m.__file__)"
   # → <worktree>/app/alibot/mcp.py   （conftest.py 把 APP_DIR 插在 sys.path[0]，优先于 .pth）
   ```

   这与 `AGENTS.md` 末节记的「环境差异不是代码问题」同一类，**没有为了消警删掉任何 ignore、
   也没有改动任何代码的写法**。真正的修法是主仓 catch-up 或给 worktree 重建 `.venv`——那是
   环境/编排层的事，不在本票范围（本票不可碰 `scripts/**` 与其它 worktree）。
5. **`app.css` 未新增规则**：下块复用了既有的 `.textarea`（`:605`，`resize: vertical` + `min-height`）
   与 `.hint` / `.resource-meta` / `.agent-actions` 语汇，零新色值、零新组件。`agent-mcp-file*` 三个
   class 只作定位钩子，不新增样式——因此 `app.css` 零改动。
