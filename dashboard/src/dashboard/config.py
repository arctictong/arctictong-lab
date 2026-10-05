"""Where the dashboard gets its settings from.

Two sources, both plain files on CT 109, both overridable by environment so the
tests never touch ``/etc``:

* ``/etc/dashboard/services.yaml`` -- the service list.  It is a file rather than
  a constant because the list is the part the owner will actually edit, and a
  list that lives in code is a list that gets edited in the wrong place.
* ``/etc/dashboard/secrets/headscale.key`` -- the headscale API key, read by
  :mod:`dashboard.web.app`.  Never in this module: the key has one reader and
  pretending otherwise would only give it a second.
* ``/etc/dashboard/secrets/pve.token`` -- the value half of the PVE API token.
  Same rule, same reason.  What *is* here is the token's id
  (``dashboard@pve!dashboard``), which is the readable half of the header and
  not a secret.

What is *not* configurable is the headscale base URL default: it is the address
of a machine on one homelab, and making it wrong is a one-line env change rather
than a file that can drift.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SERVICES_PATH = "/etc/dashboard/services.yaml"

DEFAULT_HEADSCALE_URL = "http://192.168.1.197:8080"
DEFAULT_HEADSCALE_KEY_PATH = "/etc/dashboard/secrets/headscale.key"

DEFAULT_PVE_URL = "https://192.168.1.190:8006"
#: Only the value from ``pveum user token add`` lives in this file.  The token
#: *id* below is the other half of the ``PVEAPIToken`` header and is not a
#: secret -- it reads like a user name because that is what it is.
DEFAULT_PVE_TOKEN_PATH = "/etc/dashboard/secrets/pve.token"
DEFAULT_PVE_TOKEN_ID = "dashboard@pve!dashboard"
DEFAULT_PVE_TIMEOUT = 10.0
#: Ticket 05's number: a ``vzdump`` that finished within this window is recent.
DEFAULT_PVE_BACKUP_FRESH_HOURS = 24.0

#: Statuses that mean "the service is broken" rather than "the service answered
#: and said no".  Anything else non-2xx is a ``warn`` -- see
#: :func:`dashboard.collector.services.classify`.
DEFAULT_DOWN_STATUS: tuple[int, ...] = (502, 503, 504)

DEFAULT_TIMEOUT = 3.0
DEFAULT_WARN_LATENCY_MS = 750

#: How a service is watched.  ``http`` asks for a URL; ``tcp`` only opens a
#: socket, because two of the things worth watching here do not answer HTTP at
#: all -- rustdesk-server's rendezvous ports, and hubrelay's status port, which
#: wants a bearer token and answers 401 without one.
SERVICE_TYPES = ("http", "tcp")


class ConfigError(Exception):
    """Config that parsed but cannot be used."""


@dataclass(frozen=True)
class Service:
    """One thing to watch."""

    name: str
    #: For ``http`` this is the URL to GET.  For ``tcp`` it is derived from
    #: ``host``/``port`` so that the API and the page keep reading one field.
    url: str
    timeout: float = DEFAULT_TIMEOUT
    warn_latency_ms: int = DEFAULT_WARN_LATENCY_MS
    down_status: tuple[int, ...] = DEFAULT_DOWN_STATUS
    #: ``"http"`` or ``"tcp"``; see :data:`SERVICE_TYPES`.
    type: str = "http"
    host: str | None = None
    port: int | None = None

    @property
    def is_tcp(self) -> bool:
        return self.type == "tcp"

    @classmethod
    def from_dict(cls, raw: Any, index: int) -> Service:
        where = f"services[{index}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: expected a mapping, got {type(raw).__name__}")

        name = raw.get("name")
        url = raw.get("url")
        kind = raw.get("type", "http")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{where}: 'name' must be a non-empty string")
        if not isinstance(kind, str) or kind.lower() not in SERVICE_TYPES:
            raise ConfigError(
                f"{where} ({name}): 'type' must be one of {', '.join(SERVICE_TYPES)}"
            )
        kind = kind.lower()

        host = raw.get("host")
        port = raw.get("port")
        if kind == "tcp":
            if not isinstance(host, str) or not host.strip():
                raise ConfigError(f"{where} ({name}): type: tcp needs a 'host'")
            if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                raise ConfigError(f"{where} ({name}): type: tcp needs a 'port' between 1 and 65535")
            if url is not None:
                raise ConfigError(f"{where} ({name}): type: tcp uses 'host'/'port', not 'url'")
            host = host.strip()
            url = f"tcp://{host}:{port}"
        else:
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise ConfigError(f"{where} ({name}): 'url' must start with http:// or https://")
            if host is not None or port is not None:
                raise ConfigError(f"{where} ({name}): 'host'/'port' are only for type: tcp")

        try:
            timeout = float(raw.get("timeout", DEFAULT_TIMEOUT))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{where} ({name}): 'timeout' must be a number") from exc
        if timeout <= 0:
            raise ConfigError(f"{where} ({name}): 'timeout' must be positive")

        try:
            warn_ms = int(raw.get("warn_latency_ms", DEFAULT_WARN_LATENCY_MS))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{where} ({name}): 'warn_latency_ms' must be an integer") from exc
        if warn_ms < 0:
            raise ConfigError(f"{where} ({name}): 'warn_latency_ms' must not be negative")

        down = raw.get("down_status", DEFAULT_DOWN_STATUS)
        if down is None:
            down = []
        elif isinstance(down, int):
            # A scalar is the common shorthand for "this one status is an outage".
            down = [down]
        elif isinstance(down, tuple):
            down = list(down)
        elif not isinstance(down, list):
            raise ConfigError(f"{where} ({name}): 'down_status' must be a list of integers")
        if not all(isinstance(x, int) for x in down):
            raise ConfigError(f"{where} ({name}): 'down_status' must contain only integers")
        if any(x < 100 or x > 599 for x in down):
            raise ConfigError(f"{where} ({name}): 'down_status' entries must be HTTP status codes")

        return cls(
            name=name.strip(),
            url=url,
            timeout=timeout,
            warn_latency_ms=warn_ms,
            down_status=tuple(down),
            type=kind,
            host=host,
            port=port,
        )


def parse_services(raw: Any) -> list[Service]:
    """Turn a loaded YAML document into services.

    Accepts either a bare list or ``{"services": [...]}`` so the file can grow a
    second top-level key later without breaking the reader.
    """
    if isinstance(raw, dict):
        raw = raw.get("services", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError(f"'services' must be a list, got {type(raw).__name__}")

    services = [Service.from_dict(item, i) for i, item in enumerate(raw)]

    seen: dict[str, int] = {}
    for svc in services:
        if svc.name in seen:
            raise ConfigError(f"duplicate service name {svc.name!r}")
        seen[svc.name] = 1
    return services


@dataclass
class Settings:
    """Everything the app reads at request time."""

    services: list[Service] = field(default_factory=list)
    services_source: str = "<none>"
    services_error: str | None = None

    headscale_url: str = DEFAULT_HEADSCALE_URL
    headscale_key_path: str = DEFAULT_HEADSCALE_KEY_PATH
    headscale_timeout: float = 10.0
    headscale_verify_tls: bool = True

    pve_url: str = DEFAULT_PVE_URL
    pve_token_path: str = DEFAULT_PVE_TOKEN_PATH
    pve_token_id: str = DEFAULT_PVE_TOKEN_ID
    pve_timeout: float = DEFAULT_PVE_TIMEOUT
    #: Safe by default; CT 109's unit turns it off because PVE ships a
    #: self-signed certificate.  Pinning that certificate is round 2.
    pve_verify_tls: bool = True
    pve_backup_fresh_hours: float = DEFAULT_PVE_BACKUP_FRESH_HOURS

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env

        path = source.get("SERVICES_CONFIG", DEFAULT_SERVICES_PATH)
        services: list[Service] = []
        error: str | None = None
        if os.path.exists(path):
            try:
                services = parse_services(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
            except (ConfigError, OSError, yaml.YAMLError) as exc:
                # A broken file must not take the dashboard down -- the nodes
                # panel still works, and the services panel says why it is empty.
                error = f"{type(exc).__name__}: {exc}"

        return cls(
            services=services,
            services_source=path,
            services_error=error,
            headscale_url=source.get("HEADSCALE_URL", DEFAULT_HEADSCALE_URL),
            headscale_key_path=source.get("HEADSCALE_KEY_PATH", DEFAULT_HEADSCALE_KEY_PATH),
            headscale_timeout=float(source.get("HEADSCALE_TIMEOUT", "10")),
            headscale_verify_tls=source.get("HEADSCALE_VERIFY_TLS", "true").lower() != "false",
            pve_url=source.get("PVE_URL", DEFAULT_PVE_URL),
            pve_token_path=source.get("PVE_TOKEN_PATH", DEFAULT_PVE_TOKEN_PATH),
            pve_token_id=source.get("PVE_TOKEN_ID", DEFAULT_PVE_TOKEN_ID),
            pve_timeout=float(source.get("PVE_TIMEOUT", str(DEFAULT_PVE_TIMEOUT))),
            pve_verify_tls=source.get("PVE_VERIFY_TLS", "true").lower() != "false",
            pve_backup_fresh_hours=float(
                source.get("PVE_BACKUP_FRESH_HOURS", str(DEFAULT_PVE_BACKUP_FRESH_HOURS))
            ),
        )


def get_settings() -> Settings:
    """Request-time settings.

    Deliberately not cached: the services file is meant to be editable, and a
    homelab of one node does not care that this re-reads a small YAML on each
    request.
    """
    return Settings.from_env()
