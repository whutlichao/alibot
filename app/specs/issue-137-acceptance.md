# Issue #137 验收：`agentscope` 2.0.9 → 2.0.10

- 票：**#137**（impl，G-impl；规格 D7 / §2.6）
- 分支：`issue-137-impl`
- 日期：2026-10-18
- 结论：pin 已改到 `agentscope==2.0.10`；§2.6 的 8 项差异逐项核对（2.0.10 读数 11/11，2.0.9 对照 0/11）；
  全量门禁绿；pytest 读数与升级前**一致**（`653 passed, 2 skipped`）；未改任何业务行为。

## 1. 变更

| 文件 | 变化 |
| --- | --- |
| `app/pyproject.toml` | pin `2.0.9` → `2.0.10`；注释改为 **2026-10-18 核对**（`docs.agentscope.io` 站点根 → `/en/versions/2.0.10`，PyPI 最新同为 2.0.10） |
| `app/README.md` | `pin 的版本` 段落与 credential 白名单的版本引用同步到 2.0.10 |
| `app/alibot/agent_seam.py` | 注释里的“当前 pin”版本引用同步到 2.0.10（**只改注释**，无代码/行为改动） |
| `app/specs/issue-137-acceptance.md` | 本报告 |

仓库**没有锁文件**（`app/uv.lock` / `uv.lock` 都不存在），安装说明沿用 `app/README.md` 的
`uv pip install --python .venv/bin/python -e "./app[dev]"`。

## 2. 判据 1：pin 生效、读数可复跑

```sh
$ grep -n 'agentscope==' app/pyproject.toml
13:  "agentscope==2.0.10",

$ .venv/bin/python -c "import agentscope, importlib.metadata as m; print(agentscope.__version__, m.version('agentscope'))"
2.0.10 2.0.10

$ uv pip compile app/pyproject.toml | grep '^agentscope=='
agentscope==2.0.10
```

- 第三条是从 `app/pyproject.toml` 重新解析依赖图（uv 0.11.22），不是读已装环境：pin 被解析成 2.0.10。
- 共享 `.venv` 里装的就是 `agentscope-2.0.10.dist-info`（`Site packages` 只有一个版本，没有 2.0.9 残留）。

## 3. 判据 2：§2.6 八项差异逐项核对

方法：把 PyPI 的 `agentscope-2.0.9-py3-none-any.whl` 解包到 `/tmp/as209/wheel/`，与 `.venv` 的 2.0.10
逐文件 diff；再用一个临时脚本按**符号源码**断言（`inspect.getsourcelines` + 正/负 needle）。
脚本不提交：它断言的是框架内部源码，不是本仓的外部可观察行为，进测试会违反
`app/tests/test_test_layering_guard.py` 的分层判据。

读数：**2.0.10 = 11/11 PASS，2.0.9 = 0/11 PASS**（同一脚本、同一指针）。

