"""Config parsing -- the file the owner will actually edit.

Every case here is a typo in services.yaml that would otherwise show up as an
empty panel at 3am instead of a sentence saying which line is wrong.
"""

from __future__ import annotations

import pytest

from dashboard.config import ConfigError, Settings, parse_services


def test_parse_bare_list() -> None:
    services = parse_services([{"name": "Nextcloud", "url": "https://nc.example.com"}])
    assert [s.name for s in services] == ["Nextcloud"]


def test_parse_wrapped_in_services_key() -> None:
    services = parse_services({"services": [{"name": "Nagios", "url": "http://n.example.com"}]})
    assert [s.name for s in services] == ["Nagios"]


def test_parse_empty_document_is_empty_list() -> None:
    assert parse_services(None) == []
    assert parse_services([]) == []


def test_defaults_are_applied() -> None:
    (service,) = parse_services([{"name": "x", "url": "http://x.example.com"}])
    assert service.timeout == 3.0
    assert service.warn_latency_ms == 750
    assert service.down_status == (502, 503, 504)


def test_overrides_are_honoured() -> None:
    (service,) = parse_services(
        [
            {
                "name": "slow-target",
                "url": "https://x.example.com",
                "timeout": 8,
                "warn_latency_ms": 2500,
                "down_status": 500,
            }
        ]
    )
    assert service.timeout == 8.0
    assert service.warn_latency_ms == 2500
    assert service.down_status == (500,)  # scalar is accepted and normalised


def test_missing_url_is_rejected() -> None:
    with pytest.raises(ConfigError, match="url"):
        parse_services([{"name": "x"}])


def test_non_http_url_is_rejected() -> None:
    """A scheme typo would otherwise become an httpx error on every request."""
    with pytest.raises(ConfigError, match="url"):
        parse_services([{"name": "x", "url": "nx.example.com"}])


def test_missing_name_is_rejected() -> None:
    with pytest.raises(ConfigError, match="name"):
        parse_services([{"url": "http://x.example.com"}])


def test_non_numeric_timeout_is_rejected() -> None:
    with pytest.raises(ConfigError, match="timeout"):
        parse_services([{"name": "x", "url": "http://x", "timeout": "soon"}])


def test_negative_warn_latency_is_rejected() -> None:
    with pytest.raises(ConfigError, match="warn_latency_ms"):
        parse_services([{"name": "x", "url": "http://x", "warn_latency_ms": -1}])


def test_duplicate_names_are_rejected() -> None:
    """Two rows with one name would render as two dots and one verdict."""
    with pytest.raises(ConfigError, match="duplicate"):
        parse_services(
            [
                {"name": "x", "url": "http://a.example.com"},
                {"name": "x", "url": "http://b.example.com"},
            ]
        )


def test_error_names_the_offending_index() -> None:
    with pytest.raises(ConfigError, match=r"services\[1\]"):
        parse_services(
            [
                {"name": "good", "url": "http://a.example.com"},
                {"name": "bad", "url": "not-a-url"},
            ]
        )


def test_settings_reports_a_broken_file_without_raising(tmp_path, monkeypatch) -> None:
    """A bad file must degrade the services panel, not take the dashboard down."""
    bad = tmp_path / "services.yaml"
    bad.write_text("services:\n  - name: x\n    url: nope\n", encoding="utf-8")

    monkeypatch.setenv("SERVICES_CONFIG", str(bad))
    settings = Settings.from_env()

    assert settings.services == []
    assert settings.services_error is not None
    assert "url" in settings.services_error
    assert settings.services_source == str(bad)


