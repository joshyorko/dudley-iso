#!/usr/bin/env bash
# Explicit host capability boundary; Dagger owns phase ordering, not nested KVM.
set -euo pipefail
[[ ${OMARCHY_REMOTE_BUILDER:-} == harvester && $(hostname) == devsy-shared-harvester-c577d ]]
run=${1:?}; phase=${2:?}
[[ $run =~ ^/home/kdlocpanda/omarchy-builds/quattro-[a-zA-Z0-9.-]+$ ]]
cd "$run/source"
exec 9>"$run/../quattro-runtime.lock"
flock -n 9 || { echo 'Another genuine Quattro phase owns the host boundary' >&2; exit 1; }
export PATH="/home/kdlocpanda/omarchy-builds/tools:$PATH"
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$PWD"
export QUATTRO_SOURCE_SHA
QUATTRO_SOURCE_SHA=$(git -c safe.directory="$PWD" rev-parse HEAD)
export QUATTRO_RUN_ID=${run##*/}
export CONTAINERS_STORAGE_CONF="$run/podman-storage.conf"
export PROBE_PODMAN_ROOT=/var/lib/containers/omarchy-quattro
export PROBE_PODMAN_RUNROOT=/run/containers/omarchy-quattro
mkdir -p "$PROBE_PODMAN_ROOT" "$PROBE_PODMAN_RUNROOT"
cat >"$CONTAINERS_STORAGE_CONF" <<EOF
[storage]
driver="overlay"
graphroot="$PROBE_PODMAN_ROOT"
runroot="$PROBE_PODMAN_RUNROOT"
EOF
mkdir -p "$run/evidence"
exec > >(tee -a "$run/evidence/$phase.log") 2>&1
finish() {
    local status=$?
    printf '%s\n' "$status" >"$run/evidence/$phase.exit-code"
    exit "$status"
}
trap finish EXIT
minimum_gib=35
[[ $phase != desktop ]] || minimum_gib=5
df -Pk "$run" | awk -v minimum_gib="$minimum_gib" 'NR==2 {exit !($4 >= minimum_gib*1024*1024)}'
case "$phase" in
    probe)
        export RUNNER_TEMP="$run" PROBE_OUTPUT="$run/evidence/native-probe"
        if [[ -s $run/accepted-source.oci.tar ]]; then
            export PROBE_SOURCE_ARCHIVE="$run/accepted-source.oci.tar"
        fi
        bash scripts/probe-quattro-filesystem.sh
        ;;
    build)
        export OMARCHY_BOOTC_SOURCE_ARCHIVE="$run/accepted-source.oci.tar"
        python3 scripts/omarchy_quattro_build.py
        ;;
    live)
        export CAPTURE_HELPER="$run/payload-contract/scripts/ci/capture-vm-screen.py" RUNNER_TEMP="$run"
        bash scripts/capture-quattro-live-ui.sh output/omarchy-quattro/omarchy-quattro.iso
        ;;
    install|desktop)
        harness="$run/upstream"
        if [[ ! -d $harness/.git ]]; then
            git clone --filter=blob:none --branch quattro --single-branch --no-checkout "$(cat omarchy-quattro/upstream_url)" "$harness"
            git -C "$harness" checkout --detach "$(cat omarchy-quattro/upstream_revision)"
        fi
        [[ $(git -C "$harness" rev-parse HEAD) == "$(cat omarchy-quattro/upstream_revision)" ]]
        mkdir -p /usr/share/edk2/x64
        ln -sf /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/edk2/x64/OVMF_CODE.4m.fd
        ln -sf /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/edk2/x64/OVMF_VARS.4m.fd
        sed -i 's/omarchy-pkg-add qemu-full edk2-ovmf socat imagemagick tesseract tesseract-data-eng/true/' "$harness/bin/omarchy-iso-test"
        iso="$PWD/output/omarchy-quattro/omarchy-quattro.iso"
        cd "$harness"
        if [[ $phase == install ]]; then
            ./bin/omarchy-iso-test "$iso" --install-only --no-preview --timeout 14400
        else
            test -s "$harness/test-runs/omarchy-quattro/base.qcow2"
            digest=$(jq -er '.accepted_source_ref | split("@")[1]' "$run/source/omarchy-quattro/image.json")
            suite="$run/acceptance-source"
            if [[ ! -d $suite/.git ]]; then
                git init -q "$suite"
                git -C "$suite" fetch -q --depth=1 https://github.com/omacom/omarchy.git \
                    "$(cat "$run/source/omarchy-quattro/accepted_omarchy_revision")"
                git -C "$suite" checkout -q --detach FETCH_HEAD
            fi
            bash "$run/source/scripts/quattro-initial-desktop-smoke.sh" "$harness" "$iso" "$digest" "$suite"
        fi
        ;;
    *) exit 2 ;;
esac
