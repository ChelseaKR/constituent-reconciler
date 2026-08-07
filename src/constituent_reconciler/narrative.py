"""Plain-language narrative run summary for boards and funders.

Renders a one-page Markdown summary of a completed run from the artifacts
``reconcile run`` writes: ``run_summary.json`` and, when the active policy pack
produces one, ``aggregate_summary.json``. The page carries counts only. No
name, field value, or record identifier appears in the output, so an executive
director can hand it to a board without a privacy review of the page itself.

English and Spanish render from the same data and the same section structure,
via the ``_STRINGS`` table, so the two languages stay at parity.
"""

from __future__ import annotations

from collections.abc import Mapping

from constituent_reconciler.suppression import SUPPRESSED

LANGUAGES: tuple[str, ...] = ("en", "es")

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "title": "# Reconciliation run summary",
        "intro": (
            "This page describes one run of the constituent-reconciler pipeline "
            "in plain language. It reports counts only; no personal detail or "
            "record identifier appears on this page."
        ),
        "h_in": "## What came in",
        "records_in": ("- Records read from intake files and existing records: **{n}**"),
        "candidate_pairs": ("- Pairs of records that might describe the same person: **{n}**"),
        "h_merge": "## What merged automatically and what went to a person",
        "auto_pairs": "- Pairs merged automatically at high confidence: **{n}**",
        "review_pairs": "- Uncertain pairs sent to a human reviewer: **{n}**",
        "resolved": "- Records after merging: **{n}**",
        "merged_records": "- Records formed by combining duplicates: **{n}**",
        "merge_rule": (
            "The pipeline never merges an uncertain pair on its own. A person "
            "decides each one, so nothing merges silently."
        ),
        "h_withheld": "## What was withheld and why",
        "withheld_count": ("- Records withheld because consent was not granted: **{n}**"),
        "withheld_rule": (
            "A record without granted, current consent is never written to the "
            "output. Any consent status other than granted blocks the write. "
            "Withheld records are counted by number only, never by name or "
            "identifier."
        ),
        "withheld_none": (
            "No record was withheld in this run. A record without granted, "
            "current consent would have been held back from the output; when in "
            "doubt, the write is blocked."
        ),
        "consent_not_required": (
            "This run did not enforce a consent requirement (policy pack "
            "`{pack}`), so no record was withheld on consent grounds. Under a "
            "consent-requiring pack, a record without granted consent is held "
            "back from the output."
        ),
        "dv_note": (
            "The run used the `dv` privacy pack, under which no client "
            "information leaves the machine: cloud extraction is disabled and "
            "write targets must be local."
        ),
        "h_aggregate": "## Shareable counts, small groups hidden",
        "aggregate_intro": (
            "The counts below are the run's shareable aggregate. They contain "
            'no field values. A count between 1 and 10 is replaced with "{label}" '
            "so no small group of people can be picked out, following the U.S. "
            "CMS small-cell suppression rule. A zero stays visible because it "
            "reveals no one."
        ),
        "aggregate_total": "- Resolved records in the aggregate: **{n}**",
        "aggregate_total_suppressed": (
            "- Resolved records in the aggregate: **{label}** (publishing the "
            "real count would let a hidden cell below be recovered by "
            "subtraction)"
        ),
        "suppressed_label": "suppressed",
        "h_caveat": "## Standing caveat",
        "caveat": (
            "This is a reference implementation, not legal advice. An "
            "organization adopting it needs its own review against its own "
            "obligations."
        ),
    },
    "es": {
        "title": "# Resumen de la ejecución de reconciliación",
        "intro": (
            "Esta página describe en lenguaje llano una ejecución del proceso "
            "constituent-reconciler. Contiene únicamente conteos; en ella no "
            "aparece ningún dato personal ni identificador de registro."
        ),
        "h_in": "## Qué ingresó",
        "records_in": (
            "- Registros leídos de los archivos de admisión y de los registros existentes: **{n}**"
        ),
        "candidate_pairs": (
            "- Pares de registros que podrían describir a la misma persona: **{n}**"
        ),
        "h_merge": "## Qué se fusionó automáticamente y qué pasó a una persona",
        "auto_pairs": ("- Pares fusionados automáticamente con alta confianza: **{n}**"),
        "review_pairs": ("- Pares inciertos enviados a una persona revisora: **{n}**"),
        "resolved": "- Registros después de la fusión: **{n}**",
        "merged_records": "- Registros formados al combinar duplicados: **{n}**",
        "merge_rule": (
            "El proceso nunca fusiona por su cuenta un par incierto. Una persona "
            "decide cada uno, así que nada se fusiona en silencio."
        ),
        "h_withheld": "## Qué se retuvo y por qué",
        "withheld_count": (
            "- Registros retenidos porque el consentimiento no fue otorgado: **{n}**"
        ),
        "withheld_rule": (
            "Un registro sin consentimiento otorgado y vigente nunca se escribe "
            "en la salida. Cualquier estado de consentimiento distinto de "
            "otorgado bloquea la escritura. Los registros retenidos se cuentan "
            "solo por número, nunca por nombre ni identificador."
        ),
        "withheld_none": (
            "Ningún registro fue retenido en esta ejecución. Un registro sin "
            "consentimiento otorgado y vigente se habría excluido de la salida; "
            "ante la duda, la escritura se bloquea."
        ),
        "consent_not_required": (
            "Esta ejecución no aplicó un requisito de consentimiento (paquete "
            "de política `{pack}`), así que ningún registro fue retenido por "
            "motivos de consentimiento. Bajo un paquete que exige "
            "consentimiento, un registro sin consentimiento otorgado se excluye "
            "de la salida."
        ),
        "dv_note": (
            "La ejecución usó el paquete de privacidad `dv`, bajo el cual "
            "ninguna información de clientes sale de la máquina: la extracción "
            "en la nube está desactivada y los destinos de escritura deben ser "
            "locales."
        ),
        "h_aggregate": "## Conteos compartibles, con grupos pequeños ocultos",
        "aggregate_intro": (
            "Los conteos siguientes son el agregado compartible de la "
            "ejecución. No contienen valores de campos. Un conteo entre 1 y 10 "
            "se reemplaza por «{label}» para que ningún grupo pequeño de "
            "personas pueda ser señalado, según la regla de supresión de celdas "
            "pequeñas de los CMS de EE. UU. Un cero permanece visible porque no "
            "revela a nadie."
        ),
        "aggregate_total": "- Registros resueltos en el agregado: **{n}**",
        "aggregate_total_suppressed": (
            "- Registros resueltos en el agregado: **{label}** (publicar el "
            "conteo real permitiría recuperar por resta una celda oculta a "
            "continuación)"
        ),
        "suppressed_label": "suprimido",
        "h_caveat": "## Advertencia permanente",
        "caveat": (
            "Esta es una implementación de referencia, no asesoría legal. Una "
            "organización que la adopte necesita su propia revisión frente a "
            "sus propias obligaciones."
        ),
    },
}

