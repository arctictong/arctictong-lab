"""Service probes, tested against a real HTTP server rather than a mock.

``httpx.MockTransport`` would be easier and would prove less: the states the
dashboard distinguishes -- 200, 401, 500, 503, a slow 200, a connection that is
never answered -- are all things a real socket does and a stub has to be told
about.  So these tests start ``http.server`` in a thread and point the collector
at it, which is the only way the timeout case is honest.
"""

from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from dashboard.collector.services import classify, probe_all
from dashboard.config import Service

#: Long enough that the "slow" handler reliably exceeds it, short enough that
#: the test does not become a fixture that takes a second.
SLOW_SECONDS = 0.35


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        path = self.path
        if path == "/ok":
            self._send(200, b"ok")
        elif path == "/notfound":
            self._send(404, b"nope")
        elif path == "/boom":
            self._send(500, b"server error")
        elif path == "/gateway-down":
            self._send(503, b"upstream gone")
        elif path == "/slow":
            time.sleep(SLOW_SECONDS)
            self._send(200, b"slow but fine")
        else:
            self._send(404, b"?")

    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        """Silence stderr; the server is a test fixture, not a service."""


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(verify=True) as shared:
        yield shared


def svc(base: str, path: str, **over) -> Service:
    return Service(name=path.strip("/"), url=f"{base}{path}", **over)


def by_name(results) -> dict[str, object]:
    return {r.name: r for r in results}


# --- classify() is the decision table --------------------------------------


@pytest.mark.parametrize(
    ("status", "latency_ms", "warn_ms", "expected"),
    [
        (200, 10, 750, "up"),
        (204, 10, 750, "up"),
        (200, 900, 750, "warn"),  # answered, but slow enough to notice
        (401, 10, 750, "warn"),  # answered and refused us: not an outage
        (404, 10, 750, "warn"),
        (500, 10, 750, "warn"),  # answered in 30ms: a bug, not a dead service
        (503, 10, 750, "down"),  # a gateway saying the thing behind it is gone
        (504, 10, 750, "down"),
    ],
)
def test_classify_table(status: int, latency_ms: int, warn_ms: int, expected: str) -> None:
    service = Service(name="x", url="http://x", warn_latency_ms=warn_ms)
    assert classify(status, latency_ms, service) == expected


def test_classify_honours_per_service_down_status() -> None:
    """The owner decides what counts as an outage for his own service."""
    strict = Service(name="x", url="http://x", down_status=(500,))
    assert classify(500, 5, strict) == "down"
    # ...and 503 is no longer special for this one.
    assert classify(503, 5, strict) == "warn"


# --- against a real socket ---------------------------------------------------


async def test_probe_reports_real_latency_not_zero(client, server) -> None:
    results = by_name(await probe_all([svc(server, "/ok")], client=client))
    row = results["ok"]
    assert row.state == "up"
    assert row.status_code == 200
    # The AC is "real number, not 0/null" -- on a loopback this is sub-5ms, so
    # the bound that matters is "> 0 and sane", not an exact figure.
    assert row.latency_ms > 0
    assert row.latency_ms < 5_000


async def test_probe_4xx_is_warn_not_down(client, server) -> None:
    results = by_name(await probe_all([svc(server, "/notfound")], client=client))
    assert results["notfound"].state == "warn"
    assert results["notfound"].status_code == 404


async def test_probe_503_is_down(client, server) -> None:
    results = by_name(await probe_all([svc(server, "/gateway-down")], client=client))
    assert results["gateway-down"].state == "down"


async def test_probe_slow_2xx_is_warn(client, server) -> None:
    """A 200 slower than the threshold is yellow, and the ms is the real wait."""
    slow = svc(server, "/slow", warn_latency_ms=100)
    row = (await probe_all([slow], client=client))[0]
    assert row.state == "warn"
    assert row.status_code == 200
    assert row.latency_ms >= 100


async def test_probe_timeout_is_down_and_says_so(client) -> None:
    """Nothing is listening on this port; the error text must name the reason."""
    dead = Service(name="dead", url="http://127.0.0.1:1/", timeout=0.5)
    row = (await probe_all([dead], client=client))[0]
    assert row.state == "down"
    assert row.status_code is None
    assert row.error


async def test_probe_bad_hostname_is_down(client) -> None:
    row = (
        await probe_all(
            [Service(name="ghost", url="http://no-such-host.invalid/", timeout=1.0)],
            client=client,
        )
    )[0]
    assert row.state == "down"
    assert row.error


async def test_probe_all_is_concurrent(client, server) -> None:
    """Five 350ms probes must not take 1.75s.

    This is the property that makes the panel useful: one dead service with a 3s
    timeout must not delay the answer about the others.
    """
    services = [svc(server, "/slow", warn_latency_ms=10_000) for _ in range(5)]
    started = time.perf_counter()
    await probe_all(services, client=client)
    elapsed = time.perf_counter() - started

    assert elapsed < SLOW_SECONDS * 3, f"probes ran serially ({elapsed:.2f}s)"


async def test_probe_all_empty_is_empty(client) -> None:
    assert await probe_all([], client=client) == []


# --- type: tcp ---------------------------------------------------------------
#
# Same "real socket, not a mock" rule as above.  Nothing is ever accepted: the
# kernel completes the handshake from the listen backlog, so a bare connect()
# succeeds -- which is exactly the fact the tcp probe reports.


@pytest.fixture
def listener():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    try:
        yield srv.getsockname()[1]
    finally:
        srv.close()


def tcp_svc(port: int, **over) -> Service:
    return Service(
        name="tcp",
        url=f"tcp://127.0.0.1:{port}",
        type="tcp",
        host="127.0.0.1",
        port=port,
        **over,
    )


async def test_tcp_probe_is_up_when_something_listens(listener) -> None:
    row = (await probe_all([tcp_svc(listener)]))[0]
    assert row.state == "up"
    assert row.status_code is None  # no protocol, so no code to report
    assert row.error is None
    assert row.latency_ms > 0  # AC: a real number, not 0/null


async def test_tcp_probe_is_down_when_nothing_listens() -> None:
    row = (await probe_all([tcp_svc(1, timeout=1.0)]))[0]
    assert row.state == "down"
    assert row.status_code is None
    assert row.error


async def test_tcp_probe_is_warn_when_the_connect_is_too_slow(listener, monkeypatch) -> None:
    """The threshold branch, isolated.

    A real loopback connect is sub-millisecond, so this cannot be reached
    honestly by making the socket slow -- only by freezing the clock.
    """
    from dashboard.collector import services as mod

    monkeypatch.setattr(mod, "_elapsed_ms", lambda _started: 9999)
    row = (await probe_all([tcp_svc(listener, warn_latency_ms=750)]))[0]
    assert row.state == "warn"
    assert row.latency_ms == 9999


async def test_tcp_probe_ignores_down_status(listener) -> None:
    """No status code exists, so a configured down_status cannot turn a
    successful connect red behind the owner's back."""
    row = (await probe_all([tcp_svc(listener, down_status=(0,))]))[0]
    assert row.state == "up"


async def test_probe_all_mixes_http_and_tcp(client, server, listener) -> None:
    results = by_name(await probe_all([svc(server, "/ok"), tcp_svc(listener)], client=client))
    assert results["ok"].state == "up"
    assert results["tcp"].state == "up"
