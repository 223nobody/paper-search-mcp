from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .cache import (
    get_search_session,
    update_search_session_metadata,
    write_selection_ui_state,
)
from .config import get_env
from .engine.parse import _parse_selected_indices


SELECTION_CONFIRMATION_TIMEOUT_ENV = "SELECTION_CONFIRMATION_TIMEOUT_SECONDS"
DEFAULT_SELECTION_CONFIRMATION_TIMEOUT_SECONDS = 120


def selection_confirmation_timeout_seconds() -> int:
    raw = (
        get_env(
            SELECTION_CONFIRMATION_TIMEOUT_ENV,
            str(DEFAULT_SELECTION_CONFIRMATION_TIMEOUT_SECONDS),
        ).strip()
        or str(DEFAULT_SELECTION_CONFIRMATION_TIMEOUT_SECONDS)
    )
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_SELECTION_CONFIRMATION_TIMEOUT_SECONDS


def selection_revision(session: Dict[str, Any]) -> str:
    papers = session.get("papers", []) if isinstance(session, dict) else []
    total = len(papers) if isinstance(papers, list) else 0
    return str(session.get("updated_at") or session.get("created_at") or total)


def format_selected_indices(indices: List[int]) -> str:
    return ",".join(str(index) for index in indices)


def normalize_selected_indices(selected_indices: str, max_index: int) -> List[int]:
    return _parse_selected_indices(selected_indices or "", max_index)


def _selected_paper_fingerprint(
    papers: List[Dict[str, Any]],
    indices: List[int],
) -> List[str]:
    values: List[str] = []
    for index in indices:
        if index < 1 or index > len(papers):
            values.append(f"{index}:")
            continue
        paper = papers[index - 1]
        if not isinstance(paper, dict):
            values.append(f"{index}:")
            continue
        values.append(
            "|".join(
                [
                    str(index),
                    str(paper.get("source") or ""),
                    str(paper.get("paper_id") or ""),
                    str(paper.get("doi") or ""),
                    str(paper.get("pdf_url") or ""),
                    str(paper.get("title") or ""),
                ]
            )
        )
    return values


