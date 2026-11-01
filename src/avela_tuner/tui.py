"""The tuner's terminal UI (urwid, the classic ncurses look): the radio's sections on the left, the chosen section's
fields as a form on the right, built from what the radio reports (no per-feature code), a message line and the F-key
line at the bottom.

    F2 save (only the changed fields; an empty password keeps the saved one)   F5 reload   F6 dump   F7 restore
    F8 restart the radio   F10 quit   Tab: between the sections and the form

The radio's errors appear under the field they are about. Serial requests run as jobs: a job does the device work
(off the screen's thread in the app) and returns what to update on the screen, which the Runner runs on the screen's
thread; tests run jobs at once.
"""

from __future__ import annotations

import os
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import urwid

from avela_tuner import settings_file
from avela_tuner.device import CommandError, Device, DeviceError, Section, Values

Result = TypeVar("Result")
Update = Callable[[], None]  # what a job changes on the screen, run on the screen's thread
Job = Callable[[], Update]  # the device work; returns its screen update
Runner = Callable[[Job], None]

DEFAULT_SETTINGS_FILE = "avela-settings.json"
NAME_COLUMN = 12
KEYS = " F2 Save  F5 Reload  F6 Dump  F7 Restore  F8 Restart  F10 Quit   Tab: sections / fields"

PALETTE = [
    ("header", "black", "light gray"),
    ("footer", "black", "dark cyan"),
    ("focus", "black", "light gray"),
    ("edit", "white", "dark blue"),
    ("edit_focus", "black", "light cyan"),
    ("description", "dark gray", ""),
    ("note", "dark cyan", ""),
    ("error", "light red", ""),
    ("ok", "light green", ""),
    ("dialog", "black", "light gray"),
]


def list_box(walker: urwid.SimpleFocusListWalker[urwid.Widget]) -> urwid.ListBox[int]:
    # urwid types ListBox's rows as its internal flow-widget protocol, which its own Widget class doesn't match
    return urwid.ListBox(walker)  # type: ignore[arg-type]


