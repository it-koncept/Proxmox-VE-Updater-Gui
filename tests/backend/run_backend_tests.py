#!/usr/bin/env python3
"""Backend security/behavior tests (Phase 2 acceptance).

SAFETY: the engine is a fake stub (tests/backend/fake-engine.sh) and PVE is a
local fake HTTP server. No real apt/pct/qm/PVE calls ever happen. Run as a
normal user.

Run: python3 tests/backend/run_backend_tests.py
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import server as server_mod  # noqa: E402
import jobs as jobs_mod  # noqa: E402

# ---- fake PVE identity fixtures -------------------------------------------
TICKETS = {
    "t-admin": ("admin@pve", {"/": {"Sys.Modify": 1, "VM.Allocate": 1,
                                    "VM.Audit": 1, "Sys.Audit": 1}}),
    "t-auditor": ("auditor@pve", {"/vms": {"VM.Audit": 1},
                                  "/nodes": {"Sys.Audit": 1}}),
    "t-nobody": ("nobody@pve", {}),
}
CSRF = {"t-admin": "csrf-admin", "t-auditor": "csrf-auditor",
        "t-nobody": "csrf-nobody"}


class FakePve(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        # POST /access/ticket — fake PVE credential check (fake passwords,
        # fake tickets; nothing real is ever involved).
        if "/access/ticket" not in self.path:
            self.send_response(404)
            self.end_headers()
            return
        n = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(n).decode())
        except Exception:
            body = {}
        user, pw = body.get("username"), body.get("password")
        if user == "admin@pve" and pw == "pw-admin":
            self._json({"data": {"ticket": "t-admin",
                                 "CSRFPreventionToken": "csrf-admin",
                                 "username": "admin@pve"}})
        elif user == "tfauser@pve" and pw == "pw-tfa":
            tfa = body.get("tfa-challenge")
            if tfa and tfa.get("response") == "123456":
                self._json({"data": {"ticket": "t-admin",
                                     "CSRFPreventionToken": "csrf-admin",
                                     "username": "admin@pve"}})
            else:
                self._json({"data": {"tfa-fingerprint": "fake-fp",
                                     "tfa-challenge": {"tfa-type": "totp"}}})
        else:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            body = b'{"data":null}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        cookie = self.headers.get("Cookie", "")
        ticket = None
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "PVEAuthCookie":
                ticket = v
        if ticket in TICKETS and "/access/permissions" in self.path:
            userid, perms = TICKETS[ticket]
            self._json({"data": {"userid": userid, "permissions": perms}})
            return
        if ticket in CSRF and "/access/csrf_token" in self.path:
            self._json({"data": {"CSRFPreventionToken": CSRF[ticket]}})
            return
        if ticket in TICKETS and "/nodes/testnode/qemu" in self.path:
            self._json({"data": [{"vmid": 207, "name": "vm-test",
                                  "status": "running", "tags": "apt"}]})
            return
        if ticket in TICKETS and "/nodes/testnode/lxc" in self.path:
            self._json({"data": [{"vmid": 107, "name": "ct-test",
                                  "status": "running", "tags": "apt"}]})
            return
        self.send_response(401)
        self.end_headers()


TMP = tempfile.mkdtemp(prefix="pvub-test-")
FAKE_ENGINE = os.path.join(HERE, "fake-engine.sh")
CALLS = os.path.join(TMP, "engine_calls.log")
os.environ["FAKE_ENGINE_CALLS"] = CALLS

pve_httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakePve)
PVE_PORT = pve_httpd.server_address[1]
threading.Thread(target=pve_httpd.serve_forever, daemon=True).start()

CFG = {
    "engine": FAKE_ENGINE,
    "pve_api_url": f"http://127.0.0.1:{PVE_PORT}/api2/json",
    "store_dir": os.path.join(TMP, "store"),
    "node": "testnode",
    "read_privs": ["VM.Audit", "Sys.Audit"],
    "update_privs": ["VM.Allocate"],
    "host_update_privs": ["Sys.Modify"],
    "approval_ttl_s": 900,
    "runner_cmd": [sys.executable, os.path.join(ROOT, "backend", "runner.py")],
    "scan_timeout": 20,
    "update_timeout": 20,
    "runner_timeout": 4,
    "bind": "127.0.0.1",
    "engine_version": "3.5.0-test",
}
CFG_PATH = os.path.join(TMP, "config.json")
with open(CFG_PATH, "w") as f:
    json.dump(CFG, f)
CFG["runner_cmd"] += ["--config", CFG_PATH]
with open(CFG_PATH, "w") as f:
    json.dump(CFG, f)

BE = server_mod.Backend(CFG)
HTTPD = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.make_handler(BE))
PORT = HTTPD.server_address[1]
threading.Thread(target=HTTPD.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"


def req(method, path, ticket=None, csrf=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if ticket:
        r.add_header("Cookie", "PVEAuthCookie=" + ticket)
    if csrf:
        r.add_header("CSRFPreventionToken", csrf)
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def calls():
    try:
        with open(CALLS) as f:
            return f.read()
    except FileNotFoundError:
        return ""


def set_mode(mode):
    if mode == "normal":
        os.environ.pop("FAKE_ENGINE_MODE", None)
    else:
        os.environ["FAKE_ENGINE_MODE"] = mode


def full_job(target_type="lxc", target_id="107", operation="update"):
    st, out = req("POST", "/api/jobs", "t-admin", "csrf-admin",
                  {"target_type": target_type, "target_id": target_id,
                   "operation": operation})
    assert st == 201, out
    job = out["job"]
    st, out = req("POST", f"/api/jobs/{job['job_id']}/approve", "t-admin",
                  "csrf-admin",
                  {"confirm_fingerprint": job["preview_fingerprint"]})
    assert st == 200, out
    st, out = req("POST", f"/api/jobs/{job['job_id']}/start", "t-admin",
                  "csrf-admin", {})
    assert st == 200, out
    return job["job_id"]


def req2_logout():
    r = urllib.request.Request(BASE + "/api/logout", data=b"{}", method="POST")
    r.add_header("Cookie", "PVEAuthCookie=t-admin")
    r.add_header("CSRFPreventionToken", "csrf-admin")
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, {}, resp.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as e:
        return e.code, {}, ""


class BackendTests(unittest.TestCase):
    def setUp(self):
        set_mode("normal")

    # ---- login relay (SEC-001: PVE is the only identity store) ---------------
    def _login(self, user, pw, tfa=None, fp=None):
        body = {"username": user, "password": pw, "realm": "pve"}
        if tfa:
            body["tfa"] = tfa
            body["tfa_fingerprint"] = fp
        r = urllib.request.Request(BASE + "/api/login",
                                   data=json.dumps(body).encode(), method="POST")
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode()), \
                    resp.headers.get("Set-Cookie", "")
        except urllib.error.HTTPError as e:
            return e.code, {}, ""

    def test_login_wrong_password_401(self):
        st, _, cookie = self._login("admin@pve", "wrong")
        self.assertEqual(st, 401)
        self.assertEqual(cookie, "")

    def test_login_rate_limits_after_five_failures(self):
        for _ in range(5):
            self._login("admin@pve", "wrong")
        st, _, _ = self._login("admin@pve", "pw-admin")
        self.assertEqual(st, 429)
        BE.login_guard.clear()  # unblock later tests

    def test_login_success_sets_httponly_cookie_and_returns_csrf(self):
        st, out, cookie = self._login("admin@pve", "pw-admin")
        self.assertEqual(st, 200)
        self.assertEqual(out["userid"], "admin@pve")
        self.assertEqual(out["csrf_token"], "csrf-admin")
        self.assertIn("PVEAuthCookie=t-admin", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        # the returned ticket+csrf work end-to-end against the real backend
        st, out = req("GET", "/api/me", ticket="t-admin")
        self.assertEqual(st, 200)
        self.assertEqual(out["userid"], "admin@pve")

    def test_login_tfa_challenge_roundtrip(self):
        st, out, _ = self._login("tfauser@pve", "pw-tfa")
        self.assertEqual(st, 200)
        self.assertTrue(out.get("tfa_required"))
        st, out, cookie = self._login("tfauser@pve", "pw-tfa",
                                      {"type": "totp", "response": "123456"},
                                      "fake-fp")
        self.assertEqual(st, 200)
        self.assertIn("PVEAuthCookie=t-admin", cookie)

    def test_logout_clears_cookie(self):
        st, _, cookie = req2_logout()
        self.assertEqual(st, 200)
        self.assertIn("Max-Age=0", cookie)

    # ---- static UI (no traversal, CSP present) -------------------------------
    def test_index_served_with_csp(self):
        with urllib.request.urlopen(BASE + "/", timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))
            self.assertIn("default-src 'self'",
                          resp.headers.get("Content-Security-Policy", ""))
            body = resp.read().decode()
        self.assertIn("/app.js", body)

    def test_unknown_and_traversal_paths_404(self):
        for p in ("/../backend/server.py", "/ui/index.html", "/etc/passwd"):
            try:
                st, _ = req("GET", p, ticket="t-admin")
            except urllib.error.HTTPError as e:
                st = e.code
            self.assertEqual(st, 404, p)

    # ---- guest list proxy (uses caller's own PVE permissions) -----------------
    def test_guests_list_for_admin(self):
        st, out = req("GET", "/api/guests", ticket="t-admin")
        self.assertEqual(st, 200)
        ids = {(g["type"], g["vmid"]) for g in out}
        self.assertIn(("lxc", 107), ids)
        self.assertIn(("qemu", 207), ids)

    def test_guests_forbidden_without_read(self):
        st, _ = req("GET", "/api/guests", ticket="t-nobody")
        self.assertEqual(st, 403)

    # ---- authentication ----------------------------------------------------
    def test_health_public(self):
        st, _ = req("GET", "/api/health")
        self.assertEqual(st, 200)

    def test_no_ticket_rejected(self):
        st, _ = req("GET", "/api/scan")
        self.assertEqual(st, 401)

    def test_bad_ticket_rejected(self):
        st, _ = req("GET", "/api/scan", ticket="forged-ticket")
        self.assertEqual(st, 401)

    def test_crlf_ticket_rejected(self):
        # either the HTTP client refuses the malformed header (urllib does)
        # or the server must answer 401; both are safe outcomes.
        try:
            st, _ = req("GET", "/api/scan", ticket="x\r\nX-Evil: 1")
        except Exception:
            return
        self.assertEqual(st, 401)

    # ---- authorization (SEC-002/003) ----------------------------------------
    def test_auditor_can_scan(self):
        st, out = req("GET", "/api/scan?target_type=lxc&target_id=107",
                      ticket="t-auditor")
        self.assertEqual(st, 200)
        self.assertEqual(out["guests"][0]["packages"], ["pkg1", "pkg2"])

    def test_auditor_cannot_create_job(self):
        st, _ = req("POST", "/api/jobs", "t-auditor", "csrf-auditor",
                    {"target_type": "lxc", "target_id": "107",
                     "operation": "update"})
        self.assertEqual(st, 403)

    def test_nobody_gets_nothing(self):
        st, _ = req("GET", "/api/scan", ticket="t-nobody")
        self.assertEqual(st, 403)

    def test_auditor_cannot_host_update(self):
        st, _ = req("POST", "/api/jobs", "t-auditor", "csrf-auditor",
                    {"target_type": "host", "target_id": "testnode",
                     "operation": "host-update"})
        self.assertEqual(st, 403)

    # ---- CSRF (SEC-007) ------------------------------------------------------
    def test_post_without_csrf_rejected(self):
        st, _ = req("POST", "/api/jobs", "t-admin", None,
                    {"target_type": "lxc", "target_id": "107",
                     "operation": "update"})
        self.assertEqual(st, 403)

    def test_post_wrong_csrf_rejected(self):
        st, _ = req("POST", "/api/jobs", "t-admin", "csrf-auditor",
                    {"target_type": "lxc", "target_id": "107",
                     "operation": "update"})
        self.assertEqual(st, 403)

    # ---- input validation (SEC-005/006/012) ---------------------------------
    def test_injection_target_id_rejected(self):
        st, _ = req("POST", "/api/jobs", "t-admin", "csrf-admin",
                    {"target_type": "lxc", "target_id": "107; rm -rf /",
                     "operation": "update"})
        self.assertEqual(st, 400)

    def test_bad_target_type_rejected(self):
        st, _ = req("POST", "/api/jobs", "t-admin", "csrf-admin",
                    {"target_type": "docker", "target_id": "107",
                     "operation": "update"})
        self.assertEqual(st, 400)

    def test_bad_operation_rejected(self):
        st, _ = req("POST", "/api/jobs", "t-admin", "csrf-admin",
                    {"target_type": "lxc", "target_id": "107",
                     "operation": "rm-rf"})
        self.assertEqual(st, 400)

    def test_unknown_path_404_and_put_405(self):
        st, _ = req("GET", "/api/nope", ticket="t-admin")
        self.assertEqual(st, 404)
        st, _ = req("PUT", "/api/jobs", "t-admin", "csrf-admin", {})
        self.assertEqual(st, 405)

    # ---- approval binding + lifecycle (SPEC §6) ------------------------------
    def test_full_flow_succeeds_with_audit(self):
        job_id = full_job()
        job = BE.store.get_job(job_id)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["actor"], "admin@pve")
        self.assertEqual(job["approval_actor"], "admin@pve")
        acts = [a["action"] for a in BE.store.audit_log(job_id)]
        self.assertIn("create", acts)
        self.assertIn("approve", acts)
        # engine got a fixed argv with the numeric id only
        self.assertIn("--only 107 --report", calls())

    def test_approve_requires_fingerprint_echo(self):
        st, out = req("POST", "/api/jobs", "t-admin", "csrf-admin",
                      {"target_type": "lxc", "target_id": "107",
                       "operation": "update"})
        job_id = out["job"]["job_id"]
        st, _ = req("POST", f"/api/jobs/{job_id}/approve", "t-admin",
                    "csrf-admin", {"confirm_fingerprint": "deadbeef"})
        self.assertEqual(st, 400)
        st, _ = req("POST", f"/api/jobs/{job_id}/start", "t-admin",
                    "csrf-admin", {})
        self.assertEqual(st, 409)  # not approved
        BE.store.transition(job_id, "cancelled")  # free the target

    def test_stale_approval_blocks(self):
        job_id = full_job()  # uses 107; free it by success
        # second job with ttl 0: approve then start must block
        CFG["approval_ttl_s"] = 0
        st, out = req("POST", "/api/jobs", "t-admin", "csrf-admin",
                      {"target_type": "lxc", "target_id": "111",
                       "operation": "update"})
        self.assertEqual(st, 201)
        jid = out["job"]["job_id"]
        req("POST", f"/api/jobs/{jid}/approve", "t-admin", "csrf-admin",
            {"confirm_fingerprint": out["job"]["preview_fingerprint"]})
        st, _ = req("POST", f"/api/jobs/{jid}/start", "t-admin", "csrf-admin", {})
        self.assertEqual(st, 409)
        self.assertEqual(BE.store.get_job(jid)["status"], "blocked")
        CFG["approval_ttl_s"] = 900

    def test_changed_preview_blocks(self):
        st, out = req("POST", "/api/jobs", "t-admin", "csrf-admin",
                      {"target_type": "lxc", "target_id": "107",
                       "operation": "update"})
        jid = out["job"]["job_id"]
        req("POST", f"/api/jobs/{jid}/approve", "t-admin", "csrf-admin",
            {"confirm_fingerprint": out["job"]["preview_fingerprint"]})
        set_mode("changed")  # runner re-scan now differs from approved fp
        st, _ = req("POST", f"/api/jobs/{jid}/start", "t-admin", "csrf-admin", {})
        self.assertEqual(st, 200)
        self.assertEqual(BE.store.get_job(jid)["status"], "blocked")

    # ---- concurrency (FR-014) -------------------------------------------------
    def test_duplicate_active_job_rejected(self):
        st, out = req("POST", "/api/jobs", "t-admin", "csrf-admin",
                      {"target_type": "lxc", "target_id": "107",
                       "operation": "update"})
        self.assertEqual(st, 201)
        st, _ = req("POST", "/api/jobs", "t-admin", "csrf-admin",
                    {"target_type": "lxc", "target_id": "107",
                     "operation": "update"})
        self.assertEqual(st, 409)
        # cancel the active job so other tests are unaffected
        BE.store.transition(out["job"]["job_id"], "cancelled")

    # ---- runner hardening (defense in depth) -----------------------------------
    def test_runner_rejects_tampered_job_file(self):
        qd = os.path.join(CFG["store_dir"], "queue")
        os.makedirs(qd, exist_ok=True)
        bad = {"job_id": "00000000-0000-0000-0000-000000000000",
               "target_type": "lxc", "target_id": "107; evil",
               "operation": "update", "fingerprint": "0" * 64,
               "approved_at": jobs_mod.now_iso(), "ttl_s": 900}
        with open(os.path.join(qd, bad["job_id"] + ".job"), "w") as f:
            json.dump(bad, f)
        subprocess.run(CFG["runner_cmd"], timeout=30)
        with open(os.path.join(qd, bad["job_id"] + ".result")) as f:
            res = json.load(f)
        self.assertEqual(res["status"], "blocked")
        self.assertNotIn("evil", calls())

    def test_timeout_records_interrupted(self):
        st, out = req("POST", "/api/jobs", "t-admin", "csrf-admin",
                      {"target_type": "lxc", "target_id": "111",
                       "operation": "update"})
        self.assertEqual(st, 201)
        jid = out["job"]["job_id"]
        req("POST", f"/api/jobs/{jid}/approve", "t-admin", "csrf-admin",
            {"confirm_fingerprint": out["job"]["preview_fingerprint"]})
        set_mode("slow")
        # runner_timeout=4 < engine sleep 30 -> runner killed -> no result
        st, _ = req("POST", f"/api/jobs/{jid}/start", "t-admin", "csrf-admin", {})
        self.assertEqual(st, 200)
        self.assertEqual(BE.store.get_job(jid)["status"], "interrupted")
        set_mode("normal")

    # ---- history + log hygiene (SEC-011) ---------------------------------------
    def test_history_readable_by_auditor(self):
        job_id = full_job()
        st, out = req("GET", "/api/jobs?limit=10", ticket="t-auditor")
        self.assertEqual(st, 200)
        ids = [j["job_id"] for j in out]
        self.assertIn(job_id, ids)
        st, out = req("GET", f"/api/jobs/{job_id}", ticket="t-auditor")
        self.assertEqual(st, 200)
        self.assertEqual(out["job"]["status"], "succeeded")

    def test_log_file_permissions(self):
        job_id = full_job()
        lp = os.path.join(CFG["store_dir"], "logs", job_id + ".log")
        self.assertTrue(os.path.exists(lp))
        mode = stat.S_IMODE(os.stat(lp).st_mode)
        self.assertEqual(mode, 0o640)
        db = os.path.join(CFG["store_dir"], "history.db")
        self.assertEqual(stat.S_IMODE(os.stat(db).st_mode), 0o640)

    def test_backup_status_endpoint(self):
        st, out = req("GET", "/api/backup-status", ticket="t-auditor")
        self.assertEqual(st, 200)
        self.assertEqual(out["backup_status"], "ok")


def tearDownModule():
    HTTPD.shutdown()
    pve_httpd.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
