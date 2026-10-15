#!/usr/bin/env python3
"""pve-updater backend: a narrow, allowlisted HTTP API in front of the Bash
engine. Browser requests are untrusted; PVE is the identity store; every
authorization decision is made server-side; no shell interpolation anywhere.

Endpoints (closed set):
  GET  /api/health                 liveness (no data, no auth)
  GET  /api/scan                   engine --scan report (read verb)
  GET  /api/backup-status          engine --backup-status JSON (read verb)
  GET  /api/jobs[?limit&target_id] history (read verb)
  GET  /api/jobs/<id>              job detail + log tail (read verb)
  POST /api/jobs                   create job (update verb, CSRF)
  POST /api/jobs/<id>/approve      approve job (update verb, CSRF)
  POST /api/jobs/<id>/start        run job via root runner (update verb, CSRF)

Auth: PVEAuthCookie ticket verified against the PVE API (SEC-001/002).
CSRF: CSRFPreventionToken header must equal PVE's token for the ticket
(SEC-007). API tokens are deliberately NOT accepted from the browser.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pveauth import PveAuth, path_privileges  # noqa: E402
import jobs as jobs_mod  # noqa: E402
from runner import load_config  # noqa: E402

GUEST_ID_RE = re.compile(r"^[0-9]{1,8}$")
NODE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
JOBID_RE = re.compile(r"^[0-9a-f-]{36}$")


class Backend:
    def __init__(self, cfg):
        self.cfg = cfg
        self.auth = PveAuth(cfg["pve_api_url"])
        self.store = jobs_mod.Store(cfg["store_dir"])
        self.submit_lock = threading.Lock()

    # ---- engine helpers (fixed argv, no shell) -----------------------------
    def engine(self, args, timeout):
        return subprocess.run([self.cfg["engine"]] + args,
                              capture_output=True, text=True, timeout=timeout)

    def scan_report(self, extra_args):
        import tempfile
        p = None
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            rp = tf.name
        try:
            p = self.engine(["--scan"] + extra_args + ["--report", rp],
                            self.cfg.get("scan_timeout", 600))
            if p.returncode not in (0, 1):
                return None, p
            with open(rp) as f:
                return json.load(f), p
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            return None, p
        finally:
            try:
                os.remove(rp)
            except OSError:
                pass

    @staticmethod
    def scan_packages(report, target_type, target_id):
        pkgs = []
        for g in (report or {}).get("guests", []):
            if target_type == "host" or str(g.get("vmid")) == str(target_id):
                pkgs.extend(g.get("packages", []))
        return pkgs


def make_handler(be):
    cfg = be.cfg

    class Handler(BaseHTTPRequestHandler):
        server_version = "pve-updater/0.1"

        # ---- plumbing ------------------------------------------------------
        def log_message(self, format, *args):  # keep logs quiet/safe
            sys.stderr.write("[http] %s\n" % (format % args))

        def _send(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _cookie(self, name):
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                k, _, v = part.strip().partition("=")
                if k == name:
                    return v
            return None

        def _identify(self):
            ident = be.auth.verify(self._cookie("PVEAuthCookie"))
            if not ident:
                self._send(401, {"error": "authentication required"})
                return None
            return ident

        def _csrf(self, ident):
            token = self.headers.get("CSRFPreventionToken", "")
            if not be.auth.csrf_ok(self._cookie("PVEAuthCookie"), token):
                self._send(403, {"error": "CSRF token missing or invalid"})
                return False
            return True

        def _body(self):
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return None
            if n <= 0 or n > 64 * 1024:
                return None
            try:
                doc = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:
                return None
            return doc if isinstance(doc, dict) else None

        def _need_priv(self, ident, path, privs, deny_msg):
            held = path_privileges(ident["permissions"], path)
            if not held.intersection(privs):
                self._send(403, {"error": deny_msg})
                return False
            return True

        def _has_any_read(self, ident):
            # history endpoints expose only job metadata: any read privilege
            # anywhere in the permission map is sufficient.
            allp = set()
            for mapping in ident["permissions"].values():
                if isinstance(mapping, dict):
                    allp.update(p for p, v in mapping.items() if v)
            if not allp.intersection(cfg["read_privs"]):
                self._send(403, {"error": "read permission required"})
                return False
            return True

        def _target_path(self, ttype, tid):
            if ttype == "host":
                return "/nodes/" + str(cfg.get("node", ""))
            return "/vms/" + str(tid)

        # ---- routes --------------------------------------------------------
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/api/health":
                return self._send(200, {"status": "ok",
                                        "engine_version": cfg.get("engine_version", "unknown")})
            ident = self._identify()
            if not ident:
                return
            q = parse_qs(u.query)
            if u.path == "/api/scan":
                ttype = q.get("target_type", [""])[0]
                tid = q.get("target_id", [""])[0]
                extra = []
                if ttype == "host":
                    if not self._need_priv(ident, "/nodes/" + str(cfg.get("node", "")),
                                           cfg["read_privs"], "read permission required"):
                        return
                    extra = ["--host"]
                elif ttype in ("lxc", "qemu"):
                    if not GUEST_ID_RE.match(tid):
                        return self._send(400, {"error": "invalid target_id"})
                    if not self._need_priv(ident, "/vms/" + tid,
                                           cfg["read_privs"], "read permission required"):
                        return
                    extra = ["--only", tid]
                else:
                    if not self._need_priv(ident, "/nodes/" + str(cfg.get("node", "")),
                                           cfg["read_privs"], "read permission required"):
                        return
                rep, p = be.scan_report(extra)
                if rep is None:
                    return self._send(502, {"error": "scan failed",
                                            "rc": getattr(p, "returncode", None)})
                return self._send(200, rep)
            if u.path == "/api/backup-status":
                if not self._need_priv(ident, "/nodes/" + str(cfg.get("node", "")),
                                       cfg["read_privs"], "read permission required"):
                    return
                try:
                    p = be.engine(["--backup-status"], 120)
                except subprocess.TimeoutExpired:
                    return self._send(504, {"error": "backup-status timed out"})
                if p.returncode != 0:
                    return self._send(502, {"error": "backup-status failed"})
                try:
                    return self._send(200, json.loads(p.stdout))
                except json.JSONDecodeError:
                    return self._send(502, {"error": "backup-status bad JSON"})
            if u.path == "/api/jobs":
                if not self._has_any_read(ident):
                    return
                limit = q.get("limit", ["50"])[0]
                tid = q.get("target_id", [""])[0]
                if tid and not GUEST_ID_RE.match(tid):
                    return self._send(400, {"error": "invalid target_id"})
                return self._send(200, be.store.list_jobs(limit, tid or None))
            m = re.match(r"^/api/jobs/([0-9a-f-]{36})$", u.path)
            if m:
                if not self._has_any_read(ident):
                    return
                job = be.store.get_job(m.group(1))
                if not job:
                    return self._send(404, {"error": "no such job"})
                log_tail = None
                lp = os.path.join(cfg["store_dir"], "logs", m.group(1) + ".log")
                if os.path.exists(lp):
                    with open(lp, errors="replace") as f:
                        f.seek(max(0, os.path.getsize(lp) - 8192))
                        log_tail = f.read()
                return self._send(200, {"job": job, "log_tail": log_tail})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            ident = self._identify()
            if not ident:
                return
            if not self._csrf(ident):
                return
            known = ("/api/jobs",)
            if not (u.path in known or
                    re.match(r"^/api/jobs/[0-9a-f-]{36}/(approve|start)$", u.path)):
                return self._send(404, {"error": "not found"})
            body = self._body() if self.headers.get("Content-Length") else {}
            if body is None:
                body = {}

            if u.path == "/api/jobs":
                ttype = body.get("target_type")
                tid = str(body.get("target_id", ""))
                op = body.get("operation")
                if ttype not in ("lxc", "qemu", "host"):
                    return self._send(400, {"error": "invalid target_type"})
                if ttype == "host":
                    if not NODE_RE.match(tid) or tid != str(cfg.get("node", "")):
                        return self._send(400, {"error": "invalid target_id"})
                    if op != "host-update":
                        return self._send(400, {"error": "invalid operation"})
                    if not self._need_priv(ident, "/nodes/" + tid,
                                           cfg["host_update_privs"],
                                           "host update permission required"):
                        return
                else:
                    if not GUEST_ID_RE.match(tid):
                        return self._send(400, {"error": "invalid target_id"})
                    if op != "update":
                        return self._send(400, {"error": "invalid operation"})
                    if not self._need_priv(ident, "/vms/" + tid,
                                           cfg["update_privs"],
                                           "update permission required"):
                        return
                # fresh scan -> immutable preview fingerprint
                extra = ["--host"] if ttype == "host" else ["--only", tid]
                rep, p = be.scan_report(extra)
                if rep is None:
                    return self._send(502, {"error": "pre-scan failed"})
                pkgs = be.scan_packages(rep, ttype, tid)
                fp = jobs_mod.fingerprint(ttype, tid, op, pkgs)
                with be.submit_lock:
                    job = be.store.create_job(
                        actor=ident["userid"], target_type=ttype, target_id=tid,
                        target_name=str(body.get("target_name", ""))[:64],
                        operation=op, fp=fp)
                if job is None:
                    return self._send(409, {"error": "active job already exists "
                                                     "for this target"})
                return self._send(201, {"job": job, "packages": pkgs})

            m = re.match(r"^/api/jobs/([0-9a-f-]{36})/(approve|start)$", u.path)
            if not m:
                return self._send(404, {"error": "not found"})
            job_id, action = m.group(1), m.group(2)
            job = be.store.get_job(job_id)
            if not job:
                return self._send(404, {"error": "no such job"})
            # authorization follows the job's own target (never trust the client)
            if job["target_type"] == "host":
                if not self._need_priv(ident, "/nodes/" + job["target_id"],
                                       cfg["host_update_privs"],
                                       "host update permission required"):
                    return
            else:
                if not self._need_priv(ident, "/vms/" + job["target_id"],
                                       cfg["update_privs"],
                                       "update permission required"):
                    return
            if action == "approve":
                if body.get("confirm_fingerprint") != job["preview_fingerprint"]:
                    return self._send(400, {"error": "approval must echo the "
                                                     "exact preview fingerprint"})
                if not be.store.approve(job_id, ident["userid"],
                                        cfg["approval_ttl_s"]):
                    return self._send(409, {"error": "cannot approve (state?)"})
                return self._send(200, {"job": be.store.get_job(job_id)})

            # action == start
            job = be.store.get_job(job_id)
            if job["status"] == "approved" and not be.store.approval_valid(
                    job, cfg["approval_ttl_s"]):
                be.store.transition(job_id, "blocked", actor=ident["userid"],
                                    error_summary="approval expired")
                return self._send(409, {"error": "approval expired; job blocked"})
            if job["status"] != "approved":
                return self._send(409, {"error": f"job is {job['status']}"})
            be.store.write_queue_file(job, cfg["approval_ttl_s"])
            if not be.store.transition(job_id, "running", actor=ident["userid"]):
                be.store.clear_queue(job_id)
                return self._send(409, {"error": "cannot start (state?)"})
            try:
                subprocess.run(cfg["runner_cmd"], timeout=cfg.get(
                    "runner_timeout", 7800))
            except subprocess.TimeoutExpired:
                be.store.transition(job_id, "interrupted",
                                    error_summary="runner timed out")
            result = be.store.read_result(job_id)
            be.store.clear_queue(job_id)
            if result is None:
                be.store.transition(job_id, "interrupted",
                                    error_summary="no runner result")
            else:
                ok = be.store.transition(
                    job_id, result.get("status", "failed"),
                    actor=ident["userid"], exit_code=result.get("exit_code"),
                    error_summary=result.get("error"),
                    log_ref=os.path.join("logs", job_id + ".log"))
                if not ok:  # runner said something illegal; never invent success
                    be.store.transition(job_id, "interrupted",
                                        error_summary="illegal runner status: "
                                        + str(result.get("status"))[:64])
            return self._send(200, {"job": be.store.get_job(job_id)})

        # reject every other method explicitly
        def do_PUT(self):
            self._send(405, {"error": "method not allowed"})

        do_DELETE = do_PATCH = do_PUT
    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    cfg = load_config(args.config)
    be = Backend(cfg)
    httpd = ThreadingHTTPServer(
        (args.host or cfg.get("bind", "127.0.0.1"),
         args.port or cfg.get("port", 8765)),
        make_handler(be))
    print(f"pve-updater backend on {httpd.server_address} "
          f"(engine={cfg['engine']})", file=sys.stderr)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
