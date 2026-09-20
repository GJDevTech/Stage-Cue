import tkinter as tk

from ui.theme import (
    PALETTE,
    create_icon,
    icon_size,
    modern_button,
    px,
    ui_font,
)


class AuthPage(tk.Frame):
    def __init__(self, parent, controller, auth_service, db_service):
        super().__init__(parent, bg=PALETTE["canvas"])
        self.controller = controller

        shell = tk.Frame(self, bg=PALETTE["surface"])
        shell.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.82, relheight=0.72)

        hero = tk.Frame(shell, bg=PALETTE["navy"], padx=px(46), pady=px(44))
        hero.pack(side="left", fill="both", expand=True)
        hero_icon = create_icon(hero, "monitor", icon_size(36), "#60A5FA")
        tk.Label(hero, image=hero_icon, bg=PALETTE["navy"]).pack(anchor="w")
        tk.Label(
            hero,
            text="STAGE CUE",
            font=ui_font(28, "bold"),
            bg=PALETTE["navy"],
            fg="#FFFFFF",
        ).pack(anchor="w", pady=(px(18), px(6)))
        tk.Label(
            hero,
            text="Prepare locally. Present confidently.",
            font=ui_font(14),
            bg=PALETTE["navy"],
            fg="#BFDBFE",
        ).pack(anchor="w")
        for text in (
            "Offline-first song library",
            "Independent Live and Stage views",
            "Church-based collaboration",
        ):
            row = tk.Frame(hero, bg=PALETTE["navy"])
            row.pack(fill="x", anchor="w", pady=(24 if text.startswith("Offline") else 10, 0))
            check = create_icon(row, "check", icon_size(15), "#34D399")
            tk.Label(row, image=check, bg=PALETTE["navy"]).pack(side="left")
            tk.Label(
                row,
                text=text,
                font=ui_font(10),
                bg=PALETTE["navy"],
                fg="#E2E8F0",
            ).pack(side="left", padx=(px(10), px(0)))

        container = tk.Frame(shell, bg=PALETTE["surface"], padx=px(52), pady=px(48))
        container.pack(side="right", fill="both", expand=True)
        card = tk.Frame(
            container,
            bg=PALETTE["surface"],
        )
        card.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            card,
            text="Welcome back",
            font=ui_font(24, "bold"),
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
        ).pack(anchor="w", pady=(px(0), px(7)))
        tk.Label(
            card,
            text="Sign in once, then continue working offline whenever needed.",
            font=ui_font(10),
            wraplength=px(350),
            justify="left",
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
        ).pack(anchor="w", pady=(px(0), px(26)))

        self.login_button = modern_button(
            card,
            "Continue with Google",
            self.login,
            color="#4285F4",
            hover_color="#3367D6",
            icon="account",
        )
        self.login_button.pack(fill="x", pady=px(7), ipady=px(2))

        self.status_label = tk.Label(
            card,
            text="",
            wraplength=px(360),
            justify="left",
            bg=PALETTE["surface"],
            fg=PALETTE["warning"],
        )
        self.status_label.pack(anchor="w", pady=(px(14), px(0)))

    def on_show(self):
        self.set_busy(False)

    def set_status(self, message):
        self.status_label.config(text=message)

    def set_busy(self, busy):
        self.login_button.config(state="disabled" if busy else "normal")

    def login(self):
        self.set_busy(True)
        self.set_status("Opening Google login…")
        self.controller.login_online()
