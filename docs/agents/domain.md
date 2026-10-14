# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root, section **`## Language`**: this repo's domain glossary.
  This repo keeps its glossary in `CONTEXT.md` rather than `GLOSSARY.md`.
  If a root `GLOSSARY.md` is ever added, read that too, and read `GLOSSARY-MAP.md` instead if it exists (it points at one `GLOSSARY.md` per context).
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context repo (this repo):

```text
/
├── CONTEXT.md              ← glossary lives in its `## Language` section
├── docs/
│   ├── agents/             ← this configuration
│   └── adr/                ← architecture decision records
└── alibot-design-system/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`'s `## Language` section. Don't drift to synonyms the glossary explicitly avoids — the `_Avoid_` lines in that file are there for exactly this.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_
