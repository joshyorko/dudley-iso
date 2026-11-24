"""Contract tests for the isolated Quattro installer build."""

import json
import hashlib
import importlib.util
import subprocess
from pathlib import Path

import pytest
from scripts import omarchy_quattro_build as builder

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


def test_probe_mounts_work_under_existing_container_run_directory() -> None:
    probe = (REPO / "scripts/probe-quattro-filesystem.sh").read_text()
    assert '-v "$work:/run/quattro-probe:rshared"' in probe
    assert 'oci-archive:/run/quattro-probe/source.oci.tar' in probe
    assert '--skip-finalize /run/quattro-probe/target' in probe
    assert '-v "$work:$work:rshared"' not in probe


@pytest.mark.parametrize("tampered", [False, True])
def test_cached_archive_still_requires_exact_signed_manifest(tmp_path, monkeypatch, tampered):
    cached = tmp_path / "cached.oci.tar"
    cached.write_bytes(b"original compressed archive")
    destination = tmp_path / "embedded.oci.tar"
    raw = '{"config":{"digest":"sha256:' + "b" * 64 + '"}}'
    digest = "0" * 64 if tampered else hashlib.sha256(raw.encode()).hexdigest()
    monkeypatch.setenv("OMARCHY_BOOTC_SOURCE_ARCHIVE", str(cached))

    def inspect(args, *, capture=False):
        assert args == ["skopeo", "inspect", "--raw", f"oci-archive:{destination}"]
        assert capture
        return raw

    monkeypatch.setattr(builder, "_run", inspect)
    if tampered:
        with pytest.raises(ValueError, match="digest mismatch"):
            builder.export_verified_archive(f"{builder.IMAGE_NAME}@sha256:{digest}", destination)
    else:
        builder.export_verified_archive(f"{builder.IMAGE_NAME}@sha256:{digest}", destination)
        assert destination.read_bytes() == cached.read_bytes()


@pytest.mark.parametrize("tampered", [False, True])
def test_archive_cache_miss_retains_only_a_verified_registry_copy(tmp_path, monkeypatch, tampered):
    cached = tmp_path / "cached.oci.tar"
    destination = tmp_path / "embedded.oci.tar"
    raw = '{"config":{"digest":"sha256:' + "b" * 64 + '"}}'
    digest = "0" * 64 if tampered else hashlib.sha256(raw.encode()).hexdigest()
    source = f"{builder.IMAGE_NAME}@sha256:{digest}"
    monkeypatch.setenv("OMARCHY_BOOTC_SOURCE_ARCHIVE", str(cached))

    def skopeo(args, *, capture=False):
        if args[1] == "copy":
            assert args[2] == "--preserve-digests" and args[3] == f"docker://{source}"
            destination.write_bytes(b"original compressed archive")
            return ""
        assert args == ["skopeo", "inspect", "--raw", f"oci-archive:{destination}"]
        return raw

    monkeypatch.setattr(builder, "_run", skopeo)
    if tampered:
        with pytest.raises(ValueError, match="digest mismatch"):
            builder.export_verified_archive(source, destination)
        assert not cached.exists()
    else:
        builder.export_verified_archive(source, destination)
        assert cached.read_bytes() == destination.read_bytes()


CONTRACT_SPEC = importlib.util.spec_from_file_location(
    "quattro_native_contract", REPO / "omarchy-quattro/native_contract.py"
)
assert CONTRACT_SPEC and CONTRACT_SPEC.loader
NATIVE_CONTRACT = importlib.util.module_from_spec(CONTRACT_SPEC)
CONTRACT_SPEC.loader.exec_module(NATIVE_CONTRACT)


def test_native_target_uses_the_deployment_and_bootcs_exact_verity_policy(tmp_path):
    deployment = "a" * 128
    (tmp_path / "state/deploy" / deployment / "etc").mkdir(parents=True)
    entries = tmp_path / "boot/efi/loader/entries"
    entries.mkdir(parents=True)
    entry = entries / "bootc.conf"
    for policy in (deployment, "?" + deployment):
        entry.write_text(f"title bootc\noptions root=UUID=example composefs={policy} rw\n")
        assert NATIVE_CONTRACT.native_composefs_cmdline(tmp_path) == f"composefs={policy}"
    entry.write_text("options composefs=" + "b" * 128 + "\n")
    with pytest.raises(RuntimeError, match="boot entry"):
        NATIVE_CONTRACT.native_composefs_cmdline(tmp_path)


