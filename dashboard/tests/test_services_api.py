"""The HTTP surface, driven through the real app.

These tests care about one thing above all: whatever breaks, the answer is still
JSON in the same shape.  An ops endpoint that returns an HTML stack trace when
upstream is down is an endpoint that takes the dashboard itself down.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.models.nodes import HeadscaleNode
from dashboard.web.app import APP

SERVICES_YAML = """\
services:
  - name: {name}
    url: {url}
"""


@pytest.fixture
def client() -> TestClient:
    return TestClient(APP)


def _services_file(tmp_path: Path, name: str, url: str) -> str:
    path = tmp_path / f"{name}.yaml"
    path.write_text(SERVICES_YAML.format(name=name, url=url), encoding="utf-8")
    return str(path)


def _no_services(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SERVICES_CONFIG", str(tmp_path / "absent.yaml"))


def _bad_services(monkeypatch, tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("services:\n  - name: x\n    url: broken\n", encoding="utf-8")
    monkeypatch.setenv("SERVICES_CONFIG", str(bad))


def _stub_probe(monkeypatch, rows: dict[str, tuple[str, int, int | None]]) -> None:
    """Make probe_all return fixed rows, keyed by service name."""

    async def fake(services):
        out = []
        for s in services:
            state, latency, status = rows.get(s.name, ("down", 0, None))
            out.append(_Result(s.name, s.url, state, latency, status))
        return out

    class _Result:
        def __init__(self, name, url, state, latency_ms, status_code):
            self.name, self.url = name, url
            self.state, self.latency_ms, self.status_code = state, latency_ms, status_code
            self.error = None

        def as_dict(self):
            return {
                "name": self.name,
                "url": self.url,
                "state": self.state,
                "latency_ms": self.latency_ms,
                "status_code": self.status_code,
                "error": self.error,
            }

    monkeypatch.setattr("dashboard.web.app.services.probe_all", fake)


# --- /api/health -------------------------------------------------------------


def test_health_is_always_ok_and_never_touches_upstream(client, monkeypatch, tmp_path) -> None:
    """tsguard polls this; it must answer even with no key and no headscale."""
    _no_services(monkeypatch, tmp_path)
    monkeypatch.setenv("HEADSCALE_KEY_PATH", str(tmp_path / "no-such-key"))

    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["services_configured"] == 0


def test_health_reports_a_broken_services_file(client, monkeypatch, tmp_path) -> None:
    _bad_services(monkeypatch, tmp_path)
    body = client.get("/api/health").json()
    assert body["services_configured"] == 0
    assert body["services_error"] is not None


# --- /api/nodes --------------------------------------------------------------


def test_nodes_shape(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HEADSCALE_KEY_PATH", str(tmp_path / "no-such-key"))
    r = client.get("/api/nodes")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert "nodes" in body and isinstance(body["nodes"], list)
    assert "generated_at" in body


def test_nodes_still_json_when_headscale_is_unreachable(client, monkeypatch, tmp_path) -> None:
    """The AC: unplug headscale and the endpoint still answers JSON, not HTML."""
    monkeypatch.setenv("HEADSCALE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("HEADSCALE_KEY_PATH", str(tmp_path / "no-such-key"))
    monkeypatch.setenv("HEADSCALE_TIMEOUT", "1")

    r = client.get("/api/nodes")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["nodes"] == []
    assert body["error"]


def test_nodes_error_names_the_key_file(client, monkeypatch, tmp_path) -> None:
    """A missing key file is an operator problem, so the message says so."""
    monkeypatch.setenv("HEADSCALE_KEY_PATH", str(tmp_path / "absent.key"))
    assert "absent.key" in client.get("/api/nodes").json()["error"]


# --- /api/services -----------------------------------------------------------


def test_services_shape_from_config(client, monkeypatch, tmp_path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "services:\n"
        "  - name: Nextcloud\n"
        "    url: https://nc.example.com\n"
        "  - name: Nagios\n"
        "    url: http://nagios.example.com\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SERVICES_CONFIG", str(path))
    _stub_probe(
        monkeypatch,
        {"Nextcloud": ("up", 12, 200), "Nagios": ("down", 3000, None)},
    )

    r = client.get("/api/services")
    assert r.status_code == 200
    body = r.json()

    names = [s["name"] for s in body["services"]]
    assert names == ["Nextcloud", "Nagios"]
    assert body["services"][0]["latency_ms"] == 12
    assert body["services"][0]["state"] == "up"
    assert body["summary"] == {"up": 1, "warn": 0, "down": 1}


def test_services_error_is_json_when_config_is_broken(client, monkeypatch, tmp_path) -> None:
    _bad_services(monkeypatch, tmp_path)
    r = client.get("/api/services")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["services"] == []
    assert "bad.yaml" in body["error"]


def test_services_with_no_config_is_an_empty_list_not_an_error(
    client, monkeypatch, tmp_path
) -> None:
    _no_services(monkeypatch, tmp_path)
    body = client.get("/api/services").json()
    assert body["services"] == []
    assert "error" not in body


def test_every_service_row_carries_a_name_and_state(client, monkeypatch, tmp_path) -> None:
    """A row without a state would render as a dot with no colour."""
    path = tmp_path / "s.yaml"
    path.write_text(
        "services:\n  - name: A\n    url: http://a.example.com\n  - name: B\n    url: http://b.example.com\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SERVICES_CONFIG", str(path))
    _stub_probe(monkeypatch, {"A": ("up", 5, 200), "B": ("warn", 900, 200)})

    rows = client.get("/api/services").json()["services"]
    assert len(rows) == 2
    for row in rows:
        assert row["name"]
        assert row["state"] in {"up", "warn", "down"}
        assert isinstance(row["latency_ms"], int)


def test_latency_is_a_real_number_in_the_payload(client, monkeypatch, tmp_path) -> None:
    """AC: latency must not be 0/null across the board."""
    path = _services_file(tmp_path, "nc", "https://nc.example.com")
    monkeypatch.setenv("SERVICES_CONFIG", path)
    _stub_probe(monkeypatch, {"nc": ("up", 47, 200)})

    row = client.get("/api/services").json()["services"][0]
    assert row["latency_ms"] == 47


# --- type: tcp through the whole app -----------------------------------------


def test_tcp_service_goes_through_the_app(client, monkeypatch, tmp_path) -> None:
    """Real socket, real config file, real endpoint.

    This is the layer that broke last time: the shape of a row with data in it
    was never exercised, so /api/nodes was an HTML 500 on the first real deploy.
    """
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    try:
        path = tmp_path / "tcp.yaml"
        path.write_text(
            "services:\n"
            "  - name: hubrelay\n"
            "    type: tcp\n"
            "    host: 127.0.0.1\n"
            f"    port: {port}\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("SERVICES_CONFIG", str(path))

        body = client.get("/api/services").json()
        (row,) = body["services"]
        assert row["name"] == "hubrelay"
        assert row["state"] == "up"
        assert row["status_code"] is None  # no protocol, so no code
        assert row["url"] == f"tcp://127.0.0.1:{port}"
        assert isinstance(row["latency_ms"], int)
        assert body["summary"] == {"up": 1, "warn": 0, "down": 0}

        page = client.get("/dashboard/")
        assert page.status_code == 200
        assert f"tcp://127.0.0.1:{port}" in page.text
        # Shown as text, not a link: a browser can do nothing with tcp://
        assert 'href="tcp://' not in page.text
    finally:
        srv.close()


# --- the page ----------------------------------------------------------------


def test_dashboard_page_renders_both_panels(client, monkeypatch, tmp_path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "services:\n  - name: Nagios\n    url: http://nagios.example.com\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SERVICES_CONFIG", str(path))
    monkeypatch.setenv("HEADSCALE_KEY_PATH", str(tmp_path / "no-such-key"))
    _stub_probe(monkeypatch, {"Nagios": ("down", 3000, None)})

    r = client.get("/dashboard/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    html = r.text

    assert "Services" in html
    assert "Nagios" in html
    assert "Nodes" in html
    assert "down" in html


def test_dashboard_page_survives_a_dead_everything(client, monkeypatch, tmp_path) -> None:
    """Both panels broken must still be a page, not a 500."""
    monkeypatch.setenv("SERVICES_CONFIG", str(tmp_path / "bad.yaml"))
    (tmp_path / "bad.yaml").write_text("services: [oops\n", encoding="utf-8")
    monkeypatch.setenv("HEADSCALE_KEY_PATH", str(tmp_path / "absent.key"))

    r = client.get("/dashboard/")
    assert r.status_code == 200
    assert "Ops Dashboard" in r.text


def test_root_redirects_to_dashboard(client) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/dashboard/"


def test_unknown_path_is_json_404_not_an_html_page(client) -> None:
    """House rule: every answer from this app is JSON unless it is the page."""
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    json.loads(r.text)


# --- regression: a populated node list ---------------------------------------
#
# Every test above reaches /api/nodes with an empty list -- a missing key file or
# an unreachable upstream -- so the row builder never ran and never crashed.  It
# was broken the whole time: four capitalised attribute names that do not exist
# on the model, and last_seen_human used as a value instead of called.  The first
# real deploy answered with an HTML 500.  These tests put a node through.


class _FakeHeadscale:
    def __init__(self, nodes):
        self._nodes = nodes

    async def fetch_nodes(self):
        return self._nodes


def _stub_nodes(monkeypatch, nodes) -> None:
    monkeypatch.setattr("dashboard.web.app._headscale_client", lambda: _FakeHeadscale(nodes))


def _a_node() -> HeadscaleNode:
    """The real shape: name is the raw hostname, givenName is what the owner typed."""
    return HeadscaleNode.model_validate(
        {
            "id": "7",
            "name": "DESKTOP-CDFJ4TV",
            "givenName": "noteapp",
            "online": True,
            "lastSeen": "2020-01-01T00:00:00Z",
            "ipAddresses": ["100.64.0.5", "fd7a:115c::1"],
            "tags": ["tag:server"],
            "expiry": "2026-12-30T00:00:00Z",
        }
    )


def test_nodes_serialises_a_real_node(client, monkeypatch, tmp_path) -> None:
    _no_services(monkeypatch, tmp_path)
    _stub_nodes(monkeypatch, {"7": _a_node()})

    r = client.get("/api/nodes")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")

    row = r.json()["nodes"][0]
    assert row["id"] == "7"
    # The owner's label, not the hostname headscale also reports.
    assert row["name"] == "noteapp"
    assert row["online"] is True
    assert row["ip"] == "100.64.0.5"  # the first IPv4, not the IPv6
    assert row["os"] is None  # v1 has no OS field; never guessed
    assert row["tags"] == ["tag:server"]
    assert row["key_expiry"] == "2026-12-30T00:00:00Z"
    assert row["last_seen"].endswith("ago")


def test_dashboard_page_renders_a_node(client, monkeypatch, tmp_path) -> None:
    """The same row builder, reached through the page rather than the API."""
    _no_services(monkeypatch, tmp_path)
    _stub_nodes(monkeypatch, {"7": _a_node()})

    r = client.get("/dashboard/")
    assert r.status_code == 200
    assert "noteapp" in r.text
    # The raw hostname must not leak into the table as the label.
    assert "DESKTOP-CDFJ4TV" not in r.text
    assert "100.64.0.5" in r.text


def test_an_internal_bug_is_still_json(monkeypatch, tmp_path) -> None:
    """The "always JSON" promise has to cover this app's own crashes too.

    raise_server_exceptions=False because Starlette's error middleware sends the
    handler's response and *then* re-raises so the server logs a traceback; the
    re-raise is intended, but TestClient's default surfaces it instead of the
    body that already went out.
    """
    _no_services(monkeypatch, tmp_path)

    async def boom(_services):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("dashboard.web.app.services.probe_all", boom)

    with TestClient(APP, raise_server_exceptions=False) as c:
        r = c.get("/api/services")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["error"] == "internal error: RuntimeError"
