<!-- retro 收口：10 条候选全部落地。与已合入的 #127 去重后交付，非票面工作，不挂 Closes。 -->

## Summary

这轮 retro 出结论时，仓库已经被一次**重叠的落地**推进过（#127 把五道门禁接上了 Actions，并在
`AGENTS.md` 记了本机噪声）。本 PR 是**去重后的收口**（另一个会话的 #127 已经接上 CI，随后 #128 又修掉了 CI 首跑暴露的 SSE 竞态——
本 PR 不重复这两件事，只把它们收成单一入口与可机械判的守卫），一句话：把「命令抄在各处」收敛成**一个脚本**，
把「靠 reviewer 人眼守」收敛成**三条守卫测试**，把「跑绿取决于环境干净」收敛成**测试自己处理环境**。

| Retro 候选 | 落点 | 与 #127 的关系 |
| --- | --- | --- |
| ① 没有任何自动闸门 | `scripts/check.sh` 为唯一入口；workflow 收敛为**一条** `./scripts/check.sh` | #127 已接 CI，但命令在 workflow / README 各一份；本 PR 收敛成一条 |
| ② 门禁清单随票面冻结、新测试文件掉出门禁 | `check.sh` 的 Node 用例族走 **glob**；`app/README.md` 检查段改指脚本 | #127 的 CI 已对 node 用 glob；本 PR 扩到本地入口并去掉 README 的逐文件清单 |
| ③ 测试隐式依赖宿主环境 | `conftest.py` 的 autouse fixture 摘 `no_proxy` 的方括号 IPv6 条目；`check.sh` 内置 `unset GIT_CONFIG_*` | #127 只在注释 / AGENTS.md 记了绕法；本 PR 把它修掉 |
| ④ 「pytest 只断言外部可观察行为」无检查 | `app/tests/test_test_layering_guard.py` | 新增 |
| ⑤ 文档词表与数据契约会漂移 | `app/tests/test_docs_vocabulary_guard.py` | 新增 |
| ⑥ `SCHEMA_VERSION` 被钉成历史号 | `app/tests/test_schema_version_pins_guard.py` | 新增（main 那处等值断言已由 #125 放宽，本 PR 只加守卫） |
| ⑦ 没有「怎么跑多票实施」的文档 | `docs/agents/implementation.md` + `AGENTS.md` 的 Implementation 指针 | 新增 |
| ⑧ review 的标准来源没有指针 | `docs/agents/review-rules.md` + `AGENTS.md` 的 Code review 指针 | 新增 |
| ⑨ 合并后不 push，worktree 被清理差点丢工作 | `scripts/merge-ticket.sh`（`--no-ff` + `push` + 打印 `branch/sha/ticket`）+ 文档纪律 | 新增 |
| ⑩ 工具经济 | `check.sh --fast`；README 写明「不要补 `-q`」；`implementation.md` 记录等待纪律 | 新增 |

```diff
+ scripts/check.sh                    # 全量 / --fast；Node 用例族走 glob；pyright 入口自动解析
+ scripts/merge-ticket.sh             # merge --no-ff + 立刻 push + 打印 branch/sha/ticket
  .github/workflows/check.yml         # #127 接上的五道命令 → 收敛成一条 ./scripts/check.sh
+ docs/agents/implementation.md       # 门禁、环境坑、schema 号段、测试分层、worktree/合并/push
+ docs/agents/review-rules.md         # 标准来源清单 + 只有判断题才配得上的 7 条规则
+ app/tests/test_test_layering_guard.py
+ app/tests/test_docs_vocabulary_guard.py
+ app/tests/test_schema_version_pins_guard.py
  AGENTS.md                           # +Implementation / +Code review 两条指针；噪声段按已修的两条更新绕法
  app/README.md                       # 检查段改指 scripts/check.sh；登记三条守卫
  app/tests/conftest.py               # +autouse：摘掉 no_proxy / NO_PROXY 里的方括号 IPv6 条目
  mypy.ini                            # 注明 files 按 cwd 解析、必须在仓库根跑
```

