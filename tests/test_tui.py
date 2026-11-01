from pathlib import Path

import pytest
from fake_radio import FakeRadio

from avela_tuner.device import Device
from avela_tuner.tui import DEFAULT_SETTINGS_FILE, Job, Tuner

SIZE = (100, 30)


def run_at_once(job: Job) -> None:
    job()()


def make_tuner(radio: FakeRadio, timeout: float = 2.0) -> Tuner:
    tuner = Tuner(lambda: Device(radio, timeout=timeout), port_name="fake", run=run_at_once)
    tuner.start()
    return tuner


def screen(tuner: Tuner) -> str:
    return "\n".join(line.decode("utf-8") for line in tuner.root.render(SIZE, focus=True).text)


def type_into(tuner: Tuner, field_name: str, value: str) -> None:
    edit = tuner._inputs[field_name]
    edit.set_edit_text(value)


def type_keys(tuner: Tuner, text: str) -> None:
    for character in text:
        tuner.press(character, SIZE)


def answer_dialog(tuner: Tuner, path: str) -> None:
    for _ in DEFAULT_SETTINGS_FILE:
        tuner.press("backspace", SIZE)
    type_keys(tuner, path)
    tuner.press("enter", SIZE)


def open_weather(tuner: Tuner) -> None:
    for _ in range(3):  # the section list has the focus at start: Wi-Fi, Stations, Player, Weather
        tuner.press("down", SIZE)


@pytest.fixture
def tuner(radio: FakeRadio) -> Tuner:
    return make_tuner(radio)


def test_shows_the_radios_sections_and_values(tuner: Tuner) -> None:
    text = screen(tuner)
    assert "fake: connected" in text
    assert "Wi-Fi" in text and "Weather" in text
    assert "Home" in text
    assert "secret123" not in text  # secrets aren't fetched
    open_weather(tuner)
    text = screen(tuner)
    assert "Warsaw" in text
    assert "found: Warsaw, Poland" in text
    assert "F2 Save" in text


def test_saves_only_what_changed(tuner: Tuner, radio: FakeRadio) -> None:
    open_weather(tuner)
    type_into(tuner, "place", "Krakow")
    assert tuner.changes() == {"place": "Krakow"}
    tuner.press("f2", SIZE)
    assert radio.settings["weather"] == {"place": "Krakow", "units": "metric"}
    assert "Saved." in screen(tuner)


def test_nothing_changed(tuner: Tuner) -> None:
    tuner.press("f2", SIZE)
    assert "Nothing changed." in screen(tuner)


def test_shows_the_radios_error_under_its_field(tuner: Tuner, radio: FakeRadio) -> None:
    open_weather(tuner)
    type_into(tuner, "units", "kelvin")
    tuner.press("f2", SIZE)
    assert "metric or imperial" in screen(tuner)
    assert radio.settings["weather"]["units"] == "metric"


def test_empty_password_keeps_the_saved_one(tuner: Tuner, radio: FakeRadio) -> None:
    type_into(tuner, "ssid", "Other")
    tuner.press("f2", SIZE)
    assert radio.settings["wifi"] == {"ssid": "Other", "password": "secret123"}


def test_a_typed_password_is_masked_and_saved(tuner: Tuner, radio: FakeRadio) -> None:
    type_into(tuner, "password", "newsecret9")
    assert "newsecret9" not in screen(tuner)
    tuner.press("f2", SIZE)
    assert radio.settings["wifi"]["password"] == "newsecret9"


def test_dump_and_restore(tuner: Tuner, radio: FakeRadio, tmp_path: Path) -> None:
    path = tmp_path / "radio.json"
    tuner.press("f6", SIZE)
    assert "Dump the settings to" in screen(tuner)
    answer_dialog(tuner, str(path))
    assert path.exists()
    assert "secret123" not in path.read_text()
    assert f"Dumped to {path}" in screen(tuner)

    radio.settings["weather"]["place"] = "Gdansk"
    tuner.press("f7", SIZE)
    answer_dialog(tuner, str(path))
    assert radio.settings["weather"]["place"] == "Warsaw"


def test_escape_closes_a_dialog(tuner: Tuner) -> None:
    tuner.press("f6", SIZE)
    tuner.press("esc", SIZE)
    assert "Dump the settings to" not in screen(tuner)


def test_restart(tuner: Tuner, radio: FakeRadio) -> None:
    tuner.press("f8", SIZE)
    assert radio.restarted
    assert "Restarting" in screen(tuner)


def test_no_radio() -> None:
    tuner = make_tuner(FakeRadio(silent=True), timeout=0.3)
    text = screen(tuner)
    assert "fake: not connected" in text
    assert "no Avela console" in text
