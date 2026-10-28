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

### 2.1 本机全量（`./scripts/check.sh`，SSE 提速收口后、负载回落后重跑）

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

> 这组是**本票前半段（仅 SSE 提速）**的读数，那时 §7 的 docker 守卫还没落地（用例 1278、文件 371）。
> 加入 §7 的 4 条守卫后，**收口后的最终读数见 §7.8**（`1282 passed, 11 skipped`、`372 files`）。
> 本节保留不覆盖，是为了让「SSE 提速」那一段的读数仍可复跑对照。

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

默认 `-n auto`（本机 8 核）同一条命令的独立读数（SSE 提速收口后、同一时段）：

```text
1278 passed, 11 skipped in 75.17s (0:01:15)
```

两档**都退出 0**，用例数与串行基线一致（1278 passed / 11 skipped；串行基线 756.90s 是
1271 passed / 11 skipped，多出的 7 条是 `test_sse_lifetime_config.py` 的 7 条新守卫用例）。
**§7 的 4 条 docker 守卫落地后的最终读数见 §7.8**（`1282 passed, 11 skipped`）。

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
| 前半段 commit | `ba479ab`（SSE 提速 + 并行化） |
| 本轮 commit | `88302a5`（code：docker 注入点 + 4 条守卫）、`2fec905` / `b578072`（验收记录与本机读数）、`ddb397d`（README 例外清单）、`0e60e65`（理由不同源的澄清）；§7.9 的 CI 读数回填为最后一个 commit |
| 推送 | `git push origin issue-328-impl`（SSH；分支已建立 upstream） |

body 分三段（`## Summary` / `## Evidence` / `## Merge Danger`），Evidence 段贴的是 §1–§4 的实测
读数（7 条慢用例前后并列、全量门禁读数、`-n 4` 与 `-n auto` 两档、守卫用例与手工读数）。
Merge Danger 如实写明：本票改的是**门禁入口**与**全局测试装置**，属「门禁自身的改动」——一旦有偏差，
后续每张票的门禁读数都不可信，并点名请人重点看第 2 条（未放宽判据）与第 6 条（守卫）。

---

## 7. 并行暴露的容器清扫冲突（续作）

### 7.1 两次 CI 红

PR #329（`issue-328-impl`）的首轮 CI 与同 run 重跑，失败面与「SSE 提速 / pytest 并行」的断言无关，
全部落在 docker 用例上：

| run | 结果 | 失败用例 |
| --- | --- | --- |
| 40605229943（首轮） | 1 failed, 1286 passed, 2 skipped (299s) | `test_docker_workspace.py::test_release_closes_the_gateway_and_the_docker_client` |
| 同一 run 重跑 | 2 failed, 1285 passed, 2 skipped (425s) | 上一条 + `test_mcp_assembly.py::test_docker_backend_materializes_mcp_through_the_workspace_gateway` |

失败原文（逐字）：

```text
gateway did not become healthy within 30.0s. Tail of /root/.agentscope/gateway.log: <no gateway log available>
DockerError: [404] No such exec instance: 3877a20d…
```

### 7.2 根因链

```text
create_app 的 lifespan
  └─ await sweep_stale_docker_workspaces()          # 每次进 lifespan 都跑
       └─ active_names = {entry.container_name for entry in _pool.values()}
                                                    # 判据只有**本进程**的池
       └─ 不在 active_names 里 ⇒ 孤儿 ⇒ container.delete(force=True)
  └─ xdist 的每个 pytest worker 是**独立进程**
       └─ worker B 进 lifespan ⇒ A 正在用的容器「不在 B 的池里」⇒ 被强删
            └─ A 那边：gateway.log 读不到（<no gateway log available>）、exec 实例 404
```

串行时同一时刻只有一个进程持容器，所以这条 bug 从 #150 起就一直没暴露；把全量 pytest 改成
`-n auto --dist loadfile`（本票前半段）才把它顶出来。

