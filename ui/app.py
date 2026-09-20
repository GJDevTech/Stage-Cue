from __future__ import annotations

from queue import Empty, Queue
from threading import Thread
from time import monotonic
import tkinter as tk
from tkinter import messagebox
from typing import Any, Callable

from services.config import bundled_resource_path
from services.errors import AdminRequired, MembershipRequired
from services.local_settings import LocalSettings
from ui.appearance_dialog import AppearanceDialog
from ui.dialogs import (
    AdminDialog,
    DisplaySettingsDialog,
    GlobalLibraryDialog,
    OutputSettingsDialog,
)
from ui.pages.account_creation_page import AccountCreationPage
from ui.pages.auth_page import AuthPage
from ui.pages.church_selection_page import ChurchSelectionPage
from ui.pages.control_center_page import ControlCenterPage
from ui import theme
from version import __version__

APP_VERSION = __version__

PAGE_CLASSES = (
    AuthPage,
    AccountCreationPage,
    ChurchSelectionPage,
    ControlCenterPage,
)


def responsive_window_geometry(screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
    """Return a centred window size that stays inside the usable screen.

    The preferred size is expressed in logical pixels and scaled, so a 150%
    machine asks for a proportionally larger window instead of cramming the
    same layout into the same box.
    """

    available_width = max(760, screen_width - theme.px(40))
    available_height = max(520, screen_height - theme.px(80))
    width = min(theme.px(1380), available_width)
    height = min(theme.px(880), available_height)
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2 - theme.px(8))
    return width, height, x, y


