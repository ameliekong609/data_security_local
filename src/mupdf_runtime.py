"""Runtime settings for local PyMuPDF processing."""

from __future__ import annotations

import fitz


def quiet_mupdf_console_output() -> None:
    """Suppress noisy MuPDF repair/xref messages printed to the terminal.

    Broken or producer-quirky PDFs can emit many low-level messages even when
    MuPDF is able to recover and continue. Real failures still surface as Python
    exceptions from the calling code.
    """

    try:
        fitz.TOOLS.mupdf_display_warnings(False)
        fitz.TOOLS.mupdf_display_errors(False)
        fitz.TOOLS.reset_mupdf_warnings()
    except Exception:
        pass
