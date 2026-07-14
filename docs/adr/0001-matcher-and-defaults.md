# 0001 — Matcher choice and pre-tuned defaults

Status: accepted (v0.1)

## Context

The resolve step needs to decide, for a pair of constituent records, how likely
they are the same person. The target user is a one or two person nonprofit IT
shop. They cannot label training pairs, tune match weights, or reason about m and
u probabilities. The portfolio rule, and the research that motivated this
project, is explicit: wrap an existing matcher, do not reimplement record
linkage.

Three real options were considered.

- **Splink** (Fellegi-Sunter, runs on DuckDB in-process). Unsupervised. Match
  weights can be set directly, or estimated by expectation-maximisation without
  labels. Strong library, active maintenance, Apache-friendly ecosystem.
- **dedupe** (the `dedupe` library / dedupe.io). Requires interactive active
  learning to label pairs, which the target user cannot do.
- **A built-in scorer.** Rejected: it would be reimplementing linkage, the one
  thing the ground rules forbid, and it would be a weaker artifact than wrapping
  a respected library.

## Decision

Wrap **Splink**. Configure it with **hand-set m and u probabilities per
comparison level**, specified in `defaults.py`. No training, no
expectation-maximisation, no labeled pairs. The model is fully specified in
source, so a run is deterministic and reproducible, which the committed eval
depends on.

The contribution of this project is therefore not the matcher. It is:

1. the pre-tuned default settings that make Splink usable with zero data work,
2. the orchestration around it (normalize, block, score, band, cluster, gate),
3. the fail-closed two-threshold band and the human review surface.

## Why hand-set m/u rather than EM

Expectation-maximisation is unsupervised and would fit "no labeled pairs," but on
the small, varied data a single nonprofit holds it can converge to unstable
weights, and its result is not reproducible without pinning seeds and training
blocking rules. Hand-set weights trade a little adaptivity for determinism,
transparency, and a model a reviewer can read. The numbers encode ordinary
survey intuition: same-person records usually agree on name and date of birth but
suffer typos and nicknames; different-person records rarely agree on all fields
at once. A later version can add an opt-in EM-trained mode for organizations with
enough data, behind the same interface.

## Consequences

- The matcher is a hard dependency (`splink`, which pulls DuckDB and pandas). The
  core is therefore not zero-dependency; that is an accepted cost of "wrap, do
  not reinvent."
- Because weights are fixed, the eval is reproducible and the false-merge rate
  can be a merge-blocking gate.
- The defaults are tuned against the seeded fixtures. Real feedback from real
  feeds will move them; that is expected and is why they live in one documented
  module, not scattered through the code.

## Notes on other v0.1 choices

- **argparse, not click/typer**, and **stdlib dataclasses, not pydantic**, for
  the periphery, to keep the dependency surface to the matcher plus its
  transitive needs. This can be revisited if config validation grows.
- **tomllib** (standard library on 3.11+) for the recipe, which is why the
  project requires Python 3.11.
