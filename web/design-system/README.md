# Ludex design-system gallery

Component previews for Claude Design (`/design-sync`). Each `components/<name>/index.html` is a
standalone preview whose **first line** is a `<!-- @dsCard group="…" -->` marker (the Design System
pane groups cards by that label). See `docs/web-design-system-design.md` for the full plan.

## Status: Phase 2a — verify-first (2026-06-21)

The one unverified assumption in the staged-vanilla plan is whether `/design-sync` accepts a
**vanilla HTML** component library (vs requiring React/Storybook). These first two components exist
to test exactly that, before building the rest of the gallery:

- `components/field-card/` — the Field tab's cards (clean, fully tokenized). Group **Fields**.
- `components/button/` — the button family (the app has no single button component yet; this is
  where the round-trip will help unify). Group **Actions**.

Both are **self-contained** (an inline `:root` mirrors the tokens each uses + the component CSS is
byte-identical to the live app), so they render standalone in the pane regardless of how it resolves
asset links — a clean test of the format, not of CSS-link resolution. Open either file directly
(`file://`) to eyeball it.

## How to verify (JJ runs this — `/design-sync` is user-triggered)

1. From the repo root, run `/design-sync` and point it at this directory (`web/design-system/`).
2. Create a new design-system project (or pick one) when prompted.
3. Confirm the two cards render in claude.ai/design under **Fields** / **Actions**.

If they render → the vanilla path works; Cody builds out the rest of the gallery (every component,
shared tokens). If `/design-sync` rejects vanilla HTML → we re-decide with JJ (Option B, a parallel
React/Storybook design system) — but the Phase-1 `tokens.css` foundation holds either way.

## TODO for the full gallery (after the format is confirmed)

- **Single source**, not the inline-mirror used here for verification: resolve whether the pane can
  link an uploaded `tokens.css` (so a token change flows to every card), or generate the inline
  block from `tokens.css` at sync time. The inline `:root` above is a verification shortcut, not the
  end state — the whole point is that a change in `tokens.css` reaches both the app and the gallery.
- Author the remaining components (modal, chip, panel, roster item, status pill, tab, lxm-game card),
  extracting each into `components.css` as it's galleryified (Phase 1's deferred componentization).
