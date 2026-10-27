## Summary

全量门禁 92% 的时间在 pytest，而 pytest 里有 5 条用例各等约 55 秒、2 条各等 10 秒。两处都是可测的、
非猜测的浪费。本票把「读配置的时机」和「门禁怎么跑」这两件事一起改掉，**不改任何对外行为**。

```diff
 app/alibot/api.py
-  SSE_STREAM_MAX_SECONDS = _env_seconds("ALIBOT_SSE_ROTATE_SECONDS", 55.0)   # 导入期快照
+  SSE_STREAM_DEFAULT_MAX_SECONDS = 55.0                                      # 唯一默认来源
+  def sse_stream_max_seconds() -> float:                                     # 每次调用都读
+      return _env_seconds("ALIBOT_SSE_ROTATE_SECONDS", SSE_STREAM_DEFAULT_MAX_SECONDS)
   …
   channel_events / task_events / dm_events / all_events
-    deadline = … + SSE_STREAM_MAX_SECONDS
+    deadline = … + sse_stream_max_seconds()          # 四处都改，漏一处就是再造一条 55 秒

 app/tests/conftest.py
+  @pytest.fixture(autouse=True, scope="session")     # 与既有两个 session 装置同形
+  def _shorten_sse_stream_lifetime(): ALIBOT_SSE_ROTATE_SECONDS = "2"

 app/tests/test_agent_round.py, test_bounded_relay.py
+  blocked_seam = launched["seam"]     # restart() 会重建 seam：被阻塞线程持的是**旧**闸门
   after = launched["restart"]()
-  launched["seam"].release()          # 释放的是新 seam ⇒ 线程只能等 _gate.wait(timeout=10)
+  blocked_seam.release()

 app/pyproject.toml
+  "pytest-xdist>=3.5"          # dev extra；门禁并行化的依赖

 scripts/check.sh
-  ( cd app && "$PY" -m pytest )                          # 全量
+  ( cd app && "$PY" -m pytest -n auto --dist loadfile )  # 仅全量；窄档/指定文件仍串行

 app/tests/test_sse_lifetime_config.py (新增)
  守卫：默认 55.0 未被改小；且读取发生在**调用时**（改 env 立刻生效、清掉回到默认）

 app/tests/conftest.py, test_event_stream.py, test_round_outcome_visibility.py
+  读线程容忍 rotate 控制帧（``data: {}``）：裸取 event["type"] 的用例会 KeyError，
+  跳过空载荷帧；断言一条未放宽（为什么只有这 3 处需要，见验收文档 §5.1 的判据表）
```

为什么两件事要一起做：产品的 55 秒是**对外承诺**（README 与反代配置按它排），测试却必须等它到期。
把读取改成运行时，测试才能用自己的 session 装置把寿命压到 2 秒；而压短之后，「默认值被顺手改小」
与「读取退回导入期快照」这两种回归都会**静默全绿**（前者只表现为生产轮换变密，后者只表现为测试变慢
5 分钟）。所以第 6 条守卫是这次改动的必要配套，不是锦上添花。

`--dist loadfile` 是刻意的：本仓用例按文件共享装置与顺序假设，同文件落在同一 worker 才稳
（速度上与默认 `load` 同档，所以这是语义选择，不是提速手段）。

## Evidence

> **读数口径**：宿主在大部分采集时间里高负载（8 核机器 load average 47–170，多个无关进程抢核），
> 早先的绝对耗时偏高、已作废；下面用的是负载回落到 5–12 之后重测的读数。**CI 读数为准。**

- **Before:** 7 条慢用例（base `f47652b`，本机干净工作区，`--durations=0`）
  ```text
  55.47s  test_rotate_frame_lets_the_client_tell_a_rotation_from_an_outage
  55.42s  test_global_event_stream_rotates_and_sends_no_replay_before_it
  55.12s  test_event_stream_of_a_channel_whose_target_row_is_missing
  55.12s  test_both_claim_records_reach_the_live_stream_and_match_the_replay
  55.10s  test_task_event_stream_replays_thread_messages_and_rejects_missing_tasks
  10.01s  test_bounded_relay…::test_in_flight_round_is_not_replayed_after_restart
  10.01s  test_agent_round…::test_in_flight_round_is_not_replayed_after_restart
  7 passed in 297.53s
  ```
  **After:** 同一条命令
  ```text
  2.87s / 2.39s / 2.19s / 2.17s / 1.50s   （5 条 55 秒级）
  0.55s / 0.51s                          （2 条 10 秒级）
  7 passed in 14.76s
  ```
  断言一条未改。

- **全量 pytest（同一台机器，前后对比）：**
  ```text
  串行基线（票面，正常负载）              756.90s
  -n 4 --dist loadfile（CI 等价形态）     120.13s   exit 0
  -n auto --dist loadfile（本机 8 核）     75.17s   exit 0  （门禁内 78.98s）
  --dist load（对照，-n 4）               117.42s   exit 0
  ```
  用例数一致：`1278 passed, 11 skipped`（串行基线 1271 passed / 11 skipped，多出的 7 条是本票
  新增的守卫用例）。`loadfile` 与 `load` 在本机同速，选前者是因为**语义**：同文件落在同一 worker，
  本仓用例按文件共享装置与顺序假设。

- **全量门禁（`./scripts/check.sh`，exit 0，整条）：**
  ```text
  pytest（全量，-n auto --dist loadfile）  1278 passed, 11 skipped in 78.98s
  ruff check                              All checks passed!
  ruff format --check                     371 files already formatted
  mypy（仓库根）                          Success: no issues found in 135 source files
  pyright                                 0 errors, 0 warnings, 0 informations
  Vitest（app/frontend）                  Test Files 23 passed (23) / Tests 437 passed (437)
  门禁全绿                                 real 1m36.072s
  ```

- **生产默认未被改小：** `test_sse_lifetime_config.py` 7 passed；手工读数（不带环境变量）
  `no env -> 55.0`、`env=9 -> 9.0`、`env=bogus -> 55.0`。

- **rotate 语义未被削弱：** 两条锚点用例在缩短寿命后照旧通过——轮换前先发完补拉帧，随后恰好一个
  命名事件 `event: rotate`；全局流在 rotate 之后读到 EOF、无任何后续命名事件。

逐条判据、命令与完整读数：`app/specs/issue-328-acceptance.md`。

## Merge Danger

**Door:** 双向

代码本身可整体 revert（四段都是纯性能/装置改动，没有 schema、没有数据迁移、没有对外契约变化）。

**Blast Radius:** 全仓

本票改的是**门禁入口**（`scripts/check.sh` 的全量分支）与**全局测试装置**（`app/tests/conftest.py`
的 session 级 autouse fixture）——这两处一旦有偏差，后续**每一张票**的门禁读数都不可信。请重点看：

1. **全量 pytest 并行**是否真的没有放宽任何判据（本票未删/未 skip/未 xfail 任何用例，未动
   `filterwarnings = ["error"]`；1278 passed / 11 skipped 与串行基线的跳过数一致）。
2. **第 6 条守卫**是否守住了那两条沉默回归路径（默认值被改小、读取退回导入期快照）——
   这正是本票最容易在以后被无意推翻的地方。
3. `--fast <files>` 与 `--studio` 窄档**仍是串行**，历史窄档读数仍可比。

不在本票范围：CI 拆 job、docs-only `paths-ignore`、删除 CI 里那次显式构建步。
