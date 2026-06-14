"""Redact PII from screenshot/image files using Tesseract OCR."""

import re
import pytesseract
from PIL import Image, ImageDraw
from pathlib import Path
from dataclasses import dataclass

from src.config_loader import RedactionConfig
from src.deterministic_redactor import Redaction
import fitz


@dataclass
class ImageRedactionResult:
    input_filename: str
    output_filename: str
    redactions: list[Redaction]
    error: str | None = None


def _get_ocr_data(image_path: str) -> dict:
    """Run Tesseract OCR and return word-level bounding boxes."""
    img = Image.open(image_path)
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    return data


def _find_text_regions(ocr_data: dict, search_text: str, case_sensitive: bool = False) -> list[tuple]:
    """Find bounding box regions for a text string in OCR data.
    Returns list of (x, y, w, h) tuples covering the matched words.
    """
    words = ocr_data["text"]
    n = len(words)

    # Build a list of (word, index) skipping empty entries
    word_entries = []
    for i in range(n):
        w = words[i].strip()
        if w:
            word_entries.append((w, i))

    if not word_entries:
        return []

    # Normalize search text into words
    search_words = search_text.split()
    if not search_words:
        return []

    matches = []
    for start_idx in range(len(word_entries)):
        # Try to match search_words starting from start_idx
        if start_idx + len(search_words) > len(word_entries):
            break

        matched = True
        for j, sw in enumerate(search_words):
            actual_word = word_entries[start_idx + j][0]
            if case_sensitive:
                if actual_word != sw:
                    matched = False
                    break
            else:
                if actual_word.lower() != sw.lower():
                    matched = False
                    break

        if matched:
            # Collect bounding boxes for matched words
            regions = []
            for j in range(len(search_words)):
                ocr_idx = word_entries[start_idx + j][1]
                x = ocr_data["left"][ocr_idx]
                y = ocr_data["top"][ocr_idx]
                w = ocr_data["width"][ocr_idx]
                h = ocr_data["height"][ocr_idx]
                regions.append((x, y, w, h))
            matches.append(regions)

    return matches


def _merge_regions(regions: list[tuple]) -> tuple:
    """Merge multiple word regions into one bounding box."""
    if not regions:
        return (0, 0, 0, 0)
    x_min = min(r[0] for r in regions)
    y_min = min(r[1] for r in regions)
    x_max = max(r[0] + r[2] for r in regions)
    y_max = max(r[1] + r[3] for r in regions)
    return (x_min, y_min, x_max - x_min, y_max - y_min)


def _find_pattern_regions(ocr_data: dict, pattern: str) -> list[tuple]:
    """Find regions matching a regex pattern in OCR text."""
    # Reconstruct full text with positions
    words = ocr_data["text"]
    n = len(words)

    # Build full text line by line
    matches_with_regions = []
    # Group words by line
    lines = {}
    for i in range(n):
        word = words[i].strip()
        if not word:
            continue
        line_num = ocr_data["line_num"][i]
        block_num = ocr_data["block_num"][i]
        key = (block_num, line_num)
        if key not in lines:
            lines[key] = []
        lines[key].append({
            "text": word,
            "idx": i,
            "left": ocr_data["left"][i],
            "top": ocr_data["top"][i],
            "width": ocr_data["width"][i],
            "height": ocr_data["height"][i],
        })

    for key, line_words in lines.items():
        line_text = " ".join(w["text"] for w in line_words)
        for match in re.finditer(pattern, line_text, re.IGNORECASE):
            # Find which words are covered by this match
            matched_text = match.group(0)
            char_pos = 0
            covered_words = []
            for w in line_words:
                word_start = line_text.find(w["text"], char_pos)
                word_end = word_start + len(w["text"])
                if word_end > match.start() and word_start < match.end():
                    covered_words.append(w)
                char_pos = word_end + 1

            if covered_words:
                regions = [(w["left"], w["top"], w["width"], w["height"]) for w in covered_words]
                matches_with_regions.append(regions)

    return matches_with_regions


