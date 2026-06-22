"""Citation formatting helpers for paper dictionaries."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


def _paper_value(paper: Dict[str, Any], *names: str) -> str:
    extra = paper.get("extra") if isinstance(paper.get("extra"), dict) else {}
    for name in names:
        value = paper.get(name)
        if value in (None, "") and isinstance(extra, dict):
            value = extra.get(name)
        if isinstance(value, (list, tuple, set)):
            return "; ".join(str(item).strip() for item in value if str(item).strip())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _authors(paper: Dict[str, Any]) -> List[str]:
    value = paper.get("authors")
    if isinstance(value, (list, tuple, set)):
        return [str(author).strip() for author in value if str(author).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"\s*;\s*|\s+ and\s+|\s*,\s*(?=[A-Z][A-Za-z'`-]+(?:\s|$))", text)
    authors = [part.strip() for part in parts if part.strip()]
    return authors or [text]


def _year(paper: Dict[str, Any]) -> str:
    for name in ("year", "published_year", "publication_year", "published_date", "date"):
        match = re.search(r"(19|20)\d{2}", _paper_value(paper, name))
        if match:
            return match.group(0)
    return ""


def _venue(paper: Dict[str, Any]) -> str:
    return _paper_value(
        paper,
        "publication_venue",
        "venue",
        "journal",
        "journal_ref",
        "conference",
        "booktitle",
        "container_title",
    )


def _entry_type(paper: Dict[str, Any]) -> str:
    text = " ".join(
        [
            _venue(paper),
            _paper_value(paper, "type", "publication_type", "crossref_type"),
            _paper_value(paper, "source"),
        ]
    ).lower()
    if any(word in text for word in ("conference", "proceedings", "workshop", "symposium", "cvpr", "iclr", "neurips", "nips", "acl", "emnlp", "icml")):
        return "inproceedings"
    if any(word in text for word in ("book", "monograph", "textbook")):
        return "book"
    return "article"


def _bibtex_escape(value: str) -> str:
    return (
        value.replace("\\", "\\textbackslash{}")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("&", "\\&")
    )


def _citation_key(paper: Dict[str, Any]) -> str:
    authors = _authors(paper)
    last_name = "paper"
    if authors:
        first_author = authors[0].replace(",", " ").split()
        if first_author:
            last_name = re.sub(r"[^A-Za-z0-9]+", "", first_author[-1]).lower() or "paper"
    year = _year(paper) or "nd"
    title_words = re.findall(r"[A-Za-z0-9]+", _paper_value(paper, "title").lower())
    title_word = title_words[0] if title_words else "untitled"
    return f"{last_name}{year}{title_word}"


def _field_pairs(paper: Dict[str, Any]) -> List[tuple[str, str]]:
    venue = _venue(paper)
    entry_type = _entry_type(paper)
    fields = [
        ("title", _paper_value(paper, "title")),
        ("author", " and ".join(_authors(paper))),
        ("year", _year(paper)),
        ("doi", _paper_value(paper, "doi")),
        ("url", _paper_value(paper, "url", "pdf_url")),
    ]
    if venue:
        fields.append(("booktitle" if entry_type == "inproceedings" else "journal", venue))
    for target, names in (
        ("volume", ("volume",)),
        ("number", ("issue", "number")),
        ("pages", ("pages", "page")),
        ("publisher", ("publisher",)),
    ):
        fields.append((target, _paper_value(paper, *names)))
    return [(name, value) for name, value in fields if value]


def to_bibtex(paper: Dict[str, Any], key: str = "") -> str:
    """Export a paper dictionary as a BibTeX entry."""
    entry_key = key.strip() or _citation_key(paper)
    lines = [f"@{_entry_type(paper)}{{{entry_key},"]
    for name, value in _field_pairs(paper):
        lines.append(f"  {name} = {{{_bibtex_escape(value)}}},")
    if len(lines) > 1:
        lines[-1] = lines[-1].rstrip(",")
    lines.append("}")
    return "\n".join(lines)


def to_ris(paper: Dict[str, Any]) -> str:
    """Export a paper dictionary as RIS."""
    ris_type = {
        "inproceedings": "CPAPER",
        "book": "BOOK",
    }.get(_entry_type(paper), "JOUR")
    lines = [f"TY  - {ris_type}"]
    for author in _authors(paper):
        lines.append(f"AU  - {author}")
    mapping = [
        ("TI", _paper_value(paper, "title")),
        ("PY", _year(paper)),
        ("JO", _venue(paper)),
        ("DO", _paper_value(paper, "doi")),
        ("UR", _paper_value(paper, "url", "pdf_url")),
        ("VL", _paper_value(paper, "volume")),
        ("IS", _paper_value(paper, "issue", "number")),
        ("SP", _paper_value(paper, "pages", "page")),
        ("PB", _paper_value(paper, "publisher")),
    ]
    lines.extend(f"{tag}  - {value}" for tag, value in mapping if value)
    lines.append("ER  -")
    return "\n".join(lines)


def to_endnote(paper: Dict[str, Any]) -> str:
    """Export a paper dictionary as EndNote tagged text."""
    endnote_type = {
        "inproceedings": "Conference Paper",
        "book": "Book",
    }.get(_entry_type(paper), "Journal Article")
    lines = [f"%0 {endnote_type}"]
    lines.extend(f"%A {author}" for author in _authors(paper))
    mapping = [
        ("%T", _paper_value(paper, "title")),
        ("%D", _year(paper)),
        ("%J", _venue(paper)),
        ("%R", _paper_value(paper, "doi")),
        ("%U", _paper_value(paper, "url", "pdf_url")),
        ("%V", _paper_value(paper, "volume")),
        ("%N", _paper_value(paper, "issue", "number")),
        ("%P", _paper_value(paper, "pages", "page")),
        ("%I", _paper_value(paper, "publisher")),
    ]
    lines.extend(f"{tag} {value}" for tag, value in mapping if value)
    return "\n".join(lines)


def export_citation(paper: Dict[str, Any], format: str = "bibtex", key: str = "") -> str:
    """Export a paper dictionary as bibtex, ris, or endnote/enw."""
    fmt = re.sub(r"[^a-z0-9]+", "", (format or "bibtex").strip().lower())
    if fmt in {"bib", "bibtex"}:
        return to_bibtex(paper, key=key)
    if fmt == "ris":
        return to_ris(paper)
    if fmt in {"endnote", "enw"}:
        return to_endnote(paper)
    raise ValueError("Unsupported citation format. Use bibtex, ris, or endnote.")


def export_citations(papers: Iterable[Dict[str, Any]], format: str = "bibtex") -> str:
    """Export multiple paper dictionaries as one citation text block."""
    separator = "\n\n" if (format or "bibtex").strip().lower() in {"bib", "bibtex"} else "\n"
    return separator.join(export_citation(paper, format=format) for paper in papers)
