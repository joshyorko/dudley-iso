#!/usr/bin/env bash
# Deployment-boundary experiment on an owned disposable disk, NOT acceptance.
set -euo pipefail
if [[ ${OMARCHY_REMOTE_BUILDER:-} == harvester ]]; then
    [[ $(hostname) == devsy-shared-harvester-c577d ]] || exit 2
elif [[ ${GITHUB_ACTIONS:-} != true || ${RUNNER_ENVIRONMENT:-} != github-hosted ]]; then
    echo 'Native deployment probe requires the owned Harvester or GitHub-hosted runner' >&2; exit 2
fi
: "${RUNNER_TEMP:?}" "${PROBE_OUTPUT:?}"
ref=ghcr.io/joshyorko/omarchy-bootc@sha256:aca01a25953209b1e38b8aa115fe253283153f159f6f28defaede9c528476386
source_sha=65a8e151b0bb44762a8cb1b9bdebd03c3a874296
mkdir -p "$PROBE_OUTPUT"
df -Pk "$RUNNER_TEMP" | awk 'NR==2 {exit !($4 >= 35*1024*1024)}'
work=$(mktemp -d "$RUNNER_TEMP/quattro-native-probe.XXXXXXXX")
loop=''
cleanup() {
    local status=$?
    trap - EXIT
    if mountpoint -q "$work/target"; then sudo -n umount --recursive "$work/target" || status=1; fi
    if [[ -n $loop ]] && [[ $(sudo -n losetup -n -O BACK-FILE "$loop") == "$work/disk.raw" ]]; then
        sudo -n losetup --detach "$loop" || status=1
    fi
    # Never upload the disk, OCI archive or image store.
    if ! mountpoint -q "$work/target"; then sudo -n rm -rf -- "$work"; fi
    exit "$status"
}
trap cleanup EXIT
sudo -n skopeo copy --preserve-digests "docker://$ref" "oci-archive:$work/source.oci.tar:quattro-probe" \
    2>&1 | tee "$PROBE_OUTPUT/registry-copy.log"
sudo -n skopeo inspect --raw "oci-archive:$work/source.oci.tar" | tee "$PROBE_OUTPUT/archive-manifest.json" >/dev/null
[[ $(skopeo manifest-digest "$PROBE_OUTPUT/archive-manifest.json") == "${ref##*@}" ]]
runtime_id=$(jq -er '.config.digest | select(test("^sha256:[a-f0-9]{64}$"))' "$PROBE_OUTPUT/archive-manifest.json")
sudo -n podman load --input "$work/source.oci.tar" 2>&1 | tee "$PROBE_OUTPUT/load.log"
sudo -n podman image inspect "$runtime_id" | tee "$PROBE_OUTPUT/image-inspect.json" >/dev/null
jq -e --arg id "$runtime_id" --arg sha "$source_sha" \
    '(.[0].Id | sub("^sha256:"; "") | "sha256:" + .) == $id and .[0].Labels["org.opencontainers.image.revision"] == $sha' \
    "$PROBE_OUTPUT/image-inspect.json" >/dev/null
truncate -s 48G "$work/disk.raw"
parted -s "$work/disk.raw" mklabel gpt mkpart ESP fat32 1MiB 1025MiB set 1 esp on mkpart ROOT btrfs 1025MiB 100%
loop=$(sudo -n losetup --find --show --partscan "$work/disk.raw")
sudo -n udevadm settle
sudo -n mkfs.vfat -F 32 "${loop}p1"
sudo -n mkfs.btrfs -f "${loop}p2"
mkdir "$work/target"
sudo -n mount "${loop}p2" "$work/target"
sudo -n mkdir -p "$work/target/boot/efi"
sudo -n mount "${loop}p1" "$work/target/boot/efi"
sudo -n podman run --rm --privileged --pid=host --ipc=host --userns=host \
    --network=host --security-opt label=disable \
    -v /dev:/dev -v /run:/run -v /sys:/sys:ro \
    -v /var/lib/containers:/var/lib/containers -v "$work:/run/quattro-probe:rshared" \
    "$runtime_id" bootc install to-filesystem \
    --source-imgref oci-archive:/run/quattro-probe/source.oci.tar \
    --target-imgref ghcr.io/joshyorko/omarchy-bootc:testing \
    --composefs-backend --bootloader systemd --generic-image \
    --skip-fetch-check --skip-finalize /run/quattro-probe/target \
    2>&1 | tee "$PROBE_OUTPUT/install.log"
sudo -n find "$work/target" -maxdepth 5 -printf '%y %P -> %l\n' | tee "$PROBE_OUTPUT/target-layout.txt" >/dev/null
sudo -n findmnt --json --submounts "$work/target" | tee "$PROBE_OUTPUT/target-mounts.json" >/dev/null
: >"$PROBE_OUTPUT/target-view.json"
sudo -n python3 - "$work/target" "$PROBE_OUTPUT/target-view.json" <<'PY'
import json, os, sys
from pathlib import Path
root=Path(sys.argv[1])
paths=['etc/passwd','etc/shadow','usr/bin/bash','usr/bin/omarchy-provision-user','var','state/deploy','boot/efi']
result={'scope':'Deployment-boundary diagnostics only, NOT installer or guest acceptance','paths':{}}
for name in paths:
    path=root/name
    result['paths'][name]={'lexists':os.path.lexists(path),'is_file':path.is_file(),'is_dir':path.is_dir(),
                          'symlink':os.readlink(path) if path.is_symlink() else None}
result['deployments']=[p.name for p in (root/'state/deploy').iterdir()] if (root/'state/deploy').is_dir() else []
Path(sys.argv[2]).write_text(json.dumps(result,indent=2)+'\n')
PY
# Keep the actual process result even when the physical root lacks a chroot
# userspace. This is a probe result, never an account-provisioning PASS.
status=0
sudo -n chroot "$work/target" /usr/bin/id 2>&1 | tee "$PROBE_OUTPUT/physical-chroot.log" || status=$?
jq -n --arg image "$ref" --arg source "$source_sha" --argjson chroot_exit "$status" \
    '{scope:"native filesystem deployment probe only",image:$image,source_sha:$source,
      physical_chroot_exit:$chroot_exit,iso_built:false,interactive_installation_qualified:false}' \
    >"$PROBE_OUTPUT/probe.receipt.json"
