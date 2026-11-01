"""avela-tuner: set up an Avela radio over USB (and update its firmware over the network).

    avela-tuner                                 the TUI: sections, their fields, save, dump, restore, restart
    avela-tuner sections                        what the radio can set: sections and their fields
    avela-tuner get [SECTION] [--secrets]       saved values (all sections, or one)
    avela-tuner set SECTION NAME=VALUE ...      change values (others keep theirs)
    avela-tuner dump [-o FILE] [--secrets]      every section's values as a settings file (JSON)
    avela-tuner restore FILE                    set every section from a settings file
    avela-tuner restart                         restart the radio
    avela-tuner play [STATION]                  play (the current station, or the one in slot STATION)
    avela-tuner stop                            stop playing
    avela-tuner volume LEVEL                    set the volume, 0..100
    avela-tuner update FILE [--address HOST] [--password PASSWORD]
                                                send a new firmware over the network (on the radio first: Info,
                                                double-click the knob)

The port: --port, else $AVELA_PORT, else /dev/ttyUSB0. The update's address: --address, else $AVELA_ADDRESS, else
avela.local; the setup page's password: --password, else $AVELA_PASSWORD (none needed on the setup network).

Exit codes: 0 done; 1 bad use of this tool or a bad settings file; 2 a value the radio refused; 3 no such section or
field; 4 the radio couldn't store it; 5 no radio answering (port, firmware, protocol, address); 6 the radio refused
the update (its update screen isn't open, a wrong or missing page password, or the file isn't an Avela firmware).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from avela_tuner import firmware_upload, settings_file
from avela_tuner.device import CommandError, Device, DeviceError

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_INVALID_VALUE = 2
EXIT_UNKNOWN = 3
EXIT_STORAGE = 4
EXIT_NO_DEVICE = 5
EXIT_UPDATE_REFUSED = 6

ERROR_EXIT_CODES = {
    "invalid_value": EXIT_INVALID_VALUE,
    "invalid_arguments": EXIT_INVALID_VALUE,
    "unknown_field": EXIT_UNKNOWN,
    "unknown_section": EXIT_UNKNOWN,
    "unknown_command": EXIT_UNKNOWN,
    "storage_failed": EXIT_STORAGE,
}

OpenDevice = Callable[[argparse.Namespace], Device]
Command = Callable[[Device, argparse.Namespace, TextIO], int]
NetworkCommand = Callable[[argparse.Namespace, TextIO], int]  # talks to the radio over the network, not USB


class UsageError(Exception):
    """Bad use of the tool (exit code 1)."""


def parse_assignments(assignments: Sequence[str]) -> dict[str, str]:
    values = {}
    for assignment in assignments:
        name, equals, value = assignment.partition("=")
        if not equals or not name:
            raise UsageError(f"expected NAME=VALUE, got {assignment!r}")
        values[name] = value
    return values


# -- commands -----------------------------------------------------------------------------------------------------


def command_sections(device: Device, arguments: argparse.Namespace, output: TextIO) -> int:
    for section in device.sections():
        print(f"{section.id}  {section.title}", file=output)
        for item in section.fields:
            secret = "  (secret)" if item.secret else ""
            print(f"    {item.name:<10} {item.description}{secret}", file=output)
    return EXIT_OK


def command_get(device: Device, arguments: argparse.Namespace, output: TextIO) -> int:
    sections = device.sections()
    if arguments.section:
        sections = [section for section in sections if section.id == arguments.section]
        if not sections:
            raise CommandError("unknown_section", "", "Unknown section.")
    for section in sections:
        saved = device.get(section.id, secrets=arguments.secrets)
        print(section.id, file=output)
        for item in section.fields:
            value = saved.values.get(item.name)
            shown = "(not shown; --secrets shows it)" if value is None and item.secret else (value or "")
            print(f"    {item.name} = {shown}", file=output)
        for note in saved.notes:
            print(f"    {note}", file=output)
    return EXIT_OK


def command_set(device: Device, arguments: argparse.Namespace, output: TextIO) -> int:
    values = parse_assignments(arguments.assignments)
    try:
        message = device.set(arguments.section, values)
    except ValueError as error:
        raise UsageError(str(error)) from error
    print(message, file=output)
    return EXIT_OK


def command_dump(device: Device, arguments: argparse.Namespace, output: TextIO) -> int:
    settings = settings_file.dump(device, secrets=arguments.secrets)
    if arguments.output:
        path = Path(arguments.output)
        settings_file.write(path, settings, secrets=arguments.secrets)
        if arguments.secrets:
            print(f"{path}: holds secrets (the Wi-Fi password); readable by you only", file=sys.stderr)
    else:
        output.write(settings_file.to_text(settings))
    return EXIT_OK


def command_restore(device: Device, arguments: argparse.Namespace, output: TextIO) -> int:
    lines = settings_file.restore(device, settings_file.read(Path(arguments.file)))
    for line in lines:
        print(line.text, file=output)
    return max((ERROR_EXIT_CODES.get(line.error, EXIT_USAGE) for line in lines if line.error), default=EXIT_OK)


def command_restart(device: Device, arguments: argparse.Namespace, output: TextIO) -> int:
    device.restart()
    print("Restarting.", file=output)
    return EXIT_OK


PLAYER_SECTION = "player"  # the firmware's PlayerSection: station, playing, volume


def command_play(device: Device, arguments: argparse.Namespace, output: TextIO) -> int:
    values = {"playing": "yes"}
    if arguments.station is not None:
        values = {"station": str(arguments.station), **values}
    print(device.set(PLAYER_SECTION, values), file=output)
    return EXIT_OK


def command_stop(device: Device, arguments: argparse.Namespace, output: TextIO) -> int:
    print(device.set(PLAYER_SECTION, {"playing": "no"}), file=output)
    return EXIT_OK


def command_volume(device: Device, arguments: argparse.Namespace, output: TextIO) -> int:
    print(device.set(PLAYER_SECTION, {"volume": str(arguments.level)}), file=output)
    return EXIT_OK


def command_update(arguments: argparse.Namespace, output: TextIO) -> int:
    path = Path(arguments.file)
    try:
        firmware = path.read_bytes()
    except OSError as error:
        raise UsageError(f"can't read {path}: {error.strerror}") from error
    print(f"Sending {path.name} ({len(firmware)} bytes) to {arguments.address}…", file=output)
    print(firmware_upload.upload(arguments.address, firmware, arguments.timeout, arguments.password), file=output)
    return EXIT_OK


# -- the program --------------------------------------------------------------------------------------------------


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avela-tuner",
        description="Set up an Avela radio over USB; without a command, the TUI.",
        epilog=__doc__.split("\n\n", 2)[2] if __doc__ else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--port", default=os.environ.get("AVELA_PORT", "/dev/ttyUSB0"))
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=10.0, help="seconds to wait for the radio (default 10)")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("sections", help="what the radio can set").set_defaults(run=command_sections)

    get = commands.add_parser("get", help="saved values")
    get.add_argument("section", nargs="?")
    get.add_argument("--secrets", action="store_true", help="also secret fields (the Wi-Fi password)")
    get.set_defaults(run=command_get)

    change = commands.add_parser("set", help="change values; fields not given keep theirs")
    change.add_argument("section")
    change.add_argument("assignments", nargs="+", metavar="NAME=VALUE")
    change.set_defaults(run=command_set)

    dump = commands.add_parser("dump", help="every section's values as a settings file")
    dump.add_argument("-o", "--output", help="file to write (default: standard output)")
    dump.add_argument("--secrets", action="store_true", help="include secret fields (the Wi-Fi password)")
    dump.set_defaults(run=command_dump)

    restore = commands.add_parser("restore", help="set every section from a settings file")
    restore.add_argument("file")
    restore.set_defaults(run=command_restore)

    commands.add_parser("restart", help="restart the radio").set_defaults(run=command_restart)

    play = commands.add_parser("play", help="play the current station, or the one in a slot")
    play.add_argument("station", nargs="?", type=int, help="the station's slot (avela-tuner get stations)")
    play.set_defaults(run=command_play, section=PLAYER_SECTION)

    commands.add_parser("stop", help="stop playing").set_defaults(run=command_stop, section=PLAYER_SECTION)

    volume = commands.add_parser("volume", help="set the volume")
    volume.add_argument("level", type=int, help="0..100")
    volume.set_defaults(run=command_volume, section=PLAYER_SECTION)

    update = commands.add_parser("update", help="send a new firmware over the network")
    update.add_argument("file", help="the firmware image (avela_firmware.bin)")
    update.add_argument(
        "--address",
        default=os.environ.get("AVELA_ADDRESS", "avela.local"),
        help="the radio's host name or IP address (default avela.local)",
    )
    update.add_argument(
        "--password",
        default=os.environ.get("AVELA_PASSWORD"),
        help="the setup page's password (default $AVELA_PASSWORD; the radio's Access tab)",
    )
    update.set_defaults(run_over_network=command_update)
    return parser


def open_from_arguments(arguments: argparse.Namespace) -> Device:
    return Device.open(arguments.port, arguments.baud, arguments.timeout)


def main(
    argv: Sequence[str] | None = None, open_device: OpenDevice = open_from_arguments, output: TextIO | None = None
) -> int:
    output = output or sys.stdout
    arguments = make_parser().parse_args(argv)
    if arguments.command is None:
        from avela_tuner.tui import run_tui  # urwid only for the TUI

        run_tui(lambda: open_device(arguments), port_name=arguments.port)
        return EXIT_OK
    if hasattr(arguments, "run_over_network"):
        return run_over_network(arguments.run_over_network, arguments, output)
    run: Command = arguments.run
    try:
        with open_device(arguments) as device:
            return run(device, arguments, output)
    except (UsageError, settings_file.SettingsFileError) as error:
        print(f"avela-tuner: {error}", file=sys.stderr)
        return EXIT_USAGE
    except CommandError as error:
        print(f"avela-tuner: {error.describe(getattr(arguments, 'section', None) or '')}", file=sys.stderr)
        return ERROR_EXIT_CODES.get(error.error, EXIT_USAGE)
    except DeviceError as error:
        print(f"avela-tuner: {error}", file=sys.stderr)
        return EXIT_NO_DEVICE


def run_over_network(command: NetworkCommand, arguments: argparse.Namespace, output: TextIO) -> int:
    try:
        return command(arguments, output)
    except UsageError as error:
        print(f"avela-tuner: {error}", file=sys.stderr)
        return EXIT_USAGE
    except firmware_upload.UpdateRefused as error:
        print(f"avela-tuner: the radio refused the update: {error}", file=sys.stderr)
        return EXIT_UPDATE_REFUSED
    except firmware_upload.NoAnswer as error:
        print(f"avela-tuner: {error}", file=sys.stderr)
        return EXIT_NO_DEVICE
