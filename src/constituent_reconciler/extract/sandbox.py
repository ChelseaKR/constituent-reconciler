"""Sandboxed, resource-limited PDF extraction.

Attacker-supplied PDFs are the primary untrusted input surface. Parsing them
in the main process means a crafted file that hangs, balloons memory, or
crashes the parser takes the whole run down with it — and a parser exploit
would run with access to the entire constituent file.

``SandboxedExtractor`` runs the pdfplumber parse in a spawned child process
with best-effort resource caps applied inside the child (``resource.setrlimit``
on CPU seconds and address space, POSIX only) and a wall-clock timeout enforced
by the parent. Input files over a size cap are refused before any parsing
starts. Every failure mode — oversize input, timeout, nonzero exit, crash,
missing result — fails closed: the extractor returns a single zero-confidence
page with no fields plus a ``note`` explaining why, so ``read_pdf_records``
routes the document to human review instead of crashing the run.

Non-goals, stated honestly: this is containment, not a syscall sandbox. The
child runs the same interpreter with the same privileges and filesystem
access; ``RLIMIT_AS`` is not enforced on every platform (notably macOS), and
on Windows the ``resource`` module does not exist, so only the wall-clock
timeout applies there. The Docker path (see ``Dockerfile``) provides the
stronger isolation boundary for deployments that need one.
"""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path

from constituent_reconciler.extract.base import ExtractionResult, PageResult

_DEFAULT_WALL_TIMEOUT_S = 60.0
_DEFAULT_CPU_SECONDS = 30
_DEFAULT_MAX_ADDRESS_SPACE_BYTES = 1 << 30  # 1 GiB
_DEFAULT_MAX_INPUT_BYTES = 50 * 1024 * 1024  # 50 MiB

# How long the parent waits for the child to exit after it has delivered its
# result. This only cushions interpreter shutdown, not parsing work.
_JOIN_GRACE_S = 10.0

# Signature of the function run inside the child process.
WorkerTarget = Callable[[Path, Connection, int, int], None]


def _apply_resource_limits(cpu_seconds: int, max_address_space_bytes: int) -> None:
    """Best-effort rlimits inside the child. No-op where unsupported.

    The ``resource`` module is POSIX-only; on Windows this degrades gracefully
    to the parent's wall-clock timeout. Individual ``setrlimit`` calls that the
    platform refuses (macOS does not reliably enforce ``RLIMIT_AS``) are also
    skipped rather than failing the parse of a legitimate document.
    """
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows
        return
    for rlimit, cap in (
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_AS, max_address_space_bytes),
    ):
        try:
            _, hard = resource.getrlimit(rlimit)
            soft = cap if hard == resource.RLIM_INFINITY else min(cap, hard)
            resource.setrlimit(rlimit, (soft, hard))
        except (ValueError, OSError):  # pragma: no cover - platform-dependent
            continue


def _extract_in_child(
    path: Path, conn: Connection, cpu_seconds: int, max_address_space_bytes: int
) -> None:
    """Child-process entry point: cap resources, parse, pipe the result back.

    Must stay module-level so the spawn context can pickle it by reference.
    On a parse failure the exception message is sent back (so the fail-closed
    note can say why) and the child exits nonzero.
    """
    _apply_resource_limits(cpu_seconds, max_address_space_bytes)
    try:
        from constituent_reconciler.extract.pdf import PdfplumberExtractor

        result = PdfplumberExtractor().extract(path)
    except Exception as exc:
        conn.send(f"{type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc
    conn.send(result)
    conn.close()


class SandboxedExtractor:
    """Run ``PdfplumberExtractor`` in a constrained child process, fail-closed.

    Satisfies the ``Extractor`` protocol, so it drops in wherever
    ``PdfplumberExtractor`` was used directly. ``worker`` exists for tests to
    inject a misbehaving child; production callers should not pass it.
    """

    def __init__(
        self,
        *,
        wall_timeout_s: float = _DEFAULT_WALL_TIMEOUT_S,
        cpu_seconds: int = _DEFAULT_CPU_SECONDS,
        max_address_space_bytes: int = _DEFAULT_MAX_ADDRESS_SPACE_BYTES,
        max_input_bytes: int = _DEFAULT_MAX_INPUT_BYTES,
        worker: WorkerTarget = _extract_in_child,
    ) -> None:
        self.wall_timeout_s = wall_timeout_s
        self.cpu_seconds = cpu_seconds
        self.max_address_space_bytes = max_address_space_bytes
        self.max_input_bytes = max_input_bytes
        self._worker = worker

    def extract(self, path: Path) -> ExtractionResult:
        try:
            size = path.stat().st_size
        except OSError as exc:
            return _fail_closed(path, f"could not stat input: {exc}")
        if size > self.max_input_bytes:
            return _fail_closed(
                path,
                f"input is {size} bytes, over the {self.max_input_bytes}-byte cap; not parsed",
            )

        ctx = multiprocessing.get_context("spawn")
        recv_conn, send_conn = ctx.Pipe(duplex=False)
        proc = ctx.Process(
            target=self._worker,
            args=(path, send_conn, self.cpu_seconds, self.max_address_space_bytes),
            daemon=True,
        )
        deadline = time.monotonic() + self.wall_timeout_s
        proc.start()
        send_conn.close()  # parent keeps only the read end

        payload: object = None
        try:
            # Wait on the pipe rather than join() so a large result can never
            # deadlock against a child blocked on send().
            if recv_conn.poll(self.wall_timeout_s):
                try:
                    payload = recv_conn.recv()
                except (EOFError, OSError):
                    payload = None  # child died without a usable result
            else:
                proc.kill()
                proc.join()
                return _fail_closed(
                    path,
                    f"extraction exceeded the {self.wall_timeout_s}s wall-clock "
                    "limit; child killed",
                )
            proc.join(timeout=max(deadline - time.monotonic(), 0.0) + _JOIN_GRACE_S)
            if proc.is_alive():
                proc.kill()
                proc.join()
                return _fail_closed(path, "child did not exit after replying; killed")
        finally:
            recv_conn.close()
            if proc.is_alive():  # pragma: no cover - defensive
                proc.kill()
                proc.join()

        if proc.exitcode != 0:
            reason = (
                payload if isinstance(payload, str) else f"child exited with code {proc.exitcode}"
            )
            return _fail_closed(path, f"extraction failed: {reason}")
        if not isinstance(payload, ExtractionResult):
            return _fail_closed(path, "child exited cleanly but returned no result")
        return payload


def _fail_closed(path: Path, reason: str) -> ExtractionResult:
    """A zero-confidence, fieldless result that routes the document to review."""
    return ExtractionResult(
        source_file=path.name,
        pages=[PageResult(page_num=1, confidence=0.0)],
        note=reason,
    )
