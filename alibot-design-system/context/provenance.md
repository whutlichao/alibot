# Provenance

How this design system was derived, what is exact, and what is reconstructed.

## Sources

| Source | Role |
| --- | --- |
| `worksplice-app.html` (1,865 lines) | **Primary evidence.** Copied from Open Design project "Web Prototype" (`228a9bdf-5a68-4d36-a153-9771522ea041`). |
| `context/source-context.md` | Open Design handoff record (source id, metadata, generation contract). |
| `worksplice-app.html.artifact.json` | Artifact manifest: kind `html`, entry `worksplice-app.html`. |

No reference images, browser snapshots, sketches, font files or binary assets were
present in the copied project. The single HTML file is complete and self-contained
(inline `<style>` + inline `<script>`), so it carried the entire visual language.

## Method

1. Read `context/source-context.md`, then the full `worksplice-app.html`.
2. Extracted the `:root` token block **verbatim** — it already declares the
   `modern-minimal` direction and the `--bg/--surface/--fg/--muted/--border/--accent`
   contract the product's Next.js components depend on. Copied unchanged into
   `colors_and_type.css`.
3. Walked every CSS rule to catalogue components, states and layout constants
   (rail 252px, dock 380px/340px, stream 880px, board columns 236px).
4. Read the JS to capture real copy, seed data, status vocabulary and interaction
   semantics (task state machine `todo → in_progress → in_review → done → closed`,
   presence states, toast strings, command palette entries).
5. Reproduced each surface as a self-contained, working slice in `screens/`, and
   assembled the reusable kit in `ui_kits/app/`.
6. Reconstructed vector assets from the inline SVG paths in the source.

## What is exact (read directly from the source)

- All color tokens and their oklch values.
- Font stacks (`--font`, `--mono`) and every font size / weight / letter-spacing.
- Radii (6/8/12/16), spacing rhythm, control heights.
- All component class names and their states, including drag `drag-over` /
  `invalid-over`, `.is-on`, `.is-active`, disabled rules.
- All user-visible copy in Simplified Chinese, agent/member names, channel names,
  task titles, log lines, runtime metrics and toast messages.
- Responsive breakpoints (1080 / 900px) and reduced-motion handling.

## What is reconstructed (source had no binaries)

- `assets/brand-mark.svg` and `assets/wordmark.svg` — redrawn from the inline brand
  SVG in the rail header (three rules + accent node for the mark; the wordmark pairs
  the lowercase name with the mono `LOCAL · E2EE` postmark).
- `assets/icon-sprite.svg` / `build/icons.svg` + `build/icons.json` — the icon set is
  the collection of inline stroke paths used across the app (pin, plus, search, task,
  user, members, settings, bell, panel, attach, mention, send). Stroke width 1.9–2.4,
  `viewBox 0 0 24 24`, `stroke="currentColor"`, no fill.
- `fonts/` is intentionally empty: the system stack is a deliberate choice (it
  renders CJK natively). No font files exist in the source to preserve.

## Known gaps (honest placeholders)

- No dark theme exists in the source; the token file documents only the light surface.
- No skeleton / loading components exist in the source. `DESIGN.md` §7 marks this as
  a gap and prescribes `--panel-2` shimmer blocks for future work.
- No image or avatar binaries; the source renders initial tiles with tinted
  backgrounds. This package preserves that behavior and ships no avatar images.
- `screens/*.html` are faithful slices, not the original monolith; the monolith is
  preserved in place at the project root for exact comparison.
