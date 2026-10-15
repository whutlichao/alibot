# fonts/

**本包不发布任何字体文件。这是刻意的决定，不是缺失。**

## 为什么

设计系统只用两套字体栈，二者都在 Foundations 里按名字声明：

```css
--font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif;
--mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
```

- **系统栈是有意选择。** UI 语言是简体中文（`SKILL.md` §6），系统 sans 在 macOS /
  Windows / Linux 上都能原生渲染 CJK（`PingFang SC` / `Hiragino Sans GB` /
  `Microsoft YaHei`），不需要为了一个字形集去下载几 MB 的 webfont。
- **`SKILL.md` §7 的反模式**里写着「no webfont that cannot render CJK」。装入一个拉丁
  优先的字体文件，恰好就是这条反模式。
- **源原型里没有任何字体二进制。** `alibot-app.html` 是自包含的（内联 `<style>` +
  内联 `<script>`），只声明字体栈，不引用字体文件。`context/provenance.md` 记录了这一点：
  「`fonts/` is intentionally empty: the system stack is a deliberate choice (it renders
  CJK natively). No font files exist in the source to preserve.」

## 因此

| 情形 | 做法 |
| --- | --- |
| 你在用本包做界面 | 不要在这里找字体；直接用 `var(--font)` / `var(--mono)` |
| 你的运行环境缺某个字形 | 在字体栈**末尾**补该系统字体，不要引入 webfont |
| 你认为必须装箱某个字体 | 那是一次方向变更，先改 `DESIGN.md` §3 与 `SKILL.md` §7，再动文件 |

目录里这个 `README.md` 就是 `README.md` 顶层清单列出的 `fonts/` 条目的真实落点。
