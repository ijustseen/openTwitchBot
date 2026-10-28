"""Окно OpenBot: подключение, список команд и редактор скриптов."""

from __future__ import annotations

import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from app.bot import TwitchService, command_context, normalize_channel
from app.clipboard import ClipboardError, bind_paste, insert_text, read_clipboard, write_clipboard
from app.guide import PROMPT, open_wiki
from app.highlight import attach_highlight, refresh_highlight
from app.oauth import REDIRECT_URI, AuthError, login
from app.runner import run_script
from app.update import UpdateError, UpdateOffer, current_version, find_update, install_update, updates_supported
from app.store import (
    Command,
    Config,
    create_command,
    delete_command_file,
    load_config,
    parse_cooldown,
    read_script,
    save_config,
    validate_trigger,
    write_script,
)

PERM_LABELS = {
    "all": "Все",
    "mods": "Модераторы и стример",
}
PERM_BY_LABEL = {label: key for key, label in PERM_LABELS.items()}

MONO = "Menlo" if sys.platform == "darwin" else "Consolas"
BG = "#0c0f14"
CARD = "#161b24"
LINE = "#2a3344"
INK = "#e8eef8"
MUTED = "#8e9aaf"
ACCENT = "#4c8dff"
ACCENT_HOVER = "#3a74e0"
SOFT = "#222a38"
DANGER = "#d45454"
DANGER_HOVER = "#b84343"
DANGER_OFF = "#3a2428"
OK = "#3ddc84"
START = "#2f9e62"
START_HOVER = "#278554"
START_OFF = "#1a3326"
AI = "#6a5cff"
AI_HOVER = "#5748e6"


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("OpenBot")
        self.geometry("1180x860")
        self.minsize(1040, 760)
        self.configure(fg_color=BG)

        self.cfg: Config = load_config()
        self._closing = False
        self._status_kind = "idle"
        self._selected_id: str | None = None
        self._clean: tuple[str, str, str, str, str] | None = None
        self._building = False
        self._rows: dict[str, ctk.CTkFrame] = {}

        self.bot = TwitchService(self.append_log, self.set_status, self._on_thread_done, self._on_session)
        self.bot.update_config(self.cfg)
        self._build()
        self.refresh_list()
        if self.cfg.commands:
            self._show_command(self.cfg.commands[0].id)
        else:
            self._clear_editor()
        if self.cfg.nickname.strip():
            self.append_log(f"Окно готово. Аккаунт {self.cfg.nickname.strip()}. Укажите канал и нажмите «Старт».")
        else:
            self.append_log("Окно готово. Войдите через Twitch, укажите канал и нажмите «Старт».")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._update_offer: UpdateOffer | None = None
        self._updating = False
        if updates_supported():
            self.append_log(f"Версия {current_version()}.")
            self.after(300, self._check_updates)

    def _card(self, parent) -> ctk.CTkFrame:
        return ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16, border_width=1, border_color=LINE)

    def _ghost(self, parent, text: str, command, width: int = 0) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=34,
            fg_color="transparent",
            border_width=1,
            border_color=LINE,
            hover_color=SOFT,
            text_color=INK,
        )

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = self._card(self)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        header.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(header, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        top.grid_columnconfigure(1, weight=1)
        title = ctk.CTkFrame(top, fg_color="transparent")
        title.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(title, text="OpenBot", font=ctk.CTkFont(size=24, weight="bold"), text_color=INK).pack(
            side="left"
        )
        ctk.CTkLabel(title, text="чат-бот Twitch", text_color=MUTED).pack(side="left", padx=(10, 0))
        status = ctk.CTkFrame(top, fg_color=SOFT, corner_radius=12)
        status.grid(row=0, column=1, sticky="e")
        self.status_dot = ctk.CTkLabel(status, text="●", text_color=MUTED, width=16)
        self.status_dot.pack(side="left", padx=(10, 0), pady=6)
        self.status_label = ctk.CTkLabel(status, text="Не подключён", text_color=MUTED)
        self.status_label.pack(side="left", padx=(2, 12), pady=6)

        form = ctk.CTkFrame(header, fg_color="transparent")
        form.grid(row=1, column=0, sticky="ew", padx=16, pady=(12, 4))
        form.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(form, text="Канал", text_color=MUTED).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.channel_entry = ctk.CTkEntry(
            form, width=190, height=36, placeholder_text="чей чат слушать", border_color=LINE
        )
        self.channel_entry.grid(row=0, column=1, sticky="w")
        self.account_label = ctk.CTkLabel(form, text="Вход не выполнен", text_color=MUTED, anchor="w")
        self.account_label.grid(row=0, column=2, sticky="w", padx=(16, 8))
        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=0, column=4, sticky="e")
        self.login_btn = self._ghost(actions, "Войти через Twitch", self._open_login, 168)
        self.login_btn.pack(side="left", padx=(0, 8))
        self.wiki_btn = self._ghost(actions, "Вики", self._open_wiki, 72)
        self.wiki_btn.pack(side="left", padx=(0, 8))
        self.stop_btn = ctk.CTkButton(
            actions,
            text="Стоп",
            width=84,
            height=34,
            command=self._on_stop,
            state="disabled",
            fg_color=DANGER_OFF,
            hover_color=DANGER_HOVER,
            text_color=INK,
        )
        self.stop_btn.pack(side="left", padx=(0, 8))
        self.start_btn = ctk.CTkButton(
            actions,
            text="Старт",
            width=96,
            height=34,
            command=self._on_start,
            fg_color=START,
            hover_color=START_HOVER,
        )
        self.start_btn.pack(side="left")

        self.update_bar = ctk.CTkFrame(header, fg_color="#1c2a44", corner_radius=12)
        self.update_bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 0))
        self.update_bar.grid_columnconfigure(0, weight=1)
        self.update_label = ctk.CTkLabel(self.update_bar, text="", anchor="w", text_color=INK)
        self.update_label.grid(row=0, column=0, sticky="ew", padx=12, pady=8)
        self.update_later = self._ghost(self.update_bar, "Позже", self._dismiss_update, 80)
        self.update_later.grid(row=0, column=1, padx=4, pady=8)
        self.update_now = ctk.CTkButton(
            self.update_bar,
            text="Обновить",
            width=110,
            height=34,
            command=self._confirm_update,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
        )
        self.update_now.grid(row=0, column=2, padx=(4, 10), pady=8)
        self.update_bar.grid_remove()

        ctk.CTkLabel(
            header,
            text="Скрипт команды может делать на компьютере всё, что может Python от вашего имени.",
            text_color=MUTED,
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", padx=16, pady=(8, 14))

        if self.cfg.channel:
            self.channel_entry.insert(0, self.cfg.channel)
        self._refresh_account()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=18, pady=4)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        sidebar = self._card(body)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        sidebar.grid_rowconfigure(1, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)
        side_head = ctk.CTkFrame(sidebar, fg_color="transparent")
        side_head.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        side_head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(side_head, text="Команды", font=ctk.CTkFont(size=16, weight="bold"), text_color=INK).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkButton(
            side_head,
            text="Новая",
            width=78,
            height=32,
            command=self._new_command,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
        ).grid(row=0, column=1, sticky="e")
        self.list_frame = ctk.CTkScrollableFrame(sidebar, width=292, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))

        editor = self._card(body)
        editor.grid(row=0, column=1, sticky="nsew")
        editor.grid_columnconfigure(0, weight=1)
        editor.grid_rowconfigure(2, weight=1)

        meta = ctk.CTkFrame(editor, fg_color="transparent")
        meta.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        meta.grid_columnconfigure(1, weight=1)
        meta.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(meta, text="Команда", text_color=MUTED).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.trigger_entry = ctk.CTkEntry(meta, height=36, placeholder_text="!hello", border_color=LINE)
        self.trigger_entry.grid(row=0, column=1, sticky="ew", padx=(0, 16))
        ctk.CTkLabel(meta, text="О чём", text_color=MUTED).grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.desc_entry = ctk.CTkEntry(meta, height=36, placeholder_text="коротко для себя", border_color=LINE)
        self.desc_entry.grid(row=0, column=3, sticky="ew")
        ctk.CTkLabel(meta, text="Пауза, сек", text_color=MUTED).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        self.cooldown_entry = ctk.CTkEntry(meta, width=90, height=36, placeholder_text="0", border_color=LINE)
        self.cooldown_entry.grid(row=1, column=1, sticky="w", pady=(8, 0))
        ctk.CTkLabel(meta, text="Кто может", text_color=MUTED).grid(row=1, column=2, sticky="w", padx=(0, 8), pady=(8, 0))
        self.perm_menu = ctk.CTkOptionMenu(
            meta, values=list(PERM_LABELS.values()), width=220, height=36, fg_color=SOFT, button_color=SOFT
        )
        self.perm_menu.grid(row=1, column=3, sticky="w", pady=(8, 0))

        ctk.CTkLabel(editor, text="Код  ·  def run(ctx)", anchor="w", text_color=MUTED).grid(
            row=1, column=0, sticky="w", padx=16, pady=(10, 0)
        )
        self.script_box = ctk.CTkTextbox(
            editor,
            font=ctk.CTkFont(family=MONO, size=13),
            wrap="none",
            undo=True,
            autoseparators=True,
            maxundo=80,
            fg_color="#10141b",
            border_width=1,
            border_color=LINE,
        )
        self.script_box.grid(row=2, column=0, sticky="nsew", padx=16, pady=8)
        bind_paste(self, self.script_box)
        attach_highlight(self.script_box)

        buttons = ctk.CTkFrame(editor, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        buttons.grid_columnconfigure(5, weight=1)
        self.save_btn = ctk.CTkButton(
            buttons, text="Сохранить", height=34, command=self._save_editor, fg_color=ACCENT, hover_color=ACCENT_HOVER
        )
        self.save_btn.grid(row=0, column=0, padx=(0, 6))
        self.test_btn = self._ghost(buttons, "Проверить", self._test_command, 110)
        self.test_btn.grid(row=0, column=1, padx=6)
        self.paste_btn = self._ghost(buttons, "Вставить", self._paste_script, 96)
        self.paste_btn.grid(row=0, column=2, padx=6)
        self.prompt_btn = ctk.CTkButton(
            buttons,
            text="Для нейросети",
            width=150,
            height=34,
            command=self._copy_prompt,
            fg_color=AI,
            hover_color=AI_HOVER,
        )
        self.prompt_btn.grid(row=0, column=3, padx=6)
        self.delete_btn = ctk.CTkButton(
            buttons,
            text="Удалить",
            height=34,
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            command=self._delete_command,
        )
        self.delete_btn.grid(row=0, column=6, sticky="e")

        log_frame = self._card(self)
        log_frame.grid(row=2, column=0, sticky="ew", padx=18, pady=(8, 16))
        log_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_frame, text="Журнал", anchor="w", text_color=MUTED).grid(
            row=0, column=0, sticky="w", padx=14, pady=(10, 0)
        )
        self.log_box = ctk.CTkTextbox(
            log_frame,
            height=108,
            font=ctk.CTkFont(family=MONO, size=12),
            state="disabled",
            fg_color="#10141b",
            border_width=0,
        )
        self.log_box.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 10))

    def append_log(self, text: str) -> None:
        if self._closing:
            return

        def _write() -> None:
            if self._closing or not self.winfo_exists():
                return
            from time import strftime

            self.log_box.configure(state="normal")
            for line in text.splitlines() or [""]:
                self.log_box.insert("end", f"[{strftime('%H:%M:%S')}] {line}\n")
            line_count = int(self.log_box.index("end-1c").split(".")[0])
            if line_count > 400:
                self.log_box.delete("1.0", f"{line_count - 400}.0")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.after(0, _write)

    def set_status(self, kind: str, detail: str = "") -> None:
        if self._closing:
            return

        self._status_kind = kind

        def _apply() -> None:
            if self._closing or not self.winfo_exists():
                return
            self._status_kind = kind
            colors = {
                "idle": (MUTED, "Не подключён"),
                "connecting": ("#e6b35a", "Подключение…"),
                "online": (OK, "В сети"),
                "stopping": ("#e6b35a", "Отключение…"),
                "error": ("#ff6b6b", "Ошибка"),
            }
            color, label = colors.get(kind, colors["idle"])
            if kind == "error" and detail:
                label = f"Ошибка: {detail}"
            self.status_label.configure(text=label[:90], text_color=color)
            self.status_dot.configure(text_color=color)
            alive = self.bot.running()
            can_start = not alive and kind not in ("connecting", "online", "stopping")
            self.start_btn.configure(
                state="normal" if can_start else "disabled",
                fg_color=START if can_start else START_OFF,
                hover_color=START_HOVER,
            )
            can_stop = alive and kind == "online"
            self.stop_btn.configure(
                state="normal" if can_stop else "disabled",
                fg_color=DANGER if can_stop else DANGER_OFF,
                hover_color=DANGER_HOVER,
            )

        self.after(0, _apply)

    def _on_thread_done(self, error: BaseException | None) -> None:
        if error:
            self.set_status("error", str(error))
            return
        self.set_status("idle", "")
        self.append_log("Отключено")

    def _on_session(self, session) -> None:
        def _apply() -> None:
            if self._closing or not self.winfo_exists():
                return
            self.cfg.token = session.token
            self.cfg.refresh_token = session.refresh_token
            if session.login:
                self.cfg.nickname = session.login
            self._refresh_account()
            save_config(self.cfg)

        self.after(0, _apply)

    def _open_login(self) -> None:
        if getattr(self, "_login_dialog", None) is not None:
            try:
                self._login_dialog.focus()
                return
            except Exception:
                self._login_dialog = None
        self._login_dialog = LoginDialog(self)
        self._login_dialog.protocol("WM_DELETE_WINDOW", self._close_login)

    def _close_login(self) -> None:
        dialog = getattr(self, "_login_dialog", None)
        self._login_dialog = None
        if dialog is not None:
            dialog.cancel.set()
            dialog.destroy()

    def apply_login(self, client_id: str, client_secret: str, session) -> None:
        self.cfg.client_id = client_id
        self.cfg.client_secret = client_secret
        self.cfg.token = session.token
        self.cfg.refresh_token = session.refresh_token
        if session.login:
            self.cfg.nickname = session.login
        self._refresh_account()
        save_config(self.cfg)
        self.bot.update_config(self.cfg)
        who = session.login or "аккаунт"
        self.append_log(f"Вход выполнен: {who}. Можно нажимать «Старт».")
        self._close_login()

    def _refresh_account(self) -> None:
        nick = self.cfg.nickname.strip()
        if nick:
            self.account_label.configure(text=f"Аккаунт: {nick}", text_color=OK)
            self.login_btn.configure(text="Сменить аккаунт")
        else:
            self.account_label.configure(text="Вход не выполнен", text_color=MUTED)
            self.login_btn.configure(text="Войти через Twitch")

    def refresh_list(self) -> None:
        self._building = True
        try:
            for child in self.list_frame.winfo_children():
                child.destroy()
            self._rows.clear()
            if not self.cfg.commands:
                ctk.CTkLabel(
                    self.list_frame,
                    text="Пока пусто.\nНажмите «Новая».",
                    text_color=MUTED,
                    justify="left",
                ).pack(anchor="w", padx=8, pady=12)
                return
            for command in self.cfg.commands:
                row = ctk.CTkFrame(self.list_frame, fg_color=SOFT, corner_radius=12)
                row.pack(fill="x", padx=4, pady=4)
                row.grid_columnconfigure(0, weight=1)
                title = ctk.CTkLabel(
                    row,
                    text=command.trigger,
                    anchor="w",
                    font=ctk.CTkFont(size=14, weight="bold"),
                    text_color=INK if command.enabled else MUTED,
                )
                title.grid(row=0, column=0, sticky="w", padx=10, pady=(8, 0))
                switch = ctk.CTkSwitch(row, text="", width=46, progress_color=ACCENT)
                if command.enabled:
                    switch.select()
                else:
                    switch.deselect()
                switch.configure(command=lambda cid=command.id: self._toggle(cid))
                switch.grid(row=0, column=1, padx=(4, 8), pady=(8, 0))
                description = command.description.strip() or "без описания"
                desc = ctk.CTkLabel(
                    row, text=description, anchor="w", text_color=MUTED, wraplength=210, justify="left"
                )
                desc.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))
                for widget in (row, title, desc):
                    widget.bind("<Button-1>", lambda _event, cid=command.id: self._select(cid))
                self._rows[command.id] = row
        finally:
            self._building = False
        self._paint_selection()

    def _paint_selection(self) -> None:
        for command_id, row in self._rows.items():
            if command_id == self._selected_id:
                row.configure(fg_color="#1c3358", border_width=1, border_color=ACCENT)
            else:
                row.configure(fg_color=SOFT, border_width=0)

    def _find(self, command_id: str | None) -> Command | None:
        for command in self.cfg.commands:
            if command.id == command_id:
                return command
        return None

    def _snapshot(self) -> tuple[str, str, str, str, str]:
        return (
            self.trigger_entry.get(),
            self.desc_entry.get(),
            self.cooldown_entry.get(),
            self.perm_menu.get(),
            self.script_box.get("1.0", "end-1c"),
        )

    def _confirm_discard(self) -> bool:
        if self._selected_id is None or self._snapshot() == self._clean:
            return True
        answer = messagebox.askyesnocancel(
            "Несохранённые изменения",
            "Сохранить текущую команду перед переходом?",
        )
        if answer is None:
            return False
        if answer:
            return self._save_editor()
        return True

    def _select(self, command_id: str) -> None:
        if command_id == self._selected_id:
            return
        if not self._confirm_discard():
            return
        self._show_command(command_id)

    def _show_command(self, command_id: str) -> None:
        command = self._find(command_id)
        if command is None:
            return
        self._selected_id = command_id
        self._set_entry(self.trigger_entry, command.trigger)
        self._set_entry(self.desc_entry, command.description)
        self._set_entry(self.cooldown_entry, _format_cooldown(command.cooldown_sec))
        self.perm_menu.set(PERM_LABELS.get(command.permission, PERM_LABELS["all"]))
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", read_script(command))
        self._refresh_highlight()
        self._clean = self._snapshot()
        self._paint_selection()

    def _clear_editor(self) -> None:
        self._selected_id = None
        self._set_entry(self.trigger_entry, "")
        self._set_entry(self.desc_entry, "")
        self._set_entry(self.cooldown_entry, "")
        self.perm_menu.set(PERM_LABELS["all"])
        self.script_box.delete("1.0", "end")
        self._refresh_highlight()
        self._clean = self._snapshot()
        self._paint_selection()

    def _set_entry(self, entry: ctk.CTkEntry, value: str) -> None:
        entry.delete(0, "end")
        if value:
            entry.insert(0, value)

    def _toggle(self, command_id: str) -> None:
        if self._building:
            return
        command = self._find(command_id)
        if command is None:
            return
        command.enabled = not command.enabled
        save_config(self.cfg)
        self.bot.update_config(self.cfg)
        state = "включена" if command.enabled else "выключена"
        self.append_log(f"{command.trigger} {state}")

    def _save_editor(self) -> bool:
        command = self._find(self._selected_id)
        if command is None:
            return False
        try:
            trigger = validate_trigger(self.trigger_entry.get(), self.cfg.commands, command.id)
            cooldown = parse_cooldown(self.cooldown_entry.get())
        except ValueError as exc:
            messagebox.showerror("Команда", str(exc))
            return False
        command.trigger = trigger
        command.description = self.desc_entry.get().strip()
        command.cooldown_sec = cooldown
        command.permission = PERM_BY_LABEL.get(self.perm_menu.get(), "all")
        write_script(command, self.script_box.get("1.0", "end-1c"))
        save_config(self.cfg)
        self.bot.update_config(self.cfg)
        self._clean = self._snapshot()
        self.refresh_list()
        self.append_log(f"Сохранено {trigger}")
        return True

    def _new_command(self) -> None:
        if not self._confirm_discard():
            return
        command = create_command(self.cfg.commands)
        self.cfg.commands.append(command)
        save_config(self.cfg)
        self.bot.update_config(self.cfg)
        self.refresh_list()
        self._show_command(command.id)
        self.append_log(f"Добавлена {command.trigger}")

    def _delete_command(self) -> None:
        command = self._find(self._selected_id)
        if command is None:
            return
        if not messagebox.askyesno("Удалить", f"Удалить {command.trigger} и её скрипт?"):
            return
        self.cfg.commands = [item for item in self.cfg.commands if item.id != command.id]
        delete_command_file(command)
        save_config(self.cfg)
        self.bot.update_config(self.cfg)
        self.refresh_list()
        self._clear_editor()
        self.append_log(f"Удалено {command.trigger}")

    def _paste_script(self) -> None:
        if self._find(self._selected_id) is None:
            messagebox.showinfo("Команда", "Сначала выберите или создайте команду.")
            return
        try:
            text = read_clipboard(self)
        except ClipboardError:
            self.append_log("Буфер обмена пуст")
            return
        self.script_box.focus()
        insert_text(self.script_box, text)
        self._refresh_highlight()
        self.append_log("Текст вставлен в редактор. Нажмите «Сохранить», чтобы он заработал.")

    def _refresh_highlight(self) -> None:
        refresh_highlight(self.script_box)

    def _copy_prompt(self) -> None:
        write_clipboard(self, PROMPT)
        self.append_log("Инструкция для нейросети скопирована. Вставьте её в чат с моделью.")

    def _open_wiki(self) -> None:
        try:
            open_wiki()
        except OSError:
            self.append_log("Страница вики не найдена.")

    def _test_command(self) -> None:
        command = self._find(self._selected_id)
        if command is None:
            messagebox.showinfo("Команда", "Сначала выберите или создайте команду.")
            return
        source = self.script_box.get("1.0", "end-1c")
        trigger = self.trigger_entry.get().strip() or command.trigger
        channel = normalize_channel(self.channel_entry.get())

        def work() -> None:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as handle:
                handle.write(source)
                path = Path(handle.name)
            try:
                context = command_context(command, channel)
                context["message"] = f"{trigger} пример"
                result = run_script(path, context)
            finally:
                path.unlink(missing_ok=True)
            self._show_test_result(trigger, result)

        self.append_log(f"Проверка {trigger}…")
        threading.Thread(target=work, daemon=True).start()

    def _show_test_result(self, trigger: str, result) -> None:
        if result.log.strip():
            self.append_log(result.log.rstrip("\n"))
        if result.error:
            self.append_log(f"{trigger} ошибка:\n{result.error.strip()}")
        if result.replies:
            for reply in result.replies:
                self.append_log(f"ответ (в чат не отправлен): {reply}")
        elif result.ok:
            self.append_log("Скрипт выполнился и ничего не ответил")

    def _remember_connection(self) -> None:
        self.cfg.channel = normalize_channel(self.channel_entry.get())
        self._set_entry(self.channel_entry, self.cfg.channel)
        save_config(self.cfg)
        self.bot.update_config(self.cfg)

    def _on_start(self) -> None:
        self._remember_connection()
        if self._selected_id and self._snapshot() != self._clean:
            self.append_log("В чат пойдёт сохранённая версия. Нажмите «Сохранить», чтобы применить правки в редакторе.")
        self.bot.start()

    def _on_stop(self) -> None:
        if self._status_kind != "online" or not self.bot.running():
            return
        self.set_status("stopping", "")
        self.append_log("Отключение…")

        def work() -> None:
            self.bot.stop()
            if self.bot.running():
                self.append_log("Не удалось остановить бота")
                self.set_status("error", "не остановился")
                return
            if self._status_kind in ("stopping", "idle"):
                self.set_status("idle", "")

        threading.Thread(target=work, daemon=True).start()

    def _check_updates(self) -> None:
        if self._closing or not updates_supported():
            return

        def work() -> None:
            try:
                offer = find_update()
            except UpdateError as exc:
                message = str(exc)
                self.after(0, lambda message=message: self.append_log(f"Не удалось проверить обновления: {message}"))
                return
            if offer is None or self._closing:
                return
            self.after(0, lambda offer=offer: self._show_update(offer))

        threading.Thread(target=work, daemon=True).start()

    def _show_update(self, offer: UpdateOffer) -> None:
        if self._closing or not self.winfo_exists() or self._updating:
            return
        self._update_offer = offer
        self.update_label.configure(text=f"Доступна версия {offer.version}.")
        self.update_bar.grid()
        self.append_log(f"Доступна версия {offer.version}.")

    def _dismiss_update(self) -> None:
        self._update_offer = None
        self.update_bar.grid_remove()

    def _confirm_update(self) -> None:
        offer = self._update_offer
        if offer is None or self._updating:
            return
        if not messagebox.askyesno(
            "Обновление",
            f"Скачать версию {offer.version} и перезапустить OpenBot?\n\nНастройки и команды сохранятся.",
            parent=self,
        ):
            return
        self._updating = True
        self.update_now.configure(state="disabled")
        self.update_later.configure(state="disabled")
        self.update_label.configure(text=f"Скачивание {offer.version}…")
        version = offer.version

        def progress(read: int, total: int) -> None:
            if total <= 0:
                return
            percent = min(100, int(read * 100 / total))
            self.after(0, lambda percent=percent: self._set_update_progress(version, percent))

        def work() -> None:
            try:
                install_update(offer, progress)
            except UpdateError as exc:
                message = str(exc)
                self.after(0, lambda message=message: self._update_failed(message))
                return
            except Exception:
                self.after(0, lambda: self._update_failed("Не удалось скачать обновление."))
                return
            self.after(0, self._restart_for_update)

        threading.Thread(target=work, daemon=True).start()

    def _set_update_progress(self, version: str, percent: int) -> None:
        if self._closing or not self.winfo_exists():
            return
        self.update_label.configure(text=f"Скачивание {version}… {percent}%")

    def _update_failed(self, message: str) -> None:
        self._updating = False
        if self._closing or not self.winfo_exists():
            return
        self.update_now.configure(state="normal")
        self.update_later.configure(state="normal")
        if self._update_offer is not None:
            self.update_label.configure(text=f"Доступна версия {self._update_offer.version}.")
        self.append_log(message)
        messagebox.showerror("Обновление", message, parent=self)

    def _restart_for_update(self) -> None:
        if self._closing:
            return
        self.append_log("Обновление скачано. Перезапуск…")
        self._on_close()

    def _on_close(self) -> None:
        self._closing = True
        dialog = getattr(self, "_login_dialog", None)
        if dialog is not None:
            dialog.cancel.set()
        try:
            self._remember_connection()
            self.bot.stop()
        finally:
            self.destroy()


