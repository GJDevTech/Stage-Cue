from __future__ import annotations

from dataclasses import dataclass
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
