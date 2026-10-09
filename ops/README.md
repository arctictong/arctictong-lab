# ops — homelab operations scripts

Standalone scripts run **by hand** against the homelab hosts. They are not part
of any deployed service; each one is a runbook in script form.

| file | what it does |
|---|---|
| `pve-audit.sh` | Read-only audit of a **Proxmox VE** node: host/date, PVE version, kernel, subscription, cluster status, apt repos, storage cfg, disks, ZFS/LVM, network, boot mode, Ceph, HA, VMs/CTs, GPU, CPU/RAM/rootfs, and `pve8to9 --full`. Modifies nothing — safe to run as root on the PVE host. |

## Run

The PVE host in this lab is `192.168.1.190` (see
`.scratch/ops-dashboard/README.md`). Copy the script over and capture its output:

```bash
scp ops/pve-audit.sh root@192.168.1.190:/root/
ssh root@192.168.1.190 'bash /root/pve-audit.sh' > pve-audit.txt 2>&1
```

The `pve8to9 --full` section is the bulk of the output; everything above it is
the context that makes the checklist readable.
