"""Runs extractors in an isolated child process with a wall-clock timeout and memory cap."""

from __future__ import annotations

import logging
import multiprocessing as mp
from dataclasses import asdict
from multiprocessing.connection import Connection
from typing import Any

from app.services.extraction.base import ExtractionLimits, ExtractionResult
from app.services.extraction.extractors import extractor_for

log = logging.getLogger(__name__)
MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024


def _child(
    conn: Connection, kind: str, data: bytes, limits: dict[str, Any], converter: str
) -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    except (ImportError, ValueError, OSError):
        pass
    try:
        extractor = extractor_for(kind, converter)
        if extractor is None:
            result = ExtractionResult("unsupported", format=kind, warnings=["no extractor"])
        else:
            result = extractor.extract(data, ExtractionLimits(**limits))
        conn.send(asdict(result))
    except MemoryError:
        conn.send(
            asdict(ExtractionResult("failed", format=kind, warnings=["memory limit exceeded"]))
        )
    except Exception as exc:
        conn.send(
            asdict(
                ExtractionResult(
                    "failed", format=kind, warnings=[f"could not read file ({type(exc).__name__})"]
                )
            )
        )
    finally:
        conn.close()


def run_extraction(
    kind: str,
    data: bytes,
    limits: ExtractionLimits,
    *,
    timeout: float,
    legacy_converter: str = "none",
    isolate: bool = True,
) -> ExtractionResult:
    if not isolate:
        extractor = extractor_for(kind, legacy_converter)
        if extractor is None:
            return ExtractionResult("unsupported", format=kind, warnings=["no extractor"])
        try:
            return extractor.extract(data, limits)
        except Exception as exc:
            return ExtractionResult(
                "failed", format=kind, warnings=[f"could not read file ({type(exc).__name__})"]
            )

    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_child, args=(child, kind, data, asdict(limits), legacy_converter), daemon=True
    )
    proc.start()
    child.close()
    try:
        if parent.poll(timeout):
            payload = parent.recv()
            proc.join(5)
            return ExtractionResult(**payload)
        log.warning("extraction_timeout", extra={"kind": kind, "timeout": timeout})
        return ExtractionResult("failed", format=kind, warnings=["extraction timed out"])
    except EOFError:
        return ExtractionResult("failed", format=kind, warnings=["extractor crashed"])
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        parent.close()
