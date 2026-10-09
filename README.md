# arctictong-lab

Homelab ops workspace: the Proxmox/headscale environment, the aggregation
dashboard that reads it, and the board of tickets it was built from.

Every sibling project is its own repository and is **not** part of this one:

| project | what it is | where it lives |
|---|---|---|
| [`tsguard`](https://github.com/arctictong/tsguard) | Tailscale/Headscale watchdog, Telegram command center | separate repo |
| [`hsbridge`](https://github.com/arctictong/hsbridge) | WireGuard hub relay + work-side shim | separate repo |
| `note-app` | notes web app (runs on CT 105) | local for now |

## What is here

- **`dashboard/`** — the aggregation dashboard on CT 109. Three read-only panels
  (headscale nodes, service probes, Proxmox containers/VMs) behind
  `/api/nodes`, `/api/services`, `/api/pve/summary`, plus the `/dashboard/` page.
  Python + FastAPI; `dashboard/README.md` is the entry point.
- **`ha-weather/`** — Home Assistant rain-radar + PM2.5 + nowcast package for
  Bueng Kum, Bangkok: telegrams a rendered map and scores the forecast models
  against what actually fell. `ha-weather/README.md` is the entry point.
- **`tmd-radar/`** — two standalone pages that view TMD public radar products
  (the national composite and the BKK nowcast), plus the PHP CORS proxy the
  nowcast page fetches through. See `tmd-radar/README.md`.
- **`ops/`** — by-hand homelab ops scripts that belong to no deployed service;
  currently the read-only Proxmox audit `pve-audit.sh`. See `ops/README.md`.
- **`note-app-config/`** — the few tracked files that carry the note-app
  credential fix (the app itself is local, not version controlled).
- **`.scratch/ops-dashboard/`** — the board (`README.md`) and the tickets the
  dashboard and tsguard v2 were sliced into (`issues/01` … `issues/09`). These
  are the reasoning behind the code, not the code itself: what was tried, what
  was cut, and which claims were later proven wrong.

## Environment it was built against

| host | role | address |
|---|---|---|
| CT 106 | headscale | — |
| CT 107 | hubrelay (WireGuard relay, nginx stream on 443) | `192.168.1.198` |
| CT 108 | tsguard | `192.168.1.199` / `100.64.0.16` |
| CT 109 | dashboard | `192.168.1.200` |
| — | Proxmox API (read-only token) | `192.168.1.190:8006` |

The board in `.scratch/ops-dashboard/README.md` is the primary source for how
each piece actually behaves — it records the facts discovered the hard way,
including the ones that contradicted an earlier conclusion.