**范围**：`_reaper_loop` → `sweep_idle_docker_workspaces()` 只扫本进程池里 `active == 0` 的条目，
跨进程安全；`atexit` 的 `shutdown_docker_workspaces()` 同样只碰本进程池，安全。
**只有 `sweep_stale_docker_workspaces()` 这一条是跨进程的。**

### 7.3 修法与授权范围（协调者 `orca orchestration ask` 裁定）

**修法**：`create_app` 新增关键字参数 `docker_startup: DockerStartupHook | None = None`。

- `None`（生产默认）⇒ 等价于 `_default_docker_startup()`，即 **`start_docker_reaper()` +
  `await sweep_stale_docker_workspaces()`**——与这条注入点出现之前**逐字相同**（见 §7.5 的 diff）。
- 传入一个空异步函数 ⇒ **关掉**这条启动动作（不是「跑个空函数假装扫过了」）。

`start_docker_reaper()` 一并被关掉，理由：它只扫本进程池（跨进程安全），但会在每个 worker 起一个
每 30 秒 `asyncio.run` 一次的守护线程；`-n auto` 下 8 核就是 8 个线程轮询 docker daemon，既无必要
也会加重 CI 争用。没有任何用例依赖它：`test_docker_workspace.py` 验空闲回收时是**直接**调
`sweep_idle_docker_workspaces(now=...)`，不经过这条线程。

**替身放在哪**：`tests/scripted.py`（本仓替身之家，非 pytest 的手工工具 `measure_budget.py` 也复用
它），不放 `conftest.py`——后者是 pytest 插件模块，顶部还有 `sys.path` 改写，让非 pytest 入口依赖
它是不必要的副作用（Standards 轴 findings #2，已修）。

**为什么改产品入口，而不是在 pytest 侧绕开**（Standards 轴判断题 2「不新开接缝」的那一问）：