# Labels for the breakdown and category names that appear in
# aggregate_summary.json. An unknown key falls through unchanged rather than
# failing, so a new breakdown never breaks report rendering.
_CELL_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "consent": "consent",
        "resolution": "resolution",
        "granted": "granted",
        "withheld": "withheld",
        "merged": "merged",
        "singleton": "single records",
    },
    "es": {
        "consent": "consentimiento",
        "resolution": "resolución",
        "granted": "otorgado",
        "withheld": "retenido",
        "merged": "fusionados",
        "singleton": "registros únicos",
    },
}


def _int(summary: Mapping[str, object], key: str) -> int:
    value = summary.get(key, 0)
    return value if isinstance(value, int) else 0


def _render_aggregate(
    aggregate: Mapping[str, object], strings: dict[str, str], labels: dict[str, str]
) -> list[str]:
    total = aggregate.get("total_resolved", 0)
    if total == SUPPRESSED:
        # A suppressed total is not "no records": it is a real count the report
        # is withholding because publishing it would hand back a hidden cell
        # from one of the breakdowns below. _int's fallback-to-0 exists for a
        # missing or malformed key, not this, and rendering "0" here would say
        # something false rather than something withheld.
        total_line = strings["aggregate_total_suppressed"].format(label=strings["suppressed_label"])
    else:
        total_line = strings["aggregate_total"].format(n=_int(aggregate, "total_resolved"))
    lines = [total_line]
    breakdowns = aggregate.get("breakdowns")
    if not isinstance(breakdowns, Mapping):
        return lines
    for name, cells in breakdowns.items():
        if not isinstance(cells, Mapping):
            continue
        parts = []
        for key, value in cells.items():
            shown = strings["suppressed_label"] if value == SUPPRESSED else str(value)
            parts.append(f"{labels.get(str(key), str(key))} {shown}")
        lines.append(f"- {labels.get(str(name), str(name))}: " + ", ".join(parts))
    return lines


def render_narrative(
    result_summary: Mapping[str, object],
    aggregate: Mapping[str, object] | None = None,
    *,
    lang: str = "en",
) -> str:
    """Render the one-page narrative summary as Markdown.

    ``result_summary`` is the parsed ``run_summary.json`` a run writes;
    ``aggregate`` is the parsed ``aggregate_summary.json`` when the run produced
    one (the DV pack does), else ``None``. Both carry counts only, so the
    rendered page cannot contain a name, field value, or record identifier.
    """

    if lang not in _STRINGS:
        expected = ", ".join(sorted(_STRINGS))
        raise ValueError(f"unsupported language: {lang!r} (expected one of: {expected})")
    strings = _STRINGS[lang]
    pack = str(result_summary.get("policy_pack", "default"))
    consent_required = bool(result_summary.get("consent_required", False))
    withheld = _int(result_summary, "withheld_no_consent")

    lines: list[str] = [
        strings["title"],
        "",
        strings["intro"],
        "",
        strings["h_in"],
        "",
        strings["records_in"].format(n=_int(result_summary, "records_in")),
        strings["candidate_pairs"].format(n=_int(result_summary, "candidate_pairs")),
        "",
        strings["h_merge"],
        "",
        strings["auto_pairs"].format(n=_int(result_summary, "auto_merged_pairs")),
        strings["review_pairs"].format(n=_int(result_summary, "review_pairs")),
        strings["resolved"].format(n=_int(result_summary, "resolved_records")),
        strings["merged_records"].format(n=_int(result_summary, "merged_records")),
        "",
        strings["merge_rule"],
        "",
        strings["h_withheld"],
        "",
    ]
    if consent_required:
        lines.append(strings["withheld_count"].format(n=withheld))
        lines.append("")
        lines.append(strings["withheld_rule"] if withheld else strings["withheld_none"])
    else:
        lines.append(strings["consent_not_required"].format(pack=pack))
    if pack == "dv":
        lines += ["", strings["dv_note"]]
    if aggregate is not None:
        lines += [
            "",
            strings["h_aggregate"],
            "",
            strings["aggregate_intro"].format(label=strings["suppressed_label"]),
            "",
        ]
        lines += _render_aggregate(aggregate, strings, _CELL_LABELS[lang])
    lines += ["", strings["h_caveat"], "", strings["caveat"]]
    return "\n".join(lines) + "\n"
