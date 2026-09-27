"""Native composefs deployment backend for the upstream Omarchy installer.

`ctx.target` is the physical sysroot mounted by archinstall. Native composefs
does not expose an OSTree deployment directory: mutable install-time state is
written below this sysroot's /etc and /var, while /usr is image-owned. The
backend never searches for an OSTree deployment and never calls an OSTree-only
bootc finalizer.
"""

from __future__ import annotations

import base64
import os
import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import archinstall_adapter as arch
from .context import InstallContext
from .native_finalize import finalize_native_filesystem as _finalize_native_filesystem
from .native_contract import validate_native_install_contract
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


EMBEDDED_IMAGE_REF = "localhost/omarchy-quattro:embedded"
EMBEDDED_ARCHIVE = Path(os.environ.get(
    "OMARCHY_BOOTC_IMAGE_ARCHIVE", "/var/cache/omarchy/bootc/quattro.oci.tar"
))
TRACKING_REF = os.environ.get("OMARCHY_BOOTC_TARGET_IMGREF", "").strip()
IMAGE_REF_FILE = Path("/root/omarchy_bootc_image_ref")
TRACKING_REF_FILE = Path("/root/omarchy_bootc_target_imgref")
ARCHIVE_CHECKSUM = EMBEDDED_ARCHIVE.with_suffix(EMBEDDED_ARCHIVE.suffix + ".sha256")
SIGNATURE_RECEIPT = EMBEDDED_ARCHIVE.with_suffix(EMBEDDED_ARCHIVE.suffix + ".signature.json")


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
    actual_archive_sha = hashlib.sha256(EMBEDDED_ARCHIVE.read_bytes()).hexdigest()
    if not receipt or receipt[0] != actual_archive_sha:
        raise RuntimeError("embedded Quattro OCI archive checksum mismatch")
    load = subprocess.run(
        ["podman", "load", "--input", str(EMBEDDED_ARCHIVE)],
        check=False, capture_output=True, text=True,
    )
    if load.returncode:
        raise RuntimeError("could not load the embedded Quattro image archive")
    inspect = subprocess.run(
        ["podman", "image", "inspect", EMBEDDED_IMAGE_REF, "--format", "{{.Digest}}"],
        check=False, capture_output=True, text=True,
    )
    if inspect.returncode or inspect.stdout.strip() != match.group(1):
        raise RuntimeError("loaded embedded image digest does not match the accepted source digest")
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
    command = [
        "podman", "run", "--rm", "--privileged", "--pid=host", "--ipc=host",
        "--userns=host", "--network=host", "--security-opt", "label=disable",
        "--volume", "/dev:/dev", "--volume", "/run:/run", "--volume", "/sys:/sys:ro",
        "--volume", "/var/lib/containers:/var/lib/containers",
        "--volume", f"{ctx.target}:{ctx.target}",
        "--volume", f"{EMBEDDED_ARCHIVE}:/run/omarchy-bootc/quattro.oci.tar:ro",
        EMBEDDED_IMAGE_REF,
        *args,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        if os.environ.get("OMARCHY_INSTALL_DEBUG") == "1":
            ctx.log_path.parent.mkdir(parents=True, exist_ok=True)
            with ctx.log_path.open("a", encoding="utf-8") as log:
                log.write(result.stdout + result.stderr)
        raise RuntimeError("native composefs image deployment failed")
    return result


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
    handler = ctx.state["arch_config_handler"]
    config = handler.config
    pre_mounted = arch.is_pre_mount(config)

    if not pre_mounted:
        info("› partitioning + formatting + encrypting")
        arch.perform_filesystem_operations(config)

    with arch.open_installer(config, ctx.target, silent=True) as installer:
        if not pre_mounted:
            installer.mount_ordered_layout()
        installer.sanity_check(offline=True, skip_ntp=True, skip_wkd=True)
        root = _sysroot_path(ctx)
        if not root.is_dir():
            raise RuntimeError(f"physical install sysroot is not mounted: {root}")

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
            str(ctx.target),
        )

        # The installer owns only machine-local config in the physical sysroot.
        # /usr is immutable image content and must never be overlaid by adapter
        # writes. Validate the paths before handing control to upstream writers.
        for mutable_dir in ("etc", "var"):
            _sysroot_path(ctx, mutable_dir).mkdir(parents=True, exist_ok=True)
        marker = _sysroot_path(ctx, "var", "lib", "omarchy-bootc", "installer-origin")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("upstream-omarchy-iso\n")
        marker.chmod(0o644)

        if not ctx.defer_provisioning and config.auth_config and config.auth_config.users:
            installer.create_users(config.auth_config.users)
        if config.swap and config.swap.enabled:
            installer.setup_swap(algo=config.swap.algorithm)
        if config.timezone:
            installer.set_timezone(config.timezone)
        if config.ntp:
            installer.activate_time_synchronization()
        if root_user := arch.root_user(config):
            installer.set_user_password(root_user)

        if pre_mounted:
            _write_pre_mounted_fstab(ctx)
        else:
            installer.genfstab()
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


def _run_official_system_finalizer(ctx: InstallContext) -> None:
    """Run Omarchy's official system setup without mutating immutable /usr hooks."""
    if ctx.defer_provisioning:
        command = ["/usr/bin/omarchy-apply-system", "--defer-provisioning", "--first-install"]
    else:
        command = ["/usr/bin/omarchy-apply-system", "--install-user", ctx.username, "--first-install"]
    _run_target_setup_command(ctx, command)


def build_phases(ctx: InstallContext):
    """Use upstream UX and configurator with a native composefs system backend."""
    return [
        ("Verifying embedded Quattro image", verify_embedded_image),
        ("Checking native installer support", validate_native_install_contract),
        ("Preparing live environment", prepare_live),
        ("Preparing install target", prepare_install_target),
        ("Installing Arch + Omarchy", _install_system),
        ("Staging provisioning", stage_provisioning_state),
        ("Configuring system", _run_official_system_finalizer),
        ("Finalizing user", run_chroot_finalizer),
        ("Configuring login", configure_login),
        ("Configuring SSH access", configure_ssh_access),
        ("Configuring Tailscale", configure_tailscale),
        ("Configuring DNS resolver", configure_dns_resolver),
        ("Validating boot setup", _validate_boot),
        ("Finalizing native filesystem", lambda context: _finalize_native_filesystem(context.target)),
    ]
