from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import quote

import requests


BIBLE_API_BASE = "https://bible-api.com"

# Public-domain / freely licensed translations exposed by bible-api.com.
TRANSLATIONS: tuple[tuple[str, str], ...] = (
    ("King James Version", "kjv"),
    ("World English Bible", "web"),
    ("American Standard Version (1901)", "asv"),
    ("Bible in Basic English", "bbe"),
    ("Darby Bible", "darby"),
    ("Douay-Rheims 1899 American Edition", "dra"),
    ("Young's Literal Translation (NT only)", "ylt"),
    ("Chinese Union Version", "cuv"),
)

# Canonical Protestant ordering. Chapter counts let the UI navigate locally;
# verse text is retrieved only for the chapter/reference the operator requests.
BOOKS: tuple[tuple[str, int], ...] = (
    ("Genesis", 50), ("Exodus", 40), ("Leviticus", 27), ("Numbers", 36),
    ("Deuteronomy", 34), ("Joshua", 24), ("Judges", 21), ("Ruth", 4),
    ("1 Samuel", 31), ("2 Samuel", 24), ("1 Kings", 22), ("2 Kings", 25),
    ("1 Chronicles", 29), ("2 Chronicles", 36), ("Ezra", 10), ("Nehemiah", 13),
    ("Esther", 10), ("Job", 42), ("Psalms", 150), ("Proverbs", 31),
    ("Ecclesiastes", 12), ("Song of Solomon", 8), ("Isaiah", 66), ("Jeremiah", 52),
    ("Lamentations", 5), ("Ezekiel", 48), ("Daniel", 12), ("Hosea", 14),
    ("Joel", 3), ("Amos", 9), ("Obadiah", 1), ("Jonah", 4), ("Micah", 7),
    ("Nahum", 3), ("Habakkuk", 3), ("Zephaniah", 3), ("Haggai", 2),
    ("Zechariah", 14), ("Malachi", 4), ("Matthew", 28), ("Mark", 16),
    ("Luke", 24), ("John", 21), ("Acts", 28), ("Romans", 16),
    ("1 Corinthians", 16), ("2 Corinthians", 13), ("Galatians", 6),
    ("Ephesians", 6), ("Philippians", 4), ("Colossians", 4),
    ("1 Thessalonians", 5), ("2 Thessalonians", 3), ("1 Timothy", 6),
    ("2 Timothy", 4), ("Titus", 3), ("Philemon", 1), ("Hebrews", 13),
    ("James", 5), ("1 Peter", 5), ("2 Peter", 3), ("1 John", 5),
    ("2 John", 1), ("3 John", 1), ("Jude", 1), ("Revelation", 22),
)


@dataclass(frozen=True)
class BiblePassage:
    reference: str
    translation_id: str
    translation_name: str
    verses: list[dict[str, Any]]


_BOOK_ALIASES = {name.casefold(): name for name, _chapters in BOOKS}
_BOOK_ALIASES.update({
    "song of songs": "Song of Solomon",
    "psalm": "Psalms",
    "revelations": "Revelation",
})


def canonical_book_name(value: str) -> str:
    """Return the canonical Stage Cue book name for a user-entered name."""

    cleaned = " ".join(str(value).replace(".", " ").split()).casefold()
    direct = _BOOK_ALIASES.get(cleaned)
    if direct:
        return direct
    matches = [name for name, _count in BOOKS if name.casefold().startswith(cleaned)]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"Unknown or ambiguous Bible book: {value}")


def parse_bible_reference(reference: str) -> tuple[str, int, int | None, int | None]:
    """Parse references such as ``John 3``, ``John 3:16`` or ``John 3:16-18``.

    Cross-chapter ranges are intentionally rejected for now so MongoDB only
    needs one indexed chapter lookup for each request.
    """

    reference = " ".join(str(reference).strip().split())
    match = re.match(
        r"^(?P<book>(?:[1-3]\s+)?[A-Za-z][A-Za-z\s]+?)\s+"
        r"(?P<chapter>\d+)"
        r"(?::(?P<start>\d+)(?:\s*[-–—]\s*(?P<end>\d+))?)?$",
        reference,
    )
    if not match:
        raise ValueError("Use a reference such as John 3, John 3:16, or John 3:16-18.")
    book = canonical_book_name(match.group("book"))
    chapter = int(match.group("chapter"))
    chapter_limit = dict(BOOKS).get(book, 0)
    if chapter < 1 or (chapter_limit and chapter > chapter_limit):
        raise ValueError(f"{book} has {chapter_limit} chapter(s).")
    start = int(match.group("start")) if match.group("start") else None
    end = int(match.group("end")) if match.group("end") else start
    if start is not None and start < 1:
        raise ValueError("Verse numbers must be positive.")
    if end is not None and start is not None and end < start:
        raise ValueError("The ending verse cannot be before the starting verse.")
    return book, chapter, start, end