| 备选方案 | 为什么不选 |
| --- | --- |
| session 级 autouse 装置里 `monkeypatch.setattr(api_module, "sweep_stale_docker_workspaces", ...)` | 只能覆盖 pytest 进程：`measure_budget.py` 是**非 pytest** 手工入口，它同样会进 lifespan，却拿不到装置。要么再给它写一份重复的绕法，要么留着它继续误删。 |
| 在 `create_app` 里检测 pytest（`if "pytest" in sys.modules: skip`） | 给**生产路径**加一条隐形分支：后来者读 lifespan 只会看到一条静默的 `if`，既不知道它的存在，也不会在改动时维护它。 |
| 修 `sweep_stale_docker_workspaces()` 让它跨进程安全 | 是**产品语义**变更（生产多进程下的归属问题），属跟进票 [#330](https://github.com/whutlichao/alibot/issues/330)，不在本票。 |

所以选「显式传参」：它把「这条动作在测试里被关掉」变成**调用点可见的事实**（每处都能 grep 到
`docker_startup=`），并且对非 pytest 入口同样有效。代价是 `create_app` 多一个关键字参数——那是
本票唯一碰到的产品签名，已在 PR 的 Merge Danger 里点名请人复核默认值未改行为。

**授权范围（比票面 spec 更大，是 code-review 发现的范围不足，不是 scope 漂移）**：我最初按
「`build_app` + `peer_backends` 两处」上报，协调者核对后要求**每一个进 lifespan 的测试装配点都
显式传替身**，因为根因与「哪个文件触发的」无关：任何进 lifespan 的路径都会 sweep 一次。

机械复核（同一份 AST 判据，另外把 `conftest.py` 也扫进来）共 **14 处带 `docker_startup=`**：
`conftest.py` 2 处 + `conftest.py` 之外 12 处直调点。协调者清单里的「12 处」正是后者；表格合计
14 是因为把 `conftest.py` 的两处也列了出来。（守卫本身扫 `test_*.py` + `measure_budget.py`，
不扫 `conftest.py`：后者是插件模块、装配入口只有两处，靠下面这张表人工核对。）

| 文件 | 行（改后） | 说明 |
| --- | --- | --- |
| `app/tests/conftest.py` | `build_app`（`:155`） | 各 fixture 的公共入口 |
| `app/tests/conftest.py` | `peer_backends`（`:208`） | 同一数据目录上开多个后端 |
| `app/tests/measure_budget.py` | `run()`（`:1207`） | 非 `test_*.py`，但真的进 lifespan |
| `app/tests/test_reminder_delivery.py` | `:103` / `:534` / `:597` / `:617`（4 处） | `:103` 是装配 helper；`:534` 是注入失败用例；`:597` / `:617` 是**生产装配**用例（只关 docker） |
| `app/tests/test_runtime_interrupt.py` | `:111` / `:190`（2 处） | |
| `app/tests/test_runtime_finished.py` | `:61`（1 处） | |
| `app/tests/test_web_assembly.py` | `:366` / `:802` / `:827` / `:836`（4 处） | |

合计 **14 处**（= 2 + 12）。`test_docker_startup_injection.py:80` 自己那处**故意**不传——它是
正对照，必须走生产路径；扫描器不扫自己（与 `test_test_layering_guard.py` 同一体例）。

**不改**：`test_schema_version_gate.py:222` 的 `create_app(...)`——它在
`pytest.raises(SchemaVersionMismatch)` 里，异常发生在 lifespan **之前**，根本进不去，加参数是噪音。
**不改**：`test_docker_workspace.py` 里直调 `sweep_stale_docker_workspaces()` /
`shutdown_docker_workspaces()` / `sweep_idle_docker_workspaces()` 的那几条——它们验的是清扫本身
对不对，必须继续跑真实实现（本票没有给任何 docker 用例加 skip / xfail / 环境门）。

### 7.4 生产多进程的问题**不在本票修**

`sweep_stale_docker_workspaces()` 的实现一字未动，它按「本进程池」判孤儿的语义在**生产单进程**
下是对的；多进程（同一台机器跑两个 alibot 实例）下的归属问题已开跟进票
**[#330](https://github.com/whutlichao/alibot/issues/330)**（标题：修：启动清扫按「本进程池」判孤儿，
会把另一进程正在用的容器删掉）。本票只加注入点，不假装那个问题不存在。

### 7.5 默认行为的 diff（生产路径逐字不变）

```diff
-        try:
-            start_docker_reaper()
-            await sweep_stale_docker_workspaces()
-        except Exception:
-            pass
+        with suppress(Exception):
+            await run_docker_startup()
```

```python
# 模块级：生产默认实现（`app/alibot/api.py:730`）
async def _default_docker_startup() -> None:
    """生产默认：起空闲回收线程 + 清扫上一进程遗留的容器（#150）。

    Docker 是可选后端，daemon 不在时两步都安静失败，不影响主服务启动。
    """
    start_docker_reaper()
    await sweep_stale_docker_workspaces()


# create_app 里、lifespan 之前解析一次（与兄弟注入点 make_driver 同一形状；`api.py:787`）
run_docker_startup = docker_startup if docker_startup is not None else _default_docker_startup
```

lifespan 里的调用点（`api.py:878-879`，缩减形式见上面那段 diff）：
`with suppress(Exception): await run_docker_startup()`。

三处差异逐条说明：

1. **默认值解析搬到 lifespan 之外**：`make_driver` / `approvals` 这些兄弟注入点都在装配期把默认
   解析成局部变量，这里跟上了同一形状——lifespan 里读的是一个确定的 callable，不再带三目。
2. `try/except Exception: pass` 换成 `with suppress(Exception)` 是**等价**的（同一异常面、同一吞掉
   行为），为的是过 ruff 的 `SIM105`。
3. 调用顺序（先 `reaper`、后 `sweep`）与异常面都没变；`_default_docker_startup` 内联了原来那两行。

守卫的第二条用例（§7.6）用**正对照**钉住这件事：默认参数下 `calls == ["reaper", "sweep"]`。

### 7.6 守卫（票面「新增守卫测试」那一类的第二份；已落地）

`app/tests/test_docker_startup_injection.py`，四条用例：

| 用例 | 断言 |
| --- | --- |
| `test_test_assembly_never_runs_the_real_docker_startup` | `build_app` 进 lifespan 时真实 `sweep` / `reaper` **零调用** |
| `test_production_assembly_still_runs_the_docker_startup` | 默认参数（生产形态）**必须**调用，且顺序是 `["reaper", "sweep"]` |
| `test_every_lifespan_entering_create_app_call_passes_the_double` | **AST 扫描**：`app/tests` 下每个 `create_app(...)` 调用都带 `docker_startup=`（例外只有一条） |
| `test_the_ast_scan_exemption_is_narrow` | 豁免名单必须真的只有一条，且锚点仍是 `create_app` 调用 |

第二条是正对照：没有它，第一条只要「永远什么都不做」就能过——替身接线断了也测不出来。
第三条守的是「后来者新增一处直调 `create_app` 却忘了传」：那种漏法**本机看不出来**（本机 docker
用例全 skip），只在 CI 上表现成两条与被改代码无关的 docker 用例红。

**红证据（本轮重跑，逐字）**：把 `conftest.py` 的 `build_app` 里那一行 `docker_startup=docker_startup,`
临时删掉，跑守卫（4 条）：

```text
$ cd app && ../.venv/bin/python -m pytest tests/test_docker_startup_injection.py
F...                                                                     [100%]
>       assert calls == [], f"测试装配跑了真实 Docker 启动动作：{calls}"
E       AssertionError: 测试装配跑了真实 Docker 启动动作：['reaper', 'sweep']
E       assert ['reaper', 'sweep'] == []
E         Left contains 2 more items, first extra item: 'reaper'
tests/test_docker_startup_injection.py:70: AssertionError
FAILED tests/test_docker_startup_injection.py::test_test_assembly_never_runs_the_real_docker_startup
1 failed, 3 passed in 0.91s
```

恢复（`shasum` 前后同为 `52a3c95…`）后：`4 passed`。判据用「记录调用」而不是「让真实函数抛错」——
lifespan 那段是 `suppress(Exception)`，抛出来的异常会被吞掉，布尔读数就永远是「没炸」。

**越层判断（`docs/agents/review-rules.md` 判断题 3 / `app/README.md` 的「pytest 只断言外部可观察
行为」）**：前两条用例断言的是**模块级接线**，不是 HTTP 面能观察到的行为——但「测试装配不跑真实
容器清扫」这件事**只有两种读法**：等 CI 上别的 worker 的容器被删（几十分钟、且不受控），或直接看
调用序列（毫秒级、确定）。它与 i18n 那两处例外的**共同点只是「没有 HTTP 读数面」**，理由本身
**不同源**（那两处比的是词条数据契约，这里比的是装配接线）——所以 README 里它是**另起一段的一类**，
不是「第三处」。因此它是**接缝级守卫**，与 `test_agent_seam_client_lifecycle.py` 同类，不是行为用例；
`test_test_layering_guard.py` 的机械判据（不 import 私有名、不断言词表形状、不读 Studio 源码）全部通过。

**落回 README（协调者裁定，本轮已补）**：`app/README.md` 在「两处例外，理由同源（词条表是数据
契约）」那段**之后另起一段**，新增**一类**不以 HTTP 面读数的守卫——「断言装配层接线或配置默认值」，
理由是它与词条表那两处**不同源**，硬塞进同一段会让那段的结论句（「两处都只读数据契约」）变成假话。
那一类里列**两处**，不是一处：

1. `app/tests/test_sse_lifetime_config.py`（上一轮就在本分支，此前从未进过 README 的例外清单）；
2. `app/tests/test_docker_startup_injection.py`（本轮）。

`test_test_layering_guard.py` 的 docstring 里「只有两处 i18n 词条数据契约例外」**不动**：那是它自己
那条机械判据（不读 Studio 源码 / 不断言词表形状 / 不 import 私有名）的适用范围说明，而新守卫**不违反**
那三条（在含新守卫的工作区里该文件全绿）。两条纪律管的是不同的事。


### 7.7 本机验证的边界（必须写明）

**本机（macOS）没有 docker daemon。** 全部 docker 用例在本地 `skip`（本机全量
`1282 passed, 11 skipped`，其中 docker 相关 9 条、见 §7.8 的逐条清单）。因此：

- 本地能证：非 docker 用例全绿 + 全量门禁 exit 0 + 并行不引入新的不稳 + 守卫的两条方向。
- 本地**不能**证：docker 侧的行为。**docker 相关修复的验证以 CI 为准**（§7.9）。

### 7.8 本机读数（续作，收口后）

用例总数因本票新增的守卫从 1278 变成 **1282 passed, 11 skipped**（**+4**，全部来自
`app/tests/test_docker_startup_injection.py`）：

| 增量 | 用例 | 性质 |
| --- | --- | --- |
| +1 | `test_test_assembly_never_runs_the_real_docker_startup` | 行为守卫：测试装配零调用 |
| +1 | `test_production_assembly_still_runs_the_docker_startup` | 行为守卫（正对照）：生产装配必须调，顺序 `reaper→sweep` |
| +1 | `test_every_lifespan_entering_create_app_call_passes_the_double` | AST 扫描守卫：每个 `create_app(...)` 都带 `docker_startup=` |
| +1 | `test_the_ast_scan_exemption_is_narrow` | AST 扫描守卫：豁免名单必须只有一条且锚点仍有效 |

即 **2 条行为守卫 + 2 条 AST 扫描守卫**。交叉核对：`--collect-only` 求和 **1293** collected
（1278 + 4 + 11 = 1293，其中 11 条是本机无 daemon 时 skip 的 docker 用例）。

```sh
cd app && ../.venv/bin/python -m pytest -n 4 --dist loadfile     # CI 等价形态
cd app && ../.venv/bin/python -m pytest -n auto --dist loadfile  # 本机 8 核
./scripts/check.sh                                               # 全量门禁
```

```text
-n 4 --dist loadfile    1282 passed, 11 skipped in 120.11s
-n auto --dist loadfile 1282 passed, 11 skipped in 74.18s（门禁内那一次）
```

全量门禁（`./scripts/check.sh`，**exit 0**，整条 `real 1m29.812s`）：

```text
pytest（全量，-n auto --dist loadfile）  1282 passed, 11 skipped in 74.18s
ruff check                              All checks passed!
ruff format --check                     372 files already formatted
mypy（仓库根）                          Success: no issues found in 136 source files
pyright                                 0 errors, 0 warnings, 0 informations
Vitest（app/frontend）                  Test Files 23 passed (23) / Tests 437 passed (437) in 4.82s
门禁全绿
real 1m29.812s
```

`ruff format --check` 从上一轮的 371 变成 **372 files already formatted**（新增的守卫文件），
`ruff format --check` 与 `ruff check` 两条都绿——这正是收口前卡住 exit 1 的那一处（本文件 §7.5
的 markdown 代码块少一个空行）。

**这 11 条 skip 里包含全部 docker 用例**（`-rs` 逐条核过，本机无 daemon）：docker 相关共 **9 条**
——`test_docker_workspace.py` **8** 条（`:123` / `:183` / `:256` / `:313` / `:355` / `:370` / `:738` /
`:754`）+ `test_mcp_assembly.py` **1** 条（`:486`），skip 理由都是
`Docker daemon is not available`。另 **2 条与 docker 无关**：`test_agent_smoke.py:53`（真模型冒烟
默认跳过，要 `ALIBOT_SMOKE_MODEL`）与 `test_reminder_schema_step.py:314`（本机没有
`libfaketime`）。一条都没被 skipped 掉以外的方式处理（没有 skip / xfail / 环境门被新增；
它们本来就在该条件下 skip）。**docker 侧的修复只能由 CI 证明。**

顺带说明：上一轮（仅 SSE 提速，用例 1278）的 `-n 4` 是 120.13s；收口后这一轮 `-n 4` 是 120.11s
**基本持平**——多跑的 4 条守卫里有 2 条真的会起一次 `TestClient` lifespan，代价落在秒级。
`-n auto` 74.18s（门禁内）说明并行本身没有退化。

### 7.9 CI（续作）

**结论：绿。** 代码提交 `88302a5` 之后，本票每次推送在 CI 上都是 `success`——已逐一核对的三次为
（`cf6ff6d` → run [40614097315](https://github.com/whutlichao/alibot/actions/runs/40614097315)，8m28s；
`fb182c7` → run [40616121247](https://github.com/whutlichao/alibot/actions/runs/40616121247)，8m08s；
`56e2ccc` → run [40617056636](https://github.com/whutlichao/alibot/actions/runs/40617056636)，9m33s），
三次的 `pytest（全量）` 读数完全一致（`1291 passed, 2 skipped`），不是偶然。下面这段读数取自其中
`fb182c7` 那次（与其它两次只差 docs）。

```text
== pytest（全量） ==
1291 passed, 2 skipped in 379.21s (0:06:19)

== ruff check ==                     All checks passed!
== ruff format --check ==            373 files already formatted
== mypy（仓库根） ==                 Success: no issues found in 136 source files
== pyright ==                        0 errors, 0 warnings, 0 informations
== Vitest（Vue 版工作台） ==         Test Files 24 passed (24) / Tests 448 passed (448)
门禁全绿
```

**这一次 CI 就是 docker 侧修复的正面证据**，两条读数对齐得严丝合缝：

| | 本机（macOS，无 daemon） | CI（Linux，有 daemon） |
| --- | --- | --- |
| pytest 汇总 | `1282 passed, 11 skipped` | **`1291 passed, 2 skipped`** |
| docker 用例 | 9 条全 skip | **9 条全跑过（并入 1291 passed）** |
| 其余 skip | 2 条（真模型冒烟、`libfaketime`） | 同样这 2 条 |

1291 − 1282 = **9**，正好是上表那 9 条 docker 用例；skipped 从 11 降到 2，差额也是这 9 条。
即：**`test_docker_workspace.py` 的 8 条与 `test_mcp_assembly.py` 的 1 条在并行 CI 上真的跑完了并且通过**
——包括首轮红掉的那两条（`test_release_closes_the_gateway_and_the_docker_client`、
`test_docker_backend_materializes_mcp_through_the_workspace_gateway`）。修复前同一 job 是
`1 failed, 1286 passed, 2 skipped` / `2 failed, 1285 passed, 2 skipped`（§7.1）。

两处与本机读数的差异及原因：

- `ruff format --check` 373（本机 372）、`Vitest` 24 files / 448 tests（本机 23 / 437）：CI 在
  **merge commit**（分支 + `main@33e2142`，即 PR #327 已合并的前端改动）上跑，本机是分支自身。
  pytest 的 collected 数不受影响（1293）。
- `pytest（全量）` 377–429s 比本机 74.18s 慢：CI runner 4 核 + 有 daemon 的 9 条 docker 用例真跑。

> 过程记录（如实）：本票收尾期间连着推了多个 docs 提交，`.github/workflows/check.yml` 的
> `concurrency: {group: check-<ref>, cancel-in-progress: true}` 把先推的几次 CI 逐次取消
> （`2fec905` / `b578072` / `ddb397d` / `0e60e65` 的 run 结论都是 `cancelled`，**不算通过证据**）。
> 跑完并给出 `success` 的是 `cf6ff6d`（40614097315）、`fb182c7`（40616121247）与 `56e2ccc`
> （40617056636）三次；三者的 `1291 passed, 2 skipped` 完全一致。
