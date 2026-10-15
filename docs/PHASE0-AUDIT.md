# PHASE 0 AUDIT — Proxmox VE Updater GUI v2

Date: 2026-10-15 · Agent: Hermes Agent · Model identifier (verified from live runtime, not changed): `qwen3.8-flash-next-iq3_s` (custom provider) — the maintainer's "Qwen 3.8 Flash" is a display label.

Scope discipline: analysis only. No production code modified. The only file created by this phase is this report. No sudo/root used, no real updates/installs/reboots, no Proxmox GUI files touched, no packages installed, no models downloaded.

---

## 1. Repository map and existing behavior

### 1.1 What is where (verified by inspection)

| Location | What it is | Status |
|---|---|---|
| `/home/hermes/proxmox-ve-updater-gui/` | GUI-v2 development pack (SPEC/ARCHITECTURE/PLAN/TESTING/AGENTS/README/HERMES_QWEN_PROMPT) | Docs only, **not a git repo**. `ollama-check.sh` is listed in its README but **absent** on disk. |
| `/home/hermes/Proxmox-VE-Updater/` | The actual project repo (clone of `github.com/it-koncept/Proxmox-VE-Updater-`) | git `main` @ `eb8376e`, clean, in sync with `origin/main`. |
| `prod-node:/root/update-vms.sh` (<lan-ip>) | Deployed engine v3.4.0 (since 2026-10-15; v2 kept as `.v2.bak`) | Not touched in this phase. |

### 1.2 Repo contents (verified)

```
update-vms.sh          737 lines, v3.4.0 — the whole engine, single file
tests/run_tests.sh     625 lines — mock-based suite, 63 cases
tests/mocks/           pvesh, pct, qm, id, apt-get, pvecm (PATH-shadowed, argv-logging)
tests/fixtures/        resources_basic.json, jobs_all.json, jobs_partial.json, pvecm_real.txt
contrib/systemd/       update-vms.service (oneshot, NoNewPrivileges, PrivateTmp),
                       update-vms.timer (Sun 03:30, jitter, Persistent), env.update-vms
contrib/logrotate/update-vms
docs/CURRENT_STATE.md  Fase A analysis + Fase B status
README.md CHANGELOG.md SPEC.md(older v1.0 spec) ARCHITECTURE.md IMPLEMENTATION_PLAN.md TESTING.md AGENTS.md QWEN_PROMPT.md LICENSE MIT
```

### 1.3 Existing behavior of `update-vms.sh` v3.4.0 (read in full)

- **Inventory:** `pvesh get /cluster/resources --type vm --output-format json`, filtered to node (`PVE_NODE` or hostname). pvesh failure or non-JSON = hard error exit 2 (never silent success).
- **Selection:** exact `;`-separated tag match, case-insensitive: `apt` → noninteractive `apt-get update && upgrade` (confdef/confold), `yum` → `dnf` else `yum`, `windows` → skip; only `running` guests; numeric VMID validation; `--only`/`--exclude` validated as numeric lists.
- **Exec:** LXC via `timeout $PCT_EXEC_TIMEOUT pct exec`; QEMU via `qm guest exec --timeout $QEMU_EXEC_TIMEOUT` with guest rc read from JSON `.exitcode`. Guest-agent preflight (`pvesh …/agent`) — no agent ⇒ *skipped*, never success. Disk preflight (`df -P -k /` in guest, `MIN_GUEST_FREE_MB`); unknown ⇒ skipped.
- **Backup coverage (advisory):** enabled vzdump jobs from `pvesh get /cluster/backup`; not-covered ⇒ warning; unreadable list ⇒ "backup status unknown". Never blocks.
- **Host mode (`--host`):** quorum gate (`pvecm status`, `Quorate: Yes`), free-space gate, typed-YES-on-tty or explicit `--yes`, `apt-get update` failure aborts dist-upgrade, per-phase `HOST_UPDATE_TIMEOUT`, reboot-required marker ⇒ status `warning`, never reboots.
- **Modes:** default update, `--dry-run` (no guest exec at all), `--list` (non-root ok, no lock), `--check`, `--version`, `--report <path>` (JSON via jq only), `--log-file`.
- **Concurrency:** global `flock -n` on `LOCK_FILE` (one run at a time, node-wide).
- **Status model:** `success | warning | skipped | failed | planned`; exit codes `0 ok | 1 guest/host failure | 2 preflight/usage | 3 lock busy`.

### 1.4 Baseline verification actually run in this phase (non-root, dev VM)

