# worksplice Design System

> Category: Project Design System
> Surface: web
> Direction: `macaron` (warm cream paper + deep plum ink + one pastel per role,
> four tiers per family) — colour re-decided by ADR-0015; the form half of
> `modern-minimal` stays in force
> Source: Open Design project "Web Prototype" (228a9bdf-5a68-4d36-a153-9771522ea041),
> copied file `worksplice-app.html` (1,865 lines).

worksplice is a local-first, encrypted multi-agent + human collaboration workspace.
Humans and Agents share Teams / Channels and cooperate over Work Objects (messages,
tasks). The interface is a **three-column Next.js application shell** — a persistent
workspace rail on the left, the channel main column in the center, and a contextual
detail dock on the right — with modals and a command palette layered on top.

This document is the human-facing contract. `colors_and_type.css` is the machine
source for color + type; `tokens.css` adds spacing / radius / elevation; `SKILL.md`
tells an agent how to compose new surfaces with it.

---

## 1. Visual Theme & Atmosphere

A calm, legible operator console for human–agent teamwork. The mood is **technical
but warm**: a warm cream paper canvas, deep plum ink, hairline separators, and a
pastel hue per role — action is macaron pink, position is lavender, attention is
lemon, warning is peach, and the four presence states keep a readable mid-tone of
their own hue. The default density remains 13px body and 6–8px vertical rhythm
on non-message surfaces. Channel and thread message prose uses the local readability
exception in ADR-0016 (14px, 68ch maximum measure, roomier vertical rhythm; thread
roomier still), without changing global tokens. The layered surfaces
(`--bg` → `--panel` → `--surface`) create depth without shadow noise.

The signature is **status made visible**: agents carry a live presence dot
(online / working / error / offline), tasks carry a state-machine color, and both
appear consistently in the rail, the message stream, cards, and the dock. Nothing is
decorative; every hue means something.

Restraint rules observed in the source:

- One hue per role, and four tiers per family: `fill` (the pastel; fills only, ink is
  `--on-accent`), `soft` (tint), `deep` (text, ≥4.5:1), `graphic` (ring / dot /
  boundary, ≥3:1). Never put a `fill` on paper as text or as a status dot.
- Radius is small and consistent (6/8/12/16). No pill-shaped cards; pills only for
  counts, tags, status and reaction chips.
- Shadows are hairline and directional, used only for popovers, modals and the
  message hover toolbar — never on resting content.
- Monospace is a first-class voice: every identifier, timestamp, count, token metric,
  key/value row and section label is mono with `tabular-nums`.

## 2. Color

All color is authored in `oklch()` in `colors_and_type.css`. Do not introduce hex.

### Surfaces & text (the load-bearing `--bg --surface --fg --muted --border --accent` contract)

| Token | Value | Role |
| --- | --- | --- |
| `--bg` | `oklch(98% 0.011 85)` | page canvas behind the main column (warm cream) |
| `--surface` | `oklch(99.4% 0.006 85)` | cards, panels, header chrome, popovers |
| `--panel` | `oklch(95.7% 0.018 85)` | rail, board columns, stat wells, pin strip |
| `--panel-2` | `oklch(93.1% 0.022 84)` | nested wells, inactive tags, code chips |
| `--fg` | `oklch(33% 0.045 300)` | primary text (deep plum), active tab underline, toolbar fill |
| `--muted` | `oklch(46% 0.030 300)` | secondary text, inactive nav / tab labels |
| `--faint` | `oklch(52% 0.024 300)` | tertiary text, meta, placeholders, scrollbar |
| `--border` | `oklch(90% 0.013 86)` | hairline separators |
| `--border-strong` | `oklch(78% 0.020 86)` | inputs, composer, interactive borders |
| `--on-accent` | `var(--fg)` | the ink that sits on every `fill` tier |

Families are organised by **tier** — `fill` (pastel: fills only, `--on-accent` on top),
`soft` (`color-mix(<family> N%, transparent)`; the ratios are 22 / 21 / 36 / 24 for
accent / selected / unread / warn and 20% for the four presence families), `deep`
(text) and `graphic` (ring / dot / boundary). Each family's bare name holds the tier
that was its main use before the split:

