"""Streamlit UI for local custom redaction profiles and PDF detection review."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import fitz
import pandas as pd
import streamlit as st

# Allow `streamlit run app/streamlit_app.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.export_workflow import export_reviewed_pdf
from src.review_actions import (
    approve_all_findings,
    record_seen_findings,
    summarize_review_loop,
)
from src.services.custom_terms import CustomTermDetector, ReviewFinding
from src.services.profiles import CustomTerm, ProfileStore, RedactionProfile


ENTITY_TYPES = [
    "PERSON",
    "COMPANY",
    "TRUST",
    "ADDRESS",
    "ACCOUNT",
    "CLIENT_ID",
    "EMAIL",
    "PHONE",
    "DOB",
    "ABN",
    "TFN",
    "CUSTOM",
]


def _profile_store() -> ProfileStore:
    profile_dir = os.environ.get("DATA_SECURITY_PROFILE_DIR")
    return ProfileStore(profile_dir)


def _profile_options(profiles: list[RedactionProfile]) -> dict[str, str]:
    return {f"{profile.profile_name} ({len(profile.terms)} terms)": profile.profile_id for profile in profiles}


def _split_variants(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _open_uploaded_pdf(data: bytes, filename: str) -> fitz.Document | None:
    """Open an uploaded PDF when it is not encrypted."""

    doc = fitz.open(stream=data, filetype="pdf")
    if doc.is_encrypted and not doc.authenticate(""):
        doc.close()
        return None
    return doc


def _finding_rows(findings: list[ReviewFinding]) -> list[dict[str, object]]:
    rows = []
    for finding in findings:
        rows.append({
            "Status": finding.status,
            "File": finding.file_id,
            "Page": finding.page_number + 1,
            "Entity type": finding.entity_type,
            "Detected text": finding.text,
            "Replacement": finding.proposed_replacement,
            "Source": finding.source_detector,
            "Confidence": finding.confidence,
        })
    return rows


def render_profile_editor(store: ProfileStore) -> RedactionProfile | None:
    st.header("1. Choose or create local profile")
    profiles = store.list_profiles()

    with st.expander("Create a new local profile", expanded=not profiles):
        profile_name = st.text_input("Profile name", placeholder="e.g. Synthetic Client A")
        if st.button("Create profile", type="primary"):
            if profile_name.strip():
                created = store.create_profile(profile_name)
                st.session_state["selected_profile_id"] = created.profile_id
                st.rerun()
            st.warning("Enter a profile name first.")

    profiles = store.list_profiles()
    if not profiles:
        st.info("Create a profile before adding terms or running detection.")
        return None

    options = _profile_options(profiles)
    labels = list(options.keys())
    selected_id = st.session_state.get("selected_profile_id")
    default_index = 0
    if selected_id in options.values():
        default_index = list(options.values()).index(selected_id)
    selected_label = st.selectbox("Select profile", labels, index=default_index)
    profile = store.get_profile(options[selected_label])
    st.session_state["selected_profile_id"] = profile.profile_id

    st.subheader("2. Add custom term")
    with st.form("add-term", clear_on_submit=True):
        original = st.text_input("Original term")
        entity_type = st.selectbox("Entity type", ENTITY_TYPES)
        replacement = st.text_input("Replacement label", value=f"[{entity_type}_1]")
        variants = st.text_area("Variants / aliases (one per line)")
        notes = st.text_area("Notes (local only)")
        submitted = st.form_submit_button("Add term")
        if submitted:
            store.add_term(profile.profile_id, CustomTerm(
                original=original,
                entity_type=entity_type,
                replacement=replacement,
                variants=_split_variants(variants),
                notes=notes,
            ))
            st.success("Term saved locally.")
            st.rerun()

    st.subheader("3. Edit profile terms")
    if not profile.terms:
        st.info("No custom terms in this profile yet.")
        return profile

    for term in profile.terms:
        with st.expander(f"{term.entity_type} → {term.replacement}"):
            with st.form(f"edit-{term.term_id}"):
                original = st.text_input("Original", value=term.original, key=f"orig-{term.term_id}")
                entity_type = st.selectbox(
                    "Entity type",
                    ENTITY_TYPES,
                    index=ENTITY_TYPES.index(term.entity_type) if term.entity_type in ENTITY_TYPES else ENTITY_TYPES.index("CUSTOM"),
                    key=f"etype-{term.term_id}",
                )
                replacement = st.text_input("Replacement", value=term.replacement, key=f"repl-{term.term_id}")
                variants = st.text_area(
                    "Variants / aliases (one per line)",
                    value="\n".join(term.variants),
                    key=f"vars-{term.term_id}",
                )
                notes = st.text_area("Notes", value=term.notes, key=f"notes-{term.term_id}")
                col1, col2 = st.columns(2)
                if col1.form_submit_button("Save changes"):
                    store.update_term(
                        profile.profile_id,
                        term.term_id,
                        original=original,
                        entity_type=entity_type,
                        replacement=replacement,
                        variants=_split_variants(variants),
                        notes=notes,
                    )
                    st.success("Term updated locally.")
                    st.rerun()
                if col2.form_submit_button("Delete term"):
                    store.delete_term(profile.profile_id, term.term_id)
                    st.warning("Term deleted.")
                    st.rerun()

    return profile


def _apply_review_table(findings: list[ReviewFinding], edited: pd.DataFrame) -> list[ReviewFinding]:
    """Copy editable review table values back onto finding objects."""
    updated: list[ReviewFinding] = []
    for index, finding in enumerate(findings):
        if index >= len(edited):
            updated.append(finding)
            continue
        row = edited.iloc[index]
        finding.status = str(row.get("Status", finding.status)).strip().lower()
        finding.entity_type = str(row.get("Entity type", finding.entity_type)).strip() or finding.entity_type
        finding.proposed_replacement = str(row.get("Replacement", finding.proposed_replacement)).strip() or finding.proposed_replacement
        updated.append(finding)
    return updated


def render_detection(profile: RedactionProfile | None) -> None:
    st.header("4. Run local detection with selected profile")
    if profile is None:
        st.info("Select a profile to enable detection.")
        return
    uploaded_files = st.file_uploader(
        "Choose a folder of local PDFs",
        type=["pdf"],
        accept_multiple_files="directory",
        help="Select a directory; PDFs in the directory and subdirectories will be included.",
    )
    if not uploaded_files:
        return
    if st.button("Detect custom terms", type="primary"):
        findings: list[ReviewFinding] = []
        uploaded_bytes: dict[str, bytes] = {}
        skipped_files: list[str] = []
        detector = CustomTermDetector(profile)
        for uploaded in uploaded_files:
            data = uploaded.getvalue()
            uploaded_bytes[uploaded.name] = data
            try:
                doc = _open_uploaded_pdf(data, uploaded.name)
            except Exception as exc:
                skipped_files.append(f"{uploaded.name}: {exc}")
                continue
            if doc is None:
                skipped_files.append(f"{uploaded.name}: encrypted PDF requires a password")
                continue
            try:
                findings.extend(detector.detect_pdf(doc, file_id=uploaded.name))
            finally:
                doc.close()
        previous_seen = set(st.session_state.get("review_seen_keys", set()))
        pass_number = min(int(st.session_state.get("review_pass_number", 0)) + 1, 3)
        st.session_state["custom_term_findings"] = findings
        st.session_state["uploaded_pdf_bytes"] = uploaded_bytes
        st.session_state["skipped_files"] = skipped_files
        st.session_state["review_pass_number"] = pass_number
        st.session_state["seen_before_current_pass"] = previous_seen
        st.session_state["review_seen_keys"] = record_seen_findings(previous_seen, findings)
        st.session_state["review_complete"] = False

    skipped_files = st.session_state.get("skipped_files", [])
    if skipped_files:
        with st.expander("Skipped files", expanded=True):
            for skipped in skipped_files:
                st.warning(skipped)

    findings = st.session_state.get("custom_term_findings", [])
    if findings:
        st.subheader("Review table")
        st.caption("Set each finding to approved/rejected/pending, edit type/replacement, then export approved findings locally.")
        edited = st.data_editor(
            pd.DataFrame(_finding_rows(findings)),
            column_config={
                "Status": st.column_config.SelectboxColumn(
                    "Status",
                    options=["pending", "approved", "rejected"],
                    required=True,
                ),
                "Entity type": st.column_config.SelectboxColumn(
                    "Entity type",
                    options=ENTITY_TYPES,
                    required=True,
                ),
            },
            disabled=["File", "Page", "Detected text", "Source", "Confidence"],
            hide_index=True,
            use_container_width=True,
        )
        findings = _apply_review_table(findings, edited)
        st.session_state["custom_term_findings"] = findings
        st.session_state["custom_term_review_table"] = edited

        review_summary = summarize_review_loop(
            findings,
            seen_keys=set(st.session_state.get("seen_before_current_pass", set())),
            pass_number=int(st.session_state.get("review_pass_number", 1)),
            max_passes=3,
            review_complete=bool(st.session_state.get("review_complete", False)),
        )
        st.subheader("Review loop status")
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Pass", f"{review_summary.pass_number}/{review_summary.max_passes}")
        col2.metric("New", review_summary.new_count)
        col3.metric("Pending", review_summary.pending_count)
        col4.metric("Approved", review_summary.approved_count)
        col5.metric("Rejected", review_summary.rejected_count)
        if review_summary.at_max_passes and review_summary.pending_count:
            st.warning(review_summary.stop_reason)
        else:
            st.info(review_summary.stop_reason)

        if st.button("Approve all findings"):
            approve_all_findings(findings)
            st.session_state["custom_term_findings"] = findings
            st.session_state["review_complete"] = False
            st.rerun()

        if st.button("Mark review complete", disabled=not review_summary.can_mark_complete):
            st.session_state["review_complete"] = True
            st.rerun()

        st.subheader("5. Export approved redactions")
        output_dir = st.text_input("Local export directory", value=str(Path.cwd() / "local_exports"))
        approved_count = sum(1 for finding in findings if finding.status == "approved")
        st.caption(f"Approved findings ready for export: {approved_count}")
        if st.button("Export approved PDFs", type="primary", disabled=not review_summary.can_export):
            uploaded_bytes = st.session_state.get("uploaded_pdf_bytes", {})
            results = []
            for filename, pdf_bytes in uploaded_bytes.items():
                file_findings = [finding for finding in findings if finding.file_id == filename]
                if not any(finding.status == "approved" for finding in file_findings):
                    continue
                results.append(export_reviewed_pdf(
                    pdf_bytes=pdf_bytes,
                    input_filename=filename,
                    findings=file_findings,
                    output_dir=output_dir,
                ))
            st.session_state["export_results"] = results
            st.success(f"Exported {len(results)} PDF(s) locally.")

    else:
        st.info("No custom-term findings yet for this run.")

    results = st.session_state.get("export_results", [])
    if results:
        st.subheader("Export results")
        for result in results:
            st.write(f"**{result.input_filename}** — {result.redaction_count} redaction(s)")
            st.code(f"PDF: {result.output_pdf}\nMap: {result.mapping_json}\nAudit: {result.audit_json}")


def main() -> None:
    st.set_page_config(page_title="Local PII Redaction Profiles", layout="wide")
    st.title("Local custom redaction profiles")
    st.caption("Profiles and detections stay on this machine. Do not upload real client content to external services.")
    store = _profile_store()
    profile = render_profile_editor(store)
    render_detection(profile)


if __name__ == "__main__":
    main()