def test_image_config_separates_embedded_digest_from_future_tracking_ref() -> None:
    config = json.loads(IMAGE_CONFIG.read_text())
    # The prototype uses the owner-authorized signed testing payload.
    assert config["accepted_source_ref"] == "ghcr.io/joshyorko/omarchy-bootc@sha256:aca01a25953209b1e38b8aa115fe253283153f159f6f28defaede9c528476386"
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
    assert 'docker.io/archlinux/archlinux:latest' in patch
    assert 'DOCKER_ARGS+=(--network=host)' in patch
    workflow = (REPO / ".github/workflows/quattro-install-e2e.yml").read_text()
    assert "pinned-upstream-unit" in workflow
    assert 'for test in "$upstream"/test/unit/*-test.sh' in workflow
    assert "unittest discover" in workflow
    assert "needs.accepted-image-input.outputs.ready == 'true'" in workflow
    assert "Verify and record build receipts" in workflow
    assert "cd output/omarchy-quattro && sha256sum --check omarchy-quattro.iso.sha256" in workflow
    assert "--install-only --no-preview" in workflow
    assert "--reuse-base --no-preview" in workflow
    assert "Run installed-system desktop acceptance" in workflow
    assert 'sudo env "PATH=$PATH"' in workflow
    assert '"GITHUB_SHA=$GITHUB_SHA"' in workflow
    assert '"$(command -v just)" iso omarchy-quattro' in workflow
    assert "--encrypt" not in workflow
    assert "--provision" not in workflow


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


def test_native_accounts_use_bootcs_composed_root_and_persistent_state() -> None:
    backend = (REPO / "omarchy-quattro/bootc_backend.py").read_text()
    assert "/usr/lib/bootc/initramfs-setup" in backend
    assert "native_composefs_cmdline(physical)" in backend
    assert backend.index("_prepare_native_view(ctx)") < backend.index("installer.create_users(")
    assert 'ctx.state["native_physical_target"]' in backend
    assert "_finish_native_filesystem" in backend


def test_native_contract_rejects_encryption_before_live_preparation() -> None:
    class Context:
        state = {}
        encrypt = False
        user_configuration = {"disk_config": {"disk_encryption": {"encryption_type": "luks"}}}

    with pytest.raises(RuntimeError, match="encrypted Quattro installs are not supported"):
        NATIVE_CONTRACT.validate_native_install_contract(Context())

    backend = (REPO / "omarchy-quattro/bootc_backend.py").read_text()
    assert backend.index('("Checking native installer support", validate_native_install_contract)') < backend.index(
        '("Preparing live environment", prepare_live)'
    )


def test_native_contract_allows_unencrypted_deferred_provisioning() -> None:
    class Context:
        state = {}
        encrypt = False
        defer_provisioning = True
        user_configuration = {"disk_config": {"disk_encryption": {"encryption_type": "no_encryption"}}}

    NATIVE_CONTRACT.validate_native_install_contract(Context())
    backend = (REPO / "omarchy-quattro/bootc_backend.py").read_text()
    assert "_validate_provisioning_state(ctx)" in backend
    assert "if ctx.defer_provisioning:" in backend


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


def test_archive_export_keeps_original_registry_layers(monkeypatch, tmp_path):
    import hashlib
    from scripts import omarchy_quattro_build as builder
    raw = json.dumps({'config': {'digest': 'sha256:' + 'b' * 64}})
    digest = 'sha256:' + hashlib.sha256(raw.encode()).hexdigest()
    ref = 'ghcr.io/joshyorko/omarchy-bootc@' + digest
    archive = tmp_path/'image.oci.tar'
    calls = []
    def transport(args, **kwargs):
        calls.append(args)
        if args[1] == 'copy':
            if args[3].startswith('containers-storage:'):
                raise RuntimeError('changing layer representation: Instructed to preserve digests')
            assert args[2] == '--preserve-digests' and args[3].startswith('docker://ghcr.io/joshyorko/omarchy-bootc@sha256:')
            return ''
        return raw
    monkeypatch.setattr(builder, '_run', transport)
    builder.export_verified_archive(ref, archive)
    assert len(calls) == 2
    assert calls[0][3] == 'docker://' + ref
    assert NATIVE_CONTRACT.verified_archive_config(raw, digest) == 'sha256:' + 'b' * 64
    with pytest.raises(RuntimeError, match='manifest differs'):
        NATIVE_CONTRACT.verified_archive_config(raw + '\n', digest)
    with pytest.raises(ValueError, match='digest mismatch'):
        builder.export_verified_archive(ref.rsplit('@', 1)[0] + '@sha256:' + 'c' * 64, archive)
