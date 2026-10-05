"""PVE ``cluster/resources`` + ``cluster/tasks``, against payloads the box sent.

``tests/fixtures/pve_resources.json`` and ``pve_tasks.json`` are verbatim ``GET``
bodies from ``https://192.168.1.190:8006`` (single node ``pve``, captured
2026-10-01): ten guests, vmid 100-109, and the twenty-five most recent cluster
tasks.  Three facts from those files shape everything below.

* ``cluster/resources`` mixes guests with the node and its storages -- a guest is
  whatever carries ``type`` ``lxc`` or ``qemu``, never "every row".
* ``cluster/tasks`` accepts **no** query parameters on this version: ``limit``,
  ``type`` and ``source`` are each answered with ``400 ... property is not
  defined in schema``.  Whatever filtering happens, happens here.
* There is not one ``vzdump`` among the tasks, and ``cluster/backup`` answered
  ``[]``.  This host has no backup job at all.  The dashboard has to be able to
  say "no recent backup" about that rather than invent a green row, so the
  fixture with zero backups is the *primary* test data, not a sad edge case.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from dashboard.collector.pve import PveClient, PveError, collect_summary, redact
from dashboard.config import Settings
from dashboard.models.pve import (
    PveGuest,
    humanize_uptime,
    parse_guests,
    parse_tasks,
    recent_backup_vmids,
)

FIXTURES = Path(__file__).parent / "fixtures"

#: The capture was taken at this instant, so "24 hours ago" has a fixed meaning.
NOW = 1_790_850_000

SECRET = "66dfcc2e-4f51-4d4e-8f87-6444213bf6b5"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _a_vzdump(endtime: int, *, status: str | None = "OK", vmid: str = "105") -> dict:
    """A ``vzdump`` task in the shape a real task on this box has.

    Every field comes from the real ``vzstart:105`` entry in the fixture; only
    ``type``/``id``/``status``/``endtime`` differ.  No vzdump has ever run here,
    so there is no real one to copy -- which is exactly why the fixture with
    zero backups is the one that matters.
    """
    task = {
        "upid": f"UPID:pve:000C4229:0A4B0143:6ABE1FE0:vzdump:{vmid}:root@pam:",
        "node": "pve",
        "type": "vzdump",
        "id": vmid,
        "user": "root@pam",
        "starttime": endtime - 60,
        "endtime": endtime,
        "saved": "1",
    }
    if status is not None:
        task["status"] = status
    return task


def _pve_client(handler, **kwargs) -> tuple[PveClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(recording))
    client = PveClient(
        url="https://192.168.1.190:8006",
        token_id="dashboard@pve!dashboard",
        token_secret=SECRET,
        client=http,
        **kwargs,
    )
    return client, seen


# --- parsing the real payloads ------------------------------------------------


def test_the_real_capture_holds_ten_guests() -> None:
    guests = parse_guests(_fixture("pve_resources.json"))

    assert [g.vmid for g in guests] == [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
    assert [g.name for g in guests][:3] == [
        "HomeAssistant",
        "NginxProxyManager",
        "Rustdesk-server",
    ]
    assert all(g.status == "running" for g in guests)
    # Two of the ten are VMs, not containers; the panel has to tell them apart.
    assert [g.vmid for g in guests if g.type == "qemu"] == [100, 103]


def test_the_node_and_its_storages_are_not_guests() -> None:
    """``cluster/resources`` is one list of everything, not a list of guests."""
    payload = {
        "data": [
            {"id": "node/pve", "type": "node", "node": "pve", "status": "online"},
            {"id": "storage/local", "type": "storage", "node": "pve", "disk": 1},
            {
                "id": "lxc/109",
                "type": "lxc",
                "vmid": 109,
                "node": "pve",
                "name": "Dashboard",
                "status": "running",
                "uptime": 72258,
            },
        ]
    }

    assert [g.vmid for g in parse_guests(payload)] == [109]


def test_a_bare_list_is_accepted_too() -> None:
    guests = parse_guests([{"type": "lxc", "vmid": 7, "name": "x", "status": "stopped"}])
    assert [g.vmid for g in guests] == [7]


def test_a_stopped_guest_reports_no_uptime_even_though_pve_sends_zero() -> None:
    """The real box answers ``uptime: 0`` for a stopped container.

    The first version of this module assumed PVE omitted the key -- the fixture
    only held running guests, and the hand-written stopped row had no ``uptime``
    field at all, so the assumption survived every test.  Then the live check on
    105 came back ``uptime_sec: 0`` and the page said "0s" about a container that
    was not running.
    """
    guests = parse_guests(
        {
            "data": [
                {
                    "type": "lxc",
                    "vmid": 105,
                    "name": "NoteApp",
                    "status": "stopped",
                    "uptime": 0,
                }
            ]
        }
    )

    (guest,) = guests
    assert guest.status == "stopped"
    assert guest.uptime is None
    assert guest.as_dict(backup_recent=False)["uptime_sec"] is None


def test_a_running_guest_keeps_its_uptime() -> None:
    guests = parse_guests(
        {
            "data": [
                {
                    "type": "lxc",
                    "vmid": 105,
                    "name": "NoteApp",
                    "status": "running",
                    "uptime": 579,
                }
            ]
        }
    )

    (guest,) = guests
    assert guest.uptime == 579
    assert guest.as_dict(backup_recent=False)["uptime_sec"] == 579


def test_a_guest_row_has_exactly_the_shape_the_spec_asks_for() -> None:
    guest = PveGuest(type="lxc", vmid=105, name="NoteApp", status="running", uptime=579)

    assert guest.as_dict(backup_recent=True) == {
        "vmid": 105,
        "name": "NoteApp",
        "type": "lxc",
        "status": "running",
        "uptime_sec": 579,
        "backup_recent": True,
    }


# --- joining tasks to guests --------------------------------------------------


def test_the_real_task_log_has_no_vzdump_at_all() -> None:
    """The finding that matters: this host has never backed anything up."""
    tasks = parse_tasks(_fixture("pve_tasks.json"))

    assert len(tasks) == 25
    assert [t for t in tasks if t.type == "vzdump"] == []
    assert recent_backup_vmids(tasks, fresh_seconds=86_400, now=NOW) == set()


def test_a_successful_vzdump_makes_its_guest_recent() -> None:
    tasks = parse_tasks({"data": [_a_vzdump(NOW - 3_600)]})

    assert recent_backup_vmids(tasks, fresh_seconds=86_400, now=NOW) == {105}


def test_a_vzdump_older_than_the_window_is_not_recent() -> None:
    tasks = parse_tasks({"data": [_a_vzdump(NOW - 86_401)]})

    assert recent_backup_vmids(tasks, fresh_seconds=86_400, now=NOW) == set()


def test_a_failed_vzdump_is_not_recent() -> None:
    """A task that ran and failed is not a backup; status is the error text."""
    tasks = parse_tasks({"data": [_a_vzdump(NOW - 60, status="command 'tar' failed")]})

    assert recent_backup_vmids(tasks, fresh_seconds=86_400, now=NOW) == set()


def test_an_unfinished_vzdump_is_not_recent() -> None:
    raw = _a_vzdump(NOW - 60, status=None)
    del raw["endtime"]

    assert recent_backup_vmids(parse_tasks({"data": [raw]}), fresh_seconds=86_400, now=NOW) == set()


def test_real_failures_in_the_log_are_parsed_as_failures() -> None:
    """The fixture has two genuinely failed tasks; their text is not ``OK``."""
    tasks = parse_tasks(_fixture("pve_tasks.json"))
    unfinished = [t for t in tasks if t.endtime is None]
    failed = [t for t in tasks if t.endtime is not None and not t.succeeded]

    assert len(unfinished) == 2  # the two vnc sessions still open when captured
    assert len(failed) == 2
    assert all("failed to open" in (t.status or "") for t in failed)


# --- uptime for humans --------------------------------------------------------


def test_uptime_reads_like_a_person_said_it() -> None:
    assert humanize_uptime(None) is None
    assert humanize_uptime(0) == "0s"
    assert humanize_uptime(45) == "45s"
    assert humanize_uptime(599) == "9m"
    assert humanize_uptime(3_600) == "1h 0m"
    assert humanize_uptime(1_727_442) == "19d 23h"


# --- the HTTP client ----------------------------------------------------------


async def test_the_client_sends_the_pve_token_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api2/json/cluster/resources"
        return httpx.Response(200, json=_fixture("pve_resources.json"))

    client, seen = _pve_client(handler)
    guests = await client.fetch_guests()
    await client.aclose()

    assert [g.vmid for g in guests] == [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
    # The key travels in a header, never in the URL -- a URL ends up in logs.
    assert seen[0].headers["authorization"] == (
        "PVEAPIToken=dashboard@pve!dashboard=66dfcc2e-4f51-4d4e-8f87-6444213bf6b5"
    )
    assert "66dfcc2e" not in str(seen[0].url)


async def test_a_rejected_token_is_not_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"data": None, "message": "authentication failure"})

    client, _ = _pve_client(handler)
    with pytest.raises(PveError) as caught:
        await client.fetch_guests()
    await client.aclose()

    assert caught.value.retryable is False
    assert "401" in str(caught.value)


async def test_the_token_never_reaches_the_error_text() -> None:
    """A server can echo anything back; the message is filtered anyway."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"upstream said {SECRET}")

    client, _ = _pve_client(handler)
    with pytest.raises(PveError) as caught:
        await client.fetch_guests()
    await client.aclose()

    assert SECRET not in str(caught.value)
    assert "***" in str(caught.value)
    assert redact(f"oops {SECRET}", SECRET) == "oops ***"


