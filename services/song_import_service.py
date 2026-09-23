from __future__ import annotations

"""Import song files from OpenSong, VideoPsalm, and ProPresenter.

The importers normalize external formats into Stage Cue's native song model:
section-labelled lyrics plus author/copyright/CCLI metadata where available.
"""

from dataclasses import dataclass, field
import base64
import binascii
import json
from pathlib import Path
import re
from typing import Any, Iterable
import xml.etree.ElementTree as ET
import zipfile

from services.presentation_service import segments_from_lyrics


@dataclass
class ImportedSong:
    title: str
    lyrics: str
    author: str = ""
    copyright: str = ""
    ccli_number: str = ""
    segments: list[dict[str, Any]] = field(default_factory=list)
    source: str = ""


@dataclass
class ImportedSongbook:
    name: str
    songs: list[ImportedSong]
    source: str
    warnings: list[str] = field(default_factory=list)


class SongImportError(ValueError):
    pass


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------


def _local_name(tag: str) -> str:
    return str(tag).split("}")[-1].casefold()


def _clean_lines(text: Any) -> str:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in raw.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _ci_get(mapping: dict[str, Any], *names: str, default: Any = None) -> Any:
    if not isinstance(mapping, dict):
        return default
    lowered = {str(key).casefold(): value for key, value in mapping.items()}
    for name in names:
        key = name.casefold()
        if key in lowered:
            return lowered[key]
    return default


def _section_label(raw: Any, fallback: str = "Verse 1") -> str:
    value = str(raw or "").strip()
    if not value:
        return fallback
    compact = re.sub(r"[\s_\-]+", "", value).casefold()
    match = re.fullmatch(r"(?:v|verse)(\d+)", compact)
    if match:
        return f"Verse {int(match.group(1))}"
    if compact in {"v", "verse"}:
        return "Verse 1"
    match = re.fullmatch(r"(?:c|chorus|refrain)(\d+)", compact)
    if match:
        number = int(match.group(1))
        return "Chorus" if number == 1 else f"Chorus {number}"
    if compact in {"c", "chorus", "refrain"}:
        return "Chorus"
    match = re.fullmatch(r"(?:b|bridge)(\d+)", compact)
    if match:
        number = int(match.group(1))
        return "Bridge" if number == 1 else f"Bridge {number}"
    if compact in {"b", "bridge"}:
        return "Bridge"
    if compact in {"p", "pc", "prechorus", "prechorus1"}:
        return "Pre-Chorus"
    match = re.fullmatch(r"(?:p|pc|prechorus)(\d+)", compact)
    if match:
        number = int(match.group(1))
        return "Pre-Chorus" if number == 1 else f"Pre-Chorus {number}"
    if compact in {"t", "tag"}:
        return "Tag"
    if compact in {"i", "intro"}:
        return "Intro"
    if compact in {"e", "o", "end", "ending", "outro"}:
        return "Ending"
    # Friendly title-case while preserving useful numeric suffixes.
    return re.sub(r"\s+", " ", value).strip().title()


def _song_from_sections(
    title: str,
    sections: Iterable[tuple[str, str]],
    *,
    author: str = "",
    copyright_text: str = "",
    ccli_number: str = "",
    source: str = "",
    deduplicate_exact_sections: bool = False,
) -> ImportedSong:
    blocks: list[str] = []
    seen: set[tuple[str, str]] = set()
    for label, body in sections:
        clean_body = _clean_lines(body)
        if not clean_body.strip():
            continue
        clean_label = _section_label(label)
        key = (clean_label.casefold(), re.sub(r"\s+", " ", clean_body).strip().casefold())
        if deduplicate_exact_sections and key in seen:
            continue
        seen.add(key)
        blocks.append(f"[{clean_label}]\n{clean_body}")
    lyrics = "\n\n".join(blocks).strip()
    if not lyrics:
        raise SongImportError(f"No readable lyrics were found for {title or 'this song'}.")
    resolved_title = str(title or "Untitled Song").strip() or "Untitled Song"
    return ImportedSong(
        title=resolved_title,
        lyrics=lyrics,
        author=str(author or "").strip(),
        copyright=str(copyright_text or "").strip(),
        ccli_number=str(ccli_number or "").strip(),
        segments=segments_from_lyrics(lyrics),
        source=source,
    )


# ---------------------------------------------------------------------------
# OpenSong
# ---------------------------------------------------------------------------


