"""Connector repair-capability declarations (ADR 0012).

Repair is not part of the connector protocol. ``Connector.write_all`` keeps its
one-method contract, and an adapter that declares nothing here remains a
complete, valid connector. An adapter that supports repair publishes a
:class:`RepairDeclaration`: the destination product it targets, the exact
destination versions its repair behavior was verified against, the operations
supported on those versions, and the vendor evidence behind the verification.
The planner (``repair.py``) reads declarations through
:func:`supported_operations` before naming any operation in a plan.

The fail-closed rules are structural. A destination version absent from a
declaration's enumerated list is unsupported, even when the same adapter writes
to it every day; a version range or wildcard is refused at construction time,
so "verified against 5.81" can never quietly become "verified against 5.x";
and no declaration means no remote repair at all, which
``tests/test_connector_conformance.py`` asserts for every registered
connector. No adapter ships a declaration yet: the CiviCRM pilot requires
current vendor documentation and a disposable live instance first, and this
module refuses to let that evidence be asserted from memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Version tokens must name one exact release. Any of these marks would turn
# the token into a range, a wildcard, or an alias that drifts over time.
_RANGE_MARKS: tuple[str, ...] = ("*", "<", ">", "=", "+", "~", ",", " ")


class RepairDeclarationError(ValueError):
    """A repair declaration failed validation and was refused, fail-closed."""


@dataclass(frozen=True)
class RepairOperation:
    """One repair operation a declaration offers, marked for destructiveness.

    ``destructive`` is load-bearing: applying a plan that contains any
    destructive operation against a remote destination requires a second
    reviewer in every policy pack (ADR 0012). The apply path is not
    implemented yet; the marking exists so a plan can already say which of
    its operations will carry that requirement.
    """

    name: str
    destructive: bool

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise RepairDeclarationError("a repair operation requires a non-blank name")


@dataclass(frozen=True)
class RepairDeclaration:
    """What one adapter has verified it can repair, and the evidence for it.

    ``connector`` is the registry name a recipe's ``[output]`` section uses.
    ``destination`` names the product and API surface (for the planned pilot,
    CiviCRM API v4). ``verified_versions`` enumerates the exact destination
    versions the repair behavior was exercised against, one by one.
    ``vendor_documentation``, ``checked_on``, and ``live_instance`` record
    where the semantics were read, when, and which disposable live instance
    they were exercised on; a declaration without that evidence is refused.
    """

    connector: str
    destination: str
    verified_versions: tuple[str, ...]
    operations: tuple[RepairOperation, ...]
    vendor_documentation: str
    checked_on: str
    live_instance: str

    def __post_init__(self) -> None:
        for field_name in ("connector", "destination", "vendor_documentation", "live_instance"):
            if not str(getattr(self, field_name)).strip():
                raise RepairDeclarationError(f"a repair declaration requires {field_name}")
        try:
            date.fromisoformat(self.checked_on)
        except ValueError:
            raise RepairDeclarationError(
                f"checked_on must be the ISO date the vendor documentation was read, "
                f"got {self.checked_on!r}"
            ) from None
        self._check_versions()
        if not self.operations:
            raise RepairDeclarationError(
                "a repair declaration must name at least one verified operation"
            )
        names = [operation.name for operation in self.operations]
        if len(names) != len(set(names)):
            raise RepairDeclarationError("repair operation names must be unique")

    def _check_versions(self) -> None:
        if not self.verified_versions:
            raise RepairDeclarationError(
                "a repair declaration must enumerate at least one verified destination version"
            )
        for version in self.verified_versions:
            bad = (
                not version.strip()
                or version.strip().lower() == "latest"
                or any(mark in version for mark in _RANGE_MARKS)
            )
            if bad:
                raise RepairDeclarationError(
                    f"verified version {version!r} is not an exact release: versions are "
                    "enumerated one by one, never a range, wildcard, or floating alias"
                )


# The declarations adapters have published, keyed by registry connector name.
# Empty today, deliberately: no destination's delete, merge, or restore
# semantics have been read from current vendor documentation and exercised on
# a live disposable instance, so no adapter may claim them.
REPAIR_DECLARATIONS: dict[str, RepairDeclaration] = {}


def declare_repair(declaration: RepairDeclaration) -> None:
    """Publish one adapter's declaration. Declaring a connector twice raises.

    A silent overwrite could swap a verified operation set for an unverified
    one, so a duplicate is an error the same way a duplicate connector
    registration is.
    """

    if declaration.connector in REPAIR_DECLARATIONS:
        raise RepairDeclarationError(
            f"repair capabilities already declared for connector {declaration.connector!r}"
        )
    REPAIR_DECLARATIONS[declaration.connector] = declaration


def repair_declaration(connector_name: str) -> RepairDeclaration | None:
    """The declaration for a connector, or None when the adapter made none."""

    return REPAIR_DECLARATIONS.get(connector_name)


def supported_operations(
    connector_name: str, destination_version: str
) -> tuple[RepairOperation, ...]:
    """The operations verified for this exact connector and destination version.

    Returns an empty tuple unless a declaration exists and enumerates
    ``destination_version`` by name. An unknown or blank version therefore
    yields no operations, which is the fail-closed default the planner turns
    into manual instructions.
    """

    declaration = REPAIR_DECLARATIONS.get(connector_name)
    if declaration is None or destination_version not in declaration.verified_versions:
        return ()
    return declaration.operations
