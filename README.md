# data_security_local

Local-first PII redaction MVP for PDF review, custom redaction profiles, pseudonym mapping, and safe export checks.

## Streamlit Community Cloud

This repo can be deployed as a **synthetic-data demo** on Streamlit Community Cloud.

Use this entrypoint:

```text
app/streamlit_app.py
```

Important privacy rule:

- Do not upload real client documents to the Community Cloud deployment.
- Community Cloud runs on Streamlit-managed servers, so uploaded files are not processed on your local machine.
- Use the cloud deployment only for synthetic demos and product review.
- For real client files, run the app locally or inside an approved private environment.

## Local Run

```bash
python -m streamlit run app/streamlit_app.py
```

## Tests

```bash
python -m pytest
```
