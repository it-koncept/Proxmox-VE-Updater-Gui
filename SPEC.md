# Requirements Specification — Proxmox VE Updater GUI v2

## 1. Purpose
Extend the existing Bash-based Proxmox VE Updater with a safe, maintainable way to operate it from the Proxmox VE web interface. The existing CLI and scheduled operation must remain usable.

## 2. Scope
### Included
- Host package update scanning and controlled updates.
- LXC update scanning and controlled updates where supported.
- QEMU/KVM guest update support only where a documented, configured in-guest mechanism exists.
- Preview/dry-run, explicit approval, backup-status checks, job status, logs, and history.
- Manual and scheduled operation.
- Optional notifications may be considered after core requirements.
- Debian development environment, Bash engine, mock-based tests, Hermes Agent + local Qwen workflow.

### Excluded from first release
- Cluster-wide orchestration or coordinated multi-node updates.
- Automatic backup creation.
- Automatic rollback of operating-system/package updates.
- Updating arbitrary guests without a supported guest-side mechanism.
- Replacing Proxmox's native package-management interface.
- Any production dependency on a GUI patch that is silently overwritten by Proxmox upgrades.

## 3. Integration decision gate
Before coding, inspect the repository and the target Proxmox VE versions. Compare:
A. Supported/public Proxmox API-based integration where it can meet the requirements.
B. A separately served companion UI linked from Proxmox, if native embedding is not supported.
C. A version-pinned GUI extension only if the agent proves the mechanism, labels it unsupported if applicable, documents upgrade risks, and obtains explicit user approval.

Do not assume that a generic third-party menu plugin is officially supported. Do not modify shipped Proxmox GUI files in Phase 0. The agent must recommend one option with evidence, tradeoffs, compatibility, installation, upgrade, and uninstall plans.

## 4. Functional requirements
- FR-001: Preserve the current CLI behaviour unless a change is approved and documented.
- FR-002: Provide a read-only scan of update availability without installing packages.
- FR-003: Display target type, target ID/name, scan time, result, and errors.
- FR-004: Support the Proxmox host.
- FR-005: Support eligible LXC containers after validating state, distribution/tooling, and safety constraints.
- FR-006: Support a QEMU/KVM guest only when a supported guest update mechanism is configured and verified. Clearly report unsupported/offline guests.
- FR-007: Show a preview of intended actions and available package-change information before execution.
- FR-008: Require explicit user approval before any modifying action; approval must be validated server-side and bound to the intended target/action.
- FR-009: Check backup status without creating backups. Display source, timestamp, success/failure/unknown state, and freshness where available.
- FR-010: If backup status is missing, failed, unknown, or stale, block by default or require a separate, audited administrator override according to a documented policy. Never silently treat unknown as healthy.
- FR-011: Display job progress/status, start/end time, exit status, and a useful error summary.
- FR-012: Keep a durable history of scans and update jobs, with retention and permissions documented.
- FR-013: Record the authenticated actor, target, requested action, approval, timestamps, and final outcome for modifying jobs when identity is available.
- FR-014: Prevent concurrent update jobs against the same target and protect against duplicate submission.
- FR-015: Provide a CLI command or documented invocation for the same core actions where practical.
- FR-016: Support manual runs and scheduled runs without relying on a logged-in browser.
- FR-017: Surface reboot-required status when reliably detectable; do not reboot automatically in v1.
- FR-018: Provide installation, upgrade, and uninstall documentation with safe failure behaviour.
- FR-019: Show clear states for unsupported, unreachable, skipped, failed, completed, and partially completed targets.
- FR-020: Do not report success unless the backend has verified the command result.

## 5. Security requirements
- SEC-001: Use Proxmox's established authentication/authorization model where supported by the chosen integration. Do not invent a second password store.
- SEC-002: Enforce authorization on the backend for every action; hiding a button is not authorization.
- SEC-003: Separate read/scan permissions from update permissions.
- SEC-004: Use least privilege. Do not make a general web process root just to simplify execution.
- SEC-005: Expose a narrow allowlist of actions; never accept arbitrary shell commands or command fragments from the browser.
- SEC-006: Use safe argument handling and avoid `eval`; validate VMID/CTID and target IDs against authoritative Proxmox state.
- SEC-007: Protect against CSRF, unauthorized requests, path traversal, command injection, and unsafe log rendering, according to the selected transport/UI design.
- SEC-008: Do not expose secrets, tokens, environment dumps, or sensitive guest data in UI/logs.
- SEC-009: Use locks and job deduplication for update operations.
- SEC-010: Apply explicit timeouts and safe signal handling; record interrupted jobs as interrupted/unknown, not successful.
- SEC-011: Persist logs/history with restrictive ownership and permissions.
- SEC-012: Validate all inputs server-side, including target, operation, package selection if supported, and approval state.
- SEC-013: Do not run destructive integration tests against a production node.
- SEC-014: Never execute real update commands in mock/unit tests. Use test doubles and temporary directories.
- SEC-015: Do not silently alter Proxmox-owned files or configuration. Any required unsupported modification needs explicit approval and a documented uninstall/recovery path.
- SEC-016: Do not store long-lived privileged API credentials in browser code or world-readable files.
- SEC-017: Scheduled runs must use a defined service account/permission model and must write audit logs.

## 6. Job lifecycle
Recommended state model:
`queued -> validating -> scanning -> awaiting_approval -> approved -> running -> verifying -> succeeded`
Terminal alternatives: `failed`, `cancelled`, `interrupted`, `blocked`, `unsupported`, `partial`.
A scan can finish without approval; approval applies only to a specific immutable target/action preview. If the target or preview changes, require fresh approval. Do not treat browser confirmation alone as a durable approval token.

## 7. UI requirements
- Node-scoped entry point or clearly documented companion UI integration.
- Summary for host, eligible LXC containers, and eligible QEMU/KVM guests.
- Scan action that makes no changes.
- Per-target detail and package/action preview.
- Explicit approval dialog with target and action summary.
- Backup status and policy outcome.
- Job state, logs, failure reason, and reboot-required indication.
- Search/filter for history and per-target history.
- Clear accessibility labels and error states.
- UI must not imply that a VM was updated if only its virtual hardware or Proxmox configuration was inspected.

## 8. Testing and acceptance
- Unit tests use mocks for `apt`, `apt-get`, `dpkg`, `pct`, `qm`, backup status sources, systemd/service commands, and reboot detection as applicable.
- Test unauthorized requests, malformed IDs, command injection strings, stale approvals, duplicate requests, concurrent jobs, timeouts, interrupted jobs, log permissions, and missing/stale backup state.
- Test that a scan does not modify package state.
- Test CLI and scheduled invocation still work.
- Integration tests run only in disposable Debian/Proxmox test environments.
- Every feature has a reproducible test and documentation.

## 9. Definition of done
A release is done only when:
1. The integration method is documented and its support status is explicit.
2. CLI and scheduled use still work.
3. All required mock tests pass.
4. Authorization is enforced server-side.
5. No arbitrary shell execution path is exposed.
6. Backup unknown/stale states are handled safely.
7. Installation, upgrade, uninstall, logs, and recovery are documented.
8. The agent reports exact changed files, commands/tests run, failures, and remaining risks.
