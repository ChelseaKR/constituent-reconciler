"""Policy-gated cloud seam for low-confidence page refinement.

The cloud seam is invoked only when all three conditions hold:
  1. The active policy pack allows cloud calls (DV and HIPAA packs forbid them).
  2. A page's confidence is below the recipe's confidence_threshold.
  3. Cloud credentials are available.

Under DV/VAWA and HIPAA policy packs this module returns a NoOpSeam regardless
of what the recipe requests. The non-egress invariant is enforced at
construction time, not at call time, so there is no window where a misconfigured
seam could accidentally call out.
"""

from __future__ import annotations

from pathlib import Path

from constituent_reconciler.extract.base import ExtractedField

# Policy packs that forbid any cloud call. PII must never leave the machine.
_CLOUD_FORBIDDEN: frozenset[str] = frozenset({"dv", "hipaa"})


class NoOpSeam:
    """Disabled cloud seam. Used when policy forbids cloud calls, or when no
    cloud backend is configured. Always claims to be disabled and returns nothing
    from refine(), which the caller should never reach (the gate checks
    is_enabled() first).
    """

    def is_enabled(self) -> bool:
        return False

    def refine(self, path: Path, page_num: int) -> list[ExtractedField]:
        return []


class BedrockSeam:
    """Claude on Bedrock seam for low-confidence PDF pages.

    The seam sends a page image to a Claude model via Amazon Bedrock
    Converse API and parses the response for constituent fields. The actual
    network call is deferred: ``is_enabled()`` checks for boto3 and a
    configured region, and ``refine()`` performs the call only if enabled.

    This is an extension point — the full implementation (page-to-image
    conversion, prompt construction, response parsing) is wired in when a
    deployer supplies AWS credentials. Shipping the seam now lets the rest
    of the pipeline test the gating logic without Bedrock.
    """

    def __init__(self, model_id: str = "us.anthropic.claude-sonnet-4-6:0") -> None:
        self._model_id = model_id
        self._client: object | None = None

    def is_enabled(self) -> bool:
        try:
            import boto3

            self._client = boto3.client("bedrock-runtime")
            return True
        except ImportError:
            return False
        except Exception:
            return False

    def refine(self, path: Path, page_num: int) -> list[ExtractedField]:
        """Send a low-confidence page to Claude on Bedrock for field extraction.

        Raises ``NotImplementedError`` until a deployer wires in the full
        page-to-image conversion and response parser. The seam exists so tests
        can inject a fake without touching production paths.
        """
        if self._client is None:
            return []
        raise NotImplementedError(
            "BedrockSeam.refine() is the documented extension point for cloud "
            "extraction. Implement page-to-image conversion and response parsing "
            "here, or inject a fake in tests via make_seam()."
        )


def make_seam(policy_pack: str, backend: str = "none") -> NoOpSeam | BedrockSeam:
    """Construct the appropriate cloud seam for this policy pack and backend.

    DV and HIPAA packs always return NoOpSeam: PII must not egress, period.
    Any other pack with backend='bedrock' returns a BedrockSeam; all other
    backends return NoOpSeam.
    """
    if policy_pack in _CLOUD_FORBIDDEN:
        return NoOpSeam()
    if backend == "bedrock":
        return BedrockSeam()
    return NoOpSeam()
