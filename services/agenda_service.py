from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


AGENDA_FORMAT = "stage-cue-agenda"
AGENDA_VERSION = 3
SUPPORTED_AGENDA_VERSIONS = {1, 2, 3}
VALID_TRANSITIONS = {"direct", "hide_text", "show_logo"}


def _transition(value: Any) -> str:
    value = str(value or "direct").strip().lower()
    return value if value in VALID_TRANSITIONS else "direct"


def build_agenda_document(
    church_id: str,
    church_name: str,
    agenda_name: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    serialized_items: list[dict[str, Any]] = []
    for item in items:
        kind = str(item.get("_kind") or "song").lower()
        if kind == "bible":
            serialized_items.append(
                {
                    "kind": "bible",
                    "title": str(item.get("title", "Bible passage")),
                    "reference": str(item.get("reference", "")),
                    "translation": str(item.get("translation", "kjv")),
                    "translationName": str(item.get("translation_name", "")),
                    "verses": list(item.get("verses", [])),
                    "transition": _transition(item.get("_transition")),
                }
            )
        elif kind == "presentation":
            serialized_items.append(
                {
                    "kind": "presentation",
                    "title": str(item.get("title", "Presentation")),
                    "path": str(item.get("path", "")),
                    "transition": _transition(item.get("_transition")),
                }
            )
        else:
            serialized_items.append(
                {
                    "kind": "song",
                    "songId": str(item["id"]),
                    "title": str(item.get("title", "Untitled")),
                    "transition": _transition(item.get("_transition")),
                }
            )
    return {
        "format": AGENDA_FORMAT,
        "version": AGENDA_VERSION,
        "churchId": church_id,
        "churchName": church_name,
        "name": agenda_name.strip() or "Untitled Agenda",
        "savedAt": datetime.now(timezone.utc).isoformat(),
        "items": serialized_items,
    }


def validate_agenda_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("format") != AGENDA_FORMAT:
        raise ValueError("This is not a Stage Cue agenda file.")
    version = document.get("version")
    if version not in SUPPORTED_AGENDA_VERSIONS:
        raise ValueError("This Stage Cue agenda version is not supported.")
    if not isinstance(document.get("churchId"), str) or not document["churchId"]:
        raise ValueError("The agenda does not identify its church.")
    items = document.get("items")
    if not isinstance(items, list):
        raise ValueError("The agenda item list is invalid.")

    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("The agenda contains an invalid entry.")
        kind = str(item.get("kind") or "song").strip().lower()
        if kind == "bible":
            verses = item.get("verses")
            if not isinstance(verses, list) or not verses:
                raise ValueError("The agenda contains a Bible entry without verses.")
            normalized_items.append(
                {
                    "kind": "bible",
                    "title": str(item.get("title", "Bible passage")),
                    "reference": str(item.get("reference", "")),
                    "translation": str(item.get("translation", "kjv")),
                    "translationName": str(item.get("translationName", "")),
                    "verses": verses,
                    "transition": _transition(item.get("transition")),
                }
            )
            continue
        if kind == "presentation":
            path = str(item.get("path", "")).strip()
            if not path:
                raise ValueError("The agenda contains a presentation without a file path.")
            normalized_items.append(
                {
                    "kind": "presentation",
                    "title": str(item.get("title") or Path(path).stem or "Presentation"),
                    "path": path,
                    "transition": _transition(item.get("transition")),
                }
            )
            continue
        if not str(item.get("songId", "")).strip():
            raise ValueError("The agenda contains an invalid song entry.")
        normalized_items.append(
            {
                "kind": "song",
                "songId": str(item["songId"]),
                "title": str(item.get("title", "Untitled")),
                "transition": _transition(item.get("transition")),
            }
        )
    return {**document, "version": int(version), "items": normalized_items}


def save_agenda_file(path: Path | str, document: dict[str, Any]) -> None:
    validated = validate_agenda_document(document)
    Path(path).write_text(
        json.dumps(validated, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_agenda_file(path: Path | str) -> dict[str, Any]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"The agenda file could not be read: {exc}") from exc
    return validate_agenda_document(document)
