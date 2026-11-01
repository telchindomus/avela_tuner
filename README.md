# avela-tuner

Set up an Avela radio over its USB cable: a TUI, and a command line for scripts. It reads and writes the same settings
sections as the radio's web setup page (Wi-Fi, weather, and whatever the firmware adds), shows the radio's errors on
the field they are about, and dumps and restores all settings as one file.

## Install and run

```
poetry install                       # its own environment (Python 3.11+), with pyserial and urwid
poetry run avela-tuner               # the TUI
poetry run avela-tuner get weather   # or a command
```

To use it like any installed command, outside the checkout: build a wheel (`cicd/build.sh`, below) and
`pipx install build/artifacts/latest/avela_tuner-<version>-py3-none-any.whl`.

The port: `--port`, else `$AVELA_PORT`, else `/dev/ttyUSB0`. Also `--baud` (115200) and `--timeout` (10 s).

## The TUI

```
avela-tuner [--port /dev/ttyUSB0]
```

The radio's sections on the left; the chosen one's fields on the right, with their descriptions and what the radio
notes (e.g. what the weather place lookup found); the keys on the bottom line. Tab moves between the section list and
the fields, the arrows within them. Change values and save (`F2`): only the changed fields are sent; a password field
left empty keeps the saved password. The radio's errors appear under their field. `F5` reload, `F6` dump to a file
(optionally with secrets), `F7` restore from a file, `F8` restart the radio, `F10` quit. In a dialog: Enter = OK,
Esc = cancel.

## The command line

```
avela-tuner sections                         # what the radio can set: sections and their fields
avela-tuner get [SECTION] [--secrets]        # saved values (all sections, or one)
avela-tuner set SECTION NAME=VALUE ...       # change values; fields not given keep theirs
avela-tuner dump [-o FILE] [--secrets]       # every section's values as a settings file (JSON)
avela-tuner restore FILE                     # set every section from a settings file
avela-tuner restart                          # restart the radio
avela-tuner play [STATION]                   # play the current station, or the one in slot STATION
avela-tuner stop                             # stop playing
avela-tuner volume LEVEL                     # the volume, 0..100
avela-tuner update FILE [--address HOST] [--password PASSWORD]
                                             # a new firmware, over the network (see below)
```

Examples:

```
avela-tuner set weather place="New York" units=imperial
avela-tuner set wifi ssid=Home password=secret123
avela-tuner set access password="my page password"
avela-tuner dump -o avela.json                  # without the Wi-Fi password
avela-tuner dump -o avela-full.json --secrets   # with it (the file is made readable by you only)
avela-tuner restore avela.json                  # a field not in the file keeps the radio's value
avela-tuner set stations 7.name="KEXP" 7.genre=Eclectic 7.url=http://live-mp3-128.kexp.org/kexp128.mp3
avela-tuner set stations 7.url=                 # an empty address removes station 7
avela-tuner play 7
avela-tuner volume 40
```

Stations live in slots 1..16, each `<slot>.name`, `<slot>.genre`, `<slot>.url` (an MP3 stream). `play`, `stop` and
`volume` are short for `set player ...` (`station`, `playing=yes|no`, `volume`); the radio carries them out at once and
remembers the station, the volume and whether it plays across a restart.

