"""Portable GitHub Releases updater for Stage Cue.

The application only checks and downloads in-process. Replacement is delegated
to a short-lived script after Stage Cue exits so the running executable/app is
never overwritten in place.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

from packaging.version import InvalidVersion, Version

from services.release_config import GITHUB_REPOSITORY, RELEASE_API_URL


APP_NAME = "Stage Cue"
USER_AGENT = "Stage-Cue-Updater/0.2.6"
REQUEST_TIMEOUT = 12


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    digest: str | None = None


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    notes: str
    html_url: str
    asset: ReleaseAsset


def _api_url() -> str:
    if RELEASE_API_URL.strip():
        return RELEASE_API_URL.strip()
    repository = GITHUB_REPOSITORY.strip()
    if not repository:
        raise UpdateError("This development build has no GitHub release repository configured.")
    return f"https://api.github.com/repos/{repository}/releases/latest"


def _request_json(url: str) -> dict:
    request = Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No published Stage Cue release was found.") from exc
        raise UpdateError(f"GitHub returned HTTP {exc.code} while checking for updates.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise UpdateError("Could not reach GitHub to check for updates.") from exc


def _target_asset_names() -> tuple[str, ...]:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows":
        return ("StageCue.exe", "Stage.Cue.exe", "Stage Cue.exe")
    if system == "Linux":
        return ("StageCue.AppImage", "Stage.Cue.AppImage", "Stage Cue.AppImage")
    if system == "Darwin":
        architecture = "arm64" if machine in {"arm64", "aarch64"} else "x64"
        return (
            f"StageCue-macOS-{architecture}.zip",
            f"Stage.Cue-macOS-{architecture}.zip",
            f"Stage Cue-macOS-{architecture}.zip",
            "StageCue-macOS.zip",
            "Stage.Cue-macOS.zip",
            "Stage Cue-macOS.zip",
        )
    raise UpdateError(f"Automatic updates are not supported on {system}.")


def _normalise_version(tag: str) -> str:
    value = tag.strip()
    return value[1:] if value.lower().startswith("v") else value


def check_for_update(current_version: str) -> ReleaseInfo | None:
    payload = _request_json(_api_url())
    if payload.get("draft") or payload.get("prerelease"):
        return None
    tag = str(payload.get("tag_name", "")).strip()
    try:
        if Version(_normalise_version(tag)) <= Version(_normalise_version(current_version)):
            return None
    except InvalidVersion as exc:
        raise UpdateError(f"GitHub returned an invalid release version: {tag or 'missing'}") from exc

    by_name = {str(item.get("name")): item for item in payload.get("assets", [])}
    selected = next((by_name[name] for name in _target_asset_names() if name in by_name), None)
    if selected is None:
        raise UpdateError(f"Release {tag} does not include a build for this computer.")
    digest = selected.get("digest")
    if digest and str(digest).lower().startswith("sha256:"):
        digest = str(digest).split(":", 1)[1]
    else:
        digest = None
    asset = ReleaseAsset(
        name=str(selected["name"]),
        url=str(selected["browser_download_url"]),
        size=int(selected.get("size") or 0),
        digest=digest,
    )
    return ReleaseInfo(
        version=_normalise_version(tag),
        tag=tag,
        notes=str(payload.get("body") or "No release notes were provided."),
        html_url=str(payload.get("html_url") or ""),
        asset=asset,
    )


def download_update(
    release: ReleaseInfo,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    update_dir = Path(tempfile.mkdtemp(prefix="stage-cue-update-"))
    destination = update_dir / release.asset.name
    request = Request(release.asset.url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    downloaded = 0
    try:
        with urlopen(request, timeout=30) as response, destination.open("wb") as output:
            total = int(response.headers.get("Content-Length") or release.asset.size or 0)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, total)
    except Exception:
        shutil.rmtree(update_dir, ignore_errors=True)
        raise
    if release.asset.digest and digest.hexdigest().lower() != release.asset.digest.lower():
        shutil.rmtree(update_dir, ignore_errors=True)
        raise UpdateError("The downloaded update failed its SHA-256 integrity check.")
    return destination


def _quote_sh(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _windows_install(download: Path) -> None:
    target = Path(sys.executable).resolve()
    if not getattr(sys, "frozen", False):
        raise UpdateError("Updates can only be installed by a packaged Stage Cue app.")
    if not os.access(target.parent, os.W_OK):
        raise UpdateError("Stage Cue is in a read-only folder. Move it to a folder you can edit, then try again.")
    script = download.parent / "update-stage-cue.cmd"
    content = (
        "@echo off\r\n"
        "setlocal\r\n"
        f":wait\r\ntasklist /FI \"PID eq {os.getpid()}\" | find \"{os.getpid()}\" >nul\r\n"
        "if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\r\n"
        f"move /Y \"{download}\" \"{target}\" >nul\r\n"
        "if errorlevel 1 (start \"\" cmd /c \"echo Stage Cue could not replace its app file. & pause\" & exit /b 1)\r\n"
        f"start \"\" \"{target}\"\r\n"
        "del \"%~f0\"\r\n"
    )
    script.write_text(content, encoding="utf-8", newline="")
    subprocess.Popen(["cmd", "/c", "start", "", "/min", str(script)], close_fds=True)


def _linux_install(download: Path) -> None:
    appimage = os.environ.get("APPIMAGE", "").strip()
    target = Path(appimage).resolve() if appimage else Path(sys.executable).resolve()
    if not appimage:
        raise UpdateError("Automatic installation requires the Stage Cue AppImage build.")
    if not os.access(target.parent, os.W_OK):
        raise UpdateError("The Stage Cue AppImage is in a read-only folder. Move it to a writable folder, then try again.")
    script = download.parent / "update-stage-cue.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 1; done\n"
        f"chmod +x {_quote_sh(str(download))}\n"
        f"mv -f {_quote_sh(str(download))} {_quote_sh(str(target))}\n"
        f"exec {_quote_sh(str(target))} >/dev/null 2>&1 &\n"
        "rm -f -- \"$0\"\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    subprocess.Popen(["/bin/sh", str(script)], start_new_session=True)


def _mac_app_root() -> Path:
    executable = Path(sys.executable).resolve()
    for parent in (executable, *executable.parents):
        if parent.name.endswith(".app"):
            return parent
    raise UpdateError("Could not locate the running Stage Cue.app bundle.")


def _mac_install(download: Path) -> None:
    if not zipfile.is_zipfile(download):
        raise UpdateError("The macOS update is not a valid ZIP archive.")
    extract_dir = download.parent / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["/usr/bin/ditto", "-x", "-k", str(download), str(extract_dir)],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise UpdateError("The macOS update could not be extracted.") from exc
    candidates = list(extract_dir.glob("**/Stage Cue.app"))
    if len(candidates) != 1:
        raise UpdateError("The macOS update does not contain exactly one Stage Cue.app.")
    source = candidates[0]
    target = _mac_app_root()
    if not os.access(target.parent, os.W_OK):
        raise UpdateError("Stage Cue.app is in a read-only folder. Move it to a writable folder, then try again.")
    backup = target.with_name("Stage Cue.app.old")
    script = download.parent / "update-stage-cue.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 1; done\n"
        f"rm -rf {_quote_sh(str(backup))}\n"
        f"mv {_quote_sh(str(target))} {_quote_sh(str(backup))}\n"
        f"if mv {_quote_sh(str(source))} {_quote_sh(str(target))}; then\n"
        f"  rm -rf {_quote_sh(str(backup))}\n"
        f"  open {_quote_sh(str(target))}\n"
        "else\n"
        f"  mv {_quote_sh(str(backup))} {_quote_sh(str(target))}\n"
        "fi\n"
        "rm -f -- \"$0\"\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    subprocess.Popen(["/bin/sh", str(script)], start_new_session=True)


def install_update(download: Path) -> None:
    system = platform.system()
    if system == "Windows":
        _windows_install(download)
    elif system == "Linux":
        _linux_install(download)
    elif system == "Darwin":
        _mac_install(download)
    else:
        raise UpdateError(f"Automatic updates are not supported on {system}.")
