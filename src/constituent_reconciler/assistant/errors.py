"""Shared exceptions for the AI assistant package.

Kept in their own module (no dependency on ``provider.py`` or the feature
modules) so any part of the package, and the CLI wiring in ``cli.py``, can
import them without pulling in the Anthropic SDK or boto3.
"""

from __future__ import annotations


class AssistantError(RuntimeError):
    """Base class for assistant-package failures."""


class ProviderNotConfigured(AssistantError):
    """No usable model provider is available (no credentials, no SDK installed).

    Raised before any prompt is built. The caller (CLI command or review
    server route) is expected to report this as an ordinary, expected
    condition -- the AI layer is optional by design -- not a crash.
    """


class ProviderCallFailed(AssistantError):
    """The provider was reachable but the call itself failed.

    Wraps the underlying SDK/boto3 exception so callers never need to catch
    provider-specific exception types. The deterministic pipeline is
    unaffected by this exception by construction: nothing in
    ``pipeline.py``, ``decisions.py``, or the ``run``/``review``/``apply``
    CLI commands calls into the assistant package at all.
    """


class RateLimitExceeded(AssistantError):
    """A per-session or daily AI call budget was exceeded.

    The caller must fail closed on the AI feature only: return the response
    a human would get from a normal rate limit (for the CLI, a clear message
    and a non-zero exit code; for the review server, HTTP 429), and leave
    every deterministic route (``run``, ``review``, ``apply``, and the
    review server's own decision-saving endpoints) completely unaffected.
    """


class VerificationFailed(AssistantError):
    """An AI claim could not be verified against real evidence and was withheld.

    Not necessarily an error condition end-to-end -- callers generally catch
    this per-claim and drop the unverifiable claim rather than propagate the
    exception, per the "unverifiable claims withheld and counted" design.
    It is a real exception type (not a sentinel return value) so a caller
    that forgets to handle withholding fails loudly instead of silently
    showing an unverified claim.
    """


class SourceDocumentUnavailable(AssistantError):
    """A field has a source span, but the document it points into could not be read.

    Raised rather than returned as ``None`` because the two conditions are
    not the same. A field with no span at all (a CSV-sourced record, or a
    field the extractor never located) has no source text by construction,
    and skipping it is correct. A field whose span names a document that
    cannot be found or read is a broken run: the grounding evidence the
    proposal path exists to quote against is missing, and continuing would
    write an empty draft and exit 0 as though the model had simply nothing
    to say.
    """
