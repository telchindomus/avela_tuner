"""Settings files (avela-settings, version 1): every section's values, made by a dump and set again by a restore.

    {"format": "avela-settings", "version": 1, "protocol": 2,
     "sections": {"wifi": {"ssid": "Home"}, "weather": {"place": "Warsaw", "units": "metric"}}}

Secret fields (the Wi-Fi password) are in it only when the dump asked for them; a restore leaves fields that aren't in
the file as the radio has them.
"""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from pathlib import Path

from avela_tuner.device import PROTOCOL_VERSION, CommandError, Device

FORMAT = "avela-settings"
VERSION = 1

Settings = dict[str, dict[str, str]]


class SettingsFileError(Exception):
    """Not a usable settings file."""


@dataclass(frozen=True)
class RestoreLine:
    """What happened to one section of a restore: done, or the radio's error (error is its name, else empty)."""

    section_id: str
    text: str
    error: str = ""


def dump(device: Device, secrets: bool) -> Settings:
    return {section.id: device.get(section.id, secrets=secrets).values for section in device.sections()}


def write(path: Path, settings: Settings, secrets: bool) -> None:
    document = {"format": FORMAT, "version": VERSION, "protocol": PROTOCOL_VERSION, "sections": settings}
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if secrets:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # it holds the Wi-Fi password: only for its owner


def to_text(settings: Settings) -> str:
    document = {"format": FORMAT, "version": VERSION, "protocol": PROTOCOL_VERSION, "sections": settings}
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def read(path: Path) -> Settings:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SettingsFileError(f"{path}: can't read it: {error}") from error
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise SettingsFileError(f"{path}: not an Avela settings file (made by a dump)")
    if document.get("version") != VERSION:
        raise SettingsFileError(f"{path}: settings file version {document.get('version')}; this tool reads {VERSION}")
    sections = document.get("sections")
    if not isinstance(sections, dict) or not all(
        isinstance(values, dict) and all(isinstance(value, str) for value in values.values())
        for values in sections.values()
    ):
        raise SettingsFileError(f'{path}: "sections" must map each section to its name: value strings')
    return sections


def restore(device: Device, settings: Settings) -> list[RestoreLine]:
    """Sets every section of the file the radio has; goes on after a section fails."""
    known = {section.id for section in device.sections()}
    lines = []
    for section_id, values in settings.items():
        if section_id not in known:
            lines.append(
                RestoreLine(section_id, f"{section_id}: skipped: this radio has no such section", "unknown_section")
            )
        elif not values:
            lines.append(RestoreLine(section_id, f"{section_id}: nothing to set"))
        else:
            try:
                lines.append(RestoreLine(section_id, f"{section_id}: {device.set(section_id, values)}"))
            except CommandError as error:
                lines.append(RestoreLine(section_id, error.describe(section_id), error.error))
            except ValueError as error:
                lines.append(RestoreLine(section_id, f"{section_id}: {error}", "invalid_arguments"))
    return lines