## Evidence

**门禁（在本分支上、用新脚本本身跑；已 rebase 到 main `afb05e5`）**

```console
$ ./scripts/check.sh
pytest（全量）        → 653 passed, 2 skipped
ruff check            → All checks passed!
ruff format --check   → 137 files already formatted（未跑 ruff format）
mypy（仓库根）        → Success: no issues found in 61 source files
pyright               → 0 errors, 0 warnings, 0 informations
node --test（glob）   → tests 132 / pass 132 / fail 0
门禁全绿
```

**红证据一：分层守卫真的会红（探针文件，跑完已删）**

```console
$ # 探针：断言 SEAM_STATUSES 形状 + 读 studio/app.js + import _system_prompt
$ pytest tests/test_test_layering_guard.py -q
FAILED test_pytest_does_not_read_studio_sources
FAILED test_pytest_does_not_assert_the_shape_of_vocabulary_constants
FAILED test_pytest_does_not_import_private_alibot_names
3 failed   # 删掉探针后：全部通过
```

**红证据二：代理噪声的因果链**

```console
ambient NO_PROXY = localhost,127.0.0.1,::1,[::1]
ambient request -> InvalidURL Invalid port: ':1]'
sanitized NO_PROXY = localhost,127.0.0.1,::1
sanitized request -> 200
```

修复后**带脏环境**直接跑（不做任何 `env -u`）：`tests/test_agent_seam_client_lifecycle.py` → `4 passed`。

**红证据三：版本号守卫带正控**（`test_the_scanner_catches_both_pin_shapes_and_allows_the_derived_forms`）：
`SCHEMA_VERSION == 13` 与 `SCHEMA_VERSION == PIN`（同文件 `PIN = 13`）都被判死；`>= 13`、
`SCHEMA_VERSION - 1 == 12`、`steps[-1][1] == SCHEMA_VERSION`、`str(SCHEMA_VERSION)` 全部放行。

**交叉验证：新守卫在 #125 合入后的树上仍绿**（避免「两个 PR 各自绿、合起来红」）

```console
studio reads: {test_reminder_delivery.py}     # 唯一已登记豁免
vocab shape: {}
private imports: {}
schema pins: {}
```


## Merge Danger

**Door:** two-way

没有 schema 变更、没有数据迁移、没有对外 API/UI 变更。回滚 = 删掉新增文件与几行文档/配置；
`app/tests/conftest.py` 的改动只在测试进程内改 `no_proxy`，不影响产品行为。

**Blast Radius:** 开发流程（dev-wide，产品面为零）

- CI 仍是同一个 job，但**步骤从五条收敛成一条**（`./scripts/check.sh`）：以后加门禁只改脚本；
- 提交前多一步 `./scripts/check.sh`；`--fast` 是秒级，给开发循环用；
- pytest 多 16 条守卫用例（合计 653 passed）；守卫扫 `app/tests/test_*.py` 的 AST，新测试文件自动纳入；
- 与 #127 的重叠已就地解决：`.github/workflows/check.yml` 被本 PR 改写为一条命令；`AGENTS.md` 保留
  #127 的「本机已知环境噪声」段，并按本 PR 真修掉的两条更新了绕法；`app/tests/test_reminder_schema_step.py`
  **不再修改**（main 已由 #125 放宽）；
- **并发提示**：本 PR 期间仓库被另一个会话推进过（#125 / #127 / #128 合入）。本分支已重建在
  `bd4dd99` 之上；`test_event_stream.py` 的 SSE 竞态由 #128 修掉，本 PR **不再改它**（我先前那版
  等终态的修复已丢弃，避免重复）。
- `scripts/check.sh` 依赖 `pyright`（不在 repo 依赖里）：本地按 `PYRIGHT_BIN` → `node_modules/.bin/pyright`
  → PATH → `~/.pi-lens/...` 依次解析，CI 里 `npm install --global pyright`。解析不到时脚本直接报错，
  不静默跳过。
