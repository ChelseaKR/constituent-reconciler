# OpenSSF Scorecard report — 2026-07-17

First dated Scorecard snapshot for this repository (roadmap item D8). Run
locally with the Scorecard CLI before the scheduled workflow existed, so the
baseline is on record from day one:

- Tool: `scorecard` CLI v5.5.0
- Command: `scorecard --repo=github.com/ChelseaKR/constituent-reconciler`
- Repository commit analyzed: `d04c423e41d1`
- Date: 2026-07-17
- Aggregate score: **6.8 / 10**

Continuous runs: `.github/workflows/scorecard.yml` re-runs the analysis weekly
and on every push to `main`, publishes results to the OpenSSF API, and uploads
SARIF to Code Scanning. This file is a dated snapshot, not a generated
artifact; regenerate it by re-running the command above and commit the next
snapshot under a new date when the posture changes materially.

## Scores

| Score | Check | Scorecard's reason |
|---|---|---|
| 10 | Binary-Artifacts | no binaries found in the repo |
| 3 | Branch-Protection | branch protection is not maximal on development and all release branches |
| 10 | CI-Tests | 30 out of 30 merged PRs checked by a CI test |
| 0 | CII-Best-Practices | no effort to earn an OpenSSF best practices badge detected |
| 0 | Code-Review | found 0/30 approved changesets |
| 3 | Contributors | project has 1 contributing company or organization |
| 10 | Dangerous-Workflow | no dangerous workflow patterns detected |
| 10 | Dependency-Update-Tool | update tool detected (Renovate) |
| 0 | Fuzzing | project is not fuzzed |
| 10 | License | license file detected |
| 0 | Maintained | project was created within the last 90 days |
| n/a | Packaging | packaging workflow not detected |
| 9 | Pinned-Dependencies | dependency not pinned by hash detected (`Dockerfile:33` pip install) |
| 10 | SAST | SAST tool is run on all commits |
| 10 | Security-Policy | security policy file detected |
| n/a | Signed-Releases | no releases found |
| 10 | Token-Permissions | workflow tokens follow least privilege |
| 10 | Vulnerabilities | 0 existing vulnerabilities detected |

## Reading the low scores honestly

- **Code-Review (0).** Accurate: this is a solo-maintained repository and no
  changeset has a second-party approval. The waiver and its compensating
  controls (merge-blocking CI, privacy-invariant tests, SAST, secret scanning)
  are recorded in ADR 0008. The score will stay at 0 until the project has a
  second maintainer; that is a fact about staffing, not an unmitigated gap.
- **Branch-Protection (3).** A ruleset protecting `main` is applied (required
  status checks, no direct pushes). Scorecard scores it below maximal because
  strict settings such as required approving reviews are not enabled; requiring
  approvals is impossible to satisfy solo (see ADR 0008 again). Partial credit
  here is expected and accepted.
- **Maintained (0).** Mechanical: the check scores any repository younger than
  90 days as 0 regardless of activity. The repository's first commit is from
  May 2026; this row corrects itself with age.
- **Signed-Releases / Packaging (n/a).** No git tag or GitHub release exists
  yet. The tag-triggered release workflow (`release.yml`: build, SBOM, SLSA
  provenance attestation) is committed and will be exercised by the first
  human-cut signed tag; the maintainer action is tracked in the execution
  roadmap (item D3).
- **CII-Best-Practices (0).** The OpenSSF Best Practices badge has not been
  applied for. Deliberate for now: the questionnaire's substance overlaps the
  standards-conformance table already maintained in the README, and the badge
  is worth pursuing once a release exists.
- **Fuzzing (0).** No fuzzer runs against the parse paths. The untrusted-PDF
  surface is currently mitigated by the sandboxed extractor and the threat
  model (docs/THREAT-MODEL.md); structured fuzzing of the extract path is a
  candidate follow-up, not a commitment.
- **Pinned-Dependencies (9).** One warning: the `pip install` at
  `Dockerfile:33` pins versions but not hashes. All 38 GitHub Actions uses and
  the base container image are digest-pinned.
- **Contributors (3).** Mechanical solo-maintainer score; same root cause as
  Code-Review.
