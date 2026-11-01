"""Sends a new firmware to the radio over the network: POST http://<address>/update with the image as the body, the
same request the radio's update page makes. On the home network it needs the setup page's password (HTTP Basic
authentication, user name avela-admin). The radio accepts it only while its firmware update screen is
open (Info, double-click the knob), and only an Avela firmware; it answers in one sentence, then restarts into the new
firmware."""

from __future__ import annotations

import base64
import urllib.error
import urllib.request

PAGE_PATH = "/update"
USER_NAME = "avela-admin"  # the radio's only user
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403


class NoAnswer(Exception):
    """The radio didn't answer (address, network, or it isn't running)."""


class UpdateRefused(Exception):
    """The radio answered with an error: its update screen isn't open, or it refused the image (the message says)."""


def upload(address: str, firmware: bytes, timeout: float, password: str | None = None) -> str:
    """Checks the page password with the update page (GET), then sends the image; the radio's answer when it took
    it. The check comes first because the radio refuses an upload without reading it: it closes the connection, and
    a client still sending sees a broken connection, not the refusal."""
    headers = {}
    if password is not None:
        credentials = base64.b64encode(f"{USER_NAME}:{password}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {credentials}"
    url = f"http://{address}{PAGE_PATH}"
    _send(urllib.request.Request(url, headers=headers), address, timeout, password)
    post = urllib.request.Request(
        url, data=firmware, method="POST", headers={**headers, "Content-Type": "application/octet-stream"}
    )
    return _send(post, address, timeout, password)


def _send(request: urllib.request.Request, address: str, timeout: float, password: str | None) -> str:
    """The radio's answer, or UpdateRefused / NoAnswer."""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return str(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        if error.code == HTTP_UNAUTHORIZED:
            raise UpdateRefused(
                "wrong page password" if password is not None else "it asks for its page password (--password)"
            ) from error
        if error.code == HTTP_FORBIDDEN and request.get_method() == "GET":
            raise UpdateRefused(
                "it has no page password yet: set one over USB (avela-tuner set access password=...) "
                "or on the setup page's Access tab on its setup network"
            ) from error
        raise UpdateRefused(error.read().decode("utf-8", "replace") or f"HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        raise NoAnswer(f"no answer from {address}: {reason}") from error
