---
name: worksplice-design-system
description: Apply the worksplice design system — a local-first encrypted multi-agent collaboration workspace (three-column AppShell, macaron direction: warm cream paper + one pastel hue per role, each family in fill/soft/deep/graphic tiers, oklch tokens). Use when designing or building any worksplice screen, component, prototype, deck or marketing surface, or when asked to "use the worksplice design system", "match worksplice", or work in the worksplice repo/UI.
---

# worksplice Design System — Agent Guide

Use this skill to produce UI that is indistinguishable from the source product.
`DESIGN.md` is the human contract; this file is the operational recipe.

## 1. Load the foundations

Always import both token files before writing any CSS:

```html
<link rel="stylesheet" href="../colors_and_type.css">
<link rel="stylesheet" href="../tokens.css">
```

- `colors_and_type.css` — the `--bg/--surface/--fg/--muted/--border/--accent`
  contract plus the full oklch palette (paper family, accent family, four semantic
  statuses) with every family's tiers, avatar tints and the type scale.
- `tokens.css` — spacing, radius, elevation, layout constants, motion, z-index.

**Tiers (ADR-0015).** Each colour family carries four tiers — `fill` (the pastel
itself: fills only, ink is `--on-accent` on top), `soft` (tint: background hint,
never a boundary), `deep` (text: ≥4.5:1) and `graphic` (ring / dot / boundary:
≥3:1). A family's bare name holds the tier that was its main use before the tier
split; see `colors_and_type.css` for which one.

**Product role families.** If you are building *inside the worksplice app* (not a
standalone surface), three more families come from the product extension layer
(`app/globals.css`): `--selected*` (current position: nav / anchor row / tab /
selected chip), `--unread*` (unread badge, search-hit highlight) and `--warn*`
(warning: pending review, banners). `ui_kits/app/app.css` consumes them too, so
loading the kit means loading that layer (or declaring the three families
yourself). `--online-text` is the same shape at baseline.

Never hard-code a hex, rgb, hsl or named color. Derive everything from tokens with
`var(--…)` or `color-mix(in oklch, …)`.

## 2. Pick the right shell

| You are building | Start from |
| --- | --- |
| A new workspace surface | `ui_kits/app/index.html` + `ui_kits/app/app.css` |
| A single isolated screen | the closest file in `screens/` |
| A token/component review | a card in `preview/` |
| A new component in-app | copy the matching class block from `ui_kits/app/spec.css` |

The applied kit `ui_kits/app/` is the canonical assembly: AppShell → rail → main
(header + view + composer) → dock, plus modals and the command palette.

## 3. Structural rules (non-negotiable)

1. **AppShell**: `display:flex; height:100dvh; overflow:hidden`. Three columns:
   rail `252px` (`--panel`, right hairline), main `flex:1` (`--bg`), dock `380px`
   (`--surface`, left hairline, hidden by default).
2. **Centered content**: message stream and search results sit in a
   `max-width: var(--stream-max)` (880px) column, centered. Channel/thread message
   prose alone additionally caps at `68ch`; do not apply this cap to the stream or composer.
3. **Board**: columns are fixed `236px`, laid out in a horizontally scrolling row.
   Never stretch columns to fill.
4. **Density**: general UI remains 13px body with `--sp-3/--sp-4` (6–8px) row
   padding; buttons remain `--control-h` 32px. The local ADR-0016 exception is only
   channel/thread message prose: 14px and `68ch` max measure; channel rows use 12px
   vertical padding, 8px paragraph spacing and 1.65 line-height; thread rows use
   16px, 12px and 1.7. Keep global tokens unchanged. Inline code stays 12px with
   no independent line-height; fenced React `CodeBlock` stays 12px / 1.62. Keep
   its render path and horizontal scrolling unchanged. Do not apply the prose-size
   exception to the stream/composer, agent-session transcript or `.log-row .msg`
   activity summaries.
5. **Responsive**: `≤1080px` dock → 340px. `≤900px` app becomes a column, a sticky
   topbar appears, rail + dock become off-canvas drawers with a scrim, board stacks
   to a single full-width column.

