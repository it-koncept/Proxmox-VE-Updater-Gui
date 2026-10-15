/* pve-updater UI: vanilla JS, no inline handlers (CSP script-src 'self').
   CSRF token lives in memory only (from the login response), never in
   localStorage. All rendering goes through textContent — no innerHTML with
   user data. */
"use strict";

const S = { csrf: null, userid: null, perms: null, poll: null };

async function api(method, path, body) {
  const opt = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  if (method !== "GET" && S.csrf) {
    opt.headers["CSRFPreventionToken"] = S.csrf;
  }
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch (e) { /* empty body */ }
  if (!r.ok) {
    const err = new Error((data && data.error) || ("HTTP " + r.status));
    err.status = r.status;
    throw err;
  }
  return data;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = String(text);
  return n;
}
const $ = (id) => document.getElementById(id);

function hasPriv(path, privs) {
  if (!S.perms) return false;
  for (const [k, map] of Object.entries(S.perms)) {
    const norm = "/" + k.split("/").filter(Boolean).join("/");
    const target = "/" + path.split("/").filter(Boolean).join("/");
    if (norm === "/" || target === norm || target.startsWith(norm + "/")) {
      for (const p of privs) if (map && map[p]) return true;
    }
  }
  return false;
}

/* ---------- login / logout ---------- */
let tfaChallenge = null;

$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("login-msg").textContent = "";
  const body = {
    username: $("l-user").value,
    password: $("l-pass").value,
    realm: $("l-realm").value || "pve",
  };
  if (tfaChallenge) {
    body.tfa = { type: tfaChallenge.tfa_type, response: $("l-tfa").value };
    body.tfa_fingerprint = tfaChallenge.tfa_fingerprint;
  }
  try {
    const r = await api("POST", "/api/login", body);
    if (r.tfa_required) {
      tfaChallenge = r;
      $("tfa-row").classList.remove("hidden");
      $("login-msg").textContent = "Second factor required — enter your code.";
      $("l-pass").value = "";
      return;
    }
    tfaChallenge = null;
    S.csrf = r.csrf_token;
    S.userid = r.userid;
    $("l-pass").value = "";
    await startApp();
  } catch (e) {
    $("login-msg").textContent = e.message;
    $("l-pass").value = "";
  }
});

$("btn-logout").addEventListener("click", async () => {
  try { await api("POST", "/api/logout", {}); } catch (e) { /* ignore */ }
  S.csrf = null; S.userid = null; S.perms = null;
  if (S.poll) { clearInterval(S.poll); S.poll = null; }
  $("app").classList.add("hidden");
  $("login").classList.remove("hidden");
  $("whoami").textContent = "";
});

/* ---------- app ---------- */
async function startApp() {
  $("login").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("btn-logout").classList.remove("hidden");
  const me = await api("GET", "/api/me");
  S.perms = me.permissions;
  $("whoami").textContent = me.userid;
  await Promise.all([loadHealth(), loadBackupBanner(), loadGuests(), loadHistory()]);
  if (!S.poll) S.poll = setInterval(refreshActive, 3000);
}

async function loadHealth() {
  try {
    const h = await api("GET", "/api/health");
    $("engine-info").textContent = "engine " + h.engine_version;
  } catch (e) { /* health is public; failure means backend down */ }
}

async function loadBackupBanner() {
  const b = $("banner");
  b.textContent = "";
  try {
    const r = await api("GET", "/api/backup-status");
    if (r.backup_status === "ok") {
      b.appendChild(el("div", "ok", "Backup jobs: enabled (" +
        (r.enabled_backup_jobs || 0) + ")"));
    } else {
      b.appendChild(el("div", "warn", "Backup coverage unknown or missing — " +
        "updates will warn per target."));
    }
  } catch (e) {
    if (e.status === 403) return; // auditors without node read: no banner
    b.appendChild(el("div", "warn", "backup-status: " + e.message));
  }
}

