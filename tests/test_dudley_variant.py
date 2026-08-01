"""Contract tests for Dudley's Dakota and Bluefin installer families."""

import json
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).parent.parent


def _read(path: str) -> str:
    return (REPO / path).read_text().strip()


@pytest.mark.parametrize(
    ("target", "tag", "payload_ref", "installer_variant"),
    [
        (
            "dudley-dakota",
            "dakota-nvidia",
            "ghcr.io/joshyorko/dudley-os:dakota-nvidia",
            "dudley-dakota",
        ),
        (
            "dudley-bluefin",
            "nvidia",
            "ghcr.io/joshyorko/dudley-os:nvidia",
            "dudley-bluefin",
        ),
    ],
)
def test_iso_family_targets_use_published_dudley_images(
    target: str, tag: str, payload_ref: str, installer_variant: str
) -> None:
    assert _read(f"{target}/payload_ref") == payload_ref
    assert _read(f"{target}/live_target") == "dudley-os"
    assert _read(f"{target}/registry") == "joshyorko"
    assert _read(f"{target}/tag") == tag
    assert _read(f"{target}/installer_variant") == installer_variant
    assert _read(f"{target}/live_title") == "Dudley Installer"


@pytest.mark.parametrize(
    ("variant_name", "base_ref", "nvidia_ref", "bootloader", "composefs"),
    [
        (
            "dudley-dakota",
            "ghcr.io/joshyorko/dudley-os:dakota",
            "ghcr.io/joshyorko/dudley-os:dakota-nvidia",
            "systemd",
            True,
        ),
        (
            "dudley-bluefin",
            "ghcr.io/joshyorko/dudley-os:stable",
            "ghcr.io/joshyorko/dudley-os:nvidia",
            "grub",
            False,
        ),
    ],
)
def test_live_configuration_selects_dudley_on_all_hardware(
    variant_name: str,
    base_ref: str,
    nvidia_ref: str,
    bootloader: str,
    composefs: bool,
) -> None:
    variant = REPO / "live/src" / variant_name
    assert (variant / "base_imgref").read_text().strip() == base_ref
    assert (variant / "nvidia_imgref").read_text().strip() == nvidia_ref
    assert (variant / "bootloader").read_text().strip() == bootloader
    assert (variant / "composefs").read_text().strip() == str(composefs).lower()

    images = json.loads((variant / "images.json").read_text())
    assert images["default_image"] == base_ref
    assert len(images["images"]) == 1
    assert images["images"][0]["imgref"] == base_ref
    assert images["images"][0]["nvidia_imgref"] == nvidia_ref
    assert images["images"][0]["bootloader"] == (
        "grub2" if bootloader == "grub" else bootloader
    )
    assert images["images"][0]["composefs"] is composefs

    recipe = json.loads((variant / "recipe.json").read_text())
    assert recipe["distro_name"] == "Dudley"
    assert recipe["welcome_title"] == "Welcome to Dudley"


def test_repository_contains_only_dudley_product_targets() -> None:
    for retired in ("dudley", "dakota", "bluefin", "bluefin-lts-hwe", "stable", "lts"):
        assert not (REPO / retired).exists()

    workflows = sorted(path.name for path in (REPO / ".github/workflows").glob("*.yml"))
    assert workflows == ["validate.yml"]


def test_generated_media_uses_dudley_identity() -> None:
    build_iso = (REPO / "live/src/build-iso.sh").read_text()
    configure_live = (REPO / "live/src/configure-live.sh").read_text()
    assert "DUDLEY_LIVE" in build_iso
    assert "DAKOTA_LIVE" not in build_iso
    assert "DUDLEY_LIVE_READY" in configure_live
    assert "DAKOTA_LIVE_READY" not in configure_live
    assert (REPO / "live/src/dracut/95dudley-isofile/dudley-isofile.sh").is_file()


@pytest.mark.parametrize(
    ("args", "target"),
    [
        (["iso"], "dudley-dakota"),
        (["iso", "dakota"], "dudley-dakota"),
        (["iso", "bluefin"], "dudley-bluefin"),
    ],
)
def test_one_command_interface_selects_iso_family(
    args: list[str], target: str
) -> None:
    result = subprocess.run(
        ["just", "--dry-run", *args],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    assert f'iso-sd-boot "{target}"' in result.stderr


def test_one_command_interface_rejects_unknown_iso_family() -> None:
    result = subprocess.run(
        ["just", "iso", "silverblue"],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "supported ISO families: dakota, bluefin" in result.stderr


@pytest.mark.parametrize("recipe", ["container", "iso-sd-boot"])
def test_lower_level_recipes_default_to_dakota_target(recipe: str) -> None:
    result = subprocess.run(
        ["just", "--dry-run", recipe],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "dudley-dakota" in result.stderr
