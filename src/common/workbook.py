"""
Uploading the contracts workbook for the code interpreter.
================================================================================

Both implementations attach the same file, and both reach it through an OpenAI
client - `OpenAIChatClient`'s inner client on one side, the project's client on
the other - so the upload itself is shared.

Two things are worth knowing before copying this:

* The upload is **not** idempotent. `files.create` returns a new id every time,
  and a code interpreter container with the same file twice is just wasted
  money. `ensure_workbook_uploaded` therefore looks for an existing file with
  the same filename first.
* `purpose="assistants"` is what makes a file eligible for the code interpreter
  container. `purpose="user_data"` uploads fine and then silently is not there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.common.config import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKBOOK_PATH = REPO_ROOT / "data" / "fiberops-contracts.xlsx"

UPLOAD_PURPOSE = "assistants"


def workbook_path() -> Path:
    """The workbook, or an error explaining how to produce it."""
    if not WORKBOOK_PATH.exists():
        raise ConfigError(
            f"The sample workbook is missing: {WORKBOOK_PATH}\n\n"
            "Generate it with:\n"
            "    python -m infra.make_workbook"
        )
    return WORKBOOK_PATH


def ensure_workbook_uploaded(files_api: Any, *, reuse: bool = True) -> str:
    """Return the file id of the workbook, uploading it only when needed.

    `files_api` is the `.files` namespace of an OpenAI client. Both
    implementations have one, which is why this works for both.
    """
    path = workbook_path()

    if reuse:
        existing = _find_by_filename(files_api, path.name)
        if existing is not None:
            return existing

    with path.open("rb") as handle:
        uploaded = files_api.create(file=(path.name, handle), purpose=UPLOAD_PURPOSE)
    return uploaded.id


def _find_by_filename(files_api: Any, filename: str) -> str | None:
    """Most recent upload with this name, or None.

    Listing can fail on some deployments; a failed lookup should cost a
    re-upload, not the whole turn.
    """
    try:
        listing = files_api.list()
    except Exception:  # noqa: BLE001 - fall back to uploading again
        return None

    matches = [item for item in getattr(listing, "data", []) or [] if item.filename == filename]
    if not matches:
        return None

    matches.sort(key=lambda item: getattr(item, "created_at", 0), reverse=True)
    return matches[0].id
