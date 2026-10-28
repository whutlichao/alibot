# 验收记录：<票标题>

**票型**：实施票｜**门禁**：G-impl｜**测试档**：窄（<选择器 / 影响面为何可圈定>）
**Ticket**：<#n；无 issue 时写「用户反馈直派」>｜**base**：`<commit>`｜**branch**：`<branch>`
**PR**：<链接>｜**PR body**：`app/specs/<slug>-pr-body.md`
**CI**：<run 链接> success（`<head>`）
**采集时间**：YYYY-MM-DD｜**环境**：<OS / Python / 浏览器版本>

> PR 号与 CI 读数在 PR 创建后**一次**回填（理由与触发条件见 `docs/agents/implementation.md` §交付）。

---

## Status

| 判据（票面 Observable acceptance） | 落点 | 读数 | 结论 |
| --- | --- | --- | --- |
| ① <判据原文> | `<file:line>` / `<data-od-id>` | <命令输出或浏览器读数原文> | ✅ |

## 红证据（隔离基线 `<base>`）

```text
<基线 worktree 上同一批用例的失败读数；没有基线就说清为什么不需要>
```

## 改动落点

| 文件 | 改动 |
| --- | --- |
| `<path>` | <一句话> |

## 命令与读数

```sh
scripts/check.sh --fast   # <读数>
scripts/check.sh          # <读数>
```

## 说明

- <未跑的测试 / 省略的 review 及理由 / 构建产物处置 / 已知代价>