def _xml_child_text(root: ET.Element, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for child in root:
        if _local_name(child.tag) in wanted:
            return "".join(child.itertext()).strip()
    return ""


def _parse_opensong_lyrics(raw: str) -> list[tuple[str, str]]:
    current_label = "Verse 1"
    current_lines: list[str] = []
    sections: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal current_lines
        body = "\n".join(current_lines).strip("\n")
        if body.strip():
            sections.append((current_label, body))
        current_lines = []

    for original in str(raw or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = original.strip()
        if stripped.startswith("[") and stripped.endswith("]") and len(stripped) > 2:
            flush()
            current_label = _section_label(stripped[1:-1])
            continue
        # OpenSong comments and chord rows are not lyrics.
        if original.lstrip().startswith(";") or original.lstrip().startswith("."):
            continue
        lyric_line = original[1:] if original.startswith((" ", "\t")) else original
        # A single | is a line break; || is an explicit slide break.  Stage Cue
        # represents slide breaks within a section as blank lines.
        lyric_line = lyric_line.replace("||", "\n\n").replace("|", "\n")
        current_lines.extend(lyric_line.split("\n"))
    flush()
    return sections


def _parse_opensong(path: Path, data: bytes | None = None) -> ImportedSong:
    try:
        root = ET.fromstring(data if data is not None else path.read_bytes())
    except (ET.ParseError, OSError) as exc:
        raise SongImportError(f"{path.name} is not a readable OpenSong XML song: {exc}") from exc
    if _local_name(root.tag) != "song":
        raise SongImportError(f"{path.name} is XML, but its root element is not <song>.")
    title = _xml_child_text(root, "title") or path.stem or path.name
    sections = _parse_opensong_lyrics(_xml_child_text(root, "lyrics"))
    return _song_from_sections(
        title,
        sections,
        author=_xml_child_text(root, "author"),
        copyright_text=_xml_child_text(root, "copyright"),
        ccli_number=_xml_child_text(root, "ccli"),
        source="OpenSong",
    )


def _looks_like_opensong(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:4096].lstrip()
    except OSError:
        return False
    return b"<song" in chunk.lower() and (chunk.startswith(b"<") or chunk.startswith(b"<?xml"))


# ---------------------------------------------------------------------------
# VideoPsalm
# ---------------------------------------------------------------------------


def _videopsalm_payload(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    # Some installations use .vpc for plain JSON, so JSON is tried first.
    try:
        text = raw.decode("utf-8-sig")
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

    if zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path) as archive:
                candidates = [name for name in archive.namelist() if name.casefold().endswith(".json")]
                if not candidates:
                    candidates = [name for name in archive.namelist() if not name.endswith("/")]
                for name in candidates:
                    try:
                        blob = archive.read(name)
                    except RuntimeError as exc:
                        raise SongImportError(
                            "This VideoPsalm .vpc is password-protected. In VideoPsalm, disable "
                            "the songbook's Compressed option and save/export it as .json, then import that file."
                        ) from exc
                    try:
                        payload = json.loads(blob.decode("utf-8-sig"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if isinstance(payload, dict):
                        return payload
        except (zipfile.BadZipFile, OSError) as exc:
            raise SongImportError(f"Could not open VideoPsalm file {path.name}: {exc}") from exc
    raise SongImportError(
        f"{path.name} is not readable VideoPsalm JSON. If it is a protected .vpc, export an uncompressed .json copy from VideoPsalm."
    )


def _videopsalm_section_label(verse: dict[str, Any], verse_position: int) -> str:
    explicit = _ci_get(verse, "Type", "Section", "Name", "Caption")
    if explicit:
        return _section_label(explicit)
    tag = _ci_get(verse, "Tag", default=0)
    try:
        tag_number = int(tag or 0)
    except (TypeError, ValueError):
        tag_number = 0
    # In commonly exported VideoPsalm songbooks Tag=1 denotes a chorus/refrain.
    if tag_number == 1:
        return "Chorus"
    verse_id = _ci_get(verse, "ID", "Verse", "Number", default=verse_position)
    try:
        verse_number = int(verse_id)
    except (TypeError, ValueError):
        verse_number = verse_position
    if verse_number <= 0:
        verse_number = verse_position
    return f"Verse {verse_number}"


def _parse_videopsalm(path: Path) -> ImportedSongbook:
    payload = _videopsalm_payload(path)
    songs_raw = _ci_get(payload, "Songs", default=[])
    if not isinstance(songs_raw, list):
        raise SongImportError(f"{path.name} does not contain a VideoPsalm Songs array.")
    description = str(_ci_get(payload, "Description", "Name", "Title", default="") or "").strip()
    book_name = description if description and len(description) <= 100 else path.stem
    book_author = str(_ci_get(payload, "Author", default="") or "").strip()
    book_copyright = str(_ci_get(payload, "Copyright", default="") or "").strip()
    imported: list[ImportedSong] = []
    warnings: list[str] = []
    for index, song in enumerate(songs_raw, start=1):
        if not isinstance(song, dict):
            continue
        title = str(_ci_get(song, "Text", "Title", "Name", default=f"Song {index}") or f"Song {index}").strip()
        verses = _ci_get(song, "Verses", "Slides", default=[])
        if not isinstance(verses, list):
            warnings.append(f"Skipped {title}: it had no Verses array.")
            continue
        sections: list[tuple[str, str]] = []
        for verse_position, verse in enumerate(verses, start=1):
            if not isinstance(verse, dict):
                continue
            body = _ci_get(verse, "Text", "Lyrics", "Content", default="")
            if not str(body or "").strip():
                continue
            sections.append((_videopsalm_section_label(verse, verse_position), str(body)))
        try:
            imported.append(
                _song_from_sections(
                    title,
                    sections,
                    author=str(_ci_get(song, "Author", "Authors", default=book_author) or book_author),
                    copyright_text=str(_ci_get(song, "Copyright", default=book_copyright) or book_copyright),
                    ccli_number=str(_ci_get(song, "CCLI", "CCLINumber", "SongNumber", default="") or ""),
                    source="VideoPsalm",
                    deduplicate_exact_sections=True,
                )
            )
        except SongImportError as exc:
            warnings.append(f"Skipped {title}: {exc}")
    if not imported:
        raise SongImportError(f"No readable songs were found in VideoPsalm songbook {path.name}.")
    return ImportedSongbook(name=book_name or path.stem, songs=imported, source="VideoPsalm", warnings=warnings)


# ---------------------------------------------------------------------------
# RTF used by ProPresenter
# ---------------------------------------------------------------------------


_DESTINATIONS = {
    "fonttbl", "colortbl", "datastore", "themedata", "stylesheet", "info",
    "header", "footer", "pict", "object", "filetbl", "listtable", "listoverridetable",
    "generator", "xmlnstbl", "rsidtbl",
}


def _rtf_to_text(data: bytes | str) -> str:
    if isinstance(data, bytes):
        raw = data.decode("latin-1", errors="replace")
    else:
        raw = str(data or "")
    if "{\\rtf" not in raw[:80].casefold():
        return _clean_lines(raw)

    out: list[str] = []
    stack: list[tuple[bool, int]] = []
    skip = False
    uc_skip = 1
    pending_skip = 0
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "{":
            stack.append((skip, uc_skip))
            i += 1
            continue
        if ch == "}":
            if stack:
                skip, uc_skip = stack.pop()
            i += 1
            continue
        if ch != "\\":
            if pending_skip:
                pending_skip -= 1
            elif not skip:
                out.append(ch)
            i += 1
            continue

        i += 1
        if i >= len(raw):
            break
        symbol = raw[i]
        if symbol in "\\{}":
            if not skip:
                out.append(symbol)
            i += 1
            continue
        if symbol == "*":
            skip = True
            i += 1
            continue
        if symbol == "'" and i + 2 < len(raw):
            try:
                decoded = bytes([int(raw[i + 1:i + 3], 16)]).decode("cp1252", errors="replace")
                if not skip:
                    out.append(decoded)
            except ValueError:
                pass
            i += 3
            continue
        match = re.match(r"([A-Za-z]+)(-?\d+)? ?", raw[i:])
        if not match:
            i += 1
            continue
        word = match.group(1).casefold()
        number = match.group(2)
        i += match.end()
        if word in _DESTINATIONS:
            skip = True
            continue
        if skip:
            continue
        if word in {"par", "line"}:
            out.append("\n")
        elif word == "tab":
            out.append("\t")
        elif word == "emdash":
            out.append("—")
        elif word == "endash":
            out.append("–")
        elif word == "bullet":
            out.append("•")
        elif word == "uc" and number is not None:
            uc_skip = max(0, int(number))
        elif word == "u" and number is not None:
            code = int(number)
            if code < 0:
                code += 65536
            try:
                out.append(chr(code))
            except ValueError:
                pass
            pending_skip = uc_skip
    text = "".join(out).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return _clean_lines(text)


def _decode_text_blob(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return _rtf_to_text(value)
    text = str(value).strip()
    if not text:
        return ""
    if text.startswith("{\\rtf"):
        return _rtf_to_text(text)
    compact = re.sub(r"\s+", "", text)
    if len(compact) >= 24 and len(compact) % 4 == 0:
        try:
            blob = base64.b64decode(compact, validate=True)
            if blob.startswith(b"{\\rtf"):
                return _rtf_to_text(blob)
            printable = blob.decode("utf-8")
            if printable.strip():
                return _clean_lines(printable)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            pass
    return _clean_lines(text)


# ---------------------------------------------------------------------------
# ProPresenter 6 XML
# ---------------------------------------------------------------------------


def _attr_ci(element: ET.Element, *names: str) -> str:
    lowered = {str(k).casefold(): str(v) for k, v in element.attrib.items()}
    for name in names:
        if name.casefold() in lowered:
            return lowered[name.casefold()].strip()
    return ""


def _text_candidates(element: ET.Element) -> list[str]:
    values: list[str] = []
    for node in element.iter():
        tag = _local_name(node.tag)
        if "text" in tag or "rtf" in tag or "string" in tag:
            if node.text and node.text.strip():
                decoded = _decode_text_blob(node.text)
                if decoded:
                    values.append(decoded)
        for key, value in node.attrib.items():
            lowered = str(key).casefold()
            if "rtf" in lowered or "plaintext" in lowered or lowered in {"text", "string"}:
                decoded = _decode_text_blob(value)
                if decoded:
                    values.append(decoded)
    # Longer candidate is usually the actual lyrics; Pro6 often carries the same
    # text in several representations.
    unique: list[str] = []
    seen: set[str] = set()
    for value in sorted(values, key=len, reverse=True):
        key = re.sub(r"\s+", " ", value).strip().casefold()
        if key and key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


def _parse_pro6(path: Path, data: bytes | None = None) -> ImportedSong:
    try:
        root = ET.fromstring(data if data is not None else path.read_bytes())
    except (ET.ParseError, OSError) as exc:
        raise SongImportError(f"Could not parse ProPresenter 6 file {path.name}: {exc}") from exc
    title = (
        _attr_ci(root, "CCLISongTitle", "songTitle", "name", "title")
        or path.stem
    )
    group_nodes = [node for node in root.iter() if "slidegroup" in _local_name(node.tag)]
    sections: list[tuple[str, str]] = []
    for group_index, group in enumerate(group_nodes, start=1):
        label = _section_label(
            _attr_ci(group, "name", "label", "title", "hotKey") or f"Verse {group_index}"
        )
        # Prefer individual slide descendants, keeping slide breaks as blank lines.
        slide_nodes = [node for node in group.iter() if "displayslide" in _local_name(node.tag)]
        chunks: list[str] = []
        if slide_nodes:
            for slide in slide_nodes:
                candidates = _text_candidates(slide)
                if candidates:
                    chunks.append(candidates[0])
        else:
            candidates = _text_candidates(group)
            if candidates:
                chunks.append(candidates[0])
        if chunks:
            sections.append((label, "\n\n".join(chunks)))
    if not sections:
        candidates = _text_candidates(root)
        if candidates:
            sections = [("Verse 1", candidates[0])]
    author = _attr_ci(root, "CCLIAuthor", "author")
    ccli = _attr_ci(root, "CCLISongNumber", "CCLINumber", "ccli")
    copyright_year = _attr_ci(root, "CCLICopyrightYear", "copyrightYear")
    publisher = _attr_ci(root, "CCLIPublisher", "publisher")
    copyright_text = " ".join(part for part in (copyright_year, publisher) if part).strip()
    return _song_from_sections(
        title,
        sections,
        author=author,
        copyright_text=copyright_text,
        ccli_number=ccli,
        source="ProPresenter 6",
    )


# ---------------------------------------------------------------------------
# ProPresenter 7 protobuf
# ---------------------------------------------------------------------------


def _proto_uuid(message: Any) -> str:
    if message is None:
        return ""
    value = getattr(message, "string", None)
    if value:
        return str(value)
    return str(message)


def _pro7_element_text(element_wrapper: Any) -> str:
    try:
        element = element_wrapper.element
        text_message = element.text
    except Exception:
        return ""
    raw = getattr(text_message, "rtf_data", b"") or getattr(text_message, "rtfData", b"")
    if raw:
        return _rtf_to_text(raw)
    # Future/alternate schemas may expose plain text alongside RTF.
    for attr in ("plain_text", "text", "string"):
        value = getattr(text_message, attr, None)
        if value:
            return _clean_lines(value)
    return ""


def _pro7_cue_text(cue: Any) -> str:
    chunks: list[str] = []
    fallback: list[str] = []
    for action in getattr(cue, "actions", []):
        try:
            slide_action = action.slide
            presentation_slide = slide_action.presentation
            base_slide = presentation_slide.base_slide
        except Exception:
            continue
        for wrapper in getattr(base_slide, "elements", []):
            text = _pro7_element_text(wrapper)
            if not text:
                continue
            name = str(getattr(wrapper, "name", "") or getattr(getattr(wrapper, "element", None), "name", ""))
            lowered_name = name.casefold()
            fallback.append(text)
            if any(word in lowered_name for word in ("ccli", "copyright", "footer", "author", "song title")):
                continue
            chunks.append(text)
    selected = chunks or fallback
    unique: list[str] = []
    seen: set[str] = set()
    for text in selected:
        key = re.sub(r"\s+", " ", text).strip().casefold()
        if key and key not in seen:
            seen.add(key)
            unique.append(text)
    return "\n".join(unique).strip()


def _parse_pro7_bytes(data: bytes, path: Path) -> ImportedSong:
    try:
        from pp7stubs import presentation_pb2
    except ImportError as exc:
        raise SongImportError(
            "ProPresenter 7 .pro import requires the pp7stubs package. Install/update Stage Cue's requirements and try again."
        ) from exc

    presentation = presentation_pb2.Presentation()
    try:
        presentation.ParseFromString(data)
    except Exception as exc:
        raise SongImportError(f"Could not decode ProPresenter 7 file {path.name}: {exc}") from exc

    cue_by_id: dict[str, Any] = {}
    for cue in getattr(presentation, "cues", []):
        cue_id = _proto_uuid(getattr(cue, "uuid", None))
        if cue_id:
            cue_by_id[cue_id] = cue

    group_by_id: dict[str, Any] = {}
    group_order: list[str] = []
    for cue_group in getattr(presentation, "cue_groups", []):
        group = getattr(cue_group, "group", None)
        group_id = _proto_uuid(getattr(group, "uuid", None))
        if group_id:
            group_by_id[group_id] = cue_group
            group_order.append(group_id)

    # Prefer the selected arrangement, otherwise the first arrangement.
    arrangement = None
    arrangements = list(getattr(presentation, "arrangements", []))
    selected_id = _proto_uuid(getattr(presentation, "selected_arrangement", None))
    if selected_id:
        for candidate in arrangements:
            if _proto_uuid(getattr(candidate, "uuid", None)) == selected_id:
                arrangement = candidate
                break
    if arrangement is None and arrangements:
        arrangement = arrangements[0]
    if arrangement is not None:
        arranged_ids = [
            _proto_uuid(identifier)
            for identifier in getattr(arrangement, "group_identifiers", [])
        ]
        arranged_ids = [identifier for identifier in arranged_ids if identifier in group_by_id]
        if arranged_ids:
            group_order = arranged_ids + [gid for gid in group_order if gid not in set(arranged_ids)]

    sections: list[tuple[str, str]] = []
    for group_index, group_id in enumerate(group_order, start=1):
        cue_group = group_by_id[group_id]
        group = getattr(cue_group, "group", None)
        label = _section_label(getattr(group, "name", "") or f"Verse {group_index}")
        slides: list[str] = []
        for identifier in getattr(cue_group, "cue_identifiers", []):
            cue = cue_by_id.get(_proto_uuid(identifier))
            if cue is None:
                continue
            text = _pro7_cue_text(cue)
            if text:
                slides.append(text)
        if slides:
            sections.append((label, "\n\n".join(slides)))

    # Fallback for presentations without cue groups.
    if not sections:
        slides = []
        for cue in getattr(presentation, "cues", []):
            text = _pro7_cue_text(cue)
            if text:
                slides.append(text)
        if slides:
            sections = [("Verse 1", "\n\n".join(slides))]

    ccli = getattr(presentation, "ccli", None)
    title = str(getattr(ccli, "song_title", "") or getattr(presentation, "name", "") or path.stem)
    author = str(getattr(ccli, "author", "") or "")
    ccli_number = str(getattr(ccli, "song_number", "") or getattr(ccli, "ccli_song_number", "") or "")
    copyright_year = str(getattr(ccli, "copyright_year", "") or "")
    publisher = str(getattr(ccli, "publisher", "") or "")
    copyright_text = " ".join(part for part in (copyright_year, publisher) if part).strip()
    return _song_from_sections(
        title,
        sections,
        author=author,
        copyright_text=copyright_text,
        ccli_number=ccli_number,
        source="ProPresenter 7",
    )


def _parse_pro7(path: Path) -> ImportedSong:
    return _parse_pro7_bytes(path.read_bytes(), path)


def _parse_probundle(path: Path) -> ImportedSong:
    try:
        with zipfile.ZipFile(path) as archive:
            pro_files = [name for name in archive.namelist() if name.casefold().endswith(".pro")]
            if not pro_files:
                raise SongImportError(f"{path.name} does not contain a .pro presentation file.")
            member = pro_files[0]
            data = archive.read(member)
            return _parse_pro7_bytes(data, Path(member))
    except zipfile.BadZipFile as exc:
        raise SongImportError(
            f"Could not read {path.name} as a ProPresenter bundle. Some ProPresenter bundles use non-standard ZIP64 headers; export the song as a .pro file and import that instead."
        ) from exc


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


def import_song_source(path: str | Path) -> ImportedSongbook:
    source_path = Path(path).expanduser()
    if not source_path.exists():
        raise SongImportError(f"File or folder not found: {source_path}")
    if source_path.is_dir():
        return import_song_folder(source_path)

    suffix = source_path.suffix.casefold()
    if suffix in {".json", ".vpc"}:
        return _parse_videopsalm(source_path)
    if suffix == ".pro6":
        song = _parse_pro6(source_path)
        return ImportedSongbook(source_path.stem, [song], song.source)
    if suffix == ".pro":
        song = _parse_pro7(source_path)
        return ImportedSongbook(source_path.stem, [song], song.source)
    if suffix == ".probundle":
        song = _parse_probundle(source_path)
        return ImportedSongbook(source_path.stem, [song], song.source)
    if suffix in {".xml", ".ost", ""} or _looks_like_opensong(source_path):
        song = _parse_opensong(source_path)
        return ImportedSongbook(source_path.parent.name or "OpenSong", [song], song.source)
    raise SongImportError(
        "Unsupported song format. Stage Cue can import OpenSong song files/folders, "
        "VideoPsalm .json/.vpc songbooks, and ProPresenter .pro6/.pro/.probundle presentations."
    )


def import_song_folder(path: str | Path) -> ImportedSongbook:
    folder = Path(path).expanduser()
    if not folder.is_dir():
        raise SongImportError(f"Not a folder: {folder}")
    songs: list[ImportedSong] = []
    warnings: list[str] = []
    sources: set[str] = set()
    # Avoid importing JSON data files recursively from unrelated Stage Cue projects;
    # only formats known to the requested importers are considered.
    for candidate in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
        if not candidate.is_file() or candidate.name.startswith("."):
            continue
        suffix = candidate.suffix.casefold()
        try:
            if suffix in {".pro", ".pro6", ".probundle"}:
                imported = import_song_source(candidate)
            elif suffix in {".json", ".vpc"}:
                # VideoPsalm file can itself be a full songbook.
                imported = _parse_videopsalm(candidate)
            elif suffix in {".xml", ".ost", ""} or _looks_like_opensong(candidate):
                imported_song = _parse_opensong(candidate)
                imported = ImportedSongbook(folder.name, [imported_song], imported_song.source)
            else:
                continue
        except SongImportError as exc:
            warnings.append(f"{candidate.name}: {exc}")
            continue
        songs.extend(imported.songs)
        warnings.extend(imported.warnings)
        sources.add(imported.source)
    if not songs:
        detail = f" First issue: {warnings[0]}" if warnings else ""
        raise SongImportError(f"No supported songs were found in {folder}.{detail}")
    source_name = ", ".join(sorted(sources)) if sources else "External"
    return ImportedSongbook(folder.name, songs, source_name, warnings)
