"""Native composefs deployment backend for the upstream Omarchy installer.

Archinstall mounts the physical sysroot. Bootc's own initramfs setup then
exposes its composed root and persistent /etc and /var for upstream setup.
The backend never searches for an OSTree deployment or calls its finalizer.
"""

from __future__ import annotations

import base64
import os
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from . import archinstall_adapter as arch
from .context import InstallContext
from .native_finalize import finalize_native_filesystem as _finalize_native_filesystem
from .native_contract import native_composefs_cmdline, validate_native_install_contract, verified_archive_tar_config
from .phases_impl import (
    configure_dns_resolver,
    configure_login,
    configure_ssh_access,
    configure_tailscale,
    prepare_install_target,
    prepare_live,
    run_chroot_finalizer,
    _run_target_setup_command,
    stage_provisioning_state,
    _write_pre_mounted_fstab,
    _validate_provisioning_state,
)
from .ui import info


EMBEDDED_ARCHIVE = Path(os.environ.get(
    "OMARCHY_BOOTC_IMAGE_ARCHIVE", "/var/cache/omarchy/bootc/quattro.oci.tar"
))
TRACKING_REF = os.environ.get("OMARCHY_BOOTC_TARGET_IMGREF", "").strip()
IMAGE_REF_FILE = Path("/root/omarchy_bootc_image_ref")
TRACKING_REF_FILE = Path("/root/omarchy_bootc_target_imgref")
ARCHIVE_CHECKSUM = EMBEDDED_ARCHIVE.with_suffix(EMBEDDED_ARCHIVE.suffix + ".sha256")
SIGNATURE_RECEIPT = EMBEDDED_ARCHIVE.with_suffix(EMBEDDED_ARCHIVE.suffix + ".signature.json")
CONTAINER_TARGET = Path("/run/omarchy-bootc/target")
RUNTIME_DISK = Path("/var/lib/omarchy-installer-disk")