- `bash -n update-vms.sh` → clean.
- `shellcheck -S warning update-vms.sh tests/run_tests.sh tests/mocks/*` → clean (ShellCheck 0.11.0).
- `./tests/run_tests.sh` → **63 passed, 0 failed** (mock suite; verified safe by inspection first: it shadows `pvesh/pct/qm/id/apt-get/pvecm` via PATH with argv-logging mocks, writes only under `mktemp -d`, and asserts *absence* of mutating calls).
- Dev machine: Debian 13.7 (trixie), bash 5.2.37, jq 1.7, kernel `6.8.12-20-pve` (this dev VM is itself a guest on prod-node).

No integration tests were run (no disposable PVE test node was used in this phase).

---

## 2. Requirement traceability (GUI-v2 `SPEC.md` → code)

Legend: ✅ exists · ◐ partial · ✖ missing · ❓ needs maintainer decision

| Req | State | Evidence / gap |
|---|---|---|
| FR-001 preserve CLI | ✅ | v3.4 CLI is the contract; GUI must call it, not replace it. |
| FR-002 read-only scan, no installs | ◐ | `--dry-run`/`--list`/`--check` exist; host scan = `apt-get -s` simulation exists only inside `--host --dry-run`. No per-target "what packages will change" scan output for guests. |
| FR-003 target/type/time/result/errors | ✅ | `--report` JSON: vmid, type, name, action, status, reason, duration_s, summary, timings. |
| FR-004 host | ◐ | Update path exists (`--host`); a first-class *scan* verb (`apt list --upgradable`-style, machine-readable) does not. |
| FR-005 LXC eligibility checks | ✅ | running-state, rc checks, timeout, disk preflight. Distro/tooling detection is tag-based, not distro-probed (acceptable; document). |
| FR-006 QEMU only with verified guest mechanism | ✅ | agent preflight; skipped-with-hint otherwise. Fleet fact: 102/104 have no agent ⇒ permanently skipped until enabled. |
| FR-007 preview of package changes | ✖ | dry-run prints commands, not package lists. Needs `apt-get -s` / `apt list --upgradable` capture per target. |
| FR-008 server-side approval bound to target/action | ✖ | CLI has typed-YES (tty only). No durable, server-validated approval token. Core new backend work. |
| FR-009 backup status w/ source+timestamp+freshness | ◐ | Only *job-coverage* config check (advisory). No last-backup-success/timestamp source. PBS is the backup storage on this fleet ⇒ needs PBS API (❓ decision). |
| FR-010 unknown/stale blocks or audited override | ◖ | Current policy = advisory warn (Fase B decision). GUI spec demands block-by-default ⇒ policy change decision. |
| FR-011 job progress/status/exit/error summary | ◐ | End-of-run report exists; no live progress, no job records store. |
| FR-012 durable history store | ✖ | Only flat log/report files; no queryable history, retention is logrotate-only. |
| FR-013 audit actor identity | ✖ | CLI knows no "actor"; backend must record it. |
| FR-014 per-target concurrency + dedup | ◐ | Global flock (node-wide, coarse). No per-target lock, no dedup of identical submissions. |
| FR-015 CLI parity | ✅ | Engine already is the CLI. |
| FR-016 scheduled without browser | ✅ | systemd service/timer shipped (default `--dry-run`). |
| FR-017 reboot-required | ✅ | host marker; guest reboot-required not detected (acceptable v1; document). |
| FR-018 install/upgrade/uninstall docs | ◐ | Install steps in README; no upgrade/uninstall procedure, no packaging. |
| FR-019 clear states | ✅ | status model + exit codes. |
| FR-020 no unverified success | ✅ | rc checks, `.exitcode` parsing, hard pvesh errors. |
| SEC-001..005,007..014 (engine side) | ✅/◐ | Largely satisfied by v3 hardening (see CHANGELOG); approval/identity/CSRF/transport are backend concerns (✖). |
| SEC-006 least privilege | ❓ | Backend must be non-root; host updates need root ⇒ sudo-allowlist design decision. |
| SEC-016/017 service account + audit | ✖ | Scheduled runs are local root systemd today; GUI-era model needs defined identity + audit. |

Biggest structural gaps: **no backend, no auth, no durable approval, no history store, no per-target locking, no package-change preview, no backup freshness source.**

---

## 3. Supported versions — verified vs assumed

