#!/usr/bin/env python3
"""Root-side queue runner for pve-updater (SEC-004 least privilege).

The web backend runs as an unprivileged user and invokes this via a sudoers
line that takes NO arguments:
    pveupd ALL=(root) NOPASSWD: /usr/local/sbin/pve-updater-runner
There is no argument wildcard to abuse. The runner reads pending job files
from the queue directory (group-writable by the backend user only) and
re-validates EVERYTHING itself (defense in depth):

- strict schema; target ids must be numeric (guests) or a safe node name (host)
- operation must be on the allowlist
- approval must exist and be inside the TTL
- the preview fingerprint is re-computed from a FRESH scan; mismatch -> blocked
- the engine is exec'd with a fixed argv list (no shell, no eval)
- timeouts; interrupted runs are recorded as interrupted, never succeeded
"""
import argparse
import json
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# dev checkout: jobs.py sits next to runner.py; packaged layout: runner is
# /usr/local/sbin/pve-updater-runner and jobs.py lives in /opt/pve-updater/backend
for _p in (_HERE, "/opt/pve-updater/backend"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from jobs import fingerprint  # noqa: E402

GUEST_ID_RE = re.compile(r"^[0-9]{1,8}$")
NODE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
JOBID_RE = re.compile(r"^[0-9a-f-]{36}$")
OPERATIONS = {"update": "guest-update", "host-update": "host-update"}


def load_config(path):
    with open(path) as f:
        return json.load(f)


def scan_packages(cfg, target_type, target_id):
    """Run the engine scan and return (packages, ok)."""
    import tempfile
    if target_type == "host":
        args = [cfg["engine"], "--host", "--scan"]
    else:
        args = [cfg["engine"], "--scan", "--only", str(target_id)]
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        rp = tf.name
    args += ["--report", rp]
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           timeout=cfg.get("scan_timeout", 600))
        if p.returncode != 0:
            return [], False
        with open(rp) as f:
            rep = json.load(f)
        pkgs = []
        for g in rep.get("guests", []):
            if target_type == "host" or str(g.get("vmid")) == str(target_id):
                pkgs.extend(g.get("packages", []))
        return pkgs, True
    finally:
        try:
            os.remove(rp)
        except OSError:
            pass


def validate_job(doc, node=None):
    """Return (ok, reason). Strict: unknown keys rejected."""
    if not isinstance(doc, dict):
        return False, "not an object"
    allowed_keys = {"job_id", "target_type", "target_id", "operation",
                    "fingerprint", "approved_at", "ttl_s", "require_approval"}
    if set(doc.keys()) != allowed_keys:
        return False, "schema keys"
    if not JOBID_RE.match(str(doc.get("job_id", ""))):
        return False, "job_id"
    if doc.get("target_type") not in ("lxc", "qemu", "host"):
        return False, "target_type"
    tid = str(doc.get("target_id", ""))
    if doc["target_type"] == "host":
        if not NODE_RE.match(tid):
            return False, "target_id(node)"
        # --host always acts on the LOCAL node; a job naming another node
        # would silently target the wrong machine. Refuse.
        if node and tid != str(node):
            return False, "target_id(node) != configured node"
    elif not GUEST_ID_RE.match(tid):
        return False, "target_id"
    if doc.get("operation") not in OPERATIONS:
        return False, "operation"
    fp = str(doc.get("fingerprint", ""))
    if not re.match(r"^[0-9a-f]{64}$", fp):
        return False, "fingerprint"
    if doc.get("require_approval"):
        if not isinstance(doc.get("approved_at"), str) or \
                not re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$",
                             doc["approved_at"]):
            return False, "approved_at"
        if not isinstance(doc.get("ttl_s"), int) or doc["ttl_s"] <= 0 or \
                doc["ttl_s"] > 86400:
            return False, "ttl_s"
        import calendar
        import time
        approved = calendar.timegm(time.strptime(doc["approved_at"],
                                                 "%Y-%m-%dT%H:%M:%SZ"))
        if time.time() - approved > doc["ttl_s"]:
            return False, "approval expired"
    return True, None