def verify_embedded_image(ctx: InstallContext) -> None:
    """Verify archive integrity and exact accepted digest before disk mutation."""
    if not EMBEDDED_ARCHIVE.is_file() or not ARCHIVE_CHECKSUM.is_file() or not SIGNATURE_RECEIPT.is_file():
        raise RuntimeError("embedded Quattro image archive, checksum, or signature receipt is missing")
    source_ref = IMAGE_REF_FILE.read_text().strip()
    match = re.fullmatch(r"ghcr\.io/joshyorko/omarchy-bootc@(sha256:[0-9a-f]{64})", source_ref)
    if not match:
        raise RuntimeError("embedded source reference is not pinned to an accepted image digest")
    if TRACKING_REF_FILE.read_text().strip() != TRACKING_REF:
        raise RuntimeError("embedded tracking ref does not match the separately configured target ref")
    if TRACKING_REF != "ghcr.io/joshyorko/omarchy-bootc:testing":
        raise RuntimeError("installed OS tracking ref must be ghcr.io/joshyorko/omarchy-bootc:testing")
    try:
        signature = json.loads(SIGNATURE_RECEIPT.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("embedded image signature receipt is malformed") from exc
    if (
        not isinstance(signature, dict)
        or
        signature.get("schema") != "omarchy-bootc.image-signature/v1"
        or signature.get("source_ref") != source_ref
        or signature.get("source_digest") != match.group(1)
        or signature.get("certificate_identity") != "https://github.com/joshyorko/omarchy-bootc/.github/workflows/build.yml@refs/heads/main"
        or signature.get("certificate_oidc_issuer") != "https://token.actions.githubusercontent.com"
        or not isinstance(signature.get("verified_signatures"), list)
        or not signature["verified_signatures"]
    ):
        raise RuntimeError("embedded image signature receipt does not prove the accepted publisher")
    attestations = signature.get("verified_attestations")
    attestation_ok = False
    if isinstance(attestations, list):
        for entry in attestations:
            if not isinstance(entry, dict) or not isinstance(entry.get("payload"), str):
                continue
            try:
                statement = json.loads(base64.b64decode(entry["payload"], validate=True))
            except (ValueError, json.JSONDecodeError):
                continue
            predicate = statement.get("predicate") if isinstance(statement, dict) else None
            if (
                isinstance(predicate, dict)
                and predicate.get("schema") == "omarchy-bootc.published-image/v1"
                and predicate.get("image") == source_ref.rsplit("@", 1)[0]
                and predicate.get("oci_manifest_digest") == match.group(1)
                and predicate.get("acceptance_overlay") == "not-applied"
            ):
                attestation_ok = True
                break
    if not attestation_ok:
        raise RuntimeError("embedded image signature receipt does not prove the accepted attestation")
    receipt = ARCHIVE_CHECKSUM.read_text().split()
    with EMBEDDED_ARCHIVE.open("rb") as stream:
        actual_archive_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    if not receipt or receipt[0] != actual_archive_sha:
        raise RuntimeError("embedded Quattro OCI archive checksum mismatch")
    ctx.state["omarchy_bootc_runtime_id"] = verified_archive_tar_config(EMBEDDED_ARCHIVE, match.group(1))
    ctx.state["omarchy_bootc_archive_verified"] = True


def _load_embedded_runtime(ctx: InstallContext) -> None:
    if not ctx.state.get("omarchy_bootc_archive_verified"):
        raise RuntimeError("embedded archive was not verified before target preparation")
    source = subprocess.run(
        ["findmnt", "--noheadings", "--raw", "--nofsroot", "--output", "SOURCE,FSTYPE",
         "--mountpoint", str(ctx.target)],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    if len(source) != 2 or source[1] != "btrfs" or not source[0].startswith("/dev/"):
        raise RuntimeError("native installer runtime requires the selected Btrfs root device")
    if RUNTIME_DISK.is_symlink() or RUNTIME_DISK.is_mount():
        raise RuntimeError("a previous installer runtime disk is still mounted")
    RUNTIME_DISK.mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run(["mount", "-t", "btrfs", "-o", "subvolid=5", source[0], str(RUNTIME_DISK)], check=True)
    subprocess.run(["mount", "--make-private", str(RUNTIME_DISK)], check=True)
    # Keep storage on the selected disk, but outside bootc's empty deployment root.
    store = RUNTIME_DISK / ".omarchy-installer-storage"
    if store.exists() or store.is_symlink():
        raise RuntimeError("selected disk already contains an installer runtime store")
    store.mkdir(mode=0o700)
    temporary = store / "tmp"
    temporary.mkdir(mode=0o700)
    runroot = Path("/run/omarchy-bootc/podman")
    runroot.mkdir(parents=True, exist_ok=True)
    command = ["env", f"TMPDIR={temporary}", "podman", "--root", str(store),
               "--runroot", str(runroot), "--storage-driver=overlay"]
    ctx.state["omarchy_bootc_runtime_command"] = command
    ctx.state["omarchy_bootc_runtime_store"] = store
    load = subprocess.run(
        [*command, "load", "--input", str(EMBEDDED_ARCHIVE)],
        check=False, capture_output=True, text=True,
    )
    if load.returncode:
        ctx.log_path.parent.mkdir(parents=True, exist_ok=True)
        with ctx.log_path.open("a", encoding="utf-8") as log:
            log.write(load.stdout + load.stderr)
        raise RuntimeError("could not load the embedded Quattro image archive")
    runtime_id = ctx.state["omarchy_bootc_runtime_id"]
    inspect = subprocess.run(
        [*command, "image", "inspect", runtime_id, "--format", "{{.Id}}"],
        check=False, capture_output=True, text=True,
    )
    if inspect.returncode or "sha256:" + inspect.stdout.strip().removeprefix("sha256:") != runtime_id:
        raise RuntimeError("loaded runtime config does not match accepted archive manifest")
    scratch = store / "bootc-tmp"
    scratch.mkdir(mode=0o700)
    subprocess.run(["mount", "--bind", str(scratch), "/var/tmp"], check=True)
    ctx.state["omarchy_bootc_image_loaded"] = True


def _sysroot_path(ctx: InstallContext, *parts: str) -> Path:
    """Resolve an installer-owned mutable path and reject symlink escapes."""
    root = ctx.target.resolve()
    path = root.joinpath(*parts)
    if not path.resolve(strict=False).is_relative_to(root):
        raise RuntimeError(f"installer path escapes physical sysroot: {path}")
    return path


def _run_embedded(ctx: InstallContext, *args: str) -> subprocess.CompletedProcess[str]:
    if not ctx.state.get("omarchy_bootc_image_loaded"):
        raise RuntimeError("embedded Quattro image was not verified before installation")
    if not TRACKING_REF or "@sha256:" in TRACKING_REF:
        raise RuntimeError("future tracking ref must be supplied separately from the accepted source digest")
    runtime_dir = Path("/run/omarchy-bootc")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    CONTAINER_TARGET.mkdir(parents=True, exist_ok=True)
    command = [
        *ctx.state["omarchy_bootc_runtime_command"], "run", "--rm", "--privileged", "--pid=host", "--ipc=host",
        "--userns=host", "--network=host", "--security-opt", "label=disable",
        "--volume", "/dev:/dev", "--volume", "/run:/run", "--volume", "/sys:/sys:ro",
        "--volume", "/var/lib/containers:/var/lib/containers",
        "--volume", "/var/tmp:/var/tmp",
        "--volume", f"{runtime_dir}:{runtime_dir}:rshared",
        "--volume", f"{ctx.target}:{CONTAINER_TARGET}:rshared",
        "--volume", f"{EMBEDDED_ARCHIVE}:/run/omarchy-bootc/quattro.oci.tar:ro",
        ctx.state["omarchy_bootc_runtime_id"],
        *args,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        ctx.log_path.parent.mkdir(parents=True, exist_ok=True)
        with ctx.log_path.open("a", encoding="utf-8") as log:
            log.write(result.stdout + result.stderr)
        evidence = _sysroot_path(ctx, ".omarchy-installer-evidence")
        evidence.mkdir(mode=0o700, exist_ok=True)
        (evidence / "native-deployment.log").write_text(result.stdout + result.stderr)
        raise RuntimeError("native composefs image deployment failed")
    return result


def _prepare_native_view(ctx: InstallContext) -> None:
    physical = ctx.target
    cmdline = native_composefs_cmdline(physical)
    view = Path("/run/omarchy-bootc/native-root")
    view.mkdir(parents=True, exist_ok=True)
    if view.is_mount():
        raise RuntimeError("a previous native installer root is still mounted")
    _run_embedded(ctx, "/usr/lib/bootc/initramfs-setup", "--sysroot", str(CONTAINER_TARGET),
                  "--target", str(view), "--cmdline", cmdline)
    try:
        deployment = cmdline.removeprefix("composefs=").removeprefix("?")
        if not (view / "usr/bin/bash").is_file() or not (view / "etc").samefile(
            physical / "state/deploy" / deployment / "etc"
        ):
            raise RuntimeError("native root does not expose the installed userspace and persistent configuration")
        # Preserve the official storage layout across the composed root view.
        for source, destination in (("boot", "boot"), ("home", "var/home"),
                                    ("var/log", "var/log"), ("var/cache/pacman/pkg", "var/cache/pacman/pkg")):
            if (physical / source).is_mount() or source == "boot":
                (view / destination).mkdir(parents=True, exist_ok=True)
                subprocess.run(["mount", "--rbind", str(physical / source), str(view / destination)], check=True)
    except BaseException:
        subprocess.run(["umount", "--recursive", str(view)], check=False)
        raise
    ctx.state["native_physical_target"] = physical
    ctx.target = view
    ctx.target_log_path = view / "var/log/omarchy-install.log"


def cleanup_native_view(ctx: InstallContext) -> None:
    physical = ctx.state.get("native_physical_target")
    if physical is None:
        return
    if ctx.target.is_mount():
        subprocess.run(["umount", "--recursive", str(ctx.target)], check=True)
    ctx.target = physical
    ctx.target_log_path = physical / "var/log/omarchy-install.log"
    del ctx.state["native_physical_target"]


def _finish_native_filesystem(ctx: InstallContext) -> None:
    cleanup_native_view(ctx)
    subprocess.run([*ctx.state["omarchy_bootc_runtime_command"], "image", "rm", "--force",
                    ctx.state["omarchy_bootc_runtime_id"]], check=True, capture_output=True, text=True)
    subprocess.run(["umount", "/var/tmp"], check=True)
    shutil.rmtree(ctx.state["omarchy_bootc_runtime_store"])
    subprocess.run(["umount", str(RUNTIME_DISK)], check=True)
    ctx.state["omarchy_bootc_image_loaded"] = False
    _finalize_native_filesystem(ctx.target)


def _sanitize_fstab(ctx: InstallContext) -> None:
    """Keep non-root mounts and discard a root remount entry unsupported by composefs."""
    fstab = _sysroot_path(ctx, "etc", "fstab")
    if not fstab.exists():
        raise RuntimeError(f"installer did not create {fstab}")
    kept = []
    for line in fstab.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "/":
            continue
        kept.append(line)
    fstab.write_text("\n".join(kept).rstrip() + "\n")


def _install_system(ctx: InstallContext) -> None:
    if not ctx.state.get("omarchy_bootc_archive_verified"):
        raise RuntimeError("embedded archive was not verified before disk mutation")
    handler = ctx.state["arch_config_handler"]
    config = handler.config
    pre_mounted = arch.is_pre_mount(config)

    if not pre_mounted:
        info("› partitioning + formatting + encrypting")
        arch.perform_filesystem_operations(config)

    with arch.open_installer(config, ctx.target, silent=True) as installer:
        if not pre_mounted:
            installer.mount_ordered_layout()
        installer.sanity_check(skip_ntp=True, skip_wkd=True)
        root = _sysroot_path(ctx)
        if not root.is_mount():
            raise RuntimeError(f"physical install sysroot is not mounted: {root}")

        _load_embedded_runtime(ctx)

        info("› deploying the pinned offline Quattro image with native composefs")
        _run_embedded(
            ctx,
            "bootc", "install", "to-filesystem",
            "--source-imgref", "oci-archive:/run/omarchy-bootc/quattro.oci.tar",
            "--target-imgref", TRACKING_REF,
            "--bootloader", "systemd",
            "--composefs-backend",
            "--skip-fetch-check",
            "--skip-finalize",
            str(CONTAINER_TARGET),
        )

        _configure_native_target(ctx, config, pre_mounted)


def _configure_native_target(ctx: InstallContext, config: arch.ArchConfig, pre_mounted: bool) -> None:
    """Configure the deployed composed root without partitioning or importing again."""
    fstab = subprocess.run(["genfstab", "-U", str(ctx.target)], check=True,
                           capture_output=True, text=True).stdout
    _prepare_native_view(ctx)

    # The installer owns only machine-local config in the physical sysroot.
    # /usr is immutable image content and must never be overlaid by adapter
    # writes. Validate the paths before handing control to upstream writers.
    for mutable_dir in ("etc", "var"):
        _sysroot_path(ctx, mutable_dir).mkdir(parents=True, exist_ok=True)
    marker = _sysroot_path(ctx, "var", "lib", "omarchy-bootc", "installer-origin")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("upstream-omarchy-iso\n")
    marker.chmod(0o644)

    with arch.open_installer(config, ctx.target, silent=True) as installer:
        if not ctx.defer_provisioning and config.auth_config and config.auth_config.users:
            installer.create_users(config.auth_config.users)
        if config.swap and config.swap.enabled:
            generator = _sysroot_path(ctx, "usr/lib/systemd/system-generators/zram-generator")
            if not os.access(generator, os.X_OK):
                raise RuntimeError("zram swap requires zram-generator in the signed image")
            swap_config = _sysroot_path(ctx, "etc/systemd/zram-generator.conf")
            swap_config.parent.mkdir(parents=True, exist_ok=True)
            swap_config.write_text(
                "[zram0]\n" + f"compression-algorithm = {config.swap.algorithm.value}\n"
            )
            installer.enable_service("systemd-zram-setup@zram0.service")
        if config.timezone:
            installer.set_timezone(config.timezone)
        if config.ntp:
            installer.activate_time_synchronization()
        if root_user := arch.root_user(config):
            installer.set_user_password(root_user)

    if pre_mounted:
        _write_pre_mounted_fstab(ctx)
    else:
        _sysroot_path(ctx, "etc", "fstab").write_text(fstab)
    _sanitize_fstab(ctx)


def _validate_boot(ctx: InstallContext) -> None:
    if ctx.defer_provisioning:
        # Reuse the pinned upstream contract so a missing service, pending
        # marker, or owner provisioning payload cannot be reported as success.
        _validate_provisioning_state(ctx)
    boot = _sysroot_path(ctx, "boot")
    if not boot.is_dir():
        raise RuntimeError(f"bootc did not create a boot directory under {ctx.target}")
    # bootc owns the bootloader and composefs boot metadata. Omarchy's Limine
    # validation and factory snapshot phases are intentionally not run here.


def _configure_native_system(ctx: InstallContext) -> None:
    """Apply only audited machine-local setup; packages and boot stay image-owned."""
    leaves = (
        "config/browser-policy.sh",
        "config/increase-lockout-limit.sh",
        "config/ssh-command-path.sh",
        "config/ssh-keepalive.sh",
        "config/enable-services.sh",
        "login/sddm.sh",
    )
    script = r'''set -euo pipefail
export OMARCHY_FIRST_INSTALL=1 OMARCHY_UPGRADE=0
source "$OMARCHY_INSTALL/helpers/logging.sh"
start_install_log
current=$(sed -n 's/^LANG=//p' /etc/locale.conf 2>/dev/null | tail -1 | tr -d '"') || current=""
case ${current:-C} in C|POSIX) current=C.UTF-8 ;; esac
available=$(locale -a | tr '[:upper:]' '[:lower:]' | tr -d '._-')
normalized=$(printf '%s' "$current" | tr '[:upper:]' '[:lower:]' | tr -d '._-')
grep -Fxq "$normalized" <<<"$available" || { echo "Locale must be supplied by the signed image: $current" >&2; exit 1; }
if grep -q '^LANG=' /etc/locale.conf 2>/dev/null; then
    sed -i "s/^LANG=.*/LANG=$current/" /etc/locale.conf
else
    printf 'LANG=%s\n' "$current" >>/etc/locale.conf
fi
export LANG="$current"
''' + "\n".join(f'run_logged "$OMARCHY_INSTALL/{leaf}"' for leaf in leaves) + r'''
# The upstream lock helper also changes image-owned fingerprint hooks. Keep
# its password PAM policy, without invoking those hardware/package operations.
cat >/etc/pam.d/omarchy-lock-password <<'PAM'
#%PAM-1.0
auth       required                    pam_faillock.so preauth silent deny=10 unlock_time=120
-auth      [success=2 default=ignore]  pam_systemd_home.so
auth       [success=1 default=bad]     pam_unix.so try_first_pass nullok
auth       [default=die]               pam_faillock.so authfail deny=10 unlock_time=120
auth       optional                    pam_permit.so
auth       required                    pam_env.so
auth       required                    pam_faillock.so authsucc
account    include                     system-local-login
PAM
chmod 0644 /etc/pam.d/omarchy-lock-password
for unit in limine-snapper-sync.service snapper-cleanup.timer snapper-timeline.timer; do
    [[ $(systemctl is-enabled "$unit" 2>/dev/null || true) == masked ]]
done
grep -Fxq ENABLED=yes /etc/ufw/ufw.conf
stop_install_log
'''
    _run_target_setup_command(ctx, ["/usr/bin/bash", "-euc", script])
    receipt = _sysroot_path(ctx, "var/lib/omarchy-bootc/native-system-setup.json")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({
        "schema": "omarchy-bootc.native-system-setup/v1",
        "upstream_mutable_leaves": leaves,
        "package_and_hardware_setup": "supplied by the signed image",
        "boot_ownership": "bootc-composefs",
    }, indent=2) + "\n")


def build_phases(ctx: InstallContext):
    """Use upstream UX and configurator with a native composefs system backend."""
    return [
        ("Verifying embedded Quattro image", verify_embedded_image),
        ("Checking native installer support", validate_native_install_contract),
        ("Preparing live environment", prepare_live),
        ("Preparing install target", prepare_install_target),
        ("Installing Arch + Omarchy", _install_system),
        ("Staging provisioning", stage_provisioning_state),
        ("Configuring system", _configure_native_system),
        ("Finalizing user", run_chroot_finalizer),
        ("Configuring login", configure_login),
        ("Configuring SSH access", configure_ssh_access),
        ("Configuring Tailscale", configure_tailscale),
        ("Configuring DNS resolver", configure_dns_resolver),
        ("Validating boot setup", _validate_boot),
        ("Finalizing native filesystem", _finish_native_filesystem),
    ]