| Family | Role | fill | deep | graphic |
| --- | --- | --- | --- | --- |
| `--accent` | action: primary button, brand mark, composer send, agent identity | `oklch(85.7% 0.086 356.8)` | `oklch(53% 0.086 356.3)` | `oklch(62.6% 0.086 356.7)` |
| `--selected` | current position: nav, anchor row, tab, selected chip | `oklch(84.9% 0.081 299.9)` | `oklch(52.4% 0.081 299.8)` | `oklch(62.4% 0.081 300.5)` |
| `--unread` | attention: unread badge, search-hit highlight | `oklch(91% 0.100 94.8)` | `oklch(51.8% 0.100 94.5)` | `oklch(61.6% 0.099 94.7)` |
| `--warn` | warning: pending review, banners | `oklch(86.3% 0.084 52.2)` | `oklch(52.6% 0.084 51.9)` | `oklch(62.1% 0.083 51.6)` |

`--accent-hover` is `oklch(79.4% 0.104 356.6)` (a fill, one step deeper).

### Semantic status

The four presence hues are the exception to "the bare name is the fill tier": their
bare names hold the `graphic` tier (dots, bars, markers), because that was their main
use before the split. `--error` holds the `deep` tier — text, stroke and dot share it.

| Token | Value | Tier | Meaning |
| --- | --- | --- | --- |
| `--online` | `oklch(60.1% 0.108 159.4)` | graphic | agent idle / granted / success |
| `--working` | `oklch(61.9% 0.110 74.2)` | graphic | agent busy / pending review / highlight |
| `--error` | `oklch(53.2% 0.128 25.9)` | deep | failure, destructive |
| `--offline` | `oklch(61.8% 0.026 304.2)` | graphic | agent offline / closed |
| `--online-fill` | `oklch(80.8% 0.108 159.6)` | fill | success badge / banner |
| `--working-fill` | `oklch(84.3% 0.110 74.6)` | fill | busy badge / banner |
| `--error-fill` | `oklch(74.5% 0.127 25.8)` | fill | failure badge / banner / toast err |
| `--offline-fill` | `oklch(82.5% 0.026 303.4)` | fill | offline badge |
| `--online-text` / `--working-text` | `oklch(50.6% 0.107 159.9)` / `oklch(52.3% 0.110 75.2)` | deep | status hues as text |

Task status maps directly: `todo → --faint`, `in_progress → --accent-graphic`,
`in_review → --working`, `done → --online`, `closed → --offline`. A task's *badge
background* is the fill tier instead (`--accent` / `--working-fill` / `--online-fill`
/ `--offline-fill`, with `--panel-2` for the neutral state).

### Avatar palette

Five deterministic pastel tints keyed by member index (`--av-0…--av-4`): lavender,
mint, peach, lemon, and a lotus grey used for "you". Every tint clears 7.1:1 against
the deep plum ink. Avatar tiles are 7px-radius squares, not circles — a deliberate
differentiator from chat products.

## 3. Typography

One system sans for everything, one monospace for anything numeric or structural.
No display serif, no third family. The system stack is deliberate — it renders the
Chinese UI (`PingFang SC`) and Latin identically across platforms, which a webfont
would not without shipping megabytes.

```
--font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif;
--mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
```

| Role | Size / weight | Usage |
| --- | --- | --- |
| Channel title (h1) | 16px / 680 | `#频道名`, `@成员名` in the header |
| Dock / thread title | 14–14.5px / 680 | panel subjects |
| Body | 13px / 400–650 | nav, buttons and general UI (except channel/thread message prose) |
| Channel/thread message prose | 14px / 400–650 | channel messages and thread replies; local exception, see ADR-0016 |
| Caption / hint | 11.5px | card meta, field hints, helper text |
| Small | 12px | chips, list rows, secondary copy |
| Mono meta | 10.5px | timestamps, counts, `kbd` |
| Mono label | 9.5px / 600 | uppercase group + section labels (`letter-spacing .1em`) |
| Stat numeral | 17px / 700 mono | `--stat` metrics, `tabular-nums` |

Headings use `letter-spacing: -0.02em`; uppercase mono labels use `+0.1em`.

## 4. Spacing, Radius & Elevation

Truth lives in `tokens.css`. The base unit is **4px** with a 2px half-step.

- Spacing: `2 · 4 · 6 · 8 · 12 · 14 · 16 · 18 · 20 · 24 · 30 · 56` (`--sp-1…--sp-12`)
- Channel message rows use 12px vertical padding and 8px paragraph spacing; thread replies use 16px and 12px. These are local ADR-0016 exceptions; the global scale values do not change.
- Control heights: `--control-h: 32px`, `--control-h-sm: 27px`, `--tap-min: 44px`
- Radii: `--r-sm 6` (rows, chips), `--r-md 8` (buttons, inputs, cards),
  `--r-lg 12` (composer, modal, columns), `--r-xl 16` (modal), `--r-pill 99`
- Elevation: `--shadow-composer` (resting input), `--shadow-card` (card hover),
  `--shadow-pop` (popover, modal, drawers)

