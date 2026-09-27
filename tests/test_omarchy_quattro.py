"""Contract tests for the isolated Quattro installer build."""

import json
import subprocess
from pathlib import Path

import pytest

from scripts.omarchy_quattro_build import (
    IMAGE_CONFIG,
    OUTPUT_ISO,
    OUTPUT_SIGNATURE,
    OUTPUT_SHA256,
    verify_archive,
    verify_image_config,
    verify_pulled_digest,
    verify_signer_status,
)
from scripts.native_finalize import finalize_native_filesystem


REPO = Path(__file__).parents[1]


def test_image_config_separates_embedded_digest_from_future_tracking_ref() -> None:
    config = json.loads(IMAGE_CONFIG.read_text())
    # The release digest is intentionally a repo-owned post-merge input.
    assert config["accepted_source_ref"] == ""
    assert config["tracking_ref"] == "ghcr.io/joshyorko/omarchy-bootc:testing"
    assert config["accepted_signer_identity"] == (
        "https://github.com/joshyorko/omarchy-bootc/.github/workflows/build.yml@refs/heads/main"
    )
    assert config["accepted_source_ref"] != config["tracking_ref"]


def test_upstream_pin_is_current_live_quattro_revision() -> None:
    assert (REPO / "omarchy-quattro/upstream_url").read_text().strip() == (
        "https://github.com/omacom/omarchy-iso.git"
    )
    assert (REPO / "omarchy-quattro/upstream_revision").read_text().strip() == (
        "86c07785cb0f63be78edb1349843d5817b5c0e66"
    )
    patch = (REPO / "omarchy-quattro/upstream.patch").read_text()
    assert "bin/omarchy-iso-make" in patch
    assert "builder/build-iso.sh" in patch
    assert "OMARCHY_INSTALL_BACKEND" in patch


def test_builder_uses_fixed_output_paths_and_no_external_values() -> None:
    assert OUTPUT_ISO == REPO / "output/omarchy-quattro/omarchy-quattro.iso"
    assert OUTPUT_SHA256 == REPO / "output/omarchy-quattro/omarchy-quattro.iso.sha256"
    assert OUTPUT_SIGNATURE == REPO / "output/omarchy-quattro/omarchy-quattro.image-signature.json"
    result = subprocess.run(
        ["just", "--dry-run", "iso", "omarchy-quattro"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "scripts/build-omarchy-quattro-iso.sh" in result.stderr
    assert "OMARCHY_QUATTRO_IMAGE_REF" not in result.stderr


def test_wrong_or_missing_accepted_digest_is_rejected() -> None:
    with pytest.raises(ValueError, match="digest"):
        verify_image_config(
            {
                "accepted_source_ref": "",
                "tracking_ref": "ghcr.io/joshyorko/omarchy-bootc:testing",
                "accepted_signer_identity": "https://github.com/joshyorko/omarchy-bootc/.github/workflows/build.yml@refs/heads/main",
                "accepted_signer_oidc_issuer": "https://token.actions.githubusercontent.com",
            }
        )


def test_valid_source_digest_and_tracking_tag_remain_separate() -> None:
    source = "ghcr.io/joshyorko/omarchy-bootc@sha256:" + "a" * 64
    tracking = "ghcr.io/joshyorko/omarchy-bootc:testing"
    assert verify_image_config(
        {
            "accepted_source_ref": source,
            "tracking_ref": tracking,
            "accepted_signer_identity": "https://github.com/joshyorko/omarchy-bootc/.github/workflows/build.yml@refs/heads/main",
            "accepted_signer_oidc_issuer": "https://token.actions.githubusercontent.com",
        }
    )[0] == source
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_pulled_digest("sha256:" + "a" * 64, "sha256:" + "b" * 64)


def test_archive_tampering_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "image.oci.tar"
    archive.write_bytes(b"embedded image")
    checksum = tmp_path / "image.oci.tar.sha256"
    checksum.write_text("0" * 64 + "  image.oci.tar\n")
    with pytest.raises(ValueError, match="checksum"):
        verify_archive(archive, checksum)


def test_signer_failure_is_rejected() -> None:
    with pytest.raises(ValueError, match="signer"):
        verify_signer_status(1, "verification failed")


def test_backend_uses_native_composefs_without_ostree_or_bootc_finalize() -> None:
    backend = (REPO / "omarchy-quattro/bootc_backend.py").read_text()
    assert "--composefs-backend" in backend
    assert "--source-imgref" in backend
    assert "--target-imgref" in backend
    assert "--skip-fetch-check" in backend
    assert "SIGNATURE_RECEIPT" in backend
    assert "ostree admin" not in backend.lower()
    assert "install finalize" not in backend
    assert backend.index('("Verifying embedded Quattro image", verify_embedded_image)') < backend.index(
        '("Preparing live environment", prepare_live)'
    )
    assert 'fields[1] == "/"' in backend or 'fields[1] == \'/\'' in backend


def test_native_filesystem_finalization_orders_fstrim_readonly_and_freeze(tmp_path: Path) -> None:
    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    calls = []

    def runner(args, **_kwargs):
        calls.append(args)
        return Result()

    class Context:
        target = tmp_path

    finalize_native_filesystem(Context.target, runner)
    assert [call[0] for call in calls] == ["fstrim", "mount", "fsfreeze", "fsfreeze"]
    assert calls[0][1] == "--quiet-unsupported"
    assert calls[1][1:3] == ["-o", "remount,ro"]
    assert calls[2][1:] == ["--freeze", str(tmp_path)]
    assert calls[3][1:] == ["--unfreeze", str(tmp_path)]


def test_existing_iso_family_recipes_are_unchanged() -> None:
    for family, target in (("dakota", "dudley-dakota"), ("bluefin", "dudley-bluefin")):
        result = subprocess.run(
            ["just", "--dry-run", "iso", family],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        )
        assert f'iso-sd-boot "{target}"' in result.stderr
