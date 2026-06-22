"""Publisher direct PDF URL helpers for known open-access DOI prefixes."""

from __future__ import annotations

from typing import Callable, Optional


def _arxiv_pdf_url(doi: str) -> str:
    return doi.lower().replace("10.48550/arxiv.", "https://arxiv.org/pdf/", 1)


def _plos_pdf_url(doi: str) -> str:
    return f"https://journals.plos.org/plosone/article/file?id={doi}&type=printable"


def _elife_pdf_url(doi: str) -> str:
    return f"https://elifesciences.org/articles/{doi.split('/')[-1]}/pdf"


def _frontiers_pdf_url(doi: str) -> str:
    return f"https://www.frontiersin.org/articles/{doi}/pdf"


def _mdpi_pdf_url(doi: str) -> str:
    return f"https://www.mdpi.com/{doi}/pdf"


def _hindawi_pdf_url(doi: str) -> str:
    parts = doi.split("/", 1)
    if len(parts) != 2:
        return ""
    suffix_parts = parts[1].split(".")
    journal = suffix_parts[0] if suffix_parts else ""
    if not journal:
        return ""
    return f"https://downloads.hindawi.com/journals/{journal}/{parts[1]}.pdf"


def _springer_open_pdf_url(doi: str) -> str:
    return f"https://link.springer.com/content/pdf/{doi}.pdf"


def _nature_oa_pdf_url(doi: str) -> str:
    return f"https://www.nature.com/articles/{doi.split('/')[-1]}.pdf"


def _peerj_pdf_url(doi: str) -> str:
    return f"https://peerj.com/articles/{doi.split('/')[-1]}.pdf"


def _copernicus_pdf_url(doi: str) -> str:
    return f"https://doi.org/{doi}"


PUBLISHER_PDF_TEMPLATES: dict[str, Callable[[str], str]] = {
    "10.1038/s41467": _nature_oa_pdf_url,
    "10.1038/s41598": _nature_oa_pdf_url,
    "10.48550": _arxiv_pdf_url,
    "10.1371": _plos_pdf_url,
    "10.7554": _elife_pdf_url,
    "10.3389": _frontiers_pdf_url,
    "10.3390": _mdpi_pdf_url,
    "10.1155": _hindawi_pdf_url,
    "10.1186": _springer_open_pdf_url,
    "10.7717": _peerj_pdf_url,
    "10.5194": _copernicus_pdf_url,
}


def resolve_publisher_direct_url(doi: str) -> Optional[str]:
    """Return a best-effort direct PDF URL for known OA DOI prefixes."""
    normalized = (doi or "").strip().lower()
    if normalized.startswith("doi:"):
        normalized = normalized[4:].strip()
    if normalized.startswith("https://doi.org/"):
        normalized = normalized[len("https://doi.org/") :].strip()
    if not normalized:
        return None

    for prefix, url_fn in sorted(PUBLISHER_PDF_TEMPLATES.items(), key=lambda item: -len(item[0])):
        if normalized.startswith(prefix):
            try:
                url = url_fn(normalized)
            except Exception:
                continue
            if url:
                return url
    return None
