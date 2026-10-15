# Backend architecture & operations (Phase 2)

Decisions taken (were open questions Q3/Q4 in the audit; maintainer can
override): **Python 3 stdlib only** (runs on Debian 12/13 with no pip
dependencies) and **root queue-helper** (argument-free sudo rule; no sudoers
argv wildcards).

## Components
- `backend/server.py` — ThreadingHTTPServer with a closed verb set:
  `scan`, `backup-status`, `jobs` (read); `jobs create/approve/start` (update).
  No endpoint accepts a command, path, or shell fragment — only typed
  target ids (`^[0-9]{1,8}$` / node name regex) and enum operations.
- `backend/pveauth.py` — PVE is the identity store: the caller's
  `PVEAuthCookie` ticket is verified by calling PVE's own
  `/access/permissions?effective=1`; the same response drives server-side
  authorization (read vs update privileges per target path). CSRF writes
  must echo PVE's `CSRFPreventionToken` for that ticket. API tokens are not
  accepted from browsers.
- `backend/jobs.py` — sqlite history (`history.db`, 0640) + audit table;
  job state machine per SPEC §6; unique partial index = one active job per
  target; approval bound to preview fingerprint with TTL.
- `backend/runner.py` — the ONLY root-side component. Invoked with zero
  arguments via sudoers. Re-validates every job file (strict schema, numeric
  ids, operation allowlist, approval TTL), re-runs the scan and compares the
  fingerprint (stale preview -> `blocked`), then execs the Bash engine with a
  fixed argv list. Timeouts -> `interrupted`, never `succeeded`.

## Trust boundaries
browser (untrusted) -> server.py (authN/authZ/validation) -> queue file
(0640, group pveupd) -> runner (root, re-validates) -> update-vms.sh (engine,
own locks/gates). Every hop re-checks the previous hop's work.

## Install (documented, never automatic)
```
useradd -r -s /usr/sbin/nologin -d /var/lib/pve-updater pveupd
install -d -m 0750 -o pveupd -g pveupd /var/lib/pve-updater
install -d /opt/pve-updater && cp -r backend /opt/pve-updater/
install -m 0755 backend/runner.py /usr/local/sbin/pve-updater-runner
install -m 0640 backend/config.json.example /etc/pve-updater/config.json  # EDIT
install -m 0440 integration/sudoers.d.pve-updater /etc/sudoers.d/pve-updater
visudo -cf /etc/sudoers.d/pve-updater        # refuse to continue on error
install -m 0644 integration/systemd/pve-updater.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now pve-updater
```
Entry point from the PVE GUI: markdown link in the node comment field
(supported community pattern), e.g. `[Updater](https://<node>:8765/)`.

## TLS / exposure
Default bind is 127.0.0.1 (dev/test). LAN exposure requires TLS — either put
behind a reverse proxy with the PVE cert or set `bind` plus a cert-terminating
proxy; decision Q8 still open. Never bind a raw HTTP port to a public iface.

## Upgrade / uninstall / recovery
- Upgrade: stop unit, replace `/opt/pve-updater`, run the mock suites, restart.
  History schema is versioned; queue files are inert across restarts (stale
  `running` jobs are surfaced as `interrupted` on next start — Phase 3 task).
- Uninstall: disable/remove unit, remove sudoers drop-in, `/opt/pve-updater`,
  user, and `/var/lib/pve-updater`. Nothing PVE-owned was ever touched;
  `dpkg -V pve-manager proxmox-widget-toolkit` stays clean.
- Recovery: backend down => CLI + systemd timer keep working (engine is
  independent). Broken sudoers drop-in => single removable file, gated by
  `visudo -cf` at install.

## Tests
`python3 tests/backend/run_backend_tests.py` — 43 cases against a fake PVE
API and a fake engine stub: authN (401s, CRLF), login relay (wrong pw 401,
rate-limit 429 after 5 fails, HttpOnly/SameSite=Strict cookie, TFA challenge
roundtrip), logout cookie clear, authZ (auditor vs admin, host policy), CSRF,
injection/bad-enum inputs, approval fingerprint binding, stale approval ->
blocked, changed preview -> blocked, duplicate target -> 409, runner tamper
rejection, timeout -> interrupted, history/log 0640, audit attribution,
static UI (CSP header, traversal 404), guest-list proxy. No real apt/pct/qm/
PVE calls.

