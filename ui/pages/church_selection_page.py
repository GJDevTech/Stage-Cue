import tkinter as tk

from ui.theme import (
    PALETTE,
    icon_size,
    modern_button,
    px,
    style_entry,
    ui_font,
)


class ChurchSelectionPage(tk.Frame):
    def __init__(self, parent, controller, auth_service, db_service):
        super().__init__(parent, bg=PALETTE["canvas"])
        self.controller = controller
        self.memberships = []
        self.available = []

        header = tk.Frame(self, bg=PALETTE["navy"], padx=px(24), pady=px(16))
        header.pack(fill="x")
        self.account_var = tk.StringVar(value="Choose a church")
        tk.Label(
            header,
            textvariable=self.account_var,
            font=ui_font(18, "bold"),
            bg=PALETTE["navy"],
            fg="white",
        ).pack(side="left")
        modern_button(
            header,
            "Log Out",
            controller.logout,
            color=PALETTE["neutral"],
            hover_color=PALETTE["neutral_hover"],
            icon="logout",
            compact=True,
        ).pack(side="right")
        self.back_button = modern_button(
            header,
            "Back to Current Church",
            controller.return_to_control_center,
            color=PALETTE["primary"],
            hover_color=PALETTE["primary_hover"],
            icon="back",
            compact=True,
        )
        self.back_button.pack(side="right", padx=px(6))

        body = tk.Frame(self, bg=PALETTE["canvas"], padx=px(28), pady=px(26))
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(1, weight=1)

        self.status_var = tk.StringVar(value="Loading churches…")
        tk.Label(
            body,
            textvariable=self.status_var,
            bg=PALETTE["primary_soft"],
            fg=PALETTE["primary_soft_text"],
            anchor="w",
            padx=px(12),
            pady=px(8),
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(px(0), px(12)))

        member_panel = self._panel(body, "Your churches")
        member_panel.grid(row=1, column=0, sticky="nsew", padx=(px(0), px(8)))
        member_panel.grid_rowconfigure(1, weight=1)
        member_panel.grid_columnconfigure(0, weight=1)
        self.member_list = self._listbox(member_panel)
        self.member_list.grid(row=1, column=0, sticky="nsew", padx=px(12), pady=px(8))
        self.member_list.bind("<Double-Button-1>", lambda _event: self.open_selected())
        controls = tk.Frame(member_panel, bg=PALETTE["surface"])
        controls.grid(row=2, column=0, sticky="ew", padx=px(12), pady=(px(0), px(12)))
        self._button(controls, "Open", self.open_selected, PALETTE["primary"], "church").pack(
            side="left"
        )
        self._button(
            controls, "Register New Church", self.open_register_dialog, PALETTE["success"], "plus"
        ).pack(side="left", padx=px(6))

        available_panel = self._panel(body, "Other churches")
        available_panel.grid(row=1, column=1, sticky="nsew", padx=(px(8), px(0)))
        available_panel.grid_rowconfigure(1, weight=1)
        available_panel.grid_columnconfigure(0, weight=1)
        self.available_list = self._listbox(available_panel)
        self.available_list.grid(row=1, column=0, sticky="nsew", padx=px(12), pady=px(8))
        controls = tk.Frame(available_panel, bg=PALETTE["surface"])
        controls.grid(row=2, column=0, sticky="ew", padx=px(12), pady=(px(0), px(12)))
        self._button(
            controls, "Request Membership", self.request_selected, PALETTE["purple"], "users"
        ).pack(side="left")
        self._button(controls, "Refresh", controller.refresh_church_choices, PALETTE["neutral"], "refresh").pack(
            side="left", padx=px(6)
        )

    @staticmethod
    def _button(parent, text, command, color, icon=None):
        return modern_button(
            parent,
            text,
            command,
            color=color,
            icon=icon,
            compact=True,
        )

    @staticmethod
    def _listbox(parent):
        return tk.Listbox(
            parent,
            font=ui_font(10),
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            selectbackground=PALETTE["select_bg"],
            selectforeground=PALETTE["select_fg"],
            activestyle="none",
            exportselection=False,
        )

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
            font=ui_font(13, "bold"),
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
        ).grid(row=0, column=0, sticky="w", padx=px(12), pady=(px(12), px(4)))
        return panel

    def on_show(self):
        account = self.controller.current_account or {}
        self.account_var.set(
            f"{account.get('name', 'Account')}  ·  {account.get('email', '')}"
        )
        if self.controller.current_session:
            self.back_button.pack(side="right", padx=px(6))
        else:
            self.back_button.pack_forget()
        self.controller.refresh_church_choices()

    def show_choices(self, choices):
        self.memberships = choices["memberships"]
        self.available = choices["available"]
        self.member_list.delete(0, tk.END)
        for church in self.memberships:
            location = f" — {church['location']}" if church.get("location") else ""
            self.member_list.insert(
                tk.END, f"{church['name']}{location}  [{church['role']}]"
            )
        self.available_list.delete(0, tk.END)
        for church in self.available:
            location = f" — {church['location']}" if church.get("location") else ""
            self.available_list.insert(tk.END, f"{church['name']}{location}")
        pending_names = ", ".join(church["name"] for church in choices["pending"])
        if pending_names:
            self.status_var.set(f"Pending membership requests: {pending_names}")
        elif not self.memberships:
            self.status_var.set(
                "Register a church or request membership from an existing church."
            )
        else:
            self.status_var.set("Choose a church to continue.")

    def set_status(self, message):
        self.status_var.set(message)

    def open_selected(self):
        selection = self.member_list.curselection()
        if not selection:
            self.set_status("Select one of your churches.")
            return
        self.set_status("Opening church…")
        self.controller.select_church(self.memberships[selection[0]]["id"])

    def request_selected(self):
        selection = self.available_list.curselection()
        if not selection:
            self.set_status("Select a church first.")
            return
        church = self.available[selection[0]]
        self.set_status(f"Sending request to {church['name']}…")
        self.controller.request_membership(church["id"])

    def open_register_dialog(self):
        popup = tk.Toplevel(self)
        popup.title("Register Church")
        popup.geometry("410x280")
        popup.configure(bg=PALETTE["surface"])
        popup.transient(self)
        popup.grab_set()
        frame = tk.Frame(popup, bg=PALETTE["surface"], padx=px(28), pady=px(24))
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="Church name", bg=PALETTE["surface"]).pack(anchor="w")
        name_entry = style_entry(tk.Entry(frame, font=ui_font(10)))
        name_entry.pack(fill="x", pady=(px(4), px(12)), ipady=px(6))
        tk.Label(frame, text="Location", bg=PALETTE["surface"]).pack(anchor="w")
        location_entry = style_entry(tk.Entry(frame, font=ui_font(10)))
        location_entry.pack(fill="x", pady=(px(4), px(12)), ipady=px(6))
        status = tk.Label(
            frame, text="", bg=PALETTE["surface"], fg=PALETTE["warning"]
        )
        status.pack(anchor="w")

        def save():
            if not name_entry.get().strip():
                status.config(text="Church name is required.")
                return
            save_button.config(state="disabled")
            status.config(text="Registering church…")
            self.controller.register_church(
                name_entry.get(), location_entry.get(), popup, save_button, status
            )

        save_button = self._button(frame, "Register", save, PALETTE["success"], "check")
        save_button.pack(anchor="e", pady=(px(10), px(0)))
        name_entry.focus_set()
