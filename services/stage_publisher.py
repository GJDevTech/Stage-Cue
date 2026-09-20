from __future__ import annotations

import hashlib
import json
from queue import Empty, Full, Queue
import re
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import quote

import requests


class StagePublisher:
    """Send only the newest changed cue to the Render stage server."""
    
    KEEP_ALIVE_SECONDS = 10

    CHURCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

    def __init__(self, server_url: str = "", control_token: str = ""):
        self.server_url = server_url.strip().rstrip("/")
        self.control_token = control_token.strip()
        self._queue: Queue[dict[str, Any] | None] = Queue(maxsize=1)
        self._lock = Lock()
        self._last_digest = ""
        self._closed = Event()

        self._thread = Thread(
            target=self._worker,
            name="stage-cue-publisher",
            daemon=True,
        )
        self._thread.start()

        self._keep_alive_thread = Thread(
            target=self._keep_alive_worker,
            name="stage-cue-render-keep-alive",
            daemon=True,
        )
        self._keep_alive_thread.start()

    @property
    def enabled(self) -> bool:
        return self.server_url.startswith(("http://", "https://"))

    def configure(self, server_url: str, control_token: str) -> None:
        with self._lock:
            self.server_url = server_url.strip().rstrip("/")
            self.control_token = control_token.strip()
            self._last_digest = ""

    def publish(self, church_id: str, cue: dict[str, Any]) -> bool:
        church_id = church_id.strip()
        if not self.enabled or not self.CHURCH_ID_PATTERN.fullmatch(church_id):
            return False
        encoded = json.dumps(
            {"churchId": church_id, "cue": cue},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        with self._lock:
            if digest == self._last_digest:
                return False
            self._last_digest = digest
        message = {"churchId": church_id, "cue": cue, "_digest": digest}
        try:
            self._queue.put_nowait(message)
        except Full:
            try:
                self._queue.get_nowait()
            except Empty:
                pass
            self._queue.put_nowait(message)
        return True

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

    def _worker(self) -> None:
        session = requests.Session()
        while True:
            message = self._queue.get()
            if message is None:
                return
            with self._lock:
                server_url = self.server_url
                token = self.control_token
            digest = str(message.pop("_digest", ""))
            church_id = quote(str(message.pop("churchId")), safe="")
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            try:
                response = session.post(
                    f"{server_url}/api/churches/{church_id}/cue",
                    json={"cue": message["cue"]},
                    headers=headers,
                    timeout=(5, 65),
                )
                response.raise_for_status()
            except requests.RequestException:
                # The local secondary screen must continue even when Render is offline.
                # Allow an unchanged cue to be attempted again after connectivity returns.
                with self._lock:
                    if self._last_digest == digest:
                        self._last_digest = ""
                continue
                
    def _keep_alive_worker(self) -> None:
        """Keep Render awake while the Stage Cue application is running."""
        session = requests.Session()

        try:
            while not self._closed.is_set():
                with self._lock:
                    server_url = self.server_url

                if server_url.startswith(("http://", "https://")):
                    try:
                        response = session.get(
                            f"{server_url}/health",
                            timeout=(5, 30),
                        )
                        response.raise_for_status()
                        print(response)
                    except requests.RequestException:
                        # A temporary connection failure must not crash Stage Cue.
                        pass

                # Wait ten minutes, but wake immediately when the app closes.
                if self._closed.wait(self.KEEP_ALIVE_SECONDS):
                    break
        finally:
            session.close()
