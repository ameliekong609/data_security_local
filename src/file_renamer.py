import re
from src.config_loader import FilenameRule


def rename_file(filename: str, rules: list[FilenameRule]) -> str:
    result = filename
    for rule in rules:
        flags = 0 if rule.case_sensitive else re.IGNORECASE
        result = re.sub(re.escape(rule.pattern), rule.replacement, result, flags=flags)
    return result
