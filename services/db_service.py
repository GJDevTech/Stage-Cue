from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from services.presentation_service import normalize_segments, segments_to_lyrics


DEFAULT_DISPLAY_SETTINGS: dict[str, Any] = {
    "fontFamily": "Arial",
    "fontSize": 54,
    "bold": True,
    "italic": False,
    "textColor": "#FFFFFF",
    "textCase": "preserve",
    "backgroundColor": "#000000",
    "backgroundType": "solid",
    "backgroundImageData": "",
    "backgroundImageFileName": "",
    "backgroundVideoPath": "",
    "backgroundVideoFileName": "",
    "alignment": "center",
    "outlineEnabled": False,
    "outlineColor": "#000000",
    "outlineWidth": 2,
    "shadowEnabled": True,
    "shadowColor": "#000000",
    "shadowOffsetX": 3,
    "shadowOffsetY": 3,
    "horizontalPosition": 50,
    "verticalPosition": 50,
    "textWidth": 90,
    "textBoxX": 5,
    "textBoxY": 25,
    "textBoxWidth": 90,
    "textBoxHeight": 50,
    "textHorizontalAlign": "center",
    "textVerticalAlign": "center",
    "logoData": "",
    "logoFileName": "",
    "logoX": 5,
    "logoY": 5,
    "logoWidth": 20,
    "maxLinesPerSlide": 4,
    "bibleMaxLinesPerSlide": 4,
    "bibleMaxCharactersPerSlide": 180,  # legacy setting kept for migration
    "upcomingTextColor": "#A7B0BC",
    "upcomingBoxX": 5,
    "upcomingBoxY": 72,
    "upcomingBoxWidth": 90,
    "upcomingBoxHeight": 26,
}

DEFAULT_SONG_TITLE_STYLE: dict[str, Any] = {
    "fontFamily": "Arial",
    "fontSize": 30,
    "bold": True,
    "italic": False,
    "textColor": "#FFFFFF",
    "textCase": "preserve",
}

DEFAULT_SECTION_TYPE_STYLE: dict[str, Any] = {
    "fontFamily": "Arial",
    "fontSize": 23,
    "bold": False,
    "italic": False,
    "textColor": "#D1D5DB",
    "textCase": "preserve",
}

DEFAULT_UPCOMING_SONG_TITLE_STYLE: dict[str, Any] = {
    "fontFamily": "Arial",
    "fontSize": 24,
    "bold": True,
    "italic": False,
    "textColor": "#F8FAFC",
    "textCase": "preserve",
}

DEFAULT_STAGE_DISPLAY_SETTINGS: dict[str, Any] = {
    **DEFAULT_DISPLAY_SETTINGS,
    # Standard Stage View layout is centered and shows song context above lyrics.
    "textHorizontalAlign": "center",
    "showCurrentSongTitle": True,
    "showSectionType": True,
    "showUpcomingSongTitle": True,
    "songTitleStyle": dict(DEFAULT_SONG_TITLE_STYLE),
    "sectionTypeStyle": dict(DEFAULT_SECTION_TYPE_STYLE),
    "upcomingSongTitleStyle": dict(DEFAULT_UPCOMING_SONG_TITLE_STYLE),
    "showNextSlide": False,
    "nextSlideCount": 0,
}

DEFAULT_MESSAGE_DISPLAY_SETTINGS: dict[str, Any] = {
    **DEFAULT_DISPLAY_SETTINGS,
    "fontSize": 64,
}

DEFAULT_BIBLE_DISPLAY_SETTINGS: dict[str, Any] = {
    **DEFAULT_DISPLAY_SETTINGS,
    # Bible passages get their own audience-facing style and pagination.
    "fontSize": 50,
    "maxLinesPerSlide": 4,
}


