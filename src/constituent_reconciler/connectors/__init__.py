"""Destination connectors for resolved records."""

from __future__ import annotations

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
from constituent_reconciler.connectors.csv_out import CsvConnector
from constituent_reconciler.connectors.webhook import (
    WebhookConfig,
    WebhookConnector,
)

__all__ = [
    "WRITE_ACTIONS",
    "Connector",
    "ConnectorError",
    "WriteResult",
    "CsvConnector",
    "CivicrmConnector",
    "CivicrmConfig",
    "Transport",
    "UrllibTransport",
    "WebhookConnector",
    "WebhookConfig",
]
