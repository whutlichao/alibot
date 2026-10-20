# #199 验收：多 provider 搜索 + 凭据入库 + 联网默认档 + Studio 设置面

判据来源：派发任务书 Change A–F；术语遵循 `CONTEXT.md`（联网档 / 凭据 / 远端数据面确认）。
门禁唯一入口：`scripts/check.sh`（CI 跑同一个脚本）。

## 1. 判据 → 落点

| 验收判据 | 落点 | 读数 |
| --- | --- | --- |
| A1 五家带凭据 provider + 免凭据 `exa-mcp` | `app/alibot/web.py` `SEARCH_BACKENDS` | `tests/test_web_tools.py` 的五个形状用例 + `test_exa_mcp_posts_a_jsonrpc_tools_call_without_any_credential` |
| A3 解析四条路径 | `describe_search_provider()` | 四条路径各一条用例（环境显式 / DB 默认 / 固定顺序 / 免凭据回退） |
| A4 结果映射与上限 | `_search_hits` / `_exa_text_hits` / `_render_hits` | 截断、空结果、429、JSON-RPC error / `isError` 用例 |
| A5 `fetch` 闸门不变 | `fetch_tool` 未改动 | 既有 `test_web_tools.py` fetch 用例全绿 |
| B1 schema 22 + 凭据表 | `db.py` SCHEMA / `migrate._step_21_to_22` | `tests/test_web_access_schema.py`、`tests/test_schema_migration.py` |
| B2 掩码回读 | `db.web_provider_credential_views()` / `web.mask_credential()` | `tests/test_web_providers.py` 断言响应里找不到完整 key |
| B3 凭据 API | `api.py` 四条端点 + `service.py` | `tests/test_web_providers.py`（PUT/GET/DELETE、默认切换、400 不部分写入） |
| B4 `exa-mcp` 免确认 | `agent_seam._web_tools` | `test_an_unset_web_access_mounts_web_search_by_default` |
| C1 三态 CHECK | `WEB_ACCESS_LEVELS = ("off", "search", "search+fetch")` | schema 用例 + `test_web_access_config.py` |
| C2 默认 `search` / `off` 关闭 | `db.web_settings_for_target` | `web_settings_for_target` 读数（见 §3.4） |
| D1 Studio 联网区块 | `studio/app.js` `webAccessSection` | `tests/channel_config_ui.test.cjs` 四条联网用例 |
| D2 文案两端 | `studio/i18n.js` | `tests/i18n_ui.test.cjs` 键集 / 参数 / 裸中文扫描 |
| E 文档 | ADR-0026 修订节、`CONTEXT.md`、`app/README.md` | 人工复核 |
| F 测试 | 本文件 §2 / §3 | 红→绿与真机读数 |

## 2. 红 → 绿（同一命令）

基线：`git worktree add /tmp/alibot-main-199 origin/main`（= `3d1e424`），只复制本票改动后的测试文件，
不复制实现。

```sh
# 红：旧代码
cd /tmp/alibot-main-199/app
../.venv/bin/python -m pytest -o addopts="" \
  tests/test_web_tools.py tests/test_web_assembly.py tests/test_web_access_schema.py \
  tests/test_web_access_config.py tests/test_web_providers.py \
  --continue-on-collection-errors -q
# 15 failed, 18 passed, 1 error（`test_web_tools.py` 导入 `FALLBACK_PROVIDER` 失败；
#   `off` 被 CHECK 拒、迁移没有 21→22、凭据端点 404、默认档未挂工具）
node --test app/tests/channel_config_ui.test.cjs
# 21 tests, 16 pass, 5 fail（联网区块不存在）
```

```sh
# 绿：本票代码
cd app
../.venv/bin/python -m pytest -o addopts="" \
  tests/test_web_tools.py tests/test_web_assembly.py tests/test_web_access_schema.py \
  tests/test_web_access_config.py tests/test_web_providers.py -q
# 65 passed in 9.05s
node --test tests/channel_config_ui.test.cjs
# 21 tests, 21 pass
```

红证据原文：`app/specs/issue-199-evidence/red-pytest.txt`、`red-ui.txt`。

四类重点判据的单独红→绿：

- **A3**：旧 `resolve_search_provider({})` 抛 `ProviderResolutionError`（要求设置 BRAVE / TAVILY key）；
  新代码返回 `exa-mcp`（`source=fallback`）。
