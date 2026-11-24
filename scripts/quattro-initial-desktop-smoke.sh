#!/usr/bin/env bash
set -euo pipefail
harness=${1:?}; iso=${2:?}; digest=${3:?}
[[ $digest =~ ^sha256:[a-f0-9]{64}$ ]]
generated="$harness/bin/omarchy-iso-initial-smoke"
python3 - "$harness/bin/omarchy-iso-test" "$generated" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text()
marker = "# ---------------------------------------------------------------------- main"
assert source.count(marker) == 1
Path(sys.argv[2]).write_text(source.split(marker, 1)[0])
PY
cat >>"$generated" <<'SMOKE'
[[ $REUSE_BASE == true && -s $BASE_DISK ]] || { echo 'An installed base is required' >&2; exit 1; }
log 'Initial disk-only desktop acceptance; optional upstream acceptance suite is not run'
qemu-img create -f qcow2 -b "$BASE_DISK" -F qcow2 "$RUN_DIR/run.qcow2" >/dev/null
start_vm "$RUN_DIR/run.qcow2" "$RUN_DIR/serial.log"
tr '\0' '\n' <"/proc/$(cat "$PIDFILE")/cmdline" >"$RUN_DIR/qemu-argv.txt"
qmp '"query-block"' >"$RUN_DIR/block-devices.json"
jq -se 'map(select(.return | type == "array")) as $r |
  ($r | length) == 1 and all($r[0].return[]; ((.inserted.file? // "") | endswith(".iso") | not))' \
  "$RUN_DIR/block-devices.json" >/dev/null
establish_session
before=$(ssh_session 'hyprctl -j clients | jq "[.[] | select(.class == \"foot\")] | length"')
[[ $before =~ ^[0-9]+$ ]]
press meta_l-ret
wait_for_guest_state 'terminal shortcut opens a new window' 30 \
  ssh_session "hyprctl -j clients | jq -e '[.[] | select(.class == \"foot\")] | length > $before'"
type_text 'uname -sr'
press ret
capture_console success-initial-terminal
verify_surface_shortcut menu meta_l-spc omarchy-menu omarchy.menu
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
cleanup_shortcut_state
capture_console success-initial-desktop
log "Initial disk-only password login, terminal, menu and installed-image checks passed: $RUN_DIR"
SMOKE
bash -n "$generated"
QUATTRO_EXPECTED_DIGEST="$digest" bash "$generated" "$iso" --reuse-base --no-preview --timeout 14400
