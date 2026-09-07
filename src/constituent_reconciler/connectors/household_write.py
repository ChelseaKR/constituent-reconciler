"""The shared shape of a household write, and what a connector reports back.

EXP-07 shipped household suggestions and a shared household-id column in the
CSV connectors. The live CiviCRM and Salesforce connectors ignored the confirmed
map, so an organization on the API path got contacts and lost the household it
had just reviewed. Both connectors now write it, and both report the same
result shape so the conformance suite can hold them to one contract.

Nothing here decides membership. ``household.plan_household_writes`` refuses any
household with a withheld or unwritten member before a connector sees it, so a
household reaching a connector is one a reviewer confirmed and whose every
member landed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: CiviCRM's machine name for the member-to-household relationship. Named here
#: rather than inline so the two connectors and the conformance suite agree on
#: one string, and so a change to it is a one-line, reviewable diff.
HOUSEHOLD_RELATIONSHIP_TYPE = "Household Member of"

#: Actions a household write reports. ``would-write`` is the dry-run preview,
#: derived from the plan's own ids with no call of any kind.
HOUSEHOLD_WRITE_ACTIONS = frozenset({"created", "updated"})


@dataclass(frozen=True)
class HouseholdWriteResult:
    """What one household write did. Ids and an action, never a field value."""

    household_id: str
    #: The destination's own id for the household record, or the external id on
    #: a dry run. Reported so a provenance entry can name the thing that exists.
    external_id: str
    action: str
    members: tuple[str, ...] = field(default=())

    @property
    def is_write(self) -> bool:
        return self.action in HOUSEHOLD_WRITE_ACTIONS
