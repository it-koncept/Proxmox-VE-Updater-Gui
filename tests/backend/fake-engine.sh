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
    i=$(idx_of --only); id="${args[$((i+1))]:-0}"
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
    printf '{"mode":"update","guests":[{"vmid":107,"status":"success"}]}\n' > "$rp"
fi
exit 0
