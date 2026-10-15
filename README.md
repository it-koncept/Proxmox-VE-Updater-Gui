# Proxmox VE Updater — GUI v2 development pack

This pack defines the next development stage for `it-koncept/Proxmox-VE-Updater-`: an integration shown in the Proxmox VE web interface, backed by the existing Bash updater.

## Important
- This is a **development specification**, not an installable plugin or production code.
- The agent must inspect the current repository and supported Proxmox VE versions before choosing an integration mechanism.
- Do not patch Proxmox's shipped GUI files as the default approach. Treat any unsupported GUI extension mechanism as a compatibility risk and document it.
- Never run real updates, reboot hosts, or modify a production Proxmox node during analysis or mock testing.
- Keep the existing CLI and scheduled execution usable.
- Use mock-based tests; no network access or package installation is required for unit tests.

## Contents
- `SPEC.md` — functional and security requirements.
- `ARCHITECTURE.md` — target architecture and integration decision gate.
- `IMPLEMENTATION_PLAN.md` — staged build plan and acceptance criteria.
- `TESTING.md` — mock-based test strategy.
- `AGENTS.md` — persistent instructions for Hermes Agent.
- `HERMES_QWEN_PROMPT.md` — copy/paste initial task for Hermes Agent using the configured Qwen model.
- `ollama-check.sh` — harmless check of local Ollama/model inventory.

## Suggested workflow
1. Extract this pack next to or into a documentation folder in the repository.
2. Review `SPEC.md`.
3. Open Hermes Agent in the repository with the intended Qwen model selected.
4. Paste the full contents of `HERMES_QWEN_PROMPT.md`.
5. Let the agent complete **Phase 0 only** and review its report before approving implementation.
6. Use a disposable Debian development VM and a separate Proxmox test node for integration tests.

## Model name
The model label “Qwen 3.8 Flash” may be a UI/display label rather than an Ollama model identifier. If using Ollama, run `ollama list` and configure Hermes with the exact model/provider identifier available in your setup. Do not change model configuration automatically.

## First release scope
- Scan for updates on the Proxmox host, supported LXC containers, and supported QEMU/KVM guests.
- Preview changes and require explicit authorization before modifying systems.
- Check backup status only; do not create backups automatically.
- Keep a protected history of scans and update jobs.
- CLI and scheduled mode remain supported.
- Cluster-wide orchestration is explicitly deferred.
