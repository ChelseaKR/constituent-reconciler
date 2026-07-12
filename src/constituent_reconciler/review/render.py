"""HTML for the review queue.

Every page is self-contained: the CSS and the small progressive-enhancement
script are inlined, and nothing is fetched from a network or a CDN. That keeps
the UI working with no connection and is part of the no-egress guarantee.

The markup is built for WCAG 2.2 AA. Status is carried by text and a symbol, not
colour alone; the comparison is a real table with scoped headers; the decision
controls are ordinary buttons that work without JavaScript, and a keyboard
reviewer can complete a pass using Tab and the visible access keys. The script
only adds single-key shortcuts on top of controls that already work.
"""

from __future__ import annotations

from html import escape

from constituent_reconciler.models import Correction
from constituent_reconciler.review.session import (
    APPROVED,
    AWAITING_SECOND,
    CONFLICT_NOTE,
    REJECTED,
    ClusterEdgeView,
    ClusterGroupView,
    ClusterMemberView,
    ClusterPreview,
    FieldCell,
    GoldenFieldView,
    PairView,
    ReviewSession,
    field_label,
    rationale_for,
)

_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  font: 16px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  margin: 0; color: #1a1a1a; background: #fff;
}
.skip-link {
  position: absolute; left: -999px; top: 0; background: #003366; color: #fff;
  padding: 0.5rem 1rem; z-index: 10;
}
.skip-link:focus { left: 0; }
header, main, footer { max-width: 60rem; margin: 0 auto; padding: 0 1rem; }
header { border-bottom: 2px solid #003366; padding-top: 1rem; padding-bottom: 0.5rem; }
h1 { font-size: 1.4rem; margin: 0.2rem 0; }
.privacy {
  background: #002b1d; color: #fff; padding: 0.5rem 1rem; font-weight: 600;
}
.calibration {
  background: #4a3800; color: #fff; padding: 0.5rem 1rem; font-weight: 600;
}
.progress { margin: 0.5rem 0; font-weight: 600; }
.bar {
  background: #eee; border: 1px solid #999; height: 0.9rem;
  border-radius: 4px; overflow: hidden;
}
.bar > span { display: block; height: 100%; background: #003366; }
table.compare { border-collapse: collapse; width: 100%; margin: 1rem 0; }
table.compare th, table.compare td {
  border: 1px solid #999; padding: 0.5rem 0.6rem; text-align: left; vertical-align: top;
}
table.compare th[scope=row] { width: 9rem; background: #f2f4f7; }
.agree { font-weight: 600; }
.rationale {
  border: 1px solid #003366; border-left-width: 6px; border-radius: 6px;
  background: #f2f4f7; padding: 0.6rem 1rem; margin: 1rem 0;
}
.rationale h3 { margin: 0 0 0.3rem; font-size: 1.05rem; }
.rationale p { margin: 0; }
.tag {
  display: inline-block; padding: 0.05rem 0.45rem; border: 1px solid; border-radius: 4px;
  font-size: 0.8rem; font-weight: 700;
}
.tag.match { color: #054d1c; border-color: #054d1c; background: #e6f4ea; }
.tag.differ { color: #6b1010; border-color: #6b1010; background: #fdecea; }
.tag.neutral { color: #333; border-color: #999; background: #f2f4f7; }
.span { color: #444; font-size: 0.8rem; }
.verdict { margin: 0.4rem 0; font-weight: 700; }
.verdict.approved { color: #054d1c; }
.verdict.rejected { color: #6b1010; }
.correct-fieldset {
  border: 1px solid #999; border-radius: 6px; padding: 0.8rem 1rem; margin: 1rem 0;
}
.correct-fieldset label { display: block; margin-top: 0.4rem; font-weight: 600; }
.correct-fieldset input[type=text], .correct-fieldset select { font: inherit; padding: 0.4rem; }
.tag.corrected { color: #7a4b00; border-color: #7a4b00; background: #fff3e0; }
.actions { display: flex; flex-wrap: wrap; gap: 0.6rem; margin: 1rem 0; }
button, .btn {
  font: inherit; padding: 0.55rem 1rem; border: 2px solid #003366; border-radius: 6px;
  background: #003366; color: #fff; cursor: pointer; text-decoration: none;
}
button.secondary, .btn.secondary { background: #fff; color: #003366; }
a { color: #003366; }
:focus-visible { outline: 3px solid #c05600; outline-offset: 2px; }
ol.queue { padding-left: 1.2rem; }
ol.queue li { margin: 0.3rem 0; }
kbd {
  border: 1px solid #999; border-bottom-width: 2px; border-radius: 4px;
  padding: 0 0.35rem; font-size: 0.85rem; background: #f2f4f7;
}
.note { color: #333; font-size: 0.9rem; }
.cluster-preview h3 { margin: 0 0 0.4rem; font-size: 1.05rem; }
.cluster-preview h4 { margin: 0.8rem 0 0.2rem; font-size: 0.95rem; }
.cluster-preview .groups { display: flex; flex-wrap: wrap; gap: 1.2rem; }
.cluster-preview .group { flex: 1 1 16rem; min-width: 14rem; }
.cluster-preview ul { margin: 0.2rem 0; padding-left: 1.2rem; }
.cluster-preview li { margin: 0.15rem 0; }
"""

_SCRIPT = """
// Progressive enhancement only: every action below also works via Tab + Enter
// on a visible control. a=approve, c=correct, r=reject, j=next, k=previous.
document.addEventListener('keydown', function (e) {
  if (e.target.matches('input, textarea, select')) return;
  var k = e.key.toLowerCase();
  var map = { a: 'approve', c: 'correct', r: 'reject' };
  if (map[k]) {
    var b = document.querySelector('button[value="' + map[k] + '"]');
    if (b) { e.preventDefault(); b.click(); }
  } else if (k === 'j' || k === 'k') {
    var sel = k === 'j' ? '[data-nav="next"]' : '[data-nav="prev"]';
    var link = document.querySelector(sel);
    if (link) { e.preventDefault(); window.location = link.href; }
  }
});
"""


def _calibration_banner(calibration: int) -> str:
    """The planted-pairs disclosure, shown on every page while planting is on.

    Transparency requirement (EXP-09): the reviewer is always told planted
    pairs exist, and no individual pair is ever marked as planted.
    """

    if calibration <= 0:
        return ""
    noun = "pair" if calibration == 1 else "pairs"
    return (
        '<div class="calibration" role="note">This queue includes '
        f"{calibration} planted known-answer {noun} for calibration. "
        "They are not marked; your decisions on them are used only to report "
        "reviewer agreement and are never applied to records.</div>"
    )


def _page(title: str, body: str, *, privacy: bool, calibration: int = 0) -> str:
    privacy_banner = (
        '<div class="privacy" role="status">Privacy mode (DV policy pack): '
        "this server stays on your machine. Decisions remain PII-free; any field "
        "correction is stored separately and locally.</div>"
        if privacy
        else ""
    )
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n<body>\n"
        '<a class="skip-link" href="#main">Skip to main content</a>\n'
        f"{privacy_banner}"
        f"{_calibration_banner(calibration)}"
        f"{body}\n"
        f"<script>{_SCRIPT}</script>\n"
        "</body>\n</html>\n"
    )


def _progress_bar(done: int, total: int) -> str:
    pct = 0 if total == 0 else round(done / total * 100)
    return (
        f'<div class="bar" role="img" aria-label="{done} of {total} pairs decided">'
        f'<span style="width:{pct}%"></span></div>'
    )


def _reviewer_line(session: ReviewSession) -> str:
    """Who is reviewing, and whether two-person review is on."""

    two_person = (
        " Two-person review is on: a merge takes effect only after two "
        "different reviewers approve it."
        if session.require_second_reviewer
        else ""
    )
    return (
        f'<p class="note">Reviewing as <strong>{escape(session.reviewer)}</strong>.{two_person}</p>'
    )


def _decided_by(session: ReviewSession, index: int, verdict: str) -> str:
    """The distinct reviewer names behind a verdict, for attribution."""

    names = sorted({e.reviewer for e in session.audit(index) if e.verdict == verdict})
    return ", ".join(names)


def render_overview(session: ReviewSession, *, apply_command: str) -> str:
    """The landing page: progress, the queue, and what to do when finished."""

    counts = session.counts()
    decided = counts.approved + counts.corrected + counts.rejected
    total = session.total
    privacy = session.privacy_mode

    rows: list[str] = []
    for view in session.views():
        verdict = session.verdict(view.index)
        if verdict == APPROVED and session.corrections_for(view.index):
            state = '<span class="tag corrected">CORRECTED &amp; APPROVED &#10003;</span>'
        elif verdict == APPROVED:
            state = '<span class="tag match">APPROVED &#10003;</span>'
        elif verdict == REJECTED:
            state = '<span class="tag differ">REJECTED &#10007;</span>'
        elif verdict == AWAITING_SECOND:
            approver = escape(_decided_by(session, view.index, APPROVED))
            state = (
                '<span class="tag neutral">AWAITING SECOND REVIEWER</span> '
                f"(approved by {approver})"
            )
        else:
            state = "<span>not yet reviewed</span>"
        label = f"{escape(view.left_id)} vs {escape(view.right_id)}"
        why = escape(rationale_for(view).short())
        rows.append(
            f'<li><a href="/pair/{view.index}">Pair {view.index + 1}: {label}</a> '
            f"&mdash; {state}"
            f'<div class="note">{why}.</div></li>'
        )

    if total == 0:
        queue_html = (
            "<p>There are no uncertain pairs to review. Every match was decided automatically.</p>"
        )
        next_link = ""
    else:
        queue_html = '<ol class="queue">\n' + "\n".join(rows) + "\n</ol>"
        nxt = session.next_undecided()
        if nxt is not None:
            next_link = f'<p><a class="btn" href="/pair/{nxt}">Review next pair</a></p>'
        else:
            next_link = "<p><strong>All pairs reviewed.</strong></p>"

    awaiting = (
        f", {counts.awaiting_second} awaiting a second reviewer"
        if session.require_second_reviewer or counts.awaiting_second
        else ""
    )
    body = (
        "<header>\n<h1>Review queue</h1>\n"
        f"{_reviewer_line(session)}\n"
        f'<p class="progress" aria-live="polite">{decided} of {total} decided '
        f"&mdash; {counts.approved} approved, {counts.corrected} corrected, "
        f"{counts.rejected} rejected, "
        f"{counts.pending} pending{awaiting}.</p>\n"
        f"{_progress_bar(decided, total)}\n</header>\n"
        '<main id="main">\n'
        f"{next_link}\n{queue_html}\n"
        "<h2>When you are done</h2>\n"
        "<p>Your decisions are saved as you go to "
        f"<code>{escape(str(session.decisions_path))}</code>. Corrected field values "
        f"are attributed in <code>{escape(str(session.corrections_path))}</code>, "
        "which carries PII and needs the same local handling as resolved output. Apply with:</p>\n"
        f"<pre><code>{escape(apply_command)}</code></pre>\n"
        '<p class="note">Approved pairs are merged; rejected pairs are kept '
        "separate. You can stop and resume at any time.</p>\n"
        "</main>\n"
        '<footer><p class="note">This page runs locally and sends no data over '
        "the network.</p></footer>"
    )
    return _page("Review queue", body, privacy=privacy, calibration=session.calibration_total)


def _cell(value: str, span: str) -> str:
    shown = escape(value) if value else "<em>(blank)</em>"
    span_html = f'<div class="span">source: {escape(span)}</div>' if span else ""
    return f"{shown}{span_html}"


def _correction_fieldset(view: PairView) -> str:
    options = "".join(
        f'<option value="{escape(cell.field)}">{escape(field_label(cell.field))}</option>'
        for cell in view.fields
    )
    return (
        '<div class="correct-fieldset"><h3>Fix a value and approve</h3>'
        f'<label for="field-{view.index}">Field</label>'
        f'<select id="field-{view.index}" name="field">{options}</select>'
        "<fieldset><legend>Which record is wrong</legend>"
        f'<input type="radio" name="side" value="left" checked aria-label="{escape(view.left_id)}"> '
        f"{escape(view.left_id)} "
        f'<input type="radio" name="side" value="right" aria-label="{escape(view.right_id)}"> '
        f"{escape(view.right_id)}</fieldset>"
        f'<label for="value-{view.index}">Correct value</label>'
        f'<input type="text" id="value-{view.index}" name="value" required>'
        '<div class="actions"><button type="submit" name="verdict" value="correct" '
        'accesskey="c">Save correction <kbd>C</kbd></button></div></div>'
    )


def _field_row(cell: FieldCell, correction: Correction | None, view: PairView) -> str:
    if correction is not None:
        mark = '<span class="tag corrected">corrected</span>'
    elif cell.agrees:
        mark = '<span class="tag match">match</span>'
    elif cell.comparable:
        mark = '<span class="tag differ">differs</span>'
    else:
        mark = '<span class="tag neutral">not compared</span>'
    left_cell = _cell(cell.left, cell.left_span)
    right_cell = _cell(cell.right, cell.right_span)
    if correction is not None:
        note = (
            f'<div class="note">Corrected to {escape(correction.value)} by '
            f"{escape(correction.reviewer)} at {escape(correction.corrected_at)}.</div>"
        )
        if correction.record_id == view.left_id:
            left_cell += note
        else:
            right_cell += note
    return (
        f'<tr><th scope="row">{escape(field_label(cell.field))}</th>'
        f"<td>{left_cell}</td><td>{right_cell}</td>"
        f'<td class="agree">{mark}</td></tr>'
    )


_EDGE_LABELS: dict[str, tuple[str, str]] = {
    "auto": ("match", "auto-merged"),
    "approved": ("match", "approved"),
    "rejected": ("differ", "rejected"),
    "pending": ("neutral", "pending review"),
    "scored-apart": ("neutral", "scored as a different person"),
}


def _member_row(member: ClusterMemberView) -> str:
    tag = ' <span class="tag match">survivor</span>' if member.is_primary else ""
    return f"<li>{escape(member.record_id)} (from {escape(member.source)}){tag}</li>"


def _edge_row(edge: ClusterEdgeView) -> str:
    cls, text = _EDGE_LABELS.get(edge.status, ("neutral", edge.status))
    pct = edge.probability * 100
    link = (
        f' (<a href="/pair/{edge.pair_index}">review this pair</a>)'
        if edge.pair_index is not None and edge.status == "pending"
        else ""
    )
    return (
        f"<li>{escape(edge.left)} &amp; {escape(edge.right)}: "
        f'<span class="tag {cls}">{escape(text)}</span> ({pct:.1f}%){link}</li>'
    )


def _golden_table(fields: tuple[GoldenFieldView, ...]) -> str:
    if not fields:
        return ""
    rows: list[str] = []
    for gf in fields:
        value = escape(gf.value) if gf.value else "<em>(blank)</em>"
        source = f' <span class="span">from {escape(gf.source_id)}</span>' if gf.source_id else ""
        rows.append(
            f'<tr><th scope="row">{escape(field_label(gf.field))}</th><td>{value}{source}</td></tr>'
        )
    return (
        '<table class="compare">\n<caption class="note">The golden record this '
        "cluster would produce, and which record supplied each value.</caption>\n"
        '<thead><tr><th scope="col">Field</th><th scope="col">Value</th></tr></thead>\n'
        "<tbody>\n" + "\n".join(rows) + "\n</tbody>\n</table>\n"
    )


def _cluster_group_html(group: ClusterGroupView, heading: str) -> str:
    members_html = (
        "<ul>\n" + "\n".join(_member_row(member) for member in group.members) + "\n</ul>\n"
    )
    edges_html = ""
    if len(group.edges) > 1:
        edges_html = (
            "<p>How these records are tied together:</p>\n<ul>\n"
            + "\n".join(_edge_row(edge) for edge in group.edges)
            + "\n</ul>\n"
        )
    golden_html = _golden_table(group.golden)
    return (
        f'<div class="group">\n<h4>{escape(heading)}</h4>\n'
        f"{members_html}{edges_html}{golden_html}</div>\n"
    )


def render_cluster_preview(view: PairView, preview: ClusterPreview | None) -> str:
    """The cluster and golden-record section for a pair's review screen.

    Shows the reviewer the record their decision on this pair implies, not
    only the two rows the pairwise table compares above it: the full set of
    records this would place in one cluster, how those records are otherwise
    tied together, and the golden record that cluster would produce, with
    which record supplied each field.
    """

    if preview is None:
        return ""

    if preview.conflict:
        body = f'<p class="tag differ">Conflicting decisions</p>\n<p>{escape(CONFLICT_NOTE)}</p>\n'
    elif preview.merged:
        group = preview.groups[0]
        n = len(group.members)
        lead = (
            "This decision places these records in one cluster:"
            if n > 2
            else "This pair merges into one record:"
        )
        body = f"<p>{escape(lead)}</p>\n" + _cluster_group_html(
            group, f"Cluster of {n} record{'s' if n != 1 else ''}"
        )
    else:
        left_group, right_group = preview.groups
        body = (
            "<p>Rejecting keeps these two records in separate clusters:</p>\n"
            '<div class="groups">\n'
            + _cluster_group_html(left_group, f"Record {escape(view.left_id)}'s cluster")
            + _cluster_group_html(right_group, f"Record {escape(view.right_id)}'s cluster")
            + "</div>\n"
        )

    return (
        '<div class="rationale cluster-preview" role="note">\n'
        "<h3>What this creates</h3>\n"
        f"{body}</div>\n"
    )


def render_pair(session: ReviewSession, view: PairView, *, apply_command: str) -> str:
    """The decision screen for one candidate pair."""

    counts = session.counts()
    decided = counts.approved + counts.corrected + counts.rejected
    total = session.total
    verdict = session.verdict(view.index)

    corrections = session.corrections_for(view.index)
    field_rows = [_field_row(cell, corrections.get(cell.field), view) for cell in view.fields]

    if verdict == APPROVED and corrections:
        by = escape(_decided_by(session, view.index, APPROVED))
        current = (
            '<p class="verdict approved" role="status">Current decision: CORRECTED AND '
            f"APPROVED &#10003; &mdash; approved by {by}.</p>"
        )
    elif verdict == APPROVED:
        by = escape(_decided_by(session, view.index, APPROVED))
        current = (
            '<p class="verdict approved" role="status">'
            f"Current decision: APPROVED &#10003; (merge) &mdash; approved by {by}.</p>"
        )
    elif verdict == REJECTED:
        by = escape(_decided_by(session, view.index, REJECTED))
        current = (
            '<p class="verdict rejected" role="status">'
            f"Current decision: REJECTED &#10007; (keep separate) &mdash; rejected by {by}.</p>"
        )
    elif verdict == AWAITING_SECOND:
        by = escape(_decided_by(session, view.index, APPROVED))
        if session.reviewer in session.approvers(view.index):
            note = (
                " You already approved this pair; a different reviewer must "
                "provide the second approval before it merges."
            )
        else:
            note = (
                " Your approval would complete the merge; a rejection keeps the records separate."
            )
        current = (
            '<p class="verdict" role="status">'
            f"Awaiting a second reviewer &mdash; approved by {by} so far.{note}</p>"
        )
    else:
        current = '<p class="verdict" role="status">No decision yet.</p>'

    prev_index = view.index - 1
    next_index = view.index + 1
    prev_link = (
        f'<a class="btn secondary" data-nav="prev" href="/pair/{prev_index}">&larr; Previous</a>'
        if prev_index >= 0
        else ""
    )
    next_link = (
        f'<a class="btn secondary" data-nav="next" href="/pair/{next_index}">Next &rarr;</a>'
        if next_index < total
        else '<a class="btn secondary" data-nav="next" href="/">Back to queue</a>'
    )

    pct = view.probability * 100
    rationale = escape(rationale_for(view).summary())
    cluster_html = render_cluster_preview(view, session.cluster_preview(view.index))
    body = (
        "<header>\n<h1>Review queue</h1>\n"
        f"{_reviewer_line(session)}\n"
        f'<p class="progress" aria-live="polite">Pair {view.index + 1} of {total} '
        f"&mdash; {decided} decided, {counts.pending} pending.</p>\n"
        f"{_progress_bar(decided, total)}\n</header>\n"
        '<main id="main">\n'
        f"<h2>Is this the same person?</h2>\n"
        f"<p>The matcher scored these two records at <strong>{pct:.1f}%</strong> "
        "likely to be the same person, which is below the automatic-merge line, "
        "so a person decides.</p>\n"
        '<div class="rationale" role="note">\n'
        "<h3>What matches and what differs</h3>\n"
        f"<p>{rationale}</p>\n</div>\n"
        f"{current}\n"
        '<table class="compare">\n<caption class="note">Record '
        f"{escape(view.left_id)} (from {escape(view.left_source)}) compared with "
        f"{escape(view.right_id)} (from {escape(view.right_source)}).</caption>\n"
        '<thead><tr><th scope="col">Field</th>'
        f'<th scope="col">{escape(view.left_id)} ({escape(view.left_source)})</th>'
        f'<th scope="col">{escape(view.right_id)} ({escape(view.right_source)})</th>'
        '<th scope="col">Agreement</th></tr></thead>\n'
        "<tbody>\n" + "\n".join(field_rows) + "\n</tbody>\n</table>\n"
        f"{cluster_html}"
        f'<form method="post" action="/pair/{view.index}">\n'
        f'<input type="hidden" name="token" value="{escape(session.token)}">\n'
        f"{_correction_fieldset(view) if not view.synthetic else ''}\n"
        '<div class="actions">\n'
        '<button type="submit" name="verdict" value="approve" accesskey="a">'
        "Approve merge <kbd>A</kbd></button>\n"
        '<button type="submit" class="secondary" name="verdict" value="reject" '
        'accesskey="r">Reject, keep separate <kbd>R</kbd></button>\n'
        "</div>\n</form>\n"
        '<nav class="actions" aria-label="Move between pairs">\n'
        f"{prev_link}\n{next_link}\n</nav>\n"
        '<p class="note">Keyboard: <kbd>A</kbd> approve, <kbd>C</kbd> correct, '
        "<kbd>R</kbd> reject, "
        "<kbd>J</kbd> next, <kbd>K</kbd> previous. "
        f'<a href="/">Back to the full queue</a>.</p>\n'
        "</main>\n"
        '<footer><p class="note">Decisions save to '
        f"<code>{escape(str(session.decisions_path))}</code>; corrections save to "
        f"<code>{escape(str(session.corrections_path))}</code>. Apply with "
        f"<code>{escape(apply_command)}</code>.</p></footer>"
    )
    return _page(
        f"Pair {view.index + 1} of {total}",
        body,
        privacy=session.privacy_mode,
        calibration=session.calibration_total,
    )
