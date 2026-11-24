"""Fail-closed checks for the native composefs installer contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import hashlib
import json
import re
from pathlib import Path


def verified_archive_config(raw_manifest: str, expected_digest: str) -> str:
    """Bind runtime config identity to the unchanged accepted OCI manifest."""
    if "sha256:" + hashlib.sha256(raw_manifest.encode()).hexdigest() != expected_digest:
        raise RuntimeError("embedded archive manifest differs from accepted digest")
    config = json.loads(raw_manifest).get("config", {}).get("digest", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", config):
        raise RuntimeError("embedded archive manifest has no valid runtime config")
    return config


def _has_encryption(value: Any) -> bool:
    """Recognize encrypted Archinstall disk settings without depending on its types."""
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"encryption_type", "encryption", "disk_encryption"}:
                item_value = getattr(item, "value", item)
                if isinstance(item_value, str) and item_value.lower() not in {"", "none", "no_encryption", "false"}:
                    return True
                if isinstance(item_value, bool) and item_value:
                    return True
                if isinstance(item, Mapping) and item and _has_encryption(item):
                    return True
            if _has_encryption(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_has_encryption(item) for item in value)
    elif hasattr(value, "__dict__"):
        return _has_encryption(vars(value))
    return False


def validate_native_install_contract(ctx: Any) -> None:
    """Reject encryption before any live preparation or destination-disk mutation.

    Native Quattro currently has no verified native boot unlock integration. The
    upstream deferred-provisioning encryption hooks are Limine/mkinitcpio-specific,
    so encrypted installs must remain unavailable until a native replacement is
    implemented and tested.
    """
    state = getattr(ctx, "state", None) or {}
    handler = state.get("arch_config_handler") if isinstance(state, Mapping) else None
    arch_config = getattr(handler, "config", None)
    user_config = getattr(ctx, "user_configuration", None)
    direct_disk_config = getattr(ctx, "disk_config", None)
    if (
        getattr(ctx, "encrypt", False)
        or _has_encryption(user_config)
        or _has_encryption(getattr(ctx, "config", None))
        or _has_encryption(direct_disk_config)
        or _has_encryption(getattr(ctx, "disk_encryption", None))
        or _has_encryption(arch_config)
    ):
        raise RuntimeError(
            "encrypted Quattro installs are not supported by the native composefs adapter; "
            "disable disk encryption before continuing"
        )
def native_composefs_cmdline(sysroot: Path) -> str:
    """Select the initial native deployment without changing bootc's verity policy."""
    deployments = [path.name for path in (sysroot / "state/deploy").iterdir()
                   if path.is_dir() and re.fullmatch(r"[a-f0-9]{128}", path.name)]
    if len(deployments) != 1:
        raise RuntimeError("expected exactly one initial native composefs deployment")
    options = set()
    for entry in (sysroot / "boot").glob("**/loader/entries/*.conf"):
        for line in entry.read_text().splitlines():
            if line.startswith("options "):
                options.update(token for token in line.split()[1:] if token.startswith("composefs="))
    expected = {"composefs=" + deployments[0], "composefs=?" + deployments[0]}
    if len(options) != 1 or not options.issubset(expected):
        raise RuntimeError("native boot entry does not identify the installed deployment")
    return options.pop()
