#!/usr/bin/env bash
set -euo pipefail
harness=${1:?}; iso=${2:?}; digest=${3:?}; suite=${4:?}
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
revision=$(cat "$root/omarchy-quattro/accepted_omarchy_revision")
[[ $(git -C "$suite" rev-parse HEAD) == "$revision" ]]
git -C "$suite" diff --quiet HEAD -- test
[[ -z $(git -C "$suite" ls-files --others --exclude-standard -- test) ]]
test -f "$suite/test/acceptance"
[[ $digest =~ ^sha256:[a-f0-9]{64}$ ]]
generated="$harness/bin/omarchy-iso-initial-smoke"
python3 - "$harness/bin/omarchy-iso-test" "$generated" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text()
marker = "# ---------------------------------------------------------------------- main"
assert source.count(marker) == 1
source = source.split(marker, 1)[0]
apps = "apps-menu|meta_l-alt-spc|omarchy-menu|omarchy.menu"
assert source.count(apps) == 1
source = source.replace(apps, "apps-menu|meta_l-alt-spc|omarchy-dock-apps|omarchy.dock")
launch = "setsid -f chromium --new-window about:blank >/dev/null 2>&1"
assert source.count(launch) == 1
source = source.replace(
    launch,
    "setsid -f bash -c 'profile=\\$(mktemp -d /tmp/quattro-chromium.XXXXXX); "
    "exec chromium --user-data-dir=\\\"\\$profile\\\" --no-first-run --new-window about:blank "
    ">/tmp/quattro-chromium-clipboard.log 2>&1'",
)
Path(sys.argv[2]).write_text(source)
PY
cat >>"$generated" <<'SMOKE'
[[ $REUSE_BASE == true && -s $BASE_DISK ]] || { echo 'An installed base is required' >&2; exit 1; }
log 'Disk-only desktop acceptance'
cp "$BASE_OVMF" "$RUN_DIR/OVMF_VARS.4m.fd"
BASE_OVMF="$RUN_DIR/OVMF_VARS.4m.fd"
qemu-img create -f qcow2 -b "$BASE_DISK" -F qcow2 "$RUN_DIR/run.qcow2" >/dev/null
start_vm "$RUN_DIR/run.qcow2" "$RUN_DIR/serial.log"
tr '\0' '\n' <"/proc/$(cat "$PIDFILE")/cmdline" >"$RUN_DIR/qemu-argv.txt"
qmp '"query-block"' >"$RUN_DIR/block-devices.json"
jq -se 'map(select(.return | type == "array")) as $r |
  ($r | length) == 1 and all($r[0].return[]; ((.inserted.file? // "") | endswith(".iso") | not))' \
  "$RUN_DIR/block-devices.json" >/dev/null
wait_for_ssh "$BOOT_TIMEOUT" failure-session-ssh-timeout
ssh_guest "echo $GUEST_PASSWORD | sudo -S chvt 1"
establish_session
ssh_guest 'journalctl -b --no-pager -u sddm -n 50' >"$RUN_DIR/sddm-journal.log"
before=$(ssh_session 'hyprctl -j clients | jq "[.[] | select(.class == \"foot\")] | length"')
[[ $before =~ ^[0-9]+$ ]]
press meta_l-ret
wait_for_guest_state 'terminal shortcut opens a new window' 30 \
  ssh_session "hyprctl -j clients | jq -e '[.[] | select(.class == \"foot\")] | length > $before'"
type_text 'uname -sr'
press ret
capture_console success-initial-terminal
ssh_guest "echo $GUEST_PASSWORD | sudo -S bootc status --json" >"$RUN_DIR/bootc-status.json"
jq -e --arg digest "$QUATTRO_EXPECTED_DIGEST" '.status.booted.image.imageDigest == $digest' \
  "$RUN_DIR/bootc-status.json" >/dev/null
cat >"$RUN_DIR/installed-contract.sh" <<'GUEST'
#!/usr/bin/env bash
set -euo pipefail
grep -Fxq upstream-omarchy-iso /var/lib/omarchy-bootc/installer-origin
grep -Fxq status=fresh-iso /var/lib/omarchy-bootc/adoption/state.env
test -f /var/home/omarchy/.local/state/omarchy/done/finalize-user
test "$(stat -c %u /var/home/omarchy)" = "$(id -u omarchy)"
passwd -S omarchy | awk '$2 != "P" {exit 1}'
python3 - <<'PYC'
import json
from pathlib import Path
receipt=json.loads(Path('/var/lib/omarchy-bootc/native-system-setup.json').read_text())
assert receipt['swap_policy']=='no-zram-prototype'
assert receipt['locale'].split('=',1)[1].lower().replace('.','').replace('-','')=='cutf8'
print(json.dumps(receipt,indent=2))
PYC
test -z "$(swapon --show --noheadings)"
grep -q 'composefs=' /proc/cmdline
findmnt -n -o OPTIONS --target /usr | tr , '\n' | grep -Fxq ro
findmnt -n -o OPTIONS --target /etc | tr , '\n' | grep -Fxq rw
findmnt -n -o OPTIONS --target /var | tr , '\n' | grep -Fxq rw
findmnt -n -o FSTYPE --target /boot | grep -Fxq vfat
test -s /boot/EFI/BOOT/BOOTX64.EFI
cat /proc/cmdline /etc/fstab
findmnt --json --target /usr
findmnt --json --target /etc
findmnt --json --target /var/home
bootctl --no-pager status
GUEST
ssh_guest 'cat >/tmp/quattro-installed-contract.sh' <"$RUN_DIR/installed-contract.sh"
ssh_guest "echo $GUEST_PASSWORD | sudo -S bash /tmp/quattro-installed-contract.sh" >"$RUN_DIR/installed-contract.log"
sync_omarchy
ssh_guest 'test -f .local/share/omarchy/test/acceptance'
git -C "$SYNC_DIR" rev-parse HEAD >"$RUN_DIR/acceptance-source-revision.txt"
status=0
shortcut_smoke_phase || status=1
ssh_guest 'cat /tmp/quattro-chromium-clipboard.log' >"$RUN_DIR/chromium-clipboard.log" || true
ssh_guest "OMARCHY_PATH=/usr/share/omarchy OMARCHY_ACCEPTANCE_DIR=/tmp/omarchy-acceptance \
    OMARCHY_ACCEPTANCE_SUDO_PASSWORD=$GUEST_PASSWORD bash .local/share/omarchy/test/acceptance" \
    2>&1 | tee "$RUN_DIR/acceptance.log" || status=1
ssh_guest 'tar -C /tmp -cf - omarchy-acceptance' | tar -C "$RUN_DIR" -xf - || status=1
if ((status == 0)); then
    capture_console success-acceptance-final
    log "Disk-only desktop acceptance passed: $RUN_DIR"
else
    capture_console failure-acceptance-final
fi
exit "$status"
SMOKE
bash -n "$generated"
QUATTRO_EXPECTED_DIGEST="$digest" bash "$generated" "$iso" --reuse-base --no-preview --timeout 14400 --sync-omarchy "$suite"
