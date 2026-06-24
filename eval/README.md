# Eval

`report.md` in this directory is a committed artifact. It is the output of
running the matcher on the seeded synthetic fixtures in
`examples/intake-demo/` and scoring the result against the planted ground truth
in `ground_truth.json`. There is no real personal data in the fixtures.

Regenerate it with:

```sh
make eval
```

CI regenerates it and fails if the committed copy is stale, so the numbers in the
repo always match the code.

## What is measured, and why this shape

Correctness is asymmetric. A false merge joins two different people and can
corrupt a record irreversibly. A missed match leaves a duplicate, which a later
pass can still catch. The gated metric is therefore the **false-merge rate**: of
the pairs the system merged without a human, how many were wrong. It is reported
with a Wilson interval because the denominator (auto-merged pairs) is small and a
normal-approximation interval would understate the uncertainty.

The fixtures deliberately include cases that should **not** auto-merge: two people
with the same common name and different dates of birth, and one real duplicate
whose dates of birth differ by a typo. The matcher cannot tell those apart from
the data alone, so both land in the review band. That is the point: the review
queue exists for exactly the pairs a confidence threshold should not decide
on its own. Auto-level recall is below 100% on purpose, while coverage recall
(auto plus review) captures every true duplicate.
