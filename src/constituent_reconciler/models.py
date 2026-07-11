"""Core data types for the pipeline.

These are plain dataclasses, kept free of any matcher or framework so the rest
of the code (and the tests) can reason about records, scored pairs, bands, and
clusters without importing Splink or pandas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


@dataclass(frozen=True)
class SourceSpan:
    """Location of an extracted value within its source document.

    Coordinates follow pdfplumber convention: x0/x1 are horizontal, top/bottom
    are measured from the top of the page. All values are in PDF user units
    (1/72 inch). ``page`` is 1-indexed.
    """

    source_file: str
    page: int
    x0: float
    top: float
    x1: float
    bottom: float

    def __str__(self) -> str:
        return (
            f"{self.source_file}:p{self.page}"
            f":x={self.x0:.0f}-{self.x1:.0f},y={self.top:.0f}-{self.bottom:.0f}"
        )


@dataclass(frozen=True)
class TextSpan:
    """Location of an extracted value within a plain-text source (.txt or .eml body).

    ``line`` is 1-indexed. ``col_start`` and ``col_end`` are 0-indexed character
    offsets within that line, end exclusive. Values never cross a line boundary
    because the extraction patterns stop at the first newline.
    """

    source_file: str
    line: int
    col_start: int
    col_end: int

    def __str__(self) -> str:
        return f"{self.source_file}:L{self.line}:c{self.col_start}-{self.col_end}"


# The canonical fields the matcher reasons over. Source columns are mapped onto
# these by the recipe. A recipe activates only the fields it maps, so address is
# available but does not affect a run that does not map it.
CANONICAL_FIELDS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "dob",
    "email",
    "phone",
    "address",
)

# Status tokens that read as an affirmative grant, absent any other signal.
GRANTED_STATUSES: frozenset[str] = frozenset({"granted", "active", "yes", "true"})

# Status tokens that read as an explicit revocation. Kept distinct from an
# absent or unrecognized status so a withheld record's reason can say "revoked"
# (someone said no) rather than "absent" (no one ever said yes) -- a reviewer
# acts on those two differently.
REVOKED_STATUSES: frozenset[str] = frozenset({"revoked", "withdrawn", "denied", "no", "false"})

# The fail-closed withhold reasons ``Consent.reason`` can return. Anything not
# ``None`` means the record does not clear the gate.
WITHHOLD_REASONS: frozenset[str] = frozenset(
    {"absent", "revoked", "future-dated", "expired", "out-of-scope"}
)


@dataclass(frozen=True)
class Consent:
    """Consent as a lifecycle: a status with a window and a scope, not a token.

    ``status`` is the raw source token, stripped and lower-cased. ``granted_on``
    and ``expires_on`` are optional dates read from the recipe's consent-date
    and consent-expires columns; ``None`` means the recipe does not map that
    column for this record, not that consent lasts forever by inference --
    ``reason()`` only treats a missing ``expires_on`` as "no ceiling was
    recorded", it never invents one. ``scope`` is the set of destination names
    (connector names such as ``"csv"`` or ``"civicrm"``) this consent covers;
    an empty scope covers every destination, which is the behavior for a
    recipe that does not map a scope column.

    There is no default expiry window anywhere in this class or its callers.
    If a deployment wants a hard ceiling on how long consent lasts absent an
    explicit per-record expiry date, that number is a counsel-gated policy
    decision the recipe must state explicitly (a mapped expiry column); this
    code will not guess it.
    """

    status: str = ""
    granted_on: date | None = None
    expires_on: date | None = None
    scope: frozenset[str] = field(default_factory=frozenset)

    def reason(self, *, as_of: date, destination: str | None = None) -> str | None:
        """The fail-closed withhold reason, or ``None`` if consent is active.

        Checked in order: an explicit revocation, an absent or unrecognized
        status, a not-yet-effective grant date, an expired ceiling, then scope.
        A status that is neither a recognized grant nor a recognized
        revocation (a typo, an unmapped column, a blank cell) reads as
        "absent" -- unrecognized is not evidence of consent.
        """

        status = self.status.strip().lower()
        if status in REVOKED_STATUSES:
            return "revoked"
        if status not in GRANTED_STATUSES:
            return "absent"
        if self.granted_on is not None and self.granted_on > as_of:
            return "future-dated"
        if self.expires_on is not None and self.expires_on < as_of:
            return "expired"
        if destination is not None and self.scope and destination not in self.scope:
            return "out-of-scope"
        return None

    def is_active(self, *, as_of: date, destination: str | None = None) -> bool:
        return self.reason(as_of=as_of, destination=destination) is None

    def label(self, *, as_of: date, destination: str | None = None) -> str:
        """A short, informational label: ``"granted"`` or the withhold reason."""

        reason = self.reason(as_of=as_of, destination=destination)
        return "granted" if reason is None else reason


class Band(StrEnum):
    """Where a scored pair lands after the fail-closed gate."""

    AUTO = "auto"
    REVIEW = "review"
    DROP = "drop"


@dataclass(frozen=True)
class Record:
    """One constituent record as read from a source, before normalization.

    ``raw`` holds the source column values keyed by canonical field name (the
    recipe mapping is applied at read time). ``normalized`` is filled in by the
    normalize step. ``consent`` is the record's consent lifecycle, built from
    whichever consent columns the recipe maps; a recipe that maps none of them
    leaves it at the default (absent). ``spans`` maps each canonical field name
    to where it was found in a source document (a ``SourceSpan`` for PDFs, a
    ``TextSpan`` for text and email bodies); empty for records read from
    structured CSV.
    """

    unique_id: str
    source: str
    raw: dict[str, str]
    normalized: dict[str, str] = field(default_factory=dict)
    consent: Consent = field(default_factory=Consent)
    spans: dict[str, SourceSpan | TextSpan] = field(default_factory=dict)

    def has_consent(self, *, as_of: date | None = None) -> bool:
        """Whether this record's consent is currently active, unscoped.

        Used to rank candidate survivors during golden-record selection, not to
        gate export -- the export gate (``consent.partition_by_consent``) checks
        the golden record's own ``Consent`` against the actual write
        destination and a caller-supplied ``as_of`` for reproducible runs.
        """

        return self.consent.is_active(as_of=as_of if as_of is not None else date.today())


@dataclass(frozen=True)
class Pair:
    """A scored candidate pair of record ids, with the band it was assigned."""

    left: str
    right: str
    probability: float
    band: Band

    def key(self) -> frozenset[str]:
        return frozenset((self.left, self.right))


@dataclass(frozen=True)
class Cluster:
    """A set of record ids the pipeline considers the same constituent."""

    cluster_id: str
    members: tuple[str, ...]


@dataclass(frozen=True)
class GoldenRecord:
    """The single merged record a cluster resolves to.

    ``fields`` are the surviving canonical values. ``field_sources`` is the
    field-level lineage: for each non-empty merged field, the id of the member
    record that supplied its value (fields that merged to empty have no entry).
    ``primary`` is the record id chosen as the survivor, and ``consent`` is the
    survivor's ``Consent`` lifecycle, carried through unevaluated. The export
    gate (``consent.partition_by_consent``) is what turns this into a granted or
    withheld decision, because only it knows the actual write destination and
    the run's ``as_of`` date; a golden record on its own does not decide.
    """

    cluster_id: str
    members: tuple[str, ...]
    fields: dict[str, str]
    primary: str
    field_sources: dict[str, str] = field(default_factory=dict)
    consent: Consent = field(default_factory=Consent)


@dataclass(frozen=True)
class RunResult:
    """The full output of a pipeline run, before any file is written."""

    records: dict[str, Record]
    pairs: tuple[Pair, ...]
    clusters: tuple[Cluster, ...]
    golden: tuple[GoldenRecord, ...]

    @property
    def auto_pairs(self) -> tuple[Pair, ...]:
        return tuple(p for p in self.pairs if p.band is Band.AUTO)

    @property
    def review_pairs(self) -> tuple[Pair, ...]:
        return tuple(p for p in self.pairs if p.band is Band.REVIEW)
