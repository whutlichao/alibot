# issue #30 验收取证：Agent 每轮 `asyncio.run` 导致 provider AsyncClient 跨 event loop 关闭

**票型**：实施票（缺陷修复 + 回归测试）｜**门禁**：G-impl
**Issue**：[whutlichao/alibot#30](https://github.com/whutlichao/alibot/issues/30)
**base**：`f6b8039`（main HEAD）｜**branch**：`issue-30-event-loop-fix`｜**PR**：见 §8
**采集时间**：2026-10-14｜**环境**：macOS（aarch64），Python 3.11.13（仓库根 `.venv`）

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**实际跑出来的结果**。红证据在 base commit
的**隔离副本**上取得（`/tmp/alibot-issue30-red`，由 `git archive f6b8039` 展开），**不在**主工作区。

---

## 0. 前置 skill 执行证据

### 0.1 `diagnosing-bugs`（Phase 1–6，逐步都有实跑）

| Phase | 要求 | 本票实际做的事 |
| --- | --- | --- |
| 1 构造反馈回路 | 一条**能在本 bug 上变红**、确定、快、可无人值守运行的命令 | 写了 `/tmp/alibot-loop-test/harness.py`：真 `AgentScopeAgentSeam` + 进程内 keep-alive stub 端点。命令：`/Users/apple/orca/alibot/.venv/bin/python harness.py`。**首跑即红**，见 §2.1 |
| 1 收紧回路 | 更快、信号更锐、更确定 | 连续 5 次运行，`CLOSED_LOOP_SEEN: True` **5/5**；把「stderr 文本」换成「asyncio 三条出口收集 + 未关闭 transport」双信号；把收集点钉在「新 loop 里 GC」这一确定时点，不依赖 `sleep` 竞态 |
| 2 复现 + 最小化 | 症状与用户描述一致；逐项砍到每一步都 load-bearing | 症状与 issue #30 原文逐字一致（`Task exception was never retrieved` + `RuntimeError: Event loop is closed`，栈经 `httpx2/_client.py` → `httpcore2` → `anyio` → `selector_events.py:close` → `base_events.py:call_soon` → `_check_closed`）。最小化结果见 §2.2 的「每一步都 load-bearing」清单 |
| 3 假设 | 先列 3–5 条可证伪假设再验证 | 见 §2.3 |
| 4 仪器化 | 每个探针映射到一条具体预测；一次只改一个变量 | 探针：`httpx2.AsyncClient.aclose` 间谍（记录调用发生在哪个 loop）+ `httpx2.AsyncClient.__init__` 弱引用追踪（记录 client 是否活过轮次）。清理见 §0.3（无 `[DEBUG-...]` 前缀留存） |
| 5 修复 + 回归测试 | 先写测试看它红，再修，再看它绿 | TDD 顺序见 §3；红/绿证据见 §4 |
| 6 清理 | 原始回路不再复现；回归测试通过；调试插桩清除；一次性原型删除或标记 | §5.1 原始回路复验转绿；§0.3 插桩清单 |

### 0.2 `tdd`（红 → 绿，逐步实跑）

| TDD 规则 | 本票的执行 |
| --- | --- |
| 先红后绿 | 测试文件先写、先在 base 隔离副本上跑出 **2 failed, 2 errors**（§4.1），之后才动 `agent_seam.py` |
| 垂直切片（一条测试 → 一段实现 → 再下一条） | 切片 1：成功路径（症状判据）→ 修 `run_round` 提模型到作用域 + `finally` 关。切片 2：失败路径（直接判据：`close()` 的调用时点）→ 无需再改实现（同一处 `finally` 已覆盖），但**发现**切片 1 的字符串判据不足以在失败路径上变红，于是补上「直接判据」切片，见 §4.3 |
| 在正确 seam 上测 | seam = `AgentScopeAgentSeam.run_round`（票面指定的真实适配接口）。**没有**去 mock AgentScope 内部，**没有**测私有函数（`_aclose_model_client` 只由 `run_round` 的行为间接覆盖）；`ScriptedAgentSeam` 在这条缺陷上不可见，所以本票用真实 seam |
| 不在循环里重构 | 实现只加 3 个新增块（提模型到作用域 + `finally` 关闭 + 两个模块级 helper），未顺带改动无关代码 |

### 0.3 调试插桩清理核对

```sh
$ git grep -n "DEBUG-" -- app/ | wc -l
0
```

一次性原型全部落在 `/tmp`（不在仓库内）：`/tmp/alibot-loop-test/{harness,failpath_probe,failpath_probe2,timeout_reach,timeout_hard,closefail}.py`、
`/tmp/alibot-issue30-red/`（红证据隔离副本）、`/tmp/mypy-baseline/`。仓库里**没有**留下任何原型文件。

---

## 1. 修复内容（`app/alibot/agent_seam.py`，+70 / −14）

```diff
 class AgentScopeAgentSeam:
     def run_round(self, agent, messages, *, mentioned, target_id) -> RoundOutcome:
-        async def _reply() -> Any:
-            instance = Agent(..., model=self._build_model(provider, model_name))
-            return await instance.reply(inputs if inputs else None)
+        async def _round() -> Any:
+            model = self._build_model(provider, model_name)   # ← 提到本轮作用域
+            try:
+                instance = Agent(..., model=model)
+                return await asyncio.wait_for(
+                    instance.reply(inputs if inputs else None), timeout=self.timeout_seconds
+                )
+            finally:
+                await _aclose_model_client(model)             # ← 同一个 loop 内关掉
         try:
-            reply = asyncio.run(asyncio.wait_for(_reply(), timeout=self.timeout_seconds))
+            reply = asyncio.run(_round())
```

两处关键位移：

1. **`_build_model()` 的返回值从 `Agent(...)` 的实参位置提到 `_round()` 的作用域**：于是那个
   provider 客户端在一轮结束前**始终被一个局部名字引用着**，能在本轮显式关闭，而不是等 `Agent`
   （连同它的 `Msg` 历史）变成一个引用环、被留到下一个 loop 才由 GC 触发 `__del__`。
2. **`asyncio.wait_for` 挪进 `_round()` 内部**：超时是**本轮那个 loop 内**的事件，`timeout` 语义不变，
   但关闭动作因此可以放在 `finally` 里、且必然先于 `asyncio.run` 退出。

新增两个模块级 helper（都在 `run_round` 之外，便于单独核对鸭子类型适配）：

```python
_CLOSE_METHOD_NAMES = ("aclose", "close")


def _client_closer(client):  # 鸭子类型探测：取不到 → None（安静跳过）
    for name in _CLOSE_METHOD_NAMES:
        closer = getattr(client, name, None)
        if callable(closer) and inspect.iscoroutinefunction(closer):
            return closer
    aio = getattr(client, "aio", None)  # google.genai.Client 的异步入口在 aio 上
    aclose = getattr(aio, "aclose", None)
    return aclose if callable(aclose) else None


async def _aclose_model_client(model):  # 在**当前** loop 内关闭；失败不外抛
    try:
        client = getattr(model, "client", None)
        closer = _client_closer(client) if client is not None else None
        if closer is None:
            return
        result = closer()
        if inspect.isawaitable(result):
            await result
    except Exception:
        return
```

### 1.1 鸭子类型适配在四家 provider 上逐条实测

```text
$ /Users/apple/orca/alibot/.venv/bin/python -  # 直接调用 alibot.agent_seam._client_closer
openai.AsyncClient           -> AsyncAPIClient.close
anthropic.AsyncAnthropic     -> AsyncAnthropic.close
ollama.AsyncClient           -> AsyncClient.close
genai.Client                 -> AsyncClient.aclose        # ← aio.aclose 分支命中
plain object                 -> None
某无 client 属性的对象        -> None
```

### 1.2 「收不到就安静跳过、关闭失败不污染轮次」逐条实测

```text
$ /Users/apple/orca/alibot/.venv/bin/python /tmp/alibot-loop-test/closefail.py <worktree>
AFTER_CLOSE_FAILURE status=replied bodies=['ok']     # ← close() 抛 RuntimeError，轮次仍是 replied
NO_CLIENT_ATTR: silently skipped OK                  # model 无 client 属性 → 跳过
NO_CLOSER: silently skipped OK                       # client 无任何关闭入口 → 跳过
```

### 1.3 未触碰的边界（票面约束）

```text
$ git status --short
 M app/alibot/agent_seam.py
?? app/tests/test_agent_seam_client_lifecycle.py

$ git diff --stat app/alibot/orchestration.py app/alibot/api.py app/tests/scripted.py app/tests/conftest.py
（空输出 = 一行未改）
```

- 线程模型未动：`orchestration.py` 的 4 线程池、每轮一个 loop 的设计**原样保留**（用户明确未选方案 B）。
- 接口形状未动：`AgentScopeAgentSeam` 的方法集合仍是 `__init__` / `_build_model` / `run_round`，
  `run_round` 仍是**同步**方法（用 `ast` 核对，非目测）。
- `tests/scripted.py` 的替身契约未动；未联网、未新增依赖。

---

## 2. 根因取证（diagnosing-bugs Phase 1–4）

### 2.1 Phase 1：反馈回路首跑即红

```sh
$ cd /tmp/alibot-loop-test && /Users/apple/orca/alibot/.venv/bin/python harness.py
outcome: replied 模型自主判断需要回应
--- asyncio log records ---
Task exception was never retrieved
future: <Task finished name='Task-6' coro=<AsyncClient.aclose() done, defined at
  .../httpx2/_client.py:2198> exception=RuntimeError('Event loop is closed')>
Traceback (most recent call last):
  File ".../httpx2/_client.py", line 2205, in aclose
    await self._transport.aclose()
  File ".../httpx2/_transports/default.py", line 400, in aclose
    await self._pool.aclose()
  File ".../httpcore2/_async/connection_pool.py", line 382, in aclose
    await self._close_connections(closing_connections)
  File ".../httpcore2/_async/connection.py", line 157, in aclose
    await self._connection.aclose()
  File ".../httpcore2/_async/http11.py", line 236, in aclose
    await self._network_stream.aclose()
  File ".../httpcore2/_backends/anyio.py", line 55, in aclose
    await self._stream.aclose()
  File ".../anyio/_backends/_asyncio.py", line 1467, in aclose
    self._transport.close()
  File ".../asyncio/selector_events.py", line 864, in close
    self._loop.call_soon(self._call_connection_lost, None)
  File ".../asyncio/base_events.py", line 762, in call_soon
    self._check_closed()
  File ".../asyncio/base_events.py", line 520, in _check_closed
    raise RuntimeError('Event loop is closed')
RuntimeError: Event loop is closed
--- verdict ---
CLOSED_LOOP_SEEN: True
```

**确定性与复现率**：连跑 5 次，5/5 `True`；再在剥掉 `http_proxy/https_proxy/ALL_PROXY` 的环境里
跑 3 次，仍 3/3 `True`（即**不依赖**本机代理配置，端点始终是 `127.0.0.1`）。

### 2.2 Phase 2：最小化 —— 每一步都 load-bearing

逐项砍掉一个变量，看回路是否转绿。**留下**的都不可砍：

| 元素 | 砍掉会怎样 | 为什么 load-bearing |
| --- | --- | --- |
| 真 `AgentScopeAgentSeam.run_round` | 变绿（`ScriptedAgentSeam` 不建 client） | 缺陷就在这条生命周期上 |
| provider 端点走 `httpx2` 系（openai / anthropic） | 变绿 | `ollama` 走 `httpx` v1（其 `AsyncClient` 无 `__del__` 逃逸路径），不触发 |
| stub 端点 `protocol_version = "HTTP/1.1"` + `Content-Length` | 变绿 | 没有 keep-alive 就没有「属于已关闭 loop 的活连接」挂在池里 |
| 端点真收过一次请求 | 变绿 | 连接池是空的，`aclose()` 无 transport 可碰 |
| 「在新 loop 里 GC」这个时点 | 不稳定/绿 | `__del__` 只在 GC 时跑；不逼 GC 就看不到 —— 这正是「跨 loop」的成因 |
| `asyncio.run` 每轮新建 loop | —（这是被修的对象） | 缺陷的触发条件本身 |

最小化后的判据就是本票的回归测试：**一个新 loop + 一次真 HTTP 往返 + 强制 GC + 断言无症状文本**。

### 2.3 Phase 3：假设（3 条，可证伪，按可能性排序）

| # | 假设 | 预测（若成立则…） | 实测结论 |
| --- | --- | --- | --- |
| H1 | client 因**引用环**活过本轮，`__del__` 逃到下一个 loop | 本轮返回时 `client.is_closed is False`；新 loop 里 GC 会打出 `Event loop is closed` | **成立**（红证据 5/5；`failpath_probe2` 显示 `closed right after run_round: [False]`） |
| H2 | 缺陷只出现在「带代理 mount」的 client 上（issue 栈里的 `await proxy.aclose()`） | 剥掉代理环境变量后症状消失 | **证伪**：剥掉 3 个代理变量后仍 3/3 复现（栈里那一帧只是 `_mounts` 里恰有一个非 None 的项，与是否真走代理无关） |
| H3 | 是 AgentScope 自己在关闭，只是关得晚 | 在框架代码里能找到 `aclose` 调用点 | **证伪**：`grep` 框架源码无 `aclose`/`close` 调用；`AgentScopeAgentSeam` 是唯一持有 model 的地方，关闭责任在本仓 |

H1 成立 ⇒ 修法只能是「本轮内显式关闭」，这正是用户已裁定的方案；H3 证伪 ⇒ 修复落点确实在本仓
（`agent_seam.py`），而不是去改框架或改线程模型（方案 B 因此既无必要、也未被选中）。

### 2.4 Phase 4：仪器化（两个探针，各对应一条预测）

- **探针 A**（对应 H1）：`httpx2.AsyncClient.__init__` 包一层弱引用，记录 client 在「轮次返回后」
  是否还活着。读数：**还有**（`alive after gc: [True]`）⇒ H1 的「活过本轮」成立。
- **探针 B**（对应修复目标）：`httpx2.AsyncClient.aclose` 间谍，记录调用时所在的 loop。

| 场景 | base | 修复后 |
| --- | --- | --- |
| 成功轮：`aclose` 是否在 `run_round` 内被调用 | **否**（`closed_in_round=0`） | **是**（`closed_in_round=1`） |
| 挂死端点 + `timeout=0.4`（经 `/tmp/alibot-loop-test/timeout_hard.py`） | 否 | 是 |

---

## 3. TDD 循环的实跑顺序（红先于绿）

```text
1) 写 tests/test_agent_seam_client_lifecycle.py（切片 1：成功路径）
2) 在主工作区跑 → 红
3) 复制到 base 隔离副本 /tmp/alibot-issue30-red → 红（§4.1）
4) 改 agent_seam.py（§1）
5) 跑切片 1 → 绿
6) 加切片 2（失败路径）——发现它**在 base 上却是绿的**（字符串判据在该路径下不触发，§4.3）
7) 给切片 2 补「直接判据」（记录 close 的调用时点）→ 在 base 上变红（§4.2），修复后绿
8) 全量 + 四项门禁（§6）
```

第 6 步是本次 TDD 里唯一一次「测试写歪了」——如实记录在 §4.3，因为「红不了的新测试」等于没测。

---

## 4. 红 → 绿证据

### 4.1 红证据（base `f6b8039` 的**隔离副本**）

隔离副本怎么来的（**不在主工作区做**）：

```sh
$ rm -rf /tmp/alibot-issue30-red && mkdir -p /tmp/alibot-issue30-red
$ cd /Users/apple/orca/alibot && git archive f6b8039 app mypy.ini pyrightconfig.json | tar -x -C /tmp/alibot-issue30-red
$ cd /tmp/alibot-issue30-red && ln -s /Users/apple/orca/alibot/.venv .venv
$ cp <worktree>/app/tests/test_agent_seam_client_lifecycle.py app/tests/
$ grep -c 'asyncio.run(asyncio.wait_for' app/alibot/agent_seam.py
1                                    # ← 副本里的 agent_seam.py 就是未修复的 base 版本
```

命令：

```sh
$ cd /tmp/alibot-issue30-red/app && PYTHONPATH=/tmp/alibot-issue30-red/app \
    ../.venv/bin/python -m pytest tests/test_agent_seam_client_lifecycle.py -v
```

结果：**2 failed, 2 errors**（`Event loop is closed` 在输出里出现 **5** 次）

```text
tests/test_agent_seam_client_lifecycle.py FEFE                           [100%]
=================================== FAILURES ===================================
_________ test_round_closes_provider_client_within_its_own_event_loop __________
>       assert CLOSED_LOOP_MARKER not in transcript, f"轮次结束后仍出现跨 loop 回收失败：\n{transcript}"
E       AssertionError: 轮次结束后仍出现跨 loop 回收失败：
E         Task exception was never retrieved Event loop is closed
E       assert 'Event loop is closed' not in 'Task except...op is closed'
E         'Event loop is closed' is contained here:
E           Task exception was never retrieved Event loop is closed
________________ test_failed_round_also_closes_provider_client _________________
>       assert closed_before_return, (
E       AssertionError: run_round 返回时这个 provider 客户端仍未被关闭 —— 它被留给了之后的某个 loop
E       去回收，而这正是 issue #30 的缺陷
E       assert []
========================= 2 failed, 2 errors in 3.32s ==========================
```

两条失败**各自对应不同的路径**，不是同一处报错重复：

- 切片 1（成功路径）：字符串判据命中 `Event loop is closed`。
- 切片 2（失败路径）：直接判据命中 —— 轮次返回时 `aclose` **一次都没被调用过**（`assert []`）。

teardown 的 2 个 error 是同一根因的另一面证据：GC 时 socket / transport 真的没被回收：

```text
pytest.PytestUnraisableExceptionWarning: Exception ignored in: <socket.socket fd=16, ... raddr=('127.0.0.1', 56403)>
  ResourceWarning: unclosed <socket.socket fd=16, family=2, type=1, proto=6, ...>
pytest.PytestUnraisableExceptionWarning: Exception ignored in: <function _SelectorTransport.__del__ ...>
  ResourceWarning: unclosed transport <_SelectorSocketTransport fd=16>
```

### 4.2 绿证据（同一命令，修复后）

```sh
$ cd <worktree>/app && ../.venv/bin/python -m pytest tests/test_agent_seam_client_lifecycle.py -v
collected 2 items

tests/test_agent_seam_client_lifecycle.py ..                             [100%]

============================== 2 passed in 2.82s ===============================
```

同一命令下，`Event loop is closed` 出现 **0** 次、`unclosed transport` **0** 次、teardown error **0** 个。

### 4.3 切片 2 的判据为什么必须换掉（等价性论证）

初版切片 2 也只用字符串判据，结果它在 base 上**是绿的** —— 也就是一个「假回归测试」。仪器化查出原因：

```text
$ /Users/apple/orca/alibot/.venv/bin/python /tmp/alibot-loop-test/failpath_probe2.py /tmp/alibot-issue30-red
status: failed
clients: 1 closed right after run_round: [False]
alive after gc: [True] closed: [False]     # ← client 活过轮次，但从未被关闭
MARKER: False                              # ← 于是没有「跨 loop 关闭」这件事可报
```

失败路径上 client **从未被关闭**（连 `__del__` 都没跑到），所以没有 `aclose()` 去碰已关闭的 loop。
**泄漏以「不报错的悬挂资源」形态存在**，而这一形态恰好**不是** `Event loop is closed`。
字符串判据在这里测的是「症状有没有换一种表现」，不是「缺陷有没有存在」。

于是换成**直接判据**：记录 `httpx2.AsyncClient.aclose()` 被调用时所在的 loop，断言它在
`run_round` **返回之前**就已经被调用过。

**等价性论证**：缺陷的定义是「本轮建出来的 client 在 `run_round` 返回时仍未被关闭，被留给之后的
某个 loop 去收」。注意「之后」是必要条件——本轮那个 loop 在 `asyncio.run` 退出时就已经关闭，
**任何**发生在返回之后的关闭都只可能在别的 loop 里执行。因此「返回前已关闭」与「跨 loop 关闭」
**互斥**：前者成立 ⇒ 后者不可能发生 ⇒ issue #30 的症状不可能出现。反向也成立（base 上该判据为
假，且症状确实出现），所以这条判据既不比字符串判据弱、也不比它强，而是**精确覆盖了缺陷的定义**。

两条判据现在一起用：症状判据保留（它与 issue 原文逐字对应，是「用户看到的东西」），
直接判据补上失败路径（它覆盖「症状暂时不显形的泄漏」）。

---

## 5. 回归测试设计要点

文件：`app/tests/test_agent_seam_client_lifecycle.py`（新增，2 个用例，**默认 `pytest` 就执行**）

| 设计点 | 做法 | 为什么 |
| --- | --- | --- |
| 真实 seam | `AgentScopeAgentSeam`（非 `ScriptedAgentSeam`） | 替身不建 provider 客户端，缺陷在替身路径上不可见 |
| 不联网、零成本 | 进程内 `ThreadingHTTPServer` 绑 `127.0.0.1:0`，回一份固定的 OpenAI 兼容响应 | 不需要真模型、不需要凭证、不产生任何费用；`pytest` 全量仍零成本 |
| keep-alive 前提 | `protocol_version = "HTTP/1.1"` + `Content-Length` | 连接必须留在池里，复现才有物理对象 |
| 端点自证 | 用例断言 `_StubProviderHandler.requests` 非空 | 防「因为压根没连上所以不报错」的假绿 |
| 时点确定 | 在新 `asyncio.new_event_loop()` 里 `gc.collect()` 数轮 | `__del__` 只在 GC 时跑；把 GC 放在一个新 loop 里，不依赖 `sleep` 竞态 |
| 三通道收集 | loop 的 `set_exception_handler` + `asyncio` 日志 + `threading.excepthook` | 同一异常可能走任一条出口，只接一条会漏 |
| 默认运行 | 无 `skipif`、无环境变量开关 | 票面要求：纯本机 stub，必须在**默认** `pytest` 里被跑到（与 `test_agent_smoke.py` 的「默认跳过」形成对比，后者当真调模型） |
| 不碰替身契约 | 只用公开的 `run_round` | `tests/scripted.py` 一行未改 |

### 5.1 Phase 6：原始回路复验（修复后）

```sh
$ cd /tmp/alibot-loop-test && /Users/apple/orca/alibot/.venv/bin/python harness.py   # ×3
outcome: replied 模型自主判断需要回应
CLOSED_LOOP_SEEN: False
outcome: replied 模型自主判断需要回应
CLOSED_LOOP_SEEN: False
outcome: replied 模型自主判断需要回应
CLOSED_LOOP_SEEN: False
```

`harness.py` 与回归测试**走不同的代码路径形态**（前者只跑一轮、不收集通道；后者收集三通道并加直接
判据），所以这一步是独立于回归测试的复验，不是「用测试证明测试」。

### 5.2 真实调用点复跑（4 线程池 × 12 轮）

票面点名的调用点是 `orchestration.py:76` 的 4 线程池。直接把**真实 `AgentScopeAgentSeam`** 交给一个
同样形状的 `ThreadPoolExecutor(max_workers=4)` 跑 12 轮（`/tmp/alibot-loop-test/orch_site.py`）：

```text
--- BASE (12 轮 × 4 线程池) ---
rounds: 12 statuses: ['replied']
rounds_replied: 12
CLOSED_LOOP_SEEN: True

--- FIXED ---
rounds: 12 statuses: ['replied']
rounds_replied: 12
CLOSED_LOOP_SEEN: False
```

即：多轮 + 并发下**行为面完全不变**（12/12 仍 `replied`），而 stderr 噪声**消失**。这正是
issue #30 描述的「不写协作域正本、只是噪声」这一影响面的直接验证。

---

## 6. 门禁读数（G-impl）

```sh
$ cd <worktree> && ./.venv/bin/python -m pytest        # 仓库根 cwd
181 passed, 1 skipped in 99.17s (0:01:39)

$ ./.venv/bin/ruff check app
All checks passed!

$ ./.venv/bin/ruff format --check app
48 files already formatted

$ ./.venv/bin/python -m mypy
Success: no issues found in 27 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests
0 errors, 0 warnings, 0 informations
```

| 门禁 | 基线（票面给） | 本票实测 | 判定 |
| --- | --- | --- | --- |
| `pytest` | `179 passed, 1 skipped` | **`181 passed, 1 skipped`** | ✅ 新增 2 条（本票 2 个用例），skip 仍为 1 |
| `ruff check` | — | **All checks passed!** | ✅ |
| `ruff format --check` | — | **48 files already formatted** | ✅（新增 1 个 `.py`：47→48） |
| `mypy` | `26 files` 无问题 | **27 files 无问题** | ✅（新增 1 个测试文件） |
| `pyright` | 0 errors | **0 errors, 0 warnings, 0 informations** | ✅ |

### 6.1 两处必须说清的执行差异（都不是本票引入的）

**(a) `mypy` 的 cwd**：票面写 `cd app && ../.venv/bin/python -m mypy`。这条命令在 **base commit 上
就已经失败**，与本票无关：

```text
$ cd /tmp/mypy-baseline/app && ../.venv/bin/python -m mypy     # base f6b8039 的干净展开
app/tests: error: Duplicate module named "__main__" (also at "app/alibot")
Found 1 error in 1 file (errors prevented further checking)
```

`mypy.ini` 里 `files = app/alibot, app/tests` 是**相对 cwd** 解析的：从 `app/` 下运行时，
`app/tests` 与 `app/alibot` 会被当成两个不同的顶层包路径去解析同一个 `__main__`。仓库 README
与 `mypy.ini` 自己记录的权威调用方式是**从仓库根**执行：

```text
$ cd /Users/apple/orca/workspaces/alibot/issue-30-event-loop-fix && ./.venv/bin/python -m mypy
Success: no issues found in 27 source files
```

本票按仓库自述的权威方式执行并通过（27 files）；上面的 base 对照证明这不是本票改动造成的。

**(b) pi-lens 内联告警里的 `Import "agentscope" could not be resolved`**：pi-lens 用它**自带**的解释器
逐文件检查，那个解释器看不见本仓 `.venv` 里装的 `agentscope` / `pytest` / `httpx2`，于是把第三方
import 判成 unresolved。这与 `mypy.ini` 顶部记录的「检查器环境差异」是同一类问题。核对：

```text
$ ./.venv/bin/python -c "import agentscope; print(agentscope.__file__.split('site-packages/')[-1].split('/')[0])"
agentscope                                            # venv 里确实装着

$ node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests
0 errors, 0 warnings, 0 informations                  # 仓库自带 pyrightconfig.json 的权威检查：干净
```

被报的 import 行在 base 的 `agent_seam.py` 里**逐字节相同**（`from agentscope import credential as cred`
等 4 行都在 base 上就有，只是当时位于 `_reply()` 内）；本票没有新增任何第三方 import。

---

## 7. 双轴 code-review（Standards + Spec）

见 §7.1 / §7.2。**未按「单票可圈定影响面」省略**：本票虽然影响面窄（一个函数 + 一个测试文件），
但 `diagnosing-bugs` Phase 5 明确要求「先确认存在正确的 seam」，而 seam 选得对不对、鸭子类型适配
是否覆盖了票面点名的四家 provider，都是**设计判断**，不能让作者自证。两轴并行跑、各自独立报告。

### 7.1 Standards 轴

在 `reviewer` 子代理上并行跑（输入：`git diff app/alibot/agent_seam.py` + 新增测试文件全文 +
本仓标准来源清单 + Fowler smell 基线）。它以 **Merge verdict: OK with notes** 收尾。原文要点：

**判定为「无问题」**：命名与结构清晰（`_CLOSE_METHOD_NAMES` / `_client_closer` /
`_aclose_model_client` 名实相符）；关闭落在 `finally`（超时/异常同收）；改动集中在 `run_round` + 两个
模块级 helper，符合「不在循环里重构」；中文注释与全角标点是仓库刻意写法（`pyproject.toml` 的
`extend-ignore = ["RUF001-003"]`），不记为问题。

**P1（硬性：文档化标准）** —— `tests/conftest.py:7-8` 与 `app/README.md:223-224` 明定「测试只断言
**外部可观察行为**……不 import 实现内部模块」，而新用例 `import AgentScopeAgentSeam` 并打了
`httpx2.AsyncClient.aclose`。

**→ 处理：不改落点，补显式例外说明（采纳）**。子代理给的修法本身就是在文档里登记这条例外，而不是改测试落点
（它同时明确写「acceptance §5 的 seam 论证成立」）。本票的可编辑范围**不含 `app/README.md`**，所以例外说明
写在**测试文件 docstring** 里（新增一段「关于仓库『测试只断言外部可观察行为』这条约定的例外」）：
被测对象是 `run_round` 这个**适配接缝本身**（票面指定的 target），这类缺陷在 HTTP 面上**不可见**
（issue #30 正本：它不写协作域正本、消息与轮次结论全不受影响），因此无法经 HTTP 面取证；它与同样
import 实现模块的 `test_agent_smoke.py`（README 已登记的真模型冒烟例外）同类，区别是本文件跑
**本机 stub、零成本**，进默认 `pytest` 也安全。不碰 `README.md` 不使标准失衡——标准原文挂在
`conftest.py` 里，而未修改该文件。

**P2（judgement）** —— `provider_client_close_probe` 里 `monkeypatch.undo()` 会撤销**整个函数级**
fixture 的补丁（包括 `stub_provider` 设的 `OPENAI_API_KEY` / `OPENAI_BASE_URL`），当前无害但会随
用例顺序漂移。

**→ 处理：已改（采纳）**。换成 `with monkeypatch.context() as patch:`，只撤销本探针自己的那一处补丁。

**P2（judgement）** —— 「是否 awaitable」在两处各判一次（`_client_closer` 用 `iscoroutinefunction`，
`_aclose_model_client` 又用 `isawaitable` 兜底），且 `aio.aclose` 分支不过滤，两条路径契约不一致。

**→ 处理：已改（采纳，并收紧为单一规则）**。改成：`_client_closer` 把 `_CLOSE_METHOD_NAMES` 与
`aio.aclose` 候选**放在同一循环里过同一条规则**（只接受协程函数），于是 `google.genai.Client` 的同步
`close()` 自然被排除、落到 `aio.aclose`；`_aclose_model_client` 不再二次判定，只做调用 + 忍错。
改完重测四家 provider 的入口选择**逐一未变**（§1.1 同命令重跑：四家仍命中同一入口）。

### 7.2 Spec 轴

（子代理报告，待回填）

### 7.3 Findings 处理结果汇总

| 轴 | 编号 | 性质 | 处理 |
| --- | --- | --- | --- |
| Standards | P1 | 硬性（文档化标准） | **采纳**：不改落点，在测试 docstring 登记 seam 级例外（不碰 `README.md`，超本票可编辑范围） |
| Standards | P2 | judgement（fixture 撤销范围） | **采纳**：`monkeypatch.context()` |
| Standards | P2 | judgement（awaitable 双重判定） | **采纳**：入口选择收敛为单一规则 |
| Standards | — | `_aclose_model_client` 近 Middle Man | **不采纳**：与仓库既有 helper 风格（`_system_prompt` / `_msg_text`）一致，且它持有「失败不外抛」这条契约，不是纯转发 |

改动后所有门禁**重跑**（§6 的读数已是重跑后的）：`181 passed, 1 skipped`、ruff 两项干净、mypy 27 files 干净、
pyright 0 errors；并且重新在 base 隔离副本上确认测试**仍然 2 failed + 2 errors**（修复没有被 review 改弱）。

---

## 8. 交付与状态流转

- **commit**：（待回填）
- **PR**：（待回填，PR body 见 `app/specs/issue-30-pr-body.md`）
- **issue #30 状态**：本票**不自行**改 issue 的 label / state（合并由 coordinator 执行）。

---

## 9. 遗留与未做

1. **`google.genai.Client` 的关闭路径未经真机验证**：`_client_closer` 的 `aio.aclose()` 分支用真实
   `genai.Client` 实例验证了**选中的入口是对的**（§1.1），但没有对 Gemini 端点做一次真调用
   （那需要凭证且会产生费用，超出本票「零成本」约束）。风险面：若某天 `google-genai` 改了异步
   关闭入口的名字，`_client_closer` 会**安静跳过**（而不是报错），症状会回到 issue #30 的形态。
2. **超时分支的端到端触发不可达（发现，未做）**：`asyncio.wait_for` 的 `TimeoutError` 分支在
   AgentScope 2.0.9 下**实际到不了** —— 框架把 `CancelledError` 吞掉，返回一条
   `interruption_message`（默认 `"I notice the interruption. How can I help you?"`），于是
   `run_round` 报 `replied` 而不是 `failed`（实测：`timeout=0.05` → `elapsed=0.85 status=replied`）。
   这不属于本票范围（它影响的是「超时该怎么判定」，不是「跨 loop 关闭」），**未改**；但这也是我把
   切片 2 从「超时路径」改成「失败路径」的原因（失败路径才真的可达、才真的能红）。
   `finally` 的覆盖与路径无关，超时一旦真发生同样会关闭。
3. **未做的**：线程模型改造（方案 B，用户明确未选）、`AgentSeam` 接口变更（票面禁止）、
   `testing` 替身改动（票面禁止）、issue label/state 变更（coordinator 执行）。