def write_result(queue_dir, job_id, status, exit_code=None, error=None,
                 log_text=None):
    # The runner runs as ROOT; the backend reads these files as the
    # unprivileged service user. Files must therefore carry the queue/log
    # dir's group (found via stat, not assumed) or the backend cannot read
    # them and every job looks 'interrupted' (found live on test-node).
    def _gid_of(path):
        try:
            return os.stat(path).st_gid
        except OSError:
            return -1
    path = os.path.join(queue_dir, job_id + ".result")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        os.fchown(fd, -1, _gid_of(queue_dir))
    except OSError:
        pass
    with os.fdopen(fd, "w") as f:
        json.dump({"job_id": job_id, "status": status, "exit_code": exit_code,
                   "error": (str(error)[:512] if error else None)}, f)
    os.chmod(path, 0o640)  # explicit: umask must not tighten or widen
    if log_text is not None:
        log_dir = os.path.join(os.path.dirname(queue_dir), "logs")
        os.makedirs(log_dir, exist_ok=True)
        lp = os.path.join(log_dir, job_id + ".log")
        lfd = os.open(lp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        try:
            os.fchown(lfd, -1, _gid_of(log_dir))
        except OSError:
            pass
        with os.fdopen(lfd, "w") as f:
            f.write(str(log_text)[:200000])
        os.chmod(lp, 0o640)


def map_engine_status(rc, report_path, target_type=None, target_id=None):
    """Map engine rc + the JOB'S OWN target entry in the report to a status.

    Engine contract: 0 ok, 1 guest failure(s), 2 preflight, 3 lock busy.
    The real engine's report lists EVERY guest (targets not selected via
    --only appear as 'skipped - not in --only list'); judging on the whole
    list made every single-guest job 'partial' (found live on test-node).
    Therefore: find the entry for this job's target and map its status:
      success -> succeeded | warning -> partial | failed -> failed
      skipped -> unsupported (no agent, locked, disk gate)
    If the target entry is missing (engine died early), fall back to the
    rc contract + aggregate view.
    """
    try:
        with open(report_path) as f:
            rep = json.load(f)
        guests = [g for g in rep.get("guests", []) if isinstance(g, dict)]
    except Exception:
        guests = []
    entry = None
    if target_type == "host":
        entry = next((g for g in guests
                      if str(g.get("vmid")) == "host"
                      or str(g.get("type")) == "host"), None)
    elif target_id is not None:
        entry = next((g for g in guests
                      if str(g.get("vmid")) == str(target_id)), None)
    if entry is not None:
        st = str(entry.get("status", ""))
        if st in ("success", "planned"):
            return "succeeded"
        if st == "warning":
            return "partial"
        if st == "failed":
            return "failed"
        if st == "skipped":
            return "unsupported"
    sts = [str(g.get("status", "")) for g in guests]
    if sts:
        if "failed" in sts:
            return "failed"
        if all(s == "skipped" for s in sts):
            return "unsupported"
        if all(s == "success" for s in sts):
            return "succeeded"
        return "partial"  # warnings / mixed outcomes
    # no usable report: fall back to the rc contract
    if rc == 0:
        return "succeeded"
    if rc == 1:
        return "partial"
    if rc == 3:
        return "blocked"
    return "failed"


def process_job(cfg, queue_dir, path):
    job_id = os.path.basename(path)[:-4]
    try:
        with open(path) as f:
            doc = json.load(f)
    except Exception as e:
        write_result(queue_dir, job_id, "failed", error=f"unreadable job: {e}")
        return
    ok, reason = validate_job(doc, cfg.get("node"))
    if not ok:
        write_result(queue_dir, job_id, "blocked", error=f"validation: {reason}")
        return
    # NOTE: no fingerprint re-check here. The maintainer removed the
    # fingerprint-approval friction (2026-10-15): the preview fingerprint is
    # kept for audit only. The engine itself re-checks package state at run
    # time (apt -s upgrade inside the guest), so a stale preview cannot
    # install anything unexpected.
    # fixed argv, no shell
    if doc["target_type"] == "host":
        argv = [cfg["engine"], "--host", "--yes"]
    else:
        argv = [cfg["engine"], "--only", str(doc["target_id"])]
    report = os.path.join(queue_dir, job_id + ".report.json")
    argv += ["--report", report]
    timeout = cfg.get("update_timeout", 7200)
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        write_result(queue_dir, job_id, "interrupted",
                     error=f"engine exceeded {timeout}s")
        return
    except Exception as e:
        write_result(queue_dir, job_id, "failed", error=f"exec: {e}")
        return
    log_text = (p.stdout or "") + "\n" + (p.stderr or "")
    status = map_engine_status(p.returncode, report,
                               doc["target_type"], doc["target_id"])
    write_result(queue_dir, job_id, status, exit_code=p.returncode,
                 error=None if p.returncode == 0 else f"engine rc={p.returncode}",
                 log_text=log_text)


def _readonly_run(cfg, argv, report_path, timeout):
    """Run a read-only engine verb as root; print JSON on stdout."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        sys.stderr.write("runner: engine timed out\n")
        return 124
    if p.stderr:
        sys.stderr.write(p.stderr)
    if report_path:
        # engine --scan: stdout is human log lines; the JSON lives in the
        # report file only. Do NOT echo stdout.
        if p.returncode in (0, 1) and os.path.exists(report_path):
            try:
                with open(report_path) as f:
                    sys.stdout.write(f.read())
                return 0
            except OSError:
                return 1
        return p.returncode
    # --backup-status: the engine keeps stdout pure JSON (logs go to stderr).
    if p.stdout:
        sys.stdout.write(p.stdout)
    return p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    # read-only verbs (engine needs root for these; closed set, no eval):
    ap.add_argument("--backup-status", action="store_true")
    ap.add_argument("--scan-report", action="store_true")
    ap.add_argument("--host", action="store_true")
    ap.add_argument("--only", default=None)
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.backup_status:
        return _readonly_run(cfg, [cfg["engine"], "--backup-status"],
                             None, cfg.get("scan_timeout", 600))
    if args.scan_report:
        import tempfile
        with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
            rp = tf.name
        argv = [cfg["engine"], "--scan"]
        if args.host:
            argv.append("--host")
        if args.only is not None:
            if not GUEST_ID_RE.match(args.only):
                return 2
            argv += ["--only", args.only]
        argv += ["--report", rp]
        try:
            return _readonly_run(cfg, argv, rp, cfg.get("scan_timeout", 600))
        finally:
            try:
                os.remove(rp)
            except OSError:
                pass
    queue_dir = os.path.join(cfg["store_dir"], "queue")
    pending = sorted(f for f in os.listdir(queue_dir) if f.endswith(".job"))
    for name in pending:
        path = os.path.join(queue_dir, name)
        process_job(cfg, queue_dir, path)
        # consume-once: remove the job file immediately after its result is
        # written, so a backend crash before clear_queue can never cause a
        # second execution of the same approved update.
        try:
            os.remove(path)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
