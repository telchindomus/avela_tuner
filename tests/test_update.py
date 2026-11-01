import io
import socket
from pathlib import Path

import pytest
from fake_update_page import AVELA_MAGIC, FakeUpdatePage, running

from avela_tuner import cli, firmware_upload


def update(argv: list[str]) -> tuple[int, str]:
    output = io.StringIO()
    code = cli.main(["--timeout", "2", "update", *argv], output=output)
    return code, output.getvalue()


@pytest.fixture
def firmware(tmp_path: Path) -> Path:
    path = tmp_path / "avela_firmware.bin"
    path.write_bytes(AVELA_MAGIC + bytes(range(256)) * 100)
    return path


def test_sends_the_file_as_it_is(firmware: Path) -> None:
    with running(FakeUpdatePage()) as page:
        code, text = update([str(firmware), "--address", page.address])
    assert code == cli.EXIT_OK
    assert page.received == firmware.read_bytes()
    assert "restarts into the new firmware" in text


def test_sends_the_page_password(firmware: Path) -> None:
    page = FakeUpdatePage()
    page.password = "secret pass"
    with running(page):
        code, _ = update([str(firmware), "--address", page.address, "--password", "secret pass"])
    assert code == cli.EXIT_OK
    assert page.received == firmware.read_bytes()


def test_page_password_from_the_environment(firmware: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVELA_PASSWORD", "secret pass")
    page = FakeUpdatePage()
    page.password = "secret pass"
    with running(page):
        assert update([str(firmware), "--address", page.address])[0] == cli.EXIT_OK


def test_page_password_missing(
    firmware: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AVELA_PASSWORD", raising=False)
    page = FakeUpdatePage()
    page.password = "secret pass"
    with running(page):
        code, _ = update([str(firmware), "--address", page.address])
    assert code == cli.EXIT_UPDATE_REFUSED
    assert "asks for its page password" in capsys.readouterr().err
    assert page.received is None


def test_page_password_wrong(firmware: Path, capsys: pytest.CaptureFixture[str]) -> None:
    page = FakeUpdatePage()
    page.password = "secret pass"
    with running(page):
        code, _ = update([str(firmware), "--address", page.address, "--password", "guess"])
    assert code == cli.EXIT_UPDATE_REFUSED
    assert "wrong page password" in capsys.readouterr().err
    assert page.posts == 0  # checked before the image is sent


def test_no_page_password_yet(firmware: Path, capsys: pytest.CaptureFixture[str]) -> None:
    page = FakeUpdatePage()
    page.no_password_yet = True
    with running(page):
        code, _ = update([str(firmware), "--address", page.address, "--password", "secret pass"])
    assert code == cli.EXIT_UPDATE_REFUSED
    assert "no page password yet" in capsys.readouterr().err
    assert page.posts == 0


def test_update_screen_not_open(firmware: Path, capsys: pytest.CaptureFixture[str]) -> None:
    page = FakeUpdatePage()
    page.accepting = False
    with running(page):
        code, _ = update([str(firmware), "--address", page.address])
    assert code == cli.EXIT_UPDATE_REFUSED
    assert "double-click the knob" in capsys.readouterr().err
    assert page.received is None


def test_not_an_avela_firmware(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    other = tmp_path / "other.bin"
    other.write_bytes(b"something else")
    with running(FakeUpdatePage()) as page:
        code, _ = update([str(other), "--address", page.address])
    assert code == cli.EXIT_UPDATE_REFUSED
    assert "isn't an Avela firmware" in capsys.readouterr().err


def test_no_radio_at_the_address(firmware: Path) -> None:
    with socket.socket() as unused:  # a port nobody listens on
        unused.bind(("127.0.0.1", 0))
        address = f"127.0.0.1:{unused.getsockname()[1]}"
    assert update([str(firmware), "--address", address])[0] == cli.EXIT_NO_DEVICE


def test_missing_file(tmp_path: Path) -> None:
    assert update([str(tmp_path / "missing.bin"), "--address", "127.0.0.1:9"])[0] == cli.EXIT_USAGE


def test_address_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVELA_ADDRESS", "192.168.1.23")
    arguments = cli.make_parser().parse_args(["update", "avela_firmware.bin"])
    assert arguments.address == "192.168.1.23"


def test_upload_raises_the_radios_answer() -> None:
    page = FakeUpdatePage()
    page.accepting = False
    with running(page), pytest.raises(firmware_upload.UpdateRefused, match="double-click"):
        firmware_upload.upload(page.address, AVELA_MAGIC, timeout=2.0)
