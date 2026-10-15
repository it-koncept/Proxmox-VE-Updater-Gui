#!/bin/bash
# Build the pve-updater .deb WITHOUT root (dpkg-deb --root-owner-group sets
# root:root ownership in the archive; nothing is installed by this script).
# Usage: packaging/build_deb.sh [path-to-engine]
#   default engine: ../Proxmox-VE-Updater/update-vms.sh (engine repo main)
set -eu
cd "$(dirname "$0")/.."
ROOT="$PWD"
ENGINE="${1:-$ROOT/../Proxmox-VE-Updater/update-vms.sh}"
VERSION="0.9.0-12"
REQ_ENGINE_VERSION="3.5.0"

# --- pin the engine: refuse to package an unexpected version ----------------
[ -f "$ENGINE" ] || { echo "engine not found: $ENGINE" >&2; exit 1; }
grep -q "^VERSION=\"$REQ_ENGINE_VERSION\"" "$ENGINE" || {
    echo "engine at $ENGINE is not v$REQ_ENGINE_VERSION — refusing to build" >&2
    exit 1
}

STAGE="$ROOT/build/pve-updater-$VERSION"
rm -rf "$ROOT/build"
mkdir -p "$STAGE"/{DEBIAN,usr/local/bin,usr/local/sbin,usr/share/pve-updater,\
opt/pve-updater/{backend,ui,docs},etc/pve-updater,etc/sudoers.d,etc/systemd/system}

cp packaging/debian/control packaging/debian/postinst packaging/debian/prerm \
   packaging/debian/postrm packaging/debian/conffiles "$STAGE/DEBIAN/"
chmod 0755 "$STAGE/DEBIAN"
chmod 0755 "$STAGE"/DEBIAN/postinst "$STAGE"/DEBIAN/prerm "$STAGE"/DEBIAN/postrm

# engine (the ONLY component that mutates packages) + runner + backend + ui
install -m 0755 "$ENGINE" "$STAGE/usr/local/bin/update-vms.sh"
install -m 0755 backend/runner.py "$STAGE/usr/local/sbin/pve-updater-runner"
install -m 0644 backend/server.py backend/pveauth.py backend/jobs.py \
        backend/runner.py "$STAGE/opt/pve-updater/backend/"
install -m 0644 ui/index.html ui/app.js ui/style.css "$STAGE/opt/pve-updater/ui/"
install -m 0644 docs/BACKEND.md "$STAGE/opt/pve-updater/docs/"
install -m 0644 integration/sudoers.d.pve-updater \
        "$STAGE/usr/share/pve-updater/sudoers.d.pve-updater"
install -m 0644 integration/sudoers.d.pve-updater "$STAGE/etc/sudoers.d/pve-updater"
install -m 0644 integration/systemd/pve-updater.service \
        "$STAGE/etc/systemd/system/pve-updater.service"

# package config DEFAULT: shipped under /usr/share (NOT a conffile: dpkg
# conffile prompts break non-interactive upgrades); postinst copies it to
# /etc/pve-updater/config.json on first install only.
python3 - "$STAGE/usr/share/pve-updater/config.json.default" <<'PY'
import json, sys
cfg = {
  "engine": "/usr/local/bin/update-vms.sh",
  "pve_api_url": "https://localhost:8006/api2/json",
  "store_dir": "/var/lib/pve-updater",
  "node": "CHANGE_ME",
  "read_privs": ["VM.Audit", "Sys.Audit"],
  "update_privs": ["VM.Allocate"],
  "host_update_privs": ["Sys.Modify"],
  "approval_ttl_s": 900,
  "require_approval": False,
  "runner_cmd": ["sudo", "/usr/local/sbin/pve-updater-runner", "--config", "/etc/pve-updater/config.json"],
  "scan_timeout": 600,
  "update_timeout": 7200,
  "runner_timeout": 7800,
  "bind": "127.0.0.1",
  "port": 8765,
  "engine_version": "3.5.0"
}
with open(sys.argv[1], "w") as f:
    json.dump(cfg, f, indent=2)
PY

# umask hygiene: every directory in the archive must be traversable (0755);
# file modes are set explicitly by the install calls above
find "$STAGE" -type d -exec chmod 0755 {} +

dpkg-deb --root-owner-group --build "$STAGE" "$ROOT/build/pve-updater_${VERSION}_all.deb"
echo "built: $ROOT/build/pve-updater_${VERSION}_all.deb"
dpkg-deb -I "$ROOT/build/pve-updater_${VERSION}_all.deb" | sed -n '1,20p'
dpkg-deb -c "$ROOT/build/pve-updater_${VERSION}_all.deb"
