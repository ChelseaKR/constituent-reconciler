# 0011 — Automated axe audit of the review queue

Status: accepted (v0.7 follow-up)

## Context

ADR 0007 landed the review queue's structural AA work (real table semantics,
status carried by text and a symbol, keyboard-complete controls, no-JS
fallback) but left two items open in the metrics ledger's accessibility row:
"axe clean" and "screen-reader walkthrough" (docs/ROADMAP.md). The first is a
mechanical scan a tool can run; the second is a human using real assistive
technology and judging whether the experience actually makes sense, which no
script can stand in for. This decision covers only the first half. The
walkthrough checklist for the second is committed in
docs/reviews/SCREEN-READER-WALKTHROUGH.md, marked not yet performed, because
writing a walkthrough report without a human having done the walkthrough would
be exactly the kind of overclaim the project refuses elsewhere (CASS
certification, HIPAA completeness).

## Decisions

### axe-core over jsdom, not a browser download

The review queue is server-rendered HTML with an inlined stylesheet and one
small progressive-enhancement script; there is no client-side framework whose
behavior a real browser is needed to observe. axe-core's rule engine operates
on a DOM tree, and jsdom builds a real DOM from the same HTML string the
`http.server` sends over the wire, so `scripts/axe_audit.mjs` gets a faithful
scan target without Playwright or Puppeteer downloading and pinning a Chromium
binary. That keeps the offline-first project's dev toolchain from growing a
browser-sized dependency for one accessibility check.

The honest tradeoff: axe's `color-contrast` check needs a real `<canvas>` 2D
context to sample rendered pixel colors, which jsdom does not implement. That
check always reports `incomplete` here, is called out separately in the
script's output, and does not gate the exit code. Every foreground/background
pair in `review/render.py`'s stylesheet was checked by hand against the WCAG 2
relative-luminance formula instead: the lowest ratio in the sheet is the focus
outline against white at 4.59:1 (3:1 required for a non-text indicator), and
every text pair clears 8.8:1 (4.5:1 required). A real-browser recheck of
color-contrast is folded into the screen-reader walkthrough checklist.

### A minimal, pinned, dev-only Node toolchain

This is a Python project by rule (CLAUDE.md: "keep everything around \[the
matcher\] on the standard library"), and that rule is about the shipped
pipeline, not test tooling — but a new language ecosystem is still worth
naming rather than sliding in quietly. `package.json` declares exactly two
devDependencies (`axe-core`, `jsdom`), both pinned to an exact version with a
committed `package-lock.json` for `npm ci` to reproduce. Neither package is
referenced by `Dockerfile` or `pyproject.toml`; the self-host image and the
`pip install` path are unaffected. `osv-scanner` already scans whatever
lockfiles are present in the repo, so `package-lock.json` is covered by the
existing `make security` gate without a workflow change.

### Real rendered output, not hand-written fixture HTML

`scripts/render_axe_fixtures.py` runs the actual pipeline against the
committed `examples/intake-demo` fixture and calls the same
`render_overview`/`render_pair` functions the live server calls, writing their
output to `.axe-fixtures/*.html`. The axe scan therefore sees byte-for-byte
what a reviewer's browser receives, not a hand-authored approximation that
could drift from the real templates. Six pages are captured to cover every
template branch: the overview with an undecided queue and with every pair
decided, a pair page with no verdict yet, one after approve, one after reject,
and one under the DV policy pack's privacy banner.

### A dedicated `make axe` target, not folded into `make verify`

`make axe` (fixtures, then the scan) is its own target rather than a step in
`make verify`, so a contributor without Node installed is not blocked from
`ruff`/`mypy`/`pytest`. CI runs it as its own job (`.github/workflows/ci.yml`,
job `accessibility`), parallel to `security`/`sast`/`secrets`, so it is a
named, independently-failing status check rather than a silent step buried
inside another job's log.

## Consequences

- The metrics ledger's "axe clean" half of the accessibility row is now an
  AUTO gate, enforced in CI against the real rendered markup.
- The "screen-reader walkthrough" half stays open, tracked in
  docs/ROADMAP.md and docs/reviews/SCREEN-READER-WALKTHROUGH.md, because it
  requires a human tester and this decision does not claim otherwise.
- A repo clone now optionally needs Node 20+ and `npm ci` to run the
  accessibility check locally; `make verify` and the Python test suite are
  unaffected.
- Any future change to `review/render.py` that introduces a real axe
  violation (a missing label, a heading level skip, an unlabeled control)
  fails CI at the `accessibility` job rather than shipping unnoticed.
