## Summary

Issue #199 把 Agent 联网从「两家 provider + 默认关闭 + 密钥只读环境」推进到「五家 provider + 凭据入库 + 默认开启搜索 + Studio 设置面」。

```text
search resolution (web.describe_search_provider)
  ALIBOT_WEB_SEARCH_PROVIDER
    └→ credential table is_default
        └→ configured fixed order: brave → tavily → perplexity → jina → exa
            └→ keyless exa-mcp (Exa MCP, JSON-RPC + SSE)
```

```text
app/alibot/
├── web.py          # 5 credentialed backends + exa-mcp，统一 SearchHit / 错误收口
├── db.py           # schema 21: web_provider_credential；三态 web_access；掩码视图
├── migrate.py      # 20 → 21：凭据表 + 两表 CHECK 重建（无回填）
├── service.py      # 人类面掩码读数 + 保存/清除/默认
├── api.py          # GET /api/web-providers + GET/PUT/DELETE .../credential
└── studio/         # 频道设置「联网」区块（档位 / provider 来源 / 五个 key 行）
```

关键语义：

- 读路径 `agent_config.web_access or channel.web_access or "search"`；只有 `"off"` 移除联网工具。
- 凭据**库内优先、环境变量兜底**；保存 key 同时写 `remote_consent_scope('web', provider)`。
- `exa-mcp` 免凭据、免确认；带凭据 provider 仍必须确认。
- 任何 HTTP / 日志 / 事件只出现掩码（`sk-…abcd`），`fetch` 的全部闸门逐字未动。

## Evidence

- **Before:** 基线 worktree（`origin/main` = `3d1e424`）跑同一组新用例：`15 failed, 18 passed, 1 error`；Studio `21 tests / 16 pass / 5 fail`（联网区块不存在）。原文见 `app/specs/issue-199-evidence/red-*.txt`。
  **After:** `65 passed`（五个 Python 用例文件）；Studio `21/21`；`scripts/check.sh`：`984 passed, 10 skipped` + ruff / mypy / pyright / `node --test` 208 passed（`门禁全绿`）；原文见 `app/specs/issue-199-evidence/check-final.txt`。
- **真实 Exa MCP 冒烟：** 无任何凭据时 `describe_search_provider({}) == exa-mcp`，`search_tool` 返回 3 条真实结果（`docs.agentscope.io/.../release-notes`、GitHub releases、changelog）。
- **替身传输：** Tavily 请求形状 `POST https://api.tavily.com/search` + `Authorization: Bearer …` + `{"query","max_results"}`；掩码 `tvl…abcd`，完整 key 不在掩码里。
- **UI（ego-browser，隔离数据）：** 默认 `search` + `Exa MCP（免密钥）`；保存假 key 后 `已配置 · sk-…abcd`、输入框清空；设为默认后 `Brave（来源：界面配置）`；清除并保存 `off` 后读回 `web_access="off"`。截图：`app/specs/issue-199-evidence/0*.png`。
- **迁移：** v20 真实库 → v21，`改动 0 行`，`schema_version=21`。

## Merge Danger

**Door:** one-way

- schema 21 迁移：`web_provider_credential` 新表 + `channel` / `agent_config` 的 `web_access` CHECK 重建。旧代码不能打开新库；回滚只能恢复迁移前整目录备份。
- **默认值语义变更**：未配置从「关闭」变成「默认 `search`」。升级后既有频道会获得只读的 `web_search`；需要关闭的部署必须显式写 `off`。
- **密钥存储**：`api_key` 明文入库，部署层必须把库文件权限设为 `0600`；HTTP / 日志 / 事件只回掩码。
- 迁移连接在 `BEGIN` 前关闭 `foreign_keys`（为了重建被引用的父表），链末以 `PRAGMA foreign_key_check` 兜底；schema 的 FK 定义不变。

**Blast Radius:** Agent 联网装配 / 迁移 / Studio 频道设置

- 所有联网轮次的 provider 选择、确认闸门与凭据来源都走新的唯一解析顺序。
- `scripts/check.sh` 全绿后才合并；迁移失败会回滚并保留备份。

Closes #199
