# Initial prompt for Hermes Agent (Qwen)

You are working in the existing repository:
https://github.com/it-koncept/Proxmox-VE-Updater-

The maintainer wants a next version with a GUI integration visible from Proxmox VE, while preserving Bash as the update engine. Development environment: Debian. Agent: Hermes Agent. Model is described by the maintainer as “Qwen 3.8 Flash”; verify the exact configured model identifier if relevant, but do not alter runtime configuration.

Read `AGENTS.md`, `SPEC.md`, `ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, and `TESTING.md` first.

## Your task now: Phase 0 ONLY
Do not implement features and do not modify production/project code. Inspect the current repository and create a proposal/report only (prefer `docs/PHASE0-AUDIT.md`; if you need to create this report, that is the only new project file permitted in this phase).

The report must:
1. Map the current code and existing behavior.
2. Map each requirement to existing code, missing code, or an unresolved question.
3. Identify supported Proxmox VE and Debian versions from evidence, distinguishing verified facts from assumptions.
4. Research the current official Proxmox API, auth/ACL, and GUI customization options relevant to those versions. Use authoritative documentation or source. Do not claim a custom menu plugin is officially supported without proof.
5. Compare: API-based integration, companion UI linked from Proxmox, and version-specific GUI extension. Recommend one and explain compatibility/upgrade risks.
6. Propose a threat model and how to avoid arbitrary command execution, privilege escalation, CSRF, stale approval, concurrent jobs, and unsafe logs.
7. Propose architecture and exact files/modules likely to change, without changing them.
8. Define mock tests for host, LXC, guest-agent-based QEMU/KVM guest support,backup state, authorization, approval, concurrency, timeout, history, CLI regression, and packaging.
9. Describe install/upgrade/uninstall and recovery approach.
10. List open questions that require maintainer input.

Rules:
- Do not run any real update, package install, reboot, destructive command, or production action.
- Du not use `sudo` or root privileges for analysis.
- Do not patch Proxmox-installed GUI files.
- Do not install packages or download models.
- Do not run commands from repository scripts until you have inspected them and explained any side effects.
- Do not invent source evidence or claim tests passed unless you ran them.
- If internet/research is unavailable, clearly mark the integration decision as provisional and list the sources to verify later.
- Stop after presenting the Phase 0 report and ask the maintainer to approve the next phase. Do not begin Phase 1 until explicit approval.
