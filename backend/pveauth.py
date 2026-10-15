#!/usr/bin/env python3
"""PVE identity/permission verification for the pve-updater backend.

SEC-001: PVE is the only identity store. We never accept a local password.
The caller's PVEAuthCookie ticket is verified by calling the PVE API itself;
the response gives us the authenticated userid and effective permissions.
"""
import json
import ssl
import urllib.parse
import urllib.request
import urllib.error


class PveAuth:
    def __init__(self, base_url, timeout=10, tls_verify=False):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        # PVE's own cert is self-signed by default; when talking to localhost
        # over https we pin nothing and rely on the host boundary. Set
        # tls_verify=true only when pve_api_url points at a properly CA-signed
        # endpoint.
        if self.base.startswith("https://") and not tls_verify:
            self.ssl_ctx = ssl._create_unverified_context()
        else:
            self.ssl_ctx = ssl.create_default_context()

    @staticmethod
    def _safe_ticket(ticket):
        # Reject anything that could inject headers or break cookie parsing.
        # NOTE: '=' is deliberately ALLOWED: real PVE tickets contain base64
        # padding; cookie parsing splits on the FIRST '=' only (partition).
        if not isinstance(ticket, str) or not ticket:
            return False
        if len(ticket) > 4096:
            return False
        if any(c in ticket for c in ("\r", "\n", "\x00", ";", " ")):
            return False
        return True

    def _get(self, path, ticket):
        req = urllib.request.Request(self.base + path)
        req.add_header("Cookie", "PVEAuthCookie=" + ticket)
        with urllib.request.urlopen(req, timeout=self.timeout,
                                    context=self.ssl_ctx) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def _post(self, path, ticket, body, csrf=None):
        req = urllib.request.Request(self.base + path,
                                     data=json.dumps(body).encode("utf-8"),
                                     method="POST")
        req.add_header("Cookie", "PVEAuthCookie=" + ticket)
        req.add_header("Content-Type", "application/json")
        if csrf:
            req.add_header("CSRFPreventionToken", csrf)
        with urllib.request.urlopen(req, timeout=self.timeout,
                                    context=self.ssl_ctx) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def login(self, username, password, realm=None, tfa=None,
              tfa_fingerprint=None):
        """Relay credentials to PVE's /access/ticket. PVE remains the only
        identity store (SEC-001): we never store or compare passwords.

        Returns (ticket, csrf_token, userid) on success,
        ("tfa", challenge_dict, None) when PVE demands 2FA, None on refusal.
        """
        if not isinstance(username, str) or not isinstance(password, str):
            return None
        payload = {"username": username, "password": password}
        if realm:
            payload["realm"] = realm
        if tfa is not None and tfa_fingerprint:
            payload["tfa-challenge"] = tfa
            payload["tfa-fingerprint"] = tfa_fingerprint
        body = json.dumps(payload).encode()
        req = urllib.request.Request(self.base + "/access/ticket", data=body,
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=self.ssl_ctx) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            return None
        d = data.get("data") or {}
        challenge = d.get("tfa-challenge")
        if isinstance(challenge, dict):
            return ("tfa", {"tfa_type": challenge.get("tfa-type"),
                            "tfa_fingerprint": d.get("tfa-fingerprint")}, None)
        ticket, csrf = d.get("ticket"), d.get("CSRFPreventionToken")
        userid = d.get("username")
        if not self._safe_ticket(ticket) or not isinstance(csrf, str) \
                or not isinstance(userid, str):
            return None
        return (ticket, csrf, userid)

    def verify(self, ticket):
        """Return {'userid':..., 'permissions': {...}} or None if invalid.

        Two response shapes are handled:
        - /access/permissions?effective=1 -> {userid, permissions} (works
          for root tickets; PVE 8.4 returns 400 for non-root callers)
        - plain /access/permissions -> the caller's effective map directly
          (path -> {priv: 1}); userid is then taken from the ticket format
          'PVE:<userid>:<timestamp>:<checksum>' (display/audit only; the
          permission map itself always comes from PVE).
        """
        if not self._safe_ticket(ticket):
            return None
        userid, perms = None, None
        try:
            data = self._get("/access/permissions?effective=1", ticket).get("data") or {}
            userid = data.get("userid")
            perms = data.get("permissions")
        except Exception:
            pass
        if not isinstance(perms, dict):
            try:
                perms = self._get("/access/permissions", ticket).get("data")
            except Exception:
                return None
        if not isinstance(perms, dict):
            return None
        if not isinstance(userid, str) or not userid:
            parts = ticket.split(":")
            userid = parts[1] if len(parts) >= 4 else None
        if not isinstance(userid, str) or not userid:
            return None
        return {"userid": userid, "permissions": perms}

    def csrf_ok(self, ticket, token):
        """CSRFPreventionToken must be the token PVE issued for this ticket.

        PVE 8.4 has NO /access/csrf_token endpoint (GET and POST are both
        'not implemented'). Instead we let PVE's own CSRF guard do the
        verification: POST a deliberately invalid body to a harmless
        endpoint. PVE checks the CSRF header BEFORE any handler or permission
        check runs:
          - forged/missing token  -> 401 (CSRF guard rejects)
          - valid token           -> 400 (param validation) or 403 (ACL)
        The empty body can never create anything (required params missing),
        so the probe has no side effects even for privileged callers.
        """
        if not self._safe_ticket(ticket) or not isinstance(token, str) or not token:
            return False
        if len(token) > 4096 or any(c in token for c in ("\r", "\n", "\x00")):
            return False
        try:
            self._post("/access/domains", ticket, {}, csrf=token)
            # 2xx would mean a realm was created: must never happen; fail safe.
            return False
        except urllib.error.HTTPError as e:
            return e.code != 401
        except Exception:
            return False


def path_privileges(permissions, path):
    """Union of privileges the user holds on `path` or any ancestor path.

    PVE's effective permission map keys are paths like '/', '/vms', '/vms/107'.
    """
    path = path if path.startswith("/") else "/" + path
    norm = "/" + "/".join(p for p in path.split("/") if p)
    privs = set()
    for key, mapping in permissions.items():
        k = "/" + "/".join(p for p in str(key).split("/") if p)
        if k == "/" or norm == k or norm.startswith(k + "/"):
            if isinstance(mapping, dict):
                privs.update(p for p, v in mapping.items() if v)
    return privs
