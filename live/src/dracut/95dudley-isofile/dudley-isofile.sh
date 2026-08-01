#!/bin/sh
# Locate a Dudley ISO file on Ventoy/USB partitions and expose its volume label.

LABEL="DUDLEY_LIVE"
RUN_DIR="/run/initramfs/dudley-isofile"
FOUND_MARKER="${RUN_DIR}/found"

[ -e "/dev/disk/by-label/${LABEL}" ] && return 0
[ -e "${FOUND_MARKER}" ] && return 0

mkdir -p "${RUN_DIR}" /dev/disk/by-label

debug() {
    echo "dudley-isofile: $*" > /dev/kmsg 2>/dev/null || echo "dudley-isofile: $*"
}

iso_hint=""
# shellcheck disable=SC2013  # Kernel command line is intentionally word-split.
for arg in $(cat /proc/cmdline); do
    case "$arg" in
        rd.dudley.isofile=*|findiso=*|iso-scan/filename=*|rd.live.iso=*)
            iso_hint="${arg#*=}"
            ;;
    esac
done

try_iso() {
    iso="$1"
    [ -f "$iso" ] || return 1

    loopdev=$(losetup --find --show --read-only "$iso" 2>/dev/null) || return 1
    iso_label=$(blkid -s LABEL -o value "$loopdev" 2>/dev/null || true)

    if [ "$iso_label" = "$LABEL" ]; then
        ln -sf "$loopdev" "/dev/disk/by-label/${LABEL}"
        : > "${FOUND_MARKER}"
        udevadm settle 2>/dev/null || true
        debug "using ISO file $iso via $loopdev"
        /sbin/initqueue --settled --onetime --unique /sbin/dmsquash-live-root "$loopdev"
        return 0
    fi

    losetup -d "$loopdev" 2>/dev/null || true
    return 1
}

scan_mount() {
    dev="$1"
    base=$(basename "$dev")
    mp="${RUN_DIR}/mnt-${base}"
    mkdir -p "$mp"

    mount -o ro -t auto "$dev" "$mp" 2>/dev/null || return 1

    if [ -n "$iso_hint" ]; then
        case "$iso_hint" in
            /*) try_iso "${mp}${iso_hint}" && return 0 ;;
            *) try_iso "${mp}/${iso_hint}" && return 0 ;;
        esac
    fi

    for iso in \
        "$mp"/dudley*.iso \
        "$mp"/Dudley*.iso \
        "$mp"/*.iso \
        "$mp"/ISO/*.iso \
        "$mp"/iso/*.iso \
        "$mp"/*/dudley*.iso \
        "$mp"/*/*.iso
    do
        [ -e "$iso" ] || continue
        try_iso "$iso" && return 0
    done

    umount "$mp" 2>/dev/null || true
    return 1
}

for dev in /dev/disk/by-label/* /dev/disk/by-id/*-part* /dev/sd*[0-9] /dev/vd*[0-9] /dev/nvme*n*p* /dev/mmcblk*p*; do
    [ -b "$dev" ] || continue
    [ -e "/dev/disk/by-label/${LABEL}" ] && return 0
    scan_mount "$dev" && return 0
done

return 1
