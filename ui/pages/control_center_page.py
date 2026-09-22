from __future__ import annotations

import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk
from typing import Any

from services.agenda_service import (
    build_agenda_document,
    load_agenda_file,
    save_agenda_file,
)
from services.bible_service import BOOKS, TRANSLATIONS, BiblePassage, BibleService
from services.presentation_service import (
    LyricSlide,
    build_segment_slides,
    has_segment_headers,
    next_matching_segment_slide,
    normalize_segments,
    segments_from_lyrics,
    segments_to_lyrics,
    strip_chords,
)
from services.presentation_import_service import (
    ImportedPresentationSlide,
    PresentationImportError,
    import_presentation,
)
from ui.presentation_canvas import PresentationCanvasRenderer
from ui.theme import (
    PALETTE,
    create_icon,
    icon_size,
    modern_button,
    mono_font,
    px,
    style_entry,
    style_text,
    ui_font,
)


def _elide(text: str, limit: int) -> str:
    """Shorten a label instead of letting a fixed widget width clip it."""

    text = str(text)
    return text if len(text) <= limit else f"{text[: max(1, limit - 1)]}…"


def fit_to_width(widget: tk.Widget, text: str, reserve: int = 0) -> str:
    """Trim ``text`` to the pixels the widget actually has, not a char count.

    A fixed character limit either clips on a narrow pane or wastes space on a
    wide one, and it changes meaning entirely once the font scales.
    """

    try:
        available = widget.winfo_width() - reserve
        if available <= 1:
            return text
        font = tkfont.Font(root=widget, font=widget.cget("font"))
    except (tk.TclError, RuntimeError):
        return text
    if font.measure(text) <= available:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if font.measure(text[:middle] + "…") <= available:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + "…"