async function loadGuests() {
  const tbody = $("guests").querySelector("tbody");
  tbody.textContent = "";
  let guests = [];
  try { guests = await api("GET", "/api/guests"); }
  catch (e) { tbody.appendChild(el("tr")); tbody.lastChild.appendChild(
    el("td", "error", "guest list: " + e.message)); return; }
  for (const g of guests) {
    const tr = el("tr");
    tr.appendChild(el("td", null, g.vmid));
    tr.appendChild(el("td", null, g.name));
    tr.appendChild(el("td", null, g.type));
    let statusText = g.status;
    let warnCell = null;
    if (g.type === "qemu" && g.status === "running" && g.agent !== "running") {
      // missing agent field = not configured in PVE; both cases mean
      // qm guest exec will fail and the engine will skip this guest
      statusText += " (agent: " + (g.agent || "not configured") + ")";
      warnCell = "warn";
    }
    tr.appendChild(el("td", warnCell, statusText));
    tr.appendChild(el("td", null, g.tags));
    const td = el("td");
    if (hasPriv("vms/" + g.vmid, ["VM.Allocate"])) {
      const btn = el("button", null, "Update");
      btn.addEventListener("click", () => startFlow(g.type, String(g.vmid),
        g.name, "update"));
      td.appendChild(btn);
    }
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  const hr = $("host-row");
  hr.textContent = "";
  const node = (window.__node__) || "this node";
  if (hasPriv("nodes", ["Sys.Modify"])) {
    const btn = el("button", "danger", "Update this host");
    btn.addEventListener("click", () => startFlow("host", node, node,
      "host-update"));
    hr.appendChild(btn);
  }
}

/* ---------- update flow: preview -> approve (fingerprint echo) -> start ---- */
let flow = null;

async function startFlow(ttype, tid, name, op) {
  try {
    const r = await api("POST", "/api/jobs",
      { target_type: ttype, target_id: tid, target_name: name, operation: op });
    flow = r.job;
    renderActive();
  } catch (e) {
    alert("Could not create job: " + e.message);
  }
}

function renderActive() {
  const box = $("active-job");
  box.textContent = "";
  if (!flow) { box.appendChild(el("p", "muted", "none")); return; }
  box.appendChild(el("p", null, "job " + flow.job_id + " — " +
    flow.target_type + " " + flow.target_id + " (" + flow.target_name + ")"));
  box.appendChild(el("p", "status-" + flow.status, "status: " + flow.status));
  if (flow.packages && flow.packages.length) {
    const ul = el("ul", "pkgs");
    flow.packages.slice(0, 50).forEach(p => ul.appendChild(el("li", null, p)));
    if (flow.packages.length > 50)
      ul.appendChild(el("li", "muted", "+ " + (flow.packages.length - 50) + " more"));
    box.appendChild(ul);
  }
  if (flow.status === "awaiting_approval") {
    box.appendChild(el("p", null, "Packages change if the guest updates in the " +
      "meantime; starting re-verifies a fingerprint and blocks on mismatch."));
    box.appendChild(el("p", "fp", "fingerprint: " + flow.preview_fingerprint));
    const inp = el("input");
    inp.size = 40;
    inp.placeholder = "paste fingerprint to approve";
    const btnA = el("button", null, "Approve");
    btnA.addEventListener("click", async () => {
      try {
        const r = await api("POST", "/api/jobs/" + flow.job_id + "/approve",
          { confirm_fingerprint: inp.value.trim() });
        flow = r.job; renderActive();
      } catch (e) { alert("approve: " + e.message); }
    });
    box.appendChild(inp); box.appendChild(btnA);
  }
  if (flow.status === "approved") {
    box.appendChild(el("p", "muted", "approved — start within the approval " +
      "window, otherwise the job blocks."));
    const btnS = el("button", "primary", "Start update");
    btnS.addEventListener("click", async () => {
      try {
        const r = await api("POST", "/api/jobs/" + flow.job_id + "/start", {});
        flow = r.job; renderActive(); loadHistory();
      } catch (e) { alert("start: " + e.message); renderActive(); }
    });
    box.appendChild(btnS);
  }
  if (["succeeded", "failed", "blocked", "interrupted", "cancelled",
       "unsupported", "partial"].includes(flow.status)) {
    const done = el("button", null, "Dismiss");
    done.addEventListener("click", () => { flow = null; renderActive(); });
    box.appendChild(done);
  }
}

async function refreshActive() {
  try {
    const jobs = await api("GET", "/api/jobs?limit=5");
    const act = jobs.find(j => !["succeeded", "failed", "blocked",
      "interrupted", "cancelled", "unsupported", "partial"].includes(j.status));
    if (act && (!flow || act.job_id === flow.job_id)) {
      flow = act; renderActive();
    }
    renderHistory(jobs.length ? jobs : null);
  } catch (e) { /* transient */ }
}

/* ---------- history ---------- */
function renderHistory(jobs) {
  const tbody = $("history").querySelector("tbody");
  tbody.textContent = "";
  if (!jobs) return;
  for (const j of jobs) {
    const tr = el("tr");
    tr.appendChild(el("td", null, j.created_at));
    tr.appendChild(el("td", null, j.target_type + " " + j.target_id +
      (j.target_name ? " (" + j.target_name + ")" : "")));
    tr.appendChild(el("td", null, j.operation));
    tr.appendChild(el("td", "status-" + j.status, j.status));
    tr.appendChild(el("td", null, j.actor));
    const td = el("td");
    const btn = el("button", null, "detail");
    btn.addEventListener("click", () => showDetail(j.job_id));
    td.appendChild(btn);
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
}

async function loadHistory() {
  try { renderHistory(await api("GET", "/api/jobs?limit=25")); }
  catch (e) { /* 403 handled silently */ }
}

async function showDetail(jobId) {
  const box = $("job-detail");
  box.textContent = "";
  try {
    const r = await api("GET", "/api/jobs/" + jobId);
    box.appendChild(el("h3", null, "job " + jobId + " — " + r.job.status));
    const pre = el("pre", "log");
    pre.textContent = r.log_tail || "(no log)";
    box.appendChild(pre);
  } catch (e) { box.appendChild(el("p", "error", e.message)); }
}

/* node name for host target: from health? backend knows cfg.node; expose via
   /api/health response if present, else fall back to prompt-free default. */
(async () => {
  try {
    const h = await fetch("/api/health", { credentials: "same-origin" });
    const d = await h.json();
    if (d.node) window.__node__ = d.node;
  } catch (e) { /* ignore */ }
})();
