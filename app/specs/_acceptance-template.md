# 验收记录：<票标题>

**票型**：实施票｜**门禁**：G-impl｜**测试档**：窄（<选择器 / 影响面为何可圈定>）
**Ticket**：<#n；无 issue 时写「用户反馈直派」>｜**base**：`<commit>`｜**branch**：`<branch>`
**PR body**：`app/specs/<slug>-pr-body.md`
**采集时间**：YYYY-MM-DD｜**环境**：<OS / Python / 浏览器版本>

> 本文件在**创建 PR 之前**提交，只记当时已得的读数。PR 号与 CI 状态以 GitHub 为准，
> 不写进本文件、也不为回填它们追加提交（判据见 `docs/agents/implementation.md` §交付）。
> 需要留存的 PR / CI 读数写仓库外的运行报告（`/tmp/<slug>/run-report.md`）。

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
