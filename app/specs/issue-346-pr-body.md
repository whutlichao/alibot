## Summary

```diff
 .overlay {                          /* 共享弹窗壳：不动 */
   display: grid; place-items: start center;
   overflow: auto;
 }
+.overlay.channel-tools-overlay {    /* 只有频道工具弹窗的遮罩 */
+  scrollbar-gutter: stable both-edges;
+}
```

`ChannelToolsModal.vue` 把 `channel-tools-overlay` 透传到 `ModalShell` 的根 `.overlay` 上（Vue 单根
属性透传会合并 class；`id="channelToolsOverlay"` 保留不动）。

频道工具弹窗切到「设置」页签时，长内容把遮罩撑出 15px 垂直滚动条；滚动条占宽使 grid 内容盒变窄，
水平居中的 `.modal` 左移 7.5px，切回成员/预算又跳回。修饰类规则让这张遮罩常驻预留两侧槽位，三个
页签位置一致。共享 `.overlay` 保持不变：常驻双侧 gutter 会让窄视口下的所有弹窗统一变窄 30px
（review-rules 判断题 1「共享行为不为单票需求而变」）。

## Evidence

- **Before:** 真实浏览器（ego-browser 0.6.0.20 / Chromium 154，视口 1982×1011；`~/.alibot/data` 副本 +
  独立端口 8799）。`.modal` x：成员 711 / 设置 **703.5** / 预算 711（Δ 7.5px）；设置页签 overlay
  `clientWidth` 1967（15px 滚动条），`scrollHeight` 2381 > `clientHeight` 1011。
  截图：`/tmp/alibot-346/shots/red-settings.png`
  **After:** 同一工作台重建后 `.modal` x：成员 711 / 设置 711 / 预算 711；三页签 overlay
  `clientWidth` 均为 1952（`gutter: stable both-edges`），y 始终 121.3125。
  截图：`/tmp/alibot-346/shots/green-final-settings.png`
  对照：共享 `.overlay` 的 `#settingsOverlay` 仍是 `gutter: auto`、`clientWidth` 1982。
- 契约断言红→绿：`channel-tools.test.js` 新增用例在加规则前失败（根遮罩缺 `channel-tools-overlay` 类），
  加规则后窄档 3 files / 111 tests 全绿。
- 门禁：`scripts/check.sh --fast` → 25 files / 501 tests 全绿；ruff / format / mypy / pyright 全绿。
  读数与浏览器原始数据：`/tmp/alibot-346/readings.json`、`red-test.txt`、`green-test.txt`、`gate-fast.txt`。
- **浏览器支持与渐进增强**：`scrollbar-gutter` 自 Chrome 94 / Firefox 97 / Safari 18.2（2024-12 进入
  Baseline）可用。不支持它的浏览器按原行为回退（设置页签仍会跳动，但无新增回归）；overlay 滚动条
  环境下该属性本就不生效、也没有横向位移，因此不另做渐进增强。

## Merge Danger

**Door:** two-way

**Blast Radius:** 一张遮罩

单一修饰类只作用于频道工具弹窗的根遮罩；共享 `.overlay` 与其它弹窗的宽度、居中、滚动行为未变
（对照读数见上）。回滚即删一行 CSS 与一个 class 属性。

Closes #346
