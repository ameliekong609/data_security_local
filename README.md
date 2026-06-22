# data_security_local

Local-first PII redaction MVP for PDF review, custom redaction profiles, pseudonym mapping, and safe export checks.

## Local Desktop App

This repo is a local-first desktop app for testing on Mac or Windows.

Use the launchers:

```text
run_local_mac.command
run_local_windows.bat
```

See [LOCAL_APP.md](LOCAL_APP.md).

The app runs on the user's own computer. No Nous Portal or cloud app deployment is required.

For non-technical users, build a packaged app first. See [PACKAGING.md](PACKAGING.md).

## Local Run

Manual developer run:

```bash
python desktop_app.py
```

## Tests

```bash
python -m pytest
```
