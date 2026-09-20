from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import keyring
from keyring.errors import KeyringError
import requests


class AuthenticationError(RuntimeError):
    pass


class AuthService:
    KEYRING_SERVICE = "Stage Cue"
    KEYRING_ACCOUNT = "google-oauth-credentials"

    def __init__(self, client_config_path: Path | str):
        self.client_config_path = Path(client_config_path)
        self.scopes = [
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        ]

    def login(self) -> dict[str, Any]:
        if not self.client_config_path.is_file():
            raise AuthenticationError(
                "Google OAuth is not configured. Add a Desktop OAuth client JSON "
                "and set STAGE_CUE_GOOGLE_CLIENT_CONFIG."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(self.client_config_path), self.scopes
        )
        credentials = flow.run_local_server(
            port=0,
            access_type="offline",
            prompt="consent",
            # Google expects the literal lowercase query value "true". Passing
            # Python's True is serialized as "True" and rejected with HTTP 400.
            include_granted_scopes="true",
        )
        self._save_credentials(credentials)
        return self._fetch_profile(credentials)

    def resume_online(self) -> dict[str, Any]:
        credentials = self._load_credentials()
        if not credentials:
            raise AuthenticationError("No saved Google login is available.")
        if not credentials.valid:
            if not credentials.refresh_token:
                raise AuthenticationError("The saved Google login has expired.")
            try:
                credentials.refresh(Request())
            except Exception as exc:
                raise AuthenticationError(
                    "Google login could not be refreshed while online."
                ) from exc
            self._save_credentials(credentials)
        return self._fetch_profile(credentials)

    def logout(self) -> None:
        try:
            keyring.delete_password(self.KEYRING_SERVICE, self.KEYRING_ACCOUNT)
        except KeyringError:
            pass

    def _save_credentials(self, credentials: Credentials) -> None:
        try:
            keyring.set_password(
                self.KEYRING_SERVICE,
                self.KEYRING_ACCOUNT,
                credentials.to_json(),
            )
        except KeyringError as exc:
            raise AuthenticationError(
                "Google login succeeded, but it could not be saved in the OS keyring."
            ) from exc

    def _load_credentials(self) -> Credentials | None:
        try:
            credentials_json = keyring.get_password(
                self.KEYRING_SERVICE, self.KEYRING_ACCOUNT
            )
        except KeyringError as exc:
            raise AuthenticationError("The OS keyring is unavailable.") from exc
        if not credentials_json:
            return None
        return Credentials.from_authorized_user_info(
            json.loads(credentials_json), self.scopes
        )

    @staticmethod
    def _fetch_profile(credentials: Credentials) -> dict[str, Any]:
        try:
            response = requests.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {credentials.token}"},
                timeout=10,
            )
            response.raise_for_status()
            profile = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise AuthenticationError("Google account details are unavailable.") from exc
        if not profile.get("sub") or not profile.get("email"):
            raise AuthenticationError("Google returned an incomplete account profile.")
        return profile