class LoginDialog(ctk.CTkToplevel):
    def __init__(self, master: App) -> None:
        super().__init__(master)
        self.master_app = master
        self.cancel = threading.Event()
        self.title("Вход Twitch")
        self.geometry("560x470")
        self.resizable(False, False)
        self.grid_columnconfigure(0, weight=1)

        text = (
            "Генератор токенов Twitchapps закрыт. Токен выдаёт ваше собственное приложение.\n\n"
            "1. Откройте консоль Twitch и нажмите Register Your Application.\n"
            "2. OAuth Redirect URL вставьте точно так:\n"
            f"    {REDIRECT_URI}\n"
            "3. Категория — Chat Bot. Создайте Client Secret и скопируйте его сразу.\n"
            "4. Вставьте Client ID и Secret ниже. В браузере войдите аккаунтом, от которого бот будет писать.\n"
            "Если это отдельный аккаунт бота, выдайте ему модератора на канале."
        )
        ctk.CTkLabel(self, text=text, justify="left", anchor="w", wraplength=520).grid(
            row=0, column=0, sticky="ew", padx=16, pady=(16, 8)
        )
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="w", padx=16)
        ctk.CTkButton(actions, text="Открыть консоль Twitch", width=190, command=self._open_console).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Скопировать redirect", width=180, command=self._copy_redirect).pack(side="left")

        ctk.CTkLabel(self, text="Client ID", anchor="w").grid(row=2, column=0, sticky="w", padx=16, pady=(12, 0))
        self.client_id = ctk.CTkEntry(self)
        self.client_id.grid(row=3, column=0, sticky="ew", padx=16, pady=(2, 0))
        ctk.CTkLabel(self, text="Client Secret", anchor="w").grid(row=4, column=0, sticky="w", padx=16, pady=(8, 0))
        self.client_secret = ctk.CTkEntry(self, show="*")
        self.client_secret.grid(row=5, column=0, sticky="ew", padx=16, pady=(2, 0))
        if master.cfg.client_id:
            self.client_id.insert(0, master.cfg.client_id)
        if master.cfg.client_secret:
            self.client_secret.insert(0, master.cfg.client_secret)

        self.status = ctk.CTkLabel(self, text="", text_color="#9aa0a6", anchor="w", wraplength=520)
        self.status.grid(row=6, column=0, sticky="ew", padx=16, pady=(10, 0))
        self.go_btn = ctk.CTkButton(self, text="Открыть браузер и войти", width=220, command=self._start)
        self.go_btn.grid(row=7, column=0, sticky="w", padx=16, pady=(8, 16))
        self.transient(master)
        self.after(50, self.lift)

    def _open_console(self) -> None:
        webbrowser.open("https://dev.twitch.tv/console/apps")

    def _copy_redirect(self) -> None:
        write_clipboard(self, REDIRECT_URI)
        self.status.configure(text="Redirect скопирован.")

    def _start(self) -> None:
        client_id = self.client_id.get().strip()
        client_secret = self.client_secret.get().strip()
        if not client_id or not client_secret:
            self.status.configure(text="Вставьте Client ID и Client Secret.")
            return
        self.cancel.clear()
        self.go_btn.configure(state="disabled")
        self.status.configure(text="Ждём подтверждения в браузере…")

        def work() -> None:
            try:
                session = login(client_id, client_secret, cancel=self.cancel)
            except AuthError as exc:
                message = str(exc)
                if message == "Вход отменён.":
                    return
                self.master_app.after(0, lambda message=message: self._failed(message))
                return
            except Exception as exc:
                message = str(exc)
                self.master_app.after(0, lambda message=message: self._failed(message))
                return
            self.master_app.after(0, lambda: self.master_app.apply_login(client_id, client_secret, session))

        threading.Thread(target=work, daemon=True).start()

    def _failed(self, message: str) -> None:
        if not self.winfo_exists():
            return
        self.go_btn.configure(state="normal")
        self.status.configure(text=message)


def _format_cooldown(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)


def run() -> None:
    app = App()
    app.mainloop()