**Verified facts:**
- prod-node production node: **PVE 8.4.21**, kernel 6.8.12-20-pve (recorded with commands in `docs/CURRENT_STATE.md`, 2026-10-15).
- Dev environment: **Debian 13.7 trixie** (`/etc/os-release`, this phase).
- Engine runtime deps: bash, jq, pvesh, pct, qm, flock, timeout — all present on PVE hosts except **jq** (README documents `apt install jq`).
- Upstream current release line (Roadmap wiki, fetched 2026-10-15): **PVE 9.2** (also 9.1, 9.0 documented); PVE 9.0 ships on **Debian 13** (Proxmox press release + Package Repositories wiki showing trixie suites).

**Assumptions to confirm (maintainer):**
- Target fleet = prod-node-class nodes on **PVE 8.4.x (Debian 12 Bookworm)** — 8.x↔Bookworm mapping is widely documented but was not re-verified against 8.4 release notes this phase.
- Whether the release must also run on PVE 9.x (Debian 13) at GA time.
- No evidence in the repo of any explicit version-support policy.

---

## 4. Proxmox API / auth / GUI customization — research findings (authoritative sources fetched 2026-10-15)

1. **REST API is the supported integration surface.** `https://pve.proxmox.com/wiki/Proxmox_VE_API` + `pve-docs/api-viewer`: REST-like, JSON-Schema-defined, `pvesh` exposes the same API locally. **API stability policy:** compatible within a major version; cross-major (8→9) changes possible and documented as breaking (e.g. PVE 9.0 removed the `VM.Monitor` privilege — Roadmap wiki).
2. **Auth/ACL** (`pve-docs/chapter-pveum.html`): ticket auth (cookie) **plus mandatory `CSRFPreventionToken` header on POST/PUT/DELETE**; or **API tokens** (`Authorization: PVEAPIToken=USER@REALM!TOKENID=UUID`, stateless, no CSRF needed, expire-able, revocable). Tokens with *privilege separation* get their own ACLs, always a subset of the user's. Roles/privileges are role+path based: `PVEAuditor` (read-only), `VM.Audit`, `Sys.Audit`, `VM.GuestAgent.*`, `Sys.Modify`/`Sys.PowerMgmt` for host-level ops. Per-method required privileges are documented in the API viewer.
3. **GUI customization: there is NO officially supported mechanism to add views/tabs/menu items to the PVE web UI.** Evidence:
   - Forum thread *"Supported way to extend the PVE web UI for a third-party package?"* (forum.proxmox.com, thread 185017, Jul 2026): a vendor asks exactly this; Proxmox staff (cheiss) answers only that **GUI support for *storage plugins* is "currently being worked on" with no timeline** (pve-devel series 20260623143402). No general UI-extension hook exists or is scheduled. The vendor's current approach (patching `index.html.tpl` + `PVE/API2/Nodes.pm` with a dpkg trigger) is community practice, **not endorsed**; the thread itself notes the risk of breaking the GUI on updates.
   - Forum thread 184971 (2026): community consensus + staff direction = *"creating a custom web frontend using the Proxmox VE API"* is the way to build your own UI.
   - Forum thread 143667: staff-known embedding option is only a **markdown link in the (node/VM) comments field**; iframing is discouraged ("embedding/iframe is always a very bad idea security wise").
   - **Conclusion: any claim that a custom menu plugin is officially supported would be false for PVE 8.4 and 9.x.** PVE 9 keeps the ExtJS main GUI (gradual Rust/Yew migration per Roadmap; mobile UI is Yew) — the main GUI remains closed to third-party extension in both generations.
4. **Proxmox Datacenter Manager (PDM)** is a separate product with its own access-control model — not an extension point for PVE node UIs.

---

## 5. Integration options compared + recommendation

