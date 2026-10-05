"""Proxmox VE's ``cluster/resources`` and ``cluster/tasks`` as they arrive.

Both shapes here were written against a live capture from the homelab's PVE
(``tests/fixtures/pve_*.json``), not against the API viewer, because the box and
the docs disagreed twice:

* ``cluster/resources`` is one list of *everything* -- the node, its storages and
  the guests -- so "a guest" has to be defined by ``type``, never by position.
* ``cluster/tasks`` takes **no** query parameters on this PVE: ``limit``,
  ``type`` and ``source`` each come back ``400 property is not defined in
  schema``.  Filtering tasks is therefore our job, not the server's.

The one that matters for the panel: a finished task is one whose ``status`` is
exactly ``"OK"``.  A failure is not ``status: "failed"`` -- it is the error text
itself (``"failed to open /tmp/02-npm-db.py for reading"``), and a task that was
still running when the list was taken has neither ``status`` nor ``endtime``.
Anything that is not ``"OK"`` is not a backup.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

log = logging.getLogger(__name__)

#: What ``cluster/resources`` calls a guest.  A VM is ``qemu`` there, which is
#: PVE's name for it and therefore the name the API publishes; the panel is the
#: layer that renders it as "VM".
GUEST_TYPES = ("lxc", "qemu")


class PveGuest(BaseModel):
    """One container or VM.

    Every field has a default so a guest that lost a key is still listed: a
    skipped guest is a guest nobody can see.
    """

    model_config = ConfigDict(extra="ignore")

    vmid: int = 0
    name: str = ""
    type: str = ""
    status: str = ""
    node: str = ""
    #: Seconds since boot -- but only while the guest is running.  See
    #: :meth:`_uptime_only_meaningful_while_running`.
    uptime: int | None = None

    @model_validator(mode="after")
    def _uptime_only_meaningful_while_running(self) -> PveGuest:
        """Throw away an uptime the guest cannot have.

        This is the field that caught out the first version of this module.  The
        fixture held only running guests, so a synthetic stopped row *without* an
        ``uptime`` key passed every test -- and then the real box answered
        ``uptime: 0`` for a stopped container, which the page rendered as ``0s``.
        Not "up zero seconds": not up at all.  For a guest that is not running,
        ``0`` and "unknown" are the same thing, so the honest value is ``None``
        and the row prints a dash.
        """
        if self.status != "running":
            self.uptime = None
        return self

    def as_dict(self, *, backup_recent: bool) -> dict[str, object]:
        """The wire shape from the spec, with the backup verdict joined in."""
        return {
            "vmid": self.vmid,
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "uptime_sec": self.uptime,
            "backup_recent": backup_recent,
        }


class PveTask(BaseModel):
    """One entry of ``cluster/tasks``.

    ``id`` is a *string* and holds the vmid for anything that acted on a guest
    (``vzdump``, ``vzstart``, ``vncproxy``) and ``""`` for cluster-wide tasks.
    """

    model_config = ConfigDict(extra="ignore")

    upid: str = ""
    node: str = ""
    type: str = ""
    id: str = ""
    user: str = ""
    status: str | None = None
    starttime: int | None = None
    endtime: int | None = None

    @property
    def succeeded(self) -> bool:
        """``"OK"`` and nothing else -- a failure is the error text."""
        return self.status == "OK"

    @property
    def vmid(self) -> int | None:
        """The guest this task acted on, or ``None`` for a cluster-wide one."""
        try:
            return int(self.id)
        except (TypeError, ValueError):
            return None


def _rows(payload: Any) -> list[Any]:
    """The list inside PVE's ``{"data": [...]}`` envelope.

    A bare list is accepted as well so a fixture can be the rows without the
    envelope.  Anything else is empty rather than fatal: the client is the layer
    that refuses a payload it cannot use, and it says so there.
    """
    if isinstance(payload, dict):
        inner = payload.get("data")
        return inner if isinstance(inner, list) else []
    return payload if isinstance(payload, list) else []


def parse_guests(payload: Any) -> list[PveGuest]:
    """Every container and VM in a ``cluster/resources`` answer, by vmid.

    One unparseable row is counted and skipped rather than failing the panel --
    the same trade-off headscale's node list makes, for the same reason.
    """
    guests: list[PveGuest] = []
    for row in _rows(payload):
        if not isinstance(row, dict) or row.get("type") not in GUEST_TYPES:
            continue
        try:
            guests.append(PveGuest.model_validate(row))
        except ValidationError as exc:
            log.warning("skipping unparseable pve guest: %s", exc.errors()[:2])
    guests.sort(key=lambda guest: guest.vmid)
    return guests


def parse_tasks(payload: Any) -> list[PveTask]:
    """Every task in a ``cluster/tasks`` answer, newest first (server order)."""
    tasks: list[PveTask] = []
    for row in _rows(payload):
        if not isinstance(row, dict):
            continue
        try:
            tasks.append(PveTask.model_validate(row))
        except ValidationError as exc:
            log.warning("skipping unparseable pve task: %s", exc.errors()[:2])
    return tasks


def recent_backup_vmids(
    tasks: Iterable[PveTask],
    *,
    fresh_seconds: float,
    now: float | None = None,
) -> set[int]:
    """VMIDs with a *successful* ``vzdump`` that ended inside the window.

    Three ways to not qualify, all three present in the real task log: a task
    that never finished (no ``endtime``), one that ended with an error text
    instead of ``"OK"``, and one that is simply too old.  A ``now`` in the past
    relative to ``endtime`` -- a clock that moved -- counts as recent, because
    "we cannot tell" must not read as "the backup is stale".
    """
    moment = time.time() if now is None else now
    fresh: set[int] = set()
    for task in tasks:
        vmid = task.vmid
        if task.type != "vzdump" or not task.succeeded or task.endtime is None or vmid is None:
            continue
        if moment - task.endtime <= fresh_seconds:
            fresh.add(vmid)
    return fresh


def humanize_uptime(seconds: int | None) -> str | None:
    """``1727442`` -> ``"19d 23h"``.

    ``None`` stays ``None``: a stopped guest has no uptime, and ``"0s"`` would
    read as "just booted" rather than "not running".
    """
    if seconds is None:
        return None
    seconds = max(0, int(seconds))

    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"
