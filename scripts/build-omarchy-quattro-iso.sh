#!/usr/bin/env bash
set -euo pipefail

# Build the pinned upstream Omarchy ISO with the Quattro bootc backend overlay.
# This deliberately lives beside, rather than inside, the existing Dudley ISO
# builder so the two installer families cannot share mutable build state.

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-${ROOT_DIR}/output/omarchy-quattro}"
UPSTREAM_URL="$(tr -d '[:space:]' < "${ROOT_DIR}/omarchy-quattro/upstream_url")"
UPSTREAM_REVISION="$(tr -d '[:space:]' < "${ROOT_DIR}/omarchy-quattro/upstream_revision")"
IMAGE_REF="${OMARCHY_QUATTRO_IMAGE_REF:-}"
EMBEDDED_IMAGE_REF="localhost/omarchy-quattro:embedded"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

[[ -n "${IMAGE_REF}" ]] || die \
    "OMARCHY_QUATTRO_IMAGE_REF must name the signed Quattro image"
[[ "${IMAGE_REF}" == *@sha256:* ]] || die \
    "OMARCHY_QUATTRO_IMAGE_REF must be pinned by digest (name@sha256:...)"

for tool in git podman sha256sum; do
    command -v "${tool}" >/dev/null 2>&1 || die "${tool} is required"
done

mkdir -p "${OUTPUT_DIR}"
OUTPUT_DIR="$(realpath "${OUTPUT_DIR}")"
SOURCE_DIR="$(mktemp -d "${OUTPUT_DIR}/upstream.XXXXXX")"

cleanup() {
    local status=$?
    if (( status != 0 )); then
        echo "Upstream source retained for diagnosis: ${SOURCE_DIR}" >&2
    fi
    exit "${status}"
}
trap cleanup EXIT

git clone --filter=blob:none --branch quattro --single-branch --no-checkout \
    "${UPSTREAM_URL}" "${SOURCE_DIR}"
git -C "${SOURCE_DIR}" checkout --detach "${UPSTREAM_REVISION}"
git -C "${SOURCE_DIR}" submodule update --init --recursive --jobs=8

git -C "${SOURCE_DIR}" apply --unidiff-zero \
    "${ROOT_DIR}/omarchy-quattro/upstream.patch"
install -D -m 0644 \
    "${ROOT_DIR}/omarchy-quattro/bootc_backend.py" \
    "${SOURCE_DIR}/configs/airootfs/usr/share/omarchy-iso/orchestrator/bootc_backend.py"
git -C "${SOURCE_DIR}" diff --check

if ! podman image inspect "${IMAGE_REF}" >/dev/null 2>&1; then
    podman pull "${IMAGE_REF}"
fi

actual_digest="$(podman image inspect "${IMAGE_REF}" --format '{{.Digest}}')"
expected_digest="${IMAGE_REF##*@}"
[[ "${actual_digest}" == "${expected_digest}" ]] || die \
    "local image digest ${actual_digest} does not match ${expected_digest}"

IMAGE_ARCHIVE="${OUTPUT_DIR}/omarchy-quattro-image.oci.tar"
[[ ! -e "${IMAGE_ARCHIVE}" ]] || die \
    "refusing to overwrite existing ${IMAGE_ARCHIVE}"
podman tag "${IMAGE_REF}" "${EMBEDDED_IMAGE_REF}"
podman save --format oci-archive --output "${IMAGE_ARCHIVE}" "${EMBEDDED_IMAGE_REF}"
archive_sha256="$(sha256sum "${IMAGE_ARCHIVE}" | awk '{print $1}')"

(
    cd "${SOURCE_DIR}"
    OMARCHY_CONTAINER_RUNTIME="${OMARCHY_CONTAINER_RUNTIME:-podman}" \
        OMARCHY_INSTALL_BACKEND=bootc \
        OMARCHY_BOOTC_IMAGE_REF="${IMAGE_REF}" \
        OMARCHY_BOOTC_TARGET_IMGREF="${IMAGE_REF}" \
        OMARCHY_BOOTC_IMAGE_ARCHIVE="${IMAGE_ARCHIVE}" \
        OMARCHY_ISO_REF=quattro \
        OMARCHY_MIRROR=stable \
        ./bin/omarchy-iso-make --keep-pkg-cache --no-boot-offer
)

latest_iso="$(find "${SOURCE_DIR}/release" -maxdepth 1 -type f -name '*.iso' \
    -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)"
[[ -n "${latest_iso}" && -f "${latest_iso}" ]] || die \
    "upstream builder produced no ISO"

FINAL_ISO="${OUTPUT_DIR}/omarchy-quattro.iso"
[[ ! -e "${FINAL_ISO}" ]] || die \
    "refusing to overwrite existing ${FINAL_ISO}"
cp -- "${latest_iso}" "${FINAL_ISO}"
printf '%s\n' "${IMAGE_REF}" > "${OUTPUT_DIR}/omarchy-quattro-image.ref"
printf '%s\n' "${archive_sha256}" > "${OUTPUT_DIR}/omarchy-quattro-image.oci.tar.sha256"

echo "Omarchy Quattro ISO ready: ${FINAL_ISO}"
echo "Embedded image: ${IMAGE_REF}"
