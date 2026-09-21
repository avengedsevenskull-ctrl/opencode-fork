"""Exact port of OpenCode's Wildcard.match from packages/core/src/util/wildcard.ts."""

import re
import sys


def match(input_str: str, pattern: str) -> bool:
    normalized = input_str.replace("\\", "/")
    escaped = (
        pattern.replace("\\", "/")
        .replace(".", "\\.")
        .replace("+", "\\+")
        .replace("^", "\\^")
        .replace("$", "\\$")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("[", "\\[")
        .replace("|", "\\|")
        .replace("]", "\\]")
        .replace("*", ".*")
        .replace("?", ".")
    )
    # Trailing " .*" is made optional: "playwright_*" matches both
    # "playwright" and "playwright_browser_navigate"
    if escaped.endswith(" .*"):
        escaped = escaped[:-3] + "( .*)?"
    flags = re.DOTALL
    if sys.platform == "win32":
        flags |= re.IGNORECASE
    return re.fullmatch(escaped, normalized, flags) is not None