| # | 项 | 2.0.10 落点（源码符号/行号） | 2.0.9 对照 | 本仓影响面 |
| --- | --- | --- | --- | --- |
| 1 | `TeamPipeline._run_assignments` 收尾 | `pipeline/_team_pipeline.py:284` 的 `_run_assignments`，内部 `run_all()`（:339）先把每个分组 `create_task`（:341-344），`finally` 里 `if not task.done(): task.cancel()`（:350-352）+ `await asyncio.gather(*tasks, return_exceptions=True)`（:353）再放 sentinel | :342 是裸 `await asyncio.gather(*[run_group(*_) for _ in groups.items()])`：任一成员失败会直接抛出，兄弟任务既不取消也不 join | **无调用点**（本仓没有 `team.py`/`TeamPipeline`；T1/T2 未落）。`_run_assignments` 的签名与行号 284 未变，ADR-0019 的拦截点不受影响 |
| 2 | `SOPEngine` 在 `FAILED` 时不再重试 | `sop/_engine.py:145`：`if record.phase in (SOPPhase.AWAITING, SOPPhase.FAILED): return` | :145 只判 `is SOPPhase.AWAITING`；`FAILED` 会落进下一轮 `while True`，在 `len(verifications) >= max_attempts` 之前可能再试 | **无调用点**（P1/P2 未落）。未来 `sop.py` 适配时，步骤自己判失败即收口 |
| 3 | `WorkspaceBase` 的 `workspace://` | `workspace/_base.py:1169`：`url=AnyUrl(f"workspace:///{rel}")`；文档串 :1021 写成 “rewritten as portable `workspace://` URL blocks” | **`workspace://` 构造在 2.0.9 已存在**（同为 :1169）；两版真正的 diff 只有 :1021 的文档串（`file://` → `workspace://`）。`path_to_uri` 仍把**后端绝对路径**渲染成 `file://`（2.0.10 :486），未变 | **无调用点**（W 票未落）。未来解析 `context.jsonl` 的 offload 引用时按 `workspace://` 处理；`file://` 只出现在后端路径换算 |
| 4 | `LocalWorkspace` 技能目录名冲突扩到文件系统存在性 | `workspace/_local_workspace.py:226` 与 `:839`（seed 模板 / agent 分区两处）：`while dir_name in existing_dir_names or os.path.exists(os.path.join(skills_dir, dir_name)):` | 两处都只看 `existing_dir_names`；磁盘上已存在但不在索引里的目录会被复用/覆盖 | **无调用点**（W2/W3 未落）。未来装配时同名技能落 `_1` 后缀而不是覆盖既有目录 |
| 5 | `ToolBase` 路径校验 `abspath` → `realpath` | `tool/_base.py:442` 的 `_is_dangerous_path`，:471 `abs_path = os.path.realpath(os.path.expanduser(file_path))` | :471 是 `os.path.abspath(...)`；**另一处** `_path_in_allowed_working_path` 的 `:422` 两版都用 `realpath` | 本仓只用 `FunctionTool` 的同步无路径工具（见下），不经过该检查。未来 local Bash / 文件工具按 symlink 目标判定 |
| 6 | `FunctionTool` 适配器用 `inspect.isawaitable` | `tool/_adapters.py:148`：先 `result = self._func(**kwargs)`，再 `if inspect.isawaitable(result): result = await result` | :147 用 `inspect.iscoroutinefunction(self._func)` 预判；同步函数返回 awaitable 时不会 await | `app/alibot/agent_seam.py:648/683/698` 三个 `FunctionTool` 包的都是**同步返回 `str`** 的函数（`declare_silence` / `set_task_status` / `transfer_task`）；2.0.10 走同一分支，行为不变 |
| 7 | `MCPClient` HTTP 客户端改用 SDK 工厂 | `mcp/_mcp_client.py:238`：`client = create_mcp_http_client(headers=config.headers, timeout=httpx.Timeout(config.timeout) if … else None)`（:238-245），不再直接 `httpx.AsyncClient(...)` | :237 分叉：有 headers/timeout 时直接 `httpx.AsyncClient(...)`，否则 `create_mcp_http_client()`；SDK transport 的默认值在有配置时丢失 | **无调用点**（M1/M2 未落）。未来物化 MCP 时保留 transport 默认值（如代理、事件钩子） |
| 8 | `Msg.append_usage` 复制 `usage` | `message/_base.py:530`：`self.usage = usage.model_copy()` | :530 是 `self.usage = usage`（直接收养调用方对象） | 本仓不读也不调 `append_usage`（`grep -rn "append_usage\|\.usage" app/alibot app/tests` 无命中）；运行期脚本另测：追加后把原 `usage.input_tokens` 改成 999，`msg.usage.input_tokens` 仍是 10 |

逐项可复跑的 grep：

```sh
V=.venv/lib/python3.11/site-packages/agentscope
grep -n "if not task.done()"                                $V/pipeline/_team_pipeline.py
grep -n "SOPPhase.AWAITING, SOPPhase.FAILED"                $V/sop/_engine.py
grep -n 'AnyUrl(f"workspace://'                             $V/workspace/_base.py
grep -n "existing_dir_names or os.path.exists"              $V/workspace/_local_workspace.py
grep -n "abs_path = os.path.realpath"                       $V/tool/_base.py
grep -n "inspect.isawaitable(result)"                       $V/tool/_adapters.py
grep -n "create_mcp_http_client(\|httpx.Timeout(config.timeout)" $V/mcp/_mcp_client.py
grep -n "usage.model_copy()"                                $V/message/_base.py
```

## 4. 判据 3：回归读数（升级前 vs 升级后）

升级前基线用**隔离 venv** 复现（不碰共享环境）：`uv venv /tmp/as209/venv --python 3.11` +
`uv pip install -e "<worktree>/app[dev]"`（此时 pin 仍是 2.0.9，解析结果 `agentscope==2.0.9`）。

| 读数 | 2.0.9（升级前基线） | 2.0.10（升级后） |
| --- | --- | --- |
| pytest | `653 passed, 2 skipped in 339.83s` | `653 passed, 2 skipped in 313.86s` |
| failed / error | 0 / 0 | 0 / 0 |
| 跳过项 | 2（`test_agent_smoke.py::test_real_model_round_trip` 缺 `SMOKE_MODEL`；`test_reminder_schema_step.py::test_the_date_does_not_depend_on_when_the_migration_runs` 缺 libfaketime） | 2（同两条） |

