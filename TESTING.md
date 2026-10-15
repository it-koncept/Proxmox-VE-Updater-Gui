# Testing Strategy

## General rules
- Run unit tests as a non-root user in a disposable Debian development environment.
- Never execute real `apt`, `apt-get`, `pct exec`, `qm guest exec`, reboot, or service-changing commands during unit tests.
- Put mocks earlier in `PATH`; make each mock record its arguments to a temporary test directory.
- Use fixtures for `pvesh`, `pveversion`, `pct`, `qm`, package manager output, backup status, and job state.
- Integration tests require a disposable Proxmox test node or explicit test environment.

## Required test groups
1. **Scan is read-only:** verify no install/upgrade/remove/reboot command is invoked.
2. **Host:** no updates, available updates, package-manager failure, reboot-required signal.
3. **LXC:** running/stopped, unsupported distro, missing command, unreachable container, update failure.
4. **QEMU/KVM:** agent absent, guest offline, unsupported mechanism, guest command failure, verified success.
5. **Backup policy:** fresh success, failed, missing, unknown, stale, and authorized override.
6. **Approval:** missing approval, wrong actor, expired/stale preview, changed target, changed preview fingerprint.
7. **Authorization:** read-only user cannot start update; unauthorized and unauthenticated calls rejected.
8. **Injection:** hostile IDs, shell metacharacters, unexpected operation, path traversal, oversized input.
9. **Concurrency:** duplicate submission and simultaneous jobs on same target are rejected or safely serialized.
10. **Failure lifecycle:** timeout, signal, backend restart, partial success, interrupted process.
11. **History/logging:** restrictive permissions, atomic writes, redaction, retention, audit attribution.
12. **Regression:** CLI and scheduled run remain functional.
13. **Packaging:** install/upgrade/uninstall idempotence and compatibility check.

## Example test acceptance
For every failure case, assert:
- non-zero/appropriate result;
- job is not marked successful;
- diagnostic is recorded without secrets;
- lock is released safely;
- no unintended real command is invoked.

## Suggested commands
The agent must discover the repository's actual test runner first. Do not assume Bats or another dependency is installed. Prefer existing tools or portable shell tests. Document any new dependency and why it is needed.
