output_dir := "output"
workdir := output_dir
debug := "0"
installer_channel := "stable"
compression := "fast"

default:
    @just --list

# Build Dudley's offline installer ISO.
iso:
    just iso-sd-boot dudley

# Build a live installer container for a configured target.
container target="dudley":
    #!/usr/bin/env bash
    set -euo pipefail

    test -f "{{target}}/payload_ref" || {
        echo "ERROR: {{target}}/payload_ref is missing" >&2
        exit 1
    }

    live_target=$(tr -d '[:space:]' < "{{target}}/live_target")
    live_tag=$(tr -d '[:space:]' < "{{target}}/tag")
    live_registry=$(tr -d '[:space:]' < "{{target}}/registry")

    podman build \
        --cap-add sys_admin \
        --security-opt label=disable \
        --layers \
        --build-arg DEBUG={{debug}} \
        --build-arg INSTALLER_CHANNEL={{installer_channel}} \
        --build-arg TARGET="${live_target}" \
        --build-arg TAG="${live_tag}" \
        --build-arg REGISTRY="${live_registry}" \
        --build-arg CACHE_BUST="$(date +%Y%m%d)" \
        --tag "{{target}}-installer" \
        --file live/Containerfile \
        live

# Build a systemd-boot UEFI ISO. Output: output/dudley-live.iso.
iso-sd-boot target="dudley":
    TARGET={{target}} \
    OUTPUT_DIR={{output_dir}} \
    WORKDIR={{workdir}} \
    DEBUG={{debug}} \
    INSTALLER_CHANNEL={{installer_channel}} \
    COMPRESSION={{compression}} \
    bash scripts/iso-sd-boot.sh

# Static repository validation. Runtime proof additionally requires install E2E.
test:
    pytest tests/ -q
