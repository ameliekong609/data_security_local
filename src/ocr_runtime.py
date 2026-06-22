"""Runtime discovery for bundled/local OCR tools."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytesseract


def configure_tesseract() -> None:
    """Point pytesseract at a bundled Tesseract binary when available."""

    executable = _find_tesseract_executable()
    if executable is None:
        return

    pytesseract.pytesseract.tesseract_cmd = str(executable)
    tessdata_dir = executable.parent / "tessdata"
    if tessdata_dir.is_dir():
        os.environ.setdefault("TESSDATA_PREFIX", str(tessdata_dir))


def _find_tesseract_executable() -> Path | None:
    candidates: list[Path] = []

    override = os.environ.get("TESSERACT_CMD")
    if override:
        candidates.append(Path(override))

    bundle_root = Path(getattr(sys, "_MEIPASS", ""))
    if bundle_root:
        candidates.extend(
            [
                bundle_root / "tesseract" / "tesseract.exe",
                bundle_root / "tesseract" / "tesseract",
            ]
        )

    candidates.extend(
        [
            Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
            Path("/opt/homebrew/bin/tesseract"),
            Path("/usr/local/bin/tesseract"),
            Path("/usr/bin/tesseract"),
        ]
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None
