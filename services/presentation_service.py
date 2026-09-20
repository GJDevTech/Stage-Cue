from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from uuid import uuid4


_CHORD = re.compile(
    r"\[(?:[A-G](?:#|b)?(?:m|maj|min|sus|dim|aug|add)?\d*"
    r"(?:/[A-G](?:#|b)?)?)\]",
    re.IGNORECASE,
)


def strip_chords(text: str) -> str:
    """Remove inline chord notation while preserving lyric section labels."""

    return _CHORD.sub("", text)


def apply_text_case(text: str, text_case: str = "preserve") -> str:
    """Apply a display-only case transform without changing stored lyrics."""

    normalized = str(text_case).strip().casefold()
    if normalized == "upper":
        return text.upper()
    if normalized == "lower":
        return text.lower()
    return text


SEGMENT_TYPES = (
    "verse",
    "chorus",
    "pre_chorus",
    "bridge",
    "intro",
    "tag",
    "ending",
    "custom",
)

_SECTION_HEADER = re.compile(r"^\[([^\[\]]+)\]$")


@dataclass(frozen=True)
class LyricSlide:
    segment_index: int
    segment_label: str
    part_index: int
    part_count: int
    text: str

    @property
    def label(self) -> str:
        if self.part_count <= 1:
            return self.segment_label
        return f"{self.segment_label} · {self.part_index}/{self.part_count}"


@dataclass(frozen=True)
class TextBoxLayout:
    left: float
    top: float
    right: float
    bottom: float
    text_x: float
    text_y: float
    text_width: float
    anchor: str


def calculate_text_box_layout(
    canvas_width: float, canvas_height: float, settings: dict[str, Any]
) -> TextBoxLayout:
    """Convert percentage-based box settings to canvas coordinates."""

    left = canvas_width * float(settings.get("textBoxX", 5)) / 100
    top = canvas_height * float(settings.get("textBoxY", 25)) / 100
    box_width = canvas_width * float(settings.get("textBoxWidth", 90)) / 100
    box_height = canvas_height * float(settings.get("textBoxHeight", 50)) / 100
    right = left + box_width
    bottom = top + box_height

    horizontal = str(
        settings.get("textHorizontalAlign", settings.get("alignment", "center"))
    )
    vertical = str(settings.get("textVerticalAlign", "center"))
    text_x = {"left": left, "center": (left + right) / 2, "right": right}[horizontal]
    text_y = {"top": top, "center": (top + bottom) / 2, "bottom": bottom}[vertical]
    anchor = {
        ("left", "top"): "nw",
        ("center", "top"): "n",
        ("right", "top"): "ne",
        ("left", "center"): "w",
        ("center", "center"): "center",
        ("right", "center"): "e",
        ("left", "bottom"): "sw",
        ("center", "bottom"): "s",
        ("right", "bottom"): "se",
    }[(horizontal, vertical)]
    return TextBoxLayout(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        text_x=text_x,
        text_y=text_y,
        text_width=box_width,
        anchor=anchor,
    )


def _segment_type_from_label(label: str) -> str:
    lowered = label.strip().casefold().replace("-", " ")
    if lowered.startswith("verse"):
        return "verse"
    if lowered.startswith("pre chorus"):
        return "pre_chorus"
    if lowered.startswith("chorus") or lowered.startswith("refrain"):
        return "chorus"
    for segment_type in ("bridge", "intro", "tag", "ending", "outro"):
        if lowered.startswith(segment_type):
            return "ending" if segment_type == "outro" else segment_type
    return "custom"


def _header_label(line: str) -> str | None:
    stripped = line.strip()
    match = _SECTION_HEADER.fullmatch(stripped)
    if not match:
        return None
    # A line containing only a chord, such as [C] or [Am7], is not a section.
    if strip_chords(stripped).strip() == "":
        return None
    return match.group(1).strip() or None


def has_segment_headers(lyrics: str) -> bool:
    return any(_header_label(line) for line in lyrics.splitlines())


def default_segment_label(segment_type: str, existing: list[dict[str, Any]]) -> str:
    segment_type = segment_type if segment_type in SEGMENT_TYPES else "custom"
    names = {
        "chorus": "Chorus",
        "pre_chorus": "Pre-Chorus",
        "bridge": "Bridge",
        "intro": "Intro",
        "tag": "Tag",
        "ending": "Ending",
        "custom": "Custom",
    }
    if segment_type != "verse":
        base = names[segment_type]
        matches = sum(
            1 for segment in existing if segment.get("type") == segment_type
        )
        return base if matches == 0 else f"{base} {matches + 1}"
    verse_count = sum(1 for segment in existing if segment.get("type") == "verse")
    return f"Verse {verse_count + 1}"


def segments_from_lyrics(lyrics: str) -> list[dict[str, str]]:
    """Convert legacy plain lyrics into named song segments."""

    normalized = lyrics.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    if has_segment_headers(normalized):
        return _segments_from_headers(normalized)

    sections = re.split(r"\n\s*\n", normalized)
    segments: list[dict[str, str]] = []
    for section in sections:
        segment_type = "verse"
        label = default_segment_label(segment_type, segments)
        text = section.strip()
        segments.append(
            {
                "id": str(uuid4()),
                "type": segment_type,
                "label": label,
                "lyrics": text,
            }
        )
    return segments


