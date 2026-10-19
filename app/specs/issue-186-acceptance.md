<!-- 本文件是 #186 的验收文档（判据 → 落点 → 命令与读数）；tracker 上的 issue #186 保持原样。 -->

# Issue #186 验收 —— 联网工具的装配与权限闸门（含 fetch 的 host 归一化）

> 票面：GitHub [#186](https://github.com/whutlichao/alibot/issues/186)（`feat(seam): …`，Part of #182）。
> 依据：**ADR-0026** §二 / §三 / §四（`docs/adr/0026-agent-web-access-is-explicit-opt-in.md`），
> 前置票 #184（schema 18 + 两个配置位）与 #185（工具本体）已在集成分支上。
> 票型：实施票。**不涉及 schema 号段与迁移**（`SCHEMA_VERSION` 一个字节都不动）。

| 项 | 值 |
| --- | --- |
| 分支 | `issue-186-impl`（从 `issue-182-integration` 的 `e8ec2cc` 切出） |
| 改动文件 | 待填 |
| 新增用例 | 待填 |
| 门禁 | 待填 |

---

## 0. 判据 → 落点

| 票面判据 | 落点 | 用例 |
| --- | --- | --- |
| 未配置 `web_access`：工具集逐字不变 | 待填 | 待填 |
| `read-only` + `web_access='search+fetch'`：工具集里没有 `fetch` | 待填 | 待填 |
| `ask-writes`：调用 `fetch` 产生 `approval_request`；拒绝后 `DENIED`、本轮可继续 | 待填 | 待填 |
| `permission_rule(agent)` 的 `fetch` host 规则放行该 host，其他 host 仍 ASK | 待填 | 待填 |
| 缺 `('web', provider)` 确认：工具不装配且**零请求** | 待填 | 待填 |
| `supports_tool_calling` 为假时不装配联网工具 | 待填 | 待填 |
| `fetch` 不设 `bypass_immune`（ADR-0026 §三） | 待填 | 待填 |
| 系统提示自陈 `fetch` 的风险 | 待填 | 待填 |
| 不新增 seam / 不新增前端框架 | 待填 | — |

## 1. 人定决策的落实方式

- **D1**（网络请求不独立计数、不进 `target_budget.used`）：待填
- **D2 / D4**（provider 只从进程环境解析；不加 schema 列、不给模型 provider 参数）：待填
- **D3**（#187 复用同一个 host 提取函数）：待填

## 2. 读数（红 → 绿）

待填

## 3. 门禁读数

待填

## 4. 细节自决与遗留

待填
