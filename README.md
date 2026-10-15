# Proxmox VE Updater — GUI

Web UI + REST API companion for the [Proxmox VE Updater Bash engine](https://github.com/it-koncept/Proxmox-VE-Updater-).
Scan, preview and run guest/host updates from a browser — while the proven
Bash engine stays the only thing that touches your guests.

## What you get
- Live guest list, per-guest update preview, backup-coverage check
- One-click update jobs with protected history, logs and audit trail
- Login with your existing PVE account (ticket + CSRF); role-based authZ
  (read-only vs update privileges)
- Unprivileged service; updates execute via a narrow, argument-less
  root-runner sudoers rule
- `require_approval` mode (default off) for two-step approve→start

## Install
On the PVE node (see [docs/INSTALL.md](docs/INSTALL.md) for details):

    curl -fsSL https://raw.githubusercontent.com/it-koncept/Proxmox-VE-Updater-Gui/main/install.sh -o install.sh
    less install.sh   # review before running
    sudo bash install.sh

Then tunnel to the UI: `ssh -L 8765:127.0.0.1:8765 root@<node>` →
http://localhost:8765/

Prebuilt `.deb` assets are on the [Releases](../../releases) page.

## Docs
- [docs/INSTALL.md](docs/INSTALL.md) — install, config reference, roles, uninstall
- [docs/BACKEND.md](docs/BACKEND.md) — architecture, API, security model
- [docs/PHASE0-AUDIT.md](docs/PHASE0-AUDIT.md) — original integration audit
- `SPEC.md` / `ARCHITECTURE.md` / `IMPLEMENTATION_PLAN.md` / `TESTING.md` —
  development specification this project was built from

## Development
    python3 tests/backend/run_backend_tests.py   # 48 tests, fake PVE + fake engine
    ./packaging/build_deb.sh                     # build the .deb (rootless)

## Status
Live-tested on a production PVE 8.4 node (login, scan, backup-status,
update jobs, cancel). See the releases for the change history.
