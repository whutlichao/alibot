# 验收记录：#328 门禁提速（5 条 55 秒硬等待 + 全量 pytest 并行化）

**票型**：实施票｜**门禁**：G-impl｜**Door**：双向（可整体回滚；但属「门禁自身的改动」，见 PR 的 Merge Danger）
**Ticket**：[GitHub issue #328](https://github.com/whutlichao/alibot/issues/328)
**base**：`f47652b`（本 worktree 的 base）
**branch**：`issue-328-impl`
**worktree**：`/Users/apple/orca/workspaces/alibot/issue-328-impl`
**采集时间**：2026-10-28｜**环境**：macOS（aarch64，8 核）、Python 3.11.13（worktree `.venv` 符号链接到主工作区）、Node v25
**改动面**：`app/alibot/api.py`、`app/tests/conftest.py`、`app/tests/test_agent_round.py`、
`app/tests/test_bounded_relay.py`、`app/tests/test_event_stream.py`、`app/pyproject.toml`、
`scripts/check.sh`、新增 `app/tests/test_sse_lifetime_config.py`，加本文件与 PR body。

---

## 0. 判据总览

| # | 判据 | 落点 | 读数 |
| --- | --- | --- | --- |
| 1 | 7 条慢用例降速（5×55s → 约 2s；2×10s → 亚秒级） | `api.py` 运行时读取 + `conftest.py` session 装置 + 两处旧 seam 释放 | §1 前后并列 |
| 2 | 全量门禁绿并留读数（含 `-n 4` 的 CI 等价形态） | `scripts/check.sh`、`pyproject.toml` | §2 |
| 3 | 生产默认未被改小（55.0） | `test_sse_lifetime_config.py` + 手工读数 | §3 |
| 4 | rotate 语义未被削弱 | `test_event_stream.py` 两条锚点用例 | §4 |
| 5 | 双轴 code-review 收口 | 本票 diff | §5 |
| 6 | PR（Summary / Evidence / Merge Danger） | `app/specs/issue-328-pr-body.md` | §6 |

---

## 1. 7 条慢用例：改前 / 改后并列

### 命令（两条都以 `app/` 为 cwd，同一份解释器）

```sh
# 改前（base f47652b 的干净工作区，在改动落地**之前**跑的同一命令；见 §1.1）
../.venv/bin/python -m pytest -p no:cacheprovider --durations=0 \
  tests/test_event_stream.py::test_rotate_frame_lets_the_client_tell_a_rotation_from_an_outage \
  tests/test_event_stream.py::test_global_event_stream_rotates_and_sends_no_replay_before_it \
  tests/test_event_stream.py::test_event_stream_of_a_channel_whose_target_row_is_missing \
  tests/test_tasks.py::test_task_event_stream_replays_thread_messages_and_rejects_missing_tasks \
  tests/test_auto_claim.py::test_both_claim_records_reach_the_live_stream_and_match_the_replay \
  tests/test_agent_round.py::test_in_flight_round_is_not_replayed_after_restart \
  tests/test_bounded_relay.py::test_in_flight_round_is_not_replayed_after_restart

# 改后（同一命令，代码已改）
```

### 改前（base `f47652b`，本机实测）

```text
55.47s call     tests/test_event_stream.py::test_rotate_frame_lets_the_client_tell_a_rotation_from_an_outage
55.42s call     tests/test_event_stream.py::test_global_event_stream_rotates_and_sends_no_replay_before_it
55.12s call     tests/test_event_stream.py::test_event_stream_of_a_channel_whose_target_row_is_missing
55.12s call     tests/test_auto_claim.py::test_both_claim_records_reach_the_live_stream_and_match_the_replay
55.10s call     tests/test_tasks.py::test_task_event_stream_replays_thread_messages_and_rejects_missing_tasks
10.01s call     tests/test_bounded_relay.py::test_in_flight_round_is_not_replayed_after_restart
10.01s call     tests/test_agent_round.py::test_in_flight_round_is_not_replayed_after_restart
7 passed in 297.53s (0:04:57)
```

### 改后（同一台机器、同一命令）

```text
2.87s call     tests/test_event_stream.py::test_event_stream_of_a_channel_whose_target_row_is_missing
2.39s call     tests/test_event_stream.py::test_global_event_stream_rotates_and_sends_no_replay_before_it
2.19s call     tests/test_tasks.py::test_task_event_stream_replays_thread_messages_and_rejects_missing_tasks
2.17s call     tests/test_auto_claim.py::test_both_claim_records_reach_the_live_stream_and_match_the_replay
1.50s call     tests/test_event_stream.py::test_rotate_frame_lets_the_client_tell_a_rotation_from_an_outage
0.55s call     tests/test_bounded_relay.py::test_in_flight_round_is_not_replayed_after_restart
0.51s call     tests/test_agent_round.py::test_in_flight_round_is_not_replayed_after_restart
7 passed in 14.76s
```

（首次改后运行读数为 `7 passed in 12.89s`、`7 passed in 14.43s`；上面这次是随后一次独立重跑。
`test_stream_frames_use_seq_as_the_event_id` 之类不在清单里的用例在这条命令下不参与。）

对照票面预期（5 条 55 秒级 → 约 2 秒级；2 条 10 秒级 → 亚秒级）：

| 用例 | 改前 | 改后 | 判据 |
| --- | --- | --- | --- |
| `test_rotate_frame_lets_the_client_tell_a_rotation_from_an_outage` | 55.47s | 1.50s | 该用例自己 `setenv(...,"1")`，读到 rotate 即断开 |
| `test_global_event_stream_rotates_and_sends_no_replay_before_it` | 55.42s | 2.39s | session 装置给的 2 秒 |
| `test_event_stream_of_a_channel_whose_target_row_is_missing` | 55.12s | 2.87s | 同上 |
| `test_both_claim_records_reach_the_live_stream_and_match_the_replay` | 55.12s | 2.17s | 同上 |
| `test_task_event_stream_replays_thread_messages_and_rejects_missing_tasks` | 55.10s | 2.19s | 同上 |
| `test_bounded_relay…::test_in_flight_round_is_not_replayed_after_restart` | 10.01s | 0.55s | 释放旧 seam 的闸门 |
| `test_agent_round…::test_in_flight_round_is_not_replayed_after_restart` | 10.01s | 0.51s | 同上 |

合计：297.53s → 14.76s（约 **20 倍**）。

### 1.1 红证据为什么是「基线读数」而不是「红测试」

本票**不新增行为**，它改的是「什么时候读配置」和「门禁怎么跑」，没有一条断言在改前是红的。
可证的「红」只有**耗时**：同 7 条用例在 base `f47652b` 上花 297.53s，改后 14.76s。这条读数就是
票面第 1 条判据的全部内容（「降不下来就说明改法不对，不许用『已接近』搪塞」）。

改前的读数是在**本 worktree 的干净 base `f47652b` 上**取的（改动尚未落地时跑的同一条命令），
不是从票面或其他分支搬来的数字。基线跑之前的唯一准备是 `app/frontend` 的
`npm ci --include=dev` + `npm run build`（产物不入库，缺它 `create_app` 会在 collection 阶段
直接 `RuntimeError`；这与票面基线 `756.90s` 的取法一致）。

---

## 2. 全量门禁

### 2.1 本机全量（`./scripts/check.sh`，收口后、负载回落后重跑）

```sh
( time ./scripts/check.sh )      # exit 0
```

```text
== 构建工作室产物（app/frontend → app/alibot/studio/v2） ==
✓ 65 modules transformed；产物 index-Cgy44FOx.js（303.41 kB）

== pytest（全量） ==
1278 passed, 11 skipped in 78.98s (0:01:18)

== ruff check ==                     All checks passed!
== ruff format --check ==            371 files already formatted
== mypy（仓库根） ==                  Success: no issues found in 135 source files
== pyright ==                         0 errors, 0 warnings, 0 informations
== Vitest（app/frontend） ==          Test Files 23 passed (23) / Tests 437 passed (437) in 4.88s

门禁全绿
real 1m36.072s
```

**整条门禁 1 分 36 秒**（票面无提速前的同一条门禁在本机是 12 分钟量级：pytest 串行 756.90s
再加上构建与静态检查）。

> 采集过程里宿主曾长期高负载（8 核机器 load average 47–170），那一批读数（例如门禁 `real` 32m38s、
> `-n 4` 321s）**已作废**，§2.4 给了对照表。负载回落到 5–12 之后重跑得到上面这组。

### 2.2 CI 等价形态（`-n 4 --dist loadfile`，CI runner 4 核）

```sh
cd app && ../.venv/bin/python -m pytest -n 4 --dist loadfile
```

收口后重跑（本机负载已回落到 5–7，与下面的对照同条件）：

```text
1278 passed, 11 skipped in 120.13s (0:02:00)
```

`--dist loadfile` vs 默认 `--dist load` 的 A/B（同一时段、同一台机器、均 `-n 4`）：

```text
loadfile: 1278 passed, 11 skipped in 120.13s
load:     1278 passed, 11 skipped in 117.42s
```

**速度上两者同档**，因此选 `loadfile` 的理由是**语义**而不是提速：本仓用例按文件共享装置与顺序
假设，跨文件打散会破坏这个前提。早先在宿主高负载期间测到的 `321.72s`（`-n 4`）已作废——那是
负载 47–170 期间的读数，不代表分发方式的差异。

默认 `-n auto`（本机 8 核）同一条命令的独立读数（收口后、同一时段）：

```text
1278 passed, 11 skipped in 75.17s (0:01:15)
```

两档**都退出 0**，用例数与串行基线一致（1278 passed / 11 skipped；串行基线 756.90s 是
1271 passed / 11 skipped，多出的 7 条是 `test_sse_lifetime_config.py` 的 7 条新守卫用例）。

### 2.3 `--fast` 与 `--studio` 仍串行（未改）

```sh
./scripts/check.sh --fast tests/test_sse_lifetime_config.py   # 串行，exit 0
```

`scripts/check.sh` 里只有 `else`（全量）分支加了 `-n auto --dist loadfile`；`--studio` 与
`--fast <files>` 的 `pytest` 调用一字未改，窄档读数仍可与历史对比。

### 2.4 读数口径（重要）

宿主在本次采集的大部分时间里处于高负载（8 核机器上 load average 47–170，多个无关进程抢核）。
**早先那批绝对耗时已作废**，换成负载回落到 5–12 之后重测的读数：

| 形态 | 高负载期间（作废） | 负载回落后的重跑（采信） |
| --- | --- | --- |
| `./scripts/check.sh` 整条 | 32m38s | **1m36s** |
| `-n 4 --dist loadfile` | 321.72s / 134.99s（附 4 个 worker 启动错误） | **120.13s** |
| `-n 4 --dist load` | 122.23s / 119.53s | **117.42s** |
| `-n auto --dist loadfile` | 199.28s | **75.17s**（门禁内那一次 78.98s） |

串行基线 756.90s 采于正常负载（票面提供）。因此可信的提速比是：
**756.90s（串行）→ 120.13s（4 核，CI 等价形态）≈ 6.3×**；本机 8 核 `-n auto` 是 **75.17s ≈ 10×**。

> 那段高负载也是有价值的证据：`--dist loadfile` 在一次超载运行里出现过 4 个 worker 启动错误
> （`Timeout waiting for worker to respond`），而 `load` 同条件没出——但那是宿主争核导致的
> worker 启动超时，不是分发模式的差异：负载回落后 loadfile 连跑多次均为 exit 0（见上表与
> §2.1 / §2.2 的重跑）。

---

## 3. 生产默认仍是 55 秒

守卫文件：`app/tests/test_sse_lifetime_config.py`

```sh
cd app && ../.venv/bin/python -m pytest tests/test_sse_lifetime_config.py -v
```

```text
tests/test_sse_lifetime_config.py .......                                [100%]
7 passed in 4.37s
```

7 条 = 1 条「默认 55.0」+ 1 条「调用时读环境变量」+ 5 条 `parametrize` 的非法值回退
（`""` / `"   "` / `"not-a-number"` / `"0"` / `"-3"`）。

手工读数（不带环境变量，直接调运行时函数；`-I` 隔离模式避免 cwd 上的同名模块被先导入）：

```text
$ cd app && ../.venv/bin/python -I -c "import os,sys; sys.path.insert(0,'.'); ..."
no env  -> 55.0
env=9   -> 9.0
env=bogus -> 55.0
```

这两条读数分别锁住「默认值没被改小」与「是运行时读取而非导入期快照」——把任一改成错的，
守卫就红，不必等到 5 条用例重新变成 55 秒才发现。

---

## 4. rotate 语义未被削弱

两条对外行为锚点（票面点名）连同另外 3 条 55 秒级用例一起跑：

```sh
cd app && ../.venv/bin/python -m pytest -v \
  tests/test_event_stream.py::test_rotate_frame_lets_the_client_tell_a_rotation_from_an_outage \
  tests/test_event_stream.py::test_global_event_stream_rotates_and_sends_no_replay_before_it \
  tests/test_event_stream.py::test_event_stream_of_a_channel_whose_target_row_is_missing \
  tests/test_tasks.py::test_task_event_stream_replays_thread_messages_and_rejects_missing_tasks \
  tests/test_auto_claim.py::test_both_claim_records_reach_the_live_stream_and_match_the_replay
```

```text
tests/test_event_stream.py ...                                           [ 60%]
tests/test_tasks.py .                                                    [ 80%]
tests/test_auto_claim.py .                                               [100%]
5 passed in 10.50s
```

断言**一条未改**，全部照旧通过：

- `test_rotate_frame_lets_the_client_tell_a_rotation_from_an_outage`：轮换前先发完 `message` +
  `member_status`，随后恰好一个**命名事件** `event: rotate`（不是 keep-alive 注释）。
- `test_global_event_stream_rotates_and_sends_no_replay_before_it`：`names == ["rotate"]`
  （全局流不补拉，轮换是连接后第一个命名事件），且 rotate 之后**读到 EOF、无任何后续命名事件**
  （流确实自己收尾）。

这 5 条在 `-n auto --dist loadfile` 的全量里也全部通过（§2.1 的 1278 passed）。
`SSE_POLL_SECONDS`、四条路由的帧格式与对外语义**一行未动**——本票只改「什么时候读配置」。

---

## 5. 双轴 code-review

按 `docs/agents/review-rules.md` 的「三轴分工」跑 Standards + Spec 两轴（AgentScope 轴不适用：
本票零 `agentscope` 改动）。两轴各由一个独立子代理在 `git diff f47652b` 上跑，结论分开记、不互相抵消。

### 5.1 Standards 轴

1. **（P1，已修）`app/pyproject.toml` 的注释把门禁命令又抄了一份，且理由写错。** 原注释断言
   「全量分支用 `-n auto --dist loadfile`」——那是 `scripts/check.sh` 与
   `docs/agents/implementation.md`「命令清单是缓存，缓存会漂移」明令避免的第二份缓存；后半句
   「没有 xdist 时静默退回串行」也是错的（真实现象是 `unrecognized arguments: -n`）。
   **处置**：注释改成只指回 `scripts/check.sh`（「并行度与分发方式只写在那里」），并把真实现象写对。
2. **（P2，已记录）`.github/workflows/check.yml` 的「运行依赖 + dev extra（pytest / ruff / mypy /
   httpx2）」注释清单未同步 xdist。** 该文件按 `pip install -e "app[dev]"` 安装，**实际依赖清单
   取自 `pyproject.toml`**，所以行为不受影响；但该行确实是同一种「清单缓存」。本票 Constraints
   明确「不许改 `.github/workflows/check.yml`」，故**不改**，记在这里供后续动 CI 的票顺手收口。
3. **（P1，已修）2 秒寿命对全部 SSE 用例生效，裸取 `event["type"]` 的读线程会收到 `event: rotate`。**
   `_start_reader`（`test_event_stream.py`）原先会把任何 `data:` 帧记进 `frames`，而 rotate 帧的
   载荷是 `{}`——多记一帧就让「这里只该有一条 message」这类断言变成 `[None, "message"]`。
   **处置**：`_start_reader` 收到 `event: rotate` 即结束（服务端已收尾，与「读到流结束」同一条
   路径），并写明理由。断言一条未放宽。

   **轮换帧容忍的授权范围（协调者裁定，见 §5.4）**：审计了 4 个不在 Target 清单内、但又开着
   SSE 连接的文件后，只有 **1 个**真的会因此红。逐文件判据：

   | 文件 | 帧取用方式 | 风险 |
   | --- | --- | --- |
   | `test_team_visibility.py` | 9 处全是 `.get("type")`，裸取 0 处 | 无 |
   | `test_task_channel_pointer.py` | `.get("type")`，裸取 0 处 | 无 |
   | `test_approvals_http.py` | 先用 `.get("type")` 过滤再 `types.add(event["type"])`；断言遍历的是已过滤的 `seen["events"]` | 无（空帧进不了 `events`） |
   | `test_round_outcome_visibility.py` | **裸取** `item["type"]`，且 `events` 收**全部** data 帧 | **真实风险，已修** |

   空帧落进 list ⇒ 取 `event["type"]` 抛 KeyError，这条链路只在**裸取**时成立。所以只在
   `test_round_outcome_visibility.py` 的读线程里跳过空载荷（2 行），另三个**不改**——改了是无谓
   diff，还会误导后来者以为那里有风险。断言一字未动（跳过的是空 dict，不参与任何断言）。
4. **（P2，已修）解释重复。** `api.py` 的块注释与 `sse_stream_max_seconds()` docstring 说了同一件事；
   `check.sh` 头里「并行只在全量」写了两遍。**处置**：`api.py` 块注释压成一句并指向函数；
   `check.sh` 删掉第二遍，只在「用法」段讲一次。
5. **（P2，已修）`test_sse_lifetime_config.py` 的合法性未言明。** 它直接调公开函数，按
   `review-rules.md` 判断题 3 属「接缝级配置用例」；而「默认 55 秒」没有 HTTP 可观察面（唯一行为
   读法是真等 55 秒）。**处置**：在文件头与「非法值回退」那条的 docstring 写清为什么非接缝不可、
   以及它是配置策略守卫而非行为用例。
6. **（P3，不修）`conftest.py` 的第三个 session 装置与前两个手抄同一个「保存→设置→yield→恢复」形状。**
   抽帮助函数会让三处读起来更短，但现有两处的注释各自解释**为什么**要恢复，合并后反而要在一个
   函数里塞三套理由；且本仓两个既有装置就是这个形状，跟着既有体例更省认知。保持现状。
7. **（P3，不修）「四条路由都必须调 `sse_stream_max_seconds()`」只靠文字约束。** 机械化为 AST 守卫
   是可行的，但这是**新增一条守卫测试**，超出本票「新增一个守卫文件（生产默认 55 秒）」的授权范围；
   记为后续可做的事，不在本票扩 scope。
8. **已核**：`sse_stream_max_seconds()` / `SSE_STREAM_DEFAULT_MAX_SECONDS` 的命名与位置与模块既有
   `live_sse_frame` / `SSE_POLL_SECONDS` 体例一致；命令形状依赖面已 grep
   （`build-guard.test.js` 只匹配 `step "pytest（全量）"`，该步骤名**保留未改**；CI 原样调
   `./scripts/check.sh`；`red-evidence.sh` 不跑 pytest）。

### 5.2 Spec 轴

六项逐条核过，**全部做到**：

| 项 | 结论 | 落点 |
| --- | --- | --- |
| 1 运行时读取（默认常量 + 每次调用读 + 四处调用点） | 完全做到，无残留 `SSE_STREAM_MAX_SECONDS` 引用 | `api.py:157` / `:170` / 四处 deadline |
| 2 session 级装置 2 秒 + 两条 docstring 改写 | 做到；`setenv(...,"1")` 的「等轮换」意图确认成立 | `conftest.py:100`、`test_event_stream.py:398/933` |
| 3 两处旧 seam 释放 | 做到，实测 0.51s / 0.55s | `test_agent_round.py:435`、`test_bounded_relay.py:757` |
| 4 `pytest-xdist>=3.5` + 中文注释 | 做到 | `pyproject.toml:41` |
| 5 仅全量分支并行 + 注释同步 | 做到，步骤名保留 | `scripts/check.sh` |
| 6 守卫测试（默认 55.0 + 调用时读取） | 做到，另加非法值回退 | `test_sse_lifetime_config.py` |

Spec 轴另提三处「未被要求」：

- **P2，如实记录**：新增未被要求的 `app/specs/issue-328-pr-body.md`。票面 Observable acceptance 第 6 条
  要求「用 pr skill 编写 PR body」并以 `--body-file` 创建 PR——body 必须落成文件才可用
  `--body-file`，因此这是**执行票面动作的必然产物**，不是 scope 扩张。与既有
  `app/specs/issue-*-pr-body.md` 同形。
- **P3，保留**：守卫里多出的「非法值回退」参数化（5 条）。票面第 6 条要求「至少」两件事，这第三条
  守的是同一函数同一条容错口径，不加新代码面；已在 docstring 标明它是配置策略守卫。
- **P3，已修**：「555 秒」笔误改成「周期性的」——原文「555 秒」是 `555` 与 `55` 的笔误，照
  票面「把该段 docstring 改写成当前的真实口径」一并改掉。
- **P1，已修（同一处，与 Standards #3 合流）**：Spec 轴独立发现非目标 SSE 用例的连接时长
  （1.26–1.35s）对 2 秒余量偏薄，且读线程不处理 rotate。处置见 Standards #3。

### 5.3 收口后的验证

收口改动（`_start_reader` 的 rotate 收尾 + `test_round_outcome_visibility.py` 跳过空载荷帧
+ 三处注释/docstring）之后，一次跑完所有受影响的文件：

```sh
cd app && ../.venv/bin/python -m pytest -p no:cacheprovider \
  tests/test_round_outcome_visibility.py tests/test_event_stream.py tests/test_approvals_http.py \
  tests/test_team_visibility.py tests/test_task_channel_pointer.py tests/test_dm.py \
  tests/test_tasks.py tests/test_auto_claim.py tests/test_sse_lifetime_config.py
```

```text
125 passed in 42.14s
```

随后重跑全量（§2 的读数已按收口后的代码更新）。

### 5.4 协调者裁定（`orca orchestration ask` 回信）

我最初按「4 个文件都有风险」上报，协调者实测后**缩小**了授权范围：

> 只修真正脆弱的那 1 个文件，不是 4 个。…… `test_team_visibility.py` 9 处全是 `.get("type")`，
> 裸取 0 处 —— 不可能 KeyError；`test_task_channel_pointer.py` 同；`test_approvals_http.py`
> 第 152 行用 `.get("type")` 过滤后才 `types.add(event["type"])` —— 不可能；
> `test_round_outcome_visibility.py` 第 196 / 204 行裸取 `item["type"]`，且 `events` 收的是**全部**
> data 帧 —— 真实风险。…… **可以改**：`app/tests/test_round_outcome_visibility.py`，仅在把 data 帧
> append 进 `events` 之前跳过空载荷。**不许改**：另三个。**断言一字不许动。**

同一回信里另有两条口径，已照办：

1. `conftest.py` 的 fixture docstring**不得**把「连跑 3 次全绿」写成余量证据
   （本机负载 47–170 期间跑出来的稳定性不算证据）。已改成**结构性**论据：rotate 最早只可能在
   **0.53 秒**到达（`SSE_POLL_SECONDS = 0.5` 的轮询粒度，这是下限），而寿命 2 秒是它的 4 倍。
2. PR body 的耗时读数**必须标注**采集期间的负载：本机读数采于负载 47–170 期间，绝对值偏高，
   仅作方向参考；CI 读数为准。§2 与 PR body 都已标注。

协调者给出的干净参考读数（他本机干预前的）是 `-n 4` 122s、`-n 8` 97s；本票在高负载下测到的
`-n 4` 321.72s 明显被抬高，已在 §2 说明。

---

## 6. PR

| 项 | 值 |
| --- | --- |
| PR | [#329](https://github.com/whutlichao/alibot/pull/329)（head `issue-328-impl` → base `main`） |
| PR body | `app/specs/issue-328-pr-body.md`（用 `--body-file` 提交，正文以 `Closes #328` 结尾） |
| commit | `ba479ab`（本票唯一代码提交） |
| 推送 | `git push -u origin issue-328-impl`（SSH；分支已建立 upstream） |

body 分三段（`## Summary` / `## Evidence` / `## Merge Danger`），Evidence 段贴的是 §1–§4 的实测
读数（7 条慢用例前后并列、全量门禁读数、`-n 4` 与 `-n auto` 两档、守卫用例与手工读数）。
Merge Danger 如实写明：本票改的是**门禁入口**与**全局测试装置**，属「门禁自身的改动」——一旦有偏差，
后续每张票的门禁读数都不可信，并点名请人重点看第 2 条（未放宽判据）与第 6 条（守卫）。
