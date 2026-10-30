# 验收记录：修频道工具切到「设置」页签时弹窗横向跳动

**票型**：实施票｜**门禁**：G-impl｜**测试档**：窄（改动只落共享样式正本的作用域规则、一枚 Vue 修饰类
与前端用例；不触达 Python、schema、构建配置或跨语言接口）
**Ticket**：#346｜**base**：`4fa3b21`｜**branch**：`issue-346-impl`
**PR body**：`app/specs/issue-346-pr-body.md`
**采集时间**：2026-10-30｜**环境**：macOS Darwin 27.0.0 / Node v25.0.0 / Vitest 4.1.12（jsdom）/
ego-browser 0.6.0.20（Chromium 154.0.8037.44）

> 本文件在**创建 PR 之前**提交，只记当时已得的读数。PR 号与 CI 状态以 GitHub 为准，不写进本文件、
> 也不为回填它们追加提交（判据见 `docs/agents/implementation.md` §交付）。
> 浏览器读数、截图与命令输出留存在仓库外 `/tmp/alibot-346/`（`readings.json` / `red-test.txt` /
> `green-test.txt` / `gate-fast.txt` / `shots/`）。

---

## Status

| 判据（票面 Observable acceptance） | 落点 | 读数 | 结论 |
| --- | --- | --- | --- |
| ① 红→绿浏览器读数：改动前设置页签相对成员页签的 `.modal` x 差 ≠ 0；改动后三页签 x 完全相同 | `#channelToolsOverlay`（`.overlay.channel-tools-overlay`），真实浏览器 8799 | 红：x = 711 / **703.5** / 711（Δ7.5），设置 `clientWidth` 1967；绿：x = 711 / 711 / 711，三页签 `clientWidth` 1952 | ✅ |
| ② 新增 CSS 契约断言在改动前红、改动后绿 | `tests/channel-tools.test.js` | 红：`1 failed \| 23 passed (24)`；绿：`3 passed (3)` / `111 passed (111)` | ✅ |
| ③ 窄档用例全绿 | `modal-shell` / `channel-tools` / `components` | `Test Files 3 passed (3)` / `Tests 111 passed (111)` | ✅ |
| ④ 门禁全绿 | `scripts/check.sh --fast` | `Test Files 25 passed (25)` / `Tests 501 passed (501)`；ruff / format / mypy / pyright 全绿；`门禁全绿` | ✅ |
| ⑤ 双轴 `code-review`（Standards + Spec，起点 `4fa3b21`）已跑，findings 逐条处置 | 见「双轴 review」 | 两轴 findings 与处置见下 | ✅ |
| ⑥ 验收记录在创建 PR 前提交 | 本文件 | 提交在 `issue-346-impl` | ✅ |
| ⑦ 分支已推送、PR 含 `Closes #346` | `issue-346-impl` | 交付步骤；PR 号与 CI 读数写仓库外 `/tmp/alibot-346/run-report.md` | 交付步骤 |
| ⑧ `worker_done` 已发（显式 succeeded、三句摘要、真实元数据） | 本终端 | 交付步骤 | 交付步骤 |

## 红证据

### 契约断言（同一 worktree 的改动前快照）

```text
cd app/frontend
env -u NODE_ENV npx vitest run tests/channel-tools.test.js

 FAIL  tests/channel-tools.test.js > 频道工具弹窗的静态样式契约 > 根遮罩带修饰类，正本按它预留滚动条槽位：设置页签的滚动条不推动居中弹窗（#346）
- ArrayContaining [ "overlay", "channel-tools-overlay" ]
+ [ "overlay" ]
 Test Files  1 failed (1)
      Tests  1 failed | 23 passed (24)
```

原文：`/tmp/alibot-346/red-test.txt`。加修饰类与 CSS 规则后同一批用例转绿（`/tmp/alibot-346/green-test.txt`）。

### 浏览器（改动前源码构建 + 数据目录副本）

工作台：`http://127.0.0.1:8799/studio/v2/`（`#dev2` 频道），数据目录为 `~/.alibot/data` 的副本
（红 `/tmp/alibot-346/data-red`、绿 `/tmp/alibot-346/data-green`），视口 1982×1011。

| 页签 | 红态 `.modal` x | 红态 overlay clientWidth | 绿态 `.modal` x | 绿态 overlay clientWidth |
| --- | --- | --- | --- | --- |
| 成员 | 711 | 1982 | 711 | 1952 |
| 设置 | **703.5**（Δ7.5） | **1967**（15px 滚动条） | 711（Δ0） | 1952 |
| 预算 | 711 | 1982 | 711 | 1952 |

- 红态设置页签：`scrollHeight=2381 > clientHeight=1011`，y 始终 121.3125；绿态三页签
  `getComputedStyle(overlay).scrollbarGutter === "stable both-edges"`，根节点 class 为
  `overlay channel-tools-overlay`。
- 截图：红 `/tmp/alibot-346/shots/red-settings.png`；绿 `/tmp/alibot-346/shots/green-final-settings.png`。
- 对照（副作用）：共享 `.overlay` 的另一张弹窗 `#settingsOverlay` 仍是 `gutter=auto`、
  `clientWidth=1982`、class 只有 `overlay`——作用域规则没有波及既有弹窗。
- 原始读数：`/tmp/alibot-346/readings.json`。

## 改动落点