| | A. API-based integration (own backend using PVE API/auth, no UI patching) | B. Companion UI linked from PVE | C. Version-pinned GUI extension (patch index.html.tpl / API2 route) |
|---|---|---|---|
| Official support | ✅ API is the documented, schema-defined, stability-committed surface | ✅ (it's just option A + a documented entry point) | ❌ No supported hook exists (see §4); community patches exist but are explicitly not endorsed |
| Survives PVE upgrades | ✅ within major version; 8→9 needs re-test | ✅ | ❌ every `pve-manager`/`proxmox-widget-toolkit` upgrade overwrites patches; dpkg conflicts; can brick the GUI; ExtJS→Yew migration makes it a moving target |
| "Visible from Proxmox VE" | Partial — reachable from the node, not a native tab | Partial — entry via node-comment markdown link or bookmark | Full native tab |
| Auth/ACL reuse | ✅ verify PVE ticket/CSRF or scoped API tokens; PVE remains the identity store (SEC-001) | same | Would piggyback on pveproxy session, but only by patching pve-manager (unsupported) |
| Security posture | Good: own narrow API, CSRF design, no touching PVE files | same | Poor: modifies the attack surface of the shipped GUI; unsupported-state for support contracts |
| Effort | Medium (small backend + thin UI) | Medium (A + docs/link) | Medium now, **recurring tax every PVE point release forever** |

**Recommendation: A+B hybrid — a small, node-local companion backend + minimal web UI, authenticated against PVE, entered from PVE via a documented markdown link in the node comment (or bookmark), with the Bash engine as the only executor.**

Rationale: it is the only option that (a) has authoritative evidence of being supported, (b) satisfies SEC-015 (no Proxmox file modification), (c) reuses PVE auth/ACL (SEC-001/002), and (d) has no upgrade-recurring breakage. Option C is rejected: no proof of support, direct conflict with SPEC §3 and SEC-015, and the July-2026 staff answer confirms no mechanism exists even for the storage-plugin case yet. If the maintainer later wants a *native* tab, revisit when the storage-plugin GUI work lands upstream — do not pre-commit to patching.

**Compatibility/upgrade risks of the recommendation (must be documented):** API breakage across PVE majors (8→9: e.g. privilege renames) ⇒ ship a version-compat check that disables GUI actions on untested PVE versions; PVE 9's UI rewrite does not affect us (we don't touch it); the markdown-link entry point is cosmetic and may vanish if comments are edited — document bookmark fallback.

---

## 6. Threat model (proposed)

Assets: the PVE node and all its guests (root-level package installs), PVE credentials, job history.
Adversaries: network-adjacent attacker, malicious/low-priv PVE user, CSRF from attacker site, compromised guest, operator error, race conditions.

| Threat | Mitigation (design) |
|---|---|
| **Arbitrary command execution** | Backend exposes a closed verb set (`scan`, `update`, `status`, `history`) with typed params only (VMID `^[0-9]+$`, enum target-type, enum operation). No shell interpolation anywhere: backend execs `update-vms.sh` with a fixed argv array; engine already rejects unknown args, validates IDs/env, uses no `eval`, and hardcodes the only guest commands (apt/dnf). Guest-side commands are constants in the engine, never user data. |
| **Privilege escalation** | Backend runs as dedicated non-root user `pveupd`. Root is reached only via a **narrow sudoers drop-in** allowing exactly `/usr/local/bin/update-vms.sh` with a fixed flag set (no `ALL`, no wildcards on args, `NOPASSWD` only for those lines), or — preferred if maintainer accepts — the update verb is executed by a small root-side helper that reads jobs from a queue directory writable only by `pveupd` (mode 0750, group `pveupd`). Engine units keep `NoNewPrivileges`/`PrivateTmp`. Never make the web server root (SEC-004). |
| **Authorization bypass** | Every request authenticated by **verifying the caller's PVE ticket against the PVE API** (`GET /api2/json/access/permissions` with the ticket cookie + CSRF header on writes) and enforcing role checks server-side: read verbs need `Sys.Audit`/`VM.Audit`-class rights; update verbs require an explicit admin role/path (SEC-002/003). Hiding buttons is cosmetic only. Scheduled runs use a dedicated API token with privsep + expiry (SEC-016/017). |
| **CSRF** | Browser flow uses PVE ticket + `CSRFPreventionToken` echo-check (same pattern PVE itself documents); API-token auth is never accepted from the browser context. SameSite=Strict session cookie if the backend mints its own session. |
| **Stale/forged approval** | Approval is a server-side record: `{job_id, target, operation, preview_fingerprint (hash of the scan result), actor, approved_at, TTL}`. Execution re-scans and compares fingerprint; mismatch/expiry ⇒ `blocked`, fresh approval required. Browser confirmation alone is never trusted (SPEC §6). |
| **Concurrent jobs / duplicate submission** | Keep engine's global flock; add **per-target lock** (`flock /var/lock/update-vms-<type>-<vmid>.lock`) so a GUI job and a timer run can't hit the same target; backend dedups by (target, operation) while a job is non-terminal; idempotency key on submission. |
| **Unsafe logs / history** | Log files 0640 root:pveupd; history store dir 0750; atomic writes (tmp+rename); retention policy + logrotate shipped; UI renders logs as escaped text (never innerHTML); no env dumps, no tokens in logs (engine already logs only fixed argv); redaction test in suite. |
| **Network exposure** | Backend binds the node LAN address (or localhost + existing reverse proxy decision ❓) with TLS (self-signed + fingerprint pinning, or ACME ❓ decision); port documented; PVE auth means leaked endpoint ≠ leaked credentials. |
| **Timeouts/interruption** | Engine timeouts already exist; backend adds job-level timeout + SIGTERM handling; interrupted jobs recorded `interrupted`, never `succeeded` (SEC-010). |
| **Backup blind spot** | Coverage-unknown stays `unknown`; policy decision (FR-010) makes unknown/stale block by default with audited override recorded in history. |

