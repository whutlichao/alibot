## Summary

修掉 issue #30：Agent 每一轮唤醒都在 stderr 打一条 `Task exception was never retrieved / RuntimeError:
Event loop is closed`。**只改一处生命周期**——provider 客户端从「跨轮次被下一个 loop 回收」改成
「在本轮的 loop 内显式关闭」。行为面零改动。

```diff
 AgentScopeAgentSeam.run_round            # app/alibot/agent_seam.py
   async def _reply()
-    instance = Agent(..., model=self._build_model(provider, model_name))
-    return await instance.reply(...)
+  async def _round()
+    model = self._build_model(provider, model_name)   # 提到本轮作用域
+    try
+      instance = Agent(..., model=model)
+      return await asyncio.wait_for(instance.reply(...), timeout=...)
+    finally
+      await _aclose_model_client(model)               # 同一个 loop 内关掉

-  reply = asyncio.run(asyncio.wait_for(_reply(), timeout=self.timeout_seconds))
+  reply = asyncio.run(_round())
```

**根因**（issue #30 正本）：`asyncio.run()` 每轮新建并随即关闭一个 event loop。provider 客户端
（`openai.AsyncClient` / `anthropic.AsyncAnthropic` / `ollama.AsyncClient`，底层 `httpx2.AsyncClient`）
在 `_reply()` 帧退出后失去引用，其 `__del__` 的
`asyncio.get_running_loop().create_task(self.aclose())` 逃到**之后的**某个 loop —— 而它连接池里挂着
属于**已关闭 loop** 的 socket transport，`transport.close()` 的 `loop.call_soon()` 撞上 `_check_closed()`。

**关闭入口不统一**，所以用鸭子类型探测而不是 instanceof 一串类型（四家逐条实测）：

```text
openai.AsyncClient        -> AsyncAPIClient.close       # 协程 close()
anthropic.AsyncAnthropic  -> AsyncAnthropic.close
ollama.AsyncClient        -> AsyncClient.close
genai.Client              -> AsyncClient.aclose         # aio.aclose 分支命中
无关闭入口的对象           -> None                       # 安静跳过
```

关闭放在 `finally`（成功 / 失败 / 超时同一处），**取不到入口或关闭失败都安静跳过**——收尾不该把一个
已经成功的一轮变成 failed。

```diff
 app/alibot/agent_seam.py                     # +70 / −14（含两个模块级 helper）
 app/tests/test_agent_seam_client_lifecycle.py  # 新增，2 用例，默认 pytest 就执行
 app/specs/issue-30-acceptance.md             # Answer
 app/specs/issue-30-pr-body.md                # 本文件
```

**刻意未动**（票面约束）：`orchestration.py` 的 4 线程池 / 每轮一个 loop（用户明确未选方案 B）、
`api.py` 接线、`tests/scripted.py` 替身契约、`AgentSeam` 接口形状（`run_round` 仍是同步方法）。

## Evidence

**Before**（base `f6b8039` 隔离副本）｜**After**（本分支）

回归测试用**真实** `AgentScopeAgentSeam` + 进程内 keep-alive stub 端点（`127.0.0.1:0`，**不联网、
零成本**）。`ScriptedAgentSeam` 不建 provider 客户端，缺陷在替身路径上永远不可见——所以必须用真实 seam。

| | Before（base） | After |
| --- | --- | --- |
| `pytest tests/test_agent_seam_client_lifecycle.py` | **2 failed, 2 errors**（`Event loop is closed` ×5） | **2 passed** |
| `pytest`（全量） | 179 passed, 1 skipped | **181 passed, 1 skipped** |
| `ruff check app` / `format --check app` | — | **All checks passed!** / **48 files already formatted** |
| `mypy`（仓库根 cwd） | 26 files 干净 | **27 files 干净** |
| `pyright app/alibot app/tests` | — | **0 errors, 0 warnings, 0 informations** |

红证据（base 隔离副本上的真实输出）：

```text
tests/test_agent_seam_client_lifecycle.py FEFE                           [100%]
>       assert CLOSED_LOOP_MARKER not in transcript
E       AssertionError: 轮次结束后仍出现跨 loop 回收失败：
E         Task exception was never retrieved Event loop is closed
>       assert closed_before_return
E       AssertionError: run_round 返回时这个 provider 客户端仍未被关闭 —— 它被留给了之后的某个 loop
E       去回收，而这正是 issue #30 的缺陷
========================= 2 failed, 2 errors in 3.32s ==========================
```

同一次失败里的 teardown 还带 `ResourceWarning: unclosed transport <_SelectorSocketTransport fd=16>` ——
泄漏的物理对象；修复后同一命令下这三样（症状文本 / teardown error / unclosed transport）全部归零。

**真实调用点复跑**（把真 seam 交给与 `orchestration.py:76` 同形状的 4 线程池跑 12 轮）：

```text
Before:  rounds: 12  rounds_replied: 12  CLOSED_LOOP_SEEN: True
After:   rounds: 12  rounds_replied: 12  CLOSED_LOOP_SEEN: False
```

即**行为面一字未变、噪声消失**。

## Merge Danger

**Door:** two-way — 单函数生命周期收尾，无数据迁移、无接口变更、无依赖变更。回滚只需 revert 这一个 commit。

**Blast Radius:** narrow

- 触发面是**任何非 `ollama` 的远端 provider**（openai / anthropic / gemini / dashscope / deepseek …）
  在多轮唤醒下的 stderr 噪声；本次修复覆盖全部四类关闭入口形态。
- 风险点：`google.genai.Client` 的 `aio.aclose()` 分支用真实 `genai.Client` 验证了**选中的入口是对的**，
  但未对 Gemini 端点做真调用（需凭证 + 费用，超本票零成本约束）。若 `google-genai` 日后改名异步关闭
  入口，`_client_closer` 会安静跳过而不是报错，症状会回到 issue #30 的形态 —— 这一遗留已写进 Answer §9.1。
- 另一处**如实记录**的发现：`asyncio.wait_for` 的 `TimeoutError` 分支在 AgentScope 2.0.9 下**实际到不了**
  （框架把 `CancelledError` 吞成 `interruption_message`，于是超时报 `replied` 而非 `failed`）。
  这与「跨 loop 关闭」无关、属另一个问题，本票**未改**（Answer §9.2）；`finally` 的覆盖与路径无关，
  超时一旦真发生同样会关闭。