- **B3**：旧 `PUT /api/web-providers/brave/credential` → 404；新代码 200 且响应只有 `sk-…abcd`。
- **C2**：旧库 `CHECK (web_access IN ('search','search+fetch'))` 拒绝 `off`；新库接受三态，且新频道
  `web_settings_for_target(...)["web_access"] == "search"`、`PATCH {"web_access":"off"}` 后为 `None`。
- **D**：旧 Studio 没有 `[data-od-id=channel-config-section-web]`；新面板四条 DOM 用例全绿。

## 3. 真实冒烟（一次性脚本，不入库）

### 3.1 无凭据时走 `exa-mcp`

```sh
cd app
../.venv/bin/python - <<'EOF'
import asyncio
from alibot.web import describe_search_provider, search_tool
async def main():
    print("provider:", describe_search_provider({}))
    tool = search_tool(environ={})
    result = await tool(query="AgentScope 2.0.10 release notes", count=3)
    chunks = [chunk async for chunk in result] if hasattr(result, "__aiter__") else [result]
    print(chunks[-1].state)
    print("".join(b.text for b in chunks[-1].content if getattr(b, "text", None))[:900])
asyncio.run(main())
EOF
```

读数（2026-10-20）：

```text
provider: ProviderChoice(provider='exa-mcp', source='fallback')
state: success
搜索「AgentScope 2.0.10 release notes」返回 3 条结果：
1. Release Notes - AgentScope
   URL：https://docs.agentscope.io/en/versions/2.0.10/release-notes
2. Releases · agentscope-ai/agentscope · GitHub
   URL：https://github.com/agentscope-ai/agentscope/releases
3. Changelog - AgentScope
   URL：https://docs.agentscope.io/en/versions/2.0.10/others/change-log
```

实现注记：Exa 的公开 MCP 端点会拒绝 `Python-urllib` 的默认 UA（403）；搜索请求因此显式带
`User-Agent: alibot-web-tools/0.1`（与 `fetch` 同一 UA）。

### 3.2 替身传输证明带凭据 provider 的请求形状与掩码

脚本：`app/specs/issue-199-evidence/smoke-credential-shape.py`

```text
method: POST
url: https://api.tavily.com/search
auth header: Bearer tvly-1234567890abcd
body: {'query': 'AgentScope', 'max_results': 2}
tool state: success
masked read: tvl…abcd
full key in mask: False
```

## 4. 默认档 / 关闭位读数

### 4.1 新频道默认 `search`

```text
channel.web_access: None            # 库里仍是「未配置」，不回填
web_settings.web_access: search     # 读路径回退默认档
credentials: {} default: None
```

### 4.2 `off` 显式关闭

```text
# PATCH /api/channels/{id}/config {"web_access":"off"} 后
channel: ch_b7995c66ac43
web_settings: {'web_access': None, 'credentials': {}, 'default_provider': None}
```

迁移读数（真实子进程）：

```text
迁移：/tmp/alibot-199-ui/alibot.db
源版本：21 → 目标版本：22
本次备份：/tmp/alibot-199-ui-backup-19-to-20-20261020T144602Z（该次为合并 main 前的旧读数；合并 main 后本步顺延为 21 → 22，因为 #195 的 `20 → 21` 先落地）
  21 → 22  web provider credentials + explicit off level ... 改动 0 行
迁移完成：schema_version = 22；本次共改动 0 行。
```

## 5. UI 实证（ego-browser，隔离数据目录 `/tmp/alibot-199-ui`）

操作序列：打开频道设置 →「联网」区块 → 保存一个假 key → 设为默认 → 清除 → 切 `off` 并保存配置。

| 步骤 | 页面读数 | 截图 |
| --- | --- | --- |
| 打开设置面 | 档位 `search` 选中；当前 provider `Exa MCP（免密钥）（来源：免密钥回退）`；五家均为「未配置」；key 输入框 `type=password` | `01-web-section.png` |
| 保存假 key `sk-fake-199-abcd` | 状态 `已配置 · sk-…abcd`；输入框 `value=""`，`placeholder="sk-…abcd"` | `02-key-saved.png` |
| 设为默认 | 当前 provider `Brave（来源：界面配置）`；Brave 行出现「默认」徽标 | `03-default-brave.png` |
| 清除 + 保存 `off` | 状态回到「未配置」；当前 provider 回到 `Exa MCP（免密钥）（来源：免密钥回退）`；`GET /api/channels/{id}/config` 的 `web_access="off"` | `04-off-and-cleared.png` |

