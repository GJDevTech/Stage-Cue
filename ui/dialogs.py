import base64
from io import BytesIO
from pathlib import Path
import tkinter as tk
from tkinter import colorchooser, filedialog, font as tkfont, messagebox, simpledialog, ttk

from PIL import Image, ImageOps, ImageTk

try:
    import cv2
except ImportError:
    cv2 = None

from services.presentation_service import apply_text_case, calculate_text_box_layout
from ui.theme import (
    PALETTE,
    fit_toplevel,
    icon_size,
    infer_icon,
    modern_button,
    px,
    style_entry,
    ui_font,
)


def action_button(parent, text, command, color="#4b5563"):
    """Build a dialog action button from a palette role.

    Call sites pass the legacy hex constants, so they are translated to the
    current palette here rather than edited one by one; an unrecognised colour
    is still honoured verbatim.
    """

    roles = {
        "#2563eb": ("primary", "primary_hover"),
        "#16a34a": ("success", "success_hover"),
        "#16835b": ("success", "success_hover"),
        "#dc2626": ("danger", "danger_hover"),
        "#b91c1c": ("danger_hover", "danger"),
        "#7655b5": ("purple", "purple_hover"),
        "#4b5563": ("neutral", "neutral_hover"),
        "#536273": ("neutral", "neutral_hover"),
    }
    role = roles.get(str(color).casefold())
    if role:
        base, hover = PALETTE[role[0]], PALETTE[role[1]]
    else:
        base = hover = color
    return modern_button(
        parent,
        text,
        command,
        color=base,
        hover_color=hover,
        icon=infer_icon(text),
        compact=True,
    )


class AdminDialog(tk.Toplevel):
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.members = []
        self.requests = []
        self.title("Church Members and Requests")
        fit_toplevel(self, 860, 540, 720, 440)
        self.configure(bg=PALETTE["canvas"])
        self.transient(controller)

        body = tk.Frame(self, bg=PALETTE["canvas"], padx=px(22), pady=px(22))
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(1, weight=1)

        self.status_var = tk.StringVar(value="Loading members…")
        tk.Label(
            body,
            textvariable=self.status_var,
            bg=PALETTE["primary_soft"],
            fg=PALETTE["primary_soft_text"],
            anchor="w",
            padx=px(10),
            pady=px(7),
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(px(0), px(10)))

        member_panel = self._panel(body, "Members")
        member_panel.grid(row=1, column=0, sticky="nsew", padx=(px(0), px(6)))
        self.member_list = tk.Listbox(member_panel, font=ui_font(10))
        self.member_list.pack(fill="both", expand=True, padx=px(10), pady=px(8))
        member_controls = tk.Frame(member_panel, bg=PALETTE["surface"])
        member_controls.pack(fill="x", padx=px(10), pady=(px(0), px(10)))
        action_button(member_controls, "Add by Email", self.add_member, "#16a34a").pack(
            side="left"
        )
        action_button(member_controls, "Toggle Admin", self.toggle_admin, "#2563eb").pack(
            side="left", padx=px(5)
        )
        action_button(member_controls, "Remove", self.remove_member, "#dc2626").pack(
            side="left"
        )

        request_panel = self._panel(body, "Pending requests")
        request_panel.grid(row=1, column=1, sticky="nsew", padx=(px(6), px(0)))
        self.request_list = tk.Listbox(request_panel, font=ui_font(10))
        self.request_list.pack(fill="both", expand=True, padx=px(10), pady=px(8))
        request_controls = tk.Frame(request_panel, bg=PALETTE["surface"])
        request_controls.pack(fill="x", padx=px(10), pady=(px(0), px(10)))
        action_button(request_controls, "Accept", self.accept_request, "#16a34a").pack(
            side="left"
        )
        action_button(request_controls, "Reject", self.reject_request, "#dc2626").pack(
            side="left", padx=px(5)
        )
        action_button(request_controls, "Refresh", self.reload).pack(side="left")

        self.reload()

    @staticmethod
    def _panel(parent, title):
        panel = tk.Frame(
            parent,
            bg=PALETTE["surface"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
        )
        tk.Label(
            panel,
            text=title,
            font=ui_font(12, "bold"),
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
        ).pack(anchor="w", padx=px(10), pady=(px(10), px(2)))
        return panel

    def _ids(self):
        session = self.controller.current_session
        return session["user"]["id"], session["church"]["id"]

    def reload(self):
        actor_id, church_id = self._ids()
        self.status_var.set("Loading members and requests…")

        def success(data):
            if not self.winfo_exists():
                return
            self.members = data["members"]
            self.requests = data["requests"]
            self.member_list.delete(0, tk.END)
            for member in self.members:
                self.member_list.insert(
                    tk.END,
                    f"{member['name']} — {member['email']}  [{member['role']}]",
                )
            self.request_list.delete(0, tk.END)
            for request in self.requests:
                user = request["user"]
                self.request_list.insert(tk.END, f"{user['name']} — {user['email']}")
            self.status_var.set(
                f"{len(self.members)} member(s), {len(self.requests)} pending request(s)."
            )

        self.controller.run_background(
            lambda: self.controller.cloud_service.list_admin_data(
                actor_id, church_id
            ),
            success,
            self._show_error,
        )

    def _show_error(self, error):
        if self.winfo_exists():
            self.status_var.set(str(error))

    def _run(self, message, work):
        self.status_var.set(message)

        def success(_value):
            if self.winfo_exists():
                self.reload()

        self.controller.run_background(work, success, self._show_error)

    def selected_member(self):
        selection = self.member_list.curselection()
        return self.members[selection[0]] if selection else None

    def selected_request(self):
        selection = self.request_list.curselection()
        return self.requests[selection[0]] if selection else None

    def add_member(self):
        email = simpledialog.askstring(
            "Add Member",
            "Stage Cue account email:",
            parent=self,
        )
        if not email:
            return
        actor_id, church_id = self._ids()
        self._run(
            "Adding member…",
            lambda: self.controller.cloud_service.add_member_by_email(
                actor_id, church_id, email
            ),
        )

    def toggle_admin(self):
        member = self.selected_member()
        if not member:
            self.status_var.set("Select a member first.")
            return
        actor_id, church_id = self._ids()
        if member["id"] == actor_id:
            self.status_var.set("Ask another admin to change your own role.")
            return
        make_admin = member["role"] != "admin"
        action = "promote" if make_admin else "remove admin access from"
        if not messagebox.askyesno(
            "Change Admin Role",
            f"{action.capitalize()} {member['name']}?",
            parent=self,
        ):
            return
        self._run(
            "Updating admin role…",
            lambda: self.controller.cloud_service.set_member_admin(
                actor_id, church_id, member["id"], make_admin
            ),
        )

    def remove_member(self):
        member = self.selected_member()
        if not member:
            self.status_var.set("Select a member first.")
            return
        actor_id, church_id = self._ids()
        if member["id"] == actor_id:
            self.status_var.set("Ask another admin to remove your membership.")
            return
        if not messagebox.askyesno(
            "Remove Member",
            f"Remove {member['name']} from this church?",
            parent=self,
        ):
            return
        self._run(
            "Removing member…",
            lambda: self.controller.cloud_service.remove_member(
                actor_id, church_id, member["id"]
            ),
        )

    def accept_request(self):
        request = self.selected_request()
        if not request:
            self.status_var.set("Select a membership request first.")
            return
        actor_id, church_id = self._ids()
        self._run(
            "Accepting request…",
            lambda: self.controller.cloud_service.approve_membership_request(
                actor_id, church_id, request["id"]
            ),
        )

    def reject_request(self):
        request = self.selected_request()
        if not request:
            self.status_var.set("Select a membership request first.")
            return
        actor_id, church_id = self._ids()
        self._run(
            "Rejecting request…",
            lambda: self.controller.cloud_service.reject_membership_request(
                actor_id, church_id, request["id"]
            ),
        )


class GlobalLibraryDialog(tk.Toplevel):
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.db = controller.db_service
        self.songbooks = self.db.list_global_songbooks()
        self.songs = self.db.list_global_songs()
        self.title("Import Songs and Songbooks")
        fit_toplevel(self, 820, 590, 680, 460)
        self.configure(bg=PALETTE["canvas"])
        self.transient(controller)

        body = tk.Frame(self, bg=PALETTE["canvas"], padx=px(22), pady=px(22))
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        self.status_var = tk.StringVar(
            value=(
                "Imports create independent copies for the current church. "
                "This catalog refreshes automatically whenever the app is online."
            )
        )
        tk.Label(
            body,
            textvariable=self.status_var,
            bg=PALETTE["primary_soft"],
            fg=PALETTE["primary_soft_text"],
            anchor="w",
            padx=px(10),
            pady=px(7),
        ).grid(row=0, column=0, sticky="ew", pady=(px(0), px(10)))

        panel = AdminDialog._panel(body, "Published songbooks and songs")
        panel.grid(row=1, column=0, sticky="nsew")

        tree_frame = tk.Frame(panel, bg=PALETTE["surface"])
        tree_frame.pack(fill="both", expand=True, padx=px(10), pady=(px(8), px(5)))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        self.library_tree = ttk.Treeview(
            tree_frame,
            columns=("source",),
            show="tree headings",
            selectmode="extended",
            style="StageCue.Treeview",
        )
        self.library_tree.heading("#0", text="Songbook / Song", anchor="w")
        self.library_tree.heading("source", text="Published by", anchor="w")
        self.library_tree.column("#0", width=px(430), minwidth=px(260), stretch=True)
        self.library_tree.column("source", width=px(210), minwidth=px(130), stretch=True)
        scrollbar = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.library_tree.yview
        )
        self.library_tree.configure(yscrollcommand=scrollbar.set)
        self.library_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.book_by_id = {str(book["id"]): book for book in self.songbooks}
        self.song_by_id = {str(song["id"]): song for song in self.songs}
        songs_by_book: dict[str, list[dict]] = {}
        for song in self.songs:
            songs_by_book.setdefault(str(song.get("global_songbook_id") or ""), []).append(song)

        for book in self.songbooks:
            book_id = str(book["id"])
            source = book.get("source_church_name") or "Unknown church"
            parent = self.library_tree.insert(
                "",
                tk.END,
                iid=f"book:{book_id}",
                text=str(book.get("name") or "Untitled songbook"),
                values=(source,),
                open=True,
            )
            for song in sorted(
                songs_by_book.pop(book_id, []),
                key=lambda item: str(item.get("title") or "").casefold(),
            ):
                self.library_tree.insert(
                    parent,
                    tk.END,
                    iid=f"song:{song['id']}",
                    text=str(song.get("title") or "Untitled song"),
                    values=(source,),
                )

        orphan_songs = [song for songs in songs_by_book.values() for song in songs]
        if orphan_songs:
            parent = self.library_tree.insert(
                "", tk.END, iid="other", text="Other published songs", open=True
            )
            for song in sorted(
                orphan_songs,
                key=lambda item: str(item.get("title") or "").casefold(),
            ):
                source = song.get("source_church_name") or "Unknown church"
                self.library_tree.insert(
                    parent,
                    tk.END,
                    iid=f"song:{song['id']}",
                    text=str(song.get("title") or "Untitled song"),
                    values=(source,),
                )

        controls = tk.Frame(panel, bg=PALETTE["surface"])
        controls.pack(fill="x", padx=px(10), pady=(px(0), px(10)))
        tk.Label(
            controls,
            text="Use Ctrl/Command-click or Shift-click to select multiple songbooks and songs.",
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=ui_font(8),
        ).pack(side="left")
        action_button(
            controls, "Import Selected", self.import_selected, "#16a34a"
        ).pack(side="right")

    def selected_imports(self) -> tuple[list[dict], list[dict]]:
        selected = set(self.library_tree.selection())
        selected_book_ids = {
            item.split(":", 1)[1] for item in selected if item.startswith("book:")
        }
        books = [
            self.book_by_id[book_id]
            for book_id in selected_book_ids
            if book_id in self.book_by_id
        ]
        songs = []
        for item in selected:
            if not item.startswith("song:"):
                continue
            song = self.song_by_id.get(item.split(":", 1)[1])
            if song is None:
                continue
            # The whole-book import already includes this child song.
            if str(song.get("global_songbook_id") or "") in selected_book_ids:
                continue
            songs.append(song)
        return books, songs

    def import_selected(self):
        books, songs = self.selected_imports()
        if not books and not songs:
            self.status_var.set("Select one or more songbooks or songs first.")
            return
        church_id = self.controller.current_session["church"]["id"]
        control_center = self.controller.pages["ControlCenterPage"]
        target_book = control_center.selected_songbook_id()
        try:
            for book in books:
                self.db.import_global_songbook(church_id, book["id"])
            for song in songs:
                self.db.import_global_song(church_id, song["id"], target_book)
        except Exception as exc:
            self.status_var.set(f"Import stopped: {exc}")
            control_center.refresh()
            return
        control_center.refresh()
        parts = []
        if books:
            parts.append(f"{len(books)} songbook(s)")
        if songs:
            parts.append(f"{len(songs)} individual song(s)")
        self.status_var.set(f"Imported {' and '.join(parts)} as independent copies.")