def create_selection_confirmation_token(
    *,
    selection_token: str,
    selected_indices: str,
    action: str = "download",
    save_path: str = "",
    cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a one-use UI confirmation token for the exact selection."""
    session = get_search_session(selection_token, cache_dir)
    if not session:
        return {
            "status": "not_found",
            "selection_token": selection_token,
            "message": "Search session not found.",
        }
    papers = session.get("papers", [])
    if not isinstance(papers, list):
        papers = []
    try:
        indices = normalize_selected_indices(selected_indices, len(papers))
    except ValueError as exc:
        return {
            "status": "invalid_selection",
            "selection_token": selection_token,
            "message": str(exc),
            "total": len(papers),
        }
    revision = selection_revision(session)
    selected_arg = format_selected_indices(indices)
    timeout_seconds = selection_confirmation_timeout_seconds()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
    ).isoformat()
    token = secrets.token_urlsafe(32)
    pending = {
        "token": token,
        "selected_indices": selected_arg,
        "selected_paper_fingerprint": _selected_paper_fingerprint(papers, indices),
        "selection_revision": revision,
        "action": str(action or "download"),
        "save_path": str(save_path or ""),
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at,
        "timeout_seconds": timeout_seconds,
        "used": False,
    }
    update_search_session_metadata(
        selection_token,
        {
            "pending_selection_confirmation": pending,
        },
        cache_dir,
    )
    return {
        "status": "ok",
        "selection_token": selection_token,
        "selection_confirmation_token": token,
        "selected_indices": indices,
        "selected_indices_arg": selected_arg,
        "selection_revision": revision,
        "expires_at": expires_at,
        "timeout_seconds": timeout_seconds,
    }


def confirmation_required_response(
    *,
    selection_token: str,
    selected_indices: str = "",
    message: str = "",
) -> Dict[str, Any]:
    return {
        "status": "confirmation_required",
        "selection_token": selection_token,
        "selected_indices": selected_indices,
        "downloaded": 0,
        "failed": 0,
        "message": message
        or (
            "A real checkbox UI confirmation is required before downloading "
            "more than 10 papers."
        ),
    }


def consume_selection_confirmation_token(
    *,
    selection_token: str,
    selected_indices: str,
    confirmation_token: str,
    confirmed_via: str,
    action: str = "download",
    save_path: str = "",
    cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate and consume a UI-issued token, then mark selection confirmed."""
    if not confirmation_token:
        return confirmation_required_response(
            selection_token=selection_token,
            selected_indices=selected_indices,
        )

    session = get_search_session(selection_token, cache_dir)
    if not session:
        return {
            "status": "not_found",
            "selection_token": selection_token,
            "message": "Search session not found.",
        }
    papers = session.get("papers", [])
    if not isinstance(papers, list):
        papers = []
    try:
        indices = normalize_selected_indices(selected_indices or "", len(papers))
    except ValueError as exc:
        return {
            "status": "invalid_selection",
            "selection_token": selection_token,
            "message": str(exc),
            "total": len(papers),
        }

    metadata = session.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    pending = metadata.get("pending_selection_confirmation")
    if not isinstance(pending, dict):
        return confirmation_required_response(
            selection_token=selection_token,
            selected_indices=selected_indices,
            message="Selection must be confirmed from the checkbox UI before download.",
        )
    if bool(pending.get("used")):
        return {
            "status": "confirmation_used",
            "selection_token": selection_token,
            "downloaded": 0,
            "failed": 0,
            "message": "Selection confirmation token has already been used.",
        }
    expected_token = str(pending.get("token") or "")
    if not hmac.compare_digest(expected_token, confirmation_token):
        return {
            "status": "invalid_confirmation",
            "selection_token": selection_token,
            "downloaded": 0,
            "failed": 0,
            "message": "Selection confirmation token is missing or invalid.",
        }
    expires_at = str(pending.get("expires_at") or "")
    if expires_at:
        try:
            if datetime.now(timezone.utc) >= datetime.fromisoformat(expires_at):
                return {
                    "status": "confirmation_expired",
                    "selection_token": selection_token,
                    "downloaded": 0,
                    "failed": 0,
                    "message": "Selection confirmation token expired. Reopen the selector.",
                }
        except ValueError:
            return {
                "status": "invalid_confirmation",
                "selection_token": selection_token,
                "downloaded": 0,
                "failed": 0,
                "message": "Selection confirmation token metadata is invalid.",
            }

    selected_arg = format_selected_indices(indices)
    expected_fingerprint = pending.get("selected_paper_fingerprint")
    if isinstance(expected_fingerprint, list) and expected_fingerprint != _selected_paper_fingerprint(papers, indices):
        return {
            "status": "stale_selection",
            "selection_token": selection_token,
            "downloaded": 0,
            "failed": 0,
            "message": "Selection candidates changed; refresh the selector before download.",
        }
    if str(pending.get("selected_indices") or "") != selected_arg:
        return {
            "status": "selection_mismatch",
            "selection_token": selection_token,
            "confirmed_selected_indices": str(pending.get("selected_indices") or ""),
            "requested_selected_indices": selected_arg,
            "downloaded": 0,
            "failed": 0,
            "message": "Selected indices do not match the checkbox confirmation.",
        }
    revision = selection_revision(session)
    if str(pending.get("action") or "download") != str(action or "download"):
        return {
            "status": "invalid_confirmation",
            "selection_token": selection_token,
            "downloaded": 0,
            "failed": 0,
            "message": "Selection confirmation token action does not match this request.",
        }
    expected_save_path = str(pending.get("save_path") or "")
    requested_save_path = str(save_path or "")
    if expected_save_path and requested_save_path and expected_save_path != requested_save_path:
        return {
            "status": "invalid_confirmation",
            "selection_token": selection_token,
            "downloaded": 0,
            "failed": 0,
            "message": "Selection confirmation token save path does not match this request.",
        }

    confirmed_via_name = str(confirmed_via or "ui").strip() or "ui"
    write_selection_ui_state(
        selection_token,
        {
            "selected_indices": indices,
            "selected_indices_arg": selected_arg,
            "selection_revision": revision,
            "submitted": True,
        },
        cache_dir,
    )
    consumed = dict(pending)
    consumed["used"] = True
    consumed["used_at"] = datetime.now(timezone.utc).isoformat()
    consumed["used_by"] = confirmed_via_name
    updated = update_search_session_metadata(
        selection_token,
        {
            "large_batch_selection_satisfied": True,
            "confirmed_selected_indices": selected_arg,
            "confirmed_via": confirmed_via_name,
            "selection_revision": revision,
            "confirmation_id": secrets.token_urlsafe(16),
            "pending_selection_confirmation": consumed,
        },
        cache_dir,
    )
    return {
        "status": "confirmed" if updated else "not_found",
        "selection_token": selection_token,
        "selected_indices": indices,
        "selected_indices_arg": selected_arg,
        "selection_revision": revision,
        "confirmed_via": confirmed_via_name,
        "total": len(indices),
    }
