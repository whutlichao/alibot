# worksplice Design System

A reusable, source-backed design system for **worksplice**, a local-first encrypted
multi-agent + human collaboration workspace. Extracted from the Open Design prototype
`worksplice-app.html` (project "Web Prototype", `228a9bdf-…`) and packaged so any
future surface — or an agent — can build on-brand UI without re-deriving tokens.

## What's here

```
DESIGN.md              human-facing contract: theme, color, type, spacing, layout,
                       components, motion, voice, anti-patterns
colors_and_type.css    canonical color + typography tokens (the --bg/--surface/--fg/
                       --muted/--border/--accent contract), oklch() only
tokens.css             spacing, radius, elevation, layout, motion, z-index
SKILL.md               agent-facing: how to compose new surfaces with this system
README.md              this file
context/
  provenance.md        where every value came from + fidelity notes
  source-context.md    Open Design handoff record
assets/                brand-mark.svg, wordmark.svg, icon-sprite.svg (source vectors)
build/                 icons.svg (compiled sprite) + icons.json (id → viewBox)
fonts/                 (empty) — system stack is intentional; no font files shipped
preview/               focused review cards + index.html manifest
ui_kits/app/           applied interface kit (AppShell, rail, stream, board, dock …)
screens/               sliced per-surface reproductions of the source monolith
  channel-view.html    full three-column app shell + messages + composer
  task-board.html      kanban / list task board with drag state machine
  thread-panel.html    right-dock task thread (status, assign, timeline, reply)
  agent-detail.html    right-dock agent overview / runtime / activity
  modals.html          new-channel, new-task, new-agent, reminders, settings, members
  search-command.html  search view + ⌘K command palette
  mobile-shell.html    responsive drawer shell (≤900px)
  workspace-sidebar.html  standalone rail with every nav state
  overview.html        full-page panorama index of all slices
```

## Start here

1. **Reviewers**: open `preview/index.html` — a manifest linking every focused card.
   Best first cards: `preview/colors-primary.html`, `preview/typography-specimens.html`,
   `preview/components-buttons.html`, then `preview/applied-app-shell.html`.
2. **Designers/engineers**: read `DESIGN.md`, then import
   `colors_and_type.css` + `tokens.css`.
3. **Agents**: read `SKILL.md`.

## Core principles

- **One hue per role, tiers per family.** Eight pastel role families carry the meaning —
  action=樱粉 `--accent`, selected=薰衣草 `--selected`, unread=柠檬 `--unread`,
  warn=蜜桃 `--warn`, presence=薄荷 `--online` / 杏 `--working` / 珊瑚 `--error` /
  藕灰 `--offline`. Each family is organised by **tier**: fill (pastel, ink is
  `--on-accent` on top), soft (tint, background hint only), deep (text, ≥4.5:1),
  graphic (ring / dot / boundary, ≥3:1). Color is never decorative.
- **Layered surfaces, not shadows.** Depth comes from `--bg → --panel → --surface`
  plus hairline borders; shadows only for popovers, modals and drawers.
- **Monospace as structure.** Identifiers, timestamps, counts, metrics, key/value
  rows and uppercase section labels are all `--mono` with tabular numerals.
- **The token contract is load-bearing.** Existing components depend on
  `--bg / --surface / --fg / --muted / --border / --accent` by name. Never rename.

## Direction

`macaron` — warm cream paper (`--bg` at hue 85) + deep plum ink (`--fg` at hue 300)
+ multi-hue pastel role families (ADR-0015, superseding ADR-0014's colour section
only). The **form** half of `modern-minimal` stays in force: compact 13px body
density for general UI, small radii (6/8/12/16), hairline dividers, soft shadows,
and first-letter avatar tiles. Channel/thread message prose is a local 14px
exception with line-height 1.65 / 1.7 respectively (ADR-0016); global type and
spacing token values remain unchanged. Colour is authored once in
`colors_and_type.css` (+ the product role families in the app layer); every value
is `oklch()` and every family carries the four tiers.

## Fidelity

Every token, component class and copy string was read from `worksplice-app.html`.
The brand mark, wordmark and icon set are SVG reconstructions of the inline vector
paths in that file (no binary assets existed in the source). See
`context/provenance.md` for the full account and known gaps.
