"""Contract tests for the Dudley Dakota installer target."""

import json
import subprocess
from pathlib import Path


REPO = Path(__file__).parent.parent


def _read(path: str) -> str:
    return (REPO / path).read_text().strip()


def test_dudley_target_uses_published_dudley_images() -> None:
    assert _read("dudley/payload_ref") == (
        "ghcr.io/joshyorko/dudley-os:dakota-nvidia"
    )
    assert _read("dudley/live_target") == "dudley-os"
    assert _read("dudley/registry") == "joshyorko"
    assert _read("dudley/tag") == "dakota-nvidia"
    assert _read("dudley/live_title") == "Dudley Installer"


def test_dudley_live_configuration_selects_dudley_on_all_hardware() -> None:
    variant = REPO / "live/src/dudley-os"
    assert (variant / "base_imgref").read_text().strip() == (
        "ghcr.io/joshyorko/dudley-os:dakota"
    )
    assert (variant / "nvidia_imgref").read_text().strip() == (
        "ghcr.io/joshyorko/dudley-os:dakota-nvidia"
    )
    assert (variant / "bootloader").read_text().strip() == "systemd"
    assert (variant / "composefs").read_text().strip() == "true"

    images = json.loads((variant / "images.json").read_text())
    assert images["default_image"] == "ghcr.io/joshyorko/dudley-os:dakota"
    assert len(images["images"]) == 1
    assert images["images"][0]["imgref"] == (
        "ghcr.io/joshyorko/dudley-os:dakota"
    )
    assert images["images"][0]["nvidia_imgref"] == (
        "ghcr.io/joshyorko/dudley-os:dakota-nvidia"
    )

    recipe = json.loads((variant / "recipe.json").read_text())
    assert recipe["distro_name"] == "Dudley"
    assert recipe["welcome_title"] == "Welcome to Dudley"


def test_repository_contains_only_the_dudley_product_target() -> None:
    for retired in ("dakota", "bluefin", "bluefin-lts-hwe", "stable", "lts"):
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


def test_one_command_interface_targets_dudley() -> None:
    result = subprocess.run(
        ["just", "--dry-run", "iso"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "iso-sd-boot dudley" in result.stderr
