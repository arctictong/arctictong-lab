#!/usr/bin/env bash
# pve-audit.sh — READ-ONLY audit of a Proxmox VE node
# Does not modify anything. Safe to run as root on the PVE host.
echo "########## HOST / DATE ##########"; hostname; date
echo "########## PVE VERSION ##########"; pveversion -v
echo "########## KERNEL ##########"; uname -a
echo "########## SUBSCRIPTION ##########"; pvesubscription get 2>/dev/null || echo "no subscription / no-subs repo"
echo "########## CLUSTER ##########"; pvecm status 2>/dev/null || echo "STANDALONE (not in a cluster)"
echo "########## NODES ##########"; pvecm nodes 2>/dev/null || true
echo "########## APT REPOS ##########"
cat /etc/apt/sources.list 2>/dev/null
for f in /etc/apt/sources.list.d/*; do echo "## $f"; cat "$f"; echo; done 2>/dev/null
echo "########## STORAGE.CFG ##########"; cat /etc/pve/storage.cfg 2>/dev/null
echo "########## DISKS ##########"; lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT
echo "########## ZFS ##########"; (zpool list && echo "--" && zpool status) 2>/dev/null || echo "no ZFS"
echo "########## LVM ##########"; (vgs && lvs) 2>/dev/null || echo "no LVM"
echo "########## NETWORK ##########"; cat /etc/network/interfaces
echo "########## BOOT ##########"
proxmox-boot-tool status 2>/dev/null
[ -d /sys/firmware/efi ] && echo "boot mode: UEFI" || echo "boot mode: Legacy BIOS"
[ -d /sys/firmware/efi ] && (apt-cache policy grub-efi-amd64 | head -3) || true
echo "########## CEPH ##########"; (ceph --version && ceph -s) 2>/dev/null || echo "no Ceph"
echo "########## HA ##########"; (ha-manager status) 2>/dev/null || echo "no HA"
echo "########## VMs ##########"; qm list 2>/dev/null
echo "########## CONTAINERS ##########"; pct list 2>/dev/null
echo "########## VM CRITICAL CONFIG LINES ##########"
grep -H -E "machine:|hostpci|bios:|ostype:" /etc/pve/qemu-server/*.conf 2>/dev/null
echo "########## GPU / NVIDIA ##########"
lspci 2>/dev/null | grep -Ei "vga|3d|display|nvidia" || echo "lspci not available"
nvidia-smi 2>/dev/null | head -6 || echo "no nvidia-smi"
echo "########## CPU / RAM / ROOT FS ##########"
lscpu | grep -E "Model name|Socket|Core|MHz"
free -h
df -h /
echo "########## PVE8TO9 CHECKLIST (full) ##########"
pve8to9 --full 2>&1 | head -250
