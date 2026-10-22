# 验收取证：联网档 `search+fetch` 下 `fetch` 不再逐次审批

**票型**：实施票（权限语义变更 + 文档/文案同步）｜**门禁**：G-impl（合并由 coordinator 按 G-merge 判定）
**Ticket**：[GitHub issue #242](https://github.com/whutlichao/alibot/issues/242)
**base**：`e989eec`（派活时 main HEAD）｜**branch**：`fetch-no-approval`
**采集时间**：2026-10-22｜**环境**：macOS（aarch64）、Python 3.11.13（worktree `.venv` 符号链接到主工作区 `.venv`）、AgentScope 2.0.10
**改动面**：`app/alibot/web.py`、`app/alibot/agent_seam.py`、`app/alibot/studio/i18n.js`、
`app/README.md`、`CONTEXT.md`、`docs/adr/0026-agent-web-access-is-explicit-opt-in.md`、
`app/tests/{test_permissions,test_web_assembly,test_web_tools}.py`、本文件。

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**实际跑出来的结果**。

---

## 0. 前置 skill 实跑

前置 skill：`/Users/apple/.claude/skills/implement/SKILL.md`。本票映射：

| implement 步骤 | 本票实际做的事 |
| --- | --- |
| 读票面并建票 | `gh issue create` 建 #242（标题/正文按 #236 形态：What to build / Acceptance criteria / 不在本票范围） |
| tdd 垂直切片 | 先改 `test_permissions.py` / `test_web_assembly.py` → 基线代码上 **6 + 2 红**（§2）→ 实现 → 全绿 |
| 单文件用例 + 门禁 | 迭代期跑四个受影响文件；定稿跑一次 `scripts/check.sh` 全量（§3） |
| 双轴 review | Standards + Spec 两轴（`code-review` skill），findings 与处置见 §5 |
| 提交 | 实现 commit + docs/验收 commit（§6），未合并、未推 main |

---

## 1. 判据 → 落点 → 命令与读数

| # | 判据（票面） | 落点 | 命令 | 读数 |
| --- | --- | --- | --- | --- |
| 1 | 无规则时 `ask-writes` / `trusted-writes` 下 `fetch` 判定 **ALLOW** | `web.py` `fetch_tool()` 给 `FetchTool` 传 `permission=PermissionDecision(behavior=ALLOW, …)`（`FunctionTool.__init__` 的既有参数，AgentScope 2.0.10 实测支持） | `pytest tests/test_permissions.py -k "allowed_without_rules"` | `1 passed`：两档均 `ALLOW` |
| 2 | `read-only` 下仍 **DENY**（且不装配） | 装配闸门 `settings.get("mode") != "read-only"` 逐字未动；EXPLORE 不调用 `check_permissions` | 同上 + `pytest tests/test_web_assembly.py -k read_only` | `ALLOW`/`ALLOW`/`DENY`；`fetch not in tools` |
| 3 | **deny 规则仍拒绝、ask 规则仍拉回审批**（本票最关键的判据） | 规则判定在工具 `check_permissions` **之前**（引擎顺序：deny → ask → 只读 → 工具 → allow → 兜底）；`match_rule` 未改 | `pytest tests/test_permissions.py -k "win_over_the_tools_own_allow"` | `1 passed`：两种写档下 deny → `DENY`、ask → `ASK`；未命中的 host 仍 `ALLOW` |
| 4 | 装配层：`fetch` 直接执行、无审批记录 | 无规则路径 | `pytest tests/test_web_assembly.py -k "runs_without_approval"` | `1 passed`：1 条出网请求、`list_approval_requests == []`、无 `REQUIRE_USER_CONFIRM` 事件 |
| 5 | 装配层：ask 规则存在时仍走审批 | `_run_with_decision` 等真实 `ApprovalBroker` | `pytest tests/test_web_assembly.py -k "ask_rule_still"` | `1 passed`：审批记录 `denied`、被拒绝的 host 零请求 |
| 6 | 装配层：deny 规则仍零请求 | 同上 | `pytest tests/test_web_assembly.py -k "deny_rule_blocks"` | `1 passed`：零请求、无审批记录（deny 不是审批） |
| 7 | 装配闸门四条逐字保留；`'off'` / `'search'` / DM 行为不变 | `agent_seam._web_tools()` 只改了注释措辞，四条闸门未动 | `pytest tests/test_web_assembly.py` | `47 passed`（含 `off` / `search` / DM / 能力闸门 / 容器出网） |
| 8 | 系统提示如实描述 | `agent_seam._web_prompt_note()` | `pytest tests/test_web_assembly.py -k "mounts_both"` | `1 passed`：提示仍含「暴露给第三方」，不再声称逐次审批 |
| 9 | `i18n.js` zh-CN / en 成对更新 | `agent.webAccessSearchFetchHint`、`channelConfig.webAccess.search+fetchNote` 各两表共 4 处 | `node --test app/tests/i18n_ui.test.cjs app/tests/agent_detail_ui.test.cjs app/tests/channel_config_ui.test.cjs` | `105 passed`（键集成对 / 无空值 / 参数位一致 / 无裸中文） |
| 10 | ADR-0026 追加修订节，不改既有节正文 | `docs/adr/0026-…md` 追加「修订（2026-10-22，#242）」§十六：明确推翻 §三表格两行与 §三末段「它默认 ASK」，保留「不设 bypass_immune」「host 级规则可表达」，写明理由与 `read-only` 不变 | `tail -30 docs/adr/0026-…md` | 见 §4 |
| 11 | README / CONTEXT 无残留旧措辞 | `app/README.md` 联网档小节、`CONTEXT.md`「联网档」词条 | `rg -n "每次抓取都要审批\|逐次审批" app/README.md CONTEXT.md docs/adr/0026*.md` | 见 §4：README / CONTEXT 只剩「**不再**逐次审批」与本机 shell 的无关句；ADR 的旧句是历史节原文，修订节已声明推翻范围 |
| 12 | 工具本体仍非只读（`read-only` 装配闸门的前提） | `fetch_tool(... is_read_only=False)` 未改 | `pytest tests/test_web_tools.py -k read_only_difference` | `1 passed`：`search.is_read_only is True` / `page.is_read_only is False` |
| 约束 | 不覆盖 `check_permissions`、不改 `match_rule`、不改 `permissions.py` | `git diff e989eec -- app/alibot/permissions.py` | 同左 | 空 diff；`web.py` 的 `FetchTool` 仍只覆写 `match_rule` |
| 约束 | 不跑 `ruff format` | 命令清单 | — | 只经 `scripts/check.sh` 跑 `ruff format --check` |

**AgentScope「实测」标注**（`docs/agents/review-rules.md` §8 / 规约 §2.3）：`FunctionTool.__init__` 的
`permission: PermissionDecision | None` 参数在 2.0.10 的**已安装 wheel** 里存在——`agentscope/tool/_adapters.py:54-65`
声明该参数，`:116-135` 的 `check_permissions` 直接返回它；快照文档 `docs/agentscope/2.0.10/` 没有单列
这个构造参数。仓库内已有同用法：`agent_seam._value_returning_tool`（`FunctionTool(func, permission=…)`），
本票沿用同一形态，不新增签名。复跑：

```console
$ .venv/bin/python - <<'PY'
import inspect
from agentscope.tool import FunctionTool
print(inspect.signature(FunctionTool.__init__))
PY
(func, name=None, description=None, input_schema=None, is_concurrency_safe=True,
 is_read_only=False, is_state_injected=False, middlewares=None, permission=None) -> None
```

---

## 2. 先红后绿（隔离基线）

`scripts/red-evidence.sh` 的入参只接受 `app/tests/*.test.cjs`（它用 `node --test`），无法直接跑
`.py`；本票按该脚本的同一形状手工取红读数：`git archive e989eec app` 导出**基线产品代码**到临时目录，
只把工作区里的新测试文件覆盖进副本，再用同一 `.venv` 跑 pytest（`issue-219-acceptance.md` 有同样的
脚本能力限制先例）。

```console
$ RED=$(mktemp -d "${TMPDIR:-/tmp}/alibot-red-242.XXXXXX")
$ git archive e989eec app | tar -x -C "$RED"
$ cp app/tests/test_permissions.py app/tests/test_web_assembly.py app/tests/test_web_tools.py "$RED/app/tests/"
$ cd "$RED/app" && /Users/apple/orca/alibot/.venv/bin/python -m pytest tests/test_permissions.py -q
...
FAILED tests/test_permissions.py::test_a_fetch_host_rule_matches_the_full_url_input
FAILED tests/test_permissions.py::test_a_fetch_host_rule_written_as_a_url_normalizes_the_same_way
FAILED tests/test_permissions.py::test_fetch_is_allowed_without_rules_in_both_write_modes_and_denied_in_read_only
FAILED tests/test_permissions.py::test_deny_and_ask_rules_still_win_over_the_tools_own_allow
FAILED tests/test_permissions.py::test_a_host_allow_rule_is_redundant_but_harmless_and_local_shell_still_asks
FAILED tests/test_permissions.py::test_a_pattern_that_yields_no_host_does_not_match_everything
6 failed

$ cd "$RED/app" && /Users/apple/orca/alibot/.venv/bin/python -m pytest tests/test_web_assembly.py -q -k "fetch or host or deny_rule"
FAILED tests/test_web_assembly.py::test_fetch_runs_without_approval_in_ask_writes
FAILED tests/test_web_assembly.py::test_every_host_runs_without_approval_when_no_rule_narrows_it
2 failed
```

证据目录：`/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/alibot-red-242.MmfSvi`（未删除）。
基线副本的 `alibot` 来源已核对为副本内路径（`…/alibot-red-242.MmfSvi/app/alibot/__init__.py`），
不是主工作区的可编辑安装。

改动后同一批用例：

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_permissions.py tests/test_web_tools.py \
    tests/test_web_assembly.py tests/test_docker_network_policy.py
87 passed
$ ../.venv/bin/python -m pytest tests/test_permissions.py tests/test_web_tools.py \
    tests/test_web_assembly.py tests/test_docker_network_policy.py \
    tests/test_docs_vocabulary_guard.py tests/test_agentscope_usage_guard.py tests/test_test_layering_guard.py
111 passed
```

---

## 3. 门禁读数

最终 head 上跑了一次全量 `scripts/check.sh`（与 CI 同一个脚本）：

```console
$ scripts/check.sh
== pytest（全量）
1133 passed, 11 skipped in 614.35s (0:10:14)

== ruff check
All checks passed!

== ruff format --check（只检查；不要跑 ruff format，它会整文件重排）
302 files already formatted

== mypy（仓库根）
Success: no issues found in 122 source files

== pyright
0 errors, 0 warnings, 0 informations

== node --test（Studio DOM 用例族：glob，新增文件自动纳入）
ℹ tests 269　ℹ pass 269　ℹ fail 0　ℹ cancelled 0　ℹ skipped 0　ℹ todo 0

门禁全绿
```

---

## 4. 文档与措辞自查

```console
$ rg -n "每次抓取都要审批|每次 ASK|每次调用都要人类" app/ CONTEXT.md docs/adr/0026*.md --glob '!app/specs/**'
（无命中）

$ rg -n "逐次审批" app/README.md CONTEXT.md docs/adr/0026*.md
docs/adr/0026-…md:1      # 标题（历史原文，ADR 不改写标题）
docs/adr/0026-…md:13,17  # §一 / §二 的历史正文（本票明确不改写）
docs/adr/0026-…md:116    # #199 修订节里「§三不变」的括注（历史原文）
docs/adr/0026-…md:186…   # 本票新增修订节：声明推翻 §三的两句并写明新行为
CONTEXT.md:22,531        # 本地 shell（`allow_local_shell`）的逐次审批，本票范围外，保留
app/README.md:422        # 同上（本地 shell）
app/README.md:471        # 「**不再**逐次审批」——新行为的如实描述
CONTEXT.md:285           # 「**不再**逐次审批」——新行为的如实描述
```

判读：README / CONTEXT 里不再有任何「fetch 每次抓取要审批」的旧说法；剩余 `逐次审批` 命中要么是
本地 shell（本票范围外），要么是「**不再**逐次审批」的新否定句。ADR 的旧句全部落在历史节，新修订节
已逐条声明推翻范围（§三表格两行 + §三末段「它默认 ASK」；#199 修订节里重复的括注随之失效）。

另：`agent.webAccessNote` / en 对应词条里「私聊不装配 fetch」的句子仍然成立，按票面**保留**；
`i18n.js` 里同一区块的注释把「私聊没有审批面」改为「私聊的权限档固定 read-only」（与实现事实一致）。

---

## 5. 双轴 code-review

按 `/Users/apple/.claude/skills/code-review/SKILL.md` 跑两轴。**流程偏差（如实记录）**：两个轴本来各由
一个并行子代理执行，但本机 provider 对子代理模型返回 `429 rate_limit`（`usage exceeds frequency limit`，
三个子代理均失败，含换模型重试）；因此两轴改为**同一上下文内逐条人工对照** `docs/agents/review-rules.md`
的标准来源执行，结论与证据如下。子代理不可用是环境事实，不是「已经跑过子代理」。

### Standards 轴

对照来源：`CONTEXT.md` `## Language`、`docs/adr/**`、`app/README.md`、`docs/agents/agentscope-2.md`、
`AGENTS.md`、`docs/agents/implementation.md`。

| 标准 | 判定 | 证据 / 处置 |
| --- | --- | --- |
| ADR 只做最小修订，不改写历史决策 | 通过 | ADR-0026 只**追加**「修订（2026-10-22，#242）」§十六并声明推翻范围；§一 / §二 / §四–§七 与 #199 / #202 修订节原文未动 |
| AgentScope import 白名单 / 精确 pin | 通过 | 只用 `agentscope.permission`（`ALLOWED_MODULES` 已含 `permission`）；pin 未改；`test_agentscope_usage_guard.py` 绿 |
| 规约 §2.3：文档没写的结论标「实测」 | 通过（补记） | §1 末尾补了 `FunctionTool.__init__(permission=…)` 的 wheel 实测读数与仓库内同用法先例 |
| pytest 只断言外部可观察行为 / 不 import 私有名 | 通过 | 新用例只用 `build_permission_engine` 与公开的 `fetch_tool()`；`test_test_layering_guard.py` 绿 |
| 不新开接缝 | 通过 | 复用既有 `_web_decide` 与装配层 `_ModelSubstitutingSeam`；没有新 stub / 新测试框架 |
| 领域语言（联网档 / 权限档 / 数据面确认 / 审批） | 通过 | 新增文字全部沿用词表；未引入「联网开关」「隐私设置」等 `_Avoid_` 说法 |
| 注释与文案语言 | 通过 | 中文；`PermissionDecision` 的 `message` / `decision_reason` 也是中文 |
| 不为单票改共享层 | 通过 | `permissions.py` / `match_rule` / 装配闸门零改动（`git diff e989eec -- app/alibot/permissions.py` 为空） |
| Fowler 基线（重复形状 / 投机抽象等） | 无命中 | 新的 ALLOW 决策只构造一次；测试辅助 `_fetch_rule` / `_web_decide` 复用既有形状 |

**findings**：1 条 — 规约 §2.3 的「实测」标注缺失（已补进 §1 末尾）。无 hard violation。

### Spec 轴

对照来源：issue #242 正文 + 本文件 §1 + ADR-0026（含新修订节）。

| 票面要求 | 判定 | 备注 |
| --- | --- | --- |
| 无规则时两档 ALLOW / `read-only` DENY | 已实现 | §1 #1/#2 |
| deny 仍 DENY、ask 仍 ASK、规则先于工具判定 | 已实现 | 权限层 + 装配层双层取证（§1 #3/#4/#5/#6） |
| `allow` 规则冗余但无害、能力不删 | 已实现 | `test_a_host_allow_rule_is_redundant_but_harmless_and_local_shell_still_asks` |
| `read-only` 不装配、四条装配闸门逐字保留 | 已实现 | `_web_tools` 只改注释；`'off'` / `'search'` / DM 用例全绿 |
| 系统提示 / i18n 成对 / README / CONTEXT / ADR 修订节 | 已实现 | §4；`rg` 无旧措辞 |
| 不在本票范围（`web_search` / DM / schema / 审批卡前端） | 未触碰 | `git status` 文件清单 |
| 红证据 + 全量门禁 | 已实现（机制有偏差） | `scripts/red-evidence.sh` 只接受 `*.test.cjs`，`.py` 红证据按同一 `git archive` 形状手工取得（§2），已记录 |
| 票据 Status / Answer | 已实现 | issue #242 已建；PR 以 `Closes #242` 收口，Answer 即本文件 |

**findings**：1 条 — 票面 Observable acceptance #3 指定 `scripts/red-evidence.sh <base-rev> app/tests/test_permissions.py`，
但脚本第 3 段的用法校验只放行 `app/tests/*.test.cjs`（`.py` 会以退出码 2 拒绝）；本票用等价隔离副本取证，
并把「是否扩展脚本支持 pytest」列为遗留（§7）。这不是实现缺陷，是票面与工具能力的接缝错位；未擅自改脚本
（脚本不在 Target 内）。

**两轴总结**：Standards 1 条（已修，实测标注）；Spec 1 条（工具能力错位，已记录为遗留）。两轴均无
「实现错误」类 finding。

---

## 6. 提交与 PR

（待回填 commit、PR 号与 body 路径）

---

## 7. 遗留 / 风险

- **语义变更需人审**：本票改变了 `fetch` 的默认审批行为（既有默认值/语义变更），但它是 coordinator
  已获授权的产品决策，且无 schema / 数据迁移 / 公共 API 破坏。PR 的 Merge Danger 一节如实写明，
  由 coordinator 按 G-merge 判定。
- `scripts/red-evidence.sh` 只支持 `*.test.cjs`；Python 红证据按同一形状手工取得（§2）。是否把脚本
  扩成支持 pytest 是另一张票，本票不改脚本（越出 Target）。
- 本票不跑：无（实施票；全量门禁在最终 head 上跑一次）。
