## Summary

```text
before
  agent_seam._mcp_declarations ──→ mcp_server 库行（scope_kind='agent'，装配正本）
  DM / 无项目根轮次 ──→ 不装配 MCP（没有 workspace）
  Studio 上块（库行） ≠ 下块（家目录 mcp.json 原文）

after
  agent_seam._mcp_declarations ──→ ~/.alibot/agents/<member>/mcp.json（唯一正本）
  DM / 无项目根轮次 ──→ <data_dir>/mcp-hosts/<session_id> 运行宿主（无项目文件工具）
  Studio 上块 + 下块 + 运行期装配 ──→ 同一份文件
  mcp_server 旧行 ──→ export-mcp 显式导出（dry-run / 备份 / 冲突拒绝 / 不删行）
```

- **正本迁移**：`mcp.load_member_mcp_declarations` 成为管理面与运行期唯一的读路径；`/api/mcp-servers`
  的增删改直接写成员文件（合成标识 `global:<member>:<name>`），坏文件拒绝覆盖，`env_names` 仍只存名字。
- **DM 可用且不越界**：无项目根轮次在数据目录内开一个只托管 MCP 的运行宿主，不挂任何项目文件工具；
  轮末先关客户端再关宿主，实例按 `(member, session)` 隔离。
- **迁移可验证**：`export-mcp` 默认 dry-run，`--apply --yes` 才写；整目录备份 + 清单 + 逐文件备份，
  同名不同形整体拒绝，保留文件里的额外条目，不删库行，写后用同一读路径回读。旧行上**已有**的远端
  确认会复制到合成标识——**新建与「文件里早已同形」两种条目都算**（否则先跑过旧 `migrate-mcp` 的
  安装升级后远端会被读成未确认而不再建连）；**不代做新确认**。`migrate-mcp` 退役（退出码 6，
  带 `--apply --yes` 也一样）。
- **文档同步**：ADR-0021 §三、ADR-0031（标题 + 修订段 + §一/§二/§三/§四/§六）、ADR-0038 阶段 C 记录、
  `CONTEXT.md` 六个词条、`app/README.md` 迁移与端点面。

## Evidence

- **Before**（基线 worktree，HEAD `b80412b`；库里放 Agent 级声明后跑 DM 轮次；红测试随票留档
  `app/specs/agent-mcp-home-red-dm-mcp.py`）：
  `AssertionError: 基线 DM 工具面：['declare_silence', 'memory_search']`
- **After**（`app/tests/test_dm_mcp.py`）：`2 passed`；DM 工具面含 `mcp__stub__echo`，不含
  `Write/Edit/Glob/Grep/Bash`，stdio cwd 落在 `<数据目录>/mcp-hosts/...`。
- **迁移实测**（临时 HOME + 临时数据目录 `/tmp/e96d-live`，未触碰真实数据）：
  - dry-run 零写入，报告点名「将复制既有远端确认： 2 条」（新建 1 + 早已同形 1）；
  - apply 写出 2 条、复制 2 条远端确认、写后校验通过；库里旧行与新合成标识确认行都在；
  - 再跑一次幂等（零条目、零备份）；把同名条目改成不同形状后 apply `exit=6` 且文件 sha 不变；
  - `migrate-mcp --apply --yes` 打印改用指引并 `exit=6`。
- **门禁**：`scripts/check.sh` 全绿 —— pytest `1291 passed, 11 skipped`；ruff / ruff format / mypy
  （137 files）/ pyright 全过；Vitest `24 files / 471 tests passed`。
- 验收记录：`app/specs/agent-mcp-home-acceptance.md`（判据 → 落点 → 读数）。

## Merge Danger

**Door:** two-way（代码回退即可恢复库行装配；导出**从不删除**库行与源文件，且每次改写前有数据目录 +
逐文件备份），但**未跑 `export-mcp` 的既有安装**在合并后运行期不再装配旧库行——这是本次正本切换的
显式迁移代价，已在 ADR / README / 退役提示里写明。

**Blast Radius:** MCP 装配、Studio mcp 页签、MCP 迁移命令与文档；DM 首次获得 MCP 能力；数据目录多出
`mcp-hosts/<session_id>/` 运行期目录。