async def test_a_dead_host_is_reported_as_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client, _ = _pve_client(handler)
    with pytest.raises(PveError) as caught:
        await client.fetch_guests()
    await client.aclose()

    assert caught.value.retryable is True


async def test_the_client_fetches_tasks_and_backup_jobs_too() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cluster/tasks"):
            return httpx.Response(200, json=_fixture("pve_tasks.json"))
        if request.url.path.endswith("/cluster/backup"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404, json={"data": None})

    client, seen = _pve_client(handler)
    tasks = await client.fetch_tasks()
    jobs = await client.fetch_backup_jobs()
    await client.aclose()

    assert len(tasks) == 25
    assert jobs == 0
    assert [r.url.path for r in seen] == [
        "/api2/json/cluster/tasks",
        "/api2/json/cluster/backup",
    ]


async def test_collect_summary_joins_the_real_payloads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cluster/resources"):
            return httpx.Response(200, json=_fixture("pve_resources.json"))
        if request.url.path.endswith("/cluster/tasks"):
            return httpx.Response(200, json=_fixture("pve_tasks.json"))
        return httpx.Response(200, json={"data": []})

    client, _ = _pve_client(handler)
    rows, backup_jobs = await collect_summary(client, fresh_seconds=86_400, now=NOW)
    await client.aclose()

    assert [r["vmid"] for r in rows] == [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
    assert backup_jobs == 0
    # No backup job on the box, so every guest is honestly "no recent backup".
    assert all(r["backup_recent"] is False for r in rows)


async def test_collect_summary_marks_the_guest_with_a_fresh_backup() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cluster/resources"):
            return httpx.Response(200, json=_fixture("pve_resources.json"))
        if request.url.path.endswith("/cluster/tasks"):
            return httpx.Response(200, json={"data": [_a_vzdump(NOW - 600)]})
        return httpx.Response(200, json={"data": [{"id": "backup-1"}]})

    client, _ = _pve_client(handler)
    rows, backup_jobs = await collect_summary(client, fresh_seconds=86_400, now=NOW)
    await client.aclose()

    assert [r["vmid"] for r in rows if r["backup_recent"]] == [105]
    assert backup_jobs == 1


async def test_a_failing_backup_job_call_does_not_sink_the_panel() -> None:
    """``/cluster/backup`` is an extra; losing it must not lose the guests."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cluster/resources"):
            return httpx.Response(200, json=_fixture("pve_resources.json"))
        if request.url.path.endswith("/cluster/tasks"):
            return httpx.Response(200, json=_fixture("pve_tasks.json"))
        return httpx.Response(403, json={"data": None, "message": "denied"})

    client, _ = _pve_client(handler)
    rows, backup_jobs = await collect_summary(client, fresh_seconds=86_400, now=NOW)
    await client.aclose()

    assert len(rows) == 10
    assert backup_jobs is None


# --- settings -----------------------------------------------------------------


def test_pve_settings_have_defaults_and_can_be_overridden() -> None:
    defaults = Settings.from_env({})
    assert defaults.pve_url.startswith("https://")
    assert defaults.pve_token_id == "dashboard@pve!dashboard"
    assert defaults.pve_backup_fresh_hours == 24.0
    # Safe default; CT 109's unit turns it off for PVE's self-signed certificate.
    assert defaults.pve_verify_tls is True

    overridden = Settings.from_env(
        {
            "PVE_URL": "https://10.0.0.9:8006",
            "PVE_TOKEN_PATH": "/tmp/token",
            "PVE_TOKEN_ID": "ops@pve!dash",
            "PVE_VERIFY_TLS": "false",
            "PVE_BACKUP_FRESH_HOURS": "36",
        }
    )
    assert overridden.pve_url == "https://10.0.0.9:8006"
    assert overridden.pve_token_path == "/tmp/token"
    assert overridden.pve_token_id == "ops@pve!dash"
    assert overridden.pve_verify_tls is False
    assert overridden.pve_backup_fresh_hours == 36.0