class Tuner:
    """The screen and what the keys do; `root` is the widget to show."""

    def __init__(self, open_device: Callable[[], Device], port_name: str, run: Runner):
        self._open_device = open_device
        self._port_name = port_name
        self._run = run
        self._device: Device | None = None
        self._lock = threading.Lock()
        self._sections: list[Section] = []
        self._section: Section | None = None
        self._requested_section_id = ""
        self._loaded: dict[str, str] = {}
        self._inputs: dict[str, urwid.Edit] = {}
        self._errors: dict[str, urwid.Text] = {}
        self._dialog_confirm: Callable[[], None] | None = None

        self._sections_walker: urwid.SimpleFocusListWalker[urwid.Widget] = urwid.SimpleFocusListWalker([])
        urwid.connect_signal(self._sections_walker, "modified", self._section_focus_changed)
        self._form_walker: urwid.SimpleFocusListWalker[urwid.Widget] = urwid.SimpleFocusListWalker(
            [urwid.Text("Connecting…")]
        )
        self._form_box = urwid.LineBox(list_box(self._form_walker), title="", title_align="left")
        self._columns = urwid.Columns(
            [
                (26, urwid.LineBox(list_box(self._sections_walker), title="Sections", title_align="left")),
                self._form_box,
            ],
            dividechars=1,
        )
        self._status = urwid.Text("")
        self._message = urwid.Text("")
        footer = urwid.Pile([self._message, urwid.AttrMap(urwid.Text(KEYS), "footer")])
        self._frame = urwid.Frame(self._columns, header=urwid.AttrMap(self._status, "header"), footer=footer)
        self.root: urwid.WidgetPlaceholder[urwid.Widget] = urwid.WidgetPlaceholder(self._frame)

    def start(self) -> None:
        self._set_status("connecting…")
        self._load_sections()

    def close(self) -> None:
        with self._lock:
            if self._device is not None:
                self._device.close()

    # -- keys ---------------------------------------------------------------------------------------------------------

    def handle_key(self, key: str | tuple[str, int, int, int]) -> bool:
        """The keys the widgets don't use (urwid's unhandled_input); True if it was one of ours."""
        if not isinstance(key, str):
            return False
        if self._dialog_confirm is not None:
            if key == "esc":
                self._close_dialog()
            elif key == "enter":
                self._dialog_confirm()
            return True
        actions: dict[str, Callable[[], None]] = {
            "f2": self.save,
            "f5": self.reload,
            "f6": self.ask_dump,
            "f7": self.ask_restore,
            "f8": self.restart,
            "tab": self._switch_column,
        }
        if key in ("f10", "ctrl q"):
            raise urwid.ExitMainLoop()
        if key in actions:
            actions[key]()
            return True
        return False

    def press(self, key: str, size: tuple[int, int]) -> None:
        """A key as the main loop gives it: to the widgets first, then to handle_key (for tests)."""
        unhandled = self.root.keypress(size, key)
        if unhandled:
            self.handle_key(unhandled)

    def _switch_column(self) -> None:
        self._columns.focus_position = 1 - self._columns.focus_position

    # -- the radio, as jobs -------------------------------------------------------------------------------------------

    def _with_device(self, action: Callable[[Device], Result]) -> Result:
        with self._lock:
            if self._device is None:
                self._device = self._open_device()
            return action(self._device)

    def _load_sections(self) -> None:
        def job() -> Update:
            try:
                sections = self._with_device(lambda device: device.sections())
            except (DeviceError, CommandError) as error:
                return self._failed(str(error), status="not connected")

            def update() -> None:
                self._set_status("connected")
                self._show_sections(sections)

            return update

        self._run(job)

    def _load_section(self, section: Section) -> None:
        self._requested_section_id = section.id

        def job() -> Update:
            try:
                values = self._with_device(lambda device: device.get(section.id))
            except (DeviceError, CommandError) as error:
                return self._failed(str(error))
            return lambda: self._show_section(section, values)

        self._run(job)

    def _save(self, section: Section, changes: dict[str, str]) -> None:
        def job() -> Update:
            try:
                message = self._with_device(lambda device: device.set(section.id, changes))
            except CommandError as error:
                refused = error  # `error` is unbound once the except block ends; the update runs later
                return lambda: self._show_error(refused, section.id)
            except (DeviceError, ValueError) as error:
                return self._failed(str(error))

            def update() -> None:
                self._show_message(message, error=False)
                self._load_section(section)

            return update

        self._run(job)

    def _dump(self, path: Path, secrets: bool) -> None:
        def job() -> Update:
            try:
                settings = self._with_device(lambda device: settings_file.dump(device, secrets=secrets))
                settings_file.write(path, settings, secrets=secrets)
            except (DeviceError, CommandError, OSError) as error:
                return self._failed(f"Dump failed: {error}")
            note = " (with secrets: readable by you only)" if secrets else ""
            return lambda: self._show_message(f"Dumped to {path}{note}.", error=False)

        self._run(job)

    def _restore(self, path: Path) -> None:
        def job() -> Update:
            try:
                settings = settings_file.read(path)
                lines = self._with_device(lambda device: settings_file.restore(device, settings))
            except (DeviceError, CommandError, settings_file.SettingsFileError) as error:
                return self._failed(f"Restore failed: {error}")
            failed = any(line.error for line in lines)
            report = "; ".join(line.text for line in lines) or "Nothing in the file."

            def update() -> None:
                self._show_message(report, error=failed)
                if self._section is not None:
                    self._load_section(self._section)

            return update

        self._run(job)

    def _failed(self, message: str, status: str = "") -> Update:
        def update() -> None:
            if status:
                self._set_status(status)
            self._show_message(message, error=True)

        return update

    # -- actions ------------------------------------------------------------------------------------------------------

    def changes(self) -> dict[str, str]:
        """The fields that differ from what was loaded; an empty secret field means: keep the saved one."""
        if self._section is None:
            return {}
        changed = {}
        for field in self._section.fields:
            value = self._inputs[field.name].edit_text
            if (field.secret and value) or (not field.secret and value != self._loaded.get(field.name, "")):
                changed[field.name] = value
        return changed

    def save(self) -> None:
        if self._section is None:
            return
        changed = self.changes()
        if not changed:
            self._show_message("Nothing changed.", error=False)
            return
        for error in self._errors.values():
            error.set_text("")
        self._show_message("Saving…", error=False)
        self._save(self._section, changed)

    def reload(self) -> None:
        if self._section is not None:
            self._load_section(self._section)

    def restart(self) -> None:
        def job() -> Update:
            try:
                self._with_device(lambda device: device.restart())
            except (DeviceError, CommandError) as error:
                return self._failed(str(error))
            return lambda: self._show_message("Restarting; the radio is back in a few seconds.", error=False)

        self._run(job)

    def ask_dump(self) -> None:
        self._open_dialog(
            "Dump the settings to", offer_secrets=True, confirmed=lambda path, secrets: self._dump(path, secrets)
        )

    def ask_restore(self) -> None:
        self._open_dialog(
            "Restore the settings from", offer_secrets=False, confirmed=lambda path, secrets: self._restore(path)
        )

    # -- the screen ---------------------------------------------------------------------------------------------------

    def _set_status(self, status: str) -> None:
        self._status.set_text(f" Avela tuner   {self._port_name}: {status}")

    def _show_message(self, message: str, error: bool) -> None:
        self._message.set_text(("error" if error else "ok", f" {message}"))

    def _show_sections(self, sections: list[Section]) -> None:
        self._sections = sections
        self._sections_walker[:] = [
            urwid.AttrMap(urwid.SelectableIcon(f" {section.title}", 0), None, focus_map="focus") for section in sections
        ]
        if sections:
            self._sections_walker.set_focus(0)
            self._load_section(sections[0])

    def _section_focus_changed(self) -> None:
        index = self._sections_walker.focus
        if index is not None and 0 <= index < len(self._sections):
            section = self._sections[index]
            if section.id != self._requested_section_id:
                self._load_section(section)

    def _show_section(self, section: Section, values: Values) -> None:
        self._section = section
        self._loaded = dict(values.values)
        self._inputs = {}
        self._errors = {}
        widgets: list[urwid.Widget] = []
        for field in section.fields:
            edit = urwid.Edit(
                "", "" if field.secret else values.values.get(field.name, ""), mask="*" if field.secret else None
            )
            self._inputs[field.name] = edit
            self._errors[field.name] = urwid.Text("")
            description = field.description + ("  (empty: keep the saved one)" if field.secret else "")
            widgets += [
                urwid.Columns(
                    [(NAME_COLUMN, urwid.Text(field.name)), urwid.AttrMap(edit, "edit", "edit_focus")], dividechars=1
                ),
                urwid.Padding(urwid.Text(("description", description)), left=NAME_COLUMN + 1),
                urwid.Padding(urwid.AttrMap(self._errors[field.name], "error"), left=NAME_COLUMN + 1),
            ]
        widgets += [urwid.Text(("note", note)) for note in values.notes]
        self._form_walker[:] = widgets
        self._form_box.set_title(section.title)

    def _show_error(self, error: CommandError, section_id: str) -> None:
        if error.field_name in self._errors:
            self._errors[error.field_name].set_text(error.message)
        self._show_message(error.describe(section_id), error=True)

    def _open_dialog(self, title: str, offer_secrets: bool, confirmed: Callable[[Path, bool], None]) -> None:
        path = urwid.Edit("File: ", DEFAULT_SETTINGS_FILE)
        secrets = urwid.CheckBox("Include secrets (the Wi-Fi password)")

        def confirm() -> None:
            chosen = path.edit_text.strip()
            self._close_dialog()
            if chosen:
                confirmed(Path(chosen).expanduser(), offer_secrets and secrets.get_state() is True)

        buttons = urwid.Columns(
            [
                (8, urwid.Button("OK", lambda button: confirm())),
                (12, urwid.Button("Cancel", lambda button: self._close_dialog())),
            ],
            dividechars=2,
        )
        rows: list[urwid.Widget] = [urwid.AttrMap(path, "edit", "edit_focus")]
        if offer_secrets:
            rows.append(secrets)
        rows += [urwid.Divider(), buttons, urwid.Text(("description", "Enter: OK   Esc: cancel"))]
        dialog = urwid.AttrMap(urwid.LineBox(urwid.Pile(rows), title=title), "dialog")
        self._dialog_confirm = confirm
        self.root.original_widget = urwid.Overlay(
            dialog, self._frame, align="center", width=("relative", 70), valign="middle", height="pack"
        )

    def _close_dialog(self) -> None:
        self._dialog_confirm = None
        self.root.original_widget = self._frame


class BackgroundRunner:
    """Runs jobs on threads and their screen updates on the main loop's thread (woken through urwid's pipe)."""

    def __init__(self) -> None:
        self._updates: queue.Queue[Update] = queue.Queue()
        self._pipe = -1

    def attach(self, loop: urwid.MainLoop) -> None:
        self._pipe = loop.watch_pipe(self._run_updates)

    def __call__(self, job: Job) -> None:
        threading.Thread(target=self._work, args=(job,), daemon=True).start()

    def _work(self, job: Job) -> None:
        try:
            update = job()
        except Exception as error:  # a bug, not a radio error: raised again on the screen's thread, ending the tool
            failure = error

            def update() -> None:
                raise failure

        self._updates.put(update)
        os.write(self._pipe, b"!")

    def _run_updates(self, data: bytes) -> bool:
        while not self._updates.empty():
            self._updates.get()()
        return True


def run_tui(open_device: Callable[[], Device], port_name: str) -> None:
    runner = BackgroundRunner()
    tuner = Tuner(open_device, port_name, runner)
    loop = urwid.MainLoop(tuner.root, PALETTE, unhandled_input=tuner.handle_key)
    runner.attach(loop)
    tuner.start()
    try:
        loop.run()
    finally:
        tuner.close()