def default_display_settings_bundle() -> dict[str, dict[str, Any]]:
    """Return independent per-church settings for every presentation target."""

    return {
        "liveView": dict(DEFAULT_DISPLAY_SETTINGS),
        "stageView": dict(DEFAULT_STAGE_DISPLAY_SETTINGS),
        "messageView": dict(DEFAULT_MESSAGE_DISPLAY_SETTINGS),
        "bibleView": dict(DEFAULT_BIBLE_DISPLAY_SETTINGS),
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DatabaseService:
    """Local-first SQLite storage and durable per-church sync outbox."""

    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _create_schema(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cached_session (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    session_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS songbooks (
                    id TEXT PRIMARY KEY,
                    church_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    imported_from_global_id TEXT,
                    version INTEGER NOT NULL DEFAULT 0,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS songs (
                    id TEXT PRIMARY KEY,
                    church_id TEXT NOT NULL,
                    songbook_id TEXT,
                    title TEXT NOT NULL,
                    lyrics TEXT NOT NULL DEFAULT '',
                    author TEXT NOT NULL DEFAULT '',
                    copyright TEXT NOT NULL DEFAULT '',
                    ccli_number TEXT NOT NULL DEFAULT '',
                    segments_json TEXT NOT NULL DEFAULT '[]',
                    imported_from_global_id TEXT,
                    version INTEGER NOT NULL DEFAULT 0,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS display_settings (
                    church_id TEXT PRIMARY KEY,
                    settings_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS global_songbooks (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_church_id TEXT,
                    source_church_name TEXT NOT NULL DEFAULT '',
                    source_entity_id TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS global_songs (
                    id TEXT PRIMARY KEY,
                    global_songbook_id TEXT,
                    title TEXT NOT NULL,
                    lyrics TEXT NOT NULL DEFAULT '',
                    author TEXT NOT NULL DEFAULT '',
                    copyright TEXT NOT NULL DEFAULT '',
                    ccli_number TEXT NOT NULL DEFAULT '',
                    segments_json TEXT NOT NULL DEFAULT '[]',
                    source_church_id TEXT,
                    source_church_name TEXT NOT NULL DEFAULT '',
                    source_entity_id TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_outbox (
                    operation_id TEXT PRIMARY KEY,
                    church_id TEXT,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    base_version INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(entity_type, entity_id)
                );

                CREATE INDEX IF NOT EXISTS idx_songbooks_church
                    ON songbooks(church_id, deleted, name);
                CREATE INDEX IF NOT EXISTS idx_songs_church_book
                    ON songs(church_id, songbook_id, deleted, title);
                CREATE INDEX IF NOT EXISTS idx_outbox_status
                    ON sync_outbox(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_global_songbooks_name
                    ON global_songbooks(name);
                CREATE INDEX IF NOT EXISTS idx_global_songs_title
                    ON global_songs(title);
                """
            )
            self._ensure_column(
                connection, "songbooks", "imported_from_global_id", "TEXT"
            )
            self._ensure_column(
                connection, "songs", "imported_from_global_id", "TEXT"
            )
            self._ensure_column(connection, "songs", "author", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "songs", "copyright", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "songs", "ccli_number", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "songs", "segments_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(connection, "global_songs", "author", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "global_songs", "copyright", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "global_songs", "ccli_number", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "global_songs", "segments_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(connection, "global_songbooks", "source_church_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "global_songs", "source_church_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "sync_outbox", "church_id", "TEXT")
            connection.execute(
                """
                UPDATE sync_outbox
                SET church_id = COALESCE(
                    (SELECT church_id FROM songbooks
                     WHERE songbooks.id = sync_outbox.entity_id),
                    (SELECT church_id FROM songs
                     WHERE songs.id = sync_outbox.entity_id)
                )
                WHERE church_id IS NULL
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_outbox_church_status
                ON sync_outbox(church_id, status, created_at)
                """
            )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        existing = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in existing:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def save_cached_session(self, session: dict[str, Any]) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO cached_session(singleton, session_json, updated_at)
                VALUES(1, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    session_json = excluded.session_json,
                    updated_at = excluded.updated_at
                """,
                (json.dumps(session), utc_now()),
            )

    def get_cached_session(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT session_json FROM cached_session WHERE singleton = 1"
            ).fetchone()
        return json.loads(row["session_json"]) if row else None

    def clear_cached_session(self) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM cached_session WHERE singleton = 1")

    def set_setting(self, key: str, value: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO app_settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def list_songbooks(self, church_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, church_id, name, imported_from_global_id,
                       version, updated_at
                FROM songbooks
                WHERE church_id = ? AND deleted = 0
                ORDER BY name COLLATE NOCASE
                """,
                (church_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_songbook(
        self,
        church_id: str,
        name: str,
        songbook_id: str | None = None,
        imported_from_global_id: str | None = None,
    ) -> str:
        name = name.strip()
        if not name:
            raise ValueError("Songbook name is required.")
        entity_id = songbook_id or str(uuid4())
        updated_at = utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT version, imported_from_global_id
                FROM songbooks WHERE id = ? AND church_id = ?
                """,
                (entity_id, church_id),
            ).fetchone()
            version = int(existing["version"]) if existing else 0
            import_source = (
                existing["imported_from_global_id"]
                if existing and existing["imported_from_global_id"]
                else imported_from_global_id
            )
            connection.execute(
                """
                INSERT INTO songbooks(
                    id, church_id, name, imported_from_global_id,
                    version, deleted, updated_at
                ) VALUES(?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    imported_from_global_id = COALESCE(
                        songbooks.imported_from_global_id,
                        excluded.imported_from_global_id
                    ),
                    deleted = 0,
                    updated_at = excluded.updated_at
                """,
                (entity_id, church_id, name, import_source, version, updated_at),
            )
            self._queue_operation(
                connection,
                church_id,
                "songbook",
                entity_id,
                "upsert",
                {
                    "name": name,
                    "importedFromGlobalId": import_source,
                    "updatedAt": updated_at,
                },
                version,
            )
        return entity_id

    def list_songs(
        self, church_id: str, songbook_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = """
            SELECT id, church_id, songbook_id, title, lyrics, author,
                   copyright, ccli_number, segments_json,
                   imported_from_global_id, version, updated_at
            FROM songs
            WHERE church_id = ? AND deleted = 0
        """
        parameters: list[Any] = [church_id]
        if songbook_id:
            query += " AND songbook_id = ?"
            parameters.append(songbook_id)
        query += " ORDER BY title COLLATE NOCASE"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._song_from_row(row) for row in rows]

    def get_song(self, church_id: str, song_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, church_id, songbook_id, title, lyrics, author,
                       copyright, ccli_number, segments_json,
                       imported_from_global_id, version, updated_at
                FROM songs
                WHERE id = ? AND church_id = ? AND deleted = 0
                """,
                (song_id, church_id),
            ).fetchone()
        return self._song_from_row(row) if row else None

    def save_song(
        self,
        church_id: str,
        title: str,
        lyrics: str,
        songbook_id: str | None = None,
        song_id: str | None = None,
        imported_from_global_id: str | None = None,
        *,
        author: str = "",
        copyright: str = "",
        ccli_number: str = "",
        segments: list[dict[str, Any]] | None = None,
    ) -> str:
        title = title.strip()
        if not title:
            raise ValueError("Song title is required.")
        entity_id = song_id or str(uuid4())
        updated_at = utc_now()
        normalized_segments = normalize_segments(segments, lyrics)
        # Preserve the complete lyrics document exactly as entered. Older callers
        # that provide only structured segments still receive a readable document.
        stored_lyrics = lyrics if lyrics.strip() else segments_to_lyrics(normalized_segments)
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT version, imported_from_global_id
                FROM songs WHERE id = ? AND church_id = ?
                """,
                (entity_id, church_id),
            ).fetchone()
            version = int(existing["version"]) if existing else 0
            import_source = (
                existing["imported_from_global_id"]
                if existing and existing["imported_from_global_id"]
                else imported_from_global_id
            )
            connection.execute(
                """
                INSERT INTO songs(
                    id, church_id, songbook_id, title, lyrics, author,
                    copyright, ccli_number, segments_json,
                    imported_from_global_id, version, deleted, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                    songbook_id = excluded.songbook_id,
                    title = excluded.title,
                    lyrics = excluded.lyrics,
                    author = excluded.author,
                    copyright = excluded.copyright,
                    ccli_number = excluded.ccli_number,
                    segments_json = excluded.segments_json,
                    imported_from_global_id = COALESCE(
                        songs.imported_from_global_id,
                        excluded.imported_from_global_id
                    ),
                    deleted = 0,
                    updated_at = excluded.updated_at
                """,
                (
                    entity_id,
                    church_id,
                    songbook_id,
                    title,
                    stored_lyrics,
                    author.strip(),
                    copyright.strip(),
                    ccli_number.strip(),
                    json.dumps(normalized_segments),
                    import_source,
                    version,
                    updated_at,
                ),
            )
            self._queue_operation(
                connection,
                church_id,
                "song",
                entity_id,
                "upsert",
                {
                    "songbookId": songbook_id,
                    "title": title,
                    "lyrics": stored_lyrics,
                    "author": author.strip(),
                    "copyright": copyright.strip(),
                    "ccliNumber": ccli_number.strip(),
                    "segments": normalized_segments,
                    "importedFromGlobalId": import_source,
                    "updatedAt": updated_at,
                },
                version,
            )
        return entity_id

    @staticmethod
    def _song_from_row(row: sqlite3.Row) -> dict[str, Any]:
        song = dict(row)
        try:
            stored_segments = json.loads(song.pop("segments_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            stored_segments = []
        song["segments"] = normalize_segments(stored_segments, song.get("lyrics", ""))
        return song

    def delete_entity(self, church_id: str, entity_type: str, entity_id: str) -> None:
        table = self._table_for(entity_type)
        if entity_type == "display_settings":
            raise ValueError("Display settings cannot be deleted.")
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT version FROM {table} WHERE id = ? AND church_id = ?",
                (entity_id, church_id),
            ).fetchone()
            if not row:
                return
            updated_at = utc_now()
            connection.execute(
                f"UPDATE {table} SET deleted = 1, updated_at = ? "
                "WHERE id = ? AND church_id = ?",
                (updated_at, entity_id, church_id),
            )
            self._queue_operation(
                connection,
                church_id,
                entity_type,
                entity_id,
                "delete",
                {"updatedAt": updated_at},
                int(row["version"]),
            )

    def get_display_settings(
        self, church_id: str, view_type: str = "live"
    ) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT settings_json FROM display_settings WHERE church_id = ?",
                (church_id,),
            ).fetchone()
        stored: dict[str, Any] = json.loads(row["settings_json"]) if row else {}
        bundle = self._normalize_display_settings_bundle(stored)
        key = self._display_view_key(view_type)
        return dict(bundle[key])

    def get_display_settings_bundle(
        self, church_id: str
    ) -> dict[str, dict[str, Any]]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT settings_json FROM display_settings WHERE church_id = ?",
                (church_id,),
            ).fetchone()
        stored: dict[str, Any] = json.loads(row["settings_json"]) if row else {}
        return self._normalize_display_settings_bundle(stored)

    def save_display_settings(
        self,
        church_id: str,
        settings: dict[str, Any],
        view_type: str = "live",
    ) -> None:
        view_key = self._display_view_key(view_type)
        normalized = self._validate_display_settings(settings)
        if view_key == "stageView":
            fallback_count = 1 if self._as_boolean(
                settings.get("showNextSlide", False)
            ) else 0
            try:
                next_slide_count = int(
                    settings.get("nextSlideCount", fallback_count)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("Slides ahead must be a number.") from exc
            if not 0 <= next_slide_count <= 5:
                raise ValueError("Slides ahead must be between 0 and 5.")
            normalized["nextSlideCount"] = next_slide_count
            normalized["showNextSlide"] = next_slide_count > 0
            normalized["showCurrentSongTitle"] = self._as_boolean(
                settings.get("showCurrentSongTitle", True)
            )
            normalized["showSectionType"] = self._as_boolean(
                settings.get("showSectionType", True)
            )
            normalized["showUpcomingSongTitle"] = self._as_boolean(
                settings.get("showUpcomingSongTitle", True)
            )
            normalized["songTitleStyle"] = self._normalize_context_text_style(
                settings.get("songTitleStyle"), DEFAULT_SONG_TITLE_STYLE
            )
            normalized["sectionTypeStyle"] = self._normalize_context_text_style(
                settings.get("sectionTypeStyle"), DEFAULT_SECTION_TYPE_STYLE
            )
            normalized["upcomingSongTitleStyle"] = self._normalize_context_text_style(
                settings.get("upcomingSongTitleStyle"), DEFAULT_UPCOMING_SONG_TITLE_STYLE
            )
        updated_at = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT settings_json, version FROM display_settings WHERE church_id = ?",
                (church_id,),
            ).fetchone()
            version = int(row["version"]) if row else 0
            stored = json.loads(row["settings_json"]) if row else {}
            bundle = self._normalize_display_settings_bundle(stored)
            bundle[view_key] = normalized
            connection.execute(
                """
                INSERT INTO display_settings(
                    church_id, settings_json, version, updated_at
                ) VALUES(?, ?, ?, ?)
                ON CONFLICT(church_id) DO UPDATE SET
                    settings_json = excluded.settings_json,
                    updated_at = excluded.updated_at
                """,
                (church_id, json.dumps(bundle), version, updated_at),
            )
            self._queue_operation(
                connection,
                church_id,
                "display_settings",
                church_id,
                "upsert",
                {"settings": bundle, "updatedAt": updated_at},
                version,
            )

    @staticmethod
    def _display_view_key(view_type: str) -> str:
        normalized = str(view_type).strip().casefold().replace("_", "")
        if normalized in {"live", "liveview"}:
            return "liveView"
        if normalized in {"stage", "stageview"}:
            return "stageView"
        if normalized in {"message", "messageview", "custommessage"}:
            return "messageView"
        if normalized in {"bible", "bibleview", "scripture"}:
            return "bibleView"
        raise ValueError(
            "Display target must be Live View, Stage View, Bible View, or Custom Message."
        )

    @staticmethod
    def _as_boolean(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().casefold() in {"1", "true", "yes", "on"}
        return bool(value)

    @classmethod
    def _normalize_context_text_style(
        cls, source: Any, defaults: dict[str, Any]
    ) -> dict[str, Any]:
        raw = source if isinstance(source, dict) else {}
        result = dict(defaults)
        result.update(raw)
        result["fontFamily"] = str(result.get("fontFamily") or defaults["fontFamily"]).strip() or defaults["fontFamily"]
        try:
            result["fontSize"] = int(result.get("fontSize", defaults["fontSize"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("Context font size must be a number.") from exc
        if not 10 <= result["fontSize"] <= 160:
            raise ValueError("Context font size must be between 10 and 160.")
        result["bold"] = cls._as_boolean(result.get("bold", defaults.get("bold", False)))
        result["italic"] = cls._as_boolean(result.get("italic", defaults.get("italic", False)))
        color = str(result.get("textColor") or defaults["textColor"]).strip()
        if len(color) != 7 or not color.startswith("#"):
            raise ValueError("Context text color must use #RRGGBB format.")
        try:
            int(color[1:], 16)
        except ValueError as exc:
            raise ValueError("Context text color must use #RRGGBB format.") from exc
        result["textColor"] = color.upper()
        result["textCase"] = str(result.get("textCase", defaults.get("textCase", "preserve"))).strip().casefold()
        if result["textCase"] not in {"preserve", "upper", "lower"}:
            raise ValueError("Context text case must preserve, uppercase, or lowercase text.")
        return result

    @classmethod
    def _normalize_display_settings_bundle(
        cls, stored: Any
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(stored, dict):
            stored = {}
        is_bundle = any(
            key in stored
            for key in ("liveView", "stageView", "messageView", "bibleView")
        )
        if is_bundle:
            live_source = stored.get("liveView")
            stage_source = stored.get("stageView")
            message_source = stored.get("messageView")
            bible_source = stored.get("bibleView")
            live_source = live_source if isinstance(live_source, dict) else {}
            stage_source = (
                stage_source
                if isinstance(stage_source, dict)
                else live_source
            )
            # Existing installations used the Stage View style for messages.
            message_source = (
                message_source
                if isinstance(message_source, dict)
                else stage_source
            )
            if not isinstance(bible_source, dict):
                # Migration for churches created before Bible View had an
                # independent style: start from Live View, while preserving
                # the old Stage View Bible pagination value when available.
                bible_source = dict(live_source)
                try:
                    bible_source["maxLinesPerSlide"] = int(
                        stage_source.get("bibleMaxLinesPerSlide", 4)
                    )
                except (TypeError, ValueError):
                    bible_source["maxLinesPerSlide"] = 4
        else:
            # Older installations stored one flat style. Use it for both views.
            live_source = stored
            stage_source = stored
            message_source = stored if stored else DEFAULT_MESSAGE_DISPLAY_SETTINGS
            bible_source = dict(stored) if stored else dict(DEFAULT_BIBLE_DISPLAY_SETTINGS)
        live = cls._validate_display_settings(live_source)
        stage = cls._validate_display_settings(stage_source)
        message = cls._validate_display_settings(message_source)
        bible = cls._validate_display_settings(bible_source)
        fallback_count = 1 if cls._as_boolean(
            stage_source.get("showNextSlide", False)
        ) else 0
        try:
            next_slide_count = int(
                stage_source.get("nextSlideCount", fallback_count)
            )
        except (TypeError, ValueError):
            next_slide_count = fallback_count
        stage["nextSlideCount"] = max(0, min(5, next_slide_count))
        stage["showNextSlide"] = stage["nextSlideCount"] > 0
        stage["showCurrentSongTitle"] = cls._as_boolean(
            stage_source.get("showCurrentSongTitle", True)
        )
        stage["showSectionType"] = cls._as_boolean(
            stage_source.get("showSectionType", True)
        )
        stage["showUpcomingSongTitle"] = cls._as_boolean(
            stage_source.get("showUpcomingSongTitle", True)
        )
        stage["songTitleStyle"] = cls._normalize_context_text_style(
            stage_source.get("songTitleStyle"), DEFAULT_SONG_TITLE_STYLE
        )
        stage["sectionTypeStyle"] = cls._normalize_context_text_style(
            stage_source.get("sectionTypeStyle"), DEFAULT_SECTION_TYPE_STYLE
        )
        stage["upcomingSongTitleStyle"] = cls._normalize_context_text_style(
            stage_source.get("upcomingSongTitleStyle"), DEFAULT_UPCOMING_SONG_TITLE_STYLE
        )
        return {
            "liveView": live,
            "stageView": stage,
            "messageView": message,
            "bibleView": bible,
        }

    @staticmethod
    def _validate_display_settings(settings: dict[str, Any]) -> dict[str, Any]:
        legacy_geometry = "textBoxWidth" not in settings and any(
            key in settings
            for key in ("horizontalPosition", "verticalPosition", "textWidth")
        )
        result = dict(DEFAULT_DISPLAY_SETTINGS)
        result.update(settings)
        if "textHorizontalAlign" not in settings and "alignment" in settings:
            result["textHorizontalAlign"] = settings["alignment"]
        number_ranges = {
            "fontSize": (12, 160, "Font size"),
            "outlineWidth": (0, 10, "Outline width"),
            "shadowOffsetX": (-20, 20, "Horizontal shadow offset"),
            "shadowOffsetY": (-20, 20, "Vertical shadow offset"),
            "horizontalPosition": (0, 100, "Horizontal position"),
            "verticalPosition": (0, 100, "Vertical position"),
            "textWidth": (10, 100, "Text width"),
            "textBoxX": (0, 95, "Text-box left position"),
            "textBoxY": (0, 95, "Text-box top position"),
            "textBoxWidth": (5, 100, "Text-box width"),
            "textBoxHeight": (5, 100, "Text-box height"),
            "logoX": (0, 95, "Logo left position"),
            "logoY": (0, 95, "Logo top position"),
            "logoWidth": (5, 100, "Logo width"),
            "maxLinesPerSlide": (1, 12, "Lines per slide"),
            "bibleMaxLinesPerSlide": (1, 12, "Bible lines per slide"),
            "bibleMaxCharactersPerSlide": (40, 1000, "Bible characters per slide"),
            "upcomingBoxX": (0, 95, "Upcoming-box left position"),
            "upcomingBoxY": (0, 95, "Upcoming-box top position"),
            "upcomingBoxWidth": (5, 100, "Upcoming-box width"),
            "upcomingBoxHeight": (5, 100, "Upcoming-box height"),
        }
        for key, (minimum, maximum, label) in number_ranges.items():
            try:
                result[key] = int(result[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label} must be a number.") from exc
            if not minimum <= result[key] <= maximum:
                raise ValueError(
                    f"{label} must be between {minimum} and {maximum}."
                )
        if legacy_geometry:
            width = result["textWidth"]
            height = 50
            if result["alignment"] == "left":
                left = result["horizontalPosition"]
            elif result["alignment"] == "right":
                left = result["horizontalPosition"] - width
            else:
                left = result["horizontalPosition"] - width // 2
            result.update(
                {
                    "textBoxX": max(0, min(100 - width, left)),
                    "textBoxY": max(
                        0,
                        min(100 - height, result["verticalPosition"] - height // 2),
                    ),
                    "textBoxWidth": width,
                    "textBoxHeight": height,
                    "textVerticalAlign": "center",
                }
            )
        for key, label in (
            ("textColor", "Text color"),
            ("backgroundColor", "Background color"),
            ("outlineColor", "Outline color"),
            ("shadowColor", "Shadow color"),
            ("upcomingTextColor", "Upcoming text color"),
        ):
            value = str(result[key]).strip()
            if len(value) != 7 or not value.startswith("#"):
                raise ValueError(f"{label} must use #RRGGBB format.")
            try:
                int(value[1:], 16)
            except ValueError as exc:
                raise ValueError(f"{label} must use #RRGGBB format.") from exc
            result[key] = value.upper()
        result["fontFamily"] = str(result["fontFamily"]).strip() or "Arial"
        result["textCase"] = str(result.get("textCase", "preserve")).strip().casefold()
        if result["textCase"] not in {"preserve", "upper", "lower"}:
            raise ValueError("Lyrics case must preserve, uppercase, or lowercase text.")
        result["backgroundType"] = str(
            result.get("backgroundType", "solid")
        ).strip().casefold()
        if result["backgroundType"] not in {"solid", "image", "video"}:
            raise ValueError("Background type must be solid, image, or video.")
        result["backgroundImageFileName"] = str(
            result.get("backgroundImageFileName", "")
        )[:255]
        background_image_data = str(result.get("backgroundImageData", ""))
        if background_image_data and not background_image_data.startswith(
            ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
        ):
            raise ValueError("The background image must be PNG, JPEG, or WebP.")
        if len(background_image_data) > 8_000_000:
            raise ValueError("The background image is too large; choose a smaller image.")
        result["backgroundImageData"] = background_image_data
        result["backgroundVideoPath"] = str(
            result.get("backgroundVideoPath", "")
        ).replace("\x00", "")[:4096]
        result["backgroundVideoFileName"] = str(
            result.get("backgroundVideoFileName", "")
        )[:255]
        if result["alignment"] not in {"left", "center", "right"}:
            raise ValueError("Alignment must be left, center, or right.")
        if result["textHorizontalAlign"] not in {"left", "center", "right"}:
            raise ValueError("Text-box horizontal alignment must be left, center, or right.")
        if result["textVerticalAlign"] not in {"top", "center", "bottom"}:
            raise ValueError("Text-box vertical alignment must be top, center, or bottom.")
        if result["textBoxX"] + result["textBoxWidth"] > 100:
            raise ValueError("Text-box left position plus width cannot exceed 100%.")
        if result["textBoxY"] + result["textBoxHeight"] > 100:
            raise ValueError("Text-box top position plus height cannot exceed 100%.")
        if result["upcomingBoxX"] + result["upcomingBoxWidth"] > 100:
            raise ValueError("Upcoming-box left position plus width cannot exceed 100%.")
        if result["upcomingBoxY"] + result["upcomingBoxHeight"] > 100:
            raise ValueError("Upcoming-box top position plus height cannot exceed 100%.")
        if result["logoX"] + result["logoWidth"] > 100:
            raise ValueError("Logo left position plus width cannot exceed 100%.")
        result["logoFileName"] = str(result.get("logoFileName", ""))[:255]
        logo_data = str(result.get("logoData", ""))
        if logo_data and not logo_data.startswith("data:image/png;base64,"):
            raise ValueError("The church logo must be stored as a PNG image.")
        if len(logo_data) > 4_000_000:
            raise ValueError("The church logo is too large; choose a smaller image.")
        result["logoData"] = logo_data
        for key in ("bold", "italic", "outlineEnabled", "shadowEnabled"):
            value = result[key]
            if isinstance(value, str):
                value = value.strip().casefold() in {"1", "true", "yes", "on"}
            result[key] = bool(value)
        return result

    def list_global_songbooks(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, source_church_id, source_church_name,
                       source_entity_id, updated_at
                FROM global_songbooks ORDER BY name COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_global_songs(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, global_songbook_id, title, lyrics, author,
                       copyright, ccli_number, segments_json,
                       source_church_id, source_church_name,
                       source_entity_id, updated_at
                FROM global_songs ORDER BY title COLLATE NOCASE
                """
            ).fetchall()
        songs = []
        for row in rows:
            song = dict(row)
            try:
                stored_segments = json.loads(song.pop("segments_json") or "[]")
            except (json.JSONDecodeError, TypeError):
                stored_segments = []
            song["segments"] = normalize_segments(stored_segments, song["lyrics"])
            songs.append(song)
        return songs

    def import_global_songbook(self, church_id: str, global_id: str) -> str:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id, name FROM global_songbooks WHERE id = ?", (global_id,)
            ).fetchone()
            songs = connection.execute(
                """
                SELECT id, title, lyrics, author, copyright,
                       ccli_number, segments_json FROM global_songs
                WHERE global_songbook_id = ?
                ORDER BY title COLLATE NOCASE
                """,
                (global_id,),
            ).fetchall()
        if not row:
            raise ValueError("The selected global songbook is unavailable.")
        local_songbook_id = self.save_songbook(
            church_id,
            row["name"],
            imported_from_global_id=row["id"],
        )
        for song in songs:
            try:
                segments = json.loads(song["segments_json"] or "[]")
            except (json.JSONDecodeError, TypeError):
                segments = []
            self.save_song(
                church_id,
                song["title"],
                song["lyrics"],
                songbook_id=local_songbook_id,
                imported_from_global_id=song["id"],
                author=song["author"],
                copyright=song["copyright"],
                ccli_number=song["ccli_number"],
                segments=normalize_segments(segments, song["lyrics"]),
            )
        return local_songbook_id

    def import_global_song(
        self, church_id: str, global_id: str, songbook_id: str | None = None
    ) -> str:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, title, lyrics, author, copyright,
                       ccli_number, segments_json
                FROM global_songs WHERE id = ?
                """,
                (global_id,),
            ).fetchone()
        if not row:
            raise ValueError("The selected global song is unavailable.")
        try:
            segments = json.loads(row["segments_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            segments = []
        return self.save_song(
            church_id,
            row["title"],
            row["lyrics"],
            songbook_id=songbook_id,
            imported_from_global_id=row["id"],
            author=row["author"],
            copyright=row["copyright"],
            ccli_number=row["ccli_number"],
            segments=normalize_segments(segments, row["lyrics"]),
        )

    def _queue_operation(
        self,
        connection: sqlite3.Connection,
        church_id: str,
        entity_type: str,
        entity_id: str,
        action: str,
        payload: dict[str, Any],
        base_version: int,
    ) -> None:
        operation_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO sync_outbox(
                operation_id, church_id, entity_type, entity_id, action,
                payload_json, base_version, status, error, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?)
            ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                operation_id = excluded.operation_id,
                church_id = excluded.church_id,
                action = excluded.action,
                payload_json = excluded.payload_json,
                status = 'pending',
                error = NULL,
                created_at = excluded.created_at
            """,
            (
                operation_id,
                church_id,
                entity_type,
                entity_id,
                action,
                json.dumps(payload),
                base_version,
                utc_now(),
            ),
        )

    def get_pending_operations(
        self, church_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = """
            SELECT operation_id, church_id, entity_type, entity_id, action,
                   payload_json, base_version, status, error, created_at
            FROM sync_outbox
            WHERE status IN ('pending', 'failed')
        """
        parameters: tuple[Any, ...] = ()
        if church_id is not None:
            query += " AND church_id = ?"
            parameters = (church_id,)
        query += " ORDER BY created_at"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        operations = []
        for row in rows:
            operation = dict(row)
            operation["payload"] = json.loads(operation.pop("payload_json"))
            operations.append(operation)
        return operations

    def get_outbox_marker(self, church_id: str) -> str | None:
        """Return a cheap local marker that changes whenever pending work changes."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT operation_id, status FROM sync_outbox
                WHERE church_id = ? AND status IN ('pending', 'failed')
                ORDER BY operation_id
                """,
                (church_id,),
            ).fetchall()
        if not rows:
            return None
        return "|".join(f"{row['operation_id']}:{row['status']}" for row in rows)

    def complete_operation(
        self, operation: dict[str, Any], remote_record: dict[str, Any]
    ) -> None:
        table = self._table_for(operation["entity_type"])
        remote_version = int(remote_record["version"])
        id_column = "church_id" if table == "display_settings" else "id"
        with self._connection() as connection:
            current = connection.execute(
                """
                SELECT operation_id FROM sync_outbox
                WHERE entity_type = ? AND entity_id = ?
                """,
                (operation["entity_type"], operation["entity_id"]),
            ).fetchone()
            connection.execute(
                f"UPDATE {table} SET version = ? WHERE {id_column} = ?",
                (remote_version, operation["entity_id"]),
            )
            if current and current["operation_id"] == operation["operation_id"]:
                connection.execute(
                    "DELETE FROM sync_outbox WHERE operation_id = ?",
                    (operation["operation_id"],),
                )
            elif current:
                connection.execute(
                    """
                    UPDATE sync_outbox SET base_version = ?
                    WHERE entity_type = ? AND entity_id = ?
                    """,
                    (
                        remote_version,
                        operation["entity_type"],
                        operation["entity_id"],
                    ),
                )

    def mark_operation_error(self, operation_id: str, error: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE sync_outbox SET status = 'failed', error = ?
                WHERE operation_id = ?
                """,
                (error[:500], operation_id),
            )

    def mark_conflict(self, operation_id: str, error: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE sync_outbox SET status = 'conflict', error = ?
                WHERE operation_id = ?
                """,
                (error[:500], operation_id),
            )

    def apply_remote_snapshot(
        self, church_id: str, snapshot: dict[str, list[dict[str, Any]]]
    ) -> None:
        with self._connection() as connection:
            for record in snapshot.get("songbooks", []):
                self._apply_remote_record(connection, church_id, "songbook", record)
            for record in snapshot.get("songs", []):
                self._apply_remote_record(connection, church_id, "song", record)
            for record in snapshot.get("displaySettings", []):
                self._apply_remote_record(
                    connection, church_id, "display_settings", record
                )
            for record in snapshot.get("globalSongbooks", []):
                self._apply_global_songbook(connection, record)
            for record in snapshot.get("globalSongs", []):
                self._apply_global_song(connection, record)

    def _apply_remote_record(
        self,
        connection: sqlite3.Connection,
        church_id: str,
        entity_type: str,
        record: dict[str, Any],
    ) -> None:
        entity_id = str(record["id"])
        pending = connection.execute(
            """
            SELECT 1 FROM sync_outbox
            WHERE entity_type = ? AND entity_id = ?
            """,
            (entity_type, entity_id),
        ).fetchone()
        if pending:
            return

        updated_at = record.get("updatedAt") or utc_now()
        deleted = 1 if record.get("deleted") else 0
        version = int(record.get("version", 0))
        if entity_type == "songbook":
            connection.execute(
                """
                INSERT INTO songbooks(
                    id, church_id, name, imported_from_global_id,
                    version, deleted, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    church_id = excluded.church_id,
                    name = excluded.name,
                    imported_from_global_id = excluded.imported_from_global_id,
                    version = excluded.version,
                    deleted = excluded.deleted,
                    updated_at = excluded.updated_at
                """,
                (
                    entity_id,
                    church_id,
                    record.get("name", "Untitled"),
                    record.get("importedFromGlobalId"),
                    version,
                    deleted,
                    updated_at,
                ),
            )
        elif entity_type == "song":
            segments = normalize_segments(
                record.get("segments"), record.get("lyrics", "")
            )
            connection.execute(
                """
                INSERT INTO songs(
                    id, church_id, songbook_id, title, lyrics, author,
                    copyright, ccli_number, segments_json,
                    imported_from_global_id, version, deleted, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    church_id = excluded.church_id,
                    songbook_id = excluded.songbook_id,
                    title = excluded.title,
                    lyrics = excluded.lyrics,
                    author = excluded.author,
                    copyright = excluded.copyright,
                    ccli_number = excluded.ccli_number,
                    segments_json = excluded.segments_json,
                    imported_from_global_id = excluded.imported_from_global_id,
                    version = excluded.version,
                    deleted = excluded.deleted,
                    updated_at = excluded.updated_at
                """,
                (
                    entity_id,
                    church_id,
                    record.get("songbookId"),
                    record.get("title", "Untitled"),
                    record.get("lyrics", ""),
                    record.get("author", ""),
                    record.get("copyright", ""),
                    record.get("ccliNumber", ""),
                    json.dumps(segments),
                    record.get("importedFromGlobalId"),
                    version,
                    deleted,
                    updated_at,
                ),
            )
        else:
            settings = self._normalize_display_settings_bundle(
                record.get("settings", {})
            )
            connection.execute(
                """
                INSERT INTO display_settings(
                    church_id, settings_json, version, updated_at
                ) VALUES(?, ?, ?, ?)
                ON CONFLICT(church_id) DO UPDATE SET
                    settings_json = excluded.settings_json,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (church_id, json.dumps(settings), version, updated_at),
            )

    @staticmethod
    def _apply_global_songbook(
        connection: sqlite3.Connection, record: dict[str, Any]
    ) -> None:
        connection.execute(
            """
            INSERT INTO global_songbooks(
                id, name, source_church_id, source_church_name,
                source_entity_id, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                source_church_id = excluded.source_church_id,
                source_church_name = excluded.source_church_name,
                source_entity_id = excluded.source_entity_id,
                updated_at = excluded.updated_at
            """,
            (
                str(record["id"]),
                record.get("name", "Untitled"),
                record.get("sourceChurchId"),
                record.get("sourceChurchName", "Unknown church"),
                record.get("sourceEntityId"),
                record.get("updatedAt") or utc_now(),
            ),
        )

    @staticmethod
    def _apply_global_song(
        connection: sqlite3.Connection, record: dict[str, Any]
    ) -> None:
        segments = normalize_segments(
            record.get("segments"), record.get("lyrics", "")
        )
        connection.execute(
            """
            INSERT INTO global_songs(
                id, global_songbook_id, title, lyrics, author,
                copyright, ccli_number, segments_json,
                source_church_id, source_church_name,
                source_entity_id, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                global_songbook_id = excluded.global_songbook_id,
                title = excluded.title,
                lyrics = excluded.lyrics,
                author = excluded.author,
                copyright = excluded.copyright,
                ccli_number = excluded.ccli_number,
                segments_json = excluded.segments_json,
                source_church_id = excluded.source_church_id,
                source_church_name = excluded.source_church_name,
                source_entity_id = excluded.source_entity_id,
                updated_at = excluded.updated_at
            """,
            (
                str(record["id"]),
                record.get("globalSongbookId"),
                record.get("title", "Untitled"),
                record.get("lyrics", ""),
                record.get("author", ""),
                record.get("copyright", ""),
                record.get("ccliNumber", ""),
                json.dumps(segments),
                record.get("sourceChurchId"),
                record.get("sourceChurchName", "Unknown church"),
                record.get("sourceEntityId"),
                record.get("updatedAt") or utc_now(),
            ),
        )

    def get_sync_counts(self, church_id: str | None = None) -> dict[str, int]:
        query = "SELECT status, COUNT(*) AS count FROM sync_outbox"
        parameters: tuple[Any, ...] = ()
        if church_id is not None:
            query += " WHERE church_id = ?"
            parameters = (church_id,)
        query += " GROUP BY status"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        counts = {"pending": 0, "failed": 0, "conflict": 0}
        counts.update({row["status"]: row["count"] for row in rows})
        return counts

    @staticmethod
    def _table_for(entity_type: str) -> str:
        tables = {
            "songbook": "songbooks",
            "song": "songs",
            "display_settings": "display_settings",
        }
        try:
            return tables[entity_type]
        except KeyError as exc:
            raise ValueError(f"Unsupported entity type: {entity_type}") from exc