Layout constants: `--rail-w 252px`, `--dock-w 380px` (`340px` under 1080px),
`--stream-max 880px`, `--board-col-w 236px`.

## 5. Layout & Composition

```
┌──────────────┬─────────────────────────────────────┬──────────────────┐
│ workspace    │  channel header                     │  detail dock     │
│ rail         │  (title · tools · desc · pin · tabs)│  (agent | thread)│
│ 252px        ├─────────────────────────────────────┤  380px           │
│ ┌──────────┐ │  messages stream  (max 880px)        │  ┌────────────┐  │
│ │ brand    │ │  …                                   │  │ identity   │  │
│ │ search   │ │                                      │  │ tabs       │  │
│ │ actions  │ │                                      │  │ overview / │  │
│ │ channels │ │                                      │  │ runtime /  │  │
│ │ DMs      │ │  task board  |  search view          │  │ activity   │  │
│ │ agents   │ ├─────────────────────────────────────┤  └────────────┘  │
│ │ me       │ │  composer (messages only)            │                  │
└──────────────┴─────────────────────────────────────┴──────────────────┘
```

- **AppShell**: `display:flex; height:100dvh; overflow:hidden`.
- **Rail**: fixed 252px, `--panel`, right hairline; scrolls independently.
- **Main**: flexible, `--bg`; header (`--surface`) + view + composer stack.
- **Dock**: fixed 380px, `--surface`, left hairline; hidden by default and toggled.
- **Messages / search**: content is centered in a `--stream-max` (880px) column. Channel/thread message prose additionally caps at `68ch`; the stream and composer keep the 880px width.
- **Board**: columns are fixed 236px and lay out horizontally with overflow scroll;
  no stretch-to-fill.

Responsive breakpoints observed:

- `≤1080px` — dock narrows to 340px.
- `≤900px` — `app` becomes a column; a sticky mobile topbar appears; the rail and the
  dock become off-canvas drawers (transform + scrim); the board collapses to a single
  full-width column stack.

## 6. Components

Full visual specimens live in `preview/`; the applied kit is `ui_kits/app/`.

- **Buttons** — `.btn` (32px, `--surface`, `--border-strong`), `.btn-primary`
  (`--accent` fill with `--on-accent` ink), `.btn-ghost`, `.btn-danger`, `.btn-sm` (27px).
- **Icon button** — `.icon-btn` 30px, transparent border, `--muted`; hover fills
  `--surface` and strengthens the border; `.is-on` uses `--accent-soft` fill with
  `--accent-deep` text.
- **Navigation row** — `.nav-row`: left `--selected-graphic` bar `::before` when active,
  `--surface` fill + inset hairline, mono `#` marker turns `--selected-deep`.
- **Search field** — `.search-btn` (rail) and `.search-field` (search view); both
  reveal an `--accent-graphic` ring on `:focus-within`.
- **Presence dot** — `.presence.{online|working|error|offline}`; `working` pulses
  (`pulse 1.5s`), disabled under `prefers-reduced-motion`.
- **Avatar** — `.avatar` square tiles with `.sm` / `.lg`; five tints.
- **Tabs** — underline tabs (`.tab`, `.dock-tab`) with mono count that turns
  `--selected-deep`.
- **Pin strip** — `.pin-strip`, `--panel` callout with an `--accent-deep` pin icon and link.
- **Message** — `.msg` with hover fill, absolutely-positioned `.msg-tools` toolbar,
  `.msg-author.is-agent` `--accent-deep`, `.msg-tag` "Agent" capsule, mention + code
  inline, `.task-chip` linked work object with an `--accent-graphic` left rule,
  `.reactions` pills (`.reaction.mine` = `--selected` family). Channel/thread prose
  is 14px with a 68ch maximum measure; channel/thread line-height is 1.65/1.7,
  and thread rows and paragraphs use the larger local rhythm in ADR-0016. Author
  remains clear; time and sequence remain visible
  at 10.5px mono / `--faint`. Inline code (12px, no independent line-height) and
  React fenced `CodeBlock` (12px / 1.62) retain their dedicated styles; the
  `.msg-text pre` 11.5px / 1.6 rule is not the fenced-code render path.
- **Composer** — `.composer-box` with an `--accent-graphic` focus ring, auto-growing
  textarea, attach / mention / as-task icon buttons, hint, `.composer-send` `--accent` square.
- **Kanban** — `.col` (236px, `--panel`), `.card` draggable with hover lift, drag
  `.drag-over` (`--accent-graphic` boundary) / `.invalid-over` (`--error`) affordances,
  `.drop-hint` dashed empty slot, `.seg` board/list segmented control, `.filter-chip`
  (`.is-on` = `--selected` family).
