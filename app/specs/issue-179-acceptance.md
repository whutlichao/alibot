# Issue #179 验收 —— team 频道建任务唤醒队长 + 队长身份与 TeamAssign 指令（A+B）

> 票面：GitHub [#179](https://github.com/whutlichao/alibot/issues/179)（impl，G-impl / G-merge 双向门）。
> 规格：`app/specs/agentscope-2-alignment.md` §3.3（唤醒矩阵）/ D16 / D23。
> 裁定：**ADR-0025**（新建）——显式覆盖 ADR-0022 §二「创建任务」那一格与 ADR-0016 §五的确定信号范围；
> ADR-0022 / ADR-0016 正文**一字不改**。
> 分支：`issue-179-impl`；基线 `c606b4a`（集成分支 tip）。

| 项 | 值 |
| --- | --- |
| schema | **不动**（`SCHEMA_VERSION` 未变，本票零 DDL、零迁移步骤） |
| HTTP 端点 | **不增不删**（建任务仍走既有的 `POST /api/channels/{id}/tasks`） |
| 任务五态 / 迁移表 | **不动** |
| 后端落点 | `app/alibot/orchestration.py`（`notify_task_created` / `_notify_team_task_created` / `_notify_team_task_assignee`）、`app/alibot/service.py`（`create_task` 的编排入口）、`app/alibot/agent_seam.py`（`_system_prompt(..., leader=)` / `_run_team_round_stream`） |
| 测试落点 | `app/tests/test_team_task_threads.py`（行为 + 新用例）、`app/tests/test_team_mode.py` / `app/tests/test_team_visibility.py`（既有断言按新事实调整）、`app/tests/scripted.py`（替身透传 `leader`） |
| 文档落点 | `docs/adr/0025-task-creation-wakes-the-team-leader.md`、`CONTEXT.md`、`app/specs/agentscope-2-alignment.md`、本文件 |
| `app/README.md` | **改了一句事实修正**（review Standards 轴要求）："只圈认领轮"后面补上 team 例外——建任务 / owner 改派 / Agent 转派唤醒队长的那一轮也是确定信号。**没有**新增/改变对外词表（无新 `round_outcome` 状态 / 消息 kind / 端点），因此词表守卫不需改 |

---

## 0. 交付概览（一眼看完 A/B/C）

```text
create_task()（人的动作，唯一扳机）
  → WakeOrchestrator.notify_task_created(task_id, channel_id)
      ├─ 非 team 频道：转调既有的 claim_new_task()      ← 认领逻辑只有一份，autonomous / DM 逐字不变
      └─ team 频道：_notify_team_task_created()
            队长可用 ⇒ 向队长 submit 一轮
                        target = task target；from_seq = 建任务那条 system 的 seq；
                        reason = channel-message；jump_delivered = False；pierced = False；
                        mandatory_reply = True
            队长不可用 ⇒ 零唤醒 + team_run(status="invalid-leader")（D18 同口径）

改派 / 转派（team）
  → _notify_team_task_assignee()：只唤醒队长，且 mandatory_reply = True（actor 是队长时仍零唤醒）

队长轮次（team）
  → _system_prompt(..., leader=True)：队长身份句 + TeamAssign 指引 + 「本轮由 team 任务变化唤醒」
  → transfer_task 的句子对队长改成「只属受派者，不能用它改指派人」
```

**明确不变**：认领（`claim_new_task` 在 team 频道仍早退）、任务行四字段、`TeamAssign` 的写入语义
（不写 `task.status/assignee/claimed_at`、不写任务变更 `system`、成员产出不落 message）、
`_team_member_prompt`、autonomous 轮次与 team 成员轮次 prompt 逐字不变、认领轮
「本轮由认领唤醒，你必须回应。」逐字不变。

---

## 1. 判据 1：team 建任务 ⇒ 队长一轮必答，且不认领、不改行、不写分派、不扣跳

**落点**：`orchestration.notify_task_created` / `_notify_team_task_created`；`service.create_task`。

**用例**：`app/tests/test_team_task_threads.py::test_creating_a_team_task_wakes_only_the_leader_with_a_mandatory_round`。

| 读数 | 值 |
| --- | --- |
| `scheduler.pending_count()`（建任务后） | `1` → drain 后 `0` |
| 队长接到的调用数 / 成员接到的调用数 | `1` / `0` |
| 那一轮的 `target_id` | `= task["target_id"]` |
| 那一轮的 `mandatory_reply` | `True` |
| 那一轮的 `bodies` 里有「任务已创建：建任务唤醒」 | `True`（`from_seq` 就是那条 `system` 的 seq） |
| 任务行 `status / assignee_member_id / claimed_at / updated_at` | 与建任务时**逐字相同**（`todo` / `None` / `None` / 原值） |
| `db.list_team_assignments(target_id)` | `[]`（没有新行：分派只在队长真调 `TeamAssign` 时发生） |
| `GET /api/tasks/{id}/budget` 的 `used` / 频道 `used` | `0` / `0`（人类动作不是 agent→agent 跳） |

```sh
cd app && ../.venv/bin/python -m pytest tests/test_team_task_threads.py \
  -k "creating_a_team_task_wakes_only_the_leader"
# 1 passed
```

## 2. 判据 2：队长被停用 ⇒ 建任务零唤醒 + 一条 `invalid-leader`

**用例**：`test_creating_a_team_task_with_an_unavailable_leader_wakes_nobody`。

| 读数 | 值 |
| --- | --- |
| `scheduler.pending_count()` | `0` |
| `seam.call_count` | `0` |
| `db.list_team_runs(task["target_id"])` 的 `status` | `["invalid-leader"]`，`leader_member_id` = 被停用的队长 |
| `task.claimed_at` | `None`（仍然不认领） |

## 3. 判据 3：team 改派 / 转派 ⇒ 只有队长一轮且必答，受派者无轮次

**落点**：`_notify_team_task_assignee` 的 `mandatory_reply=True`。

**用例**：`test_owner_reassignment_in_a_team_task_is_a_mandatory_round_for_the_leader`（owner 改派）、
`test_agent_transfer_only_wakes_the_leader_and_never_claims`（Agent 转派）、
`app/tests/test_team_mode.py::test_owner_reassignment_wakes_the_leader_not_the_new_assignee`。

| 读数 | 值 |
| --- | --- |
| 改派后 `scheduler.pending_count()` | `1` |
| 队长那一轮的 `mandatory_reply` | `True` |
| `seam.calls_for(受派者)` | `[]` |
| `task.assignee_member_id` / `claimed_at` | 新受派者 / `None`（改派不是认领） |

## 4. 判据 4：队长 prompt 有队长身份与 `TeamAssign`；认领轮与 autonomous 轮次逐字不变

**落点**：`agent_seam._system_prompt(..., leader=False)` + `_run_team_round_stream(leader=True)` +
`tests/scripted.py` 用 `leader=team is not None` 复用**同一个**合成函数（不手抄文案）。

| 项 | 读数 | 用例 |
| --- | --- | --- |
| 队长 prompt 含队长身份 | `"你是这个 team 频道的队长，不是成员"` 出现 | `test_team_assign_in_a_task_thread_is_task_scoped_and_leaves_the_task_row_alone`（真实适配器 + 进程内脚本模型，读 `leader_model.seen[0]`） |
| 队长 prompt 含 `TeamAssign` 指引 | `"TeamAssign"` 出现，且写明成员同一轮运行、结果作为工具结果回来 | 同上 |
| 队长 prompt **不含**成员身份句 | `"协作者的身份是「成员」"` 不出现 | 同上 |
| 队长 prompt 的必答句不与认领混用 | `"本轮由 team 任务变化唤醒，你必须回应。"`；`"本轮由认领唤醒，你必须回应。"` 不出现在队长轮 | 同上 |
| 队长轮不宣称可用 `transfer_task` 改指派人 | `"transfer_task 只属于这张任务的受派者；你是队长，不能用它改指派人"` | 同上 |
| 成员 prompt 不变 | 仍含 `"你是 team 模式频道里的成员"`，不含 `TeamAssign` / 队长身份 | 同上（读 `member_model.seen[0]`） |
| 认领轮文案逐字不变 | `app/tests/test_auto_claim.py` 三处断言仍绿（`:599` / `:621` / `:669`） | `test_the_claim_round_prompt_carries_the_task_body_and_the_assignee_identity` / `test_owner_reassignment_wakes_but_does_not_make_the_round_mandatory` / `test_channel_and_dm_rounds_carry_no_task_context` |
| autonomous 轮次 prompt 不变 | 同一批断言 + `test_tasks.py` 的「建任务零唤醒」 | `pytest tests/test_auto_claim.py tests/test_tasks.py` |

## 5. 判据 5：`scripts/check.sh` 全绿（本票门禁的读数）

```sh
bash scripts/check.sh          # 退出码 0
```

| 门 | 读数 |
| --- | --- |
| pytest（全量，从 `app/` 跑） | `854 passed, 10 skipped in 366.30s (0:06:06)`（本机 Docker/OrbStack 守护进程在收尾时已停，8 条 docker 用例因此 skip；daemon 在跑时同一套是 `862 passed, 2 skipped`） |
| `ruff check app` | `All checks passed!` |
| `ruff format --check app` | `218 files already formatted` |
| `mypy`（仓库根，`warn_unused_ignores = True`） | `Success: no issues found in 92 source files` |
| `pyright app/alibot app/tests` | `0 errors, 0 warnings, 0 informations` |
| `node --test app/tests/*.test.cjs` | `tests 180` / `pass 180` / `fail 0` |
| 末行 / 退出码 | `门禁全绿` / `0` |

**环境噪声（已取证、与本票无关）**：本机 Docker 由 OrbStack 提供，门禁跑在它上面时出现过两次超时型失败：
`tests/test_docker_workspace.py::test_workspace_reuses_the_container_then_reclaims_it_when_idle`
与 `tests/test_mcp_assembly.py::test_docker_backend_materializes_mcp_through_the_workspace_gateway`，
原因都是 `RuntimeError: gateway did not become healthy within 30.0s`。单跑这两个文件通过；收尾时
`docker ps` 报 `failed to connect to the docker API at unix:///Users/apple/.orbstack/run/docker.sock`
（守护进程已停），于是它们以 skip 计入。两个文件与本票改动无交集（`workspace.py` / `mcp.py` / docker 路径均未改）。

## 6. 判据 6：双轴 `code-review`（Standards + Spec）

见 §8「双轴 review 结论」——两轴各跑一次，findings 与逐条处置写在那里，结论同时贴到 issue #179 的 Answer。

---

## 7. 红 → 绿证据（可复跑）

**红**：在隔离副本里保留**本票的测试**、把四个实现文件换回**固定基线 `c606b4a`**（不是 `HEAD`！
实现提交之后 `HEAD` 已经含有答案，那样复跑就不再是红），跑同一组用例：

```sh
# 隔离副本：/tmp/red-179（rsync 出当前 worktree，再把 4 个实现文件换回 c606b4a）
for f in app/alibot/orchestration.py app/alibot/service.py app/alibot/agent_seam.py app/tests/scripted.py; do
  git show c606b4a:$f > /tmp/red-179/$f
done
cd /tmp/red-179/app && ../.venv/bin/python -m pytest tests/test_team_task_threads.py tests/test_team_mode.py
```

```text
>       assert harness["scheduler"].pending_count() == 1
E       assert 0 == 1
E        +  where 0 = pending_count()            # ← team 建任务：零唤醒
tests/test_team_task_threads.py:207: AssertionError
...
13 failed, 22 passed in 2.82s
```

**绿**：同一组用例在当前分支上：

```text
35 passed in 2.15s
```

## 8. 双轴 review 结论

固定点 `c606b4a`，两个子代理并行跑（Standards 轴 / Spec 轴各一次，只读）：
`git diff c606b4a...HEAD` + `git log c606b4a..HEAD --oneline`（单提交 `e2d87b8`）。

### 8.1 Standards 轴

| # | finding | 级别 | 处置 |
| --- | --- | --- | --- |
| S1 | 验收文档的红证据用 `git show HEAD:<file>`：实现提交后 `HEAD` 已含答案，所列 `13 failed` 不可复跑（违 `docs/agents/review-rules.md`「验收证据要能被复跑」） | 硬违规 | **已改**：固定为 `c606b4a:<file>`，并在文中写明「不是 `HEAD`」的理由 |
| S2 | 验收文档里的 pytest 命令多余加了 `-q`（违 `docs/agents/implementation.md`「不要重复补 `-q`」） | 硬违规 | **已删** |
| S3 | `app/README.md` 的「只圈认领轮：owner 手动改派……不强制发言」已与 team 频道新行为冲突（review-rules 第 6 条「文档与数据契约同步」） | 判断项 | **已同步**：补一句**事实修正**（team 频道的建任务 / 改派 / 转派唤醒队长那轮是确定信号），autonomous 纪律不变；本文件 §顶表格已说明为什么这不是「改词表」 |

结论：无硬违规遗留；三处均已处置。**没有**发现需要的代码异味修复项。

### 8.2 Spec 轴

| # | finding | 级别 | 处置 |
| --- | --- | --- | --- |
| P1 | `agent_seam.py` 的队长提示无条件写「你是队长，不能用它改指派人」，但 `_task_action_tools` 在 `actor == assignee` 时**真的**把 `transfer_task` 挂给队长 ⇒ 票面「措辞要与事实一致」没做到 | 需修正 | **已改**：措辞改为「`transfer_task` 只属于这张任务的受派者：你只有在就是受派者时才能用它改指派人；不是受派者时需要成员动手请用 TeamAssign」；两者都为真的表述，并加了断言 |
| P2 | 验收文档红证据用 `HEAD`，无法独立复现「先红后绿」（同 S1） | 需修正 | **已改**（同 S1） |
| P3 | 子代理无法访问 GitHub（代理阻 `gh issue view 179`），因此无法机械核对票面 Target 清单，特别无法确认额外修改 `app/tests/test_team_visibility.py` 是否获授权 | 限制（非缺陷） | **已解释**：该文件不在票面 Target 列表里，但它的 `test_team_task_does_not_pierce` 用 `scheduler.drain(limit=1)`——建任务多出一轮之后，那一次 drain 会偷跑**建任务那一轮**、使「任务消息不穿透」这条判据变成假绿。按票面「不得靠删断言过关、要保留原意」改为先 drain 建任务轮、再发任务消息并 drain；只动这一条用例，不扩任何行为。违规面已在 issue Answer 里显式登记 |

结论：两条需修正均已修；P3 属审查侧的取证限制 + 一处必须说明的测试改动（已说明）。允许范围内的行为扩张：**无**（未改 schema / 端点 / 五态 / `TeamAssign` 写入语义）。

## 9. 已知缺口与遗留（不隐藏）

| 项 | 状态 |
| --- | --- |
| 队长轮次的 `round_outcome.reason` 曾写出「本轮由**认领**唤醒（…）」——team 频道不发生认领 | **已修**（用户 2026-10-19 裁定 A，允许本票纳入 `app/alibot/i18n.py`）：新增 `reason.team_change_round` / `_ignored` / `_silent` / `_no_body` 四条（zh + en），`run_round` 按「本轮是不是队长轮」选文案；认领轮仍用 `reason.claim_*`（autonomous 读数逐字不变）。落点与读数见 §10.1 |
| pi-lens 自带 mypy（2.4.0）报若干 `[unused-ignore]`，**同时**报这些行里的 `arg-type` 错误 | **不是本票引入，且该诊断自相矛盾**：同一批行既被说「`type: ignore` 多余」、又被说「这里有个被它抑制的 `arg-type`」——只可能来自两套不一致的环境。根因：pi-lens 的 mypy 解释器**没装 `agentscope`**（`~/.pi-lens/pip-tools/bin/python -c "import agentscope"` → `ModuleNotFoundError`，于是类型全变 `Any` ⇒ ignore 显得多余），而 `resolvedCwd` 又是 `<worktree>/app`（`docs/agents/implementation.md` 要求 mypy **从仓库根跑**）。仓库门禁跑在 `.venv`（agentscope 2.0.10）：`Success: no issues found in 92 source files`；删掉其中一条 ignore 会让仓库门禁**真红**（实测 `arg-type`）。`git diff c606b4a..HEAD` 新增 `type: ignore` 数为 **0**，且基线 `main` 上就是同样的 11 条。以 `scripts/check.sh` 为准 |
| pi-lens 报的「测试分层」类若干条（`scripted.py` 导入 `_system_prompt`、`test_team_task_threads` 导入 `tests.test_team_budget` 的私有助手、用例读 `leader_model.seen[...]`、以及「用例用替身而不是真 pipeline」） | **不是本票引入 / 是仓库既有体例**：前两条在基线 `c606b4a` 里逐字存在（`scripted.py:17,216`、`test_team_task_threads.py:39`）；读 `.seen[...]` 与 `test_team_budget.py:267` 的既有取证同源，而 `docs/agents/implementation.md` 明确「票面要求核实某项能力时…才允许在既有用例族里加进程内 stub，并在验收文档里写明理由」——本票要求的正是 prompt 注入（没有 HTTP 观测面），理由见 §4。机械守卫 `test_test_layering_guard.py` 只禁 **从 `alibot.*` 导入 `_` 开头的名字**（`tests.*` 不在判据内），且它在门禁里是绿的 |

## 10. Follow-up（#179 追加项 ①②，coordinator 复核后确认）

### 10.1 ① 队长轮次的 reason 文案：不再写「认领」

**判据**：队长被任务变化唤醒的轮次里，`round_outcome.reason` **不含**「认领」、含「任务变化」；认领轮
（autonomous）的文案**逐字不变**；新词条同时存在于两份词表（`test_backend_copy` 的键集/参数守卫与
`i18n_ui.test.cjs` 绿）。

**落点**：

- `app/alibot/i18n.py`：zh / en **各 4 条**——`reason.team_change_round` / `_ignored` / `_silent` / `_no_body`
  （参数与认领那套一一对应，`{reason}` 出现在 round/ignored/silent 三条里）。
- `app/alibot/orchestration.py::run_round`：在确定信号收口前算一个**结构事实** `leader_task_change =
  team_context is not None and mandatory_reply`，据此选文案键（`reason.team_change_*` 或既有的
  `reason.claim_*`）。判据**不读 `reason` 文本**，纪律标记仍是同一个 `mandatory_reply`——没有新增第六种纪律语义。
- `docs/adr/0025-*.md` §六：把原来登记为「措辞缺口（未修）」的那条改成「已解决 + 落点」。

| 命令 | 读数 |
| --- | --- |
| `pytest tests/test_team_task_threads.py -k "silent_leader or ignored_leader"` | `2 passed`；两条都断言 `"任务变化" in reason` 且 `"认领" not in reason` |
| `pytest tests/test_auto_claim.py` | 认领轮文案断言（`本轮由认领唤醒，你必须回应。` / `reason.claim_round` 后缀）仍绿 |
| `pytest tests/test_backend_copy.py` | 两份词表同键集、同参数、各自取本地化取值，绿 |
| `node --test app/tests/i18n_ui.test.cjs` | `pass 53 / fail 0` |

**红 → 绿**（改实现前，两条新断言直接指出现状）：

```text
>       assert "任务变化" in outcomes[0]["reason"]
E       AssertionError: assert '任务变化' in '认领唤醒是确定信号，必须回应；本轮模型声明沉默，按失败处理（原因为：…）；本轮由**认领**唤醒（…）'
3 failed, 13 deselected
```

改后：`3 passed, 13 deselected`。

### 10.2 ② 队长 prompt 的成员清单口径：不把「全工作区」当成 `TeamAssign` 可选项

**判据**：频道外存在另一个 Agent 时，队长（**不是**受派者）的 prompt 里不得把它列为可分派对象；
`TeamAssign` 指引仍在。

**落点**（选 a，理由见 ADR-0025 §四）：`app/alibot/agent_seam.py::_system_prompt` 的 `task_context` 段——
队长**不是受派者**时不再输出 `可转派 Agent（…）` 那份名单（它的来源 `transfer_options_for()` 是
`Database.agent_members()`，**全工作区**），改为「需要成员动手时用 TeamAssign，可选成员见该工具的描述」；
`TeamAssign` 的可选成员正本只有一个——`team.py` 从本轮 `TeamRoundContext.members`（该频道可用成员）生成的
工具描述。队长**就是受派者**时保留原来那份 `transfer_task` 名单（那份的来源与工具授权一致）。

| 命令 | 读数 |
| --- | --- |
| `pytest tests/test_team_task_threads.py -k does_not_list_workspace_agents` | `1 passed`：`频道外成员` 不在 prompt、`可转派 Agent` 不在 prompt、`TeamAssign` 在 prompt |
| `pytest tests/test_team_task_threads.py -k team_assign_in_a_task_thread` | 真实适配器用例仍绿（队长 prompt 仍含队长身份 / `TeamAssign` / `transfer_task` 事实句） |

**红 → 绿**（改实现前）：

```text
>       assert outsider["display_name"] not in prompt, "频道外的 Agent 不是 TeamAssign 的可选项"
E       AssertionError: 频道外的 Agent 不是 TeamAssign 的可选项
E       assert '频道外成员' not in '…\n1. 频道成员\n2. 频道外成员'
```

### 10.3 本轮增量的双轴 review 与处置

固定点 `147e5b0`（上一轮 head），两个子代理并行只读审查 `147e5b0...HEAD`（单提交）。

| 轴 | finding | 处置 |
| --- | --- | --- |
| Standards | 无硬违规；一处判断项：`agent_seam.py` 的两个分支重复了任务动作的公共提示（Duplicated Code，改动作约束要同步两处） | **已改**：抽出公共前缀 `action_rules`，两个分支只差末尾那段成员 / 转派引导；非队长输出逐字不变（`test_auto_claim.py` 的频道轮次断言仍绿） |
| Spec | P2：`reason.team_change_no_body` 那条分支没有独立覆盖（只有 `silent` 与 `ignored` 两条） | **已补**：`test_a_body_less_leader_reply_on_a_task_change_round_uses_the_task_change_wording`（`EMPTY_REPLY` ⇒ `failed` 且 reason 含「任务变化」「没有产出可落库的正文」、不含「认领」） |
| Spec | P3：`CONTEXT.md` 的改动不在本次 follow-up 的 Target 清单里 | **保留并说明**：改的是上一轮票面 D 项就要求同步的**同一个词条**（**回应判断**），本轮只把新增的文案口径补进那一段（不新增词条、不动其它段落）；不同步会让领域语言正本与 `reason.*` 的取值分叉（review-rules 第 6 条） |

**本轮 follow-up 的全量门禁读数**（`bash scripts/check.sh`，退出码 **0**）：`857 passed, 10 skipped in 358.73s`
（本机 OrbStack 守护进程收尾时已停，8 条 docker 用例 skip；`ruff check` `All checks passed!`、
`ruff format --check` `218 files already formatted`、`mypy` `Success: no issues found in 92 source files`、
`pyright` `0 errors, 0 warnings, 0 informations`、`node --test` `180 pass`、末行 `门禁全绿`）。
