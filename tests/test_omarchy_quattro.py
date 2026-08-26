"""Static contract for the additive upstream Omarchy Quattro installer."""

import subprocess
from pathlib import Path


REPO = Path(__file__).parents[1]
QUATTRO = REPO / "omarchy-quattro"


def _read(name: str) -> str:
    return (QUATTRO / name).read_text().strip()


def test_quattro_is_pinned_to_the_reviewed_upstream_installer() -> None:
    assert _read("upstream_url") == "https://github.com/omacom-io/omarchy-iso.git"
    assert _read("upstream_revision") == (
        "268bac16d351a21d867e37565738f458b11cb06c"
    )
    assert (QUATTRO / "bootc_backend.py").is_file()
    assert (QUATTRO / "upstream.patch").is_file()


def test_backend_replaces_only_the_external_deployment_boundary() -> None:
    backend = (QUATTRO / "bootc_backend.py").read_text()

    for required in (
        "bootc install to-filesystem",
        "ostree admin --sysroot=/mnt --print-current-dir",
        "bootc install finalize",
        "omarchy-provision-user",
        "--source-imgref",
        "--target-imgref",
        "--skip-finalize",
    ):
        assert required in backend

    for forbidden in (
        "omarchy-apply-system",
        "limine-update",
        "mkinitcpio",
        "snapper factory",
    ):
        assert forbidden not in backend


def test_upstream_patch_selects_the_backend_only_for_quattro() -> None:
    patch = (QUATTRO / "upstream.patch").read_text()
    builder = (REPO / "scripts/build-omarchy-quattro-iso.sh").read_text()
    patch_surface = patch + builder

    for required in (
        "bin/omarchy-iso-make",
        "builder/build-iso.sh",
        "configs/airootfs/root/.automated_script.sh",
        "configs/airootfs/usr/share/omarchy-iso/orchestrator/main.py",
        "OMARCHY_INSTALL_BACKEND",
        "OMARCHY_CONTAINER_RUNTIME",
        "bootc_backend.py",
        "podman",
    ):
        assert required in patch_surface


def test_quattro_command_routes_to_a_separate_builder() -> None:
    result = subprocess.run(
        ["just", "--dry-run", "iso", "omarchy-quattro"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )

    command = result.stderr
    assert "scripts/build-omarchy-quattro-iso.sh" in command
    assert "iso-sd-boot \"omarchy-quattro\"" not in command


def test_existing_iso_families_still_route_to_their_existing_builder() -> None:
    for family, target in (("dakota", "dudley-dakota"), ("bluefin", "dudley-bluefin")):
        result = subprocess.run(
            ["just", "--dry-run", "iso", family],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        )
        assert f'iso-sd-boot "{target}"' in result.stderr


def test_builder_requires_an_immutable_quattro_image_and_preserves_artifacts() -> None:
    builder = (REPO / "scripts/build-omarchy-quattro-iso.sh").read_text()

    assert "@sha256:" in builder
    assert "output/omarchy-quattro" in builder
    assert "upstream_revision" in builder
    assert "upstream.patch" in builder
    assert "--no-boot-offer" in builder
    assert "--keep-pkg-cache" in builder
    assert "podman tag" in builder


def test_quattro_backend_finishes_after_machine_configuration() -> None:
    backend = (QUATTRO / "bootc_backend.py").read_text()
    assert backend.index('(\"Finalizing boot\", finalize_boot)') > backend.index(
        '(\"Configuring DNS resolver\", configure_dns_resolver)'
    )
    assert 'marker.write_text("upstream-omarchy-iso\\n")' in backend
    assert "bootc-pacman.conf" in backend
