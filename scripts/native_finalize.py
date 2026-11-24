"""The filesystem finalization skipped by native bootc composefs installs."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable


def finalize_native_filesystem(target: Path, runner: Callable = subprocess.run) -> None:
    """Follow bootc's native finalization for root and separately mounted boot filesystems."""
    filesystems = [target]
    filesystems.extend(path for path in (target / "boot", target / "boot/efi") if path.is_mount())
    for filesystem in filesystems:
        _finalize_filesystem(filesystem, runner)


def _finalize_filesystem(target: Path, runner: Callable) -> None:
    mountpoint = str(target)
    filesystem = runner(
        ["stat", "-f", "--format=%t", "--", mountpoint],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if not filesystem or len(filesystem.splitlines()) != 1:
        raise RuntimeError(f"could not identify mounted native filesystem: {mountpoint}")
    trim = runner(["fstrim", "--quiet-unsupported", mountpoint], check=False, capture_output=True, text=True)
    if trim.returncode:
        raise RuntimeError(f"could not trim native composefs sysroot: {trim.stderr.strip()}")
    remount = runner(["mount", "-o", "remount,ro", mountpoint], check=False, capture_output=True, text=True)
    if remount.returncode:
        raise RuntimeError(f"could not remount native composefs sysroot read-only: {remount.stderr.strip()}")
    if filesystem == "4d44":  # Linux MSDOS_SUPER_MAGIC, also used by VFAT.
        return
    freeze = runner(["fsfreeze", "--freeze", mountpoint], check=False, capture_output=True, text=True)
    if freeze.returncode:
        raise RuntimeError(f"could not freeze native composefs sysroot: {freeze.stderr.strip()}")
    try:
        pass
    finally:
        thaw = runner(["fsfreeze", "--unfreeze", mountpoint], check=False, capture_output=True, text=True)
        if thaw.returncode:
            raise RuntimeError(f"could not unfreeze native composefs sysroot: {thaw.stderr.strip()}")