class ControlCenterPage(tk.Frame):
    """Three-column song preparation and presentation workspace.

    Every padding here goes through ``px()`` so the whole layout grows and
    shrinks with the machine's scale setting.  Panel heights are *not* fixed;
    the panes are constrained from the widgets' own requested sizes in
    :meth:`_apply_pane_constraints`, which is what keeps the presentation
    controls on screen at 1366x768 as well as at 4K.
    """

    def __init__(self, parent, controller, auth_service, db_service):
        self.COLORS = {
            "app": PALETTE["canvas"],
            "toolbar": PALETTE["toolbar"],
            "panel": PALETTE["surface"],
            "panel_header": PALETTE["surface_subtle"],
            "border": PALETTE["border"],
            "text": PALETTE["text"],
            "muted": PALETTE["muted"],
            "accent": PALETTE["primary"],
            "success": PALETTE["success"],
            "danger": PALETTE["danger"],
            "purple": PALETTE["purple"],
            "neutral": PALETTE["neutral"],
            "warning": PALETTE["warning"],
        }
        super().__init__(parent, bg=self.COLORS["app"])
        self.controller = controller
        self.db = db_service
        self.songbooks: list[dict[str, Any]] = []
        self.book_by_id: dict[str, dict[str, Any]] = {}
        self.songs: list[dict[str, Any]] = []
        self.song_by_id: dict[str, dict[str, Any]] = {}
        self.service_items: list[dict[str, Any]] = []
        self._plan_church_id: str | None = None
        self.agenda_file_path: Path | None = None
        self.agenda_name = "Unsaved Agenda"
        self.agenda_dirty = False
        self.agenda_transition_var = tk.StringVar(value="Next item")
        self._pending_agenda_transition: tuple[int, int] | None = None

        self.active_song_id: str | None = None
        self.active_content_kind = "song"
        self.active_bible_item: dict[str, Any] | None = None
        self.active_presentation_item: dict[str, Any] | None = None
        self._presentation_cache: dict[str, list[ImportedPresentationSlide]] = {}
        self.editor_segments: list[dict[str, str]] = []
        self.editor_dirty = False
        self._suppress_editor_events = False

        self.slides: list[Any] = []
        self.preview_slide: Any | None = None
        self.live_slide: Any | None = None
        self.live_song_title = "No slide is live"
        self.live_mode = "READY"
        self.stage_mode = "READY"
        self.stage_message = ""
        self.presentation_active = False
        self.output_frozen = False
        self.text_hidden = False
        self.logo_visible = False
        self.live_display_settings: dict[str, Any] = {}
        self.stage_display_settings: dict[str, Any] = {}
        self.message_display_settings: dict[str, Any] = {}
        self.display_settings: dict[str, Any] = {}
        self.bible_service = BibleService()
        self.bible_passage: BiblePassage | None = None
        self.bible_loading = False

        self._build_header()
        self._build_toolbar()
        self._build_workspace()
        self._build_statusbar()
        self._bind_shortcuts()

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.after_idle(self._apply_pane_constraints)

    # ------------------------------------------------------------------
    # Window layout
    # ------------------------------------------------------------------

    def _build_header(self):
        header = tk.Frame(
            self, bg=PALETTE["navy"], padx=px(16), pady=px(8)
        )
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        brand = tk.Frame(header, bg=PALETTE["navy"])
        brand.grid(row=0, column=0, sticky="w")
        tk.Label(
            brand,
            text="STAGE",
            font=ui_font(15, "bold"),
            bg=PALETTE["navy"],
            fg="#FFFFFF",
        ).pack(side="left")
        tk.Label(
            brand,
            text=" CUE",
            font=ui_font(15, "bold"),
            bg=PALETTE["navy"],
            fg="#38BDF8",
        ).pack(side="left")
        tk.Label(
            brand,
            text=f"  v{getattr(self.controller, 'app_version', '0.2.6')}",
            font=ui_font(8, "bold"),
            bg=PALETTE["navy"],
            fg=PALETTE["navy_muted"],
        ).pack(side="left", pady=(px(5), 0))

        self.church_title_var = tk.StringVar(value="Control Center")
        tk.Label(
            header,
            textvariable=self.church_title_var,
            font=ui_font(12, "bold"),
            bg=PALETTE["navy"],
            fg=PALETTE["navy_text"],
            anchor="center",
        ).grid(row=0, column=1, sticky="ew", padx=px(12))

        # No fixed width here: a long address used to be clipped mid-word.
        self.account_var = tk.StringVar()
        tk.Label(
            header,
            textvariable=self.account_var,
            font=ui_font(9),
            bg=PALETTE["navy"],
            fg=PALETTE["navy_muted"],
            anchor="e",
        ).grid(row=0, column=2, sticky="e")

    def _build_toolbar(self):
        toolbar = tk.Frame(
            self,
            bg=self.COLORS["toolbar"],
            padx=px(12),
            pady=px(6),
            highlightthickness=1,
            highlightbackground=PALETTE["toolbar_border"],
        )
        toolbar.grid(row=1, column=0, sticky="ew")

        church = self._toolbar_group(toolbar, "CHURCH")
        self._button(
            church,
            "Switch Church",
            self.controller.switch_church,
            "neutral",
            True,
            "church",
        ).pack(side="left")

        self.admin_group = self._toolbar_group(toolbar, "ADMIN")
        self._button(
            self.admin_group,
            "Members",
            self.controller.open_admin_dialog,
            "success",
            True,
            "users",
        ).pack(side="left")
        self._button(
            self.admin_group,
            "Live Style",
            lambda: self.controller.open_display_settings("live"),
            "purple",
            True,
            "palette",
        ).pack(side="left", padx=(px(4), 0))
        self._button(
            self.admin_group,
            "Stage Style",
            lambda: self.controller.open_display_settings("stage"),
            "purple",
            True,
            "palette",
        ).pack(side="left", padx=(px(4), 0))

        self.output_group = self._toolbar_group(toolbar, "OUTPUT")
        self._button(
            self.output_group,
            "Output Setup",
            self.controller.open_output_settings,
            "accent",
            True,
            "monitor",
        ).pack(side="left")

        view = self._toolbar_group(toolbar, "THIS COMPUTER")
        self._button(
            view,
            "Display & Appearance",
            self.controller.open_appearance_settings,
            "accent",
            True,
            "display",
        ).pack(side="left")
        self._button(
            view,
            "Shortcuts",
            self.show_shortcuts,
            "neutral",
            True,
            "keyboard",
        ).pack(side="left", padx=(px(4), 0))
        self._button(
            view,
            "Check for Updates",
            lambda: self.controller.check_for_updates(manual=True),
            "neutral",
            True,
            "refresh",
        ).pack(side="left", padx=(px(4), 0))

        account = self._toolbar_group(toolbar, "ACCOUNT", side="right")
        self._button(
            account,
            "Log Out",
            self.controller.logout,
            "neutral",
            True,
            "logout",
        ).pack(side="left")

        # Remember where the admin group sits so hiding and re-showing it for a
        # non-admin member does not shuffle the toolbar order.
        self.admin_group._stagecue_pack["before"] = self.output_group

    def _build_workspace(self):
        gap = px(8)
        self.workspace = tk.PanedWindow(
            self,
            orient="horizontal",
            bg=PALETTE["sash"],
            bd=0,
            sashwidth=px(6),
            sashpad=0,
            opaqueresize=True,
        )
        self.workspace.grid(row=2, column=0, sticky="nsew", padx=gap, pady=gap)

        self.left_pane = tk.PanedWindow(
            self.workspace,
            orient="vertical",
            bg=PALETTE["sash"],
            bd=0,
            sashwidth=px(6),
            sashpad=0,
        )
        self.agenda_panel = self._panel(self.left_pane)
        self._build_agenda_panel(self.agenda_panel)
        self.left_pane.add(self.agenda_panel, minsize=px(150), height=px(210))
        self.library_panel = self._panel(self.left_pane)
        self._build_library_panel(self.library_panel)
        self.left_pane.add(self.library_panel, minsize=px(200))
        self.workspace.add(
            self.left_pane, minsize=px(250), width=px(296), stretch="never"
        )

        editor = self._panel(self.workspace)
        self._build_editor_panel(editor)
        self.workspace.add(editor, minsize=px(340), width=px(520), stretch="always")

        self.right_pane = tk.PanedWindow(
            self.workspace,
            orient="vertical",
            bg=PALETTE["sash"],
            bd=0,
            sashwidth=px(6),
            sashpad=0,
        )
        self.slides_panel = self._panel(self.right_pane)
        self._build_slides_panel(self.slides_panel)
        self.right_pane.add(self.slides_panel, minsize=px(240), height=px(300))
        self.preview_panel = self._panel(self.right_pane)
        self._build_preview_panel(self.preview_panel)
        self.right_pane.add(self.preview_panel, minsize=px(180))
        self.workspace.add(
            self.right_pane, minsize=px(320), width=px(404), stretch="always"
        )

    def _apply_pane_constraints(self):
        """Derive pane minimums from what the widgets actually need.

        The presentation rail and the agenda controls used to be cut off on
        shorter screens because the minimums were hard-coded for one font size.
        Asking the widgets how tall they are keeps them whole at any scale.
        """

        if not self.winfo_exists():
            return
        self.update_idletasks()

        def minimum(panel, extra=0):
            return max(px(120), panel.winfo_reqheight() + px(extra))

        try:
            agenda_minimum = minimum(self.agenda_panel)
            self.left_pane.paneconfigure(
                self.agenda_panel,
                minsize=min(agenda_minimum, px(260)),
            )
            self.left_pane.paneconfigure(self.library_panel, minsize=px(190))

            # The rail holds Present/Freeze/Hide/Logo plus the stage actions, so
            # this is the number that really decides whether anything clips.
            rail_minimum = (
                self.slide_rail.winfo_reqheight()
                + self.slides_header.winfo_reqheight()
                + px(20)
            )
            self.right_pane.paneconfigure(
                self.slides_panel,
                minsize=rail_minimum,
                height=max(rail_minimum, px(300)),
            )
            preview_minimum = (
                self.preview_header.winfo_reqheight()
                + self.preview_caption.winfo_reqheight()
                + px(120)
            )
            self.right_pane.paneconfigure(
                self.preview_panel, minsize=preview_minimum
            )
        except tk.TclError:
            return
        self._layout_preview_canvas()

    def _build_agenda_panel(self, panel):
        panel.grid_rowconfigure(1, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        header = self._section_header(panel, "SERVICE AGENDA", "Saved service plan")
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.agenda_file_var = tk.StringVar(value="Unsaved Agenda")
        tk.Label(
            header,
            textvariable=self.agenda_file_var,
            bg=self.COLORS["panel_header"],
            fg=self.COLORS["accent"],
            font=ui_font(8, "bold"),
            anchor="e",
        ).pack(side="right", padx=px(8))

        self.agenda_list = self._listbox(panel, font=ui_font(10, "bold"))
        self.agenda_list.grid(
            row=1, column=0, sticky="nsew", padx=(px(8), 0), pady=(px(6), px(4))
        )
        self.agenda_list.bind("<<ListboxSelect>>", self._on_agenda_selected)
        self.agenda_list.bind("<Delete>", lambda _event: self._shortcut(self.remove_from_service))
        self.agenda_list.bind(
            "<Control-Prior>", lambda _event: self._shortcut(lambda: self.move_service(-1))
        )
        self.agenda_list.bind(
            "<Control-Next>", lambda _event: self._shortcut(lambda: self.move_service(1))
        )
        scroll = ttk.Scrollbar(panel, orient="vertical", command=self.agenda_list.yview)
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, px(6)), pady=(px(6), px(4)))
        self.agenda_list.configure(yscrollcommand=scroll.set)

        transition = tk.Frame(panel, bg=self.COLORS["panel"], padx=px(8), pady=px(2))
        transition.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, px(2)))
        transition.grid_columnconfigure(1, weight=1)
        tk.Label(
            transition,
            text="After item",
            bg=self.COLORS["panel"],
            fg=self.COLORS["muted"],
            font=ui_font(8, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, px(6)))
        self.agenda_transition_combo = ttk.Combobox(
            transition,
            state="readonly",
            textvariable=self.agenda_transition_var,
            values=("Next item", "Hide text", "Show logo"),
            font=ui_font(9),
        )
        self.agenda_transition_combo.grid(row=0, column=1, sticky="ew")
        self.agenda_transition_combo.bind(
            "<<ComboboxSelected>>", self._agenda_transition_changed
        )
        self._button(
            transition, "Add PPT/PDF…", self.add_presentation_to_service, "purple", True, "plus"
        ).grid(row=0, column=2, padx=(px(6), 0))

        controls = tk.Frame(panel, bg=self.COLORS["panel"], padx=px(8), pady=px(6))
        controls.grid(row=3, column=0, columnspan=2, sticky="ew")
        self._button(controls, "Remove", self.remove_from_service, "danger", True).pack(side="left")
        self._button(controls, "Up", lambda: self.move_service(-1), "neutral", True).pack(
            side="left", padx=(px(3), 0)
        )
        self._button(controls, "Down", lambda: self.move_service(1), "neutral", True).pack(
            side="left", padx=(px(3), 0)
        )
        self._button(controls, "Clear", self.clear_service, "neutral", True, "clear").pack(
            side="left", padx=(px(3), 0)
        )
        self._separator(controls).pack(side="left", fill="y", padx=px(7), pady=px(2))
        self._button(controls, "Save As…", self.save_agenda_as, "purple", True, "save").pack(
            side="right"
        )
        self._button(controls, "Save", self.save_agenda, "success", True, "save").pack(
            side="right", padx=(0, px(3))
        )
        self._button(controls, "Load…", self.load_agenda, "accent", True, "folder").pack(
            side="right", padx=(0, px(3))
        )
        self._button(controls, "New", self.new_agenda, "neutral", True, "plus").pack(
            side="right", padx=(0, px(3))
        )

    def _build_library_panel(self, panel):
        panel.grid_rowconfigure(1, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        self._section_header(
            panel, "LIBRARY", "Songs and Bible passages"
        ).grid(row=0, column=0, sticky="ew")

        self.library_notebook = ttk.Notebook(panel)
        self.library_notebook.grid(row=1, column=0, sticky="nsew")

        songs_tab = tk.Frame(self.library_notebook, bg=self.COLORS["panel"])
        bible_tab = tk.Frame(self.library_notebook, bg=self.COLORS["panel"])
        self.library_notebook.add(songs_tab, text="Songs")
        self.library_notebook.add(bible_tab, text="Bible")
        self.library_notebook.bind("<<NotebookTabChanged>>", self._on_library_tab_changed)

        songs_tab.grid_rowconfigure(1, weight=1)
        songs_tab.grid_columnconfigure(0, weight=1)
        search = tk.Frame(songs_tab, bg=self.COLORS["panel"], padx=px(8), pady=px(6))
        search.grid(row=0, column=0, columnspan=2, sticky="ew")
        search.grid_columnconfigure(1, weight=1)
        search_icon = create_icon(search, "search", icon_size(15), self.COLORS["muted"])
        icon_label = tk.Label(search, image=search_icon, bg=self.COLORS["panel"])
        icon_label.grid(row=0, column=0, padx=(px(2), px(6)))
        icon_label._stagecue_icon = search_icon
        self.search_var = tk.StringVar()
        self.search_entry = style_entry(
            tk.Entry(search, textvariable=self.search_var, font=ui_font(10))
        )
        self.search_entry.grid(row=0, column=1, sticky="ew", ipady=px(4))
        self.search_entry.bind("<Escape>", lambda _event: self._clear_search())
        self.search_var.trace_add("write", lambda *_args: self._populate_library_tree())

        self.library_tree = ttk.Treeview(
            songs_tab,
            show="tree",
            selectmode="browse",
            style="StageCue.Treeview",
        )
        self.library_tree.grid(
            row=1, column=0, sticky="nsew", padx=(px(8), 0), pady=(0, px(4))
        )
        self.library_tree.column("#0", minwidth=px(180), width=px(260), stretch=True)
        self.library_tree.bind("<<TreeviewSelect>>", self._on_library_selected)
        self.library_tree.bind(
            "<Double-Button-1>",
            lambda _event: self.add_to_service() if self.selected_library_song_id() else None,
        )
        scroll = ttk.Scrollbar(songs_tab, orient="vertical", command=self.library_tree.yview)
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, px(6)), pady=(0, px(4)))
        self.library_tree.configure(yscrollcommand=scroll.set)

        controls = tk.Frame(songs_tab, bg=self.COLORS["panel"], padx=px(8), pady=px(6))
        controls.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._button(controls, "Book", self.add_songbook, "success", True, "plus").pack(side="left")
        self._button(controls, "Song", self.new_song, "success", True, "plus").pack(
            side="left", padx=(px(3), 0)
        )
        self._button(
            controls, "Import…", self.controller.open_import_dialog, "purple", True, "import"
        ).pack(side="left", padx=(px(3), 0))
        self._button(controls, "Delete", self.delete_selected, "danger", True, "trash").pack(
            side="left", padx=(px(3), 0)
        )
        self._button(controls, "Add →", self.add_to_service, "accent", True, "play").pack(side="right")

        admin = tk.Frame(songs_tab, bg=PALETTE["purple_soft"], padx=px(8), pady=px(5))
        admin.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.publish_controls = admin
        tk.Label(
            admin,
            text="ADMIN",
            bg=PALETTE["purple_soft"],
            fg=PALETTE["purple_soft_text"],
            font=ui_font(7, "bold"),
        ).pack(side="left", padx=(0, px(7)))
        self._button(admin, "Rename Book", self.edit_songbook, "neutral", True, "edit").pack(side="left")
        self._button(admin, "Publish Book", self.publish_songbook, "purple", True, "import").pack(
            side="left", padx=(px(3), 0)
        )

        self._build_bible_library_tab(bible_tab)

    def _build_bible_library_tab(self, tab):
        tab.grid_rowconfigure(3, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        top = tk.Frame(tab, bg=self.COLORS["panel"], padx=px(8), pady=px(6))
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        self.bible_reference_var = tk.StringVar(value="John 3:16")
        self.bible_reference_entry = style_entry(
            tk.Entry(top, textvariable=self.bible_reference_var, font=ui_font(10))
        )
        self.bible_reference_entry.grid(row=0, column=0, sticky="ew", ipady=px(4))
        self.bible_reference_entry.bind("<Return>", lambda _event: self.load_bible_reference())
        self._button(top, "Go", self.load_bible_reference, "accent", True, "search").grid(
            row=0, column=1, padx=(px(5), 0)
        )

        self.bible_translation_names = [name for name, _code in TRANSLATIONS]
        self.bible_translation_by_name = {name: code for name, code in TRANSLATIONS}
        self.bible_translation_var = tk.StringVar(value=self.bible_translation_names[0])
        translation = ttk.Combobox(
            top,
            state="readonly",
            values=self.bible_translation_names,
            textvariable=self.bible_translation_var,
            font=ui_font(8),
        )
        translation.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(px(5), 0))
        translation.bind("<<ComboboxSelected>>", self._bible_translation_changed)

        browse = tk.Frame(tab, bg=self.COLORS["panel"], padx=px(8), pady=px(2))
        browse.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, px(3)))
        browse.grid_columnconfigure(0, weight=1)
        browse.grid_columnconfigure(1, weight=0)
        self.bible_book_var = tk.StringVar(value=BOOKS[42][0])  # John
        self.bible_book_combo = ttk.Combobox(
            browse,
            state="readonly",
            values=[book for book, _chapters in BOOKS],
            textvariable=self.bible_book_var,
            font=ui_font(9),
        )
        self.bible_book_combo.grid(row=0, column=0, sticky="ew")
        self.bible_book_combo.bind("<<ComboboxSelected>>", self._bible_book_changed)
        self.bible_chapter_var = tk.StringVar(value="3")
        self.bible_chapter_combo = ttk.Combobox(
            browse,
            state="readonly",
            width=6,
            values=[str(value) for value in range(1, 22)],
            textvariable=self.bible_chapter_var,
            font=ui_font(9),
        )
        self.bible_chapter_combo.grid(row=0, column=1, padx=(px(5), 0))
        self.bible_chapter_combo.bind("<<ComboboxSelected>>", self._bible_chapter_changed)

        self.bible_status_var = tk.StringVar(value="Type a reference or choose a book and chapter.")
        tk.Label(
            tab,
            textvariable=self.bible_status_var,
            bg=self.COLORS["panel"],
            fg=self.COLORS["muted"],
            anchor="w",
            justify="left",
            wraplength=px(260),
            font=ui_font(8),
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=px(8), pady=(0, px(4)))

        self.bible_verse_list = self._listbox(tab, font=ui_font(8), selectmode="extended")
        self.bible_verse_list.grid(
            row=3, column=0, sticky="nsew", padx=(px(8), 0), pady=(0, px(4))
        )
        self.bible_verse_list.bind("<<ListboxSelect>>", self._on_bible_verse_selected)
        self.bible_verse_list.bind("<Double-Button-1>", lambda _event: self.add_bible_to_service())
        bible_scroll = ttk.Scrollbar(tab, orient="vertical", command=self.bible_verse_list.yview)
        bible_scroll.grid(row=3, column=1, sticky="ns", padx=(0, px(6)), pady=(0, px(4)))
        self.bible_verse_list.configure(yscrollcommand=bible_scroll.set)

        bible_actions = tk.Frame(tab, bg=self.COLORS["panel"], padx=px(8), pady=px(6))
        bible_actions.grid(row=4, column=0, columnspan=2, sticky="ew")
        self._button(
            bible_actions, "Load chapter", self._load_selected_bible_chapter, "neutral", True, "folder"
        ).pack(side="left")
        self._button(
            bible_actions, "Add passage →", self.add_bible_to_service, "accent", True, "play"
        ).pack(side="right")

    def _on_library_tab_changed(self, _event=None):
        try:
            selected = self.library_notebook.tab(self.library_notebook.select(), "text")
        except tk.TclError:
            return
        if selected == "Bible" and self.bible_passage is None and not self.bible_loading:
            self.load_bible_reference()

    def _build_editor_panel(self, panel):
        self.song_editor_panel = panel
        panel.grid_rowconfigure(3, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        header = self._section_header(panel, "SONG EDITOR", "Song details and lyrics")
        header.grid(row=0, column=0, sticky="ew")
        self.editor_state_var = tk.StringVar(value="NO SONG SELECTED")
        tk.Label(
            header,
            textvariable=self.editor_state_var,
            bg=self.COLORS["panel_header"],
            fg=self.COLORS["accent"],
            font=ui_font(8, "bold"),
        ).pack(side="right", padx=px(9))

        details = tk.Frame(panel, bg=self.COLORS["panel"], padx=px(10), pady=px(8))
        details.grid(row=1, column=0, sticky="ew")
        details.grid_columnconfigure(0, weight=3, uniform="detail")
        details.grid_columnconfigure(1, weight=2, uniform="detail")
        details.grid_columnconfigure(2, weight=2, uniform="detail")
        self.title_entry, self.title_var = self._labeled_entry(details, "Title", 0, 0)
        self.author_entry, self.author_var = self._labeled_entry(details, "Author", 0, 1)
        self.ccli_entry, self.ccli_var = self._labeled_entry(details, "CCLI number", 0, 2)
        self.copyright_entry, self.copyright_var = self._labeled_entry(
            details, "Copyright / attribution", 2, 0, columnspan=2
        )
        tk.Label(
            details,
            text="Songbook",
            bg=self.COLORS["panel"],
            fg=self.COLORS["text"],
            font=ui_font(9),
        ).grid(row=2, column=2, sticky="w", padx=(px(6), 0))
        self.songbook_combo = ttk.Combobox(details, state="readonly", font=ui_font(10))
        self.songbook_combo.grid(
            row=3, column=2, sticky="ew", padx=(px(6), 0), pady=(px(2), px(5))
        )
        self.songbook_combo.bind("<<ComboboxSelected>>", lambda _event: self._mark_editor_dirty())

        lyrics_header = tk.Frame(
            panel, bg=PALETTE["surface_muted"], padx=px(10), pady=px(6)
        )
        lyrics_header.grid(row=2, column=0, sticky="ew")
        tk.Label(
            lyrics_header,
            text="LYRICS & CHORDS",
            bg=PALETTE["surface_muted"],
            fg=self.COLORS["text"],
            font=ui_font(9, "bold"),
        ).pack(side="left")
        tk.Label(
            lyrics_header,
            text="Tags such as [Verse 1], [Chorus], and [Bridge] are auto-detected.",
            bg=PALETTE["surface_muted"],
            fg=self.COLORS["muted"],
            font=ui_font(8),
        ).pack(side="left", padx=(px(10), 0))

        lyrics_area = tk.Frame(panel, bg=self.COLORS["panel"])
        lyrics_area.grid(row=3, column=0, sticky="nsew", padx=px(10), pady=(px(8), px(4)))
        lyrics_area.grid_rowconfigure(0, weight=1)
        lyrics_area.grid_columnconfigure(0, weight=1)
        self.lyrics_editor = style_text(
            tk.Text(
                lyrics_area,
                wrap="word",
                undo=True,
                font=mono_font(11),
                padx=px(10),
                pady=px(8),
            )
        )
        self.lyrics_editor.grid(row=0, column=0, sticky="nsew")
        self.lyrics_editor.bind("<<Modified>>", self._lyrics_modified)
        self.lyrics_editor.bind("<Control-a>", self._select_all_lyrics, add="+")
        self.lyrics_editor.bind("<Control-A>", self._select_all_lyrics, add="+")
        try:
            self.lyrics_editor.bind("<Command-a>", self._select_all_lyrics, add="+")
        except tk.TclError:
            # The Command modifier is only exposed by Tk on macOS.
            pass
        lyrics_scroll = ttk.Scrollbar(
            lyrics_area, orient="vertical", command=self.lyrics_editor.yview
        )
        lyrics_scroll.grid(row=0, column=1, sticky="ns")
        self.lyrics_editor.configure(yscrollcommand=lyrics_scroll.set)

        footer = tk.Frame(panel, bg=self.COLORS["panel"], padx=px(10), pady=px(6))
        footer.grid(row=4, column=0, sticky="ew")
        self.editor_hint_var = tk.StringVar(
            value="Segments update automatically from tags."
        )
        tk.Label(
            footer,
            textvariable=self.editor_hint_var,
            bg=self.COLORS["panel"],
            fg=self.COLORS["muted"],
            font=ui_font(8),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self._button(
            footer, "Discard Changes", self.discard_editor_changes, "neutral", True, "clear"
        ).pack(side="right")
        self._button(
            footer, "Save Song  Ctrl+S", self.save_editor, "success", True, "save"
        ).pack(side="right", padx=(0, px(5)))

        for variable in (self.title_var, self.author_var, self.copyright_var, self.ccli_var):
            variable.trace_add("write", lambda *_args: self._mark_editor_dirty())
        self._set_editor_enabled(False)

    def _build_slides_panel(self, panel):
        panel.grid_rowconfigure(1, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        header = self._section_header(panel, "SLIDE SELECTION", "Cues from song segments")
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.slides_header = header
        self.slide_rule_var = tk.StringVar(value="4 lines per slide")
        tk.Label(
            header,
            textvariable=self.slide_rule_var,
            bg=self.COLORS["panel_header"],
            fg=self.COLORS["muted"],
            font=ui_font(8),
        ).pack(side="right", padx=px(9))

        self.slide_list = self._listbox(panel, font=ui_font(9))
        self.slide_list.grid(row=1, column=0, sticky="nsew", padx=(px(8), 0), pady=px(8))
        self._slide_labels: list[str] = []
        self._slide_label_width: int | None = None
        self.slide_list.bind("<Configure>", self._refit_slide_labels, add="+")
        self.slide_list.bind("<<ListboxSelect>>", self._on_slide_selected)
        self.slide_list.bind("<Down>", lambda _event: self._shortcut(self.next_live))
        self.slide_list.bind("<Next>", lambda _event: self._shortcut(self.next_live))
        self.slide_list.bind("<Up>", lambda _event: self._shortcut(self.previous_live))
        self.slide_list.bind("<Prior>", lambda _event: self._shortcut(self.previous_live))
        self.slide_list.bind("<Home>", lambda _event: self._shortcut(self.restart_song))
        self.slide_list.bind("<plus>", lambda _event: self._shortcut(lambda: self.jump_agenda(1)))
        self.slide_list.bind("<minus>", lambda _event: self._shortcut(lambda: self.jump_agenda(-1)))
        self.slide_list.bind("<KP_Add>", lambda _event: self._shortcut(lambda: self.jump_agenda(1)))
        self.slide_list.bind("<KP_Subtract>", lambda _event: self._shortcut(lambda: self.jump_agenda(-1)))
        scroll = ttk.Scrollbar(panel, orient="vertical", command=self.slide_list.yview)
        scroll.grid(row=1, column=1, sticky="ns", pady=px(8))
        self.slide_list.configure(yscrollcommand=scroll.set)

        rail = tk.Frame(
            panel,
            bg=PALETTE["rail"],
            padx=px(5),
            pady=px(5),
            highlightthickness=1,
            highlightbackground=PALETTE["border_strong"],
        )
        rail.grid(row=1, column=2, sticky="new", padx=(px(6), px(8)), pady=px(8))
        self.slide_rail = rail

        live = self._control_card(rail, "LIVE", "Preview + output", PALETTE["teal"])
        live.pack(fill="x")
        body = tk.Frame(live, bg=self.COLORS["panel"], padx=px(5), pady=px(5))
        body.pack(fill="x")
        self.present_button = self._button(
            body, "Start Presenting   Ctrl+P", self.toggle_present_mode, "neutral", True, "play"
        )
        self.present_button.pack(fill="x")

        freeze_hide = tk.Frame(body, bg=self.COLORS["panel"])
        freeze_hide.pack(fill="x", pady=(px(3), 0))
        self.freeze_button = self._button(
            freeze_hide, "Freeze   Ctrl+R", self.toggle_freeze, "neutral", True, "freeze"
        )
        self.freeze_button.pack(side="left", fill="x", expand=True)
        self.hide_text_button = self._button(
            freeze_hide, "Hide Text   Ctrl+T", self.toggle_text_hidden, "neutral", True, "hide"
        )
        self.hide_text_button.pack(side="left", fill="x", expand=True, padx=(px(3), 0))

        self.logo_button = self._button(
            body, "Show Logo   Ctrl+Q", self.toggle_logo, "purple", True, "logo"
        )
        self.logo_button.pack(fill="x", pady=(px(3), 0))

        self.present_state_var = tk.StringVar(value="● PREVIEW ONLY")
        self.present_state_label = tk.Label(
            body,
            textvariable=self.present_state_var,
            bg=PALETTE["badge_idle_bg"],
            fg=PALETTE["badge_idle_fg"],
            font=ui_font(7, "bold"),
            padx=px(5),
            pady=px(3),
        )
        self.present_state_label.pack(fill="x", pady=(px(4), 0))

        stage = self._control_card(rail, "STAGE", "Remote operator", PALETTE["purple"])
        stage.pack(fill="x", pady=(px(5), 0))
        stage_body = tk.Frame(stage, bg=self.COLORS["panel"], padx=px(5), pady=px(5))
        stage_body.pack(fill="x")
        stage_actions = tk.Frame(stage_body, bg=self.COLORS["panel"])
        stage_actions.pack(fill="x")
        self._button(
            stage_actions, "Clear Stage View", self.clear_stage_view, "danger", True, "clear"
        ).pack(side="left", fill="x", expand=True)
        self._button(
            stage_actions, "Send Custom Message", self.prompt_stage_message, "purple", True, "message"
        ).pack(side="left", fill="x", expand=True, padx=(px(3), 0))
        self.stage_state_var = tk.StringVar(value="Stage: ready")
        tk.Label(
            stage_body,
            textvariable=self.stage_state_var,
            bg=PALETTE["purple_soft"],
            fg=PALETTE["purple_soft_text"],
            anchor="w",
            padx=px(5),
            pady=px(3),
            font=ui_font(7, "bold"),
        ).pack(fill="x", pady=(px(4), 0))

    def _build_preview_panel(self, panel):
        panel.grid_rowconfigure(1, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        header = self._section_header(panel, "PREVIEW", "Selected slide")
        header.grid(row=0, column=0, sticky="ew")
        self.preview_header = header
        self.output_mode_var = tk.StringVar(value="● READY")
        tk.Label(
            header,
            textvariable=self.output_mode_var,
            bg=self.COLORS["panel_header"],
            fg=self.COLORS["accent"],
            font=ui_font(8, "bold"),
        ).pack(side="right", padx=px(9))

        # The matte used to carry its own 8px inset on top of the grid padding,
        # which wasted a visible band of dead space around the 16:9 canvas.
        canvas_frame = tk.Frame(panel, bg=PALETTE["matte"])
        canvas_frame.grid(row=1, column=0, sticky="nsew", padx=px(8), pady=(px(8), px(4)))
        self.preview_canvas_frame = canvas_frame
        self.preview_canvas = tk.Canvas(
            canvas_frame,
            bg="#000000",
            highlightthickness=1,
            highlightbackground=PALETTE["border_strong"],
            bd=0,
        )
        self.preview_canvas.place(relx=0.5, rely=0.5, anchor="center")
        self.preview_renderer = PresentationCanvasRenderer(self.preview_canvas)
        canvas_frame.bind("<Configure>", self._layout_preview_canvas)

        self.preview_caption_var = tk.StringVar(value="Select a slide to preview it")
        self.preview_caption = tk.Label(
            panel,
            textvariable=self.preview_caption_var,
            bg=self.COLORS["panel"],
            fg=self.COLORS["muted"],
            anchor="w",
            padx=px(10),
            pady=px(4),
            font=ui_font(8),
        )
        self.preview_caption.grid(row=2, column=0, sticky="ew")
        self.after_idle(self._layout_preview_canvas)

    def _build_statusbar(self):
        status = tk.Frame(self, bg=PALETTE["statusbar"], padx=px(12), pady=px(4))
        status.grid(row=3, column=0, sticky="ew")
        status.grid_columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Ready for offline editing")
        tk.Label(
            status,
            textvariable=self.status_var,
            bg=PALETTE["statusbar"],
            fg=PALETTE["statusbar_text"],
            anchor="w",
            font=ui_font(9),
        ).grid(row=0, column=0, sticky="ew")
        tk.Label(
            status,
            text="LOCAL-FIRST",
            bg=PALETTE["statusbar"],
            fg=PALETTE["statusbar_accent"],
            font=ui_font(8, "bold"),
        ).grid(row=0, column=1, sticky="e", padx=(px(10), 0))

    # ------------------------------------------------------------------
    # State handover across a theme or scale change
    # ------------------------------------------------------------------

    def capture_state(self) -> dict[str, Any]:
        """Snapshot everything a rebuild would otherwise throw away."""

        state: dict[str, Any] = {
            "agenda_file_path": self.agenda_file_path,
            "agenda_name": self.agenda_name,
            "agenda_dirty": self.agenda_dirty,
            "service_items": [
                self._serialize_service_item(item) for item in self.service_items
            ],
            "active_song_id": self.active_song_id,
            "active_content_kind": self.active_content_kind,
            "active_bible_item": (
                self._serialize_service_item(self.active_bible_item)
                if self.active_bible_item
                else None
            ),
            "editor_dirty": self.editor_dirty,
            "search": self.search_var.get() if hasattr(self, "search_var") else "",
            "slide_index": None,
            "lyrics": None,
            "fields": {},
        }
        if self.editor_dirty and hasattr(self, "lyrics_editor"):
            state["lyrics"] = self.lyrics_editor.get("1.0", "end-1c")
            state["fields"] = {
                "title": self.title_var.get(),
                "author": self.author_var.get(),
                "copyright": self.copyright_var.get(),
                "ccli": self.ccli_var.get(),
            }
        if hasattr(self, "slide_list"):
            selection = self.slide_list.curselection()
            if selection:
                state["slide_index"] = selection[0]
        return state

    def restore_state(self, state: dict[str, Any]) -> None:
        if not state:
            return
        if state.get("search"):
            self.search_var.set(state["search"])
        self.agenda_file_path = state.get("agenda_file_path")
        self.agenda_name = state.get("agenda_name", "Unsaved Agenda")
        self.agenda_dirty = bool(state.get("agenda_dirty"))
        service_state = state.get("service_items") or []
        if service_state:
            self.service_items = [
                item
                for raw_item in service_state
                if (item := self._deserialize_service_item(raw_item)) is not None
            ]
            self._render_service_plan()
        self._update_agenda_file_status()

        song_id = state.get("active_song_id")
        if song_id and song_id in self.song_by_id:
            self._load_song(song_id)
            if state.get("lyrics") is not None:
                self._suppress_editor_events = True
                try:
                    self.lyrics_editor.delete("1.0", "end")
                    self.lyrics_editor.insert("1.0", state["lyrics"])
                    fields = state.get("fields") or {}
                    self.title_var.set(fields.get("title", self.title_var.get()))
                    self.author_var.set(fields.get("author", self.author_var.get()))
                    self.copyright_var.set(fields.get("copyright", self.copyright_var.get()))
                    self.ccli_var.set(fields.get("ccli", self.ccli_var.get()))
                finally:
                    self._suppress_editor_events = False
                self._mark_editor_dirty()
        if state.get("active_content_kind") == "bible" and state.get("active_bible_item"):
            bible_item = self._deserialize_service_item(state["active_bible_item"])
            if bible_item:
                self._load_bible_item(bible_item)
        index = state.get("slide_index")
        if index is not None and 0 <= index < self.slide_list.size():
            self._select_preview_slide(index)

    # ------------------------------------------------------------------
    # Shared widget helpers
    # ------------------------------------------------------------------

    def _toolbar_group(self, parent, label, side="left"):
        outer = tk.Frame(parent, bg=self.COLORS["toolbar"])
        options = {
            "side": side,
            "padx": (0, px(12)) if side == "left" else (px(12), 0),
        }
        outer._stagecue_pack = options
        outer.pack(**options)
        tk.Label(
            outer,
            text=label,
            bg=self.COLORS["toolbar"],
            fg=PALETTE["toolbar_label"],
            font=ui_font(7, "bold"),
        ).pack(anchor="w", pady=(0, px(2)))
        return outer

    def _show_toolbar_group(self, group, visible):
        if visible:
            if not group.winfo_manager():
                options = dict(group._stagecue_pack)
                before = options.pop("before", None)
                if before is not None and before.winfo_exists():
                    options["before"] = before
                group.pack(**options)
        else:
            group.pack_forget()

    def _separator(self, parent, vertical=True):
        if vertical:
            return tk.Frame(parent, bg=PALETTE["border"], width=1)
        return tk.Frame(parent, bg=PALETTE["border"], height=1)

    def _control_card(self, parent, title, subtitle, accent):
        card = tk.Frame(
            parent,
            bg=self.COLORS["panel"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
        )
        heading = tk.Frame(card, bg=accent, padx=px(8), pady=px(3))
        heading.pack(fill="x")
        tk.Label(
            heading,
            text=title,
            bg=accent,
            fg="#FFFFFF",
            font=ui_font(8, "bold"),
        ).pack(side="left")
        if subtitle:
            tk.Label(
                heading,
                text=subtitle,
                bg=accent,
                fg=PALETTE["heading_subtitle"],
                font=ui_font(7),
            ).pack(side="right", padx=(px(8), 0))
        return card

    def _panel(self, parent, bg=None):
        return tk.Frame(
            parent,
            bg=bg or self.COLORS["panel"],
            highlightthickness=1,
            highlightbackground=self.COLORS["border"],
        )

    def _section_header(self, parent, title, subtitle):
        """A compact panel heading that sizes itself instead of reserving 52px."""

        frame = tk.Frame(parent, bg=self.COLORS["panel_header"], highlightthickness=0)
        tk.Frame(frame, bg=self.COLORS["accent"], width=px(3)).pack(side="left", fill="y")
        icon_name = {
            "SERVICE AGENDA": "agenda",
            "SONGBOOKS": "music",
            "SONG EDITOR": "edit",
            "SLIDE SELECTION": "slides",
            "PREVIEW": "monitor",
        }.get(title, "slides")
        icon = create_icon(frame, icon_name, icon_size(16), self.COLORS["accent"])
        icon_label = tk.Label(frame, image=icon, bg=self.COLORS["panel_header"])
        icon_label.pack(side="left", padx=(px(9), px(7)))
        icon_label._stagecue_icon = icon
        copy = tk.Frame(frame, bg=self.COLORS["panel_header"])
        copy.pack(side="left", fill="y", pady=px(6))
        tk.Label(
            copy,
            text=title,
            bg=self.COLORS["panel_header"],
            fg=self.COLORS["text"],
            font=ui_font(9, "bold"),
        ).pack(anchor="w")
        tk.Label(
            copy,
            text=subtitle,
            bg=self.COLORS["panel_header"],
            fg=self.COLORS["muted"],
            font=ui_font(7),
        ).pack(anchor="w")
        tk.Frame(frame, bg=PALETTE["divider"], height=1).pack(side="bottom", fill="x")
        return frame

    def _button(
        self,
        parent,
        text,
        command,
        kind="neutral",
        compact=False,
        icon=None,
    ):
        color = self.COLORS.get(kind, kind)
        hover = {
            self.COLORS["accent"]: PALETTE["primary_hover"],
            self.COLORS["success"]: PALETTE["success_hover"],
            self.COLORS["danger"]: PALETTE["danger_hover"],
            self.COLORS["purple"]: PALETTE["purple_hover"],
            self.COLORS["neutral"]: PALETTE["neutral_hover"],
        }.get(color, color)
        return modern_button(
            parent,
            text,
            command,
            color=color,
            hover_color=hover,
            icon=icon,
            compact=compact,
        )

    def _listbox(self, parent, font=None, **kwargs):
        return tk.Listbox(
            parent,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=self.COLORS["border"],
            highlightcolor=self.COLORS["accent"],
            font=font or ui_font(10),
            bg=PALETTE["list_bg"],
            fg=self.COLORS["text"],
            selectbackground=PALETTE["select_bg"],
            selectforeground=PALETTE["select_fg"],
            activestyle="none",
            exportselection=False,
            **kwargs,
        )

    def _labeled_entry(self, parent, label, row, column, columnspan=1):
        tk.Label(
            parent,
            text=label,
            bg=self.COLORS["panel"],
            fg=self.COLORS["text"],
            font=ui_font(9),
        ).grid(row=row, column=column, columnspan=columnspan, sticky="w", padx=(0, px(6)))
        variable = tk.StringVar()
        entry = style_entry(
            tk.Entry(parent, textvariable=variable, font=ui_font(10))
        )
        entry.grid(
            row=row + 1,
            column=column,
            columnspan=columnspan,
            sticky="ew",
            padx=(0, px(6)),
            pady=(px(2), px(5)),
            ipady=px(3),
        )
        return entry, variable

    # ------------------------------------------------------------------
    # Session, refresh, and library tree
    # ------------------------------------------------------------------

    def on_show(self):
        self.refresh()

    def church_id(self):
        session = self.controller.current_session
        return session["church"]["id"] if session else None

    def refresh(self):
        session = self.controller.current_session
        if not session:
            return
        church = session["church"]
        user = session["user"]
        role = user.get("role", "member")
        self.church_title_var.set(church["name"])
        self.account_var.set(f"{user['email']}  •  {role.upper()}")
        is_admin = role == "admin"
        self._show_toolbar_group(self.admin_group, is_admin)
        if is_admin:
            self.publish_controls.grid()
        else:
            self.publish_controls.grid_remove()

        self.songbooks = self.db.list_songbooks(church["id"])
        self.book_by_id = {book["id"]: book for book in self.songbooks}
        self.songs = self.db.list_songs(church["id"])
        self.song_by_id = {song["id"]: song for song in self.songs}
        self._update_songbook_choices()
        self._populate_library_tree()
        self._load_service_plan()
        self._apply_display_settings()
        if self.active_song_id and self.active_song_id in self.song_by_id and not self.editor_dirty:
            self._load_song(self.active_song_id)
        elif self.active_song_id not in self.song_by_id:
            self._clear_editor()
        self.refresh_sync_status()

    def set_status(self, message):
        self.status_var.set(message)

    def refresh_sync_status(self):
        counts = self.db.get_sync_counts(self.church_id())
        waiting = counts["pending"] + counts["failed"]
        if counts["conflict"]:
            self.set_status(
                f"{counts['conflict']} sync conflict(s) need review; {waiting} change(s) waiting."
            )
        elif waiting:
            self.set_status(f"{waiting} local change(s) waiting to sync.")
        else:
            self.set_status("All local changes are synchronized.")

    def show_sync_result(self, result):
        if result.conflicts:
            self.set_status(
                f"Sync finished: {result.pushed} uploaded, {result.conflicts} conflict(s) kept locally."
            )
        else:
            self.set_status(f"Sync finished: {result.pushed} change(s) uploaded.")

    def _populate_library_tree(self):
        if not hasattr(self, "library_tree"):
            return
        selected_song_id = self.selected_library_song_id() or self.active_song_id
        query = self.search_var.get().strip().casefold()
        matching = [
            song
            for song in self.songs
            if not query
            or query in song["title"].casefold()
            or query in strip_chords(song.get("lyrics", "")).casefold()
            or query in song.get("author", "").casefold()
        ]
        by_book: dict[str | None, list[dict[str, Any]]] = {}
        for song in matching:
            by_book.setdefault(song.get("songbook_id"), []).append(song)

        self.library_tree.delete(*self.library_tree.get_children())
        books = self.songbooks if not query else [
            book for book in self.songbooks if by_book.get(book["id"])
        ]
        for book in books:
            book_iid = f"book:{book['id']}"
            self.library_tree.insert(
                "", "end", iid=book_iid, text=f"▣  {book['name']}", open=bool(query)
            )
            for song in by_book.get(book["id"], []):
                song_iid = f"song:{song['id']}"
                self.library_tree.insert(book_iid, "end", iid=song_iid, text=f"♪  {song['title']}")
        unfiled = list(by_book.get(None, []))
        for book_id, orphaned_songs in by_book.items():
            if book_id is not None and book_id not in self.book_by_id:
                unfiled.extend(orphaned_songs)
        if unfiled:
            self.library_tree.insert("", "end", iid="book:", text="▣  Unfiled songs", open=bool(query))
            for song in unfiled:
                self.library_tree.insert(
                    "book:", "end", iid=f"song:{song['id']}", text=f"♪  {song['title']}"
                )
        if selected_song_id and self.library_tree.exists(f"song:{selected_song_id}"):
            iid = f"song:{selected_song_id}"
            self.library_tree.item(self.library_tree.parent(iid), open=True)
            self.library_tree.selection_set(iid)
            self.library_tree.see(iid)

    def selected_library_song_id(self):
        selection = self.library_tree.selection() if hasattr(self, "library_tree") else ()
        if selection and selection[0].startswith("song:"):
            return selection[0].split(":", 1)[1]
        return None

    def selected_song(self):
        song_id = self.selected_library_song_id() or self.active_song_id
        return self.song_by_id.get(song_id) if song_id else None

    def selected_songbook_id(self):
        selection = self.library_tree.selection() if hasattr(self, "library_tree") else ()
        if selection:
            kind, entity_id = selection[0].split(":", 1)
            if kind == "book":
                return entity_id or None
            song = self.song_by_id.get(entity_id)
            if song:
                return song.get("songbook_id")
        song = self.song_by_id.get(self.active_song_id or "")
        return song.get("songbook_id") if song else None

    def _on_library_selected(self, _event=None):
        song_id = self.selected_library_song_id()
        if song_id:
            self._request_load_song(song_id)

    def _clear_search(self):
        self.search_var.set("")
        return "break"

    # ------------------------------------------------------------------
    # Bible library
    # ------------------------------------------------------------------

    def _selected_bible_translation(self) -> str:
        return self.bible_translation_by_name.get(
            self.bible_translation_var.get(), "kjv"
        )

    def _bible_translation_changed(self, _event=None):
        if self.bible_passage is not None:
            reference = self.bible_passage.reference
            self._fetch_bible(reference, select_all=True)

    def _bible_book_changed(self, _event=None):
        book = self.bible_book_var.get()
        chapter_count = next(
            (count for name, count in BOOKS if name == book), 1
        )
        chapters = [str(value) for value in range(1, chapter_count + 1)]
        self.bible_chapter_combo.configure(values=chapters)
        if self.bible_chapter_var.get() not in chapters:
            self.bible_chapter_var.set("1")
        self._load_selected_bible_chapter()

    def _bible_chapter_changed(self, _event=None):
        self._load_selected_bible_chapter()

    def _load_selected_bible_chapter(self):
        book = self.bible_book_var.get().strip()
        chapter = self.bible_chapter_var.get().strip()
        if book and chapter:
            self._fetch_bible(f"{book} {chapter}", select_all=False)

    def load_bible_reference(self):
        reference = self.bible_reference_var.get().strip()
        if not reference:
            self.bible_status_var.set("Enter a Bible reference first.")
            return
        self._fetch_bible(reference, select_all=True)

    def _fetch_bible(self, reference: str, select_all: bool):
        if self.bible_loading:
            return
        self.bible_loading = True
        translation = self._selected_bible_translation()
        self.bible_status_var.set(f"Loading {reference}…")

        def success(passage: BiblePassage):
            self.bible_loading = False
            self.bible_passage = passage
            self.bible_reference_var.set(passage.reference)
            self.bible_verse_list.delete(0, tk.END)
            for verse in passage.verses:
                prefix = f"{verse['verse']:>3}  "
                self.bible_verse_list.insert(
                    tk.END,
                    fit_to_width(
                        self.bible_verse_list,
                        prefix + verse["text"],
                        px(18),
                    ),
                )
            if passage.verses:
                if select_all:
                    self.bible_verse_list.selection_set(0, tk.END)
                else:
                    self.bible_verse_list.selection_set(0)
                self.bible_verse_list.see(0)
                self._on_bible_verse_selected()
            self.bible_status_var.set(
                f"{passage.reference} · {passage.translation_name} · "
                f"{len(passage.verses)} verse(s)"
            )

        def error(exc: Exception):
            self.bible_loading = False
            self.bible_status_var.set(str(exc))

        self.controller.run_background(
            lambda: self.bible_service.fetch(reference, translation), success, error
        )

    def _selected_bible_verses(self) -> list[dict[str, Any]]:
        if self.bible_passage is None:
            return []
        indices = list(self.bible_verse_list.curselection())
        return [
            self.bible_passage.verses[index]
            for index in indices
            if 0 <= index < len(self.bible_passage.verses)
        ]

    @staticmethod
    def _bible_reference_for_verses(verses: list[dict[str, Any]]) -> str:
        if not verses:
            return "Bible passage"
        first, last = verses[0], verses[-1]
        if first["book"] == last["book"] and first["chapter"] == last["chapter"]:
            base = f"{first['book']} {first['chapter']}"
            if first["verse"] == last["verse"]:
                return f"{base}:{first['verse']}"
            return f"{base}:{first['verse']}-{last['verse']}"
        return (
            f"{first['book']} {first['chapter']}:{first['verse']} – "
            f"{last['book']} {last['chapter']}:{last['verse']}"
        )

    def _current_bible_item_from_selection(self) -> dict[str, Any] | None:
        if self.bible_passage is None:
            return None
        verses = self._selected_bible_verses()
        if not verses:
            return None
        reference = self._bible_reference_for_verses(verses)
        title = f"{reference} ({self.bible_passage.translation_id.upper()})"
        return self._make_bible_agenda_item(
            title=title,
            reference=reference,
            translation=self.bible_passage.translation_id,
            translation_name=self.bible_passage.translation_name,
            verses=verses,
        )

    def _on_bible_verse_selected(self, _event=None):
        item = self._current_bible_item_from_selection()
        if item is None:
            return
        self._load_bible_item(item)

    def add_bible_to_service(self):
        item = self._current_bible_item_from_selection()
        if item is None:
            self.set_status("Select one or more Bible verses first.")
            return
        self.service_items.append(item)
        self._save_service_plan()
        self._render_service_plan(len(self.service_items) - 1)
        self.set_status(f"Added {item['title']} to the service agenda.")

    def _request_load_bible_item(self, item: dict[str, Any]) -> bool:
        if self.editor_dirty:
            answer = messagebox.askyesnocancel(
                "Unsaved Song Changes",
                "Save the current song changes before opening the Bible passage?",
                parent=self,
            )
            if answer is None:
                return False
            if answer and not self.save_editor():
                return False
        self._load_bible_item(item)
        return True

    def _load_bible_item(self, item: dict[str, Any]):
        self.active_content_kind = "bible"
        self.active_bible_item = item
        self.active_presentation_item = None
        self._set_editor_enabled(False)
        self.editor_state_var.set("BIBLE PASSAGE · READ ONLY")
        self._rebuild_bible_slides()

    def _rebuild_bible_slides(self):
        item = self.active_bible_item
        if not item:
            self.slides = []
            self._slide_labels = []
            self._render_slide_labels()
            self.preview_slide = None
            self._draw_preview()
            return
        slides: list[LyricSlide] = []
        for index, verse in enumerate(item.get("verses", [])):
            label = f"{verse.get('book', '')} {verse.get('chapter', '')}:{verse.get('verse', '')}"
            slides.append(
                LyricSlide(
                    segment_index=index,
                    segment_label=label.strip(),
                    part_index=1,
                    part_count=1,
                    text=f"{verse.get('verse', '')}  {verse.get('text', '')}".strip(),
                )
            )
        self.slides = slides
        self.slide_rule_var.set(
            f"{len(slides)} Bible verse{'s' if len(slides) != 1 else ''}"
        )
        self._slide_labels = [
            f"{index:02d}  {slide.label}  ·  {slide.text}"
            for index, slide in enumerate(slides, start=1)
        ]
        self._render_slide_labels()
        if self.slides:
            self.slide_list.selection_set(0)
            self.slide_list.see(0)
            self._on_slide_selected()
        else:
            self.preview_slide = None
            self._draw_preview()

    def _make_presentation_agenda_item(
        self, path: str, title: str | None = None, transition="direct"
    ) -> dict[str, Any]:
        source = str(Path(path).expanduser().resolve())
        return {
            "id": f"presentation:{source}",
            "title": title or Path(source).stem or "Presentation",
            "path": source,
            "_kind": "presentation",
            "_transition": transition,
        }

    def add_presentation_to_service(self):
        selected = filedialog.askopenfilename(
            parent=self,
            title="Add announcement presentation",
            filetypes=[
                ("Presentations", "*.ppt *.pptx *.pdf"),
                ("PowerPoint", "*.ppt *.pptx"),
                ("PDF", "*.pdf"),
                ("All files", "*.*"),
            ],
        )
        if not selected:
            return
        self.set_status("Importing presentation slides…")
        self.update_idletasks()
        try:
            title, slides = import_presentation(selected)
        except PresentationImportError as exc:
            messagebox.showerror("Presentation Could Not Be Imported", str(exc), parent=self)
            self.set_status(str(exc))
            return
        source = str(Path(selected).expanduser().resolve())
        self._presentation_cache[source] = slides
        item = self._make_presentation_agenda_item(source, title)
        self.service_items.append(item)
        self._save_service_plan()
        self._render_service_plan(len(self.service_items) - 1)
        self._load_presentation_item(item)
        self.set_status(
            f"Added '{item['title']}' ({len(slides)} slides) to the service agenda."
        )

    def _request_load_presentation_item(self, item: dict[str, Any]) -> bool:
        if self.editor_dirty:
            answer = messagebox.askyesnocancel(
                "Unsaved Song Changes",
                "Save the current song changes before opening the presentation?",
                parent=self,
            )
            if answer is None:
                return False
            if answer and not self.save_editor():
                return False
        try:
            self._load_presentation_item(item)
        except PresentationImportError as exc:
            messagebox.showerror("Presentation Could Not Be Opened", str(exc), parent=self)
            self.set_status(str(exc))
            return False
        return True

    def _load_presentation_item(self, item: dict[str, Any]) -> None:
        source = str(Path(str(item.get("path", ""))).expanduser().resolve())
        slides = self._presentation_cache.get(source)
        if slides is None:
            title, slides = import_presentation(source)
            self._presentation_cache[source] = slides
            if not item.get("title"):
                item["title"] = title
        self.active_content_kind = "presentation"
        self.active_bible_item = None
        self.active_presentation_item = item
        self.active_song_id = None
        self._set_editor_enabled(False)
        self.editor_state_var.set("PRESENTATION · READ ONLY")
        self.slides = list(slides)
        self.slide_rule_var.set(
            f"{len(self.slides)} presentation slide{'s' if len(self.slides) != 1 else ''}"
        )
        self._slide_labels = [
            f"{index:02d}  {slide.label}" for index, slide in enumerate(self.slides, start=1)
        ]
        self._render_slide_labels()
        if self.slides:
            self.slide_list.selection_set(0)
            self.slide_list.see(0)
            self._on_slide_selected()
        else:
            self.preview_slide = None
            self._draw_preview()

    def _request_load_agenda_item(self, item: dict[str, Any]) -> bool:
        kind = str(item.get("_kind") or "song")
        if kind == "bible":
            return self._request_load_bible_item(item)
        if kind == "presentation":
            return self._request_load_presentation_item(item)
        return self._request_load_song(item["id"])

    # ------------------------------------------------------------------
    # Service agenda
    # ------------------------------------------------------------------

    def _service_key(self):
        return f"service_plan:{self.church_id()}"

    @staticmethod
    def _agenda_transition(item: dict[str, Any]) -> str:
        value = str(item.get("_transition", "direct")).strip().lower()
        return value if value in {"direct", "hide_text", "show_logo"} else "direct"

    @staticmethod
    def _agenda_transition_label(value: str) -> str:
        return {
            "direct": "Next item",
            "hide_text": "Hide text",
            "show_logo": "Show logo",
        }.get(value, "Next item")

    @staticmethod
    def _agenda_transition_value(label: str) -> str:
        return {
            "Next item": "direct",
            "Next song": "direct",
            "Hide text": "hide_text",
            "Show logo": "show_logo",
        }.get(str(label), "direct")

    def _make_song_agenda_item(self, song: dict[str, Any], transition="direct"):
        item = dict(song)
        item["_kind"] = "song"
        item["_transition"] = transition
        return item

    def _make_bible_agenda_item(
        self,
        *,
        title: str,
        reference: str,
        translation: str,
        translation_name: str,
        verses: list[dict[str, Any]],
        transition="direct",
    ):
        return {
            "id": f"bible:{translation}:{reference}:{len(verses)}",
            "title": title,
            "_kind": "bible",
            "_transition": transition,
            "reference": reference,
            "translation": translation,
            "translation_name": translation_name,
            "verses": [dict(verse) for verse in verses],
        }

    def _serialize_service_item(self, item: dict[str, Any]) -> dict[str, Any]:
        if item.get("_kind") == "presentation":
            return {
                "kind": "presentation",
                "title": item.get("title", "Presentation"),
                "path": item.get("path", ""),
                "transition": self._agenda_transition(item),
            }
        if item.get("_kind") == "bible":
            return {
                "kind": "bible",
                "title": item.get("title", "Bible passage"),
                "reference": item.get("reference", ""),
                "translation": item.get("translation", "kjv"),
                "translationName": item.get("translation_name", ""),
                "verses": item.get("verses", []),
                "transition": self._agenda_transition(item),
            }
        return {
            "kind": "song",
            "songId": item.get("id", ""),
            "transition": self._agenda_transition(item),
        }

    def _deserialize_service_item(self, raw: Any) -> dict[str, Any] | None:
        # Compatibility with Stage Cue 0.2, which stored only a list of song IDs.
        if isinstance(raw, str):
            song = self.song_by_id.get(raw)
            return self._make_song_agenda_item(song) if song else None
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind") or "song").strip().lower()
        transition = str(raw.get("transition") or "direct").strip().lower()
        if kind == "presentation":
            path = str(raw.get("path") or "").strip()
            if not path:
                return None
            return self._make_presentation_agenda_item(
                path, str(raw.get("title") or Path(path).stem), transition
            )
        if kind == "bible":
            verses = raw.get("verses")
            if not isinstance(verses, list) or not verses:
                return None
            reference = str(raw.get("reference", "")).strip()
            translation = str(raw.get("translation", "kjv")).strip().lower() or "kjv"
            translation_name = str(raw.get("translationName", "")).strip()
            title = str(raw.get("title") or reference or "Bible passage")
            return self._make_bible_agenda_item(
                title=title,
                reference=reference,
                translation=translation,
                translation_name=translation_name,
                verses=verses,
                transition=transition,
            )
        song_id = str(raw.get("songId") or raw.get("id") or "")
        song = self.song_by_id.get(song_id)
        return self._make_song_agenda_item(song, transition) if song else None

    def _load_service_plan(self):
        church_id = self.church_id()
        if not church_id:
            return
        if self._plan_church_id != church_id:
            raw = self.db.get_setting(self._service_key(), "[]")
            try:
                saved_items = json.loads(raw or "[]")
                if not isinstance(saved_items, list):
                    saved_items = []
            except (json.JSONDecodeError, TypeError):
                saved_items = []
            self._plan_church_id = church_id
            self.live_mode = "READY"
            self.stage_mode = "READY"
            self.stage_message = ""
            self.presentation_active = False
            self.output_frozen = False
            self.preview_slide = None
            self.live_slide = None
            self.live_song_title = "No slide is live"
            self.text_hidden = False
            self.logo_visible = False
            self._update_present_controls()
            self._update_live_control_buttons()
            self._update_stage_state()
            if hasattr(self, "hide_text_button"):
                self.hide_text_button.configure(
                    text="Hide Text   Ctrl+T",
                    bg=self.COLORS["neutral"],
                    activebackground=self.COLORS["neutral"],
                )
            if hasattr(self, "logo_button"):
                self.logo_button.configure(
                    text="Show Logo   Ctrl+Q",
                    bg=self.COLORS["purple"],
                    activebackground=self.COLORS["purple"],
                )
            self.agenda_file_path = None
            self.agenda_name = "Unsaved Agenda"
            self.agenda_dirty = bool(saved_items)
            self.service_items = [
                item
                for raw_item in saved_items
                if (item := self._deserialize_service_item(raw_item)) is not None
            ]
        else:
            refreshed: list[dict[str, Any]] = []
            for item in self.service_items:
                if item.get("_kind") in {"bible", "presentation"}:
                    refreshed.append(item)
                    continue
                song = self.song_by_id.get(str(item.get("id", "")))
                if song:
                    refreshed.append(
                        self._make_song_agenda_item(song, self._agenda_transition(item))
                    )
            self.service_items = refreshed
        self._save_service_plan(False)
        self._render_service_plan()

    def _save_service_plan(self, mark_dirty=True):
        if self.church_id():
            self.db.set_setting(
                self._service_key(),
                json.dumps([self._serialize_service_item(item) for item in self.service_items]),
            )
        if mark_dirty:
            self.agenda_dirty = True
        self._update_agenda_file_status()

    def _update_agenda_file_status(self):
        if not hasattr(self, "agenda_file_var"):
            return
        marker = " *" if self.agenda_dirty else ""
        self.agenda_file_var.set(f"{self.agenda_name}{marker}")

    def _confirm_replace_agenda(self):
        if not self.agenda_dirty:
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved Agenda",
            "Save changes to the current agenda before replacing it?",
            parent=self,
        )
        if answer is None:
            return False
        if answer:
            return self.save_agenda()
        return True

    def new_agenda(self):
        if not self._confirm_replace_agenda():
            return
        if self._pending_agenda_transition is not None:
            self._pending_agenda_transition = None
            self._clear_agenda_transition_visibility()
        self.service_items = []
        self.agenda_file_path = None
        self.agenda_name = "Unsaved Agenda"
        self.agenda_dirty = False
        self._save_service_plan(False)
        self._render_service_plan()
        self.set_status("New empty agenda created.")

    def load_agenda(self):
        if not self._confirm_replace_agenda():
            return
        if self._pending_agenda_transition is not None:
            self._pending_agenda_transition = None
            self._clear_agenda_transition_visibility()
        selected = filedialog.askopenfilename(
            parent=self,
            title="Load Stage Cue agenda",
            filetypes=[("Stage Cue agenda", "*.stagecue-agenda"), ("JSON", "*.json"), ("All files", "*.*")],
        )
        if not selected:
            return
        try:
            document = load_agenda_file(selected)
        except ValueError as exc:
            messagebox.showerror("Agenda Could Not Be Loaded", str(exc), parent=self)
            return
        if document["churchId"] != self.church_id():
            messagebox.showerror(
                "Different Church",
                "This agenda belongs to a different church.",
                parent=self,
            )
            return
        loaded_items: list[dict[str, Any]] = []
        missing = 0
        for raw_item in document["items"]:
            item = self._deserialize_service_item(raw_item)
            if item is None:
                missing += 1
            else:
                loaded_items.append(item)
        self.service_items = loaded_items
        self.agenda_file_path = Path(selected)
        self.agenda_name = str(document.get("name") or self.agenda_file_path.stem)
        self.agenda_dirty = False
        self._save_service_plan(False)
        self._render_service_plan(0 if self.service_items else None)
        suffix = f" {missing} unavailable item(s) were skipped." if missing else ""
        self.set_status(f"Loaded agenda '{self.agenda_name}'.{suffix}")

    def save_agenda(self):
        if self.agenda_file_path is None:
            return self.save_agenda_as()
        document = build_agenda_document(
            self.church_id(),
            self.controller.current_session["church"]["name"],
            self.agenda_name,
            self.service_items,
        )
        try:
            save_agenda_file(self.agenda_file_path, document)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Agenda Could Not Be Saved", str(exc), parent=self)
            return False
        self.agenda_dirty = False
        self._update_agenda_file_status()
        self.set_status(f"Saved agenda to {self.agenda_file_path}.")
        return True

    def save_agenda_as(self):
        selected = filedialog.asksaveasfilename(
            parent=self,
            title="Save Stage Cue agenda",
            defaultextension=".stagecue-agenda",
            initialfile=(
                f"{self.agenda_name}.stagecue-agenda"
                if self.agenda_name != "Unsaved Agenda"
                else "service.stagecue-agenda"
            ),
            filetypes=[("Stage Cue agenda", "*.stagecue-agenda"), ("All files", "*.*")],
        )
        if not selected:
            return False
        self.agenda_file_path = Path(selected)
        self.agenda_name = self.agenda_file_path.stem
        return self.save_agenda()

    def _render_service_plan(self, select_index=None):
        current = self.agenda_list.curselection()
        if select_index is None and current:
            select_index = current[0]
        self.agenda_list.delete(0, tk.END)
        transition_marks = {"direct": "→", "hide_text": "▰", "show_logo": "◆"}
        for index, item in enumerate(self.service_items, start=1):
            kind_mark = {"bible": "✝", "presentation": "▣"}.get(
                item.get("_kind"), "♪"
            )
            transition_mark = transition_marks[self._agenda_transition(item)]
            self.agenda_list.insert(
                tk.END, f"{index:02d}  {kind_mark} {item['title']}   {transition_mark}"
            )
        if self.service_items and select_index is not None:
            select_index = max(0, min(select_index, len(self.service_items) - 1))
            self.agenda_list.selection_set(select_index)
            self.agenda_list.see(select_index)
            self._on_agenda_selected()

    def add_to_service(self):
        if self._pending_agenda_transition is not None:
            self._pending_agenda_transition = None
            self._clear_agenda_transition_visibility()
        song_id = self.selected_library_song_id()
        song = self.song_by_id.get(song_id or "")
        if not song:
            self.set_status("Select a song under a songbook first.")
            return
        self.service_items.append(self._make_song_agenda_item(song))
        self._save_service_plan()
        self._render_service_plan(len(self.service_items) - 1)
        self.set_status(f"Added '{song['title']}' to the service agenda.")

    def remove_from_service(self):
        if self._pending_agenda_transition is not None:
            self._pending_agenda_transition = None
            self._clear_agenda_transition_visibility()
        selection = self.agenda_list.curselection()
        if not selection:
            self.set_status("Select an agenda item first.")
            return
        index = selection[0]
        removed = self.service_items.pop(index)
        self._save_service_plan()
        self._render_service_plan(min(index, len(self.service_items) - 1))
        self.set_status(f"Removed '{removed['title']}' from the service agenda.")

    def move_service(self, direction):
        if self._pending_agenda_transition is not None:
            self._pending_agenda_transition = None
            self._clear_agenda_transition_visibility()
        selection = self.agenda_list.curselection()
        if not selection:
            return
        source = selection[0]
        target = source + direction
        if not 0 <= target < len(self.service_items):
            return
        self.service_items[source], self.service_items[target] = self.service_items[target], self.service_items[source]
        self._save_service_plan()
        self._render_service_plan(target)

    def clear_service(self):
        if self._pending_agenda_transition is not None:
            self._pending_agenda_transition = None
            self._clear_agenda_transition_visibility()
        if self.service_items and messagebox.askyesno(
            "Clear Service Agenda", "Remove every item from the current local agenda?"
        ):
            self.service_items = []
            self._save_service_plan()
            self._render_service_plan()
            self.set_status("The service agenda is empty.")

    def _on_agenda_selected(self, _event=None):
        selection = self.agenda_list.curselection()
        if selection and selection[0] < len(self.service_items):
            item = self.service_items[selection[0]]
            self.agenda_transition_var.set(
                self._agenda_transition_label(self._agenda_transition(item))
            )
            if self._pending_agenda_transition is not None:
                self._pending_agenda_transition = None
                self._clear_agenda_transition_visibility()
            self._request_load_agenda_item(item)

    def _agenda_transition_changed(self, _event=None):
        selection = self.agenda_list.curselection()
        if not selection or selection[0] >= len(self.service_items):
            return
        item = self.service_items[selection[0]]
        item["_transition"] = self._agenda_transition_value(
            self.agenda_transition_var.get()
        )
        self._save_service_plan()
        self._render_service_plan(selection[0])
        self.set_status(
            f"After '{item['title']}': {self.agenda_transition_var.get().lower()}."
        )

    # ------------------------------------------------------------------
    # Full-song lyrics editor
    # ------------------------------------------------------------------

    def _update_songbook_choices(self):
        self._songbook_ids = [None] + [book["id"] for book in self.songbooks]
        self.songbook_combo.configure(values=["Unfiled"] + [book["name"] for book in self.songbooks])

    def _request_load_song(self, song_id):
        if song_id == self.active_song_id and self.active_content_kind == "song":
            return True
        if self.editor_dirty:
            answer = messagebox.askyesnocancel(
                "Unsaved Song Changes",
                "Save the current song changes before opening another song?",
                parent=self,
            )
            if answer is None:
                self._restore_library_selection()
                return False
            if answer and not self.save_editor():
                self._restore_library_selection()
                return False
        self._load_song(song_id)
        return True

    def _load_song(self, song_id):
        song = self.song_by_id.get(song_id)
        if not song:
            return
        self.active_content_kind = "song"
        self.active_bible_item = None
        self.active_presentation_item = None
        self._suppress_editor_events = True
        self._set_editor_enabled(True)
        self.active_song_id = song_id
        self.title_var.set(song["title"])
        self.author_var.set(song.get("author", ""))
        self.copyright_var.set(song.get("copyright", ""))
        self.ccli_var.set(song.get("ccli_number", ""))
        try:
            self.songbook_combo.current(self._songbook_ids.index(song.get("songbook_id")))
        except ValueError:
            self.songbook_combo.current(0)
        lyrics = str(song.get("lyrics", ""))
        stored_segments = normalize_segments(song.get("segments"), lyrics)
        if stored_segments and not has_segment_headers(lyrics):
            lyrics = segments_to_lyrics(stored_segments)
        self._set_lyrics_text(lyrics or "[Verse 1]\n")
        self._sync_segments_from_lyrics()
        self.editor_dirty = False
        self.editor_state_var.set("SAVED LOCALLY")
        self._suppress_editor_events = False
        self._rebuild_slides()
        self._restore_library_selection()

    def new_song(self):
        if self.editor_dirty:
            answer = messagebox.askyesnocancel(
                "Unsaved Song Changes", "Save the current song before creating a new one?", parent=self
            )
            if answer is None:
                return
            if answer and not self.save_editor():
                return
        self._suppress_editor_events = True
        self.active_content_kind = "song"
        self.active_bible_item = None
        self.active_presentation_item = None
        self._set_editor_enabled(True)
        self.active_song_id = None
        self.title_var.set("")
        self.author_var.set("")
        self.copyright_var.set("")
        self.ccli_var.set("")
        selected_book = self.selected_songbook_id()
        try:
            self.songbook_combo.current(self._songbook_ids.index(selected_book))
        except ValueError:
            self.songbook_combo.current(0)
        self._set_lyrics_text("[Verse 1]\n")
        self._sync_segments_from_lyrics()
        self.editor_dirty = True
        self.editor_state_var.set("NEW · UNSAVED")
        self._suppress_editor_events = False
        self._rebuild_slides()
        self.title_entry.focus_set()

    def _set_lyrics_text(self, lyrics):
        self.lyrics_editor.delete("1.0", tk.END)
        self.lyrics_editor.insert("1.0", lyrics)
        self.lyrics_editor.edit_modified(False)

    def _lyrics_text(self):
        return self.lyrics_editor.get("1.0", "end-1c")

    def _sync_segments_from_lyrics(self):
        self.editor_segments = segments_from_lyrics(self._lyrics_text())
        count = len(self.editor_segments)
        self.editor_hint_var.set(
            f"{count} segment{'s' if count != 1 else ''} detected automatically."
            if count
            else "Add a tag such as [Verse 1] to begin the song."
        )

    def _lyrics_modified(self, _event=None):
        if not self.lyrics_editor.edit_modified():
            return
        self.lyrics_editor.edit_modified(False)
        if self._suppress_editor_events:
            return
        self._sync_segments_from_lyrics()
        self._mark_editor_dirty()
        self._rebuild_slides()

    def _select_all_lyrics(self, _event=None):
        """Provide conventional Select All behavior consistently on every OS."""

        self.lyrics_editor.tag_add(tk.SEL, "1.0", "end-1c")
        self.lyrics_editor.mark_set(tk.INSERT, "1.0")
        self.lyrics_editor.see(tk.INSERT)
        return "break"

    def _mark_editor_dirty(self):
        if self._suppress_editor_events or self.title_entry.cget("state") != "normal":
            return
        self.editor_dirty = True
        self.editor_state_var.set("UNSAVED CHANGES")

    def save_editor(self):
        if self.title_entry.cget("state") != "normal":
            self.set_status("Select or create a song first.")
            return False
        title = self.title_var.get().strip()
        if not title:
            self.set_status("A song title is required.")
            self.title_entry.focus_set()
            return False
        songbook_index = self.songbook_combo.current()
        songbook_id = self._songbook_ids[songbook_index] if songbook_index >= 0 else None
        lyrics = self._lyrics_text().strip()
        self.editor_segments = segments_from_lyrics(lyrics)
        if lyrics and not has_segment_headers(lyrics):
            lyrics = segments_to_lyrics(self.editor_segments)
            self._suppress_editor_events = True
            self._set_lyrics_text(lyrics)
            self._suppress_editor_events = False
        song_id = self.db.save_song(
            church_id=self.church_id(),
            title=title,
            lyrics=lyrics,
            songbook_id=songbook_id,
            song_id=self.active_song_id,
            author=self.author_var.get(),
            copyright=self.copyright_var.get(),
            ccli_number=self.ccli_var.get(),
            segments=self.editor_segments,
        )
        self.active_song_id = song_id
        self.editor_dirty = False
        self.editor_state_var.set("SAVED LOCALLY")
        self.refresh()
        self.set_status(f"'{title}' saved locally — ready to sync.")
        return True

    def discard_editor_changes(self):
        if self.active_song_id and self.active_song_id in self.song_by_id:
            self._load_song(self.active_song_id)
        else:
            self._clear_editor()
        self.set_status("Unsaved editor changes discarded.")

    def _clear_editor(self):
        self._suppress_editor_events = True
        self.active_content_kind = "song"
        self.active_bible_item = None
        self.active_presentation_item = None
        self._set_editor_enabled(True)
        self.active_song_id = None
        for variable in (self.title_var, self.author_var, self.copyright_var, self.ccli_var):
            variable.set("")
        self.songbook_combo.set("")
        self.editor_segments = []
        self._set_lyrics_text("")
        self.editor_hint_var.set("Segments are detected automatically from the full song.")
        self.editor_dirty = False
        self.editor_state_var.set("NO SONG SELECTED")
        self._set_editor_enabled(False)
        self._suppress_editor_events = False
        self.slides = []
        self.preview_slide = None
        self.slide_list.delete(0, tk.END)
        self._draw_preview()

    def _set_editor_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for widget in (
            self.title_entry,
            self.author_entry,
            self.copyright_entry,
            self.ccli_entry,
            self.lyrics_editor,
        ):
            widget.configure(state=state)
        self.songbook_combo.configure(state="readonly" if enabled else "disabled")

    def _restore_library_selection(self):
        if self.active_song_id and self.library_tree.exists(f"song:{self.active_song_id}"):
            iid = f"song:{self.active_song_id}"
            self.library_tree.item(self.library_tree.parent(iid), open=True)
            self.library_tree.selection_set(iid)
            self.library_tree.see(iid)

    # ------------------------------------------------------------------
    # Slide generation, preview, and text effects
    # ------------------------------------------------------------------

    def _rebuild_slides(self):
        if self.active_content_kind == "presentation" and self.active_presentation_item:
            return
        if self.active_content_kind == "bible" and self.active_bible_item:
            self._rebuild_bible_slides()
            return
        max_lines = int(self.display_settings.get("maxLinesPerSlide", 4))
        self.slides = build_segment_slides(self.editor_segments, max_lines)
        self.slide_rule_var.set(f"{max_lines} line{'s' if max_lines != 1 else ''} per slide")
        current = self.slide_list.curselection()
        selected = current[0] if current else 0
        self._slide_labels = [
            f"{index:02d}  {slide.label}  ·  " + " / ".join(slide.text.splitlines())
            for index, slide in enumerate(self.slides, start=1)
        ]
        self._render_slide_labels()
        if self.slides:
            selected = min(selected, len(self.slides) - 1)
            self.slide_list.selection_set(selected)
            self.slide_list.see(selected)
            self._on_slide_selected()
        else:
            self.preview_slide = None
            self._draw_preview()

    def _render_slide_labels(self):
        """Redraw the cue list, eliding each row to the column's real width."""

        labels = getattr(self, "_slide_labels", [])
        selection = self.slide_list.curselection()
        top = self.slide_list.yview()[0]
        self.slide_list.delete(0, tk.END)
        for label in labels:
            self.slide_list.insert(tk.END, fit_to_width(self.slide_list, label, px(14)))
        if selection:
            index = min(selection[0], max(0, self.slide_list.size() - 1))
            self.slide_list.selection_set(index)
        if labels:
            self.slide_list.yview_moveto(top)
        self._slide_label_width = self.slide_list.winfo_width()

    def _refit_slide_labels(self, _event=None):
        if not getattr(self, "_slide_labels", None):
            return
        width = self.slide_list.winfo_width()
        if width == getattr(self, "_slide_label_width", None):
            return
        self._render_slide_labels()

    def _on_slide_selected(self, _event=None):
        selection = self.slide_list.curselection()
        if selection and selection[0] < len(self.slides):
            if _event is not None and self.text_hidden:
                self.text_hidden = False
                self._update_live_control_buttons()
            slide = self.slides[selection[0]]
            self.preview_slide = slide
            self.preview_caption_var.set(f"Selected: {slide.label}")
            self._draw_preview()
            if (
                _event is not None
                and self.presentation_active
                and not self.output_frozen
            ):
                self._present_selected_slide()

    def present_library_song(self):
        song = self.song_by_id.get(self.selected_library_song_id() or "")
        if not song:
            return
        if self._request_load_song(song["id"]) and self.slides:
            self._select_preview_slide(0)
            if not self.presentation_active:
                self.toggle_present_mode()

    def toggle_present_mode(self):
        if self.presentation_active:
            self.presentation_active = False
            self.output_frozen = False
            self.text_hidden = False
            self.live_mode = "READY"
            self.controller.close_live_view()
            self.stage_mode = "CLEAR"
            self.stage_message = ""
            self._update_stage_state()
            self._publish_stage_view()
            self._update_present_controls()
            self._update_live_control_buttons()
            self._draw_preview()
            self.set_status(
                "Present mode is off. Live View closed and Stage View cleared."
            )
            return
        selection = self.slide_list.curselection()
        if not selection or selection[0] >= len(self.slides):
            self.set_status("Select a slide first.")
            return
        self.presentation_active = True
        self.output_frozen = False
        self._update_present_controls()
        self._update_live_control_buttons()
        self._present_selected_slide()

    # Retained as a compatibility entry point for older integrations.
    def show_selected_slide(self):
        self.toggle_present_mode()

    def _present_selected_slide(self):
        if self.output_frozen:
            self.set_status(
                "Output is frozen. Preview moved, but Live View and Stage View are holding."
            )
            return
        selection = self.slide_list.curselection()
        if not selection or selection[0] >= len(self.slides):
            return
        # Hide Text is a temporary pause between cues. Advancing to any slide
        # resumes the lyrics automatically so the operator cannot accidentally
        # leave the audience looking at a blank Live View.
        if self.text_hidden:
            self.text_hidden = False
            self._update_live_control_buttons()
        slide = self.slides[selection[0]]
        live_title = self._current_content_title()
        self.live_slide = slide
        self.preview_slide = self.live_slide
        self.live_song_title = live_title
        self.live_mode = "LIVE"
        self.stage_mode = "LYRICS"
        self.stage_message = ""
        self._update_stage_state()
        self._publish_presented_slide()
        self._draw_preview()
        self.set_status(
            "Present mode is on — slide changes now update Live View and Stage View."
        )

    def _update_present_controls(self):
        if not hasattr(self, "present_button"):
            return
        if self.presentation_active:
            self.present_button.configure(
                text="Stop Presenting   Ctrl+P",
                bg=self.COLORS["success"],
                activebackground=self.COLORS["success"],
            )
            if hasattr(self, "present_state_var"):
                if self.output_frozen:
                    self.present_state_var.set("● PRESENTING · FROZEN")
                    self.present_state_label.configure(
                        bg=PALETTE["badge_frozen_bg"], fg=PALETTE["badge_frozen_fg"]
                    )
                else:
                    self.present_state_var.set("● PRESENTING")
                    self.present_state_label.configure(
                        bg=PALETTE["badge_live_bg"], fg=PALETTE["badge_live_fg"]
                    )
        else:
            self.present_button.configure(
                text="Start Presenting   Ctrl+P",
                bg=self.COLORS["neutral"],
                activebackground=self.COLORS["neutral"],
            )
            if hasattr(self, "present_state_var"):
                self.present_state_var.set("● PREVIEW ONLY")
                self.present_state_label.configure(
                    bg=PALETTE["badge_idle_bg"], fg=PALETTE["badge_idle_fg"]
                )

    def next_live(self):
        self._step_live(1)

    def previous_live(self):
        self._step_live(-1)

    def _step_live(self, direction):
        selection = self.slide_list.curselection()
        if not selection:
            return
        target = selection[0] + direction
        if 0 <= target < len(self.slides):
            if self._pending_agenda_transition is not None:
                self._pending_agenda_transition = None
                self._clear_agenda_transition_visibility()
            self._select_preview_slide(target)
            return
        if direction > 0 and self._pending_agenda_transition is not None:
            source, destination = self._pending_agenda_transition
            self._pending_agenda_transition = None
            self._clear_agenda_transition_visibility()
            self._jump_to_agenda_index(destination, 1)
            return
        if direction > 0 and self._begin_agenda_transition():
            return
        self.jump_agenda(direction)

    def restart_song(self):
        if self.slides:
            self._select_preview_slide(0)

    def _select_preview_slide(self, index):
        if not 0 <= index < len(self.slides):
            return
        self.slide_list.selection_clear(0, tk.END)
        self.slide_list.selection_set(index)
        self.slide_list.see(index)
        self._on_slide_selected()
        if self.presentation_active and not self.output_frozen:
            self._present_selected_slide()
        elif self.presentation_active:
            self.set_status(
                "Output is frozen. Preview moved, but Live View and Stage View are holding."
            )
        else:
            self.set_status(
                "Preview selected. Turn Present on to make slide changes follow live."
            )

    def jump_agenda(self, direction):
        selection = self.agenda_list.curselection()
        if not selection:
            return
        target = selection[0] + direction
        if not 0 <= target < len(self.service_items):
            self.set_status("Reached the end of the service agenda.")
            return
        if self._pending_agenda_transition is not None:
            self._pending_agenda_transition = None
            self._clear_agenda_transition_visibility()
        self._jump_to_agenda_index(target, direction)

    def _jump_to_agenda_index(self, target: int, direction: int):
        self.agenda_list.selection_clear(0, tk.END)
        self.agenda_list.selection_set(target)
        self.agenda_list.see(target)
        item = self.service_items[target]
        loaded = self._request_load_agenda_item(item)
        if loaded and self.slides:
            self.agenda_transition_var.set(
                self._agenda_transition_label(self._agenda_transition(item))
            )
            slide_index = len(self.slides) - 1 if direction < 0 else 0
            self._select_preview_slide(slide_index)

    def _begin_agenda_transition(self) -> bool:
        selection = self.agenda_list.curselection()
        if not selection:
            return False
        source = selection[0]
        destination = source + 1
        if not 0 <= destination < len(self.service_items):
            return False
        transition = self._agenda_transition(self.service_items[source])
        if transition == "direct":
            return False

        self._pending_agenda_transition = (source, destination)
        if transition == "show_logo" and self.live_display_settings.get("logoData"):
            self.text_hidden = False
            self.logo_visible = True
            message = "Church logo shown before the next agenda item. Press Next again to continue."
        else:
            # If no logo is configured, Show Logo safely falls back to a blank lyric state.
            self.text_hidden = True
            self.logo_visible = False
            message = (
                "No church logo is configured, so text is hidden before the next agenda item. "
                "Press Next again to continue."
                if transition == "show_logo"
                else "Text hidden before the next agenda item. Press Next again to continue."
            )
        self._update_live_control_buttons()
        self._draw_preview()
        if self.presentation_active and not self.output_frozen:
            self._publish_live_view()
        self.set_status(message)
        return True

    def _clear_agenda_transition_visibility(self):
        self.text_hidden = False
        self.logo_visible = False
        self._update_live_control_buttons()
        self._draw_preview()

    def _current_content_title(self) -> str:
        if self.active_content_kind == "presentation" and self.active_presentation_item:
            return str(self.active_presentation_item.get("title") or "Presentation")
        if self.active_content_kind == "bible" and self.active_bible_item:
            return str(self.active_bible_item.get("title") or "Bible passage")
        return self.title_var.get().strip() or "Untitled"

    def start_presentation(self):
        if self.service_items:
            self.agenda_list.selection_clear(0, tk.END)
            self.agenda_list.selection_set(0)
            item = self.service_items[0]
            loaded = self._request_load_agenda_item(item)
            if loaded:
                self.presentation_active = False
                self.output_frozen = False
                self.restart_song()
                self.presentation_active = True
                self._update_present_controls()
                self._update_live_control_buttons()
                self._present_selected_slide()
        else:
            self.present_library_song()

    def open_secondary_output(self):
        # Opening the output window must not promote the selected Preview cue.
        # It reopens the last presented cue, or an empty READY cue if nothing
        # has been presented during this session.
        self.controller.present_live_cue(self._presentation_cue("live"))

    def toggle_text_hidden(self):
        self.text_hidden = not self.text_hidden
        self.hide_text_button.configure(
            text=(
                "Show Text   Ctrl+T"
                if self.text_hidden
                else "Hide Text   Ctrl+T"
            ),
            bg=self.COLORS["danger"] if self.text_hidden else self.COLORS["neutral"],
            activebackground=(
                self.COLORS["danger"]
                if self.text_hidden
                else self.COLORS["neutral"]
            ),
        )
        self._draw_preview()
        self._publish_live_view()
        self.set_status(
            "Lyrics hidden in Preview and Live View. Stage View is unchanged."
            if self.text_hidden
            else "Lyrics shown in Preview and Live View. Stage View is unchanged."
        )

    def toggle_freeze(self):
        if not self.presentation_active:
            self.set_status("Turn Present on before freezing the audience outputs.")
            return
        self.output_frozen = not self.output_frozen
        self._update_present_controls()
        self._update_live_control_buttons()
        if self.output_frozen:
            self._draw_preview()
            self.set_status(
                "Live View and Stage View are frozen. Slide navigation now moves Preview only."
            )
            return
        self._present_selected_slide()
        self.set_status(
            "Outputs unfrozen. The selected Preview slide is now live on both views."
        )

    def _update_live_control_buttons(self):
        if hasattr(self, "freeze_button"):
            self.freeze_button.configure(
                text="Unfreeze   Ctrl+R" if self.output_frozen else "Freeze   Ctrl+R",
                bg=self.COLORS["warning"] if self.output_frozen else self.COLORS["neutral"],
                activebackground=(
                    self.COLORS["warning"]
                    if self.output_frozen
                    else self.COLORS["neutral"]
                ),
            )
        if hasattr(self, "hide_text_button"):
            self.hide_text_button.configure(
                text="Show Text   Ctrl+T" if self.text_hidden else "Hide Text   Ctrl+T",
                bg=self.COLORS["danger"] if self.text_hidden else self.COLORS["neutral"],
                activebackground=(
                    self.COLORS["danger"]
                    if self.text_hidden
                    else self.COLORS["neutral"]
                ),
            )
        if hasattr(self, "logo_button"):
            self.logo_button.configure(
                text="Hide Logo   Ctrl+Q" if self.logo_visible else "Show Logo   Ctrl+Q",
                bg=self.COLORS["purple"] if self.logo_visible else self.COLORS["purple"],
                activebackground=self.COLORS["purple"],
            )

    def toggle_logo(self):
        if not self.live_display_settings.get("logoData"):
            self.set_status("Choose a church logo in Live Style first.")
            return
        self.logo_visible = not self.logo_visible
        self.logo_button.configure(
            text=(
                "Hide Logo   Ctrl+Q"
                if self.logo_visible
                else "Show Logo   Ctrl+Q"
            ),
            bg=self.COLORS["purple"] if self.logo_visible else self.COLORS["neutral"],
            activebackground=(
                self.COLORS["purple"]
                if self.logo_visible
                else self.COLORS["neutral"]
            ),
        )
        self._draw_preview()
        self._publish_live_view()
        self.set_status(
            "Church logo shown in Preview and Live View; lyrics are hidden."
            if self.logo_visible
            else "Church logo hidden in Preview and Live View."
        )

    def prompt_stage_message(self):
        message = simpledialog.askstring(
            "Send Custom Stage Message",
            "Message for the Stage View:",
            parent=self,
        )
        if message is not None:
            self.send_stage_message(message)

    def clear_stage_view(self):
        self.stage_mode = "CLEAR"
        self.stage_message = ""
        self._update_stage_state()
        self._publish_stage_view()
        self.set_status("Stage View cleared. Preview and Live View are unchanged.")

    def send_stage_message(self, message=None):
        if message is None:
            message = self.stage_message_var.get() if hasattr(self, "stage_message_var") else ""
        message = str(message).strip()
        if not message:
            self.set_status("Enter a Stage View message first.")
            if hasattr(self, "stage_message_entry"):
                self.stage_message_entry.focus_set()
            return
        if len(message) > 500:
            self.set_status("Stage View messages must be 500 characters or fewer.")
            return
        if hasattr(self, "stage_message_var"):
            self.stage_message_var.set(message)
        self.stage_mode = "MESSAGE"
        self.stage_message = message
        self._update_stage_state()
        self._publish_stage_view()
        self.set_status(
            f"Stage View message sent: {message}. Preview and Live View are unchanged."
        )

    def _update_stage_state(self):
        if not hasattr(self, "stage_state_var"):
            return
        if self.stage_mode == "CLEAR":
            text = "Stage: clear"
        elif self.stage_mode == "MESSAGE":
            shortened = self.stage_message[:24]
            text = f"Stage: {shortened}{'…' if len(self.stage_message) > 24 else ''}"
        elif self.stage_mode == "LYRICS":
            text = "Stage: following lyrics"
        else:
            text = "Stage: ready"
        self.stage_state_var.set(text)

    def _apply_display_settings(self):
        if not self.church_id():
            return
        self.live_display_settings = self.db.get_display_settings(
            self.church_id(), "live"
        )
        self.stage_display_settings = self.db.get_display_settings(
            self.church_id(), "stage"
        )
        self.message_display_settings = self.db.get_display_settings(
            self.church_id(), "message"
        )
        # Keep the hosted viewer aligned even when settings arrived through cloud sync.
        self.controller.publish_stage_view_styles()
        # Retain the existing name for slide generation and the in-app Preview.
        self.display_settings = self.live_display_settings
        self._rebuild_slides()
        self._draw_preview()

    def _draw_preview(self):
        if not hasattr(self, "preview_canvas") or not self.display_settings:
            return
        self.preview_renderer.render(self._presentation_cue("live", preview=True))
        state = "PRESENT ON" if self.presentation_active else "PREVIEW"
        if self.output_frozen:
            state += " · FROZEN"
        if self.logo_visible:
            state += " · LOGO"
        elif self.text_hidden:
            state += " · TEXT HIDDEN"
        self.output_mode_var.set(f"● {state}")
        if self.preview_slide:
            self.preview_caption_var.set(f"Selected: {self.preview_slide.label}")
        else:
            self.preview_caption_var.set("Select a slide to preview it")

    def _layout_preview_canvas(self, _event=None):
        if not hasattr(self, "preview_canvas_frame"):
            return
        inset = px(8)
        available_width = max(160, self.preview_canvas_frame.winfo_width() - inset)
        available_height = max(90, self.preview_canvas_frame.winfo_height() - inset)
        aspect = 16 / 9
        if available_width / available_height > aspect:
            canvas_height = available_height
            canvas_width = round(canvas_height * aspect)
        else:
            canvas_width = available_width
            canvas_height = round(canvas_width / aspect)
        if (
            int(float(self.preview_canvas.cget("width"))) != canvas_width
            or int(float(self.preview_canvas.cget("height"))) != canvas_height
        ):
            self.preview_canvas.configure(width=canvas_width, height=canvas_height)

    def _upcoming_slides_after_live(self, count):
        """Return upcoming cues, continuing into later songs in the agenda."""

        if self.live_slide is None:
            return []
        try:
            index = self.slides.index(self.live_slide)
        except ValueError:
            return []
        limit = max(0, count)
        upcoming = [
            {"slide": slide, "title": self.live_song_title}
            for slide in self.slides[index + 1 : index + 1 + limit]
        ]
        if len(upcoming) >= limit:
            return upcoming

        selection = self.agenda_list.curselection()
        if not selection:
            return upcoming
        max_lines = int(self.live_display_settings.get("maxLinesPerSlide", 4))
        for item in self.service_items[selection[0] + 1 :]:
            if item.get("_kind") != "song":
                continue
            title = str(item.get("title") or "Untitled")
            segments = normalize_segments(item.get("segments"), item.get("lyrics", ""))
            for slide in build_segment_slides(segments, max_lines):
                upcoming.append({"slide": slide, "title": title})
                if len(upcoming) >= limit:
                    return upcoming
        return upcoming

    @staticmethod
    def _stage_lyric_text(title, segment, lyrics):
        """Include cue context for Stage View clients that render only text."""

        heading = " · ".join(part for part in (title, segment) if part)
        return f"{heading}\n\n{lyrics}" if heading and lyrics else heading or lyrics

    def _presentation_cue(self, view_type="live", preview=False):
        is_stage = view_type == "stage"
        settings = (
            self.stage_display_settings if is_stage else self.live_display_settings
        )
        slide = self.preview_slide if preview else self.live_slide
        title = (
            self._current_content_title()
            if preview
            else self.live_song_title
        )
        if preview:
            # Preview always follows selection and mirrors Live-only visibility controls.
            mode = "LIVE" if slide else "READY"
            text_hidden = self.text_hidden or self.logo_visible
        elif is_stage:
            if self.stage_mode == "CLEAR":
                mode = "CLEAR"
                slide = None
            elif self.stage_mode == "MESSAGE":
                mode = "LIVE"
                slide = None
                title = "Stage message"
                settings = self.message_display_settings
            else:
                mode = "LIVE" if slide else "READY"
            # Hide Text and Logo are intentionally local-only controls.
            text_hidden = False
        else:
            mode = self.live_mode
            text_hidden = self.text_hidden or self.logo_visible
        if is_stage and not preview and self.stage_mode == "LYRICS":
            try:
                upcoming_count = int(
                    settings.get(
                        "nextSlideCount",
                        1 if settings.get("showNextSlide", False) else 0,
                    )
                )
            except (TypeError, ValueError):
                upcoming_count = 0
            upcoming = self._upcoming_slides_after_live(
                max(0, min(5, upcoming_count))
            )
        else:
            upcoming = []
        next_cue = upcoming[0] if upcoming else None
        next_slide = next_cue["slide"] if next_cue else None
        next_title = next_cue["title"] if next_cue else ""
        if is_stage and not preview and self.stage_mode == "MESSAGE":
            cue_text = self.stage_message
            segment = "Message"
        else:
            cue_text = slide.text if slide and mode not in {"CLEAR", "READY"} else ""
            segment = slide.label if slide else ""
            if is_stage and cue_text:
                cue_text = self._stage_lyric_text(title, segment, cue_text)
        return {
            "version": 2,
            "kind": "message" if is_stage and self.stage_mode == "MESSAGE" else "lyrics",
            "title": title,
            "segment": segment,
            "text": cue_text,
            "imageData": (
                str(getattr(slide, "image_data", ""))
                if slide and mode not in {"CLEAR", "READY"}
                else ""
            ),
            "nextText": (
                self._stage_lyric_text(next_title, next_slide.label, next_slide.text)
                if next_slide and settings.get("showNextSlide", False)
                else ""
            ),
            "nextTitle": next_title,
            "nextSegment": next_slide.label if next_slide else "",
            "upcomingSlides": [
                {
                    "title": item["title"],
                    "segment": item["slide"].label,
                    "lyrics": item["slide"].text,
                    "text": self._stage_lyric_text(
                        item["title"], item["slide"].label, item["slide"].text
                    ),
                }
                for item in upcoming
            ],
            "mode": mode,
            "textHidden": text_hidden,
            "logoVisible": self.logo_visible and not is_stage,
            "settings": settings,
        }

    @staticmethod
    def _stage_slide_value(slide: Any) -> str:
        image_data = str(getattr(slide, "image_data", "") or "")
        if image_data:
            return image_data
        return str(getattr(slide, "text", "") or "")

    def _stage_database_payload(self) -> dict[str, Any]:
        """Build the small, mode-specific Firebase Stage View payload."""

        if self.stage_mode == "CLEAR":
            return {}
        if self.stage_mode == "MESSAGE":
            return {"Message": self.stage_message} if self.stage_message else {}
        if not self.presentation_active or self.live_slide is None:
            return {}

        image_data = str(getattr(self.live_slide, "image_data", "") or "")
        if image_data:
            return {"Current presentation image": image_data}

        current_lyrics = str(getattr(self.live_slide, "text", "") or "")
        if not current_lyrics:
            return {}

        current_slide: dict[str, str] = {
            "Lyrics": current_lyrics,
            "Song Title": str(self.live_song_title or ""),
            "Section Type": str(getattr(self.live_slide, "label", "") or ""),
        }
        current_slide = {key: value for key, value in current_slide.items() if value}

        try:
            next_count = max(
                0, min(5, int(self.stage_display_settings.get("nextSlideCount", 0)))
            )
        except (TypeError, ValueError):
            next_count = 0

        upcoming_payload: dict[str, dict[str, str]] = {}
        previous_title = str(self.live_song_title or "")
        for index, upcoming in enumerate(
            self._upcoming_slides_after_live(next_count), start=1
        ):
            slide = upcoming["slide"]
            lyrics = str(getattr(slide, "text", "") or "")
            if not lyrics:
                continue
            item: dict[str, str] = {"Lyrics": lyrics}
            title = str(upcoming.get("title") or "")
            # Publish a title only on the first upcoming slide after a song
            # transition. Later slides of that same song remain lyrics-only.
            if title and title != previous_title:
                item["Song Title"] = title
            if title:
                previous_title = title
            upcoming_payload[f"Slide {index}"] = item

        payload: dict[str, Any] = {"Current Slide": current_slide}
        if upcoming_payload:
            payload["Upcoming Slides"] = upcoming_payload
        return payload

    def _publish_presented_slide(self):
        self.controller.present_live_cue(self._presentation_cue("live"))
        self._publish_stage_view()

    def _publish_live_view(self):
        self.controller.present_live_cue(self._presentation_cue("live"))

    def _publish_stage_view(self):
        self.controller.publish_stage_cue(self._stage_database_payload())

    # ------------------------------------------------------------------
    # Songbook and deletion/publishing actions
    # ------------------------------------------------------------------

    def add_songbook(self):
        self._songbook_dialog()

    def edit_songbook(self):
        book_id = self.selected_songbook_id()
        book = self.book_by_id.get(book_id or "")
        if not book:
            self.set_status("Select a songbook first.")
            return
        self._songbook_dialog(book)

    def _songbook_dialog(self, songbook=None):
        popup = tk.Toplevel(self)
        popup.title("Rename Songbook" if songbook else "New Songbook")
        popup.geometry(f"{px(410)}x{px(190)}")
        popup.configure(bg=PALETTE["surface"])
        popup.transient(self)
        popup.grab_set()
        frame = tk.Frame(popup, bg=PALETTE["surface"], padx=px(22), pady=px(20))
        frame.pack(fill="both", expand=True)
        tk.Label(
            frame,
            text="Songbook name",
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=ui_font(9),
        ).pack(anchor="w")
        entry = style_entry(tk.Entry(frame, font=ui_font(11)))
        entry.insert(0, songbook["name"] if songbook else "")
        entry.pack(fill="x", pady=(px(5), px(10)), ipady=px(4))
        error = tk.Label(
            frame, text="", bg=PALETTE["surface"], fg=PALETTE["danger"]
        )
        error.pack(anchor="w")

        def save():
            try:
                self.db.save_songbook(
                    self.church_id(), entry.get(), songbook["id"] if songbook else None
                )
            except ValueError as exc:
                error.config(text=str(exc))
                return
            popup.destroy()
            self.refresh()
            self.set_status("Songbook saved locally — ready to sync.")

        self._button(frame, "Save Locally", save, "accent", True, "save").pack(
            anchor="e", pady=(px(8), 0)
        )
        entry.focus_set()

    def delete_selected(self):
        song = self.selected_song()
        if song and self.selected_library_song_id():
            if messagebox.askyesno("Delete Song", f"Delete '{song['title']}'?"):
                self.db.delete_entity(self.church_id(), "song", song["id"])
                self.refresh()
            return
        book_id = self.selected_songbook_id()
        book = self.book_by_id.get(book_id or "")
        if book and messagebox.askyesno(
            "Delete Songbook", f"Delete '{book['name']}'? Its songs become unfiled."
        ):
            for song in self.songs:
                if song.get("songbook_id") == book["id"]:
                    self.db.save_song(
                        church_id=self.church_id(),
                        title=song["title"],
                        lyrics=song["lyrics"],
                        songbook_id=None,
                        song_id=song["id"],
                        author=song.get("author", ""),
                        copyright=song.get("copyright", ""),
                        ccli_number=song.get("ccli_number", ""),
                        segments=song.get("segments", []),
                    )
            self.db.delete_entity(self.church_id(), "songbook", book["id"])
            self.refresh()

    def publish_songbook(self):
        book = self.book_by_id.get(self.selected_songbook_id() or "")
        if not book:
            self.set_status("Select a songbook to publish.")
            return
        self.controller.publish_to_global("songbook", book["id"])

    def publish_song(self):
        song = self.selected_song()
        if not song:
            self.set_status("Select a song to publish.")
            return
        self.controller.publish_to_global("song", song["id"])

    # ------------------------------------------------------------------
    # VideoPsalm-style keyboard controls
    # ------------------------------------------------------------------

    def _bind_shortcuts(self):
        self.controller.bind_all("<Control-f>", self._focus_search, add="+")
        self.controller.bind_all("<Insert>", self._add_shortcut, add="+")
        self.controller.bind_all("<Pause>", self._add_shortcut, add="+")
        self.controller.bind_all("<Alt-a>", self._add_shortcut, add="+")
        self.controller.bind_all("<F5>", lambda _event: self._active_shortcut(self.start_presentation), add="+")
        self.controller.bind_all("<Control-s>", lambda _event: self._active_shortcut(self.save_all_local), add="+")
        for sequence, command in (
            ("<Control-p>", self.toggle_present_mode),
            ("<Control-r>", self.toggle_freeze),
            ("<Control-t>", self.toggle_text_hidden),
            ("<Control-q>", self.toggle_logo),
            ("<Prior>", self.previous_live),
            ("<Next>", self.next_live),
            ("<Home>", self.restart_song),
        ):
            self.controller.bind_all(
                sequence,
                lambda event, action=command: self._output_shortcut(event, action),
                add="+",
            )
        for key in tuple("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ123456789"):
            self.controller.bind_all(
                f"<KeyPress-{key}>",
                lambda event, shortcut=key: self._segment_shortcut(event, shortcut),
                add="+",
            )

    def _is_active_page(self):
        return getattr(self.controller, "current_page_name", None) == "ControlCenterPage"

    def _focus_search(self, _event=None):
        if not self._is_active_page():
            return None
        try:
            library_tab = self.library_notebook.tab(
                self.library_notebook.select(), "text"
            )
        except tk.TclError:
            library_tab = "Songs"
        entry = self.bible_reference_entry if library_tab == "Bible" else self.search_entry
        entry.focus_set()
        entry.selection_range(0, tk.END)
        return "break"

    def _add_shortcut(self, _event=None):
        if not self._is_active_page():
            return None
        try:
            library_tab = self.library_notebook.tab(
                self.library_notebook.select(), "text"
            )
        except tk.TclError:
            library_tab = "Songs"
        if library_tab == "Bible":
            self.add_bible_to_service()
        else:
            self.add_to_service()
        return "break"

    def _active_shortcut(self, command):
        if not self._is_active_page():
            return None
        command()
        return "break"

    def save_all_local(self):
        self._save_service_plan(False)
        if self.agenda_dirty and not self.save_agenda():
            return False
        if self.editor_dirty:
            return self.save_editor()
        self.set_status("The agenda file and current song are saved locally.")
        return True

    def _segment_shortcut(self, event, shortcut):
        # A modified word-processing shortcut such as Ctrl+A must never be
        # interpreted as a presentation segment command.
        if (
            not self._is_active_page()
            or self._is_text_input_widget(event.widget)
            or int(getattr(event, "state", 0)) & 0x000C
        ):
            return None
        if self.active_content_kind != "song":
            return None
        shortcut = shortcut.casefold()
        segment_shortcuts = {
            "v": "verse",
            "c": "chorus",
            "b": "bridge",
            "p": "pre_chorus",
            "i": "intro",
            "t": "tag",
            "o": "ending",
            "e": "ending",
        }
        if shortcut.isdigit():
            self._cycle_segment_slides("verse", int(shortcut))
        elif shortcut in segment_shortcuts:
            self._cycle_segment_slides(segment_shortcuts[shortcut])
        else:
            self._cycle_custom_segment_slides(shortcut)
        return "break"

    def _output_shortcut(self, event, command):
        if not self._is_active_page() or self._is_song_editor_widget(event.widget):
            return None
        command()
        return "break"

    def _is_song_editor_widget(self, widget):
        current = widget
        while current is not None:
            if current is getattr(self, "song_editor_panel", None):
                return True
            current = getattr(current, "master", None)
        return False

    def _is_text_input_widget(self, widget):
        if self._is_song_editor_widget(widget):
            return True
        return isinstance(
            widget,
            (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox, ttk.Spinbox),
        )

    def _cycle_segment_slides(self, segment_type, verse_number=None):
        selection = self.slide_list.curselection()
        current = selection[0] if selection else None
        target_index = next_matching_segment_slide(
            self.slides,
            self.editor_segments,
            segment_type,
            current,
            verse_number,
        )
        if target_index is None:
            target = f"Verse {verse_number}" if verse_number is not None else segment_type.title()
            self.set_status(f"{target} is not present in the current song.")
            return
        self._select_preview_slide(target_index)

    def _cycle_custom_segment_slides(self, initial):
        matching_segments = {
            index
            for index, segment in enumerate(self.editor_segments)
            if str(segment.get("type", "")).casefold() == "custom"
            and str(segment.get("label", "")).strip().casefold().startswith(initial)
        }
        matches = [
            index
            for index, slide in enumerate(self.slides)
            if slide.segment_index in matching_segments
        ]
        if not matches:
            return
        selection = self.slide_list.curselection()
        current = selection[0] if selection else None
        target = (
            matches[(matches.index(current) + 1) % len(matches)]
            if current in matches
            else matches[0]
        )
        self._select_preview_slide(target)

    @staticmethod
    def _shortcut(command):
        command()
        return "break"

    def show_shortcuts(self):
        messagebox.showinfo(
            "Stage Cue Keyboard Shortcuts",
            "Ctrl+F   Focus the active Songs/Bible search\n"
            "Esc   Clear song search\n"
            "Insert / Pause / Alt+A   Add selected song or Bible passage to agenda\n"
            "Delete   Remove selected agenda item\n"
            "Ctrl+PageUp / Ctrl+PageDown   Reorder agenda item\n"
            "Ctrl+S   Save the current song and agenda\n"
            "F5   Start at the beginning of the service\n"
            "Up / PageUp   Previous slide\n"
            "Down / PageDown   Next slide (uses the agenda item's After transition)\n"
            "Home   Restart the current song or Bible passage\n"
            "+ / -   Next or previous agenda item\n"
            "V   Next Verse slide\n"
            "1–9   Corresponding Verse; press again for its next split slide\n"
            "C   Chorus; press again for the next Chorus slide\n"
            "B / P / I / T / O   Bridge / Pre-Chorus / Intro / Tag / Outro\n"
            "Other letters   Custom segment beginning with that letter\n"
            "Ctrl+P   Toggle Present mode on or off\n"
            "When Present is on, navigation immediately follows live\n"
            "Ctrl+R   Freeze or unfreeze Live View and Stage View\n"
            "Ctrl+T   Hide or show text in Preview and Live View only\n"
            "Ctrl+Q   Show or hide the Live View church logo\n"
            "Stage View messages and Clear use the Stage controls\n",
            parent=self,
        )
