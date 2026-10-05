"""``/api/pve/summary`` and the Proxmox panel, through the real app.

The guests and tasks fed in here are the ones PVE actually returned
(``tests/fixtures/``) -- ten guests, zero backups -- because the bug that got
through in ticket 03 was a row builder that had only ever run against an empty
list.  A populated row is the only thing that proves the row builder works.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.collector import pve
from dashboard.models.pve import parse_guests, parse_tasks
from dashboard.web.app import APP

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client() -> TestClient:
    return TestClient(APP)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _FakePve:
    """A stand-in for :class:`dashboard.collector.pve.PveClient`.

    Deliberately not a mock of ``collect_summary``: stubbing the client leaves
    the guest/task join running for real, which is the part worth testing.
    """

    def __init__(self, guests, tasks, backup_jobs, error=None) -> None:
        self._guests = list(guests)
        self._tasks = list(tasks)
        self._backup_jobs = backup_jobs
        self._error = error

    async def fetch_guests(self):
        if self._error is not None:
            raise self._error
        return self._guests

    async def fetch_tasks(self):
        return self._tasks

    async def fetch_backup_jobs(self):
        return self._backup_jobs


def _stub_pve(monkeypatch, *, guests=None, tasks=None, backup_jobs=0, error=None) -> None:
    if guests is None:
        guests = parse_guests(_fixture("pve_resources.json"))
    if tasks is None:
        tasks = parse_tasks(_fixture("pve_tasks.json"))
    monkeypatch.setattr(
        "dashboard.web.app._pve_client",
        lambda: _FakePve(guests, tasks, backup_jobs, error),
    )


def _stopped_noteapp():
    """Exactly what the live box sent for 105 after ``pct stop``: ``uptime: 0``."""
    return parse_guests(
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


def _missing_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PVE_TOKEN_PATH", str(tmp_path / "no-such.token"))


# --- /api/pve/summary ---------------------------------------------------------


def test_summary_returns_every_guest_from_the_real_capture(client, monkeypatch) -> None:
    _stub_pve(monkeypatch)

    r = client.get("/api/pve/summary")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")

    body = r.json()
    rows = body["guests"]
    assert [g["vmid"] for g in rows] == [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
    assert [g["name"] for g in rows][:2] == ["HomeAssistant", "NginxProxyManager"]
    assert body["backup_jobs"] == 0
    assert "generated_at" in body
    assert "error" not in body


def test_every_guest_row_has_the_spec_shape(client, monkeypatch) -> None:
    _stub_pve(monkeypatch)

    for row in client.get("/api/pve/summary").json()["guests"]:
        assert set(row) == {
            "vmid",
            "name",
            "type",
            "status",
            "uptime_sec",
            "backup_recent",
        }
        assert row["type"] in {"lxc", "qemu"}
        assert isinstance(row["vmid"], int)
        assert isinstance(row["backup_recent"], bool)


def test_a_guest_with_a_fresh_backup_is_the_only_green_one(client, monkeypatch) -> None:
    _stub_pve(
        monkeypatch,
        tasks=parse_tasks(
            {
                "data": [
                    {
                        "upid": "UPID:pve:000C4229:0A4B0143:6ABE1FE0:vzdump:105:root@pam:",
                        "node": "pve",
                        "type": "vzdump",
                        "id": "105",
                        "user": "root@pam",
                        "starttime": 1_790_849_340,
                        "endtime": 1_790_849_400,
                        "status": "OK",
                        "saved": "1",
                    }
                ]
            }
        ),
        backup_jobs=1,
    )

    rows = client.get("/api/pve/summary").json()["guests"]
    assert [r["vmid"] for r in rows if r["backup_recent"]] == [105]


def test_summary_is_json_when_the_token_file_is_missing(client, monkeypatch, tmp_path) -> None:
    _missing_token(monkeypatch, tmp_path)

    r = client.get("/api/pve/summary")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["guests"] == []
    assert "no-such.token" in body["error"]


def test_summary_is_json_when_pve_is_unreachable(client, monkeypatch) -> None:
    _stub_pve(monkeypatch, error=pve.PveError("GET /cluster/resources failed: refused"))

    r = client.get("/api/pve/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["guests"] == []
    assert "refused" in body["error"]


# --- the page -----------------------------------------------------------------


def test_page_renders_the_proxmox_panel(client, monkeypatch) -> None:
    _stub_pve(monkeypatch)

    r = client.get("/dashboard/")
    assert r.status_code == 200
    html = r.text

    assert "Proxmox" in html
    assert "Headscale-Server" in html
    assert "Dashboard" in html
    assert "19d 23h" in html  # VM 100's uptime, rendered for a person
    # VM and container are not the same thing and the table says which is which.
    assert ">VM<" in html
    assert ">LXC<" in html


def test_a_stopped_guest_is_not_shown_as_running(client, monkeypatch) -> None:
    _stub_pve(monkeypatch, guests=_stopped_noteapp(), tasks=[])

    html = client.get("/dashboard/").text

    assert '<td class="down">stopped</td>' in html
    assert ">running<" not in html
    # Not "0s": a stopped guest is not up, so the cell is a dash.
    assert '<td class="num">&mdash;</td>' in html
    assert "0s" not in html
    assert "NoteApp" in html


def test_the_page_says_out_loud_when_no_backup_job_exists(client, monkeypatch) -> None:
    """Ten red dots and no explanation is noise; the cause is one line."""
    _stub_pve(monkeypatch, backup_jobs=0)

    html = client.get("/dashboard/").text

    assert "No backup job" in html
    # The rows still tell the truth the spec asked for.
    assert "down-bg" in html


def test_the_backup_note_stays_quiet_when_a_job_exists(client, monkeypatch) -> None:
    _stub_pve(monkeypatch, backup_jobs=1)

    assert "No backup job" not in client.get("/dashboard/").text


def test_the_page_survives_a_dead_pve(client, monkeypatch, tmp_path) -> None:
    _missing_token(monkeypatch, tmp_path)

    r = client.get("/dashboard/")
    assert r.status_code == 200
    assert "Ops Dashboard" in r.text
    assert "no-such.token" in r.text