def _reference_label(first: dict[str, Any], last: dict[str, Any]) -> str:
    book = str(first.get("book") or "Bible").strip()
    chapter = int(first.get("chapter", 0) or 0)
    first_verse = int(first.get("verse", 0) or 0)
    last_verse = int(last.get("verse", first_verse) or first_verse)
    if first_verse == last_verse:
        return f"{book} {chapter}:{first_verse}"
    return f"{book} {chapter}:{first_verse}–{last_verse}"


def paginate_bible_verses(
    verses: list[dict[str, Any]],
    max_lines: int,
    characters_per_line: int = 42,
) -> list[dict[str, str]]:
    """Wrap Bible verses and split them into pages by visible line count.

    ``characters_per_line`` is an estimate derived from the configured Bible
    View text-box width and font size. Wrapping is always word-safe. If a
    single verse needs more lines than one page allows, it continues on the
    next page and repeats its verse number there for context.
    """

    try:
        line_limit = int(max_lines)
    except (TypeError, ValueError):
        line_limit = 4
    line_limit = max(1, min(12, line_limit))

    try:
        wrap_width = int(characters_per_line)
    except (TypeError, ValueError):
        wrap_width = 42
    wrap_width = max(12, min(160, wrap_width))

    pages: list[dict[str, str]] = []
    current_lines: list[str] = []
    current_verses: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current_lines, current_verses
        if not current_lines or not current_verses:
            return
        pages.append(
            {
                "label": _reference_label(current_verses[0], current_verses[-1]),
                "text": "\n".join(current_lines).strip(),
            }
        )
        current_lines = []
        current_verses = []

    def wrap_words(words: list[str], first_prefix: str, continuation_prefix: str) -> list[str]:
        wrapped: list[str] = []
        index = 0
        first_line = True
        while index < len(words):
            prefix = first_prefix if first_line else continuation_prefix
            available = max(4, wrap_width - len(prefix))
            line_words: list[str] = []
            used = 0
            while index < len(words):
                word = words[index]
                extra = len(word) + (1 if line_words else 0)
                if line_words and used + extra > available:
                    break
                line_words.append(word)
                used += extra
                index += 1
                # A single unusually long token is allowed to overflow rather
                # than being split in the middle.
                if used >= available:
                    break
            wrapped.append(prefix + " ".join(line_words))
            first_line = False
        return wrapped

    for verse in verses:
        verse_number = int(verse.get("verse", 0) or 0)
        words = str(verse.get("text", "")).split()
        if not words:
            continue

        number_text = str(verse_number) if verse_number else ""
        first_prefix = f"{number_text}  " if number_text else ""
        continuation_prefix = " " * len(first_prefix)
        verse_lines = wrap_words(words, first_prefix, continuation_prefix)
        line_index = 0

        while line_index < len(verse_lines):
            remaining = line_limit - len(current_lines)
            if remaining <= 0:
                flush()
                remaining = line_limit

            take = min(remaining, len(verse_lines) - line_index)
            chunk = verse_lines[line_index : line_index + take]

            # When a verse continues on a new slide, repeat the verse number
            # on the first continuation line so the projected fragment still
            # makes sense by itself.
            if line_index > 0 and not current_lines and number_text:
                stripped = chunk[0].lstrip()
                chunk[0] = first_prefix + stripped

            current_lines.extend(chunk)
            current_verses.extend([verse] * len(chunk))
            line_index += take

            if len(current_lines) >= line_limit:
                flush()

    flush()
    return pages


class BibleService:
    """Small client for reference/chapter lookup used by the Bible library tab."""

    def __init__(self, timeout: float = 8.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "StageCue/0.2 Bible lookup"})

    def close(self) -> None:
        self.session.close()

    def fetch(self, reference: str, translation: str = "kjv") -> BiblePassage:
        reference = str(reference).strip()
        translation = str(translation).strip().lower() or "kjv"
        if not reference:
            raise ValueError("Enter a Bible reference first.")
        url = f"{BIBLE_API_BASE}/{quote(reference, safe='')}"
        try:
            response = self.session.get(
                url,
                params={
                    "translation": translation,
                    "single_chapter_book_matching": "indifferent",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise ValueError(f"Bible lookup failed: {exc}") from exc
        except ValueError as exc:
            raise ValueError("Bible lookup returned an unreadable response.") from exc

        verses = payload.get("verses")
        if not isinstance(verses, list) or not verses:
            error = str(payload.get("error") or "No verses were found for that reference.")
            raise ValueError(error)

        normalized: list[dict[str, Any]] = []
        for verse in verses:
            if not isinstance(verse, dict):
                continue
            text = " ".join(str(verse.get("text", "")).split())
            if not text:
                continue
            normalized.append(
                {
                    "book": str(verse.get("book_name", "")).strip(),
                    "chapter": int(verse.get("chapter", 0) or 0),
                    "verse": int(verse.get("verse", 0) or 0),
                    "text": text,
                }
            )
        if not normalized:
            raise ValueError("No readable verses were returned for that reference.")

        translation_id = str(payload.get("translation_id") or translation).lower()
        translation_name = str(payload.get("translation_name") or translation_id.upper())
        return BiblePassage(
            reference=str(payload.get("reference") or reference),
            translation_id=translation_id,
            translation_name=translation_name,
            verses=normalized,
        )
