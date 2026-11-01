import io
import json
import stat
from pathlib import Path

import pytest
from fake_radio import FakeRadio

from avela_tuner import cli
from avela_tuner.device import Device


def run(argv: list[str], radio: FakeRadio, timeout: float = 2.0) -> tuple[int, str]:
    output = io.StringIO()
    code = cli.main(argv, open_device=lambda arguments: Device(radio, timeout), output=output)
    return code, output.getvalue()


def test_set_and_get(radio: FakeRadio) -> None:
    assert run(["set", "weather", "place=Krakow", "units=imperial"], radio)[0] == cli.EXIT_OK
    code, text = run(["get", "weather"], radio)
    assert code == cli.EXIT_OK
    assert "place = Krakow" in text
    assert "units = imperial" in text


def test_exit_codes() -> None:
    assert run(["set", "weather", "units=kelvin"], FakeRadio())[0] == cli.EXIT_INVALID_VALUE
    assert run(["set", "weather", "colour=red"], FakeRadio())[0] == cli.EXIT_UNKNOWN
    assert run(["set", "lights", "on=yes"], FakeRadio())[0] == cli.EXIT_UNKNOWN
    assert run(["set", "weather", "place"], FakeRadio())[0] == cli.EXIT_USAGE
    assert run(["get"], FakeRadio(silent=True), timeout=0.3)[0] == cli.EXIT_NO_DEVICE


def test_get_hides_secrets(radio: FakeRadio) -> None:
    code, text = run(["get", "wifi"], radio)
    assert "password = (not shown" in text
    assert "secret123" not in text


def test_dump_and_restore(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    assert run(["dump", "-o", str(path)], FakeRadio())[0] == cli.EXIT_OK
    settings = json.loads(path.read_text())
    assert settings["format"] == "avela-settings"
    assert "password" not in settings["sections"]["wifi"]

    secret_path = tmp_path / "with_secrets.json"
    run(["dump", "-o", str(secret_path), "--secrets"], FakeRadio())
    assert json.loads(secret_path.read_text())["sections"]["wifi"]["password"] == "secret123"
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600

    other = FakeRadio()
    other.settings["weather"] = {"place": "Paris", "units": "imperial"}
    other.settings["wifi"]["password"] = "kept-password"
    assert run(["restore", str(path)], other)[0] == cli.EXIT_OK
    assert other.settings["weather"] == {"place": "Warsaw", "units": "metric"}
    assert other.settings["wifi"]["password"] == "kept-password"  # not in the file: kept


def test_restore_reports_each_section(tmp_path: Path, radio: FakeRadio) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "format": "avela-settings",
                "version": 1,
                "sections": {"lights": {"on": "yes"}, "weather": {"units": "kelvin"}, "wifi": {"ssid": "Other"}},
            }
        )
    )
    code, text = run(["restore", str(path)], radio)
    assert code == cli.EXIT_UNKNOWN  # the worst of: unknown section (3), invalid value (2)
    assert "lights: skipped" in text
    assert "weather.units: invalid value" in text
    assert radio.settings["wifi"]["ssid"] == "Other"  # the good section is set anyway


def test_restore_refuses_other_files(tmp_path: Path) -> None:
    path = tmp_path / "other.json"
    path.write_text('{"hello": "world"}')
    assert run(["restore", str(path)], FakeRadio())[0] == cli.EXIT_USAGE


def test_play_stop_and_volume(radio: FakeRadio) -> None:
    assert run(["stop"], radio)[0] == cli.EXIT_OK
    assert radio.settings["player"]["playing"] == "no"
    assert run(["play"], radio)[0] == cli.EXIT_OK
    assert radio.settings["player"] == {"station": "1", "playing": "yes", "volume": "62"}
    assert run(["play", "3"], radio)[0] == cli.EXIT_OK
    assert radio.settings["player"]["station"] == "3"
    assert run(["volume", "40"], radio)[0] == cli.EXIT_OK
    assert radio.settings["player"]["volume"] == "40"
    assert radio.lines[-1] == ":set player volume=40"


def test_volume_needs_a_number(radio: FakeRadio) -> None:
    with pytest.raises(SystemExit) as stop:
        run(["volume", "loud"], radio)
    assert stop.value.code == 2  # argparse refuses it
