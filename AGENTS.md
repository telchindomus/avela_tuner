# avela_tuner: working here

avela-tuner sets up an Avela radio from a computer: a terminal interface (urwid) and commands over the radio's USB
console, plus `update`, which sends a firmware image over the network. `README.md` is the user documentation.

A normal Poetry project (dependencies pyserial and urwid). `poetry run task check` runs ruff, the formatter check,
strict mypy and pytest; it must pass before every commit. Branches: `main` (released), `devel` (integration),
`feature/<name>` off `devel`. `cicd/` holds `release.sh`, `test_unit.sh` and `build.sh`.

## Where things are

- `device.py`: the firmware console's machine protocol (`:sections`, `:get`, `:set`, `:restart`, one JSON reply
  line per request). It skips echo, prompt and log lines and re-syncs on the prompt after a reset. A `set` that
  doesn't fit one console line goes out as several `:set` lines (`set_lines`); a field group such as `3.name`
  `3.genre` `3.url` is never split. The line limits (`LINE_BYTES` and the others) follow the firmware's.
- `settings_file.py`: dump, write, read and restore of settings files (format `avela-settings`, version 1). It
  returns results and prints nothing.
- `cli.py`: the argparse commands and exit codes 0–6; no command opens the terminal interface. `play`, `stop` and
  `volume` set the radio's `player` section. `update` is the one command over the network: it opens no serial port.
- `firmware_upload.py`: POSTs the image to `http://<address>/update`, the radio's update page, with the page
  password (HTTP Basic, user name `avela-admin`). Tests run against a fake update page on localhost
  (`tests/fake_update_page.py`).
- `tui.py`: menuconfig-style screens, built from what the radio reports, with no per-feature code. `Tuner` holds the
  screen logic; serial requests are jobs handed to a runner (`BackgroundRunner` in the app, an immediate runner in
  the tests), with one lock around the device. Only changed fields are saved; an empty secret field keeps the saved
  password.

## Rules

- **Keep the protocol in step with the firmware** (`setup_console::protocol_version` in avela_firmware). A changed
  reply is a new protocol version on both sides.
- Full words in names, no global singletons, settings passed as explicit arguments.
