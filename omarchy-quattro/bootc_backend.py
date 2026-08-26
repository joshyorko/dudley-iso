"""Bootc deployment backend for the pinned Omarchy ISO orchestrator.

This module is copied into an exact upstream ``omarchy-iso`` checkout by the
host builder.  The upstream configurator, partitioning, credentials, user
finalizer, login setup, autoinstall, and acceptance harness remain the source
of truth.  This module owns only the external operating-system deployment
boundary.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import archinstall_adapter as arch
from .context import InstallContext
from .phases_impl import (
    PROVISION_STATE_DIR,
    _provision_encryption_password,
    _provision_install_encrypted,
    _run_target_setup_command,
    _stage_node_tarball,
    _write_pre_mounted_fstab,
    configure_dns_resolver,
    configure_login,
    configure_ssh_access,
    configure_tailscale,
    prepare_install_target,
    prepare_live,
)
from .ui import info


# These strings are intentionally kept as named contract surfaces.  The
# upstream parity contract and the repository tests must be able to identify
# the external-installer seam without interpreting Python argument assembly.
BOOTC_INSTALL_COMMAND = "bootc install to-filesystem /mnt"
OSTREE_CURRENT_DIR_COMMAND = "ostree admin --sysroot=/mnt --print-current-dir"
BOOTC_FINALIZE_COMMAND = "bootc install finalize /mnt"

EMBEDDED_IMAGE_REF = "localhost/omarchy-quattro:embedded"
EMBEDDED_ARCHIVE = Path(
    os.environ.get(
        "OMARCHY_BOOTC_IMAGE_ARCHIVE",
        "/var/cache/omarchy/bootc/quattro.oci.tar",
    )
)


def _target_image_ref() -> str:
    """Return the immutable image reference used by future bootc updates."""
    image_ref = (
        os.environ.get("OMARCHY_BOOTC_TARGET_IMGREF")
        or os.environ.get("OMARCHY_BOOTC_IMAGE_REF")
        or ""
    ).strip()
    if "@sha256:" not in image_ref:
        raise RuntimeError("the Omarchy system image must be pinned by digest")
    return image_ref


def _podman_command(ctx: InstallContext, executable: str, *args: str) -> list[str]:
    """Run a tool from the embedded OCI while exposing the prepared target."""
    if not EMBEDDED_ARCHIVE.is_file():
        raise RuntimeError(f"embedded Omarchy system image is missing: {EMBEDDED_ARCHIVE}")

    target = ctx.target
    return [
        "podman",
        "run",
        "--rm",
        "--privileged",
        "--pid=host",
        "--userns=host",
        "--network=host",
        "--security-opt",
        "label=disable",
        "--volume",
        "/dev:/dev",
        "--volume",
        "/run:/run",
        "--volume",
        "/sys:/sys:ro",
        "--volume",
        f"{target}:{target}",
        "--volume",
        f"{EMBEDDED_ARCHIVE}:/run/omarchy-bootc/quattro.oci.tar:ro",
        "--entrypoint",
        f"/usr/{'sbin' if executable == 'ostree' else 'bin'}/{executable}",
        EMBEDDED_IMAGE_REF,
        *args,
    ]


def _load_embedded_image(ctx: InstallContext) -> None:
    if ctx.state.get("omarchy_bootc_image_loaded"):
        return

    result = subprocess.run(
        ["podman", "load", "--input", str(EMBEDDED_ARCHIVE)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("could not load the embedded Omarchy system image")
    ctx.state["omarchy_bootc_image_loaded"] = True


def _run_embedded(ctx: InstallContext, executable: str, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        _podman_command(ctx, executable, *args),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if os.environ.get("OMARCHY_INSTALL_DEBUG") == "1":
            ctx.log_path.parent.mkdir(parents=True, exist_ok=True)
            with ctx.log_path.open("a", encoding="utf-8") as log:
                log.write(result.stdout)
                log.write(result.stderr)
        raise RuntimeError("the system image deployment step failed")
    return result


def _deployment_dir(ctx: InstallContext) -> Path:
    """Locate the active deployment before any machine-specific edits."""
    result = _run_embedded(
        ctx,
        "ostree",
        "admin",
        "--sysroot=/mnt",
        "--print-current-dir",
    )
    raw = result.stdout.strip().splitlines()
    if not raw:
        raise RuntimeError("the installed system image has no active deployment")

    reported = Path(raw[-1].strip())
    candidates = [
        ctx.target / reported.relative_to("/")
        if reported.is_absolute()
        else ctx.target / reported,
    ]
    if str(reported).startswith("/mnt/"):
        candidates.insert(0, ctx.target / str(reported)[len("/mnt/") :])

    for candidate in candidates:
        if candidate.is_dir():
            ctx.state["omarchy_bootc_deployment_dir"] = str(candidate)
            return candidate
    raise RuntimeError(f"active system deployment is not under {ctx.target}: {reported}")


def _deployment_etc(ctx: InstallContext) -> Path:
    deployment = Path(ctx.state.get("omarchy_bootc_deployment_dir", ""))
    if not deployment.is_dir():
        raise RuntimeError("active system deployment was not located before configuration")
    etc = deployment / "etc"
    if not etc.is_dir():
        raise RuntimeError(f"active system deployment has no etc directory: {etc}")
    return etc


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
        _load_embedded_image(ctx)

        # The only OS deployment operation in this backend.  The source is the
        # embedded archive; the immutable digest is retained for future pulls.
        _run_embedded(
            ctx,
            "bootc",
            "install",
            "to-filesystem",
            "--source-imgref",
            "oci-archive:/run/omarchy-bootc/quattro.oci.tar",
            "--target-transport",
            "registry",
            "--target-imgref",
            _target_image_ref(),
            "--bootloader",
            "systemd",
            "--composefs-backend",
            "--skip-fetch-check",
            "--skip-finalize",
            str(ctx.target),
        )
        _deployment_dir(ctx)
        _deployment_etc(ctx)
        _mark_iso_install(ctx)

        # The image owns packages and /etc/skel.  Reuse archinstall's user and
        # filesystem writers only after the deployment exists; no pacstrap or
        # package transaction runs in this variant.
        if not ctx.defer_provisioning:
            if config.auth_config and config.auth_config.users:
                installer.create_users(config.auth_config.users)
        if config.swap and config.swap.enabled:
            installer.setup_swap(algo=config.swap.algorithm)
        if config.timezone:
            installer.set_timezone(config.timezone)
        if config.ntp:
            installer.activate_time_synchronization()
        if root := arch.root_user(config):
            installer.set_user_password(root)

        if pre_mounted:
            _write_pre_mounted_fstab(ctx)
        else:
            installer.genfstab()


def configure_hibernation(_ctx: InstallContext) -> None:
    """Keep the upstream phase slot; the image owns its boot configuration."""


def _mark_iso_install(ctx: InstallContext) -> None:
    """Mark an ISO deployment so the target skips cross-distro adoption."""
    marker = ctx.target / "var/lib/omarchy-bootc/installer-origin"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("upstream-omarchy-iso\n")
    marker.chmod(0o644)


def configure_system(_ctx: InstallContext) -> None:
    """The published image is already system-configured by its image build."""


def stage_provisioning_state(ctx: InstallContext) -> None:
    """Retain upstream deferred provisioning without mutable-root setup."""
    provisioning_dir = ctx.target / PROVISION_STATE_DIR
    provisioning_dir.mkdir(parents=True, exist_ok=True)
    provisioning_dir.chmod(0o755)
    _stage_node_tarball(ctx, provisioning_dir)

    if not ctx.defer_provisioning:
        return

    service_src = ctx.target / "usr/share/omarchy/install/provisioning/omarchy-provision-owner.service"
    setup_bin = ctx.target / "usr/bin/omarchy-provision-owner"
    if not service_src.exists() or not setup_bin.exists():
        raise RuntimeError("deferred provisioning is not available in the embedded Omarchy image")

    (provisioning_dir / "pending").touch()
    unit_dst = ctx.target / "etc/systemd/system/omarchy-provision-owner.service"
    unit_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(service_src, unit_dst)
    wants_dir = ctx.target / "etc/systemd/system/multi-user.target.wants"
    wants_dir.mkdir(parents=True, exist_ok=True)
    link = wants_dir / "omarchy-provision-owner.service"
    link.unlink(missing_ok=True)
    link.symlink_to("/etc/systemd/system/omarchy-provision-owner.service")

    if _provision_install_encrypted(ctx):
        password = _provision_encryption_password(ctx)
        if not password:
            raise RuntimeError("encrypted deferred provisioning has no LUKS passphrase")
        key = provisioning_dir / "luks-key"
        key.write_text(password)
        key.chmod(0o600)
        keyfile = _deployment_etc(ctx) / "omarchy/provisioning.key"
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        keyfile.write_text(password)
        keyfile.chmod(0o600)


def finalize_boot(ctx: InstallContext) -> None:
    """Perform bootc's external-installer handoff after target configuration."""
    _deployment_etc(ctx)
    _run_embedded(ctx, "bootc", "install", "finalize", str(ctx.target))


