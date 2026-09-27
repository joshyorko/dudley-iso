"""The filesystem finalization skipped by native bootc composefs installs."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable


def finalize_native_filesystem(target: Path, runner: Callable = subprocess.run) -> None:
    """Trim, remount read-only, and freeze the physical sysroot where supported."""
    mountpoint = str(target)
    trim = runner(["fstrim", "--quiet-unsupported", mountpoint], check=False, capture_output=True, text=True)
    if trim.returncode and "not supported" not in (trim.stderr + trim.stdout).lower():
        raise RuntimeError(f"could not trim native composefs sysroot: {trim.stderr.strip()}")
    remount = runner(["mount", "-o", "remount,ro", mountpoint], check=False, capture_output=True, text=True)
    if remount.returncode:
        raise RuntimeError(f"could not remount native composefs sysroot read-only: {remount.stderr.strip()}")
    freeze = runner(["fsfreeze", "--freeze", mountpoint], check=False, capture_output=True, text=True)
    if freeze.returncode:
        if "not supported" not in (freeze.stderr + freeze.stdout).lower():
            raise RuntimeError(f"could not freeze native composefs sysroot: {freeze.stderr.strip()}")
        return
    try:
        pass
    finally:
        thaw = runner(["fsfreeze", "--unfreeze", mountpoint], check=False, capture_output=True, text=True)
        if thaw.returncode and "not supported" not in (thaw.stderr + thaw.stdout).lower():
            raise RuntimeError(f"could not unfreeze native composefs sysroot: {thaw.stderr.strip()}")
