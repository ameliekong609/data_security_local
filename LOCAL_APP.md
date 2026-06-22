# Data Security Local App

This is the local desktop version for testing on Mac or Windows.

## Privacy Rule

When you run this local app, selected files are processed on that computer. Do not use a cloud demo for real client files.

## Mac

Double-click:

```text
run_local_mac.command
```

If macOS blocks it, open Terminal in this folder and run:

```bash
chmod +x run_local_mac.command
./run_local_mac.command
```

The app opens in a small desktop window.

Optional OCR support for scanned images/PDFs:

```bash
brew install tesseract
```

## Windows

Double-click:

```text
run_local_windows.bat
```

The app opens in a small desktop window.

Optional OCR support for scanned images/PDFs:

Install Tesseract OCR for Windows and add it to `PATH`.

Most modern Windows PCs already include Microsoft Edge WebView2 Runtime. If the app window does not open, install WebView2 Runtime from Microsoft and run the launcher again.

## What The Launcher Does

The launcher:

1. Creates a local Python environment in `.venv_local`.
2. Installs the app dependencies from `requirements.txt`.
3. Starts the local desktop app from `desktop_app.py`.
4. Opens the review UI in a native desktop window.

No Nous Portal is required.
