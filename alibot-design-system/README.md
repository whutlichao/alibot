# alibot Design System

A reusable, source-backed design system for **alibot**, a local-first encrypted
multi-agent + human collaboration workspace. Extracted from the Open Design prototype
`alibot-app.html` (project "Web Prototype", `228a9bdf-…`) and packaged so any
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
alibot-app.html        the source monolith (1,890 lines) — kept for exact comparison
                       (`alibot-app.html.artifact.json` is its artifact manifest)
context/
  provenance.md        where every value came from + fidelity notes
  source-context.md    Open Design handoff record
assets/                brand-mark.svg, wordmark.svg, icon-sprite.svg (source vectors)
build/                 icons.svg (compiled sprite) + icons.json (id → viewBox)
fonts/                 README.md only — system stack is intentional; no font files shipped
preview/               focused review cards + index.html manifest
ui_kits/app/           applied interface kit
  app.css              every product component class (the one implementation of each)
  product-ext.css      the product extension layer inside this package: --selected* /
                       --unread* / --warn* (values identical to
                       app/alibot/studio/product-tokens.css — two landing sites of
                       one family; see SKILL.md §1)
  index.html           the canonical assembly: AppShell → rail → main → dock
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
   `preview/spacing-tokens.html`, then `preview/colors-semantic.html`.
2. **Designers/engineers**: read `DESIGN.md`, then import
   `colors_and_type.css` + `tokens.css`. Building inside the app also needs the three
   product role families (`--selected*` / `--unread*` / `--warn*`); this package ships
   its own copy at `ui_kits/app/product-ext.css`, linked as `../ui_kits/app/product-ext.css`
   from `screens/` and `./product-ext.css` from `ui_kits/app/index.html`. Every slice
   loads that in-package file — nothing in this package points outside it, so the
   package can be copied elsewhere and still render. The app declares the same values
   at `app/alibot/studio/product-tokens.css`; those two are two landing sites of one
   family, not a copy and its stale twin. For the assembled interface open
   `ui_kits/app/index.html`; for one surface at a time start at
   `screens/overview.html`.
3. **Agents**: read `SKILL.md`, then open `ui_kits/app/index.html` for the canonical
   assembly and the matching file in `screens/` for the nearest surface.

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
`colors_and_type.css` (+ the product role families, whose two landing sites are the
in-package `ui_kits/app/product-ext.css` and the app's
`app/alibot/studio/product-tokens.css`); every value
is `oklch()` and every family carries the four tiers.

## Fidelity

Every token, component class and copy string was read from `alibot-app.html`.
The brand mark, wordmark and icon set are SVG reconstructions of the inline vector
paths in that file (no binary assets existed in the source). See
`context/provenance.md` for the full account and known gaps.
