"""Fail-closed checks for the native composefs installer contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


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
