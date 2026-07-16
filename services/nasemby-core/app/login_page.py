from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit


CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def safe_next_location(value):
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or CONTROL_CHARACTERS.search(value):
        return "/"
    try:
        parsed = urlsplit(value)
        decoded_path = unquote(parsed.path)
    except (TypeError, ValueError, UnicodeError):
        return "/"
    if parsed.scheme or parsed.netloc or re.match(r"^/auth(?:/|$)", decoded_path, re.IGNORECASE):
        return "/"
    return value