def test_settings_reads_a_good_file(tmp_path, monkeypatch) -> None:
    good = tmp_path / "services.yaml"
    good.write_text(
        "services:\n  - name: Nextcloud\n    url: https://nc.example.com\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("SERVICES_CONFIG", str(good))
    settings = Settings.from_env()

    assert settings.services_error is None
    assert [s.name for s in settings.services] == ["Nextcloud"]


def test_settings_with_missing_file_is_simply_empty(tmp_path, monkeypatch) -> None:
    """Not-yet-deployed is not an error; it is a dashboard with nothing to show."""
    monkeypatch.setenv("SERVICES_CONFIG", str(tmp_path / "absent.yaml"))
    settings = Settings.from_env()

    assert settings.services == []
    assert settings.services_error is None


def test_settings_headscale_defaults_point_at_the_right_place(monkeypatch) -> None:
    for var in ("HEADSCALE_URL", "HEADSCALE_KEY_PATH"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings.from_env()

    assert settings.headscale_url == "http://192.168.1.197:8080"
    assert settings.headscale_key_path == "/etc/dashboard/secrets/headscale.key"
    assert settings.headscale_verify_tls is True


def test_headscale_verify_tls_can_be_turned_off(monkeypatch) -> None:
    monkeypatch.setenv("HEADSCALE_VERIFY_TLS", "false")
    assert Settings.from_env().headscale_verify_tls is False


# --- type: tcp ---------------------------------------------------------------
#
# Two of the things worth watching here do not speak HTTP: rustdesk-server's
# rendezvous ports, and hubrelay's status port (which wants a bearer token and
# answers 401 without one).  Watching them by URL would produce a permanently
# yellow row that means "we did not bring a token".


def test_http_is_the_default_type() -> None:
    (service,) = parse_services([{"name": "x", "url": "http://x.example.com"}])
    assert service.type == "http"
    assert service.is_tcp is False
    assert service.host is None
    assert service.port is None


def test_tcp_service_derives_its_display_url() -> None:
    (service,) = parse_services(
        [{"name": "RustDesk", "type": "tcp", "host": "192.168.1.193", "port": 21116}]
    )
    assert service.type == "tcp"
    assert service.is_tcp is True
    assert service.host == "192.168.1.193"
    assert service.port == 21116
    # Derived, so the API and the page keep reading one field and the row still
    # says where the probe pointed.
    assert service.url == "tcp://192.168.1.193:21116"


def test_tcp_without_host_or_port_is_rejected() -> None:
    with pytest.raises(ConfigError, match="host"):
        parse_services([{"name": "x", "type": "tcp", "port": 22}])
    with pytest.raises(ConfigError, match="port"):
        parse_services([{"name": "x", "type": "tcp", "host": "h"}])


@pytest.mark.parametrize("bad", [0, 70000, -1, "21116", True])
def test_tcp_port_must_be_a_real_port_number(bad) -> None:
    with pytest.raises(ConfigError, match="port"):
        parse_services([{"name": "x", "type": "tcp", "host": "h", "port": bad}])


def test_tcp_rejects_a_url_rather_than_ignoring_it() -> None:
    """A leftover url would look like the HTTP check still happened."""
    with pytest.raises(ConfigError, match="url"):
        parse_services([{"name": "x", "type": "tcp", "host": "h", "port": 22, "url": "http://h"}])

    with pytest.raises(ConfigError, match="url"):
        parse_services([{"name": "x", "url": "http://x", "type": "tcp", "host": "h", "port": 22}])


def test_host_and_port_on_an_http_service_are_rejected() -> None:
    """Silently ignoring them would look like the probe used them."""
    with pytest.raises(ConfigError, match="only for type: tcp"):
        parse_services([{"name": "x", "url": "http://x", "host": "h"}])
    with pytest.raises(ConfigError, match="only for type: tcp"):
        parse_services([{"name": "x", "url": "http://x", "port": 22}])


def test_unknown_type_is_rejected() -> None:
    with pytest.raises(ConfigError, match="type"):
        parse_services([{"name": "x", "url": "http://x", "type": "udp"}])


def test_tcp_service_reads_from_a_yaml_file(tmp_path, monkeypatch) -> None:
    """The whole path the owner actually edits."""
    path = tmp_path / "services.yaml"
    path.write_text(
        "services:\n"
        "  - name: hubrelay\n"
        "    type: tcp\n"
        "    host: 192.168.1.198\n"
        "    port: 8686\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SERVICES_CONFIG", str(path))
    settings = Settings.from_env()

    assert settings.services_error is None
    (service,) = settings.services
    assert service.url == "tcp://192.168.1.198:8686"