Values with spaces, double quotes or backslashes are sent in quotes, with `\"` for a quote and `\\` for a backslash,
so every value the setup page accepts can be restored; a value can't contain a line break. It needs firmware that
speaks console protocol 2.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | done |
| 1 | bad use of the tool, or not a usable settings file |
| 2 | the radio refused a value (`invalid_value`; the message says which rule) |
| 3 | no such section or field (`unknown_section`, `unknown_field`; or a command older firmware doesn't know) |
| 4 | the radio couldn't store it (`storage_failed`) |
| 5 | no radio answering: port, firmware not running, or another console protocol; for `update`, nothing at the address |
| 6 | the radio refused the update: its update screen isn't open, a wrong or missing page password, or the file isn't an Avela firmware |

Errors read `section.field: what: the radio's message`, e.g.
`weather.units: invalid value: The units are metric or imperial.` A restore reports every section, sets the good ones
even if another fails, and exits with the worst code.

### Settings file

```json
{
  "format": "avela-settings",
  "version": 1,
  "protocol": 2,
  "sections": {
    "wifi": { "ssid": "Home" },
    "weather": { "place": "Warsaw", "units": "metric" }
  }
}
```

Secret fields (the Wi-Fi password) are in it only with `--secrets`. The page password is never in it: the radio keeps
only its hash and gives out neither, so a restore leaves the radio's page password as it is.

### Firmware update

```
avela-tuner update avela_firmware.bin                    # to avela.local
avela-tuner update avela_firmware.bin --address 192.168.1.23 --password "my page password"
```

Over the network, not USB: the radio must be on your Wi-Fi. On the radio first open Info and double-click the knob:
the update screen opens, and only while it is open does the radio take a firmware. The tool sends the file to
`http://<address>/update`, the same request as the radio's own update page in a browser, and prints the radio's
answer; the radio shows the progress and restarts into the new firmware. If the new firmware crashes or restarts within
its first 30 seconds, the radio goes back to the one before by itself. The address: `--address`, else
`$AVELA_ADDRESS`, else `avela.local`; `--timeout` applies (seconds without an answer).

On your home network the radio's pages ask for the page password, the update too: `--password`, else
`$AVELA_PASSWORD`. The tool checks the password with the update page before it sends the image. On the setup
network no password is needed. Until a page password is set, the radio refuses
updates on the home network; set one over USB with `avela-tuner set access password=...`, or on the setup page's
Access tab on the setup network.

## How it talks to the radio

The firmware's serial console (avela_firmware: `components/avela_setup`, `SetupConsole`) answers lines starting with
`:` with one JSON line (console protocol 2):

```
:sections                      {"ok":true,"protocol":2,"sections":[{"id","title","fields":[{"name","description","secret"}]}]}
:get <section> [secrets]       {"ok":true,"values":{...},"notes":[...]}
:set <section> name=value ...  {"ok":true,"message":...} | {"ok":false,"error":...,"field":...,"message":...}
:restart                       {"ok":true}
```

The tool learns the sections and fields from the radio, so a new feature's settings work without a new tool. A `set`
that doesn't fit one console line (384 bytes) or one form (512 bytes, 8 fields) goes out as several, never splitting
the fields of one group (`3.name`, `3.genre`, `3.url`); a restore of 16 stations thus takes a few lines. It skips
the console's echo, its prompt and the firmware's log lines on the same port. Opening the port leaves DTR and RTS as
the system sets them (both on), which on the usual ESP32 wiring doesn't reset the board; if the board resets anyway,
the first request waits until the console is up again.

## Development

```
poetry install
poetry run task check     # ruff, ruff format --check, mypy (strict), pytest
poetry run task format    # ruff format + fixes
```

The tests run against a fake radio (tests/fake_radio.py) that answers like the firmware's console, the TUI's by pressing
keys and reading the rendered screen; no board needed.

Branches: `main` = released, `devel` = integration, work on `feature/<name>` off `devel` (merged via pull request).
`cicd/` (settings in `cicd/.env` from `cicd/.env.example`); every check and build runs in a fresh clone under
`$BUILD_DIR/clones/`, never in the working copy:

- `cicd/release.sh X.Y.Z | major | minor | patch`: `devel` → `release/X.Y.Z` → `main` + tag `vX.Y.Z` → back into
  `devel` as `X.Y.(Z+1).dev0`; checks the release commit with `cicd/test_unit.sh` before tagging; pushes `main`,
  `devel` and the tag.
- `cicd/test_unit.sh [--tag <ref>] [--source <path-or-URL>]`: `task check` on a fresh clone of origin's `devel` (or
  another branch or tag, or another repository).
- `cicd/build.sh [--tag vX.Y.Z | --version X.Y.Z]`: wheel + sdist from a fresh clone into
  `$BUILD_DIR/artifacts/<version>/` with `checksums.sha256` and `version.txt`, and a `latest` link.