- **Dock panels** — `.d-sec` + `.d-sec-title` sections, `.kv` key/value rows with
  dotted leaders, `.meter` progress (`--accent-graphic`, `.warn` → `--warn-graphic`),
  `.stat-grid` 2×2 metrics, `.log-row` level tags (`.lv` = `-soft` ground + `deep` text).
- **Thread** — `.tt-summary` sticky header, `.tt-status` state pill, `.assignee`
  pill, `.tt-actions` chip rows, `.tt-log` timeline with `ok` (`--online`) /
  `warn` (`--warn-graphic`) / `err` (`--error`) / `is-now` (`--selected-graphic`) dots,
  sticky `.tt-reply` composer.
- **Overlay / Modal** — `.overlay` (scrim + blur), `.modal` (`--r-xl`, pop shadow),
  `.field` + `.input/.textarea/.select`, `.member-opt` pick pills, `.radio-card`
  (`.is-on` = `--selected` family).
- **Command palette** — `.cmd` 520px, grouped `.cmd-group` labels, `.cmd-item` rows
  with hover `--accent-soft` fill / `--accent-deep` text.
- **Toast** — `.toast` (dark `--fg` fill, online dot) and `.toast.err`
  (`--error-fill` with `--on-accent` ink).

## 7. Motion & Interaction

- Transitions are short: `background/border/color .1–.12s ease`, `transform .2s ease`
  for drawers, `opacity .12s` for the message toolbar.
- Hover never lowers contrast. Surfaces move **up** in lightness (`--surface` fill,
  `--border` → `--border-strong`) while text moves toward `--fg`. Never `--muted`.
- Focus is always explicit: `:focus-visible { outline:2px solid var(--accent-graphic);
  outline-offset:2px }`; fields and the composer use a `0 0 0 3px var(--accent-soft)`
  ring instead of an outline.
- Loading: the only spinner-free affordance in-source is the pulsing presence dot;
  skeletons are a known gap (see anti-patterns) — reuse `--panel-2` shimmer blocks.
- The dragging card drops to `opacity:.4`; drop targets signal legality by color.
- All keyframes (`pulse`, `toast-in`) and transitions honor
  `prefers-reduced-motion: reduce`.

## 8. Voice & Brand

- Wordmark is lowercase `worksplice`; the product is described by the postmark
  `LOCAL · E2EE` set in mono uppercase under the name.
- UI locale is Simplified Chinese. Copy is direct, operational, and second-person
  neutral; agent copy is first-person ("我已扫描出 37 处裸色值").
- Agents are people-adjacent: named (`Susan`, `Atlas`, `Pixel`), role-tagged
  (`秘书 · 正在干活`), and spoken *to* with `@`.
- Counts and identifiers are always explicit and mono: `#3`, `37 处`, `1.2M`,
  `session-0412.json`.
- Messages are described as permanent: "消息不可编辑、不可删除，是永久记录。"
- Buttons are verb-first: 新建频道 / 新建任务 / 创建 Agent / 添加提醒 / 推进至进行中.

## 9. Anti-patterns

Do **not**:

- Introduce hex or RGB colors, gradients, or a hue that is not one of the role
  families. Everything is `oklch()` derived from the tokens above.
- Round avatars into circles, or make cards into pills — squares/soft rectangles only.
- Add drop shadows to resting content; shadows are reserved for layered surfaces.
- Use color without meaning: each hue is a role family (action / selected / unread /
  warn / presence), never decoration.
- Use a `fill` tier as text, a focus ring or a status dot — it is only ever paired
  with `--on-accent` on top of it.
- Put emoji or decorative illustration into functional chrome (the ☕ in one
  tea-room message is content, not UI).
- Lower text contrast on hover, or gray out labels on interaction.
- Reproduce the message hover toolbar outside a hover/focus context — it is
  chrome-light by design.
- Inflate the 13px body on non-message surfaces: this is an operator console, not a marketing page. Channel/thread message prose is the scoped 14px exception in ADR-0016.
- Ship a second primary CTA for one action in a viewport.
- Break the token contract: existing components depend on
  `--bg/--surface/--fg/--muted/--border/--accent` names. Renaming them re-breaks
  the entire app.
- Use `letter-spacing`-loosened body text or webfonts that cannot render CJK.

### Known gaps (honest placeholders)

The source prototype implements no skeletons/spinners for async content, no dark
theme, and no image binaries (avatars are initial tiles). No logo/icon font files
exist in the source; the brand mark and icon sprite in `assets/` and `build/` were
reconstructed as SVG from the inline vector paths found in the source HTML.
