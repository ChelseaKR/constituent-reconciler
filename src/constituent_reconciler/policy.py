"""Policy packs and the invariants they switch on.

A policy pack is a named bundle of confidentiality invariants the pipeline
enforces. The default pack enforces nothing beyond the ordinary fail-closed
gate. The ``dv`` pack is the one the research named the fundability unlock: it
encodes the confidentiality posture a victim-service provider operating under
VAWA and FVPSA needs, as switches the rest of the code reads and as tests assert.

The invariants here are grounded in primary guidance, not invented. The sources
and exact statutory citations are recorded in docs/RESPONSIBLE-TECH-AUDITS.md and
docs/decisions/0005-dv-policy-pack.md. In brief:

* No PII egress. VAWA bars a grantee from disclosing personally identifying
  client information "regardless of whether the information has been encoded,
  encrypted, hashed, or otherwise protected" (34 U.S.C. § 12291(b)(2)(B)(i);
  FVPSA parallel at 42 U.S.C. § 10406(c)(5)). NNEDV and HUD read entry into a
  shared database (such as HMIS) as a prohibited disclosure, which is why a
  victim-service provider keeps client data in its own comparable database. The
  ``dv`` pack therefore fuses the cloud extraction seam off and refuses any
  non-local write target.
* Consent required. VAWA requires informed, written, reasonably time-limited
  consent before any individual client information is released
  (34 U.S.C. § 12291(b)(2)(B)(ii)), and consent may not be a condition of
  services (§ 12291(b)(2)(D)(ii)(I)). The export gate withholds any record
  without granted consent.
* Aggregate, suppressed sharing. Only non-personally-identifying data in the
  aggregate may be shared for reporting (34 U.S.C. § 12291(b)(2)(D)(i)(I)). The
  ``dv`` pack emits an aggregate summary with small-cell suppression modeled on
  the U.S. CMS Cell Size Suppression Policy (suppress counts of 1-10, preserve
  true zeros). No uniform federal threshold exists and HUD, VAWA, and FVPSA set
  none; the CMS rule is the most defensible bright line and is cited as such, not
  as a DV mandate.
"""

from __future__ import annotations

from dataclasses import dataclass

# The CMS Cell Size Suppression Policy bright line: a cell holding a count of 1
# through 10 is suppressed; a true zero is preserved. Used as the default
# small-cell threshold for the aggregate export. See module docstring for the
# honest attribution.
DEFAULT_SUPPRESSION_THRESHOLD: int = 11


class PolicyViolation(RuntimeError):
    """A run was configured in a way the active policy pack forbids.

    Raised, fail-closed, before any data is written: for example, selecting a
    non-local write target under the ``dv`` pack, which would egress client PII.
    """


@dataclass(frozen=True)
class Policy:
    """The invariants an active policy pack enforces.

    Each field is read by exactly one part of the pipeline, so the pack stays a
    declarative bundle rather than scattered conditionals:

    * ``require_consent`` -> the consent export gate (consent.py)
    * ``forbid_cloud_seam`` -> the extraction seam factory (extract/seam.py)
    * ``allow_local_seam`` -> the extraction seam factory (extract/seam.py)
    * ``require_local_targets`` -> connector selection (pipeline.build_connector)
    * ``aggregate_export`` -> the aggregate summary writer (pipeline.export)

    ``forbid_cloud_seam`` and ``allow_local_seam`` are deliberately separate
    dimensions, not one inferred from the other. "No cloud calls" (PII must
    never leave the machine) and "no model at all" (no LLM may touch PII,
    even one running locally) are different questions with different
    answers: a local model run entirely on the deployer's own hardware
    satisfies the first without settling the second. Whether model-assisted
    extraction of any kind is acceptable under a given org's VAWA or HIPAA
    reading is that org's counsel's call, not this codebase's to assume. So
    ``allow_local_seam`` defaults to ``False`` even where ``forbid_cloud_seam``
    is ``True``; turning it on is either a pack-level decision recorded here
    once that analysis is written, or a recipe-level
    ``extract.local_model_override`` a deployer sets explicitly, never
    implied by requesting the local backend alone. See
    docs/decisions/0009-local-model-seam.md.
    """

    pack: str
    require_consent: bool = False
    forbid_cloud_seam: bool = False
    allow_local_seam: bool = False
    require_local_targets: bool = False
    aggregate_export: bool = False
    suppression_threshold: int = DEFAULT_SUPPRESSION_THRESHOLD


# The packs the tool ships. ``dv`` enforces the full VAWA/FVPSA posture. ``hipaa``
# turns on consent and fuses the cloud seam off, but its full invariant set
# (BAAs, the Safe Harbor de-identification method) is not yet specified here, so
# it deliberately does not claim the dv pack's local-target and aggregate rules.
#
# Neither ``dv`` nor ``hipaa`` sets ``allow_local_seam``; both leave it at its
# False default. That is the deliberate, no-model-until-counsel-says-so
# posture: a local model does not egress PII, but this codebase has not
# recorded a legal analysis saying model-assisted extraction itself clears
# either bar. A deployer whose counsel has done that analysis opts in per
# recipe via ``extract.local_model_override``, not by editing this table.
_PACKS: dict[str, Policy] = {
    "default": Policy(pack="default"),
    "dv": Policy(
        pack="dv",
        require_consent=True,
        forbid_cloud_seam=True,
        require_local_targets=True,
        aggregate_export=True,
    ),
    "hipaa": Policy(
        pack="hipaa",
        require_consent=True,
        forbid_cloud_seam=True,
    ),
}


def policy_for(pack: str) -> Policy:
    """Return the invariants for ``pack``. An unknown pack raises, fail-closed.

    A typo in the pack name must not silently fall back to the permissive
    default, so an unrecognized name is an error rather than a no-op.
    """

    try:
        return _PACKS[pack]
    except KeyError:
        known = ", ".join(sorted(_PACKS))
        raise PolicyViolation(f"unknown policy pack {pack!r}; known packs: {known}") from None
