"""Окно OpenBot: подключение, список команд и редактор скриптов."""

from __future__ import annotations

import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

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
    import_script,
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


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("OpenBot")
        self.geometry("1100x840")
        self.minsize(960, 740)

        self.cfg: Config = load_config()
        self._closing = False
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
        self.append_log("Готово. Скрипты выполняются с правами вашего пользователя.")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._update_offer: UpdateOffer | None = None
        self._updating = False
        if updates_supported():
            self.append_log(f"Версия {current_version()}.")
            self.after(300, self._check_updates)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        header.grid_columnconfigure(1, weight=1)

        top = ctk.CTkFrame(header, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 0))
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="OpenBot", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=4
        )
        self.status_label = ctk.CTkLabel(top, text="Не подключён", text_color="#9aa0a6")
        self.status_label.grid(row=0, column=1, sticky="e", padx=8)
        self.wiki_btn = ctk.CTkButton(top, text="Вики", width=70, command=self._open_wiki)
        self.wiki_btn.grid(row=0, column=2, padx=4)
        self.start_btn = ctk.CTkButton(top, text="Старт", width=90, command=self._on_start)
        self.start_btn.grid(row=0, column=3, padx=4)
        self.stop_btn = ctk.CTkButton(top, text="Стоп", width=90, command=self._on_stop, state="disabled")
        self.stop_btn.grid(row=0, column=4, padx=(4, 4))

        form = ctk.CTkFrame(header, fg_color="transparent")
        form.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(6, 10))
        form.grid_columnconfigure(5, weight=1)
        ctk.CTkLabel(form, text="Канал").grid(row=0, column=0, sticky="w", padx=(4, 6))
        self.channel_entry = ctk.CTkEntry(form, width=150, placeholder_text="mychannel")
        self.channel_entry.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(form, text="Ник").grid(row=0, column=2, sticky="w", padx=(12, 6))
        self.nick_entry = ctk.CTkEntry(form, width=130, placeholder_text="из токена")
        self.nick_entry.grid(row=0, column=3, sticky="w")
        ctk.CTkLabel(form, text="OAuth-токен").grid(row=0, column=4, sticky="w", padx=(12, 6))
        self.token_entry = ctk.CTkEntry(form, width=240, show="*", placeholder_text="oauth:...")
        self.token_entry.grid(row=0, column=5, sticky="ew")
        self._show_token = ctk.CTkButton(form, text="Показать", width=88, command=self._toggle_token)
        self._show_token.grid(row=0, column=6, padx=(8, 4))
        self.login_btn = ctk.CTkButton(form, text="Войти через Twitch", width=200, command=self._open_login)
        self.login_btn.grid(row=1, column=0, columnspan=4, sticky="w", padx=(4, 0), pady=(8, 0))

        self.update_bar = ctk.CTkFrame(header)
        self.update_bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        self.update_bar.grid_columnconfigure(0, weight=1)
        self.update_label = ctk.CTkLabel(self.update_bar, text="", anchor="w")
        self.update_label.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
        self.update_later = ctk.CTkButton(self.update_bar, text="Позже", width=80, command=self._dismiss_update)
        self.update_later.grid(row=0, column=1, padx=4, pady=8)
        self.update_now = ctk.CTkButton(self.update_bar, text="Обновить", width=110, command=self._confirm_update)
        self.update_now.grid(row=0, column=2, padx=(4, 10), pady=8)
        self.update_bar.grid_remove()

        if self.cfg.channel:
            self.channel_entry.insert(0, self.cfg.channel)
        if self.cfg.nickname:
            self.nick_entry.insert(0, self.cfg.nickname)
        if self.cfg.token:
            self.token_entry.insert(0, self.cfg.token)

        note = ctk.CTkLabel(
            self,
            text="Скрипты выполняются с правами вашего пользователя и могут делать всё, что может Python на этом компьютере.",
            text_color="#9aa0a6",
            anchor="w",
            wraplength=880,
            justify="left",
        )
        note.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 4))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=16, pady=6)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(body)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        sidebar.grid_rowconfigure(1, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(sidebar, text="Новая команда", command=self._new_command).grid(
            row=0, column=0, sticky="ew", padx=10, pady=(10, 6)
        )
        self.list_frame = ctk.CTkScrollableFrame(sidebar, width=280, label_text="Команды")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 10))

        editor = ctk.CTkFrame(body)
        editor.grid(row=0, column=1, sticky="nsew")
        editor.grid_columnconfigure(1, weight=1)
        editor.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(editor, text="Триггер").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))
        self.trigger_entry = ctk.CTkEntry(editor, placeholder_text="!hello")
        self.trigger_entry.grid(row=0, column=1, sticky="ew", padx=12, pady=(12, 4))

        ctk.CTkLabel(editor, text="Описание").grid(row=1, column=0, sticky="w", padx=12, pady=4)
        self.desc_entry = ctk.CTkEntry(editor)
        self.desc_entry.grid(row=1, column=1, sticky="ew", padx=12, pady=4)

        ctk.CTkLabel(editor, text="Кулдаун, сек").grid(row=2, column=0, sticky="w", padx=12, pady=4)
        self.cooldown_entry = ctk.CTkEntry(editor, width=80)
        self.cooldown_entry.grid(row=2, column=1, sticky="w", padx=12, pady=4)

        ctk.CTkLabel(editor, text="Кто может вызвать").grid(row=3, column=0, sticky="w", padx=12, pady=4)
        self.perm_menu = ctk.CTkOptionMenu(editor, values=list(PERM_LABELS.values()), width=220)
        self.perm_menu.grid(row=3, column=1, sticky="w", padx=12, pady=4)

        self.script_box = ctk.CTkTextbox(
            editor,
            font=ctk.CTkFont(family=MONO, size=13),
            wrap="none",
            undo=True,
            autoseparators=True,
            maxundo=80,
        )
        self.script_box.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=12, pady=8)
        bind_paste(self, self.script_box)
        attach_highlight(self.script_box)

        buttons = ctk.CTkFrame(editor, fg_color="transparent")
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))
        self.save_btn = ctk.CTkButton(buttons, text="Сохранить", command=self._save_editor)
        self.save_btn.grid(row=0, column=0, padx=(0, 6), pady=2, sticky="w")
        self.delete_btn = ctk.CTkButton(
            buttons, text="Удалить", fg_color="#8c3a3a", hover_color="#6e2e2e", command=self._delete_command
        )
        self.delete_btn.grid(row=0, column=1, padx=6, pady=2, sticky="w")
        self.test_btn = ctk.CTkButton(buttons, text="Проверить", command=self._test_command)
        self.test_btn.grid(row=0, column=2, padx=6, pady=2, sticky="w")
        self.paste_btn = ctk.CTkButton(buttons, text="Вставить", command=self._paste_script)
        self.paste_btn.grid(row=1, column=0, padx=(0, 6), pady=2, sticky="w")
        self.import_btn = ctk.CTkButton(buttons, text="Импорт .py", command=self._import_py)
        self.import_btn.grid(row=1, column=1, padx=6, pady=2, sticky="w")
        self.prompt_btn = ctk.CTkButton(buttons, text="Для нейросети", command=self._copy_prompt)
        self.prompt_btn.grid(row=1, column=2, padx=6, pady=2, sticky="w")

        log_frame = ctk.CTkFrame(self)
        log_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(4, 14))
        log_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_frame, text="Лог", anchor="w").grid(row=0, column=0, sticky="w", padx=10, pady=(8, 0))
        self.log_box = ctk.CTkTextbox(log_frame, height=120, font=ctk.CTkFont(family=MONO, size=12), state="disabled")
        self.log_box.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 10))

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

        def _apply() -> None:
            if self._closing or not self.winfo_exists():
                return
            colors = {
                "idle": ("#9aa0a6", "Не подключён"),
                "connecting": ("#e6b35a", "Подключение…"),
                "online": ("#3ddc84", "В сети"),
                "stopping": ("#e6b35a", "Отключение…"),
                "error": ("#ff6b6b", "Ошибка"),
            }
            color, label = colors.get(kind, colors["idle"])
            if kind == "error" and detail:
                label = f"Ошибка: {detail}"
            self.status_label.configure(text=label[:90], text_color=color)
            running = kind in ("connecting", "online", "stopping")
            self.start_btn.configure(state="disabled" if running else "normal")
            self.stop_btn.configure(state="normal" if kind in ("connecting", "online") else "disabled")

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
                self._set_entry(self.nick_entry, session.login)
            self._set_entry(self.token_entry, session.token)
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
            self._set_entry(self.nick_entry, session.login)
        self._set_entry(self.token_entry, session.token)
        save_config(self.cfg)
        self.bot.update_config(self.cfg)
        who = session.login or "аккаунт"
        self.append_log(f"Вход выполнен: {who}. Можно нажимать «Старт».")
        self._close_login()

    def _toggle_token(self) -> None:
        if self.token_entry.cget("show") == "*":
            self.token_entry.configure(show="")
            self._show_token.configure(text="Скрыть")
        else:
            self.token_entry.configure(show="*")
            self._show_token.configure(text="Показать")

    def refresh_list(self) -> None:
        self._building = True
        try:
            for child in self.list_frame.winfo_children():
                child.destroy()
            self._rows.clear()
            if not self.cfg.commands:
                ctk.CTkLabel(self.list_frame, text="Команд пока нет", text_color="#9aa0a6").pack(anchor="w", padx=6, pady=8)
                return
            for command in self.cfg.commands:
                row = ctk.CTkFrame(self.list_frame, fg_color="transparent")
                row.pack(fill="x", padx=2, pady=3)
                row.grid_columnconfigure(0, weight=1)
                title = ctk.CTkLabel(row, text=command.trigger, anchor="w", font=ctk.CTkFont(size=14, weight="bold"))
                title.grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
                switch = ctk.CTkSwitch(row, text="", width=46)
                if command.enabled:
                    switch.select()
                else:
                    switch.deselect()
                switch.configure(command=lambda cid=command.id: self._toggle(cid))
                switch.grid(row=0, column=1, padx=8, pady=(6, 0))
                description = command.description.strip() or "без описания"
                desc = ctk.CTkLabel(row, text=description, anchor="w", text_color="#9aa0a6", wraplength=220, justify="left")
                desc.grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))
                for widget in (row, title, desc):
                    widget.bind("<Button-1>", lambda _event, cid=command.id: self._select(cid))
                self._rows[command.id] = row
        finally:
            self._building = False
        self._paint_selection()

    def _paint_selection(self) -> None:
        for command_id, row in self._rows.items():
            if command_id == self._selected_id:
                row.configure(fg_color=("#d7e6f5", "#1f3b57"))
            else:
                row.configure(fg_color="transparent")

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

    def _import_py(self) -> None:
        if not self._confirm_discard():
            return
        chosen = filedialog.askopenfilename(
            title="Импорт скрипта",
            filetypes=[("Python", "*.py"), ("Все файлы", "*.*")],
        )
        if not chosen:
            return
        try:
            command = import_script(Path(chosen), self.cfg.commands)
        except OSError as exc:
            messagebox.showerror("Импорт", str(exc))
            return
        self.cfg.commands.append(command)
        save_config(self.cfg)
        self.bot.update_config(self.cfg)
        self.refresh_list()
        self._show_command(command.id)
        self.append_log(f"Импортирована {command.trigger}")

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
        self.cfg.nickname = self.nick_entry.get().strip()
        self.cfg.token = self.token_entry.get().strip()
        self._set_entry(self.channel_entry, self.cfg.channel)
        save_config(self.cfg)
        self.bot.update_config(self.cfg)

    def _on_start(self) -> None:
        self._remember_connection()
        if self._selected_id and self._snapshot() != self._clean:
            self.append_log("В чат пойдёт сохранённая версия. Нажмите «Сохранить», чтобы применить правки в редакторе.")
        self.bot.start()

    def _on_stop(self) -> None:
        if not self.bot.running():
            return
        self.set_status("stopping", "")
        self.append_log("Отключение…")

        def work() -> None:
            self.bot.stop()
            if self.bot.running():
                self.append_log("Не удалось остановить бота")
                self.set_status("error", "не остановился")

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
