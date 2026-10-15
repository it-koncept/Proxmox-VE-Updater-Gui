#!/bin/bash
# Fake engine for backend tests ONLY. Records argv to $FAKE_ENGINE_CALLS.
# Never touches a real system; never calls apt/pct/qm.
# Behavior via env FAKE_ENGINE_MODE: normal|changed|empty|slow
echo "$*" >> "$FAKE_ENGINE_CALLS"
mode="${FAKE_ENGINE_MODE:-normal}"
args=("$@")
has() { [[ " ${args[*]} " == *" $1 "* ]]; }
idx_of() {
    local i
    for i in "${!args[@]}"; do
        [ "${args[$i]}" = "$1" ] && { echo "$i"; return; }
    done
    echo -1
}
if has --scan; then
    i=$(idx_of --report); rp="${args[$((i+1))]}"
    i=$(idx_of --only)
    if [ "$i" -ge 0 ]; then id="${args[$((i+1))]}"; else id=0; fi
    pkgs='["pkg1","pkg2"]'
    case "$mode" in
        changed) pkgs='["pkgX"]' ;;
        empty)   pkgs='[]' ;;
    esac
    [ "$id" = "111" ] && pkgs='[]'
    printf '{"mode":"scan","guests":[{"vmid":%s,"status":"success","packages":%s}]}\n' \
        "$id" "$pkgs" > "$rp"
    exit 0
fi
if has --backup-status; then
    echo '{"backup_status":"ok","enabled_backup_jobs":1,"candidates":[]}'
    exit 0
fi
if [ "$mode" = "slow" ]; then
    sleep 30
    exit 0
fi
i=$(idx_of --report)
if [ "$i" -ge 0 ]; then
    rp="${args[$((i+1))]}"
    case "$mode" in
        fail)      printf '{"mode":"update","guests":[{"vmid":107,"status":"failed"}]}\n' > "$rp"; exit 1 ;;
        preflight) echo "preflight error" >&2; exit 2 ;;
        lock)      echo "another run holds the lock" >&2; exit 3 ;;
        skip)      printf '{"mode":"update","guests":[{"vmid":107,"status":"skipped"}]}\n' > "$rp"; exit 0 ;;
        warn)      printf '{"mode":"update","guests":[{"vmid":107,"status":"warning"}]}\n' > "$rp"; exit 0 ;;
    esac
    if has --host; then
        printf '{"mode":"host-update","guests":[{"vmid":0,"status":"success"}]}\n' > "$rp"
        exit 0
    fi
    printf '{"mode":"update","guests":[{"vmid":107,"status":"success"}]}\n' > "$rp"
fi
exit 0