## 4. Component recipes

- **Primary button**: `.btn.btn-primary` — `--accent` fill (fill tier), ink
  `--on-accent`; hover swaps to `--accent-hover` and keeps the same ink (swap both
  channels).
- **Secondary**: `.btn` — `--surface`, `--border-strong`; hover fills `--fg-soft`
  and moves the border to `--faint`.
- **Icon button**: `.icon-btn` 30px, transparent border; hover → `--surface` fill +
  `--border`; active → `.is-on` with `--accent-soft` fill / `--accent-deep` text.
- **Nav row**: `.nav-row`; active = `--surface` fill + inset hairline + 2px
  `--selected-graphic` `::before` bar + `--selected-deep` mono `#`.
- **Presence**: `.presence.online|working|error|offline`; `working` pulses.
- **Avatar**: `.avatar` square tile (`--r-sm`-ish 7px), tints `.av-0…--av-4`.
- **Tag / count**: `--r-pill` or `4px`, mono 9.5–10.5px; agent tags and mentions use
  `--accent-soft` + `--accent-deep`; a tab's count turns `--selected-deep`.
- **Card**: `.card` — `--surface`, `--border`, `--r-md`; hover strengthens border and
  adds `--shadow-card`; dragging `opacity:.4`; drop target `--accent-graphic`, invalid
  `--error`.
- **Modal**: `.overlay` (scrim + blur) + `.modal` (`--r-xl`, `--shadow-pop`); fields
  `.input/.textarea/.select` with a `--accent-graphic` focus ring.
- **Toast**: dark `--fg` fill, 7px online dot; `.err` uses `--error-fill` with
  `--on-accent` ink.

## 5. Interaction-state contract

For every hover / focus / active state, define fg and bg together and **never reduce
contrast**:

- Move surface lightness up (e.g. `--surface` fill), never text toward `--muted`.
- Text targets ≥ 4.5:1 (use the family's `deep` tier); large text and icons ≥ 3:1
  (use `graphic`). A `fill` tier is **only** ever paired with `--on-accent` — never
  put it on paper as text, a ring or a status dot.
- Every focusable element: `:focus-visible { outline:2px solid var(--accent-graphic);
  outline-offset:2px }`. Fields/composer use the `0 0 0 3px var(--accent-soft)` ring.
- Disabled is the only state allowed to drop contrast (`.btn:disabled{opacity:.45}`).
- Honor `prefers-reduced-motion: reduce` for every animation.

## 6. Copy & locale

- UI locale is **Simplified Chinese**. Keep brand names (`worksplice`), code, model
  ids and identifiers as-is.
- Buttons are verb-first (新建频道 / 创建任务 / 推进至进行中).
- Counts and ids are explicit and mono: `#3`, `37 处`, `1.2M`, `session-0412.json`.
- Agents speak in first person; humans address agents with `@`.

## 7. Anti-patterns

No hex/gradients, no circular avatars, no pill cards, no resting shadows, no emoji in
chrome, no low-contrast hover, no second primary CTA for one action, no webfont that
cannot render CJK, no colour that is not a role family, no renaming the
`--bg/--surface/--fg/--muted/--border/--accent` tokens (existing components depend on
them), no renaming the tier suffixes `-fill / -soft / -deep / -graphic` (ADR-0015's
tier vocabulary), and no reviving the retired `--accent-line` — one hue per role,
boundaries take that family's `graphic` tier.

## 8. Verify before shipping

- [ ] Both token files imported (plus the product extension layer when building
      in-app); no raw color literals.
- [ ] Three-column shell correct; centered stream at 880px; board at 236px columns.
- [ ] Hover/focus/active pairs pass contrast; focus-visible ring present; every fill
      tier pairs only with `--on-accent`.
- [ ] Responsive collapse at 1080 / 900px implemented.
- [ ] `prefers-reduced-motion` respected.
- [ ] Copy in Simplified Chinese; ids/counts mono.
- [ ] `data-od-id="…"` on regions, headings, CTAs and repeated cards.
- [ ] Channel/thread message prose alone follows ADR-0016; other surfaces and global token values retain their defaults.
