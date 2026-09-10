"""OCR via RapidOCR (local ONNX)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from lattice.tools.file_safety import PathDeniedError, resolve_agent_path
from lattice.tools.web import fence_untrusted

_engine: Any | None = None
_engine_error: str | None = None

IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
)


def _get_engine() -> Any:
    global _engine, _engine_error
    if _engine is not None:
        return _engine
    if _engine_error is not None:
        raise RuntimeError(_engine_error)
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        _engine_error = "rapidocr not installed (pip/uv add rapidocr onnxruntime)"
        raise RuntimeError(_engine_error) from exc
    try:
        _engine = RapidOCR()
    except Exception as exc:  # noqa: BLE001 — surface init failures to tool caller
        _engine_error = f"rapidocr init failed: {exc}"
        raise RuntimeError(_engine_error) from exc
    return _engine


def _format_result(result: Any) -> str:
    if result is None:
        return "(no text detected)"
    txts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if txts:
        lines: list[str] = []
        for i, text in enumerate(txts):
            score = None
            if scores is not None and i < len(scores):
                score = scores[i]
            if score is not None:
                lines.append(f"{text}  [{float(score):.3f}]")
            else:
                lines.append(str(text))
        return "\n".join(lines)
    # Legacy / alternate shapes: list of (box, text, score)
    if isinstance(result, (list, tuple)) and result:
        lines = []
        for item in result:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                lines.append(str(item[1]))
            else:
                lines.append(str(item))
        return "\n".join(lines) if lines else "(no text detected)"
    text = str(result).strip()
    return text or "(no text detected)"


def _ocr_sync(path: Path) -> str:
    engine = _get_engine()
    result = engine(str(path))
    return _format_result(result)


async def ocr_image(path: str, *, workspace: Path, home: Path) -> str:
    """Extract text from an image under the workspace (or home inbound paths)."""
    try:
        target = resolve_agent_path(path, workspace, home=home)
    except PathDeniedError as exc:
        return f"error: {exc}"
    if not target.is_file():
        return f"error: file not found: {target}"
    if target.suffix.lower() not in IMAGE_SUFFIXES:
        return f"error: unsupported image type {target.suffix or '(none)'}"

    def _run() -> str:
        try:
            body = _ocr_sync(target)
        except Exception as exc:  # noqa: BLE001
            return f"ocr error: {exc}"
        return fence_untrusted(f"ocr:{target.name}", body)

    return await asyncio.to_thread(_run)