def _segments_from_headers(lyrics: str) -> list[dict[str, str]]:
    segments: list[dict[str, str]] = []
    current_label: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_label, current_lines
        text = "\n".join(current_lines).strip()
        if current_label is not None or text:
            label = current_label or default_segment_label("verse", segments)
            segments.append(
                {
                    "id": str(uuid4()),
                    "type": _segment_type_from_label(label),
                    "label": label,
                    "lyrics": text,
                }
            )
        current_lines = []

    for line in lyrics.splitlines():
        label = _header_label(line)
        if label is not None:
            flush()
            current_label = label
        else:
            current_lines.append(line)
    flush()
    return segments


def detect_formatted_segments(lyrics: str) -> list[dict[str, str]] | None:
    """Return detected bracket-header segments, or None for ordinary lyrics."""

    if not has_segment_headers(lyrics):
        return None
    return segments_from_lyrics(lyrics)


def normalize_segments(
    segments: Any, legacy_lyrics: str = ""
) -> list[dict[str, str]]:
    if not isinstance(segments, list) or not segments:
        return segments_from_lyrics(legacy_lyrics)
    normalized: list[dict[str, str]] = []
    for raw in segments:
        if not isinstance(raw, dict):
            continue
        segment_type = str(raw.get("type", "custom")).strip().casefold()
        if segment_type not in SEGMENT_TYPES:
            segment_type = "custom"
        label = str(raw.get("label", "")).strip()
        if not label:
            label = default_segment_label(segment_type, normalized)
        normalized.append(
            {
                "id": str(raw.get("id") or uuid4()),
                "type": segment_type,
                "label": label,
                "lyrics": str(raw.get("lyrics", "")).strip(),
            }
        )
    return normalized or segments_from_lyrics(legacy_lyrics)


def segments_to_lyrics(segments: list[dict[str, Any]]) -> str:
    blocks = []
    for segment in normalize_segments(segments):
        label = segment["label"]
        text = segment["lyrics"].strip()
        blocks.append(f"[{label}]\n{text}".rstrip())
    return "\n\n".join(blocks)


def build_segment_slides(
    segments: list[dict[str, Any]], max_lines: int = 4
) -> list[LyricSlide]:
    """Split segments at blank-line groups, then enforce the line limit."""

    if max_lines < 1:
        raise ValueError("max_lines must be at least 1")
    slides: list[LyricSlide] = []
    for segment_index, segment in enumerate(normalize_segments(segments)):
        groups: list[list[str]] = []
        current_group: list[str] = []
        for source_line in str(segment.get("lyrics", "")).splitlines():
            if not source_line.strip():
                if current_group:
                    groups.append(current_group)
                    current_group = []
                continue
            display_line = strip_chords(source_line).strip()
            # A chord-only line remains part of the current group but does not
            # create an empty line on the lyrics display.
            if display_line:
                current_group.append(display_line)
        if current_group:
            groups.append(current_group)

        chunks = [
            group[start : start + max_lines]
            for group in groups
            for start in range(0, len(group), max_lines)
        ]
        for part_index, chunk in enumerate(chunks, start=1):
            slides.append(
                LyricSlide(
                    segment_index=segment_index,
                    segment_label=segment["label"],
                    part_index=part_index,
                    part_count=len(chunks),
                    text="\n".join(chunk),
                )
            )
    return slides


def matching_segment_slide_indices(
    slides: list[LyricSlide],
    segments: list[dict[str, Any]],
    segment_type: str,
    verse_number: int | None = None,
) -> list[int]:
    """Return slide positions targeted by a presentation segment shortcut."""

    matches: list[int] = []
    for index, slide in enumerate(slides):
        if not 0 <= slide.segment_index < len(segments):
            continue
        segment = segments[slide.segment_index]
        if str(segment.get("type", "")).casefold() != segment_type.casefold():
            continue
        if verse_number is not None:
            label_match = re.match(
                r"^verse\s*(\d+)", str(segment.get("label", "")), re.IGNORECASE
            )
            if not label_match or int(label_match.group(1)) != verse_number:
                continue
        matches.append(index)
    return matches


def next_matching_segment_slide(
    slides: list[LyricSlide],
    segments: list[dict[str, Any]],
    segment_type: str,
    current_index: int | None = None,
    verse_number: int | None = None,
) -> int | None:
    """Find the next shortcut target, wrapping after the last matching slide."""

    matches = matching_segment_slide_indices(
        slides, segments, segment_type, verse_number
    )
    if not matches:
        return None
    if current_index in matches:
        return matches[(matches.index(current_index) + 1) % len(matches)]
    return matches[0]


def build_lyric_slides(lyrics: str, max_lines: int = 4) -> list[str]:
    """Turn lyrics into readable slides using blank lines as section breaks."""

    if max_lines < 1:
        raise ValueError("max_lines must be at least 1")

    segments = segments_from_lyrics(lyrics)
    return [slide.text for slide in build_segment_slides(segments, max_lines)]
