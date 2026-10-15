# Installation

`pve-updater` is a companion UI + API for the Proxmox VE guest updater Bash
engine. It runs as an unprivileged systemd service on ONE PVE node and
executes updates only through a narrow root-runner sudoers rule.

## Requirements
- Proxmox VE 8.x node (tested on 8.4)
- `jq` (engine dependency), `python3` (shipped dependency pulls it in)
- root shell on the node

## Install (recommended)
This repo is currently **private**, so downloads need a token (or `gh`).
Copy `install.sh` from the repo, review it, then run on the node as root:

    GH_TOKEN=*** bash install.sh

(fine-grained PAT, `contents:read` on this repo only — create at
GitHub → Settings → Developer settings → Fine-grained tokens; delete it
afterwards if you like.) Or with `gh` installed on the node:

    gh release download --repo it-koncept/Proxmox-VE-Updater-Gui --pattern '*.deb'
    sudo apt-get install ./pve-updater_*_all.deb
    sudo systemctl enable --now pve-updater

The script fetches the latest release `.deb`, installs it, and enables the
service. Re-running upgrades in place.

## Install (manual)
Download `pve-updater_<version>_all.deb` from the GitHub Releases page and:

    sudo apt-get install ./pve-updater_<version>_all.deb
    sudo systemctl enable --now pve-updater

## First access
The UI binds to `127.0.0.1:8765` only (never exposed to the LAN by default).
From your workstation:

    ssh -L 8765:127.0.0.1:8765 root@<pve-node>
    # then open http://localhost:8765/

Login uses your normal PVE account (ticket + CSRF); API tokens are not
accepted from browsers by design.

## PVE roles
- Browse/scan/history: `PVEAuditor` (read-only) is enough.
- Start/cancel updates: needs `VM.Allocate` — e.g. `PVEVMAdmin` on `/vms`:

      pvesh set /access/acl -path /vms -users <user>@pve -roles PVEVMAdmin

## Configuration
`/etc/pve-updater/config.json` (created on first install; NOT overwritten on
upgrade):

| key | default | meaning |
|---|---|---|
| `node` | — | this PVE node's name (host updates target it) |
| `engine` | `/usr/local/bin/update-vms.sh` | Bash engine path |
| `runner_cmd` | `["sudo","/usr/local/sbin/pve-updater-runner","--config","/etc/pve-updater/config.json"]` | how the service invokes the root runner |
| `require_approval` | `false` | `true` = two-step approve (TTL) before start; `false` = ready→start directly |
| `approval_ttl_s` | `900` | approval validity when `require_approval=true` |
| `update_timeout` | `7200` | per-job engine timeout |

Restart after edits: `systemctl restart pve-updater`.

## Upgrade / uninstall
    sudo apt-get install ./pve-updater_<newversion>_all.deb   # upgrade
    sudo apt-get purge pve-updater                            # remove (keeps audit data)
    sudo rm -rf /var/lib/pve-updater                          # purge audit/history too

## Security model (short version)
- Service user `pveupd` is unprivileged; the ONLY root path is the sudoers
  rule for `/usr/local/sbin/pve-updater-runner` (no arguments).
- The runner validates every queued job (schema, permissions, target) before
  invoking the engine; the engine re-checks package state inside each guest.
- UI/API is localhost-only unless you deliberately change the bind address.
- Audit trail: `/var/lib/pve-updater` (jobs, logs, who/when), files 0640.
