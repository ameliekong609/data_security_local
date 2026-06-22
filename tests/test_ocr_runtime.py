from pathlib import Path

from src.ocr_runtime import _find_tesseract_executable


def test_find_tesseract_prefers_bundled_pyinstaller_binary(tmp_path, monkeypatch):
    bundled = tmp_path / "tesseract" / "tesseract.exe"
    bundled.parent.mkdir()
    bundled.write_text("fake", encoding="utf-8")
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)

    assert _find_tesseract_executable() == bundled


def test_find_tesseract_uses_explicit_override(tmp_path, monkeypatch):
    override = tmp_path / "custom-tesseract.exe"
    override.write_text("fake", encoding="utf-8")
    monkeypatch.setenv("TESSERACT_CMD", str(override))
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path / "missing"), raising=False)

    assert _find_tesseract_executable() == override