def finalize_user(ctx: InstallContext) -> None:
    if ctx.defer_provisioning:
        info("› deferred-provisioning install: user finalization deferred to first boot")
        return
    pacman_conf = ctx.target / "etc/pacman.conf"
    preserved_pacman_conf = ctx.state_dir / "bootc-pacman.conf"
    if pacman_conf.exists():
        shutil.copy2(pacman_conf, preserved_pacman_conf)
    try:
        _run_target_setup_command(
            ctx,
            ["/usr/bin/omarchy-provision-user", "--first-install"],
            user=ctx.username,
        )
    finally:
        if preserved_pacman_conf.exists():
            shutil.copy2(preserved_pacman_conf, pacman_conf)


def validate_boot(ctx: InstallContext) -> None:
    """Validate the handed-off system without taking ownership of its boot path."""
    _deployment_etc(ctx)
    if not (ctx.target / "boot").is_dir():
        raise RuntimeError(f"installed system has no boot filesystem at {ctx.target / 'boot'}")
    if not (ctx.target / "etc/fstab").exists():
        raise RuntimeError(f"installed system has no filesystem table at {ctx.target / 'etc/fstab'}")


def create_factory_snapshot(_ctx: InstallContext) -> None:
    """Leave rollback/factory state to the bootc deployment manager."""


def build_phases(ctx: InstallContext) -> list[tuple[str, object]]:
    """Keep the upstream dashboard sequence while swapping one backend."""
    return [
        ("Preparing live environment", prepare_live),
        ("Preparing install target", prepare_install_target),
        ("Installing Arch + Omarchy", _install_system),
        ("Configuring hibernation", configure_hibernation),
        ("Configuring system", configure_system),
        ("Staging provisioning", stage_provisioning_state),
        ("Finalizing user", finalize_user),
        ("Configuring login", configure_login),
        ("Configuring SSH access", configure_ssh_access),
        ("Configuring Tailscale", configure_tailscale),
        ("Configuring DNS resolver", configure_dns_resolver),
        ("Finalizing boot", finalize_boot),
        ("Validating boot setup", validate_boot),
        ("Creating factory snapshot", create_factory_snapshot),
    ]