命令（两边相同）：`cd app && python -m pytest --no-header -p no:cacheprovider`。
2.0.9 的 venv 里 `agentscope.__version__ == "2.0.9"`、2.0.10 的共享 venv 里是 `"2.0.10"`（已核对）。

全量门禁（`scripts/check.sh`，唯一入口）：

- pytest：见上表；
- ruff check / `ruff format --check` / mypy（仓库根）/ pyright：全绿；
- `node --test app/tests/*.test.cjs`：`132` 个用例全绿（与升级前同一集合，无跳过）。

**没有回归，因此没有为适配而改任何业务代码**——唯一的代码文件 diff 是 `agent_seam.py` 的注释版本号。

## 5. 判据 4：影响面与“不引入新行为”

- 规格清单 8 项里，**只有 #6（`FunctionTool`）落在本仓真实调用点上**，且三个包装函数都是同步返回
  `str`，2.0.10 的 `isawaitable` 分支对它们等价。
- 其余 7 项在本仓**没有调用点**：`grep -rn "TeamPipeline\|SOPEngine\|WorkspaceBase\|LocalWorkspace\|MCPClient"
  app/alibot app/tests` 无命中；`append_usage` / `.usage` 也无命中。它们只约束后续 T/P/W/M 票的装配。
- 本票不新增测试：升版没有新行为；把框架内部源码钉进测试会越过既有测试分层（见 §3 方法说明）。
  既有 `653` 个 pytest + `132` 个 DOM 用例就是这次升版的回归面。

### 清单之外、但属于本仓会用到的模块

2.0.10 的 wheel 与 2.0.9 共 **107 个文件**不同（`diff -rq`；含 `app/` 服务面、`realtime/`、`rag/`、
`tts/`、`deploy/` 等本仓不使用的模块，按 D9 不采用）。与本仓 import 面相关的：

| 模块 | 2.0.10 差异 | 本仓影响 |
| --- | --- | --- |
| `agent/_agent.py` | **无差异**（`agent/` 只有 `_realtime/_agent.py` 变了） | 无 |
| `credential/`、`permission/`、`model/_base.py` | **无差异**（`PROVIDER_CREDENTIAL_TYPE` 的 discriminator 与 `_TOOL_CHOICE_LITERAL_MODES` 不变） | 无 |
| `tool/_utils.py` | 生成工具 schema 时跳过 toolkit 注入的 `_agent_state` 参数 | 本仓工具函数没有该参数 → 无 |
| `tool/_builtin/*` | 文件工具内部调整（realpath 等） | 未装配（W 票） → 无 |
| `model/_anthropic` | 流式 tool call **无参数**时补 `input="{}"`（`content_block_stop` 分支） | `declare_silence` 无参数：解析更稳，属改善；无需适配 |
| `model/_ollama` | tool call id 改为 `_generate_id()`（原为 `"{idx}_{name}"`，跨轮会撞） | 多轮重复调用同名工具时不再撞 id；无需适配 |
| `model/_deepseek` / `_moonshot` / `_volcengine` | `extra_body` 不再原地改调用方 dict；moonshot 读 `prompt_tokens_details.cached_tokens` | 本仓只传 `parameters=...Parameters()`，不传 `extra_body`/`thinking` → 无 |
| `model/_gemini` | 每调用 `config` 覆盖模型级默认值、cache token 回退 `or 0` | 未传 per-call config → 无 |
| `model/_openai_chat` / `_dashscope` | 音频输出（WAV 包装 / omni transcript） | 本仓不请求音频 → 无 |

## 6. 残余与说明

1. 规格 §2.6 对 Workspace 的表述（“`file://` 改成 `workspace://`”）在 2.0.9→2.0.10 的实际 diff 里
   **只对应文档串**；代码里的 `workspace://` URL 构造两版相同。已按实际核对结果记录，不改规格原文。
2. `TeamPipeline` / SOP 仍是框架的**实验性**模块（2.0.10 `METADATA` 的 News 标 `Experimental`；官方文档
   同样声明），与规格 §2.6 一致：本票只核对差异，不把它们当稳定承诺。
3. 实施期间分配给我的 worktree（`/Users/apple/orca/workspaces/alibot/issue-137-impl`）被外部清理**删除两次**
   （连同本地分支），最终工作在 `/tmp/issue-137-impl` 完成并推送到 `origin/issue-137-impl`；
   内容不受影响。
