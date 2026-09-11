"""Sandboxed, resource-limited document extraction.

Attacker-supplied PDFs and images are the primary untrusted input surface.
Parsing them in the main process means a crafted file that hangs, balloons
memory, or crashes the parser takes the whole run down with it — and a parser
exploit would run with access to the entire constituent file.

``SandboxedExtractor`` runs the parse in a spawned child process with
best-effort resource caps applied inside the child (``resource.setrlimit``
on CPU seconds and address space, POSIX only) and a wall-clock timeout enforced
by the parent. Input files over a size cap are refused before any parsing
starts. Every failure mode — oversize input, timeout, nonzero exit, crash,
missing result — fails closed: the extractor returns a single zero-confidence
page with no fields plus a ``note`` explaining why, instead of crashing the
run. The pipeline reads that note as "this document could not be read": the
document contributes no records and no page counts, and is listed with the note
as its reason under the ingest report's unreadable documents, which is where the
operator sees it. It is not routed to the review queue, which holds record
pairs; there is no record here to pair.

The pipeline uses this extractor by default for every PDF backend and for
images (see ``config.ExtractConfig.sandbox``). ``ocr=True`` selects the
OCR-fallback PDF extractor inside the child for ``backend = "pdfplumber+ocr"``,
and ``image=True`` the image reader (``extract/image.py``). OCR is heavier
than a text-layer parse, so a large legitimate scan can hit the CPU cap; that
still fails closed to the unreadable list, and a recipe that needs unbounded OCR can
set ``sandbox = false`` and accept the in-process risk.

Two things a child leaves behind are the parent's to clear, because a killed
child cannot. OCR copies each page image to a temporary file for the Tesseract
binary to read (pytesseract's ``save``) and deletes it only in a ``finally``
that a SIGKILL skips; so the child writes its temporary files into a scratch
directory the parent creates for that one parse and removes once the child is
gone, killed or not. And Tesseract runs as the child's own subprocess, which a
kill aimed at the child alone leaves running; so the child leads a process
group of its own and the parent kills the group.

Non-goals, stated honestly: this is containment, not a syscall sandbox. The
child runs the same interpreter with the same privileges and filesystem
access; ``RLIMIT_AS`` is not enforced on every platform (notably macOS), and
on Windows the ``resource`` module does not exist, so only the wall-clock
timeout applies there, and there is no process group to kill. The Docker path
(see ``Dockerfile``) provides the stronger isolation boundary for deployments
that need one. A parent that is itself killed cannot remove its scratch
directory; it is named ``constituent-reconciler-extract-*`` under the system
temporary directory.
"""

from __future__ import annotations

import multiprocessing
import os
import shutil
import signal
import tempfile
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path

from constituent_reconciler.extract.base import ExtractionResult, failed_closed
from constituent_reconciler.extract.image import DEFAULT_MAX_IMAGE_PIXELS

_DEFAULT_WALL_TIMEOUT_S = 60.0
_DEFAULT_CPU_SECONDS = 30
_DEFAULT_MAX_ADDRESS_SPACE_BYTES = 1 << 30  # 1 GiB
_DEFAULT_MAX_INPUT_BYTES = 50 * 1024 * 1024  # 50 MiB

# How long the parent waits for the child to exit after it has delivered its
# result. This only cushions interpreter shutdown, not parsing work.
_JOIN_GRACE_S = 10.0

#: Prefix of each parse's scratch directory, so one left by a killed parent is
#: recognisable under the system temporary directory.
SCRATCH_PREFIX = "constituent-reconciler-extract-"


@dataclass(frozen=True)
class ChildLimits:
    """What one child may spend, and the one directory it writes temporary files to."""

    cpu_seconds: int
    max_address_space_bytes: int
    max_image_pixels: int
    scratch_dir: str


# Signature of the function run inside the child process.
WorkerTarget = Callable[[Path, Connection, ChildLimits], None]


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


def _enter_child(limits: ChildLimits) -> None:
    """Everything a child does before it opens the document.

    It leads a process group of its own, so the parent's kill reaches whatever
    it starts; the rlimits are applied, and inherited by those subprocesses;
    and ``tempfile`` is pointed at the scratch directory the parent made for
    this parse, which is where pytesseract writes its copy of each page image.
    ``TMPDIR`` is set too, for any subprocess that consults it.
    """
    if hasattr(os, "setpgrp"):
        os.setpgrp()
    _apply_resource_limits(limits.cpu_seconds, limits.max_address_space_bytes)
    tempfile.tempdir = limits.scratch_dir
    os.environ["TMPDIR"] = limits.scratch_dir