class App(tk.Tk):
    def __init__(self, auth_service, db_service, cloud_service, sync_service, stage_publisher):
        super().__init__()

        try:
            self._stagecue_app_icon = tk.PhotoImage(
                file=str(bundled_resource_path("assets/stagecue.png"))
            )
            self.iconphoto(True, self._stagecue_app_icon)
        except tk.TclError:
            pass

        self.auth_service = auth_service
        self.db_service = db_service
        self.cloud_service = cloud_service
        self.sync_service = sync_service
        self.stage_publisher = stage_publisher
        self.presentation_window = None
        self.current_session = self.db_service.get_cached_session()
        self.current_account = (
            dict(self.current_session["user"]) if self.current_session else None
        )
        self.pending_profile = None
        self.current_page_name = None
        self._background_results: Queue[tuple] = Queue()
        self._sync_in_progress = False
        self._revision_poll_in_progress = False
        self._last_synced_cloud_revision: int | None = None
        self._last_attempted_local_marker: str | None = None
        self._next_sync_retry_at = 0.0
        self.app_version = APP_VERSION

        self.local_settings = LocalSettings()
        self._apply_local_appearance()

        self.title(f"Stage Cue {APP_VERSION}")
        self._restore_window_geometry()
        self.configure(bg=theme.PALETTE["canvas"])

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.container = tk.Frame(self, bg=theme.PALETTE["canvas"])
        self.container.grid(row=0, column=0, sticky="nsew")
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.pages = {}
        self._build_pages()

        self.after(100, self._process_background_results)
        self.after(1000, self._auto_sync_tick)
        self.protocol("WM_DELETE_WINDOW", self._shutdown)
        if self.current_session:
            self.show_page("ControlCenterPage")
            self.after(500, self.resume_session_online)
        else:
            self.show_page("AuthPage")

    # ------------------------------------------------------------------
    # Appearance: per-machine theme and scaling
    # ------------------------------------------------------------------

    def _apply_local_appearance(self) -> None:
        """Resolve the stored theme and scale, then restyle Tk's shared state."""

        theme.resolve_font_families(self)
        theme.set_theme(self.local_settings.theme)
        stored_scale = self.local_settings.ui_scale
        if stored_scale == "auto":
            theme.set_scale(theme.recommended_scale(self))
        else:
            theme.set_scale(float(stored_scale))
        theme.apply_to_root(self)

    def _build_pages(self) -> None:
        for Page in PAGE_CLASSES:
            page = Page(self.container, self, self.auth_service, self.db_service)
            self.pages[Page.__name__] = page
            page.grid(row=0, column=0, sticky="nsew")

    def apply_appearance(
        self,
        theme_name: str | None = None,
        ui_scale=None,
        remember_geometry: bool | None = None,
    ) -> None:
        """Persist the new appearance for this machine and rebuild the pages.

        Tk widgets keep the colours and paddings they were created with, so the
        page tree is discarded and rebuilt rather than recoloured in place. The
        control centre hands over a state snapshot so nothing the operator has
        typed is lost across the swap.
        """

        if theme_name is not None:
            self.local_settings.theme = theme_name
        if ui_scale is not None:
            self.local_settings.ui_scale = ui_scale
        if remember_geometry is not None:
            self.local_settings.remember_window_geometry = remember_geometry

        snapshot = None
        control_center = self.pages.get("ControlCenterPage")
        if control_center is not None and hasattr(control_center, "capture_state"):
            snapshot = control_center.capture_state()
        current = self.current_page_name or "AuthPage"

        for page in self.pages.values():
            page.destroy()
        self.pages.clear()
        if hasattr(self, "_stagecue_icons"):
            # Icons are cached per colour and size, so a theme or scale change
            # invalidates every entry.
            self._stagecue_icons.clear()

        self._apply_local_appearance()
        self.configure(bg=theme.PALETTE["canvas"])
        self.container.configure(bg=theme.PALETTE["canvas"])
        self._resize_for_scale()
        self._build_pages()

        self.show_page(current)
        if snapshot is not None:
            restored = self.pages.get("ControlCenterPage")
            if restored is not None and hasattr(restored, "restore_state"):
                restored.restore_state(snapshot)

    def open_appearance_settings(self) -> None:
        AppearanceDialog(self)

    # ------------------------------------------------------------------
    # Window geometry
    # ------------------------------------------------------------------

    def _restore_window_geometry(self) -> None:
        width, height, x, y = responsive_window_geometry(
            self.winfo_screenwidth(), self.winfo_screenheight()
        )
        saved = self.local_settings.window_geometry
        if self.local_settings.remember_window_geometry and saved:
            try:
                self.geometry(saved)
            except tk.TclError:
                self.geometry(f"{width}x{height}+{x}+{y}")
        else:
            self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(theme.px(920), width), min(theme.px(600), height))

    def _resize_for_scale(self) -> None:
        """Grow or shrink the window so the new scale still fits the screen."""

        width, height, x, y = responsive_window_geometry(
            self.winfo_screenwidth(), self.winfo_screenheight()
        )
        self.minsize(1, 1)
        try:
            self.state("normal")
        except tk.TclError:
            pass
        current_width = self.winfo_width()
        current_height = self.winfo_height()
        if current_width < width or current_height < height:
            self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(theme.px(920), width), min(theme.px(600), height))

    def _remember_window_geometry(self) -> None:
        if not self.local_settings.remember_window_geometry:
            return
        try:
            if self.state() != "normal":
                return
            self.local_settings.window_geometry = self.winfo_geometry()
        except tk.TclError:
            pass

    def show_page(self, name):
        self.current_page_name = name
        page = self.pages[name]
        page.tkraise()
        if hasattr(page, "on_show"):
            page.on_show()

    def run_background(
        self,
        work: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        def runner() -> None:
            try:
                self._background_results.put((on_success, work()))
            except Exception as exc:
                self._background_results.put((on_error, exc))

        Thread(target=runner, daemon=True).start()

    def _process_background_results(self) -> None:
        try:
            while True:
                callback, value = self._background_results.get_nowait()
                callback(value)
        except Empty:
            pass
        self.after(100, self._process_background_results)

    # ------------------------------------------------------------------
    # Login, account creation, and church selection
    # ------------------------------------------------------------------

    def login_online(self) -> None:
        page = self.pages["AuthPage"]

        def work():
            profile = self.auth_service.login()
            return profile, self.cloud_service.find_account(profile)

        def success(value):
            profile, account = value
            page.set_busy(False)
            self.pending_profile = profile
            if account is None:
                self.show_page("AccountCreationPage")
                return
            self.pending_profile = None
            self.current_account = account
            self.show_page("ChurchSelectionPage")

        def error(exc):
            page.set_busy(False)
            page.set_status(str(exc))

        self.run_background(work, success, error)

    def create_account(self, display_name: str) -> None:
        page = self.pages["AccountCreationPage"]
        profile = self.pending_profile
        if not profile:
            page.show_error("Google login expired. Return to login and try again.")
            return

        def success(account):
            self.current_account = account
            self.pending_profile = None
            self.show_page("ChurchSelectionPage")

        self.run_background(
            lambda: self.cloud_service.create_account(profile, display_name),
            success,
            lambda exc: page.show_error(str(exc)),
        )

    def refresh_church_choices(self) -> None:
        page = self.pages["ChurchSelectionPage"]
        if not self.current_account:
            page.set_status("Log in to view churches.")
            return
        page.set_status("Loading churches and requests…")
        self.run_background(
            lambda: self.cloud_service.list_church_choices(
                self.current_account["id"]
            ),
            page.show_choices,
            lambda exc: page.set_status(f"Church list unavailable: {exc}"),
        )

    def select_church(self, church_id: str) -> None:
        page = self.pages["ChurchSelectionPage"]
        user_id = self.current_account["id"]

        def success(session):
            self._activate_session(session)
            self.sync_now()

        self.run_background(
            lambda: self.cloud_service.get_session(user_id, church_id),
            success,
            lambda exc: page.set_status(str(exc)),
        )

    def request_membership(self, church_id: str) -> None:
        page = self.pages["ChurchSelectionPage"]
        user_id = self.current_account["id"]

        def success(_value):
            page.set_status("Membership request sent to the church admins.")
            self.refresh_church_choices()

        self.run_background(
            lambda: self.cloud_service.request_membership(user_id, church_id),
            success,
            lambda exc: page.set_status(str(exc)),
        )

    def register_church(
        self, church_name, location, popup, save_button, status_label
    ) -> None:
        user_id = self.current_account["id"]

        def success(session):
            if popup.winfo_exists():
                popup.destroy()
            self._activate_session(session)
            self.sync_now()

        def error(exc):
            if popup.winfo_exists():
                save_button.config(state="normal")
                status_label.config(text=str(exc))

        self.run_background(
            lambda: self.cloud_service.create_church_with_admin(
                user_id, church_name, location
            ),
            success,
            error,
        )

    def switch_church(self) -> None:
        self.current_account = dict(self.current_session["user"])
        self.show_page("ChurchSelectionPage")

    def return_to_control_center(self) -> None:
        if self.current_session:
            self.show_page("ControlCenterPage")

    def _activate_session(self, session: dict[str, Any]) -> None:
        self.current_session = session
        self.current_account = dict(session["user"])
        self.db_service.save_cached_session(session)
        self._last_synced_cloud_revision = None
        self._last_attempted_local_marker = None
        self._next_sync_retry_at = 0.0
        self.show_page("ControlCenterPage")

    # ------------------------------------------------------------------
    # Online revalidation and synchronization
    # ------------------------------------------------------------------

    def resume_session_online(self) -> None:
        if (
            not self.current_session
            or not self.cloud_service.has_credentials()
            or self._sync_in_progress
        ):
            return
        self._sync_in_progress = True
        page = self.pages["ControlCenterPage"]
        page.set_status("Working offline; checking membership and cloud updates…")
        church_id = self.current_session["church"]["id"]

        def work():
            profile = self.auth_service.resume_online()
            account = self.cloud_service.find_account(profile)
            if not account:
                raise MembershipRequired("The cached account no longer exists.")
            session = self.cloud_service.get_session(account["id"], church_id)
            result = self.sync_service.sync(account["id"], church_id)
            return account, session, result

        def success(value):
            account, session, result = value
            self._sync_in_progress = False
            self._last_synced_cloud_revision = result.cloud_revision
            self._last_attempted_local_marker = None
            self._next_sync_retry_at = 0.0
            self.current_account = account
            self.current_session = session
            self.db_service.save_cached_session(session)
            page.refresh()
            page.show_sync_result(result)

        def error(exc):
            self._sync_in_progress = False
            page.set_status(f"Offline or access unavailable — local data is safe. {exc}")

        self.run_background(work, success, error)

    def sync_now(self) -> None:
        if not self.current_session:
            return
        if self._sync_in_progress:
            return
        self._sync_in_progress = True
        page = self.pages["ControlCenterPage"]
        page.set_status("Synchronizing with Atlas…")
        church_id = self.current_session["church"]["id"]
        user_id = self.current_session["user"]["id"]

        def success(result):
            self._sync_in_progress = False
            self._last_synced_cloud_revision = result.cloud_revision
            self._last_attempted_local_marker = None
            self._next_sync_retry_at = 0.0
            page.refresh()
            page.show_sync_result(result)

        def error(exc):
            self._sync_in_progress = False
            self._last_attempted_local_marker = self.db_service.get_outbox_marker(church_id)
            self._next_sync_retry_at = monotonic() + 15
            page.refresh_sync_status()
            page.set_status(f"Sync unavailable — changes remain local. {exc}")

        self.run_background(
            lambda: self.sync_service.sync(user_id, church_id), success, error
        )

    def _auto_sync_tick(self) -> None:
        self.after(1000, self._auto_sync_tick)
        if (
            not self.current_session
            or self._sync_in_progress
            or self._revision_poll_in_progress
            or not self.cloud_service.has_credentials()
        ):
            return
        church_id = self.current_session["church"]["id"]
        local_marker = self.db_service.get_outbox_marker(church_id)
        if local_marker:
            if (
                local_marker != self._last_attempted_local_marker
                or monotonic() >= self._next_sync_retry_at
            ):
                self._last_attempted_local_marker = local_marker
                self.sync_now()
            return

        self._revision_poll_in_progress = True

        def success(revision):
            self._revision_poll_in_progress = False
            revision = int(revision)
            if self._last_synced_cloud_revision is None:
                self._last_synced_cloud_revision = revision
                self.sync_now()
            elif revision != self._last_synced_cloud_revision:
                self._last_synced_cloud_revision = revision
                self.sync_now()

        def error(_exc):
            self._revision_poll_in_progress = False

        self.run_background(
            lambda: self.cloud_service.get_sync_revision(church_id), success, error
        )

    def present_stage_cue(
        self,
        live_view_cue: dict[str, Any],
        stage_view_cue: dict[str, Any] | None = None,
    ) -> None:
        self.present_live_cue(live_view_cue)
        self.publish_stage_cue(stage_view_cue or live_view_cue)

    def present_live_cue(self, live_view_cue: dict[str, Any]) -> None:
        if self.presentation_window is None:
            from ui.presentation_window import PresentationWindow

            self.presentation_window = PresentationWindow(self)
        self.presentation_window.present(live_view_cue)

    def publish_stage_cue(self, stage_view_cue: dict[str, Any]) -> None:
        if self.current_session:
            self.stage_publisher.publish(
                self.current_session["church"]["id"],
                stage_view_cue,
            )

    def configure_stage_publisher(self, server_url, token) -> None:
        self.db_service.set_setting("render_server_url", server_url.strip())
        self.db_service.set_setting("render_control_token", token.strip())
        self.stage_publisher.configure(server_url, token)

    def _shutdown(self) -> None:
        self._remember_window_geometry()
        self.stage_publisher.close()
        self.cloud_service.close()
        self.destroy()

    def publish_to_global(self, entity_type: str, entity_id: str) -> None:
        if not self.current_session or self.current_session["user"].get("role") != "admin":
            raise AdminRequired("Only a church admin can publish globally.")
        page = self.pages["ControlCenterPage"]
        if self._sync_in_progress:
            page.set_status("Wait for the current synchronization to finish.")
            return
        self._sync_in_progress = True
        user_id = self.current_session["user"]["id"]
        church_id = self.current_session["church"]["id"]
        page.set_status("Syncing and publishing a global snapshot…")

        def work():
            self.sync_service.sync(user_id, church_id)
            self.cloud_service.publish_to_global(
                user_id, church_id, entity_type, entity_id
            )
            return self.sync_service.sync(user_id, church_id)

        def success(result):
            self._sync_in_progress = False
            self._last_synced_cloud_revision = result.cloud_revision
            page.refresh()
            page.show_sync_result(result)
            page.set_status("Published to the global library.")

        def error(exc):
            self._sync_in_progress = False
            page.set_status(f"Global publishing failed: {exc}")

        self.run_background(work, success, error)

    # ------------------------------------------------------------------
    # Dialogs and logout
    # ------------------------------------------------------------------

    def open_admin_dialog(self) -> None:
        if self.current_session and self.current_session["user"].get("role") == "admin":
            AdminDialog(self)

    def open_import_dialog(self) -> None:
        if self.current_session:
            GlobalLibraryDialog(self)

    # Compatibility alias for older callers and integrations.
    def open_global_library(self) -> None:
        self.open_import_dialog()

    def open_display_settings(self, view_type="live") -> None:
        if self.current_session and self.current_session["user"].get("role") == "admin":
            DisplaySettingsDialog(self, view_type)

    def open_output_settings(self) -> None:
        if self.current_session:
            OutputSettingsDialog(self)

    def logout(self) -> None:
        if not messagebox.askyesno(
            "Log out", "Log out on this computer? Local songs will remain stored."
        ):
            return
        self.auth_service.logout()
        self.db_service.clear_cached_session()
        self.current_session = None
        self.current_account = None
        self.pending_profile = None
        if self.presentation_window is not None:
            self.presentation_window.withdraw()
        self.show_page("AuthPage")
