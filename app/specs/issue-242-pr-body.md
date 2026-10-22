## Summary

联网档为 `search+fetch` 时，`fetch` 不再逐次审批：`fetch_tool()` 给工具自身一个 `behavior=ALLOW` 的
`PermissionDecision`，`ask-writes` / `trusted-writes` 下调用直接执行。人工收紧的通道原样保留。

```diff
 fetch_tool(...)
-  return FetchTool(call_fetch, name="fetch", is_read_only=False)
+  return FetchTool(
+      call_fetch, name="fetch", is_read_only=False,
+      permission=PermissionDecision(behavior=ALLOW, message=…),
+  )

 PermissionEngine._check_default / _check_accept_edits
   deny 规则        → DENY      ← 仍生效
   ask 规则         → ASK       ← 仍生效（人工把某个 host 拉回审批）
   只读快速通道      → ALLOW
   工具 check_permissions → ALLOW  ← 本票新增的默认结论
   allow 规则        → ALLOW     ← 从此冗余（能力保留）
   兜底              → ASK
```

装配闸门一条未动：`web_access == 'search+fetch'` + 非 `read-only` + 数据面确认 + provider 解析。
`read-only` 下 `fetch` 仍不装配；`'off'` / `'search'` / DM 行为逐字不变。`web_search`、`permissions.py`
的 host 归一化、`match_rule`、schema、审批卡前端均未触碰。

```text
app/alibot/web.py              # fetch_tool() 的 permission + 文档串
app/alibot/agent_seam.py       # _web_prompt_note() 如实描述 + 一处注释
app/alibot/studio/i18n.js      # 两条词条 × zh-CN/en
app/tests/test_permissions.py  # ALLOW / deny / ask 的权限层判据
app/tests/test_web_assembly.py # 无审批执行 / ask 规则仍审批 / deny 零请求
app/README.md, CONTEXT.md      # 联网档小节
docs/adr/0026-…md              # 追加「修订（2026-10-22，#242）」§十六
app/specs/issue-242-acceptance.md
```

## Evidence

- **Before:** 隔离基线副本（`git archive e989eec app` + 工作区新测试）：
  `tests/test_permissions.py` **6 failed**（`fetch` 在 `ask-writes` 下返回 ASK），
  `tests/test_web_assembly.py` **2 failed**（无审批路径在旧代码上要求 `ApprovalBroker`）。
  证据目录：`/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/alibot-red-242.MmfSvi`。
- **After:** `cd app && ../.venv/bin/python -m pytest tests/test_permissions.py tests/test_web_tools.py tests/test_web_assembly.py tests/test_docker_network_policy.py` → **87 passed**；
  加跑三条机械守卫 → **111 passed**；`node --test app/tests/{i18n_ui,agent_detail_ui,channel_config_ui}.test.cjs` → **105 passed**。
- **关键判据**（`test_deny_and_ask_rules_still_win_over_the_tools_own_allow`）：两种写档下 `deny` → `DENY`、
  `ask` → `ASK`，未命中的 host 仍 `ALLOW`——证明规则判定在工具 `ALLOW` 之前，人工收紧没有被自动放行吃掉。
- **全量门禁**：`scripts/check.sh` → `1133 passed, 11 skipped` / ruff check 绿 / `ruff format --check`
  `302 files already formatted` / mypy `Success: no issues found in 122 source files` / pyright
  `0 errors, 0 warnings, 0 informations` / `node --test` `269 pass, 0 fail` → **门禁全绿**。CI 跑同一脚本复核。
- **双轴 code-review**：Standards 1 条（已修：补「实测」标注），Spec 1 条（`scripts/red-evidence.sh`
  只支持 `*.test.cjs`，`.py` 红证据用等价隔离副本取证并记录）。详见验收文档 §5。

## Merge Danger

**Door:** one-way

**触发项：权限** —— 本 PR 改变 `fetch` 在 `ask-writes` / `trusted-writes` 下的**默认审批行为**
（从每次 ASK 变为直接执行），属于 merge-danger 触发项「认证 / 权限 / 密钥」：任意一项成立，
整个 PR 就是单向门，拿不准一律按单向门处理。权限的默认语义是使用者的安全契约，不适用
「回滚一下就能恢复」这类按回滚成本判门的论证。**合并方式由人类决定**——coordinator 不自动合并
单向门。

**需要人审的语义变更**：本 PR 改变 `fetch` 的**默认审批行为**——`ask-writes` 下从「每次 ASK」变为
「直接执行」。这是 coordinator 已获授权的产品决策（2026-10-22 人定，ADR-0026 修订节 §十六），但按
`docs/agents/implementation.md` 与 ADR 惯例，默认语义变更仍需人按 G-merge 判定。否决 / 收紧通道保留：
`permission_rule(tool_name='fetch', pattern=<host>, behavior='deny'|'ask')` 仍在工具判定之前生效；
`read-only` 档仍不装配 `fetch`。

**Blast Radius:** 单个工具的默认权限判定 + 文档/文案；`web_search`、DM、容器出网、审批卡前端不受影响。

Closes #242