`tests/backend/dev_harness.py [port]` — same fakes, fixed port, for manual/
browser QA of `ui/` (login admin@pve / pw-admin are fake credentials).

## Packaging (Phase 6)
`packaging/build_deb.sh` builds `pve-updater_<ver>_all.deb` WITHOUT root
(`dpkg-deb --root-owner-group`). It refuses to build unless the engine at
`../Proxmox-VE-Updater/update-vms.sh` reports the pinned version (v3.5.0),
so a package can never ship an untested engine. Layout:
- `/usr/local/bin/update-vms.sh` — engine v3.5.0 (the only package-mutating
  component; the pre-existing `/root/update-vms.sh` is NOT touched — the
  config's `engine` path decides which one runs)
- `/usr/local/sbin/pve-updater-runner` — root runner (sudoers target)
- `/opt/pve-updater/{backend,ui,docs}` — backend + static UI + docs
- `/etc/pve-updater/config.json`, `/etc/sudoers.d/pve-updater` — conffiles
  (dpkg prompts on upgrade; local edits are never silently discarded)
- `/etc/systemd/system/pve-updater.service` — replaced on upgrade, then
  `daemon-reload`

postinst (idempotent): creates system user/group `pveupd`, state dir 0750,
fills `node` from hostname on first install, sets config 0640 root:pveupd,
sudoers 0440 root:root + **aborts the install if `visudo -cf` fails**,
verifies the packaged engine answers `--version`, enables the unit, restarts
only if it was already running. prerm stops/disables the service before
file replacement. postrm purge deliberately KEEPS `/var/lib/pve-updater`
(sqlite job history + logs are audit data) and the service account; removal
is a documented manual step after review.

Install (on a PVE node, maintainer-approved): `apt install
./pve-updater_0.9.0-10_all.deb`. Upgrade: same command; conffile prompts
protect local config/sudoers. Uninstall: `apt purge pve-updater` (audit data
kept by design). Recovery: engine CLI + its systemd timer keep working even
with the package removed; nothing PVE-owned is ever patched
(`dpkg -V pve-manager proxmox-widget-toolkit` stays clean).

Verified in this phase: deb builds reproducibly from a clean tree, archive
listing shows correct modes/ownership (dirs 0755, sudoers 0440 via
postinst, engine+runner executable), packaged config.json is valid JSON,
maintainer scripts pass `bash -n` + ShellCheck. NOT yet verified: an actual
install on a PVE node (needs maintainer go; postinst paths depend on
root-only tools that cannot run on the dev box).

## QEMU/KVM guests (Phase 5)
Execution inside QEMU guests goes through `qm guest exec`, which requires a
RESPONDING qemu-guest-agent (installed in the guest OS AND `agent: 1` in the
VM config). The engine's preflight skips non-responding guests (job status
`unsupported`, never `succeeded`). Timeout layering: the engine passes
`--timeout 1800` (env QEMU_EXEC_TIMEOUT) to `qm guest exec`; the backend's
`update_timeout` must stay strictly larger (default 7200) so the runner-level
timeout only fires as the outer backstop. The engine never reboots guests;
kernel/libc updates may therefore need a manual maintenance window — the UI
shows the engine's per-guest outcome verbatim. The UI's guest list surfaces
each QEMU guest's agent state (warn-styled when not running) so operators
see why a QEMU target will be skipped before creating a job.
Live status of this fleet (2026-10-15): NO QEMU guest on prod-node answers its
agent (102/130 have agent=1 configured but do not respond; 104/147 have no
agent configured). End-to-end QEMU execution against a real agent is
therefore NOT yet verifiable on this fleet without installing
qemu-guest-agent inside a guest (production change, needs maintainer go).

## GUI (Phase 3)
`ui/` is static vanilla JS served by the backend itself (fixed asset map,
CSP `default-src 'self'`, no inline scripts, all rendering via textContent).
Flow: login (relayed to PVE /access/ticket; 2FA challenge supported) ->
guest list proxied through the caller's own PVE ticket (PVE ACLs decide what
you see) -> per-target scan preview -> job create -> approve by pasting the
fingerprint -> start -> status polling -> history + log tail.
Notes: the CSRF token is handed to JS once at login and kept in memory only;
the session cookie is HttpOnly. Logout clears our cookie — a PVE ticket
cannot be revoked server-side and stays valid at PVE until expiry.
Login failures are throttled in memory per IP (5 fails -> 30 s block).
