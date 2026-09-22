from __future__ import annotations

import hashlib
import json
from queue import Empty, Full, Queue
import re
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any
from urllib.parse import quote

import keyring
from keyring.errors import KeyringError
import requests


class StagePublisherError(RuntimeError):
    """Raised when the Firebase publisher identity cannot be created or restored."""


class StagePublisher:
    """Publish the current Stage View packet to Firebase Realtime Database.

    The Smart TV is intentionally unauthenticated and only gets read access to the
    public Stage View fields.  This desktop installation uses a Firebase anonymous
    account whose UID must be explicitly allowed for the selected church by the
    Realtime Database rules.
    """

    HEARTBEAT_SECONDS = 10
    TOKEN_REFRESH_SAFETY_SECONDS = 60
    CHURCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    KEYRING_SERVICE = "Stage Cue Firebase Publisher"

    def __init__(
        self,
        database_url: str = "",
        hosting_url: str = "",
        api_key: str = "",
    ):
        self.database_url = database_url.strip().rstrip("/")
        self.hosting_url = hosting_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self._church_id = ""
        self._publisher_uid = ""
        self._refresh_token = ""
        self._id_token = ""
        self._id_token_expires_at = 0.0
        self._queue: Queue[dict[str, Any] | None] = Queue(maxsize=8)
        self._lock = Lock()
        self._auth_lock = Lock()
        self._last_digest = ""
        self._last_style_digest = ""
        self._registered_identity: tuple[str, str] | None = None
        self._closed = Event()

        self._thread = Thread(
            target=self._worker,
            name="stage-cue-firebase-publisher",
            daemon=True,
        )
        self._thread.start()
        self._heartbeat_thread = Thread(
            target=self._heartbeat_worker,
            name="stage-cue-firebase-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    @property
    def enabled(self) -> bool:
        with self._lock:
            return bool(
                self.database_url.startswith(("http://", "https://"))
                and self.api_key
                and self.CHURCH_ID_PATTERN.fullmatch(self._church_id or "")
            )

    @property
    def publisher_uid(self) -> str:
        with self._lock:
            return self._publisher_uid

    def configure(self, database_url: str, hosting_url: str, api_key: str) -> None:
        with self._lock:
            self.database_url = database_url.strip().rstrip("/")
            self.hosting_url = hosting_url.strip().rstrip("/")
            api_key = api_key.strip()
            api_key_changed = api_key != self.api_key
            self.api_key = api_key
            self._last_digest = ""
            self._last_style_digest = ""
            self._registered_identity = None
            church_id = self._church_id
            if api_key_changed:
                self._publisher_uid = ""
                self._refresh_token = ""
                self._id_token = ""
                self._id_token_expires_at = 0.0
        if church_id:
            self._load_identity(church_id)

    def set_church(self, church_id: str | None) -> None:
        church_id = str(church_id or "").strip()
        with self._lock:
            previous_church = self._church_id
            self._church_id = church_id
            self._publisher_uid = ""
            self._refresh_token = ""
            self._id_token = ""
            self._id_token_expires_at = 0.0
            self._last_digest = ""
            self._last_style_digest = ""
            self._registered_identity = None
        if church_id:
            self._load_identity(church_id)
            # When a church becomes active on this computer, clear any content
            # left behind by an earlier crash before the heartbeat makes the
            # Stage View look live again. Re-selecting the same church (for
            # example after online membership refresh) does not interrupt it.
            if church_id != previous_church and self.enabled:
                self.clear(church_id)

    def viewer_url(self, church_id: str) -> str:
        with self._lock:
            base = self.hosting_url
        if not base:
            return ""
        return f"{base}/c/{quote(church_id.strip(), safe='')}"

    def ensure_publisher_identity(self, church_id: str, api_key: str | None = None) -> str:
        """Return this installation's Firebase UID, creating it once if needed."""

        church_id = church_id.strip()
        if not self.CHURCH_ID_PATTERN.fullmatch(church_id):
            raise StagePublisherError("The current church ID is not valid for Firebase.")
        effective_api_key = (api_key if api_key is not None else self.api_key).strip()
        if not effective_api_key:
            raise StagePublisherError("Enter the Firebase Web API key first.")

        existing = self._read_identity(church_id, effective_api_key)
        if existing:
            uid = str(existing.get("uid") or "")
            refresh_token = str(existing.get("refreshToken") or "")
            if uid and refresh_token:
                with self._lock:
                    if self._church_id == church_id and self.api_key == effective_api_key:
                        self._publisher_uid = uid
                        self._refresh_token = refresh_token
                return uid

        try:
            response = requests.post(
                "https://identitytoolkit.googleapis.com/v1/accounts:signUp",
                params={"key": effective_api_key},
                json={"returnSecureToken": True},
                timeout=(5, 20),
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            message = self._firebase_error_message(exc)
            raise StagePublisherError(
                "Firebase could not create this computer's publisher identity. " + message
            ) from exc

        uid = str(data.get("localId") or "")
        refresh_token = str(data.get("refreshToken") or "")
        id_token = str(data.get("idToken") or "")
        if not uid or not refresh_token or not id_token:
            raise StagePublisherError("Firebase returned an incomplete publisher identity.")

        self._save_identity(church_id, effective_api_key, uid, refresh_token)
        expires_in = self._safe_expiry(data.get("expiresIn"))
        with self._lock:
            if self._church_id == church_id and self.api_key == effective_api_key:
                self._publisher_uid = uid
                self._refresh_token = refresh_token
                self._id_token = id_token
                self._id_token_expires_at = monotonic() + expires_in
        return uid

    def publish(self, church_id: str, payload: dict[str, Any]) -> bool:
        """Replace the church Stage View with one structured display payload.

        The payload is intentionally mode-specific. Lyrics use ``Current Slide`` and
        optional ``Upcoming Slides`` objects, custom messages use ``Message``, and
        imported presentations use ``Current presentation image``. ``Heartbeat`` is
        always added by the publisher.
        """

        church_id = church_id.strip()
        with self._lock:
            current_church = self._church_id
            database_url = self.database_url
        if (
            not database_url.startswith(("http://", "https://"))
            or church_id != current_church
            or not self.CHURCH_ID_PATTERN.fullmatch(church_id)
        ):
            return False

        packet: dict[str, Any] = {"Heartbeat": {".sv": "timestamp"}}
        current_slide = payload.get("Current Slide")
        upcoming_slides = payload.get("Upcoming Slides")
        message = payload.get("Message")
        presentation_image = payload.get("Current presentation image")

        if isinstance(current_slide, dict):
            lyrics = str(current_slide.get("Lyrics") or "")
            if lyrics:
                normalized_current = {"Lyrics": lyrics}
                song_title = str(current_slide.get("Song Title") or "")
                section_type = str(current_slide.get("Section Type") or "")
                if song_title:
                    normalized_current["Song Title"] = song_title
                if section_type:
                    normalized_current["Section Type"] = section_type
                packet["Current Slide"] = normalized_current

                if isinstance(upcoming_slides, dict):
                    normalized_upcoming: dict[str, dict[str, str]] = {}
                    for name, slide in upcoming_slides.items():
                        if not isinstance(slide, dict):
                            continue
                        upcoming_lyrics = str(slide.get("Lyrics") or "")
                        if not upcoming_lyrics:
                            continue
                        item = {"Lyrics": upcoming_lyrics}
                        upcoming_title = str(slide.get("Song Title") or "")
                        if upcoming_title:
                            item["Song Title"] = upcoming_title
                        normalized_upcoming[str(name)] = item
                    if normalized_upcoming:
                        packet["Upcoming Slides"] = normalized_upcoming
        elif message not in (None, ""):
            packet["Message"] = str(message)
        elif presentation_image not in (None, ""):
            packet["Current presentation image"] = str(presentation_image)

        encoded = json.dumps(
            {"churchId": church_id, "packet": packet},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        with self._lock:
            if digest == self._last_digest:
                return False
            self._last_digest = digest
        return self._enqueue(
            {"target": "stageViews", "churchId": church_id, "packet": packet, "_digest": digest}
        )

    def publish_styles(
        self,
        church_id: str,
        stage_style: dict[str, Any],
        message_style: dict[str, Any],
    ) -> bool:
        """Publish church Stage View styling separately from live presentation state."""

        church_id = church_id.strip()
        with self._lock:
            current_church = self._church_id
            database_url = self.database_url
        if (
            not database_url.startswith(("http://", "https://"))
            or church_id != current_church
            or not self.CHURCH_ID_PATTERN.fullmatch(church_id)
        ):
            return False

        allowed = {
            "fontFamily", "fontSize", "bold", "italic", "textColor", "textCase",
            "backgroundColor", "outlineEnabled", "outlineColor", "outlineWidth",
            "shadowEnabled", "shadowColor", "shadowOffsetX", "shadowOffsetY",
            "textBoxX", "textBoxY", "textBoxWidth", "textBoxHeight",
            "textHorizontalAlign", "textVerticalAlign", "nextSlideCount",
            "showCurrentSongTitle", "showSectionType", "showUpcomingSongTitle",
            "songTitleStyle", "sectionTypeStyle", "upcomingSongTitleStyle",
            "upcomingTextColor", "upcomingBoxX", "upcomingBoxY",
            "upcomingBoxWidth", "upcomingBoxHeight",
        }

        def clean(source: dict[str, Any]) -> dict[str, Any]:
            return {key: source[key] for key in allowed if key in source}

        packet = {"lyrics": clean(stage_style), "message": clean(message_style)}
        encoded = json.dumps(
            {"churchId": church_id, "packet": packet},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        with self._lock:
            if digest == self._last_style_digest:
                return False
            self._last_style_digest = digest
        return self._enqueue(
            {
                "target": "stageStyles",
                "churchId": church_id,
                "packet": packet,
                "_style_digest": digest,
            }
        )

    def clear(self, church_id: str) -> bool:
        # A blank mode is represented by a church node containing only Heartbeat.
        return self.publish(church_id, {})

    def close(self) -> None:
        self._closed.set()
        while True:
            try:
                self._queue.put_nowait(None)
                return
            except Full:
                try:
                    self._queue.get_nowait()
                except Empty:
                    continue

    def _enqueue(self, message: dict[str, Any]) -> bool:
        try:
            self._queue.put_nowait(message)
        except Full:
            try:
                self._queue.get_nowait()
            except Empty:
                pass
            self._queue.put_nowait(message)
        return True

    def _database_path(self, church_id: str, target: str = "stageViews") -> str:
        with self._lock:
            database_url = self.database_url
        return f"{database_url}/{target}/{quote(church_id, safe='')}.json"

    def _publisher_registration_path(self, church_id: str, uid: str) -> str:
        with self._lock:
            database_url = self.database_url
        return (
            f"{database_url}/publisherUids/{quote(church_id, safe='')}/"
            f"{quote(uid, safe='')}.json"
        )

    def _worker(self) -> None:
        session = requests.Session()
        try:
            while True:
                message = self._queue.get()
                if message is None:
                    return
                digest = str(message.get("_digest", ""))
                style_digest = str(message.get("_style_digest", ""))
                church_id = str(message["churchId"])
                target = str(message.get("target") or "stageViews")
                try:
                    response = self._authenticated_request(
                        session,
                        "PUT",
                        church_id,
                        target=target,
                        json=message["packet"],
                        timeout=(5, 30),
                    )
                    response.raise_for_status()
                except (requests.RequestException, StagePublisherError):
                    with self._lock:
                        if digest and self._last_digest == digest:
                            self._last_digest = ""
                        if style_digest and self._last_style_digest == style_digest:
                            self._last_style_digest = ""
        finally:
            session.close()

    def _heartbeat_worker(self) -> None:
        session = requests.Session()
        try:
            while not self._closed.wait(self.HEARTBEAT_SECONDS):
                with self._lock:
                    church_id = self._church_id
                    database_url = self.database_url
                if (
                    not database_url.startswith(("http://", "https://"))
                    or not self.CHURCH_ID_PATTERN.fullmatch(church_id or "")
                ):
                    continue
                try:
                    response = self._authenticated_request(
                        session,
                        "PATCH",
                        church_id,
                        target="stageViews",
                        json={"Heartbeat": {".sv": "timestamp"}},
                        timeout=(5, 20),
                    )
                    response.raise_for_status()
                except (requests.RequestException, StagePublisherError):
                    pass
        finally:
            session.close()

    def _authenticated_request(
        self,
        session: requests.Session,
        method: str,
        church_id: str,
        *,
        target: str = "stageViews",
        **kwargs: Any,
    ) -> requests.Response:
        token = self._id_token_for(church_id)
        self._ensure_publisher_registration(session, church_id, token)
        response = session.request(
            method,
            self._database_path(church_id, target),
            params={"auth": token, "print": "silent"},
            **kwargs,
        )
        if response.status_code == 401:
            token = self._id_token_for(church_id, force_refresh=True)
            self._ensure_publisher_registration(session, church_id, token, force=True)
            response = session.request(
                method,
                self._database_path(church_id, target),
                params={"auth": token, "print": "silent"},
                **kwargs,
            )
        return response

    def _ensure_publisher_registration(
        self,
        session: requests.Session,
        church_id: str,
        token: str,
        force: bool = False,
    ) -> None:
        with self._lock:
            uid = self._publisher_uid
            registration = self._registered_identity
        if not uid:
            raise StagePublisherError("Firebase did not provide a publisher user ID.")
        identity = (church_id, uid)
        if not force and registration == identity:
            return
        response = session.put(
            self._publisher_registration_path(church_id, uid),
            params={"auth": token, "print": "silent"},
            json=True,
            timeout=(5, 20),
        )
        response.raise_for_status()
        with self._lock:
            if self._church_id == church_id and self._publisher_uid == uid:
                self._registered_identity = identity

    def _id_token_for(self, church_id: str, force_refresh: bool = False) -> str:
        with self._auth_lock:
            with self._lock:
                if church_id != self._church_id:
                    raise StagePublisherError("The selected church changed before publishing.")
                if (
                    not force_refresh
                    and self._id_token
                    and monotonic() < self._id_token_expires_at
                ):
                    return self._id_token
                refresh_token = self._refresh_token
                api_key = self.api_key
            if not api_key:
                raise StagePublisherError("Firebase is not configured for Stage Cue.")
            if not refresh_token:
                # Church activation is enough. The Firebase account is created
                # automatically on first publish/heartbeat; users never need to
                # copy publisher UIDs into the console.
                self.ensure_publisher_identity(church_id, api_key)
                with self._lock:
                    if (
                        not force_refresh
                        and self._id_token
                        and monotonic() < self._id_token_expires_at
                    ):
                        return self._id_token
                    refresh_token = self._refresh_token
                if not refresh_token:
                    raise StagePublisherError(
                        "Firebase could not create the automatic Stage View publisher."
                    )

            try:
                response = requests.post(
                    "https://securetoken.googleapis.com/v1/token",
                    params={"key": api_key},
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                    timeout=(5, 20),
                )
                response.raise_for_status()
                data = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise StagePublisherError(
                    "Firebase could not refresh the Stage Cue publisher login. "
                    + self._firebase_error_message(exc)
                ) from exc

            id_token = str(data.get("id_token") or "")
            new_refresh = str(data.get("refresh_token") or refresh_token)
            uid = str(data.get("user_id") or "")
            if not id_token or not uid:
                raise StagePublisherError("Firebase returned an incomplete refreshed login.")
            self._save_identity(church_id, api_key, uid, new_refresh)
            expires_in = self._safe_expiry(data.get("expires_in"))
            with self._lock:
                if church_id != self._church_id or api_key != self.api_key:
                    raise StagePublisherError("Firebase configuration changed while publishing.")
                self._publisher_uid = uid
                self._refresh_token = new_refresh
                self._id_token = id_token
                self._id_token_expires_at = monotonic() + expires_in
            return id_token

    def _load_identity(self, church_id: str) -> None:
        with self._lock:
            api_key = self.api_key
        if not api_key:
            return
        try:
            identity = self._read_identity(church_id, api_key)
        except StagePublisherError:
            # A locked/unavailable credential store should not prevent Stage Cue
            # from starting; the setup dialog will surface the actionable error.
            return
        if not identity:
            return
        uid = str(identity.get("uid") or "")
        refresh_token = str(identity.get("refreshToken") or "")
        if not uid or not refresh_token:
            return
        with self._lock:
            if self._church_id == church_id and self.api_key == api_key:
                self._publisher_uid = uid
                self._refresh_token = refresh_token

    def _identity_account(self, church_id: str, api_key: str) -> str:
        key_fingerprint = hashlib.sha256(api_key.encode()).hexdigest()[:16]
        return f"{church_id}:{key_fingerprint}"

    def _read_identity(self, church_id: str, api_key: str) -> dict[str, str] | None:
        try:
            raw = keyring.get_password(
                self.KEYRING_SERVICE,
                self._identity_account(church_id, api_key),
            )
        except KeyringError as exc:
            raise StagePublisherError(
                "The OS credential store is unavailable for Firebase publisher credentials."
            ) from exc
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _save_identity(
        self,
        church_id: str,
        api_key: str,
        uid: str,
        refresh_token: str,
    ) -> None:
        try:
            keyring.set_password(
                self.KEYRING_SERVICE,
                self._identity_account(church_id, api_key),
                json.dumps({"uid": uid, "refreshToken": refresh_token}),
            )
        except KeyringError as exc:
            raise StagePublisherError(
                "Firebase signed in, but Stage Cue could not save the publisher login "
                "in the OS credential store."
            ) from exc

    @classmethod
    def _safe_expiry(cls, raw: Any) -> int:
        try:
            seconds = int(raw)
        except (TypeError, ValueError):
            seconds = 3600
        return max(60, seconds - cls.TOKEN_REFRESH_SAFETY_SECONDS)

    @staticmethod
    def _firebase_error_message(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                payload = response.json()
                message = payload.get("error", {}).get("message")
                if message:
                    if message == "OPERATION_NOT_ALLOWED":
                        return "Enable Anonymous sign-in in Firebase Authentication."
                    return str(message).replace("_", " ").title() + "."
            except (ValueError, AttributeError, TypeError):
                pass
        return "Check the Firebase Web API key and internet connection."
