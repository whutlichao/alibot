# Source Project Context

This design-system workspace was created from an existing Open Design project. Treat the copied project files as the primary source evidence for the generated design system.

## Source project

- Source project id: 228a9bdf-5a68-4d36-a153-9771522ea041
- Source project name: Web Prototype
- New design-system project id: d77545d0-72d5-4fb2-8ce2-3f846961fb47
- New design-system id: user:web-prototype-design-system
- Source skill id: (none)
- Source design system id: (none)

## Source metadata

```json
{
  "kind": "prototype",
  "nameSource": "prompt",
  "linkedDirs": [
    "/Users/apple/workspace/worksplice"
  ],
  "examplePrompt": true,
  "examplePromptTitle": "Wireframe Sketch",
  "examplePromptBrief": {
    "artifact_type": "lo-fi wireframe",
    "audience": "product team",
    "fidelity": "wireframe"
  }
}
```

## Copied files

- worksplice-app.html

## Skipped files

- (none)

## Generation contract

- Read this file before editing design-system outputs.
- Read the copied files directly from the project workspace; they are source evidence, not generated design-system output.
- Preserve high-signal assets, source examples, UI surfaces, copy, tokens, typography, and interaction patterns from the copied project.
- Generate a reusable Open Design design-system package in this same project: DESIGN.md, README.md, SKILL.md, colors_and_type.css, context/provenance, focused preview cards, preserved assets/build/fonts when available, and ui_kits/app/.
- Before final response, run `"$OD_NODE_BIN" "$OD_BIN" tools connectors design-system-package-audit --path . --fail-on-warnings` and fix every actionable issue.
