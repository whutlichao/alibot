<!-- 集成 PR 正文正本：四张实施票（#53 / #54 / #57 / #58）在一条 integration branch 上交付。 -->

Closes #53
Closes #54
Closes #57
Closes #58

## Summary

四张已定案实施票在一条 integration branch 上完成，成一张集成 PR：

| 票 | 交付 | 票级证据 |
| --- | --- | --- |
| #53 | 回应预算的**库内逐频道配置**（`channel_budget_limit`）+ 世代**有效上界快照**（`target_budget.applied_limit`）+ 只读载荷 `source` / `pending_limit`；schema 13 → 14 | `app/specs/issue-53-acceptance.md` |
| #54 | 人类面唯一写路径 `PATCH /api/channels/{id}/budget`（只写上界、只收 3/5/10、`used` 拒写、下一世代生效）+ 频道设置面（四读数 / 来源 / 待生效 / 三档写控件） | `app/specs/issue-54-acceptance.md` |
| #57 | 系统提示的沉默纪律定稿（沉默 = **不产出正文**）+ 旁白式交代的**消息级呈现降档**（保留自己的 `seq`、一行低权重、可展开看关联 `reason`） | `app/specs/issue-57-acceptance.md` |
| #58 | 结构化沉默信号 `silent`：`SEAM_STATUSES` 第四词 + `declare_silence` 工具通道 + `RoundOutcome` 互斥 + 库级 CHECK（schema 14 → 15） | `app/specs/issue-58-acceptance.md` |

```diff
 app/alibot/
   db.py            # +channel_budget_limit 表 / target_budget.applied_limit 快照 / silent 第六态
   migrate.py       # +13→14 / +14→15 两步（链连续、只前进、不回填旧数据）
   agent_seam.py    # 沉默纪律定稿 + declare_silence 工具通道 + RoundOutcome 互斥
   orchestration.py # 硬上界裁决读 state["limit"]；silent 在确定信号下按 failed 收口
   api.py           # GET 扩展 source/pending_limit；+PATCH /api/channels/{id}/budget
   service.py       # 频道预算读 / 写薄委托
   studio/
     app.js         # +频道预算弹窗 / 旁白降档 / silent 说法与摘要行
     app.css        # +降档权重与弹窗样式
     i18n.js        # +新词条（与 i18n.py 同键集纪律）
     index.html     # +频道头预算入口与弹窗骨架
 app/tests/         # +8 个用例文件（后端 5 / 前端 3），既有 4 处字面量跟随
 app/specs/         # +四份票级验收取证
```

**回应预算：上界只有一个解析点，读路径与压制行为同源**

```text
开世代动作（人类消息落库 / owner 改派 / 首次认领）
  db._reset_epoch_budget_locked
    target_budget.applied_limit ← 当时的 channel_budget_limit（无行 = NULL = 内置默认 3）

GET  /api/channels/{id}/budget            PATCH /api/channels/{id}/budget
  service.channel_budget                    service.set_channel_budget_limit
    db.channel_budget_view                    db.set_channel_budget_limit
      db._budget_facts_locked  ← 唯一解析点      └─ 只写 channel_budget_limit；不碰 used / applied_limit
        limit / used / remaining / source / pending_limit
WakeOrchestrator._spend_jump
  state["used"] >= state["limit"]          ← 与只读面同一份事实
```

**结构化沉默：声明与正文互不推演**

```text
AgentScopeAgentSeam.run_round
  _round_toolkit → declare_silence（仅 provider 传输层能携带工具时挂；否则请求里没有 tools）
  silence_declarations → RoundOutcome(status="silent", messages=[])   # 与非空 messages 互斥
WakeOrchestrator.run_round
  确定信号（被点名 / 认领轮）→ failed（与 ignored 同级，reason 说明是哪种沉默）
  否则 → round_outcome.status="silent"，零条回复消息，消费游标推进
```

## Evidence

**门禁（integration branch，仓库根 / `app/` 双目录按票面命令执行）**

| 门禁 | 命令 | 读数 |
| --- | --- | --- |
| pytest（全量） | `cd app && ../.venv/bin/python -m pytest`（去代理） | **637 passed, 2 skipped, 0 failed**（2 skip 为既有：真模型冒烟需 `ALIBOT_SMOKE_MODEL`、`libfaketime` 缺失） |
| ruff check | `.venv/bin/ruff check app` | `All checks passed!` |
| ruff format | `.venv/bin/ruff format --check app` | `132 files already formatted`（全程未跑 `ruff format` 重排） |
| mypy | `.venv/bin/python -m mypy`（仓库根） | `Success: no issues found in 58 source files` |
| pyright | `node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests` | `0 errors, 0 warnings, 0 informations` |
| node --test（全 glob，含票面 5 文件 + 本 PR 新增 3 文件） | `node --test app/tests/*.test.cjs` | **132 pass / 0 fail** |

