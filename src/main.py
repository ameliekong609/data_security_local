#!/usr/bin/env python3
"""PDF Data Redaction Pipeline - processes PDFs locally without sending data externally."""

import argparse
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from src.config_loader import load_config
from src.deterministic_redactor import (
    find_keyword_redactions,
    find_address_redactions,
    deduplicate_redactions,
)
from src.pattern_redactor import find_field_redactions
from src.pdf_writer import apply_redactions
from src.file_renamer import rename_file
from src.mapping_generator import (
    FileMapping,
    generate_audit_log,
    generate_mapping_csv,
    generate_mapping_json,
)
from src.services.custom_terms import CustomTermDetector
from src.services.profiles import ProfileStore, RedactionProfile


def process_pdf(
    pdf_path: Path,
    output_dir: Path,
    config,
    scan: bool = False,
    model: str = "phi3:mini",
    passwords: dict = None,
    profile: RedactionProfile | None = None,
):

    summary = {
        "input": pdf_path.name,
        "output": None,
        "keyword_redactions": 0,
        "address_redactions": 0,
        "field_redactions": 0,
        "custom_term_redactions": 0,
        "total_redactions": 0,
        "llm_findings": 0,
        "error": None,
    }

    try:
        doc = fitz.open(str(pdf_path))
        if doc.is_encrypted:
            pw = (passwords or {}).get(pdf_path.name, "")
            if not doc.authenticate(pw):
                raise ValueError("wrong password or no password provided")

        # Find all redactions
        keyword_reds = find_keyword_redactions(doc, config.keyword_rules)
        address_reds = find_address_redactions(doc, config.address_rules)
        field_reds = find_field_redactions(doc, config.field_rules)
        custom_term_reds = []
        if profile is not None:
            custom_term_reds = CustomTermDetector(profile).redactions_for_pdf(doc, file_id=pdf_path.name)

        doc.close()

        # Combine and deduplicate
        all_redactions = deduplicate_redactions(address_reds + keyword_reds + field_reds + custom_term_reds)

        # Assign unique IDs for reversibility
        import hashlib
        for i, r in enumerate(all_redactions):
            uid = hashlib.md5(
                f"{pdf_path.name}|{r.page_num}|{r.rect}|{r.original_text}|{i}".encode()
            ).hexdigest()[:12]
            r.redaction_id = f"R-{uid}"

        # Rename output file
        output_name = rename_file(pdf_path.name, config.filename_rules)
        output_path = output_dir / output_name

        # Apply redactions
        pw = (passwords or {}).get(pdf_path.name, "")
        count = apply_redactions(str(pdf_path), str(output_path), all_redactions, password=pw)

        # LLM scan on the REDACTED output (so we never flag already-redacted content)
        llm_findings = []
        if scan:
            from src.llm_scanner import scan_document
            print(f"    Scanning redacted output with local LLM ({model})...")
            redacted_doc = fitz.open(str(output_path))
            pages_text = [
                (i, redacted_doc[i].get_text("text"))
                for i in range(len(redacted_doc))
            ]
            redacted_doc.close()
            llm_findings = scan_document(pages_text, model=model)
            if llm_findings:
                print(f"    ⚠ LLM found {len(llm_findings)} potential PII item(s)")
            else:
                print(f"    ✓ LLM scan clean - no additional PII detected")

        summary["output"] = output_name
        summary["keyword_redactions"] = len([r for r in all_redactions if r.redaction_type == "keyword"])
        summary["address_redactions"] = len([r for r in all_redactions if r.redaction_type == "address"])
        summary["field_redactions"] = len([r for r in all_redactions if r.redaction_type == "field"])
        summary["custom_term_redactions"] = len([r for r in all_redactions if r.redaction_type == "custom_term"])
        summary["total_redactions"] = count
        summary["llm_findings"] = len(llm_findings)

        mapping = FileMapping(
            input_filename=pdf_path.name,
            output_filename=output_name,
            redactions=all_redactions,
            llm_findings=llm_findings,
        )

        return summary, mapping

    except Exception as e:
        summary["error"] = str(e)
        mapping = FileMapping(
            input_filename=pdf_path.name,
            output_filename="",
            redactions=[],
            llm_findings=[],
        )
        return summary, mapping


