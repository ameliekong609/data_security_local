#!/usr/bin/env python3
"""Local Streamlit review UI for PDF PII redaction.

Run locally with:
    streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path
import io
import tempfile
import zipfile

import streamlit as st

from src.config_loader import default_redaction_config
from src.review_state import DetectionStatus, ReviewSession
from src.review_workflow import (
    add_custom_detection_from_pdf,
    collect_pdf_files,
    detect_redactions_for_pdf,
    export_reviewed_pdfs,
    write_local_review_artifacts,
)
from src.review_state import build_review_session


SUPPORTED_TYPES = ["pdf", "png", "jpg", "jpeg"]
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}

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
if "detection_warnings" not in st.session_state:
    st.session_state.detection_warnings = []
if "image_results" not in st.session_state:
    st.session_state.image_results = []
if "exported_pdf_paths" not in st.session_state:
    st.session_state.exported_pdf_paths = []
if "review_artifact_paths" not in st.session_state:
    st.session_state.review_artifact_paths = []


def _write_uploaded_file(temp_root: Path, uploaded) -> Path:
    """Persist an uploaded document under a temporary local directory."""

    raw_path = Path(uploaded.name)
    safe_parts = [part for part in raw_path.parts if part not in {"", ".", ".."}]
    relative_path = Path(*safe_parts) if safe_parts else Path("uploaded.pdf")
    temp_path = temp_root / relative_path
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.write_bytes(uploaded.getbuffer())
    return temp_path


def _download_file(path: Path, *, label_prefix: str = "Download") -> None:
    """Render a Streamlit download button for a generated local/server file."""

    if not path.exists():
        st.warning(f"Output file no longer exists: {path.name}")
        return
    mime_by_suffix = {
        ".pdf": "application/pdf",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    st.download_button(
        f"{label_prefix} {path.name}",
        data=path.read_bytes(),
        file_name=path.name,
        mime=mime_by_suffix.get(path.suffix.lower(), "application/octet-stream"),
        key=f"download-{path}",
    )


def _output_zip(paths: list[Path]) -> bytes:
    """Build an in-memory ZIP containing generated output files."""

    buffer = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            if not path.exists() or not path.is_file():
                continue
            archive_name = path.name
            if archive_name in used_names:
                archive_name = f"{path.stem}-{len(used_names) + 1}{path.suffix}"
            used_names.add(archive_name)
            archive.write(path, arcname=archive_name)
    return buffer.getvalue()


with st.sidebar:
    st.header("1. Select local files")
    uploads = st.file_uploader(
        "Choose a folder of PDFs or images",
        type=SUPPORTED_TYPES,
        accept_multiple_files="directory",
        help="Select a directory; PDFs, PNGs, and JPGs in the directory and subdirectories will be uploaded into this session.",
    )
    output_dir = st.text_input("Output folder", "review_outputs")
    st.caption("On Streamlit Community this folder is inside the cloud session. Use the ZIP download after export to save files to your computer.")

    if st.button("Detect locally", type="primary"):
        temp_paths = []
        if uploads:
            temp_root = Path(tempfile.mkdtemp(prefix="local-review-pdfs-"))
            for uploaded in uploads:
                temp_paths.append(_write_uploaded_file(temp_root, uploaded))
        try:
            pdf_paths = collect_pdf_files(temp_paths)
            image_paths = [path for path in temp_paths if path.suffix.lower() in IMAGE_SUFFIXES]
            if not pdf_paths and not image_paths:
                st.error("No supported local files selected.")
            else:
                config = default_redaction_config()
                review = ReviewSession()
                warnings = []
                skipped_pdf_count = 0
                image_results = []
                for pdf_path in pdf_paths:
                    try:
                        redactions = detect_redactions_for_pdf(pdf_path, config)
                    except Exception as exc:
                        warnings.append(f"{pdf_path.name}: {exc}")
                        skipped_pdf_count += 1
                        continue
                    review.detections.extend(build_review_session(pdf_path, redactions).detections)
                for image_path in image_paths:
                    try:
                        from src.image_redactor import redact_image

                        image_results.append(redact_image(image_path, Path(output_dir), config))
                    except Exception as exc:
                        warnings.append(f"{image_path.name}: image OCR/redaction failed: {exc}")
                st.session_state.pdf_paths = [str(path) for path in pdf_paths]
                st.session_state.review = review
                st.session_state.detection_warnings = warnings
                st.session_state.image_results = image_results
                processed_count = len(pdf_paths) - skipped_pdf_count
                successful_images = len([result for result in image_results if not result.error])
                st.success(
                    f"Detected {len(review.detections)} PDF candidate(s) in {processed_count} PDF(s). "
                    f"Processed {successful_images} image(s)."
                )
        except Exception as exc:  # Streamlit should show local errors without uploading data.
            st.error(f"Detection failed: {exc}")

review: ReviewSession = st.session_state.review
pdf_paths = [Path(path) for path in st.session_state.pdf_paths]

st.header("2. Review detections in context")
if st.session_state.detection_warnings:
    with st.expander("Skipped files", expanded=True):
        for warning in st.session_state.detection_warnings:
            st.warning(warning)

if st.session_state.image_results:
    with st.expander("Image OCR exports", expanded=True):
        for result in st.session_state.image_results:
            if result.error:
                st.warning(f"{result.input_filename}: {result.error}")
            else:
                output_path = Path(result.output_filename)
                st.write(f"{result.input_filename} -> {output_path.name} ({len(result.redactions)} redaction(s))")
                _download_file(output_path, label_prefix="Download image")

if not review.detections:
    st.info("Select PDFs and click 'Detect locally' to begin.")
else:
    pending = sum(1 for d in review.detections if d.status == DetectionStatus.PENDING)
    approved = sum(1 for d in review.detections if d.status == DetectionStatus.APPROVED)
    rejected = sum(1 for d in review.detections if d.status == DetectionStatus.REJECTED)
    st.write(f"Pending: {pending} · Approved: {approved} · Rejected: {rejected}")

    grouped_counts: dict[tuple[str, str, str, str, str], int] = {}
    for detection in review.detections:
        key = (
            detection.document_name,
            detection.entity_type,
            detection.original_text,
            detection.replacement_label,
            detection.status.value,
        )
        grouped_counts[key] = grouped_counts.get(key, 0) + 1
    st.subheader("Detection summary")
    st.caption("Review original → replacement groups first. Open individual detections only when you need to inspect exceptions.")
    st.dataframe(
        [
            {
                "File": document_name,
                "Entity type": entity_type,
                "Original": original_text,
                "Replacement": replacement_label,
                "Status": status,
                "Count": count,
            }
            for (document_name, entity_type, original_text, replacement_label, status), count in sorted(grouped_counts.items())
        ],
        hide_index=True,
        use_container_width=True,
    )

    st.subheader("Bulk review")
    bulk_col1, bulk_col2 = st.columns(2)
    with bulk_col1:
        if st.button("Approve all pending", disabled=pending == 0):
            changed = review.approve_pending()
            st.success(f"Approved {changed} pending detection(s).")
            st.rerun()
    with bulk_col2:
        if st.button("Reject all pending", disabled=pending == 0):
            changed = review.reject_pending(reason="Bulk rejected in local UI")
            st.warning(f"Rejected {changed} pending detection(s).")
            st.rerun()

    pending_detections = [d for d in review.detections if d.status == DetectionStatus.PENDING]
    if pending_detections:
        file_options = sorted({d.document_name for d in pending_detections})
        type_options = sorted({d.entity_type for d in pending_detections})
        scoped_col1, scoped_col2 = st.columns(2)
        with scoped_col1:
            selected_file = st.selectbox("Pending file", file_options)
            if st.button("Approve pending in this file"):
                changed = review.approve_pending(document_name=selected_file)
                st.success(f"Approved {changed} pending detection(s) in {selected_file}.")
                st.rerun()
        with scoped_col2:
            selected_type = st.selectbox("Pending type", type_options)
            if st.button("Approve pending of this type"):
                changed = review.approve_pending(entity_type=selected_type)
                st.success(f"Approved {changed} pending {selected_type} detection(s).")
                st.rerun()

    show_individual_editor = st.toggle(
        "Show individual detection editor",
        value=False,
        help="Leave this closed for normal review. Open it only for spot checks or row-level corrections.",
    )
    if show_individual_editor:
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

st.header("4. Download ZIP output")
if review.detections:
    replacement_map = review.export_replacement_map()
    st.caption(f"Replacement map contains {len(replacement_map)} approved item(s). It is hidden by default because it can be long and sensitive.")
    if st.toggle("Show replacement map preview", value=False):
        st.json(replacement_map)

    st.caption("In Streamlit Community, generated files live in the cloud session until you download this ZIP.")
    try:
        review.confirm_replacement_map()
        exported = export_reviewed_pdfs(review, output_dir)
        map_path, audit_path = write_local_review_artifacts(review, output_dir)
        output_paths = [path for path in [*exported, map_path, audit_path] if path.exists()]
        if output_paths:
            st.download_button(
                "Download ZIP output",
                data=_output_zip(output_paths),
                file_name="redaction_outputs.zip",
                mime="application/zip",
                key="download-redaction-outputs-zip",
                type="primary",
            )
            st.caption("ZIP includes redacted PDFs, replacement map JSON, and audit JSON.")
        else:
            st.warning("No output files were generated. Check that there are approved detections before downloading.")
    except Exception as exc:
        st.warning(f"Finish review before downloading ZIP: {exc}")
