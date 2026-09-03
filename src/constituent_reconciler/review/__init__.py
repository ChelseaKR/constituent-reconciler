"""Local web review queue.

A non-technical reviewer steps through the uncertain candidate pairs, sees the
two records side by side with their source spans, and approves or rejects each
merge. The verdicts are written to ``decisions.json`` in the shape
``constituent-reconcile apply`` consumes, so the web step replaces the hand-edited CSV
without changing the rest of the pipeline.

Every verdict is attributed to a named reviewer with a timestamp, in the
decisions file's ``audit`` section. Under two-person mode (the DV pack's
default) a merge takes effect only after two distinct reviewers approve it; any
rejection rejects immediately.

The server is offline by construction: it binds the loopback interface only,
loads no external asset, and under a policy pack that requires local targets it
refuses a non-loopback bind, fail-closed. The only artifact it persists is the
decisions file, which carries record ids, verdicts, reviewer names, and
timestamps and no field values, so the minimization the DV pack expects holds
for what leaves the review step.
"""

from __future__ import annotations

from constituent_reconciler.review.session import (
    FieldCell,
    PairView,
    ReviewSession,
)

__all__ = ["FieldCell", "PairView", "ReviewSession"]
