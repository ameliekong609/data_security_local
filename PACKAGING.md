# Packaging The Desktop App

The launcher scripts are for development/testing. For non-technical colleagues, build a packaged app.

## Mac

Build on a Mac:

```bash
./scripts/build_mac_app.sh
```

Output:

```text
dist/Data Security Local.app
```

Create a shareable zip:

```bash
ditto -c -k --sequesterRsrc --keepParent "dist/Data Security Local.app" "dist/Data Security Local-mac.zip"
```

Share the zip, not the source folder. The colleague should copy the app out of OneDrive/Downloads before opening it.

## Windows

Build the Windows `.exe` on a Windows machine or Windows CI runner. A Mac build cannot reliably produce a Windows desktop executable.

From PowerShell:

```powershell
.\scripts\build_windows_app.ps1
```

The Windows build script bundles Tesseract OCR when it is available. In GitHub
Actions, the workflow installs Tesseract before packaging, so the downloaded
Windows app can OCR images without asking the tester to install Python or
Tesseract separately.

Target output:

```text
dist\Data Security Local.exe
```

Create a shareable zip:

```powershell
Compress-Archive -Path "dist\Data Security Local.exe" -DestinationPath "dist\Data Security Local-windows.zip" -Force
```

## Notes

- The packaged app includes Python and Python dependencies.
- The GitHub Actions Windows package includes Tesseract OCR for image OCR.
- Text-based PDFs do not need Tesseract.
- Unsigned Mac apps may show a Gatekeeper warning. For wider distribution, sign and notarize the app with an Apple Developer account.
