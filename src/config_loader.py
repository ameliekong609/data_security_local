import yaml
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class KeywordRule:
    pattern: str
    replacement: str
    case_sensitive: bool = False
    order: int = 100


@dataclass
class AddressRule:
    pattern: str
    replacement: str
    case_sensitive: bool = False
    variants: list[str] = field(default_factory=list)


@dataclass
class FieldRule:
    keep_last: int = 0
    context_patterns: list[str] = field(default_factory=list)
    whitelist: list[str] = field(default_factory=list)


@dataclass
class FilenameRule:
    pattern: str
    replacement: str
    case_sensitive: bool = False


@dataclass
class RedactionConfig:
    client: str
    keyword_rules: list[KeywordRule]
    address_rules: list[AddressRule]
    field_rules: dict[str, FieldRule]
    filename_rules: list[FilenameRule]


def default_redaction_config() -> RedactionConfig:
    """Return generic local rules that do not require a user-supplied config file."""

    return RedactionConfig(
        client="local",
        keyword_rules=[],
        address_rules=[],
        field_rules={
            "email": FieldRule(
                context_patterns=[
                    r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",
                ],
            ),
            "phone": FieldRule(
                keep_last=3,
                context_patterns=[
                    r"(?:Phone|Mobile|Tel|Telephone|Contact)\s*:?\s*((?:\+?\d[\d ()-]{7,}\d))",
                ],
            ),
            "account_number": FieldRule(
                keep_last=3,
                context_patterns=[
                    r"Account\s*(?:Number|No\.?|#)?\s*:?\s*(\d[\d -]{4,}\d)",
                    r"ACC(?:OUNT)?\s*:?\s*([A-Z0-9][A-Z0-9 -]{4,}[A-Z0-9])",
                ],
            ),
            "client_id": FieldRule(
                keep_last=3,
                context_patterns=[
                    r"Client\s*ID\s*:?\s*([A-Z0-9][A-Z0-9 -]{4,}[A-Z0-9])",
                    r"Customer\s*ID\s*:?\s*([A-Z0-9][A-Z0-9 -]{4,}[A-Z0-9])",
                ],
            ),
            "investor_number": FieldRule(
                keep_last=3,
                context_patterns=[
                    r"Investor\s*(?:No\.?|Number|ID)\s*:?\s*([A-Z0-9][A-Z0-9 -]{4,}[A-Z0-9])",
                ],
            ),
            "abn": FieldRule(
                keep_last=4,
                context_patterns=[
                    r"ABN\s*:?\s*(\d{2} ?\d{3} ?\d{3} ?\d{3})",
                ],
            ),
            "tfn": FieldRule(
                keep_last=3,
                context_patterns=[
                    r"TFN\s*:?\s*(\d{3} ?\d{3} ?\d{3})",
                ],
            ),
            "dob": FieldRule(
                keep_last=4,
                context_patterns=[
                    r"(?:DOB|Date of Birth)\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
                ],
            ),
        },
        filename_rules=[],
    )


def load_config(config_path: str | Path) -> RedactionConfig:
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)

    keyword_rules = []
    for item in data.get("keyword_replacements", []):
        keyword_rules.append(KeywordRule(
            pattern=item["pattern"],
            replacement=item["replacement"],
            case_sensitive=item.get("case_sensitive", False),
            order=item.get("order", 100),
        ))
    keyword_rules.sort(key=lambda r: r.order)

    address_rules = []
    for item in data.get("address_replacements", []):
        address_rules.append(AddressRule(
            pattern=item["pattern"],
            replacement=item["replacement"],
            case_sensitive=item.get("case_sensitive", False),
            variants=item.get("variants", []),
        ))

    field_rules = {}
    for name, item in data.get("field_redactions", {}).items():
        field_rules[name] = FieldRule(
            keep_last=item.get("keep_last", 0),
            context_patterns=item.get("context_patterns", []),
            whitelist=item.get("whitelist", []),
        )

    filename_rules = []
    for item in data.get("filename_replacements", []):
        filename_rules.append(FilenameRule(
            pattern=item["pattern"],
            replacement=item["replacement"],
            case_sensitive=item.get("case_sensitive", False),
        ))

    return RedactionConfig(
        client=data.get("client", "unknown"),
        keyword_rules=keyword_rules,
        address_rules=address_rules,
        field_rules=field_rules,
        filename_rules=filename_rules,
    )
