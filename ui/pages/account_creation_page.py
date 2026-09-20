import tkinter as tk

from ui.theme import (
    PALETTE,
    create_icon,
    icon_size,
    modern_button,
    px,
    style_entry,
    ui_font,
)


class AccountCreationPage(tk.Frame):
    def __init__(self, parent, controller, auth_service, db_service):
        super().__init__(parent, bg=PALETTE["canvas"])
        self.controller = controller

        card = tk.Frame(
            self,
            bg=PALETTE["surface"],
            padx=px(48),
            pady=px(42),
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
        )
        card.place(relx=0.5, rely=0.5, anchor="center")

        account_icon = create_icon(card, "account", icon_size(30), PALETTE["primary"])
        tk.Label(card, image=account_icon, bg=PALETTE["surface"]).pack(anchor="w")
        tk.Label(
            card,
            text="Create your Stage Cue account",
            font=ui_font(22, "bold"),
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
        ).pack(anchor="w", pady=(px(14), px(6)))
        self.email_var = tk.StringVar()
        tk.Label(
            card,
            textvariable=self.email_var,
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
        ).pack(anchor="w", pady=(px(0), px(20)))
        tk.Label(
            card,
            text="DISPLAY NAME",
            font=ui_font(8, "bold"),
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
        ).pack(anchor="w")
        self.name_entry = style_entry(
            tk.Entry(card, width=42, font=ui_font(11))
        )
        self.name_entry.pack(fill="x", pady=(px(6), px(14)), ipady=px(8))
        self.status_var = tk.StringVar()
        tk.Label(
            card,
            textvariable=self.status_var,
            bg=PALETTE["surface"],
            fg=PALETTE["warning"],
            wraplength=px(360),
            justify="left",
        ).pack(anchor="w")
        self.create_button = modern_button(
            card,
            "Create Account",
            self.create_account,
            color=PALETTE["primary"],
            hover_color=PALETTE["primary_hover"],
            icon="check",
        )
        self.create_button.pack(anchor="e", pady=(px(14), px(0)))

    def on_show(self):
        profile = self.controller.pending_profile or {}
        self.email_var.set(profile.get("email", ""))
        self.name_entry.delete(0, tk.END)
        self.name_entry.insert(0, profile.get("name", ""))
        self.status_var.set("")
        self.create_button.config(state="normal")
        self.name_entry.focus_set()

    def create_account(self):
        name = self.name_entry.get().strip()
        if not name:
            self.status_var.set("Enter your name.")
            return
        self.create_button.config(state="disabled")
        self.status_var.set("Creating account…")
        self.controller.create_account(name)

    def show_error(self, message):
        self.create_button.config(state="normal")
        self.status_var.set(message)
