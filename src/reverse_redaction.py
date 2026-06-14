#!/usr/bin/env python3
"""Reverse redactions using the mapping file to restore original content.

This script reads a redacted PDF and its corresponding mapping file,
then replaces the redacted text with the original values.

IMPORTANT: The mapping file is the "key" to restore originals.
           Store it securely -- anyone with the mapping can undo the redaction.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz


def reverse_pdf(redacted_pdf: str, output_pdf: str, redactions: list[dict]) -> int:
    """Reverse redactions on a PDF using mapping data.
    Returns number of reversals applied.
    """
    doc = fitz.open(redacted_pdf)
    count = 0

    # Group redactions by page
    by_page: dict[int, list[dict]] = {}
    for r in redactions:
        page_num = r["page"] - 1  # mapping uses 1-based
        by_page.setdefault(page_num, []).append(r)

    for page_num, page_reds in by_page.items():
        if page_num >= len(doc):
            continue
        page = doc[page_num]

        for r in page_reds:
            bbox = r.get("bbox", {})
            if not bbox:
                continue

            rect = fitz.Rect(bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"])
            original_text = r.get("from", "")
            if not original_text:
                continue

            # White out the redacted text
            page.add_redact_annot(
                rect,
                text=original_text,
                fontsize=0,  # auto-fit
                fill=(1, 1, 1),
            )
            count += 1

        page.apply_redactions()

    doc.save(output_pdf, garbage=4, deflate=True)
    doc.close()
    return count


def main():
    parser = argparse.ArgumentParser(
        description="Reverse redactions using mapping file to restore original content"
    )
    parser.add_argument("--mapping", required=True, help="Path to redaction_mapping.json")
    parser.add_argument("--input-dir", required=True, help="Directory containing redacted PDFs")
    parser.add_argument("--output-dir", required=True, help="Directory for restored PDFs")
    parser.add_argument("--file", help="Reverse a specific file only (by output filename)")
    args = parser.parse_args()

    mapping_path = Path(args.mapping)
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(mapping_path, "r") as f:
        mapping_data = json.load(f)

    print(f"Loaded mapping: {mapping_data['total_files']} files, "
          f"{mapping_data['total_redactions']} redactions")
    print(f"Output directory: {output_dir}")
    print("-" * 70)

    total_reversed = 0
    errors = []

    for file_entry in mapping_data["files"]:
        output_filename = file_entry["output_filename"]

        # Skip if user specified a specific file
        if args.file and args.file != output_filename:
            continue

        redacted_path = input_dir / output_filename
        if not redacted_path.exists():
            print(f"\n  SKIP: {output_filename} (not found in {input_dir})")
            continue

        # Use original input filename for the restored file
        restored_name = file_entry["input_filename"]
        restored_path = output_dir / restored_name

        redactions = file_entry.get("redactions", [])
        if not redactions:
            print(f"\n  SKIP: {output_filename} (no redactions to reverse)")
            continue

        print(f"\n  Reversing: {output_filename}")
        try:
            count = reverse_pdf(str(redacted_path), str(restored_path), redactions)
            print(f"    -> {restored_name} ({count} reversals applied)")
            total_reversed += count
        except Exception as e:
            print(f"    ERROR: {e}")
            errors.append({"file": output_filename, "error": str(e)})

    print("\n" + "=" * 70)
    print(f"SUMMARY: {total_reversed} total reversals applied, {len(errors)} errors")

    if errors:
        print("\nFiles with errors:")
        for e in errors:
            print(f"  - {e['file']}: {e['error']}")


if __name__ == "__main__":
    main()