---

## 7. Proposed architecture and files likely to change (no changes made)

```
update-vms.sh              engine — KEEP; extend verb set (scan w/ package preview,
                           per-target locks, machine-readable scan JSON, backup-status verb)
lib/                       (optional split later; not required for v1)
backend/                   NEW: small HTTP service (language ❓ — see Q3)
  auth.py|pl               PVE ticket/CSRF verification, role checks, API-token path for scheduler
  api.py|pl              closed verb set, request validation, approval binding
  jobs.py|pl           job lifecycle (queued→…→succeeded/failed/interrupted), per-target locks
  store.py|pl          JSONL/SQLite history under /var/lib/pve-updater (0750)
ui/                        NEW: static single-page UI served by backend (scan table,
                           preview, approval dialog, job status, history)
integration/
  sudoers.d.pve-updater    NEW: narrow allowlist (or queue-helper design)
  systemd/pve-updater.service|timer   NEW: backend unit (non-root)
  contrib/systemd/*        existing engine units unchanged; scheduled run switches to API-token identity ❓
tests/
  run_tests.sh + mocks/    extend: scan verbs, per-target lock, backup states, approval, injection
  backend tests            NEW: authz, CSRF, stale approval, concurrency, history perms
docs/                      install/upgrade/uninstall, permissions, compatibility matrix, recovery
```

Existing files expected to change in later phases: `update-vms.sh`, `tests/run_tests.sh`, `tests/mocks/*` (new fixtures), `contrib/systemd/*`, `README.md`, `CHANGELOG.md`, `docs/CURRENT_STATE.md`. Proxmox-shipped files: **none, ever** (SEC-015).

---

## 8. Mock test plan (extends existing 63-case suite; all mocked, never real)

1. **Host:** scan lists upgradable packages via mocked `apt-get -s`/`apt list`; scan asserts *absence* of install calls; update path: update-fail aborts dist-upgrade; quorum lost; low disk; reboot marker; unattended refusal. (Most exist; add scan-verb cases.)
2. **LXC:** running/stopped, `pct` failure, timeout (rc 124), malformed entry, disk-preflight fail/unknown. (Exist; keep as CLI regression.)
3. **QEMU/KVM (guest-agent):** agent down ⇒ skipped; `qm guest exec` rc≠0 via `.exitcode`; agent ping JSON garbage ⇒ hard error; verified success only when `.exitcode==0`. (Exist; add scan-verb.)
4. **Backup state:** fresh-success / failed / missing / unknown / stale / authorized-override — each asserted for the *policy outcome* (block vs warn vs proceed) and recorded status; PBS last-backup source mocked once Q5 is decided.
5. **Authorization:** backend tests with mocked PVE permission endpoint: unauthenticated ⇒ 401; auditor token ⇒ scan ok, update ⇒ 403; token expiry ⇒ 401.
6. **Approval:** missing approval, wrong actor, expired TTL, changed target, changed preview fingerprint ⇒ all rejected; approval bound to exact scan fingerprint.
7. **Concurrency:** two submissions same target ⇒ second rejected/serialized (per-target lock); engine flock exit 3 preserved; timer+GUI collision test.
8. **Timeout/interruption:** SIGTERM mid-job ⇒ status `interrupted`, lock released, never `succeeded`; backend restart recovers stale `running` rows as `interrupted`.
9. **History/logging:** permissions 0640/0750 asserted; atomic write (no partial JSONL); retention; redaction (token-like strings never written); audit fields (actor, target, approval, outcome) present.
10. **Injection:** hostile VMIDs, shell metachars in names/tags, oversized inputs, path traversal in report/log paths ⇒ rejected pre-exec; call-log assertions prove no mutating mock call.
11. **CLI regression:** the existing 63 cases must stay green unchanged (FR-001 gate).
12. **Packaging:** install→upgrade→uninstall run twice in a disposable container/VM (idempotent); compat check refuses on untested PVE version; uninstall leaves PVE untouched (dpkg-verify of pve-manager conffiles).