**逐条「改前 → 改后」**

| 面 | Before | After |
| --- | --- | --- |
| 频道预算读数 | `{channel_id, epoch_seq, limit:3, used, remaining}`（内置 3，无来源、无待生效） | 追加 `source`（`channel-override` / `built-in-default`）与 `pending_limit`；既有五项字段名一字未改 |
| 写上界 | `PATCH` 落到「预算只读：人类面不存在写入它的端点」（404） | `PATCH {"budget_limit":5}` → 200，当前世代 `limit=3` / `remaining` 不变、`pending_limit=5`；下一人类消息后 `limit=5` / `source=channel-override` |
| 写用量 | — | `PATCH {"budget_limit":5,"used":0}` → 400「用量与世代事实只读…由后端独占维护」，`used` 与上界都零改动 |
| 无世代频道保存上界 | 读回 `limit=新值`（看起来立刻生效） | 读回 `limit=3` + `pending_limit=新值`；首次开世代后才 `limit=新值`（按 #54 票面「read back as pending until then」） |
| 沉默纪律（prompt） | 「应当保持沉默（只回一个空字符串之外的简短说明不算回应）」 | 「沉默指的是这一轮不产出正文…也不用括注、旁白、说明或道歉去交代」＋「被点名必须回应」与「正常忽略」同段边界 |
| 旁白式交代 | 与实质回复同等呈现（普通消息行 + 正文段） | 保留自己的 `seq` 与 message 行，一行低权重、原生按钮展开看本轮 `round_outcome.reason`；普通实质回复不误降档（7 种「拿不准」形态逐条断言不降档） |
| 轮次结果状态 | `replied` / `ignored` / `failed` / `merged` / `budget-exhausted` | 追加 `silent`（模型经结构化通道声明沉默；`RoundOutcome(silent)` 与非空 `messages` 互斥）；被点名 / 认领轮的 `silent` 收口成 `failed` |
| schema | `SCHEMA_VERSION = 13` | `15`，链 `13→14→15` 连续、只前进；v13 / v14 库 `--migrate` 报告读数与旧行保真已在票级验收里取证 |

**双轴 code-review（固定点 `main` = `7ab75a1`）与收口**

- Spec 轴：无缺失 / 半成品；报 3 条越界与 3 条实现方式疑点。
- Standards 轴：报 README 未随 `silent` 第六态更新、pytest 越层断言内部常量与前端源码、为单票改共享 `_validation_detail`、无调用方的 `channel_budget_limit()`、冗余 provider 表、无世代频道 pending 口径、新前端用例未登记等。
- 收口（`issue-review-fix`，6 个 commit）：README 口径与测试登记补齐；`_validation_detail` 退回仓库原版、改由本端点字段序达成同一可观察效果；删死代码、`applied_limit` 快照取值收成单点；删掉与 `PROVIDER_KEY_ENV` 恒等的冗余表；两条越层断言改为外部可观察面（stub provider 抓请求载荷 / DOM 呈现）；无世代 pending 按票面口径改正；`issue-57` / `issue-58` 验收补裁定来源与备选。

## Merge Danger

**Door:** one-way

schema 从 13 推到 15，且迁移是显式命令 + 只前进（ADR-0015）：用户库一旦 `python -m alibot --migrate` 到 15，**旧代码会按版本闸门拒绝该数据目录**。回滚这段代码 ≠ 回滚已迁移的数据目录（回滚只能靠迁移前的整目录备份）。代码层面的 API 与 UI 变更是向后兼容的（既有字段名不改、只做附加）。

**Blast Radius:** 全仓（单用户本机应用）

- 后端 7 个文件 + Studio 4 个文件 + 迁移链（`3..15` 全链必须继续连续）+ 8 个新增测试文件 + 4 份验收文档；31 文件 / +5455 −126。
- 所有**既有数据目录**都要先备份再 `--migrate`（迁移自动做整目录备份，备份失败即拒绝迁移）；未迁移的库启动会被闸门拒绝，正文给命令与备份指引。
- 预算语义面：硬上界裁决从常量改为「当前世代快照」——无频道覆盖时逐字等价于原来的 3；有覆盖时按 3/5/10 档位生效，且只在下一个人类世代生效。
- 沉默语义面：新增第六个轮次结果状态与 `declare_silence` 工具；不支持工具传输的 provider 结构性退化到既有判据。未读 / 唤醒 / 预算仍可能被旁白污染——这是 #35 D1 明确登记的已知代价，本 PR 不修。
- 共享面：`_validation_detail` 与 `app/tests/scripted.py` 的改动都是**行为不变**的收口（前者已退回原版），但新增了 `SILENT` 行为、`test_reminder_schema_step` 的版本守卫由 `== 13` 放宽为 `>= 13`（后续合法 bump 不再假红，`12 → 13` 那一步仍由链连续性守卫逐字取证）。
