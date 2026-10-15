# Implementation Plan

## Phase 0 — Repository and integration discovery (analysis only)
**No production code edits.**
1. Inspect current repository, README, script entry points, tests, and release conventions.
2. Identify current supported Proxmox VE and Debian versions from project evidence; ask if unclear.
3. Map existing capabilities to `SPEC.md`.
4. Verify current Proxmox API, authentication/ACL, GUI extension possibilities, and upgrade compatibility from authoritative docs/source.
5. Compare integration approaches and select one with evidence.
6. Document security boundaries, installation/uninstall, and test strategy.
7. Report blockers and ask for explicit approval before Phase 1.

**Acceptance:** a written audit, feature matrix, integration decision record, proposed file plan, threat model, and test plan. No code changes.

## Phase 1 — Stabilize Bash engine
- Preserve CLI and scheduled invocation.
- Define stable scan/update/check-backup/verify interfaces.
- Standardize exit codes and machine-readable output (JSON if practical and safely generated).
- Add locks, timeouts, dry-run, and structured logs.
- Validate targets and never use `eval`.
- Add mocks before refactoring.
**Acceptance:** existing behavior retained and unit tests pass.

## Phase 2 — Backend and authorization
- Implement narrow allowlisted actions.
- Connect to Proxmox identity/permissions using the chosen supported mechanism.
- Enforce read vs update rights on server side.
- Implement immutable preview/approval binding, per-target locks, job lifecycle, and audit history.
- Do not grant a generic web server root privileges.
**Acceptance:** security tests prove unauthorized and malformed requests are rejected.

## Phase 3 — GUI and status/history
- Add node/target overview, scan, package/action preview, approval, job status, backup status, history and logs.
- Show unsupported targets and stale/unknown backup states clearly.
- Avoid claims of in-guest VM updates without a verified guest mechanism.
**Acceptance:** end-to-end UI flow works in test environment without production changes.

## Phase 4 — Host and LXC execution
- Implement host updates first.
- Add LXC support with explicit eligibility checks and configurable policy.
- Verify command outcomes and reboot-required state.
- No automatic host reboot in v1.
**Acceptance:** mocked failure paths and test-node runs pass.

## Phase 5 — QEMU/KVM guest support
- Inventory possible guest mechanisms and prerequisites.
- Support only documented/configured guest update channels.
- Mark unsupported/offline guests clearly.
**Acceptance:** no guest is reported updated unless guest-side result confirms it.

## Phase 6 — Packaging, upgrade, and release
- Create idempotent install/upgrade/uninstall procedure.
- Add version compatibility checks and backup/recovery instructions for the extension itself.
- Run static analysis, unit/mocks, security tests, and test-node integration suite.
- Document permissions, logs, retention, limitations, and troubleshooting.
**Acceptance:** release checklist signed off by maintainer.

## Later, not v1
- Cluster-aware scheduling and rolling update coordination.
- Optional notifications.
- Additional dashboards or reporting.
- Automatic backup creation only if explicitly redesigned and approved.
