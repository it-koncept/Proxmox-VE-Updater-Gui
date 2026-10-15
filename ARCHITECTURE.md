# Target Architecture

## Design principle
Keep the existing Bash updater as the domain/operations engine. Treat the GUI as a client and never make browser code responsible for privileged operations or policy decisions.

## Logical components
1. **Proxmox-facing UI** — scans, previews, approval, job status, backup status, history.
2. **Authenticated backend/adapter** — validates identity, permissions, request schema, target existence, approval binding, job locks, and audit events.
3. **Job runner** — executes only allowlisted actions with explicit timeouts and controlled environment.
4. **Bash engine** — reusable commands for scan, backup-status check, update, verification, and report.
5. **State/history store** — restrictive local storage for job metadata and logs, with schema/version and retention policy.
6. **Target adapters** — host, LXC, and supported QEMU/KVM guest mechanisms.

## Integration options to evaluate
The agent must verify current Proxmox documentation/source for the exact target versions and inspect the existing repository before choosing:
- Proxmox API and an integration approach that does not patch shipped UI assets.
- A companion web UI with a documented link/entry point if native embedding is not supported.
- A version-specific GUI extension only with evidence, explicit user approval, compatibility tests, and upgrade/uninstall plan.

Do not claim that a custom menu item is an officially supported plugin unless authoritative documentation proves it for the target version. Do not patch shipped Proxmox files during analysis.

## Trust boundaries
- Browser requests are untrusted.
- All authorization and validation occur server-side.
- UI cannot send arbitrary commands, paths, environment variables, or shell snippets.
- Approval refers to a specific target, operation, and preview fingerprint.
- Update jobs are serialized per target.
- Unknown backup status is not success.
- Interrupted jobs never become successful by default.

## Suggested module boundaries (adapt only after repo inspection)
- `cli/` existing CLI, preserved.
- `lib/` reusable Bash engine and validation.
- `integration/` Proxmox/API adapter and packaging assets.
- `backend/` only if a backend is needed; choose a minimal, maintainable implementation compatible with the project's constraints.
- `tests/` mocks, fixtures, and security regression tests.
- `docs/` install, permissions, compatibility, operations, recovery.

These are conceptual boundaries, not a mandate to restructure the repository.

## Data model (minimum)
- `job_id`, `created_at`, `started_at`, `finished_at`
- `actor` or `scheduled_identity`
- `target_type`, `target_id`, `target_name`
- `operation`, `preview_fingerprint`, `approval_actor`, `approved_at`
- `status`, `exit_code`, `reboot_required`
- `backup_status`, `backup_checked_at`
- `log_reference`, `error_summary`, `engine_version`

Avoid storing full sensitive command environments or secrets.

## Operational concerns
- Explicit timeouts, signal handling, and lock cleanup.
- Atomic history writes and restrictive file permissions.
- Log rotation/retention.
- Idempotent installation and uninstall.
- Detect version incompatibility before enabling GUI actions.
- Clear distinction between scan results and actual update results.