| 文件 | 改动 |
| --- | --- |
| `app/alibot/studio/app.css` | 模态段新增 `.overlay.channel-tools-overlay { scrollbar-gutter: stable both-edges; }` + 注释；共享 `.overlay` 不动，app.css 不新增 ID 选择器 |
| `app/frontend/src/components/ChannelToolsModal.vue` | `<ModalShell>` 加 `class="channel-tools-overlay"`（Vue 单根属性透传合并到根 `.overlay`；`id="channelToolsOverlay"` 保留） |
| `app/frontend/tests/channel-tools.test.js` | 静态样式契约：挂载后根遮罩同时带 `overlay` + `channel-tools-overlay`；CSS 规则块含 `scrollbar-gutter: stable both-edges` |
| `app/specs/issue-346-acceptance.md` | 本文件 |
| `app/specs/issue-346-pr-body.md` | PR 正文 |

## 命令与读数

```sh
cd app/frontend
env -u NODE_ENV npx vitest run tests/modal-shell.test.js tests/channel-tools.test.js tests/components.test.js
# Test Files  3 passed (3)
# Tests  111 passed (111)

cd ../..
scripts/check.sh --fast
# build: ../alibot/studio/v2/index.html + assets（产物不入库，ADR-0039）
# pytest（--fast 且未指定文件：跳过）
# ruff check: All checks passed!
# ruff format --check: 392 files already formatted
# mypy: Success: no issues found in 138 source files
# pyright: 0 errors, 0 warnings, 0 informations
# Vitest: Test Files 25 passed (25) / Tests 501 passed (501)
# 门禁全绿
```

本票测试档为窄档，不跑全量 `scripts/check.sh`（无 Python / 后端 / schema 改动）；CI 跑全量。

## 双轴 review

两轴各跑一个只读 reviewer 子代理（Standards / Spec 分开，互不抵消），起点 `4fa3b21`。首轮针对中间
实现（ID 选择器版本，后按最终修订重写）跑过一次；改成修饰类后，对最终代码 HEAD `06c69db` 复跑
两轴。以下记最终复跑结果，首轮遗留项一并列出。

### Standards 轴

无硬违规：共享 `.overlay` 未动，修复只落在 `.overlay.channel-tools-overlay` 与 `ChannelToolsModal.vue`
的 class 透传；中文注释符合 `AGENTS.md`；唯一改动的既有行是 import 增 `readStudio`，既有断言未削弱
（review-rules 判断题 9）。四条判断题：

| # | Finding | 类型 | 处置 |
| --- | --- | --- | --- |
| S1 | 新 CSS 文本契约（`readStudio` → `indexOf` → `slice` → `toContain`）与 `components.test.js` 同形，块内同形状再用一次（可能的 Duplicated Code） | 判断 | ⏭ 不修：抽 `cssRule(css, selector)` 要改 `tests/helpers/studio-globals.js` 与至少两个既有测试文件，超出本票 Target 与窄档；另票处理 |
| S2 | 反向断言「共享 `.overlay` 不含 `scrollbar-gutter`」是票面未要求的禁止判据，未来共享层的合法改动会误红（可能的 Speculative Generality） | 判断 | ✅ 已修：删除该断言，断言收敛为最终修订要求的两条 |
| S3 | 文件头注释声明断言落在渲染元素 / 文字 / 请求，新增块断言 CSS 源文本 | 提示 | ⏭ 不改：与 `components.test.js` 的 own 气泡 CSS 契约同一先例，块内已注释说明 jsdom 不做布局 |
| S4 | 同一理由在 CSS 注释、测试注释、提交信息、PR body 重复四份（散文重复，非代码 smell） | 提示 | ⏭ 不改：四份各自在不同阅读场景承担说明作用 |

### Spec 轴

最终复跑无实现缺陷（修饰类经单根透传合并、`id` 保留、两条断言命中正本）；三条交付 / 记录项：

| # | Finding | 处置 |
| --- | --- | --- |
| P1 | PR 正文缺 `Closes #346` | ✅ 已补（PR body 末尾） |
| P2 | 浏览器支持判定未记录（原 issue 要求实施票判定是否渐进增强） | ✅ 已记录（见下方「说明」与 PR body）：不支持的浏览器回退原行为、无新增回归，故不做渐进增强 |
| P3 | 验收文档「双轴 review」段与 Status ⑤ 自相矛盾（占位 vs ✅） | ✅ 本节已填实 |

首轮（中间实现）的其余提示均已被最终修订吸收：ID 选择器 → 修饰类、契约断言收敛为两条、
`modal-shell.test.js` 无重复副本。

## 说明

- **修复只落在这一张遮罩上**（coordinator spec 最终修订）：共享 `.overlay` 全局常驻双侧 gutter 会让
  窄视口下的所有弹窗统一变窄 30px（`docs/agents/review-rules.md` 判断题 1「共享行为不为单票需求而变」）。
- **已知代价**：`both-edges` 在无滚动条态也给这张遮罩两侧各留 15px（成员 / 预算页签 `clientWidth`
  1982 → 1952）。这是修订明确接受的取舍：位置稳定优先于这 30px 宽度。
- **浏览器支持判定**（原 issue 要求实施票判定是否渐进增强）：`scrollbar-gutter` 自 Chrome 94 /
  Firefox 97 / Safari 18.2（2024-12 进入 Baseline）可用。不支持它的浏览器按原行为回退——设置页签
  仍会跳动，但没有新增回归；overlay 滚动条（macOS「按需显示滚动条」/ iOS）环境下该属性本就不
  生效，而那种环境也没有 15px 的占宽位移。因此不另做渐进增强。
- **浏览器验收的隔离**：使用 `~/.alibot/data` 的副本与独立端口 8799，未写用户正在使用的 8788 数据；
  全程未点保存 / 归档 / 删除。
- **构建产物未入库**（ADR-0039）：`scripts/check.sh --fast` 在门禁头部构建 `app/alibot/studio/v2/`，
  工作区保持只有源码改动。
- **环境准备**：新 worktree 把 `.venv` 指向仓库根；`npm ci --include=dev` 装前端 devDependencies
  （宿主 `NODE_ENV=production` 会跳过它们）。
