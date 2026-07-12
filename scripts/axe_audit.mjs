#!/usr/bin/env node
// Runs axe-core against the rendered review-queue HTML fixtures.
//
// Uses jsdom rather than a real browser: axe-core's rule engine operates on the
// DOM, and jsdom builds a real DOM from the exact HTML string the Python server
// sends over the wire, with no browser binary to download or pin, which keeps
// this offline-first project's dev toolchain from growing a Chromium download.
//
// One documented, honest gap: axe's color-contrast check needs a real
// <canvas> 2D context to sample rendered pixel colors, which jsdom does not
// implement (https://github.com/jsdom/jsdom/issues/2531). That check always
// comes back "incomplete" here rather than pass or fail, so it is reported
// separately below and does not gate the exit code. Every foreground/
// background pair in review/render.py's stylesheet was checked by hand
// against the WCAG 2 relative-luminance contrast formula when this script was
// added: the lowest ratio in the sheet is the focus-outline color against
// white at 4.59:1 (needs 3:1 for a non-text UI indicator), and every text
// pair clears 8.8:1 (needs 4.5:1). A real browser re-check of color-contrast,
// plus everything axe cannot check at all under jsdom (reading order, focus
// visibility in practice, whether the rationale text actually makes sense
// spoken aloud), is part of the manual screen-reader walkthrough checklist
// (docs/reviews/SCREEN-READER-WALKTHROUGH.md).
//
// Usage: node scripts/axe_audit.mjs <fixtures-dir>
// Exit code 0 when every fixture has zero violations and zero unexpected
// incomplete results, 1 otherwise.

import { readFileSync, readdirSync } from "node:fs";
import { extname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";
import axeCore from "axe-core";

// jsdom logs a "not implemented: HTMLCanvasElement.prototype.getContext"
// error to the console every time axe's color-contrast check probes the
// canvas that does not exist. That is the expected, already-documented gap
// above, not a new failure, so it is muted here instead of spamming the run.
const quietConsole = new VirtualConsole();
quietConsole.sendTo(console, { omitJSDOMErrors: true });

const dir = process.argv[2];
if (!dir) {
  console.error("usage: node scripts/axe_audit.mjs <fixtures-dir>");
  process.exit(2);
}

const files = readdirSync(dir)
  .filter((name) => extname(name) === ".html")
  .sort();

if (files.length === 0) {
  console.error(`no .html fixtures found in ${dir}; run make axe-fixtures first`);
  process.exit(2);
}

// WCAG 2.2 AA is the review queue's stated conformance target (docs/ROADMAP.md
// metrics ledger), plus axe's general best-practice rules, which catch a few
// real issues (e.g. empty headings) outside the strict WCAG rule set.
const RUN_OPTIONS = {
  runOnly: {
    type: "tag",
    values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"],
  },
};

// The only incomplete result treated as expected: color-contrast, for the
// jsdom-has-no-canvas reason explained above. Any other incomplete result is
// a real gap in the audit's coverage of this markup and fails the run so it
// gets investigated rather than silently ignored.
const EXPECTED_INCOMPLETE = new Set(["color-contrast"]);

let totalViolations = 0;
let unexpectedIncomplete = 0;

for (const file of files) {
  const html = readFileSync(join(dir, file), "utf-8");
  const dom = new JSDOM(html, { url: "http://127.0.0.1:8765/", virtualConsole: quietConsole });
  // jsdom does not implement Document.elementsFromPoint, which a couple of
  // axe's best-practice checks (landmark-one-main, page-has-heading-one) use
  // only to detect whether an open modal dialog is covering the page. This
  // markup never opens a modal, so "nothing is covering the page" is the
  // accurate answer, not a faked one.
  dom.window.document.elementsFromPoint = () => [];
  // Pass the document element, not the document itself, as the scan root:
  // axe-core only auto-derives window/document from an explicit context when
  // that context has a non-null ownerDocument, which Document.ownerDocument
  // never does but Element.ownerDocument always does. That lets each fixture
  // bring its own jsdom realm without mutating Node's process-wide globals.
  const results = await axeCore.run(dom.window.document.documentElement, RUN_OPTIONS);
  dom.window.close();

  const unexpected = results.incomplete.filter((i) => !EXPECTED_INCOMPLETE.has(i.id));
  const expected = results.incomplete.filter((i) => EXPECTED_INCOMPLETE.has(i.id));

  if (results.violations.length === 0 && unexpected.length === 0) {
    const note = expected.length > 0 ? ` (${expected.map((i) => i.id).join(", ")}: not checkable under jsdom)` : "";
    console.log(`ok    ${file}${note}`);
  }

  if (results.violations.length > 0) {
    totalViolations += results.violations.length;
    console.log(`FAIL  ${file}: ${results.violations.length} violation(s)`);
    for (const violation of results.violations) {
      console.log(`  [${violation.impact}] ${violation.id}: ${violation.help}`);
      console.log(`    ${violation.helpUrl}`);
      for (const node of violation.nodes) {
        console.log(`    target: ${node.target.join(" ")}`);
        console.log(`    html:   ${node.html}`);
      }
    }
  }

  if (unexpected.length > 0) {
    unexpectedIncomplete += unexpected.length;
    console.log(`FAIL  ${file}: ${unexpected.length} unexpected incomplete result(s)`);
    for (const inc of unexpected) {
      console.log(`  [${inc.id}] ${inc.help} (${inc.message ?? "needs manual review"})`);
      console.log(`    ${inc.helpUrl}`);
      for (const node of inc.nodes) {
        console.log(`    target: ${node.target.join(" ")}`);
        console.log(`    html:   ${node.html}`);
      }
    }
  }
}

if (totalViolations > 0 || unexpectedIncomplete > 0) {
  console.error(
    `\naxe audit failed: ${totalViolations} violation(s), ` +
      `${unexpectedIncomplete} unexpected incomplete result(s) across ${files.length} page(s)`
  );
  process.exit(1);
}

console.log(
  `\naxe audit passed: 0 violations across ${files.length} page(s) ` +
    `(color-contrast excluded, not checkable under jsdom; see script header)`
);