def _run_child(
    path: Path,
    conn: Connection,
    limits: ChildLimits,
    parse: Callable[[Path, ChildLimits], ExtractionResult],
) -> None:
    """Enter the child's limits, parse, and pipe the result back.

    On a parse failure the exception message is sent back instead (so the
    fail-closed note can say why) and the child exits nonzero.
    """
    _enter_child(limits)
    try:
        result = parse(path, limits)
    except Exception as exc:
        conn.send(f"{type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc
    conn.send(result)
    conn.close()


def _parse_pdf(path: Path, limits: ChildLimits) -> ExtractionResult:
    from constituent_reconciler.extract.pdf import PdfplumberExtractor

    return PdfplumberExtractor().extract(path)


def _parse_pdf_with_ocr(path: Path, limits: ChildLimits) -> ExtractionResult:
    # Tesseract's import stays deferred inside the OCR module, so a text-layer
    # page parses here without OCR dependencies.
    from constituent_reconciler.extract.ocr import PdfplumberOcrExtractor

    return PdfplumberOcrExtractor().extract(path)


def _parse_image(path: Path, limits: ChildLimits) -> ExtractionResult:
    from constituent_reconciler.extract.image import extract_image

    return extract_image(path, max_pixels=limits.max_image_pixels)


# The child-process entry points. Each must stay module-level so the spawn
# context can pickle it by reference.


def _extract_in_child(path: Path, conn: Connection, limits: ChildLimits) -> None:
    _run_child(path, conn, limits, _parse_pdf)


def _extract_ocr_in_child(path: Path, conn: Connection, limits: ChildLimits) -> None:
    _run_child(path, conn, limits, _parse_pdf_with_ocr)


def _extract_image_in_child(path: Path, conn: Connection, limits: ChildLimits) -> None:
    _run_child(path, conn, limits, _parse_image)


def _kill(proc: BaseProcess) -> None:
    """Kill the child and everything it started, then reap it.

    The child leads its own process group (``_enter_child``), so signalling the
    group reaches a Tesseract grandchild as well. Before the child has made its
    group, and where process groups do not exist, the group signal fails and
    the child alone is killed.
    """
    try:
        if proc.pid is None or not hasattr(os, "killpg"):
            raise ProcessLookupError
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    proc.join()


def _remove_scratch(scratch: Path) -> None:
    """Delete one parse's scratch directory and every page image left in it.

    Runs after the child has exited, or been killed along with its group, so
    nothing can still be writing there. A removal that fails is reported rather
    than swallowed: what would be left behind is a copy of an intake page.
    """
    try:
        shutil.rmtree(scratch)
    except FileNotFoundError:
        return
    except OSError as exc:
        warnings.warn(
            f"could not remove extraction scratch directory {scratch}: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )


class SandboxedExtractor:
    """Run a document extractor in a constrained child process, fail-closed.

    Satisfies the ``Extractor`` protocol, so it drops in wherever
    ``PdfplumberExtractor`` (or, with ``ocr=True``, ``PdfplumberOcrExtractor``;
    with ``image=True``, ``ImageOcrExtractor``) was used directly. ``worker``
    exists for tests to inject a misbehaving child; production callers should
    not pass it. ``scratch_root`` is where each parse's scratch directory is
    made, the system temporary directory by default.
    """

    def __init__(
        self,
        *,
        wall_timeout_s: float = _DEFAULT_WALL_TIMEOUT_S,
        cpu_seconds: int = _DEFAULT_CPU_SECONDS,
        max_address_space_bytes: int = _DEFAULT_MAX_ADDRESS_SPACE_BYTES,
        max_input_bytes: int = _DEFAULT_MAX_INPUT_BYTES,
        max_image_pixels: int = DEFAULT_MAX_IMAGE_PIXELS,
        ocr: bool = False,
        image: bool = False,
        scratch_root: Path | None = None,
        worker: WorkerTarget | None = None,
    ) -> None:
        self.wall_timeout_s = wall_timeout_s
        self.cpu_seconds = cpu_seconds
        self.max_address_space_bytes = max_address_space_bytes
        self.max_input_bytes = max_input_bytes
        self.max_image_pixels = max_image_pixels
        self.scratch_root = scratch_root
        if worker is None:
            if image:
                worker = _extract_image_in_child
            elif ocr:
                worker = _extract_ocr_in_child
            else:
                worker = _extract_in_child
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
        scratch = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX, dir=self.scratch_root))
        try:
            return self._parse_in_child(path, scratch)
        finally:
            _remove_scratch(scratch)

    def _parse_in_child(self, path: Path, scratch: Path) -> ExtractionResult:
        limits = ChildLimits(
            cpu_seconds=self.cpu_seconds,
            max_address_space_bytes=self.max_address_space_bytes,
            max_image_pixels=self.max_image_pixels,
            scratch_dir=str(scratch),
        )
        ctx = multiprocessing.get_context("spawn")
        recv_conn, send_conn = ctx.Pipe(duplex=False)
        proc = ctx.Process(
            target=self._worker,
            args=(path, send_conn, limits),
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
                _kill(proc)
                return _fail_closed(
                    path,
                    f"extraction exceeded the {self.wall_timeout_s}s wall-clock "
                    "limit; child killed",
                )
            proc.join(timeout=max(deadline - time.monotonic(), 0.0) + _JOIN_GRACE_S)
            if proc.is_alive():
                _kill(proc)
                return _fail_closed(path, "child did not exit after replying; killed")
        finally:
            recv_conn.close()
            if proc.is_alive():  # pragma: no cover - defensive
                _kill(proc)

        if proc.exitcode != 0:
            reason = (
                payload if isinstance(payload, str) else f"child exited with code {proc.exitcode}"
            )
            return _fail_closed(path, f"extraction failed: {reason}")
        if not isinstance(payload, ExtractionResult):
            return _fail_closed(path, "child exited cleanly but returned no result")
        return payload


def _fail_closed(path: Path, reason: str) -> ExtractionResult:
    """A zero-confidence, fieldless result whose note says why the read failed.

    The pipeline accounts it as an unreadable document (``pipeline._unreadable``).
    """
    return failed_closed(path.name, reason)
