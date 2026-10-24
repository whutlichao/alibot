## Summary

Agent 详情把「暂停 / 恢复」与新增的删除一起收进页面最后的**危险操作**区块；删除是后端完整软删除，
前端两步确认。消息 / 任务 / DM / `round_outcome` 一律保留，删除只写 `member.deleted_at` 并清空该成员
的 `target_member` 行。

**follow-up（2026-10-24 用户定夺）**三处修正：配置区不再有「启用状态」档位（`enabled` 的唯一界面
入口是危险区的暂停 / 恢复按钮）；`taskMemberName()` 的就绪判据从 `state.ready` 改为 `workspaceLoaded`；
危险说明 / 确认文案不再声称「从工作台隐藏」，改成「从 Agent 列表与全部频道中移除；私信、历史消息与
任务保留」。

```text
Agent 详情（#adBody）
  agentFactsSection()      统计
  agentPromptSection()     系统提示
  agentConfigSection()     配置（暂停按钮 / pauseNote /「启用状态」档位均已移出）
  globalMcpSection()       全局 MCP
  agentDangerSection()     危险操作  ← 新增，页面最后一块
    ├─ 危险说明（agentDetail.dangerHint）
    ├─ [暂停 / 恢复] data-agent-toggle（enabled 的唯一界面入口，仍只写草稿）
    ├─ [删除] data-agent-delete（第一次点击只展开确认区）
    └─ [确认删除] / [取消]  ← 第二次点击才发请求
```

```text
DELETE /api/agents/{id}
  db.delete_agent
    ├─ member.deleted_at = now                     # 行保留，历史消息作者仍按行渲染
    ├─ DELETE target_member WHERE member_id = ?    # 移出全部频道与任务线程
    └─ 不碰 message / task / dm / agent_config / round_outcome
  → 200 {deleted, agents, members}
  → 404 owner / 未知 id / 已删除（二次删除给「已不存在」，不是幂等 204）
```

## Evidence

- **Before（DOM，隔离基线 `97184ff`）**：`scripts/red-evidence.sh 97184ff app/tests/agent_detail_ui.test.cjs`
  → `tests 38 / pass 28 / fail 10`；失败项正是危险区不存在、删除按钮 / 确认区不存在、暂停仍在配置区、
  已删除指派无回退。
- **Before（follow-up，DOM，隔离基线 `657bc85`）**：
  `scripts/red-evidence.sh 657bc85 app/tests/agent_detail_ui.test.cjs` → `tests 39 / pass 34 / fail 5`；
  失败项正是本轮三处修正（配置区仍有「启用状态」档位、`state.ready` 判据、危险文案仍说「从工作台
  隐藏」）与它们带出的词条 / 无障碍断言。
- **Before（API）**：旧代码上 `DELETE /api/agents/{id}` → `404`（没有路由）；
  `test_delete_soft_deletes_the_agent_and_keeps_history` 红在 `assert 404 == 200`。
- **After**：`node --test app/tests/agent_detail_ui.test.cjs` → 39/39；
  `cd app && ../.venv/bin/python -m pytest tests/test_agent_detail.py` → 17/17；
  `node --test app/tests/i18n_ui.test.cjs` → 53/53（两份键集、无裸中文）。
- **门禁**：`scripts/check.sh` → exit 0；pytest `1173 passed, 11 skipped`、ruff check / format 全过、
  mypy `no issues`（124 files）、pyright `0 errors`、node glob `291 pass / 0 fail`。
  follow-up 后本地 `scripts/check.sh --fast` → exit 0（ruff / format 315 files / mypy 124 files /
  pyright 0 errors / node glob `292 pass / 0 fail`）；全量门禁由 CI 在最终 head 上复核。
- **真机视口（ego-browser + 真实 Studio，1350 / 900 / 420）**：`scrollWidth == viewport`；危险区文本 /
  四个按钮越界 **0**、按钮两两相交 **0**，确认区在三档下都展开。
- **双轴 review**：Standards 2 hard → 2 修（CONTEXT 的 Channel Join `_Avoid_` 澄清、README 不再把删除
  挂到 ADR-0011）；Spec 2 missing / 1 scope / 1 wrong → 在途禁用用例 + 真机视口读数补齐、
  `taskMemberName` 增加「成员表未就绪」判据。follow-up 又对这三处修正做了**聚焦的双轴复核**：
  Standards hard 0 / 4 条判断题全部修（档位组取值与点击共用一份知识、`workspaceLoaded` 注释口径、
  测试注入孔与 `state.ready` 断言、危险文案按节点断言）；Spec 6 条 → 4 修 + 1 确认边界
  （`workspaceLoaded` 置位时机）+ 1 流程项（本 body 已更新）。结论与未修理由见
  `app/specs/agent-danger-zone-delete-acceptance.md` 的「双轴 code-review / follow-up 修正」。

## Merge Danger

**Door:** one-way。软删除保留行但没有恢复入口；合并后用户真的能不可逆地移除一个 Agent，
恢复需要数据库 / 运维介入。

**Blast Radius:** Agent 身份面。删除会改变 Agent 列表、频道成员、提及候选、状态点与搜索命中面；
消息 / 任务 / 历史读数已用用例证明逐字不变，schema 未改。

## Ticket / Docs

- 验收记录（红→绿、门禁读数、双轴 review、遗留）：`app/specs/agent-danger-zone-delete-acceptance.md`
- 契约同步：`CONTEXT.md` **成员** 词条、`app/README.md` 端点表与详情页段落。
- 关联：本票没有 GitHub issue 号，不写 `Closes`。
