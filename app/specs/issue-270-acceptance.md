# #270 验收记录

## 判据 → 落点

| #269 决定 | 文档落点 |
| --- | --- |
| 1. Skill 字节在 `~/.alibot/agents/<member_id>/skills/<name>/`，宿主写入；Docker 仍 `:ro`，宿主预读 `Skill.markdown`、容器只需读 `references/` | ADR-0021 §三；ADR-0031 §一、§三；CONTEXT.md「Agent 家目录」「Skill」；app/README.md 数据模型、Skill、workspace / Docker 说明 |
| 2. MCP 正本是 `mcp_server` Agent 级行；家目录 `mcp.json` 只读导入、由应用导出 | ADR-0021 §三；ADR-0031 §一、§二；CONTEXT.md「MCP 声明」；app/README.md 数据模型与 MCP API |
| 3. 频道级旧行与项目层旧 Skill 字节保留但不装配、不回填、不自动删除；迁移由 T5 `migrate-skills` 显式执行 | ADR-0021 §三；ADR-0031 §二、§四；CONTEXT.md「Skill」；app/README.md「Skill 归属修订与迁移策略」及 `/api/skills` |
| 4. Skill 装配只看成员家目录 > 全局公共库、不依赖 workspace；MCP 仍以 workspace 为宿主，无 workspace 时不装配 | ADR-0021 §三；ADR-0031 §二；CONTEXT.md「全局层 / 项目层」「MCP 声明」「Skill」；app/README.md Skill 与 MCP 说明 |
| 5. local Read / Glob / Grep 可将成员技能目录作为只读第二根；Write / Edit 仍限项目根 | ADR-0021 §六；ADR-0031 §三；CONTEXT.md「Workspace Root」「Agent 家目录」；app/README.md Workspace 与长期记忆说明 |
| 6. 不做 schema bump；数据迁移由独立命令完成，本票不执行 | ADR-0021 §三；ADR-0031 §二、§四；app/README.md「Skill 归属修订与迁移策略」明确无 schema bump、本轮不执行 |

## 矛盾表述处置

- ADR-0021 §一把项目 `.alibot/agents/<member_id>/` 限定为运行状态与 stdio MCP cwd；§三明确现行 Skill 字节家目录路径，并把旧项目字节与频道级行标为保留、弃用且不参与装配。
- ADR-0031 §一拆分家目录写权限，§二把 Skill 顺序收敛到成员家目录 > 全局公共库；Docker 家目录与公共库保持 `:ro`。原始 2026-10-22 决策记录未改写，并由顶部 #270 修订说明标为历史口径。
- CONTEXT.md 的 Skill、MCP 声明、全局层、项目层词条按新归属描述现行口径；旧频道行与项目层字节只作为历史数据说明保留。
- app/README.md 更新 `skill` 表行、装配顺序、Docker 说明、迁移策略及 `/api/skills` 的旧 `channel_id?` / channel scope 处置；这些旧参数不再构成装配来源。

## 检索证据

在四份指定文档检索旧路径 `workspace_root/\.alibot/skills|<workspace_root>/.alibot/skills`，命中仅为以下明确标记的历史 / 保留数据说明：

- `docs/adr/0021-workspace-is-an-explicit-project-boundary.md:45`：旧项目字节保留但弃用、装配忽略。
- `docs/adr/0031-agent-home-is-a-global-layer-beside-the-project-boundary.md:60,90`：§二明确旧字节保留但忽略；§四将旧路径标为原决策并列出 #270 修订。
- `CONTEXT.md:600`：旧项目字节作为历史数据保留且不进入装配。
- `app/README.md:518`：迁移说明明确旧字节保留但忽略。

旧优先级检索（`项目 Agent 级 > 项目频道级`、`项目就近、同名覆盖`）仅命中 ADR-0031:14-18 的原始 Decision；ADR-0031:3 明确标为历史，现行 §二:58 只保留成员家目录 > 全局公共库。

现行装配顺序见 ADR-0031 §二及 CONTEXT.md「全局层 / 项目层」，均为两层「成员家目录 > 全局公共库」；ADR-0031 顶部修订说明将未改写的原始决策记录标为历史口径。

## 命令与读数

- `scripts/check.sh --fast tests/test_docs_vocabulary_guard.py tests/test_test_layering_guard.py`：指定 pytest 用例 **14 passed**；脚本其余门禁显示 ruff check 全绿、ruff format --check **330 files already formatted**、mypy **125 source files 无问题**、pyright **0 errors**、Node **319 passed / 0 failed**，最终 `门禁全绿`。未运行 `ruff format`。
- `git diff --check`：退出码 0，无输出。
- AgentScope 快照 SHA-256 守卫：`./.venv/bin/pytest app/tests/test_agentscope_usage_guard.py -k snapshot_is_intact -q`，退出码 0，输出 `. [100%]`。
