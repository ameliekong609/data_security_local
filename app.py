#!/usr/bin/env python3
"""Local Streamlit review UI for PDF PII redaction.

Run locally with:
    streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path
import tempfile

import streamlit as st

from src.config_loader import default_redaction_config, load_config
from src.review_state import DetectionStatus, ReviewSession
from src.review_workflow import (
    add_custom_detection_from_pdf,
    build_review_for_pdfs,
    collect_pdf_files,
    export_reviewed_pdfs,
    write_local_review_artifacts,
)


ENTITY_TYPES = [
    "person",
    "company",
    "trust",
    "address",
    "email",
    "phone",
    "account",
    "client_id",
    "dob",
    "abn",
    "tfn",
    "keyword",
    "field",
    "custom",
]


st.set_page_config(page_title="Local PII Review", layout="wide")
st.title("Local PII review and pseudonym map")
st.caption("All selected documents, detections, review edits, maps and exports stay on this machine.")

if "review" not in st.session_state:
    st.session_state.review = ReviewSession()
if "pdf_paths" not in st.session_state:
    st.session_state.pdf_paths = []

with st.sidebar:
    st.header("1. Select local PDFs")
    folder = st.text_input("Folder containing PDFs", "")
    explicit_files = st.text_area(
        "Optional PDF paths, one per line",
        help="Use this when files are outside the selected folder.",
    )
    with st.expander("Advanced local config"):
        config_path = st.text_input(
            "Optional YAML rule file",
            "",
            help="Leave blank to use built-in generic local rules.",
        )
    uploads = st.file_uploader(
        "Or upload synthetic/local PDFs into a temporary local session",
        type=["pdf"],
        accept_multiple_files=True,
    )
    output_dir = st.text_input("Output folder", "review_outputs")

    if st.button("Detect locally", type="primary"):
        selected = [line.strip() for line in explicit_files.splitlines() if line.strip()]
        temp_paths = []
        if uploads:
            temp_root = Path(tempfile.mkdtemp(prefix="local-review-pdfs-"))
            for uploaded in uploads:
                temp_path = temp_root / uploaded.name
                temp_path.write_bytes(uploaded.getbuffer())
                temp_paths.append(temp_path)
        try:
            pdf_paths = collect_pdf_files(selected + temp_paths, folder or None)
            if not pdf_paths:
                st.error("No local PDF files selected.")
            else:
                config = load_config(config_path) if config_path.strip() else default_redaction_config()
                st.session_state.pdf_paths = [str(path) for path in pdf_paths]
                st.session_state.review = build_review_for_pdfs(pdf_paths, config)
                st.success(f"Detected {len(st.session_state.review.detections)} candidate(s) in {len(pdf_paths)} PDF(s).")
        except Exception as exc:  # Streamlit should show local errors without uploading data.
            st.error(f"Detection failed: {exc}")

review: ReviewSession = st.session_state.review
pdf_paths = [Path(path) for path in st.session_state.pdf_paths]

st.header("2. Review detections in context")
if not review.detections:
    st.info("Select PDFs and click 'Detect locally' to begin.")
else:
    pending = sum(1 for d in review.detections if d.status == DetectionStatus.PENDING)
    approved = sum(1 for d in review.detections if d.status == DetectionStatus.APPROVED)
    rejected = sum(1 for d in review.detections if d.status == DetectionStatus.REJECTED)
    st.write(f"Pending: {pending} · Approved: {approved} · Rejected: {rejected}")

    for detection in review.detections:
        label = (
            f"{detection.document_name} p.{detection.page_label} · "
            f"{detection.entity_type} · {detection.status.value} · {detection.replacement_label}"
        )
        with st.expander(label, expanded=detection.status == DetectionStatus.PENDING):
            st.markdown("**Context**")
            st.code(f"...{detection.context_before}«{detection.original_text}»{detection.context_after}...", language="text")
            col1, col2, col3 = st.columns([1, 1, 1])
            with col1:
                current_type_index = ENTITY_TYPES.index(detection.entity_type) if detection.entity_type in ENTITY_TYPES else 0
                entity_type = st.selectbox(
                    "Entity type",
                    ENTITY_TYPES,
                    index=current_type_index,
                    key=f"type-{detection.detection_id}",
                )
            with col2:
                replacement_label = st.text_input(
                    "Replacement label",
                    value=detection.replacement_label,
                    key=f"replacement-{detection.detection_id}",
                )
            with col3:
                original_text = st.text_input(
                    "Matched text",
                    value=detection.original_text,
                    key=f"original-{detection.detection_id}",
                )
            review.edit_detection(
                detection.detection_id,
                entity_type=entity_type,
                replacement_label=replacement_label,
                original_text=original_text,
            )

            action1, action2, action3 = st.columns([1, 1, 2])
            with action1:
                if st.button("Approve", key=f"approve-{detection.detection_id}"):
                    review.approve_detection(detection.detection_id)
                    st.rerun()
            with action2:
                if st.button("Reject", key=f"reject-{detection.detection_id}"):
                    review.reject_detection(detection.detection_id, reason="Rejected in local UI")
                    st.rerun()
            with action3:
                if detection.rect is None:
                    st.warning("This custom item is in the replacement map but is not anchored for PDF redaction export.")

st.header("3. Add a missed custom detection")
if pdf_paths:
    with st.form("custom-detection-form"):
        document_choice = st.selectbox("Document", [str(path) for path in pdf_paths])
        page_num = st.number_input("Page number", min_value=1, value=1, step=1)
        original_text = st.text_input("Missed text to pseudonymise")
        entity_type = st.selectbox("Entity type", ENTITY_TYPES, index=ENTITY_TYPES.index("custom"))
        replacement_label = st.text_input("Replacement label", "[CUSTOM_1]")
        submitted = st.form_submit_button("Add custom detection")
        if submitted:
            if not original_text.strip():
                st.error("Enter the missed text first.")
            else:
                detection_id = add_custom_detection_from_pdf(
                    review,
                    document_path=document_choice,
                    page_num=int(page_num) - 1,
                    original_text=original_text.strip(),
                    entity_type=entity_type,
                    replacement_label=replacement_label.strip(),
                )
                st.success(f"Added custom detection {detection_id}. Review and approve it above.")
                st.rerun()
else:
    st.info("Choose PDFs first to add custom detections.")

st.header("4. Confirm replacement map and export")
if review.detections:
    replacement_map = review.export_replacement_map()
    st.json(replacement_map)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Confirm replacement map"):
            try:
                review.confirm_replacement_map()
                map_path, audit_path = write_local_review_artifacts(review, output_dir)
                st.success(f"Replacement map confirmed locally: {map_path}\nAudit log: {audit_path}")
            except Exception as exc:
                st.error(str(exc))
    with col2:
        if st.button("Export approved redacted PDFs"):
            try:
                exported = export_reviewed_pdfs(review, output_dir)
                write_local_review_artifacts(review, output_dir)
                if exported:
                    st.success("Exported:\n" + "\n".join(str(path) for path in exported))
                else:
                    st.warning("No anchored approved detections were available for PDF export.")
            except Exception as exc:
                st.error(str(exc))
