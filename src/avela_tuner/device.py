"""Talking to an Avela radio over its USB serial console.

The firmware's console (avela_firmware: components/avela_setup, SetupConsole) answers lines starting with ':' with one
JSON line:
    :sections                      {"ok":true,"protocol":2,"sections":[{"id","title","fields":[{"name","description",
                                   "secret"}]}]}
    :get <section> [secrets]       {"ok":true,"values":{...},"notes":[...]}
    :set <section> name=value ...  {"ok":true,"message":...} or {"ok":false,"error":...,"field":...,"message":...}
    :restart                       {"ok":true}
The console echoes what it gets and shares the port with the firmware's log, so everything that isn't a reply line is
skipped. Opening the port keeps DTR and RTS as the operating system sets them (both on), which on the usual ESP32
auto-reset wiring doesn't reset the board; if it does reset anyway, the first request waits until the console is up.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

PROTOCOL_VERSION = 2  # 2: \" and \\ in quoted values

# What one :set may carry (avela_firmware: SerialConsole::line_capacity, SetupConsole's form_capacity and
# FormFields::max_fields);
# keep in step.
LINE_BYTES = 384
FORM_BYTES = 512
FORM_FIELDS = 8
PROMPT = b"avela> "


class SerialPort(Protocol):
    """What the device needs of a serial port (pyserial's Serial has it; tests use a fake one)."""

    def write(self, data: bytes) -> int | None: ...

    def read(self, size: int = 1) -> bytes: ...

    def close(self) -> None: ...


class DeviceError(Exception):
    """The radio didn't answer, or not in a way this tool understands."""


ERROR_WORDS = {
    "invalid_value": "invalid value",
    "invalid_arguments": "invalid arguments",
    "unknown_field": "no such field",
    "unknown_section": "no such section",
    "unknown_command": "the radio doesn't know this command (older firmware?)",
    "storage_failed": "couldn't store it",
}


class CommandError(Exception):
    """The radio answered a command with an error: error is its name (invalid_value, unknown_field, unknown_section,
    unknown_command, storage_failed, invalid_arguments), field the setting it is about (maybe empty)."""

    def __init__(self, error: str, field_name: str, message: str):
        super().__init__(message)
        self.error = error
        self.field_name = field_name
        self.message = message

    def describe(self, section_id: str) -> str:
        """section.field: what: the radio's message."""
        where = f"{section_id}.{self.field_name}" if self.field_name else section_id
        words = ERROR_WORDS.get(self.error, self.error or "error")
        return f"{where}: {words}: {self.message}" if self.message else f"{where}: {words}"


@dataclass(frozen=True)
class Field:
    name: str
    description: str
    secret: bool


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    fields: tuple[Field, ...]


@dataclass(frozen=True)
class Values:
    values: dict[str, str]
    notes: list[str] = field(default_factory=list)


def quote_value(value: str) -> str:
    """A value as the console reads it: as it is, or in quotes if it has spaces, quotes or backslashes (in quotes,
    \\" is a quote and \\\\ a backslash; outside them a backslash is itself)."""
    if any(ord(character) < 0x20 for character in value):
        raise ValueError("a value can't contain line breaks or control characters")
    if value == "" or any(character in value for character in ' "\\'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def field_group(name: str) -> str:
    """Fields that belong together go in one :set: a station's "3.name", "3.genre" and "3.url" (group "3")."""
    return name.split(".", 1)[0]


def set_lines(section_id: str, values: dict[str, str]) -> list[str]:
    """The :set lines for these values: as few as the console's limits allow, a group of fields never split."""
    if not values:
        raise ValueError("nothing to set")
    groups: dict[str, list[tuple[str, str]]] = {}
    for name, value in values.items():
        groups.setdefault(field_group(name), []).append((name, value))

    prefix = f":set {section_id}"
    lines: list[str] = []
    line, form_bytes, form_fields = prefix, 0, 0
    for group in groups.values():
        assignments = "".join(f" {name}={quote_value(value)}" for name, value in group)
        group_bytes = sum(len(name.encode()) + len(value.encode()) for name, value in group)
        fits = (
            len((line + assignments).encode()) <= LINE_BYTES
            and form_bytes + group_bytes <= FORM_BYTES
            and form_fields + len(group) <= FORM_FIELDS
        )
        if not fits and line != prefix:
            lines.append(line)
            line, form_bytes, form_fields = prefix, 0, 0
        line += assignments
        form_bytes += group_bytes
        form_fields += len(group)
        if len(line.encode()) > LINE_BYTES or form_bytes > FORM_BYTES or form_fields > FORM_FIELDS:
            raise ValueError(f"the values of {field_group(group[0][0])!r} are too long for the radio's console")
    lines.append(line)
    return lines


class Device:
    """One radio on one serial port. Requests are made one after another."""

    def __init__(self, port: SerialPort, timeout: float):
        self._port = port
        self._timeout = timeout
        self._buffer = b""
        self._ready = False

    @classmethod
    def open(cls, port_name: str, baud_rate: int, timeout: float) -> Device:
        import serial  # pyserial; imported here so the rest works (and is tested) without it

        try:
            port = serial.Serial(port_name, baud_rate, timeout=0.1)
        except serial.SerialException as error:
            raise DeviceError(f"can't open {port_name}: {error}") from error
        return cls(port, timeout)

    def close(self) -> None:
        self._port.close()

    def __enter__(self) -> Device:
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()

    # -- requests ---------------------------------------------------------------------------------------------------

    def sections(self) -> list[Section]:
        reply = self.request(":sections")
        protocol = reply.get("protocol")
        if protocol != PROTOCOL_VERSION:
            raise DeviceError(f"the radio speaks console protocol {protocol}; this tool speaks {PROTOCOL_VERSION}")
        try:
            return [
                Section(
                    id=section["id"],
                    title=section["title"],
                    fields=tuple(
                        Field(name=item["name"], description=item["description"], secret=bool(item["secret"]))
                        for item in section["fields"]
                    ),
                )
                for section in reply["sections"]
            ]
        except (KeyError, TypeError) as error:
            raise DeviceError(f"unexpected :sections reply: {reply}") from error

    def get(self, section_id: str, secrets: bool = False) -> Values:
        reply = self.request(f":get {section_id}" + (" secrets" if secrets else ""))
        values = reply.get("values")
        notes = reply.get("notes", [])
        if not isinstance(values, dict) or not isinstance(notes, list):
            raise DeviceError(f"unexpected :get reply: {reply}")
        return Values(
            values={str(name): str(value) for name, value in values.items()}, notes=[str(note) for note in notes]
        )

    def set(self, section_id: str, values: dict[str, str]) -> str:
        """Changes the section's fields (those not given keep their saved values); returns the radio's message.

        More than one :set fits goes out as several, each a whole group of fields (set_lines); the messages are joined.
        """
        messages = [str(self.request(line).get("message", "")) for line in set_lines(section_id, values)]
        return " ".join(message for message in messages if message)

    def restart(self) -> None:
        self.request(":restart")
        self._ready = False  # the next request waits for the console to come back

    def request(self, line: str) -> dict[str, Any]:
        """Sends one machine command and returns its reply; CommandError if the radio says no."""
        if not self._ready:
            self._wait_for_prompt()
        self._port.write(line.encode("utf-8") + b"\n")
        reply = self._read_reply()
        if not reply.get("ok", False):
            raise CommandError(str(reply.get("error", "")), str(reply.get("field", "")), str(reply.get("message", "")))
        return reply

    # -- the line protocol ------------------------------------------------------------------------------------------

    def _wait_for_prompt(self) -> None:
        """Wakes the console with an empty line until it shows its prompt (after a reset that takes a few seconds)."""
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            self._buffer = b""
            self._port.write(b"\n")
            wake_deadline = min(deadline, time.monotonic() + 1.0)
            while time.monotonic() < wake_deadline:
                self._buffer += self._port.read(256)
                if PROMPT in self._buffer:
                    self._buffer = b""
                    self._ready = True
                    return
        raise DeviceError("no Avela console on this port (is it the radio, and is the firmware running?)")

    def _read_reply(self) -> dict[str, Any]:
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            while b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").strip()
                if text.startswith('{"ok"'):
                    try:
                        reply: dict[str, Any] = json.loads(text)
                        return reply
                    except json.JSONDecodeError as error:
                        raise DeviceError(f"unreadable reply: {text}") from error
            self._buffer += self._port.read(256)
        raise DeviceError("the radio didn't answer in time")
