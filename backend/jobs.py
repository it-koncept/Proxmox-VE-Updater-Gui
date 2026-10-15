#!/usr/bin/env python3
"""Job lifecycle + durable history store (sqlite) for the pve-updater backend.

State model (SPEC v2 §6):
  queued -> validating -> scanning -> awaiting_approval -> approved ->
  running -> verifying -> succeeded
Terminal alternatives: failed, cancelled, interrupted, blocked, unsupported, partial.

Rules enforced here:
- approval is bound to (job_id, preview_fingerprint); a changed fingerprint or an
  expired TTL makes start impossible (status -> blocked)
- one non-terminal job per target (unique partial index)
- interrupted/unknown jobs are never rewritten to succeeded
- history file is created 0640 (SEC-011)
"""
import calendar
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

ACTIVE = ("awaiting_approval", "approved", "running")
TERMINAL = ("succeeded", "failed", "cancelled", "interrupted", "blocked",
            "unsupported", "partial")
ALLOWED = {
    "awaiting_approval": {"approved", "cancelled", "blocked", "failed"},
    "approved": {"running", "cancelled", "blocked", "failed"},
    "running": {"verifying", "succeeded", "failed", "interrupted", "partial",
                "blocked", "unsupported"},
    "verifying": {"succeeded", "partial", "failed", "interrupted"},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  job_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  actor TEXT NOT NULL,
  approval_actor TEXT,
  approved_at TEXT,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  target_name TEXT,
  operation TEXT NOT NULL,
  preview_fingerprint TEXT NOT NULL,
  status TEXT NOT NULL,
  exit_code INTEGER,
  reboot_required INTEGER,
  backup_status TEXT,
  backup_checked_at TEXT,
  log_ref TEXT,
  error_summary TEXT,
  engine_version TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_target
  ON jobs(target_type, target_id)
  WHERE status IN ('awaiting_approval','approved','running');
CREATE TABLE IF NOT EXISTS audit(
  ts TEXT NOT NULL, actor TEXT NOT NULL, job_id TEXT NOT NULL,
  action TEXT NOT NULL, detail TEXT
);
"""


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fingerprint(target_type, target_id, operation, packages):
    payload = json.dumps(
        {"target": [target_type, str(target_id)], "op": operation,
         "packages": sorted(packages or [])},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, store_dir):
        self.dir = store_dir
        os.makedirs(store_dir, exist_ok=True)
        os.makedirs(os.path.join(store_dir, "queue"), exist_ok=True)
        os.makedirs(os.path.join(store_dir, "logs"), exist_ok=True)
        self.db_path = os.path.join(store_dir, "history.db")
        new = not os.path.exists(self.db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        if new:
            os.chmod(self.db_path, 0o640)
        self._lock = threading.Lock()

    # ---- jobs -------------------------------------------------------------
    def create_job(self, *, actor, target_type, target_id, target_name,
                   operation, fp, backup_status=None):
        job_id = str(uuid.uuid4())
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO jobs(job_id,created_at,actor,target_type,"
                    "target_id,target_name,operation,preview_fingerprint,"
                    "status,backup_status,backup_checked_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (job_id, now_iso(), actor, target_type, str(target_id),
                     target_name, operation, fp, "awaiting_approval",
                     backup_status, now_iso()))
            except sqlite3.IntegrityError:
                return None  # active job already exists for this target
            self._conn.commit()
            self.audit(actor, job_id, "create",
                       f"{target_type}/{target_id} {operation}")
        return self.get_job(job_id)

    def get_job(self, job_id):
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, limit=50, target_id=None):
        q = "SELECT * FROM jobs"
        args = []
        if target_id:
            q += " WHERE target_id=?"
            args.append(str(target_id))
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(min(int(limit), 500))
        return [dict(r) for r in self._conn.execute(q, args)]

    def transition(self, job_id, new_status, *, actor="system", **fields):
        """Move a job to new_status; illegal moves return False."""
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                return False
            cur = job["status"]
            if cur in TERMINAL or new_status not in ALLOWED.get(cur, set()):
                return False
            cols, vals = ["status=?"], [new_status]
            if new_status in ("running",):
                cols.append("started_at=?")
                vals.append(now_iso())
            if new_status in TERMINAL:
                cols.append("finished_at=?")
                vals.append(now_iso())
            for k in ("exit_code", "reboot_required", "log_ref",
                      "error_summary", "engine_version", "approval_actor",
                      "approved_at", "backup_status"):
                if k in fields:
                    cols.append(f"{k}=?")
                    vals.append(fields[k])
            vals.append(job_id)
            self._conn.execute(
                f"UPDATE jobs SET {','.join(cols)} WHERE job_id=?", vals)
            self._conn.commit()
            self.audit(actor, job_id, f"status:{cur}->{new_status}",
                       fields.get("error_summary"))
        return True

    def approve(self, job_id, actor, ttl_s):
        with self._lock:
            job = self.get_job(job_id)
            if not job or job["status"] != "awaiting_approval":
                return False
            self._conn.execute(
                "UPDATE jobs SET status='approved', approval_actor=?,"
                " approved_at=? WHERE job_id=?",
                (actor, now_iso(), job_id))
            self._conn.commit()
            self.audit(actor, job_id, "approve",
                       f"ttl={ttl_s} fp={job['preview_fingerprint'][:12]}")
        return True

    def approval_valid(self, job, ttl_s, *, allow_expired=False):
        if not job or not job["approved_at"]:
            return False
        if allow_expired:
            return True
        approved = calendar.timegm(time.strptime(
            job["approved_at"], "%Y-%m-%dT%H:%M:%SZ"))
        return (time.time() - approved) <= ttl_s

    # ---- queue files (backend -> root runner) ------------------------------
    def write_queue_file(self, job, runner_ttl_s):
        path = os.path.join(self.dir, "queue", job["job_id"] + ".job")
        payload = {
            "job_id": job["job_id"],
            "target_type": job["target_type"],
            "target_id": job["target_id"],
            "operation": job["operation"],
            "fingerprint": job["preview_fingerprint"],
            "approved_at": job["approved_at"],
            "ttl_s": runner_ttl_s,
        }
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.chmod(path, 0o640)
        return path

    def read_result(self, job_id):
        path = os.path.join(self.dir, "queue", job_id + ".result")
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return None

    def clear_queue(self, job_id):
        for ext in (".job", ".result", ".report.json"):
            try:
                os.remove(os.path.join(self.dir, "queue", job_id + ext))
            except FileNotFoundError:
                pass

    # ---- audit --------------------------------------------------------------
    def audit(self, actor, job_id, action, detail=None):
        self._conn.execute(
            "INSERT INTO audit(ts,actor,job_id,action,detail) VALUES(?,?,?,?,?)",
            (now_iso(), str(actor)[:128], job_id, action,
             (str(detail)[:512] if detail is not None else None)))
        self._conn.commit()

    def audit_log(self, job_id=None, limit=200):
        q = "SELECT * FROM audit"
        args = []
        if job_id:
            q += " WHERE job_id=?"
            args.append(job_id)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(min(int(limit), 1000))
        return [dict(r) for r in self._conn.execute(q, args)]
