"""Destination connectors for resolved records.

This package also holds the connector registry: a mapping from the name a
recipe's ``[output] connector`` key uses to a factory that builds the
connector from the recipe's :class:`OutputConfig`. ``pipeline.build_connector``
resolves names through :func:`get_factory`, so adding a destination means one
new module plus one :func:`register` call here, not an edit to the
orchestrator. Every registered connector must pass the conformance suite in
``tests/test_connector_conformance.py``.

Factories take ``(output, out_dir, transports)``. ``transports`` maps a
connector name to an injected transport for testing; a factory that needs no
transport ignores it, and a network factory falls back to its default
transport when the mapping has no entry for its name.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from constituent_reconciler.config import OutputConfig
from constituent_reconciler.connectors.base import (
    WRITE_ACTIONS,
    Connector,
    ConnectorError,
    WriteResult,
)
from constituent_reconciler.connectors.civicrm import (
    CivicrmConfig,
    CivicrmConnector,
    Transport,
    UrllibTransport,
)
from constituent_reconciler.connectors.crm_csv import (
    CIVICRM_IMPORT_MAP,
    SALESFORCE_IMPORT_MAP,
    CrmCsvConnector,
)
from constituent_reconciler.connectors.csv_out import CsvConnector
from constituent_reconciler.connectors.salesforce import (
    SalesforceConfig,
    SalesforceConnector,
)
from constituent_reconciler.connectors.salesforce import (
    Transport as SalesforceTransport,
)

__all__ = [
    "WRITE_ACTIONS",
    "Connector",
    "ConnectorError",
    "WriteResult",
    "CsvConnector",
    "CrmCsvConnector",
    "CivicrmConnector",
    "CivicrmConfig",
    "SalesforceConnector",
    "SalesforceConfig",
    "Transport",
    "UrllibTransport",
    "ConnectorFactory",
    "CONNECTOR_REGISTRY",
    "register",
    "get_factory",
]

# A factory builds one connector from the recipe's output section. ``out_dir``
# is where a local connector places its file; a network connector ignores it.
ConnectorFactory = Callable[[OutputConfig, Path, Mapping[str, object]], Connector]

CONNECTOR_REGISTRY: dict[str, ConnectorFactory] = {}


def register(name: str) -> Callable[[ConnectorFactory], ConnectorFactory]:
    """Register a factory under the name a recipe's ``[output]`` section uses.

    Registering a name twice raises immediately: a silent overwrite could swap
    a local target for a network one, and the DV pack's no-egress guarantee
    leans on connector identity being unambiguous.
    """

    def decorate(factory: ConnectorFactory) -> ConnectorFactory:
        if name in CONNECTOR_REGISTRY:
            raise ValueError(f"connector name already registered: {name!r}")
        CONNECTOR_REGISTRY[name] = factory
        return factory

    return decorate


def get_factory(name: str) -> ConnectorFactory:
    """Look up a connector factory by name, failing with the known names."""
    try:
        return CONNECTOR_REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(CONNECTOR_REGISTRY))
        raise ValueError(
            f"unknown output connector: {name!r} (known connectors: {known})"
        ) from None


@register("csv")
def _build_csv(output: OutputConfig, out_dir: Path, transports: Mapping[str, object]) -> Connector:
    return CsvConnector(out_dir / "resolved.csv")


@register("salesforce_csv")
def _build_salesforce_csv(
    output: OutputConfig, out_dir: Path, transports: Mapping[str, object]
) -> Connector:
    return CrmCsvConnector(
        "salesforce_csv",
        out_dir / "salesforce_import.csv",
        SALESFORCE_IMPORT_MAP,
        external_id_column=output.external_id_field,
    )


@register("civicrm_csv")
def _build_civicrm_csv(
    output: OutputConfig, out_dir: Path, transports: Mapping[str, object]
) -> Connector:
    return CrmCsvConnector(
        "civicrm_csv",
        out_dir / "civicrm_import.csv",
        CIVICRM_IMPORT_MAP,
        external_id_column=output.external_id_field,
    )


@register("civicrm")
def _build_civicrm(
    output: OutputConfig, out_dir: Path, transports: Mapping[str, object]
) -> Connector:
    config = CivicrmConfig(
        endpoint=output.endpoint,
        api_key=os.environ.get(output.auth_env, ""),
        auth_header=output.auth_header,
        auth_scheme=output.auth_scheme,
        external_id_field=output.external_id_field,
    )
    transport = cast("Transport | None", transports.get("civicrm"))
    return CivicrmConnector(config, transport=transport)


@register("salesforce")
def _build_salesforce(
    output: OutputConfig, out_dir: Path, transports: Mapping[str, object]
) -> Connector:
    config = SalesforceConfig(
        instance_url=output.endpoint,
        access_token=os.environ.get(output.auth_env, ""),
        api_version=output.api_version,
        external_id_field=output.external_id_field,
        object_name=output.object_name,
    )
    transport = cast("SalesforceTransport | None", transports.get("salesforce"))
    return SalesforceConnector(config, transport=transport)
