"""Tests for the extraction package: base types, PDF extractor, cloud seam."""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from constituent_reconciler.extract.seam import BedrockSeam, LocalSeam, NoOpSeam, make_seam
from constituent_reconciler.models import SourceSpan

pdfplumber = pytest.importorskip("pdfplumber", reason="pdfplumber not installed")


# ---------------------------------------------------------------------------
# SourceSpan
# ---------------------------------------------------------------------------


def test_source_span_str_format() -> None:
    span = SourceSpan(source_file="form.pdf", page=2, x0=72.0, top=300.0, x1=200.0, bottom=312.0)
    assert str(span) == "form.pdf:p2:x=72-200,y=300-312"


def test_source_span_is_hashable() -> None:
    span = SourceSpan("a.pdf", 1, 0.0, 0.0, 100.0, 12.0)
    assert {span} == {span}


def test_source_span_is_frozen() -> None:
    span = SourceSpan("a.pdf", 1, 0.0, 0.0, 100.0, 12.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        span.page = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Page-level confidence heuristic
# ---------------------------------------------------------------------------


def test_empty_page_has_zero_confidence() -> None:
    from constituent_reconciler.extract.pdf import _page_confidence

    assert _page_confidence("") == 0.0
    assert _page_confidence("   ") == 0.0


def test_short_page_is_low_confidence() -> None:
    from constituent_reconciler.extract.pdf import _page_confidence

    # Fewer than _MIN_WORDS (5) words scores below 0.5.
    score = _page_confidence("Hi")
    assert score < 0.5


def test_garbled_page_is_low_confidence() -> None:
    from constituent_reconciler.extract.pdf import _page_confidence

    # Average word > 15 chars triggers the garbled-OCR heuristic.
    garbled = " ".join(["XYZABCDEFGHIJKLMNOP"] * 20)  # avg len = 18
    assert _page_confidence(garbled) < 0.5


def test_normal_page_reaches_full_confidence() -> None:
    from constituent_reconciler.extract.pdf import _page_confidence

    normal = "First Name: Alice\nLast Name: Walker\nDOB: 1970-05-12\nEmail: a@b.co\nPhone: 555"
    assert _page_confidence(normal) == 1.0


# ---------------------------------------------------------------------------
# PDF extraction (requires pdfplumber + conftest PDF fixture)
# ---------------------------------------------------------------------------


def test_extract_pdf_finds_all_fields(intake_pdf: Path) -> None:
    from constituent_reconciler.extract.base import ExtractionResult
    from constituent_reconciler.extract.pdf import extract_pdf

    result = extract_pdf(intake_pdf)
    assert isinstance(result, ExtractionResult)
    assert len(result.pages) == 1
    page = result.pages[0]
    by_field = {f.field_name: f.value for f in page.fields}
    assert by_field.get("first_name") == "Alice"
    assert by_field.get("last_name") == "Walker"
    assert by_field.get("dob") == "1970-05-12"
    assert by_field.get("email") == "alice@example.org"
    assert by_field.get("phone") == "555-123-4567"


def test_extract_pdf_page_has_full_confidence(intake_pdf: Path) -> None:
    from constituent_reconciler.extract.pdf import extract_pdf

    result = extract_pdf(intake_pdf)
    page = result.pages[0]
    assert page.confidence == 1.0
    for ef in page.fields:
        assert ef.confidence == page.confidence


def test_extract_pdf_low_confidence_page_is_flagged(low_confidence_pdf: Path) -> None:
    from constituent_reconciler.extract.pdf import extract_pdf

    result = extract_pdf(low_confidence_pdf)
    assert len(result.pages) == 1
    assert result.pages[0].confidence < 0.5
    assert result.low_confidence_pages(threshold=0.5) == result.pages


def test_extracted_field_span_is_none_or_source_span(intake_pdf: Path) -> None:
    from constituent_reconciler.extract.pdf import extract_pdf

    result = extract_pdf(intake_pdf)
    for page in result.pages:
        for ef in page.fields:
            assert ef.span is None or isinstance(ef.span, SourceSpan)


# ---------------------------------------------------------------------------
# Cloud seam policy gate
# ---------------------------------------------------------------------------


def test_no_op_seam_is_always_disabled() -> None:
    seam = NoOpSeam()
    assert seam.is_enabled() is False


def test_no_op_seam_refine_returns_empty() -> None:
    seam = NoOpSeam()
    assert seam.refine(Path("any.pdf"), 1) == []


def test_dv_pack_forces_no_op_seam() -> None:
    seam = make_seam("dv", backend="bedrock")
    assert isinstance(seam, NoOpSeam)


def test_hipaa_pack_forces_no_op_seam() -> None:
    seam = make_seam("hipaa", backend="bedrock")
    assert isinstance(seam, NoOpSeam)


def test_default_pack_none_backend_returns_no_op() -> None:
    seam = make_seam("default", backend="none")
    assert isinstance(seam, NoOpSeam)


def test_default_pack_bedrock_backend_returns_bedrock_seam() -> None:
    seam = make_seam("default", backend="bedrock")
    assert isinstance(seam, BedrockSeam)


# ---------------------------------------------------------------------------
# BedrockSeam.refine: Converse response parsing and fault tolerance
# ---------------------------------------------------------------------------


def _converse_response(
    text: str,
    *,
    with_usage: bool = False,
    usage: dict[str, object] | None = None,
) -> dict[str, Any]:
    """A minimal Bedrock Converse response carrying one text block."""
    response: dict[str, Any] = {"output": {"message": {"content": [{"text": text}]}}}
    if with_usage:
        response.update(
            {
                "usage": usage if usage is not None else {"inputTokens": 120, "outputTokens": 24},
                "stopReason": "end_turn",
            }
        )
    return response


class _StubBedrockClient:
    """Fake bedrock-runtime client: records converse() calls, returns a canned
    response or raises a canned error."""

    def __init__(
        self,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _fake_page_to_png(path: Path, page_num: int) -> bytes:
    return b"png-bytes"


@pytest.fixture()
def stub_page_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid rendering a real PDF: refine() gets deterministic PNG bytes."""
    import constituent_reconciler.extract.seam as seam_mod

    monkeypatch.setattr(seam_mod, "_page_to_png", _fake_page_to_png)


def test_bedrock_refine_parses_converse_response(stub_page_render: None) -> None:
    client = _StubBedrockClient(
        response=_converse_response(
            '{"fields": ['
            '{"name": "first_name", "value": " Alice ", "confidence": 0.9},'
            '{"name": "email", "value": "alice@example.org", "confidence": 1.4}'
            "]}"
        )
    )
    seam = BedrockSeam(model_id="test-model", client=client)
    fields = seam.refine(Path("form.pdf"), 1)

    assert [(f.field_name, f.value) for f in fields] == [
        ("first_name", "Alice"),
        ("email", "alice@example.org"),
    ]
    assert fields[0].confidence == 0.9
    assert fields[1].confidence == 1.0  # clamped to [0, 1]

    # The Converse call carried the configured model id and the page image.
    call = client.calls[0]
    assert call["modelId"] == "test-model"
    image_block = call["messages"][0]["content"][0]
    assert image_block["image"]["format"] == "png"
    assert image_block["image"]["source"]["bytes"] == b"png-bytes"


def test_bedrock_refine_emits_canonical_pii_free_telemetry(
    stub_page_render: None, caplog: pytest.LogCaptureFixture
) -> None:
    from constituent_reconciler._vendor.genai_telemetry.attributes import (
        GEN_AI_REQUEST_MODEL,
        GEN_AI_SYSTEM,
        GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS,
        GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,
        GEN_AI_USAGE_INPUT_TOKENS,
        METRIC_OPERATION_DURATION,
        PORTFOLIO_COST_USD,
    )

    response = _converse_response(
        '{"fields": []}',
        with_usage=True,
        usage={
            "inputTokens": 120,
            "outputTokens": 24,
            "cacheWriteInputTokens": 30,
            "cacheReadInputTokens": 50,
        },
    )
    with caplog.at_level(logging.INFO, logger="constituent_reconciler"):
        BedrockSeam(client=_StubBedrockClient(response=response)).refine(Path("private.pdf"), 1)
    event = json.loads(caplog.records[-1].message)
    assert event[GEN_AI_SYSTEM] == "aws.bedrock"
    assert event[GEN_AI_REQUEST_MODEL] == "us.anthropic.claude-sonnet-4-6"
    assert event[GEN_AI_USAGE_INPUT_TOKENS] == 200
    assert event[GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS] == 30
    assert event[GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS] == 50
    assert event[PORTFOLIO_COST_USD] == 0.000932
    assert event[METRIC_OPERATION_DURATION] >= 0
    assert "private.pdf" not in caplog.records[-1].message


@pytest.mark.parametrize(
    "invalid_usage",
    [
        {"inputTokens": True, "outputTokens": 24},
        {"inputTokens": -1, "outputTokens": 24},
        {"inputTokens": 120, "outputTokens": 24, "cacheWriteInputTokens": -1},
        {"inputTokens": 120, "outputTokens": 24, "cacheReadInputTokens": "1"},
    ],
)
def test_bedrock_invalid_usage_is_not_emitted(
    stub_page_render: None,
    caplog: pytest.LogCaptureFixture,
    invalid_usage: dict[str, object],
) -> None:
    from constituent_reconciler._vendor.genai_telemetry.attributes import (
        GEN_AI_USAGE_INPUT_TOKENS,
    )

    response = _converse_response(
        '{"fields": [{"name": "first_name", "value": "Alice", "confidence": 0.8}]}',
        with_usage=True,
        usage=invalid_usage,
    )
    with caplog.at_level(logging.INFO, logger="constituent_reconciler"):
        fields = BedrockSeam(client=_StubBedrockClient(response=response)).refine(
            Path("private.pdf"), 1
        )
    assert [(field.field_name, field.value) for field in fields] == [("first_name", "Alice")]
    event = json.loads(caplog.records[-1].message)
    assert GEN_AI_USAGE_INPUT_TOKENS not in event


def test_bedrock_reflected_finish_reason_is_not_emitted(
    stub_page_render: None, caplog: pytest.LogCaptureFixture
) -> None:
    from constituent_reconciler._vendor.genai_telemetry.attributes import (
        GEN_AI_RESPONSE_FINISH_REASONS,
    )

    response = _converse_response('{"fields": []}', with_usage=True)
    response["stopReason"] = "PLANTED-PII-alice@example.org"
    with caplog.at_level(logging.INFO, logger="constituent_reconciler"):
        BedrockSeam(client=_StubBedrockClient(response=response)).refine(Path("private.pdf"), 1)
    event = json.loads(caplog.records[-1].message)
    assert GEN_AI_RESPONSE_FINISH_REASONS not in event
    assert "PLANTED-PII" not in caplog.records[-1].message


def test_bedrock_refine_fenced_json_response_parses(stub_page_render: None) -> None:
    fenced = (
        '```json\n{"fields": [{"name": "phone", "value": "555-123-4567", "confidence": 0.7}]}\n```'
    )
    client = _StubBedrockClient(response=_converse_response(fenced))
    seam = BedrockSeam(client=client)
    fields = seam.refine(Path("form.pdf"), 1)
    assert [(f.field_name, f.value, f.confidence) for f in fields] == [
        ("phone", "555-123-4567", 0.7)
    ]


def test_bedrock_refine_malformed_json_returns_empty(stub_page_render: None) -> None:
    client = _StubBedrockClient(response=_converse_response("Sorry, I cannot help with that."))
    seam = BedrockSeam(client=client)
    assert seam.refine(Path("form.pdf"), 1) == []


def test_bedrock_refine_wrong_shape_returns_empty(stub_page_render: None) -> None:
    # Valid JSON, but not the {"fields": [...]} contract.
    client = _StubBedrockClient(response=_converse_response('{"answer": 42}'))
    seam = BedrockSeam(client=client)
    assert seam.refine(Path("form.pdf"), 1) == []


def test_bedrock_refine_skips_malformed_entries(stub_page_render: None) -> None:
    client = _StubBedrockClient(
        response=_converse_response(
            '{"fields": ['
            '{"name": "first_name", "value": "Alice", "confidence": 0.8},'
            '{"name": "", "value": "x", "confidence": 0.8},'
            '{"name": "email", "value": "  ", "confidence": 0.8},'
            '{"name": "phone", "value": "555", "confidence": "high"},'
            '"not-a-dict"'
            "]}"
        )
    )
    seam = BedrockSeam(client=client)
    fields = seam.refine(Path("form.pdf"), 1)
    assert [(f.field_name, f.value) for f in fields] == [("first_name", "Alice")]


def test_bedrock_refine_client_error_log_is_content_free(
    stub_page_render: None, caplog: pytest.LogCaptureFixture
) -> None:
    client = _StubBedrockClient(error=RuntimeError("PLANTED-PII-alice@example.org"))
    seam = BedrockSeam(client=client)
    with caplog.at_level(logging.WARNING, logger="constituent_reconciler.extract.seam"):
        assert seam.refine(Path("private-alice.pdf"), 1) == []
    assert "private-alice.pdf" not in caplog.text
    assert "PLANTED-PII" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_bedrock_refine_without_client_returns_empty() -> None:
    seam = BedrockSeam()
    assert seam.refine(Path("form.pdf"), 1) == []


def test_bedrock_seam_with_injected_client_is_enabled() -> None:
    seam = BedrockSeam(client=_StubBedrockClient(response=_converse_response("{}")))
    assert seam.is_enabled() is True


# ---------------------------------------------------------------------------
# Pipeline integration: PDF records carry source spans in the review queue
# ---------------------------------------------------------------------------


def test_read_pdf_records_produces_records_with_correct_fields(intake_pdf: Path) -> None:
    from dataclasses import replace

    from constituent_reconciler.config import ExtractConfig, load_recipe
    from constituent_reconciler.pipeline import read_pdf_records

    recipe = load_recipe(
        Path(__file__).resolve().parents[1] / "examples" / "intake-demo" / "recipe.toml"
    )
    recipe = replace(recipe, extract=ExtractConfig(backend="pdfplumber"))

    records = read_pdf_records(intake_pdf, "incoming", recipe=recipe, id_prefix="N")
    assert len(records) == 1
    rec = records[0]
    assert rec.raw.get("first_name") == "Alice"
    assert rec.raw.get("last_name") == "Walker"
    assert rec.source == "incoming"
    assert rec.unique_id.startswith("N")
    assert isinstance(rec.spans, dict)


def test_review_queue_includes_span_columns_when_records_have_spans(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from constituent_reconciler.config import ExtractConfig, load_recipe
    from constituent_reconciler.models import Band, Pair, Record, RunResult
    from constituent_reconciler.pipeline import _write_review_queue

    recipe = load_recipe(
        Path(__file__).resolve().parents[1] / "examples" / "intake-demo" / "recipe.toml"
    )
    recipe = replace(recipe, extract=ExtractConfig(backend="pdfplumber"))

    span = SourceSpan(source_file="form.pdf", page=1, x0=72.0, top=300.0, x1=200.0, bottom=312.0)
    left = Record(
        unique_id="N0001",
        source="incoming",
        raw={"first_name": "Alice", "last_name": "Walker"},
        spans={"first_name": span},
    )
    right = Record(
        unique_id="E0001",
        source="existing",
        raw={"first_name": "Alice", "last_name": "Walker"},
    )
    pair = Pair("N0001", "E0001", 0.85, Band.REVIEW)
    result = RunResult(
        records={"N0001": left, "E0001": right},
        pairs=(pair,),
        clusters=(),
        golden=(),
    )

    review_path = _write_review_queue(result, recipe, tmp_path)
    content = review_path.read_text(encoding="utf-8")
    header = content.splitlines()[0]
    assert "first_name_left_span" in header
    assert "first_name_right_span" in header
    assert "form.pdf:p1" in content


# --- LocalSeam: loopback-only, model-assisted extraction without egress ---


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    """Loopback stand-in for Ollama: /api/tags and /api/generate."""

    generate_body: bytes = b"{}"

    def do_GET(self) -> None:  # noqa: N802 - http.server API name.
        body = b'{"models": []}' if self.path == "/api/tags" else b"{}"
        self._reply(body)

    def do_POST(self) -> None:  # noqa: N802 - http.server API name.
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self._reply(self.generate_body if self.path == "/api/generate" else b"{}")

    def _reply(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


@contextmanager
def _fake_ollama(generate_body: bytes) -> Iterator[str]:
    handler = type("_Handler", (_FakeOllamaHandler,), {"generate_body": generate_body})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_local_seam_rejects_non_loopback_host() -> None:
    with pytest.raises(ValueError, match="loopback"):
        LocalSeam(host="http://model-server.internal:11434")


def test_local_seam_accepts_each_loopback_host() -> None:
    for host in ("http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"):
        LocalSeam(host=host)


def test_dv_and_hipaa_local_backend_returns_no_op() -> None:
    assert isinstance(make_seam("dv", backend="local"), NoOpSeam)
    assert isinstance(make_seam("hipaa", backend="local"), NoOpSeam)


def test_dv_local_override_returns_local_seam() -> None:
    seam = make_seam("dv", backend="local", local_model_override=True)
    assert isinstance(seam, LocalSeam)


def test_default_pack_local_backend_needs_no_override() -> None:
    assert isinstance(make_seam("default", backend="local"), LocalSeam)


def test_local_seam_refine_parses_fields_from_fake_server(intake_pdf: Path) -> None:
    model_reply = json.dumps(
        {
            "response": json.dumps(
                {
                    "fields": [
                        {"name": "first_name", "value": "Alice"},
                        {"name": "email", "value": " alice@example.org "},
                        {"name": "not_a_field", "value": "x"},
                        {"name": "phone", "value": ""},
                        "not-a-dict",
                    ]
                }
            )
        }
    ).encode("utf-8")
    with _fake_ollama(model_reply) as host:
        seam = LocalSeam(host=host)
        assert seam.is_enabled() is True
        fields = seam.refine(intake_pdf, 1)
    assert [(f.field_name, f.value) for f in fields] == [
        ("first_name", "Alice"),
        ("email", "alice@example.org"),
    ]


@pytest.mark.parametrize(
    "invalid_usage",
    [
        {"prompt_eval_count": True, "eval_count": 2},
        {"prompt_eval_count": -1, "eval_count": 2},
        {"prompt_eval_count": 4, "eval_count": False},
        {"prompt_eval_count": 4, "eval_count": -2},
    ],
)
def test_local_seam_invalid_usage_and_reflected_finish_reason_are_not_emitted(
    intake_pdf: Path,
    caplog: pytest.LogCaptureFixture,
    invalid_usage: dict[str, object],
) -> None:
    from constituent_reconciler._vendor.genai_telemetry.attributes import (
        GEN_AI_RESPONSE_FINISH_REASONS,
        GEN_AI_USAGE_INPUT_TOKENS,
    )

    model_reply = json.dumps(
        {
            "response": '{"fields": []}',
            "done_reason": "PLANTED-PII-alice@example.org",
            **invalid_usage,
        }
    ).encode("utf-8")
    with _fake_ollama(model_reply) as host:
        with caplog.at_level(logging.INFO, logger="constituent_reconciler"):
            LocalSeam(host=host).refine(intake_pdf, 1)
    event = json.loads(caplog.records[-1].message)
    assert GEN_AI_USAGE_INPUT_TOKENS not in event
    assert GEN_AI_RESPONSE_FINISH_REASONS not in event
    assert "PLANTED-PII" not in caplog.records[-1].message


def test_local_seam_error_log_is_content_free(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import constituent_reconciler.extract.seam as seam_mod

    seam = LocalSeam()

    def fail_request(path: str, payload: dict[str, object] | None = None) -> dict[str, Any]:
        raise OSError("PLANTED-PII-alice@example.org")

    monkeypatch.setattr(seam_mod, "_page_text", lambda path, page_num: "private intake text")
    monkeypatch.setattr(seam, "_json_request", fail_request)
    with caplog.at_level(logging.WARNING, logger="constituent_reconciler.extract.seam"):
        assert seam.refine(Path("private-alice.pdf"), 1) == []
    assert "private-alice.pdf" not in caplog.text
    assert "PLANTED-PII" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_local_seam_server_down_is_disabled_and_refine_returns_empty(
    intake_pdf: Path,
) -> None:
    with _fake_ollama(b"{}") as host:
        pass  # The server is shut down when the context exits.
    seam = LocalSeam(host=host, timeout=0.5)
    assert seam.is_enabled() is False
    assert seam.refine(intake_pdf, 1) == []


def test_local_seam_malformed_model_json_returns_empty(intake_pdf: Path) -> None:
    model_reply = json.dumps({"response": "not json at all {"}).encode("utf-8")
    with _fake_ollama(model_reply) as host:
        seam = LocalSeam(host=host)
        assert seam.refine(intake_pdf, 1) == []