class DisplaySettingsDialog(tk.Toplevel):
    def __init__(self, controller, view_type="live"):
        super().__init__(controller)
        self.controller = controller
        requested_view = str(view_type).strip().casefold()
        self.view_type = requested_view if requested_view in {"live", "stage", "message"} else "live"
        self.view_label = {
            "live": "Live View",
            "stage": "Stage View",
            "message": "Custom Message",
        }[self.view_type]
        church_id = controller.current_session["church"]["id"]
        settings = controller.db_service.get_display_settings(
            church_id, self.view_type
        )
        self.message_settings = (
            controller.db_service.get_display_settings(church_id, "message")
            if self.view_type == "stage"
            else None
        )

        self.title(f"{self.view_label} Display Settings")
        fit_toplevel(self, 920, 820, 740, 600)
        self.configure(bg=PALETTE["canvas"])
        self.transient(controller)
        self.grab_set()

        frame = tk.Frame(self, bg=PALETTE["canvas"], padx=px(20), pady=px(16))
        frame.pack(fill="both", expand=True)
        tk.Label(
            frame,
            text=f"{self.view_label} display style",
            font=ui_font(17, "bold"),
            bg=PALETTE["canvas"],
            fg=PALETTE["text"],
        ).pack(anchor="w")

        self.values = {
            key: tk.StringVar(value=str(settings[key]))
            for key in (
                "fontFamily",
                
                "fontSize",
                "textColor",
                "textCase",
                "backgroundColor",
                "outlineColor",
                "outlineWidth",
                "shadowColor",
                "shadowOffsetX",
                "shadowOffsetY",
                "textBoxX",
                "textBoxY",
                "textBoxWidth",
                "textBoxHeight",
                "logoX",
                "logoY",
                "logoWidth",
                "maxLinesPerSlide",
                "upcomingTextColor",
                "upcomingBoxX",
                "upcomingBoxY",
                "upcomingBoxWidth",
                "upcomingBoxHeight",
            )
        }
        self.flags = {
            key: tk.BooleanVar(value=bool(settings[key]))
            for key in ("bold", "italic", "outlineEnabled", "shadowEnabled")
        }
        if self.view_type == "stage":
            self.flags.update(
                {
                    "showCurrentSongTitle": tk.BooleanVar(
                        value=bool(settings.get("showCurrentSongTitle", True))
                    ),
                    "showSectionType": tk.BooleanVar(
                        value=bool(settings.get("showSectionType", True))
                    ),
                    "showUpcomingSongTitle": tk.BooleanVar(
                        value=bool(settings.get("showUpcomingSongTitle", True))
                    ),
                }
            )
        self.context_style_values = {}
        self.context_style_flags = {}
        if self.view_type == "stage":
            context_defaults = {
                "songTitleStyle": {
                    "fontFamily": "Arial", "fontSize": 30, "textColor": "#FFFFFF",
                    "textCase": "preserve", "bold": True, "italic": False,
                },
                "sectionTypeStyle": {
                    "fontFamily": "Arial", "fontSize": 23, "textColor": "#D1D5DB",
                    "textCase": "preserve", "bold": False, "italic": False,
                },
                "upcomingSongTitleStyle": {
                    "fontFamily": "Arial", "fontSize": 24, "textColor": "#F8FAFC",
                    "textCase": "preserve", "bold": True, "italic": False,
                },
            }
            for style_key, defaults in context_defaults.items():
                source = settings.get(style_key)
                source = source if isinstance(source, dict) else {}
                merged = {**defaults, **source}
                self.context_style_values[style_key] = {
                    key: tk.StringVar(value=str(merged[key]))
                    for key in ("fontFamily", "fontSize", "textColor", "textCase")
                }
                self.context_style_flags[style_key] = {
                    key: tk.BooleanVar(value=bool(merged[key]))
                    for key in ("bold", "italic")
                }
        self.text_horizontal_alignment = tk.StringVar(
            value=settings["textHorizontalAlign"]
        )
        self.text_vertical_alignment = tk.StringVar(
            value=settings["textVerticalAlign"]
        )
        self.next_slide_count = tk.StringVar(
            value=str(settings.get("nextSlideCount", 0))
        )
        self.message_values = {}
        self.message_flags = {}
        self.message_horizontal_alignment = tk.StringVar(value="center")
        self.message_vertical_alignment = tk.StringVar(value="center")
        if self.message_settings is not None:
            self.message_values = {
                key: tk.StringVar(value=str(self.message_settings[key]))
                for key in (
                    "fontFamily", "fontSize", "textColor", "textCase",
                    "backgroundColor", "outlineColor", "outlineWidth",
                    "shadowColor", "shadowOffsetX", "shadowOffsetY",
                    "textBoxX", "textBoxY", "textBoxWidth", "textBoxHeight",
                )
            }
            self.message_flags = {
                key: tk.BooleanVar(value=bool(self.message_settings[key]))
                for key in ("bold", "italic", "outlineEnabled", "shadowEnabled")
            }
            self.message_horizontal_alignment.set(
                str(self.message_settings.get("textHorizontalAlign", "center"))
            )
            self.message_vertical_alignment.set(
                str(self.message_settings.get("textVerticalAlign", "center"))
            )
        self.background_type = tk.StringVar(
            value=str(settings.get("backgroundType", "solid"))
        )
        self.background_image_data = str(settings.get("backgroundImageData", ""))
        self.background_image_file_name = tk.StringVar(
            value=str(settings.get("backgroundImageFileName", ""))
            or "No image selected"
        )
        self.background_video_path = str(settings.get("backgroundVideoPath", ""))
        self.background_video_file_name = tk.StringVar(
            value=str(settings.get("backgroundVideoFileName", ""))
            or "No video selected"
        )
        self.alignment_caption = tk.StringVar()
        self.logo_data = str(settings.get("logoData", ""))
        self.logo_file_name = tk.StringVar(
            value=str(settings.get("logoFileName", "")) or "No logo selected"
        )
        self._layout_logo_image = None
        self._layout_logo_photo = None
        self._layout_background_image = None
        self._layout_background_photo = None
        self._layout_drag = None
        self.status_var = tk.StringVar()
        self._logo_preview_active = False
        self._message_preview_active = False
        self.context_style_frames = {}

        self._build_style_preview(frame)

        notebook = ttk.Notebook(frame)
        self.settings_notebook = notebook
        notebook.pack(fill="both", expand=True, pady=(px(8), px(0)))
        text_tab = self._add_scrollable_tab(notebook, "Text")
        effects_tab = self._add_scrollable_tab(notebook, "Outline & Shadow")
        layout_tab = self._add_scrollable_tab(notebook, "Position & Slides")
        self._build_text_tab(text_tab)
        self._build_effects_tab(effects_tab)
        self._build_layout_tab(layout_tab)
        if self.view_type == "live":
            background_tab = self._add_scrollable_tab(notebook, "Background")
            logo_tab = self._add_scrollable_tab(notebook, "Church Logo")
            self._build_background_tab(background_tab)
            self._build_logo_tab(logo_tab)
        elif self.view_type == "stage":
            upcoming_tab = self._add_scrollable_tab(notebook, "Upcoming Slides")
            self._build_upcoming_tab(upcoming_tab)
            message_tab = self._add_scrollable_tab(notebook, "Messages")
            self._build_message_style_tab(message_tab)
        notebook.bind("<<NotebookTabChanged>>", self._display_settings_tab_changed)

        for variable in self.values.values():
            variable.trace_add("write", self._draw_layout_editor)
        for variable in self.flags.values():
            variable.trace_add("write", self._draw_layout_editor)
        if self.view_type == "stage":
            for style_values in self.context_style_values.values():
                for variable in style_values.values():
                    variable.trace_add("write", self._draw_layout_editor)
            for style_flags in self.context_style_flags.values():
                for variable in style_flags.values():
                    variable.trace_add("write", self._draw_layout_editor)
            for key in ("showCurrentSongTitle", "showSectionType", "showUpcomingSongTitle"):
                self.flags[key].trace_add("write", self._update_context_style_visibility)
            for variable in self.message_values.values():
                variable.trace_add("write", self._draw_layout_editor)
            for variable in self.message_flags.values():
                variable.trace_add("write", self._draw_layout_editor)
            self.message_horizontal_alignment.trace_add("write", self._draw_layout_editor)
            self.message_vertical_alignment.trace_add("write", self._draw_layout_editor)
            self._update_context_style_visibility()
        self.next_slide_count.trace_add("write", self._draw_layout_editor)
        self.background_type.trace_add("write", self._background_type_changed)

        action_button(frame, "Save Locally", self.save, "#2563eb").pack(
            anchor="e", pady=(px(8), 0)
        )

    def _display_settings_tab_changed(self, _event=None):
        try:
            title = self.settings_notebook.tab(
                self.settings_notebook.select(), "text"
            )
        except tk.TclError:
            title = ""
        if self.view_type == "live":
            self._logo_preview_active = title == "Church Logo"
        elif self.view_type == "stage":
            self._message_preview_active = title == "Messages"
        self._draw_layout_editor()

    def _add_scrollable_tab(self, notebook, title):
        outer = tk.Frame(notebook, bg=PALETTE["surface"])
        canvas = tk.Canvas(
            outer,
            bg=PALETTE["surface"],
            highlightthickness=0,
            borderwidth=0,
        )
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        inner = tk.Frame(canvas, bg=PALETTE["surface"], padx=px(20), pady=px(16))
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def update_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_width(event):
            canvas.itemconfigure(window_id, width=max(1, event.width))

        inner.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", fit_width)
        notebook.add(outer, text=title)
        return inner

    def _build_style_preview(self, parent):
        preview = tk.Frame(parent, bg=PALETTE["canvas"])
        preview.pack(fill="x")
        tk.Label(
            preview,
            text="PREVIEW — unsaved changes",
            bg=PALETTE["canvas"],
            fg=PALETTE["subtle_text"],
            font=ui_font(9, "bold"),
        ).pack(anchor="w", pady=(px(0), px(5)))
        preview_width = 420 if self.winfo_screenheight() < 850 else 520
        self.layout_canvas = tk.Canvas(
            preview,
            width=preview_width,
            height=round(preview_width * 9 / 16),
            bg="#111827",
            highlightthickness=1,
            highlightbackground=PALETTE["border_strong"],
            cursor="fleur",
        )
        self.layout_canvas.pack(anchor="center")
        self.layout_background_id = self.layout_canvas.create_image(
            0, 0, anchor="nw", state="hidden"
        )
        self.layout_box_id = self.layout_canvas.create_rectangle(
            0, 0, 0, 0, outline="#38bdf8", width=2, dash=(6, 3), fill=""
        )
        self.layout_shadow_id = self.layout_canvas.create_text(0, 0, text="")
        self.layout_outline_ids = [
            self.layout_canvas.create_text(0, 0, text="") for _ in range(8)
        ]
        self.layout_sample_id = self.layout_canvas.create_text(
            0,
            0,
            text=(
                "Service begins in 10 minutes"
                if self.view_type == "message"
                else "Sample song lyrics\ninside the text box"
            ),
            fill="white",
            font=("Arial", 16, "bold"),
        )
        self.layout_song_title_id = self.layout_canvas.create_text(
            0, 0, text="Amazing Grace", fill="#FFFFFF", anchor="n",
            justify="center", state="hidden"
        )
        self.layout_section_type_id = self.layout_canvas.create_text(
            0, 0, text="Verse 1", fill="#D1D5DB", anchor="n",
            justify="center", state="hidden"
        )
        self.layout_upcoming_title_id = self.layout_canvas.create_text(
            0, 0, text="Blessed Assurance", fill="#F8FAFC", anchor="n",
            justify="center", state="hidden"
        )
        self.layout_upcoming_sample_ids = [
            self.layout_canvas.create_text(
                0,
                0,
                text=f"Next slide {index + 1} lyrics",
                fill="#A7B0BC",
                anchor="s",
                justify="center",
                state="hidden",
            )
            for index in range(5)
        ]
        self.layout_upcoming_box_id = self.layout_canvas.create_rectangle(
            0,
            0,
            0,
            0,
            outline="#F59E0B",
            width=2,
            dash=(6, 3),
            fill="",
            state="hidden",
        )
        self.layout_upcoming_handle_id = self.layout_canvas.create_rectangle(
            0,
            0,
            0,
            0,
            fill="#F59E0B",
            outline="white",
            width=1,
            state="hidden",
        )
        self.layout_logo_id = self.layout_canvas.create_image(
            0, 0, anchor="nw", state="hidden"
        )
        self.layout_handle_id = self.layout_canvas.create_rectangle(
            0, 0, 0, 0, fill="#38bdf8", outline="white", width=1
        )
        self.layout_canvas.bind("<Configure>", self._draw_layout_editor)
        self.layout_canvas.bind("<ButtonPress-1>", self._layout_press)
        self.layout_canvas.bind("<B1-Motion>", self._layout_motion)
        self.layout_canvas.bind("<ButtonRelease-1>", self._layout_release)
        self._decode_layout_background()
        self._decode_layout_logo()

    def _build_text_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        self._label(tab, "Font family", 0)
        families = sorted(set(tkfont.families(self)))
        ttk.Combobox(
            tab,
            textvariable=self.values["fontFamily"],
            values=families,
        ).grid(row=0, column=1, sticky="ew", pady=px(5))
        self._label(tab, "Font size", 1)
        ttk.Spinbox(
            tab,
            from_=12,
            to=160,
            textvariable=self.values["fontSize"],
            width=10,
        ).grid(row=1, column=1, sticky="w", pady=px(5))
        self._color_row(tab, "Text fill color", "textColor", 2)
        self._color_row(tab, "Background color", "backgroundColor", 3)
        self._label(tab, "Lyrics letter case", 4)
        ttk.Combobox(
            tab,
            state="readonly",
            textvariable=self.values["textCase"],
            values=("preserve", "upper", "lower"),
            width=18,
        ).grid(row=4, column=1, sticky="w", pady=px(5))
        flags = tk.Frame(tab, bg=PALETTE["surface"])
        flags.grid(row=5, column=0, columnspan=2, sticky="w", pady=(px(12), px(0)))
        tk.Checkbutton(
            flags, text="Bold", variable=self.flags["bold"], bg=PALETTE["surface"]
        ).pack(side="left")
        tk.Checkbutton(
            flags, text="Italic", variable=self.flags["italic"], bg=PALETTE["surface"]
        ).pack(side="left", padx=(px(15), px(0)))

        if self.view_type == "stage":
            context = tk.LabelFrame(
                tab,
                text="Lyric context",
                bg=PALETTE["surface"],
                padx=px(10),
                pady=px(8),
            )
            context.grid(
                row=6, column=0, columnspan=2, sticky="ew", pady=(px(18), px(0))
            )
            tk.Checkbutton(
                context,
                text="Show current song title",
                variable=self.flags["showCurrentSongTitle"],
                bg=PALETTE["surface"],
            ).pack(anchor="w")
            tk.Checkbutton(
                context,
                text="Show section type (Verse 1, Chorus, etc.)",
                variable=self.flags["showSectionType"],
                bg=PALETTE["surface"],
            ).pack(anchor="w", pady=(px(4), 0))
            tk.Checkbutton(
                context,
                text="Show song title when an upcoming slide is from another song",
                variable=self.flags["showUpcomingSongTitle"],
                bg=PALETTE["surface"],
            ).pack(anchor="w", pady=(px(4), 0))

            self._build_context_style_editor(
                tab, "Current song title style", "songTitleStyle", 7
            )
            self._build_context_style_editor(
                tab, "Section type style", "sectionTypeStyle", 8
            )
            self._build_context_style_editor(
                tab, "Next song title style", "upcomingSongTitleStyle", 9
            )

    def _build_context_style_editor(self, parent, title, style_key, row):
        frame = tk.LabelFrame(
            parent, text=title, bg=PALETTE["surface"], padx=px(10), pady=px(8)
        )
        frame.grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(px(12), 0)
        )
        frame.grid_columnconfigure(1, weight=1)
        self.context_style_frames[style_key] = frame
        values = self.context_style_values[style_key]
        flags = self.context_style_flags[style_key]
        families = sorted(set(tkfont.families(self)))

        tk.Label(frame, text="Font family", bg=PALETTE["surface"]).grid(
            row=0, column=0, sticky="w", padx=(0, px(18)), pady=px(4)
        )
        ttk.Combobox(frame, textvariable=values["fontFamily"], values=families).grid(
            row=0, column=1, sticky="ew", pady=px(4)
        )
        tk.Label(frame, text="Font size", bg=PALETTE["surface"]).grid(
            row=1, column=0, sticky="w", padx=(0, px(18)), pady=px(4)
        )
        ttk.Spinbox(
            frame, from_=10, to=160, textvariable=values["fontSize"], width=10
        ).grid(row=1, column=1, sticky="w", pady=px(4))

        tk.Label(frame, text="Text color", bg=PALETTE["surface"]).grid(
            row=2, column=0, sticky="w", padx=(0, px(18)), pady=px(4)
        )
        color_holder = tk.Frame(frame, bg=PALETTE["surface"])
        color_holder.grid(row=2, column=1, sticky="ew", pady=px(4))
        color_holder.grid_columnconfigure(0, weight=1)
        style_entry(
            tk.Entry(color_holder, textvariable=values["textColor"], font=ui_font(10))
        ).grid(row=0, column=0, sticky="ew", ipady=px(3))
        modern_button(
            color_holder,
            "Choose color…",
            lambda key=style_key: self._choose_context_style_color(key),
            color=PALETTE["neutral"],
            hover_color=PALETTE["neutral_hover"],
            icon="palette",
            compact=True,
        ).grid(row=0, column=1, padx=(px(6), 0))

        tk.Label(frame, text="Letter case", bg=PALETTE["surface"]).grid(
            row=3, column=0, sticky="w", padx=(0, px(18)), pady=px(4)
        )
        ttk.Combobox(
            frame,
            state="readonly",
            textvariable=values["textCase"],
            values=("preserve", "upper", "lower"),
            width=18,
        ).grid(row=3, column=1, sticky="w", pady=px(4))

        flag_row = tk.Frame(frame, bg=PALETTE["surface"])
        flag_row.grid(row=4, column=0, columnspan=2, sticky="w", pady=(px(5), 0))
        tk.Checkbutton(
            flag_row, text="Bold", variable=flags["bold"], bg=PALETTE["surface"]
        ).pack(side="left")
        tk.Checkbutton(
            flag_row, text="Italic", variable=flags["italic"], bg=PALETTE["surface"]
        ).pack(side="left", padx=(px(15), 0))

    def _choose_context_style_color(self, style_key):
        variable = self.context_style_values[style_key]["textColor"]
        _rgb, selected = colorchooser.askcolor(
            color=variable.get(), parent=self, title="Choose color"
        )
        if selected:
            variable.set(selected.upper())

    def _update_context_style_visibility(self, *_args):
        if self.view_type != "stage" or not self.context_style_frames:
            return
        visibility = {
            "songTitleStyle": self.flags["showCurrentSongTitle"].get(),
            "sectionTypeStyle": self.flags["showSectionType"].get(),
            "upcomingSongTitleStyle": self.flags["showUpcomingSongTitle"].get(),
        }
        for key, frame in self.context_style_frames.items():
            if visibility.get(key, True):
                frame.grid()
            else:
                frame.grid_remove()
        self._draw_layout_editor()

    def _build_effects_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tk.Checkbutton(
            tab,
            text="Enable outline",
            variable=self.flags["outlineEnabled"],
            bg=PALETTE["surface"],
            font=ui_font(10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(px(0), px(8)))
        self._color_row(tab, "Outline color", "outlineColor", 1)
        self._number_row(tab, "Outline width", "outlineWidth", 2, 0, 10)
        tk.Frame(tab, bg=PALETTE["border"], height=1).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=px(16)
        )
        tk.Checkbutton(
            tab,
            text="Enable shadow",
            variable=self.flags["shadowEnabled"],
            bg=PALETTE["surface"],
            font=ui_font(10, "bold"),
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(px(0), px(8)))
        self._color_row(tab, "Shadow color", "shadowColor", 5)
        self._number_row(tab, "Horizontal offset", "shadowOffsetX", 6, -20, 20)
        self._number_row(tab, "Vertical offset", "shadowOffsetY", 7, -20, 20)

    def _build_layout_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tk.Label(
            tab,
            text="Drag the preview text box to move it; drag its blue square to resize.",
            bg=PALETTE["surface"],
            fg=PALETTE["subtle_text"],
            font=ui_font(9, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(px(0), px(6)))

        controls = tk.Frame(tab, bg=PALETTE["surface"])
        controls.grid(row=1, column=0, sticky="ew", pady=(px(4), px(0)))
        controls.grid_columnconfigure(0, weight=1)
        controls.grid_columnconfigure(1, weight=1)

        position = tk.LabelFrame(
            controls, text="Text-box position", bg=PALETTE["surface"], padx=px(10), pady=px(8)
        )
        position.grid(row=0, column=0, sticky="nsew", padx=(px(0), px(5)))
        presets = tk.Frame(position, bg=PALETTE["surface"])
        presets.grid(row=0, column=0, columnspan=2, sticky="w", pady=(px(0), px(5)))
        for caption, preset in (
            ("Top", "top"),
            ("Left", "left"),
            ("Center", "center"),
            ("Right", "right"),
            ("Bottom", "bottom"),
        ):
            modern_button(
                presets,
                caption,
                lambda name=preset: self._apply_box_preset(name),
                color="#536273",
                hover_color=PALETTE["neutral_hover"],
                icon=f"position_{preset}",
                compact=True,
            ).pack(side="left", padx=(px(0), px(3)))
        for row, (label, key, minimum, maximum) in enumerate(
            (
                ("Left (%)", "textBoxX", 0, 95),
                ("Top (%)", "textBoxY", 0, 95),
                ("Width (%)", "textBoxWidth", 5, 100),
                ("Height (%)", "textBoxHeight", 5, 100),
            ),
            start=1,
        ):
            tk.Label(position, text=label, bg=PALETTE["surface"]).grid(
                row=row, column=0, sticky="w", pady=px(2)
            )
            ttk.Spinbox(
                position,
                from_=minimum,
                to=maximum,
                textvariable=self.values[key],
                width=8,
            ).grid(row=row, column=1, sticky="e", pady=px(2))

        alignment = tk.LabelFrame(
            controls, text="Text inside the box", bg=PALETTE["surface"], padx=px(10), pady=px(8)
        )
        alignment.grid(row=0, column=1, sticky="nsew", padx=(px(5), px(0)))
        for row, vertical in enumerate(("top", "center", "bottom")):
            for column, horizontal in enumerate(("left", "center", "right")):
                caption = f"{vertical.title()} {horizontal.title()}"
                modern_button(
                    alignment,
                    caption,
                    lambda h=horizontal, v=vertical: self._set_text_alignment(h, v),
                    color=PALETTE["neutral"],
                    hover_color=PALETTE["neutral_hover"],
                    icon=f"position_{vertical}_{horizontal}".replace("center_center", "center").replace("top_center", "top").replace("bottom_center", "bottom").replace("center_left", "left").replace("center_right", "right"),
                    compact=True,
                ).grid(row=row, column=column, padx=px(2), pady=px(2), sticky="ew")
        tk.Label(
            alignment,
            textvariable=self.alignment_caption,
            bg=PALETTE["surface"],
            fg=PALETTE["primary"],
            font=ui_font(9, "bold"),
        ).grid(row=3, column=0, columnspan=3, pady=(px(7), px(0)))

        slides = tk.Frame(tab, bg=PALETTE["surface"])
        slides.grid(row=2, column=0, sticky="ew", pady=(px(12), px(0)))
        if self.view_type == "live":
            tk.Label(
                slides, text="Maximum lines per slide", bg=PALETTE["surface"], fg=PALETTE["text"]
            ).pack(side="left")
            ttk.Spinbox(
                slides,
                from_=1,
                to=12,
                textvariable=self.values["maxLinesPerSlide"],
                width=8,
            ).pack(side="left", padx=(px(12), px(0)))
        tk.Label(
            tab,
            text=(
                "Blank lines begin a new slide group. Within each group, Stage Cue "
                "applies this line limit. Set it to 2 for two-line slides."
                if self.view_type == "live"
                else (
                    "Stage View uses the slides created by the Live View line limit."
                    if self.view_type == "stage"
                    else "Custom messages use this text box independently of lyric styles."
                )
            ),
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            justify="left",
            wraplength=px(650),
        ).grid(row=3, column=0, sticky="w", pady=(px(7), px(0)))

        for key in ("textBoxX", "textBoxY", "textBoxWidth", "textBoxHeight"):
            self.values[key].trace_add("write", self._draw_layout_editor)
        self.text_horizontal_alignment.trace_add("write", self._draw_layout_editor)
        self.text_vertical_alignment.trace_add("write", self._draw_layout_editor)
        self.after_idle(self._draw_layout_editor)

    def _build_upcoming_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tk.Label(
            tab,
            text="Upcoming lyrics",
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=ui_font(12, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(
            tab,
            text="Slides ahead to display",
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
        ).grid(row=1, column=0, sticky="w", pady=(px(18), px(0)), padx=(px(0), px(18)))
        ttk.Spinbox(
            tab,
            from_=0,
            to=5,
            textvariable=self.next_slide_count,
            width=8,
        ).grid(row=1, column=1, sticky="w", pady=(px(18), px(0)))
        tk.Label(
            tab,
            text=(
                "Use 0 to show only the current slide. Values 1–5 add upcoming "
                "slides to Stage View; each later slide is rendered smaller and "
                "fainter than the one before it."
            ),
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            justify="left",
            wraplength=px(650),
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(px(10), px(0)))

        style = tk.LabelFrame(
            tab,
            text="Upcoming-slide text box",
            bg=PALETTE["surface"],
            padx=px(10),
            pady=px(10),
        )
        style.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(px(18), px(0)))
        style.grid_columnconfigure(1, weight=1)
        self._color_row(style, "Text color", "upcomingTextColor", 0)
        for row, (label, key, minimum, maximum) in enumerate(
            (
                ("Left (%)", "upcomingBoxX", 0, 95),
                ("Top (%)", "upcomingBoxY", 0, 95),
                ("Width (%)", "upcomingBoxWidth", 5, 100),
                ("Height (%)", "upcomingBoxHeight", 5, 100),
            ),
            start=1,
        ):
            self._number_row(style, label, key, row, minimum, maximum)

        presets = tk.Frame(tab, bg=PALETTE["surface"])
        presets.grid(row=4, column=0, columnspan=2, sticky="w", pady=(px(12), px(0)))
        tk.Label(presets, text="Position presets:", bg=PALETTE["surface"]).pack(side="left")
        for caption, preset in (("Top", "top"), ("Center", "center"), ("Bottom", "bottom")):
            modern_button(
                presets,
                caption,
                lambda name=preset: self._apply_upcoming_box_preset(name),
                color=PALETTE["neutral"],
                hover_color=PALETTE["neutral_hover"],
                icon=f"position_{preset}",
                compact=True,
            ).pack(side="left", padx=(px(6), px(0)))
        tk.Label(
            tab,
            text="You can also drag the amber box in the preview and resize it from its square handle.",
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(px(9), px(0)))

    def _build_message_style_tab(self, tab):
        """Edit the custom Stage View message style from the Stage View dialog."""
        tab.grid_columnconfigure(1, weight=1)
        tk.Label(
            tab,
            text="Custom message style",
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=ui_font(12, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(
            tab,
            text=(
                "These settings are used when Send Custom Message is active. "
                "They are saved and synchronized with the church Stage View style."
            ),
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            justify="left",
            wraplength=px(650),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(px(4), px(14)))

        families = sorted(set(tkfont.families(self)))
        tk.Label(tab, text="Font family", bg=PALETTE["surface"], fg=PALETTE["text"]).grid(
            row=2, column=0, sticky="w", padx=(0, px(18)), pady=px(5)
        )
        ttk.Combobox(
            tab, textvariable=self.message_values["fontFamily"], values=families
        ).grid(row=2, column=1, sticky="ew", pady=px(5))
        tk.Label(tab, text="Font size", bg=PALETTE["surface"], fg=PALETTE["text"]).grid(
            row=3, column=0, sticky="w", padx=(0, px(18)), pady=px(5)
        )
        ttk.Spinbox(
            tab, from_=12, to=160, textvariable=self.message_values["fontSize"], width=10
        ).grid(row=3, column=1, sticky="w", pady=px(5))
        self._message_color_row(tab, "Text color", "textColor", 4)
        self._message_color_row(tab, "Background color", "backgroundColor", 5)
        tk.Label(tab, text="Letter case", bg=PALETTE["surface"], fg=PALETTE["text"]).grid(
            row=6, column=0, sticky="w", padx=(0, px(18)), pady=px(5)
        )
        ttk.Combobox(
            tab,
            state="readonly",
            textvariable=self.message_values["textCase"],
            values=("preserve", "upper", "lower"),
            width=18,
        ).grid(row=6, column=1, sticky="w", pady=px(5))

        flags = tk.Frame(tab, bg=PALETTE["surface"])
        flags.grid(row=7, column=0, columnspan=2, sticky="w", pady=(px(8), px(10)))
        tk.Checkbutton(flags, text="Bold", variable=self.message_flags["bold"], bg=PALETTE["surface"]).pack(side="left")
        tk.Checkbutton(flags, text="Italic", variable=self.message_flags["italic"], bg=PALETTE["surface"]).pack(side="left", padx=(px(15), 0))
        tk.Checkbutton(flags, text="Outline", variable=self.message_flags["outlineEnabled"], bg=PALETTE["surface"]).pack(side="left", padx=(px(15), 0))
        tk.Checkbutton(flags, text="Shadow", variable=self.message_flags["shadowEnabled"], bg=PALETTE["surface"]).pack(side="left", padx=(px(15), 0))

        effects = tk.LabelFrame(tab, text="Outline & shadow", bg=PALETTE["surface"], padx=px(10), pady=px(8))
        effects.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(px(4), px(12)))
        effects.grid_columnconfigure(1, weight=1)
        self._message_color_row(effects, "Outline color", "outlineColor", 0)
        self._message_number_row(effects, "Outline width", "outlineWidth", 1, 0, 10)
        self._message_color_row(effects, "Shadow color", "shadowColor", 2)
        self._message_number_row(effects, "Horizontal shadow", "shadowOffsetX", 3, -20, 20)
        self._message_number_row(effects, "Vertical shadow", "shadowOffsetY", 4, -20, 20)

        position = tk.LabelFrame(tab, text="Message text box", bg=PALETTE["surface"], padx=px(10), pady=px(8))
        position.grid(row=9, column=0, columnspan=2, sticky="ew")
        position.grid_columnconfigure(1, weight=1)
        for row, (label, key, minimum, maximum) in enumerate((
            ("Left (%)", "textBoxX", 0, 95),
            ("Top (%)", "textBoxY", 0, 95),
            ("Width (%)", "textBoxWidth", 5, 100),
            ("Height (%)", "textBoxHeight", 5, 100),
        )):
            self._message_number_row(position, label, key, row, minimum, maximum)

        alignment = tk.Frame(tab, bg=PALETTE["surface"])
        alignment.grid(row=10, column=0, columnspan=2, sticky="w", pady=(px(12), 0))
        tk.Label(alignment, text="Alignment:", bg=PALETTE["surface"], fg=PALETTE["text"]).pack(side="left")
        ttk.Combobox(
            alignment,
            state="readonly",
            values=("left", "center", "right"),
            textvariable=self.message_horizontal_alignment,
            width=10,
        ).pack(side="left", padx=(px(8), px(4)))
        ttk.Combobox(
            alignment,
            state="readonly",
            values=("top", "center", "bottom"),
            textvariable=self.message_vertical_alignment,
            width=10,
        ).pack(side="left")

    def _message_number_row(self, parent, label, key, row, minimum, maximum):
        tk.Label(parent, text=label, bg=PALETTE["surface"], fg=PALETTE["text"]).grid(
            row=row, column=0, sticky="w", padx=(0, px(18)), pady=px(4)
        )
        ttk.Spinbox(
            parent,
            from_=minimum,
            to=maximum,
            textvariable=self.message_values[key],
            width=10,
        ).grid(row=row, column=1, sticky="w", pady=px(4))

    def _message_color_row(self, parent, label, key, row):
        tk.Label(parent, text=label, bg=PALETTE["surface"], fg=PALETTE["text"]).grid(
            row=row, column=0, sticky="w", padx=(0, px(18)), pady=px(4)
        )
        holder = tk.Frame(parent, bg=PALETTE["surface"])
        holder.grid(row=row, column=1, sticky="ew", pady=px(4))
        holder.grid_columnconfigure(0, weight=1)
        style_entry(tk.Entry(holder, textvariable=self.message_values[key], font=ui_font(10))).grid(
            row=0, column=0, sticky="ew", ipady=px(3)
        )
        modern_button(
            holder,
            "Choose color…",
            lambda field=key: self._choose_message_color(field),
            color=PALETTE["neutral"],
            hover_color=PALETTE["neutral_hover"],
            icon="palette",
            compact=True,
        ).grid(row=0, column=1, padx=(px(6), 0))

    def _choose_message_color(self, key):
        _rgb, selected = colorchooser.askcolor(
            color=self.message_values[key].get(), parent=self, title="Choose color"
        )
        if selected:
            self.message_values[key].set(selected.upper())

    def _build_background_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tk.Label(
            tab,
            text="Live View background",
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=ui_font(12, "bold"),
        ).grid(row=0, column=0, sticky="w")
        choices = tk.Frame(tab, bg=PALETTE["surface"])
        choices.grid(row=1, column=0, sticky="w", pady=(px(10), px(14)))
        for caption, value in (
            ("Solid color", "solid"),
            ("Image", "image"),
            ("Video", "video"),
        ):
            tk.Radiobutton(
                choices,
                text=caption,
                value=value,
                variable=self.background_type,
                bg=PALETTE["surface"],
            ).pack(side="left", padx=(px(0), px(16)))

        image = tk.LabelFrame(tab, text="Image", bg=PALETTE["surface"], padx=px(10), pady=px(10))
        image.grid(row=2, column=0, sticky="ew", pady=(px(0), px(10)))
        action_button(image, "Choose image…", self._choose_background_image, "#2563eb").pack(
            side="left"
        )
        tk.Label(
            image,
            textvariable=self.background_image_file_name,
            bg=PALETTE["surface"],
            fg=PALETTE["subtle_text"],
        ).pack(side="left", padx=(px(12), px(0)))

        video = tk.LabelFrame(tab, text="Video", bg=PALETTE["surface"], padx=px(10), pady=px(10))
        video.grid(row=3, column=0, sticky="ew", pady=(px(0), px(10)))
        action_button(video, "Choose video…", self._choose_background_video, "#7655b5").pack(
            side="left"
        )
        tk.Label(
            video,
            textvariable=self.background_video_file_name,
            bg=PALETTE["surface"],
            fg=PALETTE["subtle_text"],
        ).pack(side="left", padx=(px(12), px(0)))

        action_button(
            tab, "Clear image and video", self._clear_background_media, "#b91c1c"
        ).grid(row=4, column=0, sticky="w")
        tk.Label(
            tab,
            text=(
                "Images are saved with the church style. Video files remain on this "
                "computer, so another computer must select its own local video file. "
                "MP4 is recommended."
            ),
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            justify="left",
            wraplength=px(650),
        ).grid(row=5, column=0, sticky="w", pady=(px(10), px(0)))

    def _choose_background_image(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Choose Live View background image",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.webp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            image.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=88, optimize=True)
        except (OSError, ValueError) as exc:
            self.status_var.set(f"Background image could not be opened: {exc}")
            return
        self.background_image_data = "data:image/jpeg;base64," + base64.b64encode(
            buffer.getvalue()
        ).decode("ascii")
        self.background_image_file_name.set(Path(path).name)
        self.background_type.set("image")
        self._decode_layout_background()
        self._draw_layout_editor()

    def _choose_background_video(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Choose Live View background video",
            filetypes=[
                ("Video files", "*.mp4 *.mov *.m4v *.avi *.webm *.mkv"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        if cv2 is None:
            self.status_var.set(
                "Video support is not installed. Install requirements.txt first."
            )
            return
        capture = cv2.VideoCapture(path)
        ok, _frame = capture.read()
        capture.release()
        if not ok:
            self.status_var.set("That video could not be decoded. Try an MP4 file.")
            return
        self.background_video_path = path
        self.background_video_file_name.set(Path(path).name)
        self.background_type.set("video")
        self._decode_layout_background()
        self._draw_layout_editor()

    def _clear_background_media(self):
        self.background_image_data = ""
        self.background_image_file_name.set("No image selected")
        self.background_video_path = ""
        self.background_video_file_name.set("No video selected")
        self.background_type.set("solid")
        self._layout_background_image = None
        self._layout_background_photo = None
        self.layout_canvas.itemconfigure(self.layout_background_id, state="hidden")

    def _background_type_changed(self, *_args):
        self._decode_layout_background()
        self._draw_layout_editor()

    def _decode_layout_background(self):
        self._layout_background_image = None
        kind = self.background_type.get().casefold()
        if kind == "image" and self.background_image_data and "," in self.background_image_data:
            try:
                data = base64.b64decode(self.background_image_data.split(",", 1)[1])
                self._layout_background_image = Image.open(BytesIO(data)).convert("RGB")
            except Exception:
                self.status_var.set("The saved background image could not be decoded.")
        elif kind == "video" and self.background_video_path and cv2 is not None:
            capture = cv2.VideoCapture(self.background_video_path)
            ok, frame = capture.read()
            capture.release()
            if ok:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                self._layout_background_image = Image.fromarray(frame)

    def _draw_layout_background(self, width, height):
        if (
            self.view_type != "live"
            or self._layout_background_image is None
            or self.background_type.get() == "solid"
        ):
            self.layout_canvas.itemconfigure(self.layout_background_id, state="hidden")
            return
        fitted = ImageOps.fit(
            self._layout_background_image,
            (max(1, int(width)), max(1, int(height))),
            method=Image.Resampling.LANCZOS,
        )
        self._layout_background_photo = ImageTk.PhotoImage(fitted)
        self.layout_canvas.coords(self.layout_background_id, 0, 0)
        self.layout_canvas.itemconfigure(
            self.layout_background_id,
            image=self._layout_background_photo,
            state="normal",
        )
        self.layout_canvas.tag_lower(self.layout_background_id)

    def _build_logo_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tk.Label(
            tab,
            text="Church logo overlay",
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=ui_font(12, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(
            tab,
            text=(
                "The selected image is resized and stored with the church display settings. "
                "Use the Preview & Position tab to drag it directly."
            ),
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            justify="left",
            wraplength=px(620),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(px(4), px(14)))
        actions = tk.Frame(tab, bg=PALETTE["surface"])
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(px(0), px(14)))
        action_button(actions, "Choose image…", self._choose_logo, "#2563eb").pack(side="left")
        action_button(actions, "Remove logo", self._remove_logo, "#b91c1c").pack(
            side="left", padx=(px(7), px(0))
        )
        tk.Label(
            actions,
            textvariable=self.logo_file_name,
            bg=PALETTE["surface"],
            fg=PALETTE["subtle_text"],
        ).pack(side="left", padx=(px(12), px(0)))

        presets = tk.LabelFrame(tab, text="Position presets", bg=PALETTE["surface"], padx=px(10), pady=px(10))
        presets.grid(row=3, column=0, columnspan=2, sticky="ew")
        for caption, preset in (
            ("Top Left", "top_left"),
            ("Top Right", "top_right"),
            ("Center", "center"),
            ("Bottom Left", "bottom_left"),
            ("Bottom Right", "bottom_right"),
        ):
            modern_button(
                presets,
                caption,
                lambda name=preset: self._apply_logo_preset(name),
                color=PALETTE["neutral"],
                hover_color=PALETTE["neutral_hover"],
                icon=f"position_{preset}",
                compact=True,
            ).pack(side="left", padx=(px(0), px(5)))

        fields = tk.Frame(tab, bg=PALETTE["surface"])
        fields.grid(row=4, column=0, columnspan=2, sticky="w", pady=(px(18), px(0)))
        for column, (label, key, minimum, maximum) in enumerate(
            (
                ("Left (%)", "logoX", 0, 95),
                ("Top (%)", "logoY", 0, 95),
                ("Width (%)", "logoWidth", 5, 100),
            )
        ):
            tk.Label(fields, text=label, bg=PALETTE["surface"]).grid(
                row=0, column=column, sticky="w", padx=(px(0), px(18))
            )
            ttk.Spinbox(
                fields,
                from_=minimum,
                to=maximum,
                textvariable=self.values[key],
                width=10,
            ).grid(row=1, column=column, sticky="w", padx=(px(0), px(18)), pady=(px(4), px(0)))

        for key in ("logoX", "logoY", "logoWidth"):
            self.values[key].trace_add("write", self._draw_layout_editor)
        self._decode_layout_logo()

    def _choose_logo(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Choose church logo",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.webp *.gif"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            image = Image.open(path).convert("RGBA")
            image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            image.save(buffer, format="PNG", optimize=True)
        except (OSError, ValueError) as exc:
            self.status_var.set(f"Logo could not be opened: {exc}")
            return
        self.logo_data = "data:image/png;base64," + base64.b64encode(
            buffer.getvalue()
        ).decode("ascii")
        self.logo_file_name.set(Path(path).name)
        self._decode_layout_logo()
        self._draw_layout_editor()

    def _remove_logo(self):
        self.logo_data = ""
        self.logo_file_name.set("No logo selected")
        self._layout_logo_image = None
        self._layout_logo_photo = None
        if hasattr(self, "layout_canvas"):
            self.layout_canvas.itemconfigure(self.layout_logo_id, state="hidden")

    def _decode_layout_logo(self):
        self._layout_logo_image = None
        if not self.logo_data or "," not in self.logo_data:
            return
        try:
            data = base64.b64decode(self.logo_data.split(",", 1)[1])
            self._layout_logo_image = Image.open(BytesIO(data)).convert("RGBA")
        except Exception:
            self.status_var.set("The saved church logo could not be decoded.")

    def _logo_height_percent(self):
        if self._layout_logo_image is None:
            return 0
        canvas_width = max(100, self.layout_canvas.winfo_width())
        canvas_height = max(100, self.layout_canvas.winfo_height())
        ratio = self._layout_logo_image.height / max(1, self._layout_logo_image.width)
        return float(self.values["logoWidth"].get()) * canvas_width / canvas_height * ratio

    def _draw_layout_logo(self, canvas_width, canvas_height):
        if self._layout_logo_image is None:
            self.layout_canvas.itemconfigure(self.layout_logo_id, state="hidden")
            self._layout_logo_bounds = None
            return
        try:
            logo_width_percent = float(self.values["logoWidth"].get())
            logo_x = float(self.values["logoX"].get())
            logo_y = float(self.values["logoY"].get())
        except ValueError:
            return
        target_width = max(12, round(canvas_width * logo_width_percent / 100))
        ratio = self._layout_logo_image.height / max(1, self._layout_logo_image.width)
        target_height = max(1, round(target_width * ratio))
        max_height = max(12, round(canvas_height * (100 - logo_y) / 100))
        if target_height > max_height:
            target_height = max_height
            target_width = max(1, round(target_height / ratio))
        preview = self._layout_logo_image.resize(
            (target_width, target_height), Image.Resampling.LANCZOS
        )
        self._layout_logo_photo = ImageTk.PhotoImage(preview)
        x = canvas_width * logo_x / 100
        y = canvas_height * logo_y / 100
        self.layout_canvas.coords(self.layout_logo_id, x, y)
        self.layout_canvas.itemconfigure(
            self.layout_logo_id, image=self._layout_logo_photo, state="normal"
        )
        self._layout_logo_bounds = (x, y, x + target_width, y + target_height)
        self.layout_canvas.tag_raise(self.layout_logo_id)

    def _apply_logo_preset(self, preset):
        try:
            width = float(self.values["logoWidth"].get())
            height = self._logo_height_percent()
        except ValueError:
            width, height = 20, 20
        positions = {
            "top_left": (5, 5),
            "top_right": (max(0, 95 - width), 5),
            "center": ((100 - width) / 2, (100 - height) / 2),
            "bottom_left": (5, max(0, 95 - height)),
            "bottom_right": (max(0, 95 - width), max(0, 95 - height)),
        }
        x, y = positions[preset]
        self.values["logoX"].set(str(int(round(x))))
        self.values["logoY"].set(str(int(round(y))))

    def _layout_settings(self):
        return {
            "textBoxX": float(self.values["textBoxX"].get()),
            "textBoxY": float(self.values["textBoxY"].get()),
            "textBoxWidth": float(self.values["textBoxWidth"].get()),
            "textBoxHeight": float(self.values["textBoxHeight"].get()),
            "textHorizontalAlign": self.text_horizontal_alignment.get(),
            "textVerticalAlign": self.text_vertical_alignment.get(),
        }

    def _upcoming_layout_settings(self):
        return {
            "textBoxX": float(self.values["upcomingBoxX"].get()),
            "textBoxY": float(self.values["upcomingBoxY"].get()),
            "textBoxWidth": float(self.values["upcomingBoxWidth"].get()),
            "textBoxHeight": float(self.values["upcomingBoxHeight"].get()),
            "textHorizontalAlign": "center",
            "textVerticalAlign": "center",
        }

    def _context_preview_color(self, style_key, fallback):
        try:
            value = self.context_style_values[style_key]["textColor"].get().strip()
            self.winfo_rgb(value)
            return value
        except (KeyError, tk.TclError):
            return fallback

    def _context_preview_font(self, style_key, scale=0.28):
        values = self.context_style_values.get(style_key, {})
        flags = self.context_style_flags.get(style_key, {})
        family = values.get("fontFamily").get() if values.get("fontFamily") else "Arial"
        try:
            size = max(7, min(34, round(float(values["fontSize"].get()) * scale)))
        except (KeyError, TypeError, ValueError):
            size = 12
        return (
            family or "Arial",
            size,
            "bold" if flags.get("bold") and flags["bold"].get() else "normal",
            "italic" if flags.get("italic") and flags["italic"].get() else "roman",
        )

    def _draw_message_preview(self):
        if not self.message_values:
            return
        width = max(100, self.layout_canvas.winfo_width())
        height = max(100, self.layout_canvas.winfo_height())
        try:
            layout = calculate_text_box_layout(
                width,
                height,
                {
                    "textBoxX": float(self.message_values["textBoxX"].get()),
                    "textBoxY": float(self.message_values["textBoxY"].get()),
                    "textBoxWidth": float(self.message_values["textBoxWidth"].get()),
                    "textBoxHeight": float(self.message_values["textBoxHeight"].get()),
                    "textHorizontalAlign": self.message_horizontal_alignment.get(),
                    "textVerticalAlign": self.message_vertical_alignment.get(),
                },
            )
        except (ValueError, KeyError):
            return

        def message_color(key, fallback):
            try:
                value = self.message_values[key].get().strip()
                self.winfo_rgb(value)
                return value
            except (KeyError, tk.TclError):
                return fallback

        self.layout_canvas.configure(bg=message_color("backgroundColor", "#000000"))
        self.layout_canvas.itemconfigure(self.layout_background_id, state="hidden")
        self.layout_canvas.itemconfigure(self.layout_logo_id, state="hidden")
        for item in (
            self.layout_song_title_id,
            self.layout_section_type_id,
            self.layout_upcoming_title_id,
            self.layout_upcoming_box_id,
            self.layout_upcoming_handle_id,
        ):
            self.layout_canvas.itemconfigure(item, state="hidden")
        for item in self.layout_upcoming_sample_ids:
            self.layout_canvas.itemconfigure(item, state="hidden")

        self.layout_canvas.coords(
            self.layout_box_id, layout.left, layout.top, layout.right, layout.bottom
        )
        self.layout_canvas.itemconfigure(
            self.layout_box_id, outline="#A855F7", state="normal"
        )
        self.layout_canvas.itemconfigure(self.layout_handle_id, state="hidden")

        try:
            preview_size = max(10, min(32, round(float(self.message_values["fontSize"].get()) * 0.28)))
            outline_width = max(0, int(self.message_values["outlineWidth"].get()))
            shadow_x = int(self.message_values["shadowOffsetX"].get())
            shadow_y = int(self.message_values["shadowOffsetY"].get())
        except ValueError:
            preview_size, outline_width, shadow_x, shadow_y = 18, 0, 0, 0
        preview_font = (
            self.message_values["fontFamily"].get() or "Arial",
            preview_size,
            "bold" if self.message_flags["bold"].get() else "normal",
            "italic" if self.message_flags["italic"].get() else "roman",
        )
        sample_text = apply_text_case(
            "Service begins in 10 minutes", self.message_values["textCase"].get()
        )
        common = {
            "text": sample_text,
            "font": preview_font,
            "justify": self.message_horizontal_alignment.get(),
            "width": max(20, layout.text_width),
            "anchor": layout.anchor,
        }
        preview_outline = max(1, round(outline_width * 0.35)) if outline_width else 0
        offsets = (
            (-preview_outline, 0), (preview_outline, 0),
            (0, -preview_outline), (0, preview_outline),
            (-preview_outline, -preview_outline), (-preview_outline, preview_outline),
            (preview_outline, -preview_outline), (preview_outline, preview_outline),
        )
        for item, (dx, dy) in zip(self.layout_outline_ids, offsets):
            self.layout_canvas.coords(item, layout.text_x + dx, layout.text_y + dy)
            self.layout_canvas.itemconfigure(
                item,
                **common,
                fill=message_color("outlineColor", "#000000"),
                state=(
                    "normal"
                    if self.message_flags["outlineEnabled"].get() and preview_outline
                    else "hidden"
                ),
            )
        self.layout_canvas.coords(
            self.layout_shadow_id,
            layout.text_x + round(shadow_x * 0.35),
            layout.text_y + round(shadow_y * 0.35),
        )
        self.layout_canvas.itemconfigure(
            self.layout_shadow_id,
            **common,
            fill=message_color("shadowColor", "#000000"),
            state="normal" if self.message_flags["shadowEnabled"].get() else "hidden",
        )
        self.layout_canvas.coords(self.layout_sample_id, layout.text_x, layout.text_y)
        self.layout_canvas.itemconfigure(
            self.layout_sample_id,
            **common,
            fill=message_color("textColor", "#FFFFFF"),
            state="normal",
        )
        self.alignment_caption.set("Custom message preview")

    def _draw_layout_editor(self, *_args):
        if not hasattr(self, "layout_canvas"):
            return
        if self.view_type == "stage" and self._message_preview_active:
            self._draw_message_preview()
            return
        try:
            settings = self._layout_settings()
            width = max(100, self.layout_canvas.winfo_width())
            height = max(100, self.layout_canvas.winfo_height())
            layout = calculate_text_box_layout(width, height, settings)
        except (ValueError, KeyError):
            return

        background = self._preview_color("backgroundColor", "#000000")
        text_color = self._preview_color("textColor", "#FFFFFF")
        outline_color = self._preview_color("outlineColor", "#000000")
        shadow_color = self._preview_color("shadowColor", "#000000")
        self.layout_canvas.configure(bg=background)
        self._draw_layout_background(width, height)
        self.layout_canvas.coords(
            self.layout_box_id, layout.left, layout.top, layout.right, layout.bottom
        )
        self.layout_canvas.itemconfigure(
            self.layout_box_id, outline="#38bdf8", state="normal"
        )
        self.layout_canvas.itemconfigure(self.layout_handle_id, state="normal")
        handle_size = 12
        self.layout_canvas.coords(
            self.layout_handle_id,
            layout.right - handle_size,
            layout.bottom - handle_size,
            layout.right + handle_size / 3,
            layout.bottom + handle_size / 3,
        )
        try:
            preview_size = max(
                10, min(28, round(float(self.values["fontSize"].get()) * 0.28))
            )
        except ValueError:
            preview_size = 16
        preview_font = (
            self.values["fontFamily"].get() or "Arial",
            preview_size,
            "bold" if self.flags["bold"].get() else "normal",
            "italic" if self.flags["italic"].get() else "roman",
        )
        sample_text = apply_text_case(
            "Sample song lyrics\ninside the text box",
            self.values["textCase"].get(),
        )
        common = {
            "text": sample_text,
            "font": preview_font,
            "justify": settings["textHorizontalAlign"],
            "width": max(20, layout.text_width),
            "anchor": layout.anchor,
        }
        try:
            outline_width = max(0, int(self.values["outlineWidth"].get()))
            shadow_x = int(self.values["shadowOffsetX"].get())
            shadow_y = int(self.values["shadowOffsetY"].get())
        except ValueError:
            outline_width, shadow_x, shadow_y = 0, 0, 0
        preview_outline = max(1, round(outline_width * 0.35)) if outline_width else 0
        offsets = (
            (-preview_outline, 0), (preview_outline, 0),
            (0, -preview_outline), (0, preview_outline),
            (-preview_outline, -preview_outline),
            (-preview_outline, preview_outline),
            (preview_outline, -preview_outline),
            (preview_outline, preview_outline),
        )
        for item, (dx, dy) in zip(self.layout_outline_ids, offsets):
            self.layout_canvas.coords(item, layout.text_x + dx, layout.text_y + dy)
            self.layout_canvas.itemconfigure(
                item,
                **common,
                fill=outline_color,
                state=(
                    "normal"
                    if self.flags["outlineEnabled"].get() and preview_outline
                    else "hidden"
                ),
            )
        self.layout_canvas.coords(
            self.layout_shadow_id,
            layout.text_x + round(shadow_x * 0.35),
            layout.text_y + round(shadow_y * 0.35),
        )
        self.layout_canvas.itemconfigure(
            self.layout_shadow_id,
            **common,
            fill=shadow_color,
            state="normal" if self.flags["shadowEnabled"].get() else "hidden",
        )
        self.layout_canvas.coords(self.layout_sample_id, layout.text_x, layout.text_y)
        self.layout_canvas.itemconfigure(
            self.layout_sample_id, **common, fill=text_color, state="normal"
        )

        if self.view_type == "stage":
            box_height = max(20, layout.bottom - layout.top)
            center_x = (layout.left + layout.right) / 2
            title_values = self.context_style_values["songTitleStyle"]
            section_values = self.context_style_values["sectionTypeStyle"]
            title_text = apply_text_case("Amazing Grace", title_values["textCase"].get())
            section_text = apply_text_case("Verse 1", section_values["textCase"].get())
            self.layout_canvas.coords(
                self.layout_song_title_id, center_x, layout.top + box_height * 0.07
            )
            self.layout_canvas.itemconfigure(
                self.layout_song_title_id,
                text=title_text,
                fill=self._context_preview_color("songTitleStyle", "#FFFFFF"),
                font=self._context_preview_font("songTitleStyle"),
                width=max(20, layout.right - layout.left),
                state=(
                    "normal"
                    if self.flags["showCurrentSongTitle"].get()
                    else "hidden"
                ),
            )
            self.layout_canvas.coords(
                self.layout_section_type_id, center_x, layout.top + box_height * 0.25
            )
            self.layout_canvas.itemconfigure(
                self.layout_section_type_id,
                text=section_text,
                fill=self._context_preview_color("sectionTypeStyle", "#D1D5DB"),
                font=self._context_preview_font("sectionTypeStyle"),
                width=max(20, layout.right - layout.left),
                state=(
                    "normal"
                    if self.flags["showSectionType"].get()
                    else "hidden"
                ),
            )
            # Keep the lyric sample below its context headings so the preview mirrors
            # the hosted Stage View's title -> section -> current lyrics hierarchy.
            lyric_y = layout.top + box_height * (0.50 if (
                self.flags["showCurrentSongTitle"].get() or self.flags["showSectionType"].get()
            ) else 0.30)
            stage_common = dict(common)
            stage_common.update(
                anchor="n", justify="center", width=max(20, layout.right - layout.left)
            )
            for item, (dx, dy) in zip(self.layout_outline_ids, offsets):
                self.layout_canvas.coords(item, center_x + dx, lyric_y + dy)
                self.layout_canvas.itemconfigure(
                    item,
                    **stage_common,
                    fill=outline_color,
                    state=(
                        "normal"
                        if self.flags["outlineEnabled"].get() and preview_outline
                        else "hidden"
                    ),
                )
            self.layout_canvas.coords(
                self.layout_shadow_id,
                center_x + round(shadow_x * 0.35),
                lyric_y + round(shadow_y * 0.35),
            )
            self.layout_canvas.itemconfigure(
                self.layout_shadow_id,
                **stage_common,
                fill=shadow_color,
                state="normal" if self.flags["shadowEnabled"].get() else "hidden",
            )
            self.layout_canvas.coords(self.layout_sample_id, center_x, lyric_y)
            self.layout_canvas.itemconfigure(
                self.layout_sample_id, **stage_common, fill=text_color, state="normal"
            )
        else:
            self.layout_canvas.itemconfigure(self.layout_song_title_id, state="hidden")
            self.layout_canvas.itemconfigure(self.layout_section_type_id, state="hidden")
            self.layout_canvas.itemconfigure(self.layout_upcoming_title_id, state="hidden")

        try:
            upcoming_count = max(0, min(5, int(self.next_slide_count.get())))
        except ValueError:
            upcoming_count = 0
        upcoming_visible = self.view_type == "stage" and upcoming_count > 0
        try:
            upcoming_layout = calculate_text_box_layout(
                width, height, self._upcoming_layout_settings()
            )
        except (ValueError, KeyError):
            upcoming_layout = None
        if upcoming_visible and upcoming_layout:
            self.layout_canvas.coords(
                self.layout_upcoming_box_id,
                upcoming_layout.left,
                upcoming_layout.top,
                upcoming_layout.right,
                upcoming_layout.bottom,
            )
            self.layout_canvas.coords(
                self.layout_upcoming_handle_id,
                upcoming_layout.right - handle_size,
                upcoming_layout.bottom - handle_size,
                upcoming_layout.right + handle_size / 3,
                upcoming_layout.bottom + handle_size / 3,
            )
            self.layout_canvas.itemconfigure(self.layout_upcoming_box_id, state="normal")
            self.layout_canvas.itemconfigure(self.layout_upcoming_handle_id, state="normal")
        else:
            self.layout_canvas.itemconfigure(self.layout_upcoming_box_id, state="hidden")
            self.layout_canvas.itemconfigure(self.layout_upcoming_handle_id, state="hidden")
        upcoming_color = self._preview_color("upcomingTextColor", "#A7B0BC")
        for index, item in enumerate(self.layout_upcoming_sample_ids):
            visible = upcoming_visible and upcoming_layout is not None and index < upcoming_count
            if upcoming_layout:
                upcoming_width = upcoming_layout.right - upcoming_layout.left
                upcoming_height = upcoming_layout.bottom - upcoming_layout.top
                slot_height = upcoming_height / max(1, upcoming_count)
                item_y = upcoming_layout.top + slot_height * (index + 0.5)
                if (
                    self.view_type == "stage"
                    and index == 0
                    and self.flags.get("showUpcomingSongTitle")
                    and self.flags["showUpcomingSongTitle"].get()
                ):
                    item_y += slot_height * 0.12
                self.layout_canvas.coords(
                    item, upcoming_layout.left + upcoming_width / 2, item_y
                )
            sample_label = (
                "First slide of next song"
                if self.view_type == "stage" and index == 0
                else f"Next slide {index + 1} lyrics"
            )
            self.layout_canvas.itemconfigure(
                item,
                text=apply_text_case(
                    sample_label,
                    self.values["textCase"].get(),
                ),
                fill=upcoming_color,
                font=(
                    self.values["fontFamily"].get() or "Arial",
                    max(7, round(preview_size * 0.52 * (0.82**index))),
                ),
                width=max(
                    20,
                    upcoming_layout.right - upcoming_layout.left
                    if upcoming_layout
                    else width * 0.9,
                ),
                state="normal" if visible else "hidden",
            )
        if self.view_type == "stage" and upcoming_visible and upcoming_layout:
            next_title_values = self.context_style_values["upcomingSongTitleStyle"]
            first_slot_height = (upcoming_layout.bottom - upcoming_layout.top) / max(1, upcoming_count)
            first_center_y = upcoming_layout.top + first_slot_height * 0.5
            show_next_title = self.flags["showUpcomingSongTitle"].get()
            self.layout_canvas.coords(
                self.layout_upcoming_title_id,
                (upcoming_layout.left + upcoming_layout.right) / 2,
                max(upcoming_layout.top + 2, first_center_y - first_slot_height * 0.32),
            )
            self.layout_canvas.itemconfigure(
                self.layout_upcoming_title_id,
                text=apply_text_case(
                    "Blessed Assurance", next_title_values["textCase"].get()
                ),
                fill=self._context_preview_color("upcomingSongTitleStyle", "#F8FAFC"),
                font=self._context_preview_font("upcomingSongTitleStyle"),
                width=max(20, upcoming_layout.right - upcoming_layout.left),
                state="normal" if show_next_title else "hidden",
            )
        else:
            self.layout_canvas.itemconfigure(self.layout_upcoming_title_id, state="hidden")

        if self.view_type == "live" and self._logo_preview_active:
            self._draw_layout_logo(width, height)
        else:
            self.layout_canvas.itemconfigure(self.layout_logo_id, state="hidden")
        self.layout_canvas.tag_raise(self.layout_handle_id)
        self.layout_canvas.tag_raise(self.layout_upcoming_handle_id)
        self.alignment_caption.set(
            f"Selected: {settings['textVerticalAlign'].title()}–"
            f"{settings['textHorizontalAlign'].title()}"
        )

    def _preview_color(self, key, fallback):
        value = self.values[key].get().strip()
        try:
            self.winfo_rgb(value)
        except tk.TclError:
            return fallback
        return value

    def _layout_press(self, event):
        try:
            settings = self._layout_settings()
            width = max(100, self.layout_canvas.winfo_width())
            height = max(100, self.layout_canvas.winfo_height())
            layout = calculate_text_box_layout(width, height, settings)
        except (ValueError, KeyError):
            return
        upcoming_layout = None
        try:
            upcoming_count = int(self.next_slide_count.get())
        except ValueError:
            upcoming_count = 0
        if self.view_type == "stage" and upcoming_count > 0:
            try:
                upcoming_layout = calculate_text_box_layout(
                    width, height, self._upcoming_layout_settings()
                )
            except (ValueError, KeyError):
                upcoming_layout = None
        if upcoming_layout and abs(event.x - upcoming_layout.right) <= 18 and abs(event.y - upcoming_layout.bottom) <= 18:
            mode = "upcoming_resize"
        elif upcoming_layout and (
            upcoming_layout.left <= event.x <= upcoming_layout.right
            and upcoming_layout.top <= event.y <= upcoming_layout.bottom
        ):
            mode = "upcoming_move"
        elif abs(event.x - layout.right) <= 18 and abs(event.y - layout.bottom) <= 18:
            mode = "resize"
        elif getattr(self, "_layout_logo_bounds", None) and (
            self._layout_logo_bounds[0] <= event.x <= self._layout_logo_bounds[2]
            and self._layout_logo_bounds[1] <= event.y <= self._layout_logo_bounds[3]
        ):
            mode = "logo_move"
        elif layout.left <= event.x <= layout.right and layout.top <= event.y <= layout.bottom:
            mode = "move"
        else:
            return
        self._layout_drag = {
            "mode": mode,
            "startX": event.x,
            "startY": event.y,
            "logoX": float(self.values["logoX"].get()),
            "logoY": float(self.values["logoY"].get()),
            "upcomingBoxX": float(self.values["upcomingBoxX"].get()),
            "upcomingBoxY": float(self.values["upcomingBoxY"].get()),
            "upcomingBoxWidth": float(self.values["upcomingBoxWidth"].get()),
            "upcomingBoxHeight": float(self.values["upcomingBoxHeight"].get()),
            **settings,
        }

    def _layout_motion(self, event):
        if not self._layout_drag:
            return
        canvas_width = max(100, self.layout_canvas.winfo_width())
        canvas_height = max(100, self.layout_canvas.winfo_height())
        dx = (event.x - self._layout_drag["startX"]) * 100 / canvas_width
        dy = (event.y - self._layout_drag["startY"]) * 100 / canvas_height
        if self._layout_drag["mode"] in {"upcoming_move", "upcoming_resize"}:
            left = self._layout_drag["upcomingBoxX"]
            top = self._layout_drag["upcomingBoxY"]
            width = self._layout_drag["upcomingBoxWidth"]
            height = self._layout_drag["upcomingBoxHeight"]
            if self._layout_drag["mode"] == "upcoming_move":
                left = max(0, min(100 - width, left + dx))
                top = max(0, min(100 - height, top + dy))
            else:
                width = max(5, min(100 - left, width + dx))
                height = max(5, min(100 - top, height + dy))
            self._set_upcoming_box_values(left, top, width, height)
            return
        if self._layout_drag["mode"] == "logo_move":
            try:
                logo_width = float(self.values["logoWidth"].get())
                logo_height = self._logo_height_percent()
            except ValueError:
                return
            self.values["logoX"].set(
                str(
                    int(
                        round(
                            max(
                                0,
                                min(
                                    100 - logo_width,
                                    self._layout_drag["logoX"] + dx,
                                ),
                            )
                        )
                    )
                )
            )
            self.values["logoY"].set(
                str(
                    int(
                        round(
                            max(
                                0,
                                min(
                                    100 - logo_height,
                                    self._layout_drag["logoY"] + dy,
                                ),
                            )
                        )
                    )
                )
            )
            return
        left = self._layout_drag["textBoxX"]
        top = self._layout_drag["textBoxY"]
        width = self._layout_drag["textBoxWidth"]
        height = self._layout_drag["textBoxHeight"]
        if self._layout_drag["mode"] == "move":
            left = max(0, min(100 - width, left + dx))
            top = max(0, min(100 - height, top + dy))
        else:
            width = max(5, min(100 - left, width + dx))
            height = max(5, min(100 - top, height + dy))
        self._set_box_values(left, top, width, height)

    def _layout_release(self, _event=None):
        self._layout_drag = None

    def _set_box_values(self, left, top, width, height):
        for key, value in (
            ("textBoxX", left),
            ("textBoxY", top),
            ("textBoxWidth", width),
            ("textBoxHeight", height),
        ):
            self.values[key].set(str(int(round(value))))

    def _set_upcoming_box_values(self, left, top, width, height):
        for key, value in (
            ("upcomingBoxX", left),
            ("upcomingBoxY", top),
            ("upcomingBoxWidth", width),
            ("upcomingBoxHeight", height),
        ):
            self.values[key].set(str(int(round(value))))

    def _apply_upcoming_box_preset(self, preset):
        try:
            width = max(5, min(100, float(self.values["upcomingBoxWidth"].get())))
            height = max(5, min(100, float(self.values["upcomingBoxHeight"].get())))
        except ValueError:
            width, height = 90, 26
        left = (100 - width) / 2
        top = {
            "top": 3,
            "center": (100 - height) / 2,
            "bottom": max(0, 97 - height),
        }[preset]
        self._set_upcoming_box_values(left, top, width, height)

    def _apply_box_preset(self, preset):
        try:
            width = max(5, min(100, float(self.values["textBoxWidth"].get())))
            height = max(5, min(100, float(self.values["textBoxHeight"].get())))
        except ValueError:
            width, height = 90, 50
        center_x = (100 - width) / 2
        center_y = (100 - height) / 2
        positions = {
            "top": (center_x, min(5, 100 - height)),
            "left": (min(5, 100 - width), center_y),
            "center": (center_x, center_y),
            "right": (max(0, 95 - width), center_y),
            "bottom": (center_x, max(0, 95 - height)),
        }
        left, top = positions[preset]
        self._set_box_values(left, top, width, height)

    def _set_text_alignment(self, horizontal, vertical):
        self.text_horizontal_alignment.set(horizontal)
        self.text_vertical_alignment.set(vertical)

    @staticmethod
    def _label(parent, text, row):
        tk.Label(parent, text=text, bg=PALETTE["surface"], fg=PALETTE["text"]).grid(
            row=row, column=0, sticky="w", padx=(px(0), px(18)), pady=px(5)
        )

    def _number_row(self, parent, label, key, row, minimum, maximum):
        self._label(parent, label, row)
        ttk.Spinbox(
            parent,
            from_=minimum,
            to=maximum,
            textvariable=self.values[key],
            width=12,
        ).grid(row=row, column=1, sticky="w", pady=px(5))

    def _color_row(self, parent, label, key, row):
        self._label(parent, label, row)
        holder = tk.Frame(parent, bg=PALETTE["surface"])
        holder.grid(row=row, column=1, sticky="ew", pady=px(5))
        holder.grid_columnconfigure(0, weight=1)
        style_entry(tk.Entry(holder, textvariable=self.values[key], font=ui_font(10))).grid(
            row=0, column=0, sticky="ew", ipady=px(3)
        )
        modern_button(
            holder,
            "Choose color…",
            lambda: self._choose_color(key),
            color="#536273",
            hover_color=PALETTE["neutral_hover"],
            icon="palette",
            compact=True,
        ).grid(row=0, column=1, padx=(px(6), px(0)))

    def _choose_color(self, key):
        _rgb, selected = colorchooser.askcolor(
            color=self.values[key].get(), parent=self, title="Choose color"
        )
        if selected:
            self.values[key].set(selected.upper())

    def save(self):
        church_id = self.controller.current_session["church"]["id"]
        try:
            box_x = int(self.values["textBoxX"].get())
            box_y = int(self.values["textBoxY"].get())
            box_width = int(self.values["textBoxWidth"].get())
            box_height = int(self.values["textBoxHeight"].get())
            horizontal_alignment = self.text_horizontal_alignment.get()
            context_styles = {}
            if self.view_type == "stage":
                for style_key, values in self.context_style_values.items():
                    context_styles[style_key] = {
                        **{key: variable.get() for key, variable in values.items()},
                        **{
                            key: variable.get()
                            for key, variable in self.context_style_flags[style_key].items()
                        },
                    }
            self.controller.db_service.save_display_settings(
                church_id,
                {
                    **{key: variable.get() for key, variable in self.values.items()},
                    **{key: variable.get() for key, variable in self.flags.items()},
                    **context_styles,
                    "textHorizontalAlign": horizontal_alignment,
                    "textVerticalAlign": self.text_vertical_alignment.get(),
                    "logoData": self.logo_data,
                    "logoFileName": (
                        "" if self.logo_file_name.get() == "No logo selected"
                        else self.logo_file_name.get()
                    ),
                    # Keep the legacy fields synchronized for older clients.
                    "alignment": horizontal_alignment,
                    "horizontalPosition": box_x + box_width // 2,
                    "verticalPosition": box_y + box_height // 2,
                    "textWidth": box_width,
                    "backgroundType": self.background_type.get(),
                    "backgroundImageData": self.background_image_data,
                    "backgroundImageFileName": (
                        ""
                        if self.background_image_file_name.get() == "No image selected"
                        else self.background_image_file_name.get()
                    ),
                    "backgroundVideoPath": self.background_video_path,
                    "backgroundVideoFileName": (
                        ""
                        if self.background_video_file_name.get() == "No video selected"
                        else self.background_video_file_name.get()
                    ),
                    "nextSlideCount": self.next_slide_count.get(),
                    "showNextSlide": int(self.next_slide_count.get()) > 0,
                },
                self.view_type,
            )
            if self.view_type == "stage" and self.message_settings is not None:
                message_box_x = int(self.message_values["textBoxX"].get())
                message_box_y = int(self.message_values["textBoxY"].get())
                message_box_width = int(self.message_values["textBoxWidth"].get())
                message_box_height = int(self.message_values["textBoxHeight"].get())
                message_horizontal = self.message_horizontal_alignment.get()
                self.controller.db_service.save_display_settings(
                    church_id,
                    {
                        **self.message_settings,
                        **{key: variable.get() for key, variable in self.message_values.items()},
                        **{key: variable.get() for key, variable in self.message_flags.items()},
                        "textHorizontalAlign": message_horizontal,
                        "textVerticalAlign": self.message_vertical_alignment.get(),
                        "alignment": message_horizontal,
                        "horizontalPosition": message_box_x + message_box_width // 2,
                        "verticalPosition": message_box_y + message_box_height // 2,
                        "textWidth": message_box_width,
                    },
                    "message",
                )
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        self.destroy()
        page = self.controller.pages["ControlCenterPage"]
        page.refresh()
        if self.view_type in {"stage", "message"}:
            self.controller.publish_stage_view_styles()
        page.set_status(
            f"{self.view_label} settings saved locally — Stage View style synchronized."
            if self.view_type in {"stage", "message"}
            else f"{self.view_label} settings saved locally — ready to sync."
        )


class OutputSettingsDialog(tk.Toplevel):
    """Show the permanent church outputs; Firebase configuration is app-managed."""

    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.title("Live View and Stage View")
        fit_toplevel(self, 720, 470, 620, 420)
        self.configure(bg=PALETTE["canvas"])
        self.transient(controller)
        self.grab_set()

        body = tk.Frame(self, bg=PALETTE["canvas"], padx=px(28), pady=px(24))
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)

        tk.Label(
            body,
            text="Live View and Firebase Stage View",
            font=ui_font(16, "bold"),
            bg=PALETTE["canvas"],
            fg=PALETTE["text"],
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(
            body,
            text=(
                "Stage Cue automatically uses the currently selected church ID as the "
                "permanent Firebase Stage View instance. No per-church Firebase setup or "
                "publisher-ID copy/paste is required."
            ),
            bg=PALETTE["canvas"],
            fg=PALETTE["muted"],
            justify="left",
            wraplength=px(650),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(px(4), px(18)))

        self.church_id = str(controller.current_session["church"]["id"])
        self.viewer_url = controller.stage_publisher.viewer_url(self.church_id)
        rows = (
            ("Church instance ID", self.church_id),
            ("Realtime Database", controller.stage_publisher.database_url or "Not configured"),
            ("Stage View URL", self.viewer_url or "Not configured"),
        )
        for row, (label, value) in enumerate(rows, start=2):
            tk.Label(
                body, text=label, bg=PALETTE["canvas"], fg=PALETTE["text"]
            ).grid(row=row, column=0, sticky="nw", padx=(0, px(12)), pady=px(6))
            tk.Label(
                body,
                text=value,
                bg=PALETTE["surface_muted"],
                fg=PALETTE["subtle_text"],
                anchor="w",
                justify="left",
                wraplength=px(470),
                padx=px(8),
                pady=px(6),
            ).grid(row=row, column=1, sticky="ew", pady=px(6))

        self.status = tk.StringVar(
            value=(
                "Firebase publishing is automatic. The first heartbeat creates/restores "
                "this computer's Firebase identity and registers it for the selected church."
            )
        )
        tk.Label(
            body,
            textvariable=self.status,
            bg=PALETTE["primary_soft"],
            fg=PALETTE["primary_soft_text"],
            justify="left",
            wraplength=px(650),
            padx=px(9),
            pady=px(7),
        ).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(px(14), 0))

        actions = tk.Frame(body, bg=PALETTE["canvas"])
        actions.grid(row=6, column=0, columnspan=2, sticky="e", pady=(px(18), 0))
        action_button(actions, "Open Live View", self._open_output, "#16835b").pack(
            side="left", padx=(0, px(7))
        )
        action_button(actions, "Copy Stage View URL", self._copy_viewer_url, "#536273").pack(
            side="left", padx=(0, px(7))
        )
        action_button(actions, "Sync Stage Style", self._sync_style, "#7655b5").pack(
            side="left", padx=(0, px(7))
        )
        action_button(actions, "Close", self.destroy, "#2563eb").pack(side="left")

    def _copy_viewer_url(self):
        if not self.viewer_url:
            self.status.set("Firebase Hosting is not configured in this Stage Cue build.")
            return
        self.clipboard_clear()
        self.clipboard_append(self.viewer_url)
        self.status.set("The permanent church Stage View URL was copied.")

    def _open_output(self):
        page = self.controller.pages["ControlCenterPage"]
        page.open_secondary_output()
        self.status.set(
            "Live View opened. A windowed Live View is used if no second monitor is found."
        )

    def _sync_style(self):
        self.controller.publish_stage_view_styles()
        self.status.set("Stage View lyric and custom-message styles queued for Firebase.")