截图正本：`app/specs/issue-199-evidence/*.png`。页面完整快照确认了「联网」区块位于「默认权限档」
与「保存配置」之间，且五项 provider 行均带「保存凭据 / 设为默认 / 清除」。

## 6. 门禁读数

```sh
scripts/check.sh
```

```text
== pytest（全量）
984 passed, 10 skipped in 577.52s (0:09:37)
== ruff check
All checks passed!
== ruff format --check（只检查；不要跑 ruff format，它会整文件重排）
249 files already formatted
== mypy（仓库根）
Success: no issues found in 104 source files
== pyright
0 errors, 0 warnings, 0 informations
== node --test（Studio DOM 用例族：glob，新增文件自动纳入）
ℹ tests 208
ℹ pass 208
ℹ fail 0
门禁全绿
```

完整 stdout：`app/specs/issue-199-evidence/check-final.txt`。

迁移连接在 `BEGIN` 之前关闭 `foreign_keys`：重建 `channel` 这类被引用的父表必须先隐式删除父行，
延迟违例在 `COMMIT` 仍会失败。链末的 `PRAGMA foreign_key_check` 证明数据仍完整，schema 的 FK 定义不变；
这一步是 #199 为「改父表列级 CHECK」引入的共享路径变化，PR Merge Danger 已列为**单向门**的一部分。

## 7. 双轴 code-review 处置

| 发现 | 轴 | 级别 | 处置 |
| --- | --- | --- | --- |
| README 端点表仍写「只接受 brave / tavily」「唯一写路径」，且缺四个新端点 | Standards | P2 | 已改：端点表补 `GET /api/web-providers` 与凭据三条；consent 说明改为五家 + `exa-mcp` 免确认 |
| `mask_credential` 对短 key 可拼回完整值（前缀 + 尾字符） | Standards | P2 | 已修：不足 4 个隐藏字符时只回 `…`；补短 key 用例 |
| 已配置后 `PUT {"api_key": ""}` 是静默 200 | Standards | P2 | 已修：`api_key` **显式出现**即校验非空，补已配置后的空串用例 |
| README 备份目录名仍写 `15-to-19` | Standards | P3 | 已改为 `15-to-20` |
| provider 校验 / 视图查找重复、按索引耦合 | Standards | P3 | 已收敛到 `_require_web_provider` 与按 provider 查找；凭据读路径复用 `web_provider_credentials()` |
| 迁移连接为重建父表关闭外键执行 | Standards | P3 | 已在 §6 与 PR Merge Danger 明写：链末 `PRAGMA foreign_key_check` 兜底，schema 的 FK 定义不变 |
| 显式 `ALIBOT_WEB_SEARCH_PROVIDER=exa` 无 key 时报错而非走 MCP | Spec | P2 | 已修：显式 exa 无任何 key ⇒ `exa-mcp`；有 key ⇒ REST |
| `web_access: null` 的 API 文案仍说「关闭」 | Spec | P2 | 已改文案：`null` = 回到未配置（读路径回退 `search`），显式关闭写 `"off"` |
| env-only provider 的列表状态显示「未配置」 | Spec | P3 | 已加 `env_configured`；界面显示「由环境变量提供」，但清除 / 设为默认仍只对库内行生效 |
| key 输入框缺 label | Spec | P3 | 已加 `<label for>` + `webProviders.keyLabel`（两端词条） |
| `read_api_key` 文档说「应用库不存任何密钥」 | Spec | P3 | 已改为「模型凭证只从环境读；联网凭据是独立表」 |

P0/P1：无。P2 全部修复；P3 全部处置或明写为有意取舍（UA 统一、`source=invalid` 读数是界面兜底）。

## 8. 已知边界

- 不实现 DuckDuckGo / Startpage 抓取（2026-10-20 实测不可用），不新增浏览器内核或代理。
- 凭据明文入库是**部署层责任**：库文件权限 `0600`；本仓不做应用层加密，也不声称 E2EE。
- `fetch` 的全部闸门逐字未动；`web_search` 只送查询词与条数（外加 transport 级 UA / 凭据头）。
