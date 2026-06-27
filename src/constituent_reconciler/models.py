"""Core data types for the pipeline.

These are plain dataclasses, kept free of any matcher or framework so the rest
of the code (and the tests) can reason about records, scored pairs, bands, and
clusters without importing Splink or pandas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

# Consent values that permit a record to be exported. Anything else (missing,
# "revoked", "expired", unknown) is treated as no-consent and blocks export when
# the active policy requires consent. Fail-closed by construction.
CONSENT_GRANTED: frozenset[str] = frozenset({"granted", "active", "yes", "true"})


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
    normalize step. ``consent_status`` is the raw consent token, lower-cased.
    ``spans`` maps each canonical field name to where it was found in a source
    document; empty for records read from structured CSV.
    """

    unique_id: str
    source: str
    raw: dict[str, str]
    normalized: dict[str, str] = field(default_factory=dict)
    consent_status: str = ""
    spans: dict[str, SourceSpan] = field(default_factory=dict)

    def has_consent(self) -> bool:
        return self.consent_status.strip().lower() in CONSENT_GRANTED


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

    ``fields`` are the surviving canonical values, ``primary`` is the record id
    chosen as the survivor, and ``consent`` is the export decision for the
    merged record (fail-closed: granted only when the survivor carries consent).
    """

    cluster_id: str
    members: tuple[str, ...]
    fields: dict[str, str]
    primary: str
    consent: bool


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