def redact_image(image_path: Path, output_dir: Path, config: RedactionConfig) -> ImageRedactionResult:
    """Redact PII from a screenshot/image file."""
    result = ImageRedactionResult(
        input_filename=image_path.name,
        output_filename="",
        redactions=[],
    )

    try:
        # OCR the image
        ocr_data = _get_ocr_data(str(image_path))
        img = Image.open(str(image_path))
        draw = ImageDraw.Draw(img)

        redactions = []
        padding = 3  # pixels of padding around redacted area

        # Apply keyword replacements
        for rule in config.keyword_rules:
            matches = _find_text_regions(ocr_data, rule.pattern, rule.case_sensitive)
            for word_regions in matches:
                merged = _merge_regions(word_regions)
                x, y, w, h = merged
                # Draw white rectangle over the text
                draw.rectangle(
                    [x - padding, y - padding, x + w + padding, y + h + padding],
                    fill="white",
                )
                # Draw replacement text
                draw.text((x, y), rule.replacement, fill="black")

                redactions.append(Redaction(
                    page_num=0,
                    rect=fitz.Rect(x, y, x + w, y + h),
                    original_text=rule.pattern,
                    replacement_text=rule.replacement,
                    redaction_type="keyword",
                ))

        # Apply address replacements
        for rule in config.address_rules:
            all_patterns = [rule.pattern] + rule.variants
            for pattern_text in all_patterns:
                # For multi-line, search each line
                for line in pattern_text.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    matches = _find_text_regions(ocr_data, line, rule.case_sensitive)
                    for word_regions in matches:
                        merged = _merge_regions(word_regions)
                        x, y, w, h = merged
                        draw.rectangle(
                            [x - padding, y - padding, x + w + padding, y + h + padding],
                            fill="white",
                        )
                        redactions.append(Redaction(
                            page_num=0,
                            rect=fitz.Rect(x, y, x + w, y + h),
                            original_text=line,
                            replacement_text=rule.replacement,
                            redaction_type="address",
                        ))

        # Apply field redactions (BSB, account, email, ABN, etc.)
        full_text = " ".join(w for w in ocr_data["text"] if w.strip())
        for field_name, rule in config.field_rules.items():
            for pattern_str in rule.context_patterns:
                for match in re.finditer(pattern_str, full_text, re.IGNORECASE):
                    matched_value = match.group(1) if match.lastindex else match.group(0)

                    # Skip whitelisted
                    if any(wl.lower() == matched_value.lower() for wl in rule.whitelist):
                        continue
                    # Skip already masked
                    if "**" in matched_value:
                        continue

                    # Find regions for this value
                    regions_list = _find_text_regions(ocr_data, matched_value)
                    for word_regions in regions_list:
                        merged = _merge_regions(word_regions)
                        x, y, w, h = merged
                        draw.rectangle(
                            [x - padding, y - padding, x + w + padding, y + h + padding],
                            fill="white",
                        )

                        # Compute masked value
                        if field_name == "email":
                            from src.pattern_redactor import _mask_email
                            masked = _mask_email(matched_value)
                        else:
                            from src.pattern_redactor import _mask_keeping_last
                            masked = _mask_keeping_last(matched_value, rule.keep_last)

                        draw.text((x, y), masked, fill="black")

                        redactions.append(Redaction(
                            page_num=0,
                            rect=fitz.Rect(x, y, x + w, y + h),
                            original_text=matched_value,
                            replacement_text=masked,
                            redaction_type="field",
                        ))

        # Rename and save
        from src.file_renamer import rename_file
        output_name = rename_file(image_path.name, config.filename_rules)
        # Change extension to .png for clean output
        output_name = Path(output_name).stem + ".png"
        output_path = output_dir / output_name
        img.save(str(output_path))

        result.output_filename = output_name
        result.redactions = redactions

    except Exception as e:
        result.error = str(e)

    return result
