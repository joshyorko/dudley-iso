#!/usr/bin/env bash
# Boot the genuine ISO with no cidata/config answers and retain actual pixels.
set -euo pipefail
if [[ ${OMARCHY_REMOTE_BUILDER:-} == harvester ]]; then
    [[ $(hostname) == devsy-shared-harvester-c577d ]] || exit 2
elif [[ ${GITHUB_ACTIONS:-} != true || ${RUNNER_ENVIRONMENT:-} != github-hosted ]]; then
    exit 2
fi
iso=$(realpath "${1:?ISO path required}")
: "${RUNNER_TEMP:?}" "${CAPTURE_HELPER:?}"
out=output/omarchy-quattro/interactive-live
mkdir -p "$out"
work=$(mktemp -d "$RUNNER_TEMP/quattro-live-ui.XXXXXXXX")
cleanup() {
    local status=$? pid
    if [[ -s $work/qemu.pid ]]; then
        pid=$(cat "$work/qemu.pid")
        if [[ $pid =~ ^[0-9]+$ ]] && tr '\0' ' ' <"/proc/$pid/cmdline" | grep -Fq -- "$work/disk.qcow2"; then
            kill "$pid"; wait "$pid" 2>/dev/null || true
        fi
    fi
    rm -rf -- "$work"
    exit "$status"
}
trap cleanup EXIT
test -r /dev/kvm && test -w /dev/kvm
cp /usr/share/OVMF/OVMF_VARS_4M.fd "$work/vars.fd"
qemu-img create -f qcow2 "$work/disk.qcow2" 64G
qemu-system-x86_64 -machine q35,accel=kvm -cpu host -smp 4 -m 6144 \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
    -drive "if=pflash,format=raw,file=$work/vars.fd" \
    -drive "file=$work/disk.qcow2,if=virtio,format=qcow2" \
    -drive "file=$iso,media=cdrom,readonly=on" -boot order=d \
    -netdev user,id=net0 -device virtio-net-pci,netdev=net0 \
    -vga virtio -display none -serial "file:$PWD/$out/live-serial.log" \
    -qmp "unix:$work/qmp.sock,server=on,wait=off" -pidfile "$work/qemu.pid" -daemonize
for number in 1 2 3 4; do
    sleep 30
    kill -0 "$(cat "$work/qemu.pid")"
    python3 "$CAPTURE_HELPER" "$work/qmp.sock" "$out/official-live-$number.png"
done
python3 - "$iso" "$out" <<'PY'
from datetime import datetime,timezone
import hashlib,json,os,sys
from pathlib import Path
iso=Path(sys.argv[1]);out=Path(sys.argv[2])
with iso.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
entries=[]
for path in sorted(out.glob('*.png')):
    entries.append({'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                    'captured_at':datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(),
                    'stage':'actual firmware/official live UI observation; requires visual review'})
(out/'manifest.json').write_text(json.dumps({'scope':'Actual KVM ISO boot without cidata or injected answers; not installed-system proof',
 'builder_sha':os.environ.get('QUATTRO_SOURCE_SHA',os.environ.get('GITHUB_SHA')),'payload_source':'65a8e151b0bb44762a8cb1b9bdebd03c3a874296',
 'payload_digest':'sha256:aca01a25953209b1e38b8aa115fe253283153f159f6f28defaede9c528476386',
 'iso_sha256':digest,'run_id':os.environ.get('QUATTRO_RUN_ID',os.environ.get('GITHUB_RUN_ID')),'job':os.environ.get('GITHUB_JOB','harvester-live'),
 'recorded_at':datetime.now(timezone.utc).isoformat(),'screenshots':entries,
 'disk_account_questions_completed':False,'installed_desktop_qualified':False},indent=2)+'\n')
PY
