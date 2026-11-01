import pytest
from fake_radio import FakeRadio

from avela_tuner.device import LINE_BYTES, CommandError, Device, DeviceError, quote_value, set_lines


def test_sections_and_values_through_echo_logs_and_prompts(radio: FakeRadio) -> None:
    device = Device(radio, timeout=2.0)
    sections = device.sections()
    assert [section.id for section in sections] == ["wifi", "stations", "player", "weather"]
    assert sections[0].fields[1].secret
    saved = device.get("weather")
    assert saved.values == {"place": "Warsaw", "units": "metric"}
    assert saved.notes == ["found: Warsaw, Poland"]


def test_secrets_only_when_asked(radio: FakeRadio) -> None:
    device = Device(radio, timeout=2.0)
    assert "password" not in device.get("wifi").values
    assert device.get("wifi", secrets=True).values["password"] == "secret123"


def test_refused_value_is_a_command_error(radio: FakeRadio) -> None:
    with pytest.raises(CommandError) as raised:
        Device(radio, timeout=2.0).set("weather", {"units": "kelvin"})
    assert (raised.value.error, raised.value.field_name) == ("invalid_value", "units")
    assert raised.value.describe("weather") == "weather.units: invalid value: The units are metric or imperial."


def test_value_with_spaces_is_quoted(radio: FakeRadio) -> None:
    Device(radio, timeout=2.0).set("weather", {"place": "New York"})
    assert radio.settings["weather"]["place"] == "New York"


def test_other_protocol_is_refused() -> None:
    with pytest.raises(DeviceError):
        Device(FakeRadio(protocol=1), timeout=2.0).sections()


def test_silent_port() -> None:
    with pytest.raises(DeviceError):
        Device(FakeRadio(silent=True), timeout=0.3).sections()


def test_restart_waits_for_the_console_again(radio: FakeRadio) -> None:
    device = Device(radio, timeout=2.0)
    device.restart()
    assert radio.restarted
    assert device.get("weather").values["place"] == "Warsaw"


def test_quote_value() -> None:
    assert quote_value("Warsaw") == "Warsaw"
    assert quote_value("New York") == '"New York"'
    assert quote_value("") == '""'
    assert quote_value('My "Home"') == '"My \\"Home\\""'
    assert quote_value("a\\b") == '"a\\\\b"'
    with pytest.raises(ValueError):
        quote_value("two\nlines")


def test_quotes_and_backslashes_go_to_the_radio_as_they_are(radio: FakeRadio) -> None:
    device = Device(radio, 2.0)
    device.set("wifi", {"ssid": 'My "Home" WiFi', "password": 'p\\a"ss word\\'})
    assert radio.settings["wifi"]["ssid"] == 'My "Home" WiFi'
    assert radio.settings["wifi"]["password"] == 'p\\a"ss word\\'


def test_long_set_goes_out_in_lines_that_fit(radio: FakeRadio) -> None:
    long_url = "http://example.com/" + "a" * 180
    values = {}
    for slot in range(1, 5):
        values.update({f"{slot}.name": f"Station {slot}", f"{slot}.genre": "Jazz", f"{slot}.url": long_url})
    lines = set_lines("stations", values)
    assert len(lines) == 4  # one station per line: two don't fit the console's 384 bytes
    assert all(len(line.encode()) <= LINE_BYTES for line in lines)
    Device(radio, 2.0).set("stations", values)
    assert radio.settings["stations"]["4.url"] == long_url
    assert radio.lines == lines


def test_short_values_share_a_line() -> None:
    values = {f"{slot}.{part}": "x" for slot in (1, 2) for part in ("name", "genre", "url")}
    assert set_lines("stations", values) == [
        ":set stations 1.name=x 1.genre=x 1.url=x 2.name=x 2.genre=x 2.url=x",
    ]


def test_a_group_never_splits() -> None:
    values = {f"{slot}.{part}": "x" for slot in (1, 2, 3) for part in ("name", "genre", "url")}
    lines = set_lines("stations", values)  # 9 fields: over the 8 a set may carry
    assert lines == [
        ":set stations 1.name=x 1.genre=x 1.url=x 2.name=x 2.genre=x 2.url=x",
        ":set stations 3.name=x 3.genre=x 3.url=x",
    ]


def test_too_long_for_one_line_is_refused() -> None:
    with pytest.raises(ValueError, match="too long"):
        set_lines("stations", {"1.url": "http://" + "a" * 400})
