"""Render the review queue's actual HTML output to files for the axe audit.

This is not a mock of the UI: it runs the real pipeline against the committed
demo fixtures and calls the same ``render_overview``/``render_pair`` functions
the live server uses, so the HTML the axe scan sees is byte-for-byte what a
reviewer's browser would receive. ``scripts/axe_audit.mjs`` then loads each file
into a DOM and runs axe-core against it.

Six pages are captured, chosen to cover every branch the templates take:
overview with an undecided queue, overview with every pair decided, a pair page
with no verdict yet, a pair page after approve, a pair page after reject, and a
pair page under the DV privacy pack (the extra privacy banner). The empty-queue
branch (``session.total == 0``) is exercised by ``tests/test_review.py`` instead,
since the committed demo fixture always has review pairs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from constituent_reconciler import pipeline  # noqa: E402
from constituent_reconciler.config import load_recipe  # noqa: E402
from constituent_reconciler.review.render import render_overview, render_pair  # noqa: E402
from constituent_reconciler.review.session import APPROVED, REJECTED, ReviewSession  # noqa: E402

EXAMPLE = REPO_ROOT / "examples" / "intake-demo" / "recipe.toml"
APPLY_COMMAND = "reconcile apply --config recipe.toml --decisions decisions.json"


def _session(tmp_path: Path, *, privacy: bool) -> ReviewSession:
    recipe = load_recipe(EXAMPLE)
    result = pipeline.run(recipe)
    return ReviewSession(
        result,
        recipe.fields,
        tmp_path / "decisions.json",
        reviewer="axe-fixture",
        privacy_mode=privacy,
    )


def render_fixtures(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def _write(name: str, html: str) -> None:
        path = out_dir / name
        path.write_text(html, encoding="utf-8")
        written.append(path)

    session = _session(out_dir / "_state", privacy=False)
    if session.total < 2:
        raise RuntimeError(
            "the intake-demo fixture needs at least two review pairs for the "
            "axe fixture set to cover approve and reject; the demo data changed"
        )

    _write("overview-undecided.html", render_overview(session, apply_command=APPLY_COMMAND))

    views = session.views()
    _write(
        "pair-undecided.html",
        render_pair(session, views[0], apply_command=APPLY_COMMAND),
    )

    session.record(0, APPROVED)
    session.record(1, REJECTED)
    _write(
        "pair-approved.html",
        render_pair(session, session.view(0), apply_command=APPLY_COMMAND),
    )
    _write(
        "pair-rejected.html",
        render_pair(session, session.view(1), apply_command=APPLY_COMMAND),
    )
    for i in range(2, session.total):
        session.record(i, APPROVED if i % 2 == 0 else REJECTED)
    _write("overview-decided.html", render_overview(session, apply_command=APPLY_COMMAND))

    privacy_session = _session(out_dir / "_state_privacy", privacy=True)
    privacy_views = privacy_session.views()
    _write(
        "pair-privacy-banner.html",
        render_pair(privacy_session, privacy_views[0], apply_command=APPLY_COMMAND),
    )

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / ".axe-fixtures",
        help="directory to write the rendered HTML fixtures into",
    )
    args = parser.parse_args()
    written = render_fixtures(args.out)
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
