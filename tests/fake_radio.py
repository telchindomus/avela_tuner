"""A fake Avela console on a fake serial port: echoes, prompts, mixes in log lines and answers the machine commands
like the firmware's SetupConsole (sections wifi, stations, player and weather, a field not given keeps its value).
Keeps every machine command line it got (lines)."""

from __future__ import annotations

import json
import shlex

PROMPT = "avela> "
STATION_SLOTS = 16
STATION_PARTS = ("name", "genre", "url")


class FakeRadio:
    def __init__(self, protocol: int = 2, silent: bool = False):
        self.protocol = protocol
        self.silent = silent
        self.settings = {
            "wifi": {"ssid": "Home", "password": "secret123"},
            "stations": {f"{slot}.{part}": "" for slot in range(1, STATION_SLOTS + 1) for part in STATION_PARTS},
            "player": {"station": "1", "playing": "yes", "volume": "62"},
            "weather": {"place": "Warsaw", "units": "metric"},
        }
        self.settings["stations"].update(
            {"1.name": "Groove Salad", "1.url": "http://ice2.somafm.com/groovesalad-128-mp3"}
        )
        self.lines: list[str] = []
        self.restarted = False
        self.closed = False
        self._incoming = b""
        self._outgoing = b""

    # the serial port's side
    def write(self, data: bytes) -> int:
        if self.silent:
            return len(data)
        self._incoming += data
        while b"\n" in self._incoming:
            line, self._incoming = self._incoming.split(b"\n", 1)
            self._handle(line.decode("utf-8"))
        return len(data)

    def read(self, size: int = 1) -> bytes:
        chunk, self._outgoing = self._outgoing[:size], self._outgoing[size:]
        return chunk

    def close(self) -> None:
        self.closed = True

    # the console's side
    def _send(self, text: str) -> None:
        self._outgoing += text.encode("utf-8")

    def _reply(self, reply: dict) -> None:
        self._send("I (12345) weather: a log line in between\n")
        self._send(json.dumps(reply) + "\n")

    def _handle(self, line: str) -> None:
        self._send(line + "\n")  # the echo
        if line.startswith(":"):
            self._machine(line[1:])
        self._send(PROMPT)

    def _machine(self, line: str) -> None:
        self.lines.append(":" + line)
        words = line.split(" ", 2)
        command = words[0]
        if command == "sections":
            self._reply(
                {
                    "ok": True,
                    "protocol": self.protocol,
                    "sections": [
                        {
                            "id": "wifi",
                            "title": "Wi-Fi",
                            "fields": [
                                {"name": "ssid", "description": "the home network's name", "secret": False},
                                {"name": "password", "description": "its password", "secret": True},
                            ],
                        },
                        {
                            "id": "stations",
                            "title": "Stations",
                            "fields": [
                                {"name": name, "description": "a station's " + name.split(".")[1], "secret": False}
                                for name in self.settings["stations"]
                            ],
                        },
                        {
                            "id": "player",
                            "title": "Player",
                            "fields": [
                                {"name": "station", "description": "the station's slot", "secret": False},
                                {"name": "playing", "description": "yes or no", "secret": False},
                                {"name": "volume", "description": "0..100", "secret": False},
                            ],
                        },
                        {
                            "id": "weather",
                            "title": "Weather",
                            "fields": [
                                {"name": "place", "description": "a city", "secret": False},
                                {"name": "units", "description": "metric or imperial", "secret": False},
                            ],
                        },
                    ],
                }
            )
            return
        if command == "restart":
            self.restarted = True
            self._reply({"ok": True})
            return
        if command not in ("get", "set"):
            self._reply({"ok": False, "error": "unknown_command", "field": "", "message": "Unknown command."})
            return
        section = words[1] if len(words) > 1 else ""
        if section not in self.settings:
            self._reply({"ok": False, "error": "unknown_section", "field": "", "message": "Unknown section."})
            return
        if command == "get":
            secrets = len(words) > 2 and words[2] == "secrets"
            values = {name: value for name, value in self.settings[section].items() if secrets or name != "password"}
            notes = ["found: Warsaw, Poland"] if section == "weather" else []
            self._reply({"ok": True, "values": values, "notes": notes})
            return
        changes = dict(item.split("=", 1) for item in shlex.split(words[2] if len(words) > 2 else ""))
        for name in changes:
            if name not in self.settings[section]:
                self._reply(
                    {
                        "ok": False,
                        "error": "unknown_field",
                        "field": name,
                        "message": f"No field {name} here; help lists the fields.",
                    }
                )
                return
        if changes.get("units", "metric") not in ("metric", "imperial"):
            self._reply(
                {
                    "ok": False,
                    "error": "invalid_value",
                    "field": "units",
                    "message": "The units are metric or imperial.",
                }
            )
            return
        self.settings[section].update(changes)
        self._reply({"ok": True, "message": "Saved."})
