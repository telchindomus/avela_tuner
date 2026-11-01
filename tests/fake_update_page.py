"""A fake of the radio's update page on localhost: answers GET and POST /update like the firmware does (403 while no
page password is set, 401 without the right one; for POST then 403 while its update screen is closed, 400 for a file
that isn't an Avela firmware, else 200) and keeps what it received."""

from __future__ import annotations

import base64
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AVELA_MAGIC = b"AVELA"  # stands in for the firmware's app description check


class FakeUpdatePage:
    def __init__(self) -> None:
        self.accepting = True
        self.password: str | None = None  # None: the setup network, no password asked
        self.no_password_yet = False  # the home network before a page password is set: 403 for everything
        self.posts = 0
        self.received: bytes | None = None
        self.address = ""


@contextmanager
def running(page: FakeUpdatePage) -> Iterator[FakeUpdatePage]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server's name
            if self.path != "/update":
                self.answer(404, "Not found")
            elif self.refused():
                pass
            else:
                self.answer(200, "<html>the update page</html>")

        def do_POST(self) -> None:  # noqa: N802 - http.server's name
            page.posts += 1
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path != "/update":
                self.answer(404, "Not found")
            elif self.refused():
                pass
            elif not page.accepting:
                self.answer(403, "The radio doesn't accept updates now: open Info and double-click the knob first.")
            elif not body.startswith(AVELA_MAGIC):
                self.answer(400, "This isn't an Avela firmware.")
            else:
                page.received = body
                self.answer(200, "Updated. The radio restarts into the new firmware.")

        def refused(self) -> bool:
            """Answers like the radio's page password check; whether it refused."""
            if page.no_password_yet:
                self.answer(403, "<html>No page password yet</html>")
                return True
            if page.password is not None and not self.authorized(page.password):
                self.answer(401, "<html>Page password</html>")
                return True
            return False

        def authorized(self, password: str) -> bool:
            prefix = "Basic "
            header = self.headers.get("Authorization", "")
            if not header.startswith(prefix):
                return False
            user, _, given = base64.b64decode(header[len(prefix) :]).decode().partition(":")
            return user == "avela-admin" and given == password

        def answer(self, status: int, text: str) -> None:
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *arguments: object) -> None:  # noqa: A002 - http.server's name
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    page.address = f"127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield page
    finally:
        server.shutdown()
        server.server_close()
