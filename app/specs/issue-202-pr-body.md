## Summary

私聊（DM）没有频道，联网档因此回退到 **Agent 级配置 + 默认档**；`fetch` 在 DM 永不装配。

```text
web_settings_for_target(target_id, member_id)
  kind = 'dm'       → agent_config.web_access or 'search'      mode = 'read-only'
                      backend = 'local' / docker_egress = False / egress_hosts = []
  kind = 'channel'  → agent_config.web_access or channel.web_access or 'search'   （逐字未改）
  kind = 'task'     → 同上（经 task.channel_id）                                  （逐字未改）
  未知 target       → None
```

- `_web_shared_settings_locked()`：频道 / DM 两条读路径共用**同一条**凭据 + 确认行读数。
- `mode='read-only'` 让既有 `FETCH_ACCESS_LEVEL and mode != "read-only"` 闸门直接挡住 `fetch`。
- Studio：Agent 编辑弹窗新增「联网」三档（`off` / `search` / `search+fetch`）；未选择 = **不提交**
  该字段（跟随频道），已选则随 `PATCH /api/agents/{id}` 提交。Agent 详情「运行时」补一行读数。

```text
openAgentEditModal ─→ renderAgentEditWebAccess(agent.web_access)   # 三档选中态
submitAgentEdit     ─→ web_access?（未选择时不带这个键）            # 频道级配置不被覆盖
agentRuntimeNodes   ─→ + 一行「联网档」（缺失显示空字段文案）
```

无 schema / 迁移改动；`fetch` 闸门、频道联网区块、`studio/app.css` 与 `alibot-design-system/**` 未动。

Closes #202

## Evidence

- **Before:** `cd app && /Users/apple/orca/alibot/.venv/bin/python -m pytest tests/test_web_assembly.py -k dm` → `3 failed, 1 passed`
  （DM 上 `web_search` 完全不存在：`{'declare_silence'}`）；`tests/test_web_access_config.py` → `4 failed, 8 passed`（DM 读路径返回 `None`）；`node --test app/tests/agent_detail_ui.test.cjs` →
  `pass 18 / fail 3`。红证据取自隔离副本（生产文件与 `e2ad031` 逐字节相同，md5 见验收文档 §2）。
- **After:** 同三条命令 → `4 passed` / `12 passed` / `pass 21, fail 0`。
- `scripts/check.sh`（唯一门禁入口）全绿：pytest `1000 passed, 10 skipped`；ruff check
  `All checks passed`；ruff format `251 files already formatted`；mypy `no issues in 104 source files`；
  pyright `0 errors`；`node --test` `210 pass / 0 fail`。
- 判据 → 落点 → 读数：`app/specs/issue-202-acceptance.md`。

## Merge Danger

**Door:** two-way（读路径 + Studio 入口；无 schema / 迁移，回滚 = revert 本 PR）。

**Blast Radius:** low—moderate：DM 从「没有联网工具」变成默认挂只读 `web_search`；频道 / 任务分支
读数逐字不变。已知遗留（票面验收 3 第三步与 #184「Agent 侧显式 `null` 一律 400」冲突，未擅自改
API 语义）：`PATCH /api/agents/{id} {"web_access":null}` 仍是 400；「回到默认档」当前只能靠不写该
字段（对尚未配置的 Agent 成立）。详见验收文档 §7.1，等待 coordinator / 人定后再决定是否另开票
放开清空语义。