---

## 9. Install / upgrade / uninstall / recovery (outline)

- **Install:** files under `/opt/pve-updater/` + `/usr/local/bin/update-vms.sh`; state `/var/lib/pve-updater` (root:pveupd 0750); logs `/var/log/update-vms` (0640); sudoers drop-in `/etc/sudoers.d/pve-updater` (validated with `visudo -cf`); systemd units for engine (existing) + backend (new, non-root); TLS cert (source ❓); entry link added to node comment (documented, reversible). Idempotent: re-running install converges.
- **Upgrade:** stop backend, keep engine CLI contract; run mock suite + compat check against installed PVE version before enabling GUI verbs; history schema versioned + migrated; engine upgrades are file-replacement with `.bak` (proven practice on this fleet).
- **Uninstall:** disable+remove units, remove sudoers drop-in, remove `/opt/pve-updater`, optionally keep history; verify `dpkg -V pve-manager proxmox-widget-toolkit` clean (proof nothing PVE-owned was touched).
- **Recovery:** worst case = backend down ⇒ CLI + timer still work (engine independent); sudoers mistake ⇒ drop-in is a single removable file, `visudo -cf` gate prevents syntax breakage; never any PVE file to restore.

---

## 10. Open questions for the maintainer

1. **Target versions:** ship for PVE 8.4.x (current fleet) only, or 8.4.x **and** 9.x at GA? (API/privilege differences across majors; dev box is Debian 13.)
2. **Entry-point acceptance:** is a node-comment markdown link + bookmark an acceptable "visible from Proxmox VE", given no native tab is officially possible? (Or accept the documented unsupported-patch risk of option C — recommendation is no.)
3. **Backend language/dependency:** Python 3 (stdlib+small deps, available on both Debian 12/13) vs pure-Bash HTTP shim vs Perl (PVE-native)? Affects maintainability; Bash-first constraint applies to the *engine*, not necessarily the adapter.
4. **Root handoff:** narrow sudoers allowlist vs root queue-helper daemon — which privilege model does the maintainer accept?
5. **Backup freshness source:** PBS is the backup storage on this fleet; reading last-backup-success needs the PBS API. In scope, or stay with job-coverage advisory + `unknown` policy?
6. **FR-010 policy change:** current shipped behavior is *advisory warn*; GUI spec demands block-by-default for unknown/stale. Confirm the switch and the override procedure.
7. **Packaging:** keep script+units install docs, or produce a `.deb`?
8. **TLS/port:** self-signed + pinned fingerprint, ACME, or localhost behind an existing proxy? Which port?
9. **Scheduled-run identity:** keep local systemd root run, or move to dedicated API-token identity with audit rows (SEC-017)?
10. **History retention** period and whether per-guest package-change previews must be retained in history (size vs audit).
11. Dev-pack housekeeping: `ollama-check.sh` referenced in the pack README is missing — remove the reference or add the script?

---

## Sources fetched (2026-10-15)

- https://pve.proxmox.com/wiki/Proxmox_VE_API (auth, CSRF token, API tokens, stability policy)
- https://pve.proxmox.com/pve-docs/chapter-pveum.html (API tokens, privsep, roles/privileges)
- https://forum.proxmox.com/threads/supported-way-to-extend-the-pve-web-ui-for-a-third-party-package.185017/ (staff: no UI-extension hook; storage-plugin GUI "being worked on")
- https://forum.proxmox.com/threads/suggestion-customizable-proxmox-views.184971/ (custom frontend via API is the path)
- https://forum.proxmox.com/threads/customize-proxmo-webui.143667/ (markdown-link comment; iframe discouraged)
- https://pve.proxmox.com/wiki/Roadmap (PVE 9.0/9.1/9.2 current; ExtJS→Yew gradual; 9.0 breaking changes)
- https://proxmox.com press release PVE 9.0 + https://pve.proxmox.com/wiki/Package_Repositories (PVE 9 = Debian 13)
- Repo-local verified evidence: `docs/CURRENT_STATE.md` (PVE 8.4.21 on prod-node), `/etc/os-release` (Debian 13.7 dev box), test-suite run this phase (63/63).

**Status: Phase 0 complete. No project code changed. Awaiting maintainer approval before starting Phase 1 (stabilize Bash engine).**
