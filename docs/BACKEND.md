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
`python3 tests/backend/run_backend_tests.py` — 33 cases against a fake PVE
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
