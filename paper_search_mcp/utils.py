import re
from pathlib import Path


DEFAULT_SAVE_PATH = "~/Desktop"


def resolve_save_path(save_path: str = DEFAULT_SAVE_PATH) -> str:
    """Expand a user-facing save path such as ~/Desktop to an absolute path."""
    value = (save_path or DEFAULT_SAVE_PATH).strip() or DEFAULT_SAVE_PATH
    return str(Path(value).expanduser().resolve())


def extract_doi(text: str) -> str:
    """Extract DOI from arbitrary text or URL if present."""
    if not text:
        return ""
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, re.IGNORECASE)
    return match.group(0).rstrip(".,;)") if match else ""
