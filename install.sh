#!/usr/bin/env bash
# pve-updater installer — run as root on a Proxmox VE node.
# Safe to re-run: installs or upgrades to the latest GitHub release.
set -euo pipefail

REPO="it-koncept/Proxmox-VE-Updater-Gui"

[ "$(id -u)" -eq 0 ] || { echo "ERROR: run as root (sudo bash install.sh)" >&2; exit 1; }
command -v pvesh >/dev/null 2>&1 || { echo "ERROR: pvesh not found — this does not look like a Proxmox VE node." >&2; exit 1; }

API="https://api.github.com/repos/${REPO}/releases/latest"
# The repo is public; if it is ever private again, pass a token:
#   GH_TOKEN=*** bash install.sh
# (fine-grained PAT, scope: contents:read on this repo only).
AUTH=()
if [ -n "${GH_TOKEN:-}" ]; then AUTH=(-H "Authorization: Bearer ${GH_TOKEN}"); fi
JSON=$(curl -fsSL "${AUTH[@]}" "$API" 2>/dev/null || true)
# download via the API asset endpoint (works for private repos; the plain
# browser_download_url 404s without a browser session)
ASSET=$(echo "$JSON" | grep -o '"url": *"https://api\.github\.com/repos/[^"]*/releases/assets/[0-9]*"' | head -1 | cut -d'"' -f4)
[ -n "${ASSET:-}" ] || { echo "ERROR: no .deb asset found (private repo? export GH_TOKEN=*** contents:read)." >&2; exit 1; }

echo "Downloading latest release asset..."
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "${AUTH[@]}" -H "Accept: application/octet-stream" -o "$TMP/pve-updater.deb" "$ASSET"
echo "sha256: $(sha256sum "$TMP/pve-updater.deb" | cut -d' ' -f1)"

DEBIAN_FRONTEND=noninteractive apt-get install -y "$TMP/pve-updater.deb"
systemctl enable --now pve-updater

echo
echo "Installed and running. The UI listens on 127.0.0.1:8765 of this node only."
echo "From your workstation, open it with an SSH tunnel:"
echo "  ssh -L 8765:127.0.0.1:8765 root@$(hostname)"
echo "  then browse to http://localhost:8765/ and log in with your PVE account."
echo
echo "Config: /etc/pve-updater/config.json — see docs/INSTALL.md in the repo."
