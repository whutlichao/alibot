## Summary

Studio 频道头的**协作模式 pill**（team 模式 → `队长制 · Ada`，自主 → `自主`，私信 / 读不到配置 → 隐藏）
本来在功能提交里已经齐了；这一次修的是它的**隐藏态表达**，以及由此暴露的一条渲染路径异常。

```diff
 renderChannelModePill()                      # app/alibot/studio/app.js
   pill.hidden = !badge
   pill.textContent = badge ? badge.text : ''
-  if (badge) {
-    pill.setAttribute('title', badge.label)          # 显示态：无障碍名 =「协作模式：当前值」
-    pill.setAttribute('aria-label', badge.label)
-  } else {
-    pill.removeAttribute('title')                    # 隐藏态：摘掉上一频道的标签
-    pill.removeAttribute('aria-label')
-  }
+  pill.setAttribute('title', badge ? badge.label : '')        # 隐藏态写空串 = 没有名字 / 没有提示
+  pill.setAttribute('aria-label', badge ? badge.label : '')   # 只用 setAttribute，不依赖可选方法
```

为什么这四行会牵出五个文件真红：`removeAttribute` 在真实 DOM 上有，但 5 个既有用例文件各自的 DOM 替身
（`FakeNode`）只实现 `setAttribute` / `getAttribute`。缺口落在**渲染路径**上，于是每个「载入工作台 / 切 target」
的用例都抛 `TypeError`；异常又被 `loadWorkspace()` 的 `catch` 当成「后端不可达」吞掉，顺手排上重连定时器 ——
用例全绿，进程却永不退出。

```text
deliver()  ──►  loadWorkspace({silent:true})  ──►  loadActiveChannelMode()  ──►  renderChannelModePill()
                          │                                                        │
                          └── catch(err) ──► scheduleReconnect() ◄──────────────── TypeError
                                                   │
                                                   └── setTimeout(1.5s) ──► loadWorkspace() ──► …（永不收敛）
```

改动面（都在前端，无后端 / schema）：

```text
app/
├── alibot/studio/app.js                       # renderChannelModePill() 隐藏态：写空串，不再 removeAttribute
├── tests/channel_config_ui.test.cjs           # 替身收回 removeAttribute；「不残留」用例改断言「读不到名字」
└── specs/
    ├── fix-channel-mode-badge-acceptance.md   # 判据 → 落点 → 命令与读数（含 5 文件红证据 / 根因 / review）
    └── fix-channel-mode-badge-evidence/*.png  # 13 张浏览器证据（三档视口 × team / 自主 / 私信 / 保存后）
```

**没有**改：`GET /api/channels` 载荷、端点、schema、迁移；`.chan-title span` 的 ellipsis；
移动端顶栏 `#mtTitle` 的 `#名` / `@名` 格式；任务线程的 `#ttTeamNotice`；另外 5 个用例文件（一行未动）。

## Evidence

**Before**（隔离副本 `/tmp/cmb-red2` = 功能提交 `8ac6093` + 上一次 attempt 的隐藏态写法，
每个文件一条命令、90s 上限）：

```text
channel_budget_ui.test.cjs       tests 11 / pass 1  / fail 10
channel_resources_ui.test.cjs    tests 16 / pass 1  / fail 15
run_panel_ui.test.cjs            tests 12 / pass 1  / fail 11
search_ui.test.cjs               tests 16 / pass 13 / fail 3
mobile_shell.test.cjs            tests 9  / pass 8  / fail 1
i18n_ui.test.cjs                 53 条全部打印 pass，进程永不退出（40s 上限 kill，exit 124）
node --test app/tests/*.test.cjs 卡在 i18n_ui 上，永不结束
```

**After**（本分支，同一条命令口径）：

```text
# 13 个用例文件逐个跑（各自 90s 上限）
app/tests/*.test.cjs → 188 pass / 0 fail（13 个文件全 exit 0；i18n_ui 5s 退出）
# 全量 glob（240s 上限）
ℹ tests 188 / ℹ pass 188 / ℹ fail 0 / ℹ duration_ms 3907.94
# 门禁（600s 上限）
scripts/check.sh --fast → ruff All checks passed / mypy Success / pyright 0 errors / node 188 pass → 门禁全绿
```

浏览器证据（ego-browser 0.6.0.12 + 真实 uvicorn，隔离 `--data-dir=/tmp/alibot-cmb-data`、端口 8823，
种子全部走真实 HTTP 写路径；13 张图全部在**最终字节**上重拍）：

| 场景 | 读数 | 图 |
| --- | --- | --- |
| team 频道（队长 Ada） | `text 队长制 · Ada`、`aria-label 协作模式：队长制 · Ada`、`pillLeft 394 > titleRight 382`、`pillInsideChanTitle false` | `team-channel-{1440,1024,430}.png` |
| autonomous 频道 | `text 自主`、`aria-label 协作模式：自主` | `autonomous-channel-1440.png`、`channel-header-autonomous-{1440,1024,430}.png` |
| 私信 | `text ""`、`hidden true`、`title/aria-label ""`、`pillDisplay none` | `dm-hidden-{1440,1024,430}.png` |
| 设置面 team → autonomous 保存 | `AFTER-SAVE text 自主`、`noReloadMark "kept"`、`navigationEntries 1`、`overlayStillOpen true` | `settings-saved-autonomous-{1440,1024,430}.png` |

服务端字节自证（浏览器拿到的 = 分支上的）：

```sh
$ curl -s http://127.0.0.1:8823/studio/app.js | md5 -q ; md5 -q app/alibot/studio/app.js
b40e1c2634688eba726a33ff5ec515cd
b40e1c2634688eba726a33ff5ec515cd
```

**不跑全量 pytest 与迁移链专项的理由**：`git diff --name-only c606b4a | grep -c '\.py$'` = 0，本分支没有一个
Python 文件，不动 API / 数据库 / schema，因此不在本票门禁；门禁 = `scripts/check.sh --fast` + `node --test app/tests/*.test.cjs`。

## Merge Danger

**Door:** two-way —— 纯前端渲染表达 + 本票用例，回滚只需 revert 一次提交；无数据迁移、无端点变更。

**Blast Radius:** low —— 只有频道头那颗 pill 的隐藏态写法变了（属性从「缺失」变成「空串」，对外语义相同：
空 `title` 不弹提示、空 `aria-label` 在 accname 里落到下一层，且 pill 本身 `hidden`）。频道列表、消息流、
任务线程、移动端顶栏均未改动；另外 5 个 UI 用例文件一行未动。