def main():
    parser = argparse.ArgumentParser(description="Redact PII from PDF documents")
    parser.add_argument("--config", required=True, help="Path to redaction rules YAML")
    parser.add_argument("--input-dir", required=True, help="Directory containing input PDFs")
    parser.add_argument("--output-dir", required=True, help="Directory for redacted output PDFs")
    parser.add_argument("--scan", action="store_true", help="Run local LLM scan for missed PII (requires Ollama)")
    parser.add_argument("--model", default="llama3.1:8b", help="Ollama model for LLM scan (default: llama3.1:8b)")
    parser.add_argument("--mapping", choices=["csv", "json", "both"], default="both",
                        help="Mapping file format (default: both)")
    parser.add_argument("--password", action="append", nargs=2, metavar=("FILENAME", "PASSWORD"),
                        help="Password for encrypted PDF, e.g. --password file.pdf 12345")
    parser.add_argument("--profile-id", help="Local custom redaction profile id")
    parser.add_argument("--profile-dir", help="Local custom profile directory (defaults to app data directory)")
    args = parser.parse_args()

    # Build password lookup
    passwords = {}
    if args.password:
        for filename, pw in args.password:
            passwords[filename] = pw

    config = load_config(args.config)
    profile = None
    if args.profile_id:
        profile = ProfileStore(args.profile_dir).get_profile(args.profile_id)
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(list(input_dir.glob("*.pdf")) + list(input_dir.glob("*.PDF")))
    image_extensions = ("*.jpg", "*.jpeg", "*.png", "*.JPG", "*.JPEG", "*.PNG")
    image_files = sorted([f for ext in image_extensions for f in input_dir.glob(ext)])

    total_files = len(pdf_files) + len(image_files)
    if total_files == 0:
        print("No PDF or image files found in input directory.")
        return

    print(f"Processing {len(pdf_files)} PDF + {len(image_files)} image files for client: {config.client}")
    print(f"Output directory: {output_dir}")
    if args.scan:
        print(f"LLM scan enabled (model: {args.model})")
    print("-" * 70)

    total_redactions = 0
    total_llm_findings = 0
    errors = []
    all_mappings = []

    # Process PDFs
    for pdf_path in pdf_files:
        print(f"\n  Processing: {pdf_path.name}")
        summary, mapping = process_pdf(
            pdf_path, output_dir, config,
            scan=args.scan, model=args.model,
            passwords=passwords,
            profile=profile,
        )
        all_mappings.append(mapping)

        if summary["error"]:
            print(f"    ERROR: {summary['error']}")
            errors.append(summary)
        else:
            print(f"    -> {summary['output']}")
            print(f"       Keywords: {summary['keyword_redactions']}, "
                  f"Addresses: {summary['address_redactions']}, "
                  f"Fields: {summary['field_redactions']}, "
                  f"Custom terms: {summary['custom_term_redactions']}, "
                  f"Total: {summary['total_redactions']}")
            total_redactions += summary["total_redactions"]
            total_llm_findings += summary["llm_findings"]

    # Process images
    if image_files:
        from src.image_redactor import redact_image
        for img_path in image_files:
            print(f"\n  Processing: {img_path.name} (image)")
            result = redact_image(img_path, output_dir, config)
            if result.error:
                print(f"    ERROR: {result.error}")
                errors.append({"input": img_path.name, "error": result.error})
            else:
                print(f"    -> {result.output_filename}")
                print(f"       Redactions: {len(result.redactions)}")
                total_redactions += len(result.redactions)

            all_mappings.append(FileMapping(
                input_filename=img_path.name,
                output_filename=result.output_filename,
                redactions=result.redactions,
                llm_findings=[],
            ))

    # Generate mapping files
    if args.mapping in ("csv", "both"):
        csv_path = output_dir / "redaction_mapping.csv"
        generate_mapping_csv(all_mappings, csv_path)
        print(f"\nMapping CSV: {csv_path}")

    if args.mapping in ("json", "both"):
        json_path = output_dir / "redaction_mapping.json"
        generate_mapping_json(all_mappings, json_path)
        print(f"Mapping JSON: {json_path}")

    audit_path = output_dir / "redaction_audit.json"
    generate_audit_log(all_mappings, audit_path)
    print(f"Audit log: {audit_path}")

    print("\n" + "=" * 70)
    summary_parts = [
        f"{total_files} files processed",
        f"{total_redactions} total redactions applied",
        f"{len(errors)} errors",
    ]
    if args.scan:
        summary_parts.append(f"{total_llm_findings} LLM findings")
    print(f"SUMMARY: {', '.join(summary_parts)}")

    if total_llm_findings > 0:
        print(f"\n⚠ WARNING: LLM detected {total_llm_findings} potential unredacted PII item(s).")
        print("  Review the mapping file for details and update redaction_rules.yaml if needed.")

    if errors:
        print("\nFiles with errors:")
        for e in errors:
            print(f"  - {e['input']}: {e['error']}")


if __name__ == "__main__":
    main()
