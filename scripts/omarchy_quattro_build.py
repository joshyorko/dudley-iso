"""Build and verify Dudley's offline Omarchy Quattro installer ISO."""

from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
IMAGE_CONFIG = REPO / "omarchy-quattro/image.json"
OUTPUT_DIR = REPO / "output/omarchy-quattro"
OUTPUT_ISO = OUTPUT_DIR / "omarchy-quattro.iso"
OUTPUT_SHA256 = OUTPUT_DIR / "omarchy-quattro.iso.sha256"
OUTPUT_SIGNATURE = OUTPUT_DIR / "omarchy-quattro.image-signature.json"
UPSTREAM_URL = (REPO / "omarchy-quattro/upstream_url").read_text().strip()
UPSTREAM_REVISION = (REPO / "omarchy-quattro/upstream_revision").read_text().strip()
IMAGE_NAME = "ghcr.io/joshyorko/omarchy-bootc"
ATTESTATION_TYPE = "https://omarchy.org/attestation/published-image-receipt/v1"
PIN_RE = re.compile(rf"^{re.escape(IMAGE_NAME)}@sha256:[0-9a-f]{{64}}$")


def verify_image_config(config: dict[str, str]) -> tuple[str, str, str, str]:
    source = config.get("accepted_source_ref", "").strip()
    tracking = config.get("tracking_ref", "").strip()
    identity = config.get("accepted_signer_identity", "").strip()
    issuer = config.get("accepted_signer_oidc_issuer", "").strip()
    if not PIN_RE.fullmatch(source):
        raise ValueError("accepted source image must be ghcr.io/joshyorko/omarchy-bootc@sha256:<64 hex digest>")
    if tracking != f"{IMAGE_NAME}:testing":
        raise ValueError("tracking ref must be ghcr.io/joshyorko/omarchy-bootc:testing")
    if source == tracking or not identity or not issuer:
        raise ValueError("source digest and future tracking ref, plus signer identity and issuer, are required separately")
    return source, source.rsplit("@", 1)[1], identity, issuer


def verify_archive(archive: Path, checksum_file: Path) -> None:
    fields = checksum_file.read_text().split()
    if len(fields) < 1 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
        raise ValueError("embedded OCI archive checksum is malformed")
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != fields[0]:
        raise ValueError("embedded OCI archive checksum does not match")


def verify_signer_status(returncode: int, detail: str) -> None:
    if returncode:
        raise ValueError(f"image signer verification failed: {detail.strip()}")


def verify_attestation_status(
    returncode: int, detail: str, expected_ref: str, expected_digest: str
) -> list[dict[str, object]]:
    if returncode:
        raise ValueError(f"image attestation verification failed: {detail.strip()}")
    try:
        attestations = json.loads(detail)
    except json.JSONDecodeError as exc:
        raise ValueError("image attestation verification returned malformed JSON") from exc
    if not isinstance(attestations, list) or not attestations:
        raise ValueError("image attestation verification returned no attestation")
    expected_image = expected_ref.rsplit("@", 1)[0]
    expected_digest_ref = expected_digest if expected_digest.startswith("sha256:") else f"sha256:{expected_digest}"
    for entry in attestations:
        if not isinstance(entry, dict) or not isinstance(entry.get("payload"), str):
            continue
        try:
            statement = json.loads(base64.b64decode(entry["payload"], validate=True))
        except (ValueError, json.JSONDecodeError):
            continue
        predicate = statement.get("predicate") if isinstance(statement, dict) else None
        if (
            isinstance(predicate, dict)
            and predicate.get("schema") == "omarchy-bootc.published-image/v1"
            and predicate.get("image") == expected_image
            and predicate.get("oci_manifest_digest") == expected_digest_ref
            and predicate.get("acceptance_overlay") == "not-applied"
        ):
            return attestations
    raise ValueError("image attestation does not prove the accepted published artifact")


def verify_pulled_digest(expected_digest: str, actual_digest: str) -> None:
    if actual_digest.strip() != expected_digest.strip():
        raise ValueError(f"pulled image digest mismatch: expected {expected_digest}, got {actual_digest}")


def _run(args: list[str], *, cwd: Path | None = None, capture: bool = False) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args)} {detail}")
    return result.stdout or ""


def build() -> tuple[Path, Path]:
    config = json.loads(IMAGE_CONFIG.read_text())
    source_ref, digest, identity, issuer = verify_image_config(config)
    for tool in ("git", "podman", "cosign", "sha256sum"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} is required")

    # Authentication and content identity are checked before creating output
    # or performing the destructive destination-disk operations in the ISO.
    signature = subprocess.run(
        ["cosign", "verify", "--output", "json", "--certificate-identity", identity,
         "--certificate-oidc-issuer", issuer, source_ref],
        text=True, capture_output=True, check=False,
    )
    verify_signer_status(signature.returncode, signature.stderr or signature.stdout)
    try:
        verified_signatures = json.loads(signature.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("image signer verification returned malformed JSON") from exc
    if not isinstance(verified_signatures, list) or not verified_signatures:
        raise ValueError("image signer verification returned no signature")
    attestation = subprocess.run(
        [
            "cosign", "verify-attestation", "--output", "json",
            "--type", ATTESTATION_TYPE,
            "--certificate-identity", identity,
            "--certificate-oidc-issuer", issuer,
            source_ref,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    verified_attestations = verify_attestation_status(
        attestation.returncode,
        attestation.stdout or attestation.stderr,
        source_ref,
        digest,
    )
    _run(["podman", "pull", source_ref])
    actual_digest = _run(
        ["podman", "image", "inspect", source_ref, "--format", "{{.Digest}}"], capture=True
    ).strip()
    verify_pulled_digest(digest, actual_digest)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="build.", dir=OUTPUT_DIR) as temporary:
        temporary_dir = Path(temporary)
        source_dir = temporary_dir / "omarchy-iso"
        _run(["git", "clone", "--filter=blob:none", "--branch", "quattro", "--single-branch",
              "--no-checkout", UPSTREAM_URL, str(source_dir)])
        _run(["git", "checkout", "--detach", UPSTREAM_REVISION], cwd=source_dir)
        _run(["git", "submodule", "update", "--init", "--recursive", "--jobs=8"], cwd=source_dir)
        _run(["git", "apply", "--unidiff-zero", str(REPO / "omarchy-quattro/upstream.patch")], cwd=source_dir)
        overlay = source_dir / "configs/airootfs/usr/share/omarchy-iso/orchestrator/bootc_backend.py"
        shutil.copy2(REPO / "omarchy-quattro/bootc_backend.py", overlay)
        shutil.copy2(
            REPO / "scripts/native_finalize.py",
            overlay.with_name("native_finalize.py"),
        )
        shutil.copy2(
            REPO / "omarchy-quattro/native_contract.py",
            overlay.with_name("native_contract.py"),
        )
        _run(["git", "diff", "--check"], cwd=source_dir)

        archive = temporary_dir / "omarchy-quattro-image.oci.tar"
        tagged = "localhost/omarchy-quattro:embedded"
        _run(["podman", "tag", source_ref, tagged])
        _run(["podman", "save", "--format", "oci-archive", "--output", str(archive), tagged])
        checksum_file = temporary_dir / "omarchy-quattro-image.oci.tar.sha256"
        checksum_file.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")
        verify_archive(archive, checksum_file)
        signature_receipt = temporary_dir / "omarchy-quattro-image.signature.json"
        signature_receipt.write_text(json.dumps({
            "schema": "omarchy-bootc.image-signature/v1",
            "source_ref": source_ref,
            "source_digest": digest,
            "certificate_identity": identity,
            "certificate_oidc_issuer": issuer,
            "verified_signatures": verified_signatures,
            "verified_attestations": verified_attestations,
        }, indent=2, sort_keys=True) + "\n")

        env = os.environ.copy()
        env.update({
            "OMARCHY_CONTAINER_RUNTIME": "podman",
            "OMARCHY_INSTALL_BACKEND": "bootc",
            "OMARCHY_BOOTC_IMAGE_REF": source_ref,
            "OMARCHY_BOOTC_TARGET_IMGREF": config["tracking_ref"],
            "OMARCHY_BOOTC_IMAGE_ARCHIVE": str(archive),
            "OMARCHY_BOOTC_IMAGE_SIGNATURE_RECEIPT": str(signature_receipt),
            "OMARCHY_ISO_REF": "quattro",
            "OMARCHY_MIRROR": "stable",
        })
        # Apply only the internal runtime config to the child process.
        subprocess.run(["./bin/omarchy-iso-make", "--keep-pkg-cache", "--no-boot-offer"],
                       cwd=source_dir, env=env, check=True)
        candidates = sorted((source_dir / "release").glob("*.iso"), key=lambda p: p.stat().st_mtime_ns)
        if not candidates:
            raise RuntimeError("upstream builder produced no ISO")
        staged_iso = temporary_dir / OUTPUT_ISO.name
        staged_checksum = temporary_dir / OUTPUT_SHA256.name
        shutil.copy2(candidates[-1], staged_iso)
        checksum = hashlib.sha256(staged_iso.read_bytes()).hexdigest()
        staged_checksum.write_text(f"{checksum}  {OUTPUT_ISO.name}\n")
        staged_signature = temporary_dir / OUTPUT_SIGNATURE.name
        shutil.copy2(signature_receipt, staged_signature)
        os.replace(staged_iso, OUTPUT_ISO)
        os.replace(staged_checksum, OUTPUT_SHA256)
        os.replace(staged_signature, OUTPUT_SIGNATURE)
    return OUTPUT_ISO, OUTPUT_SHA256


def main() -> int:
    try:
        iso, checksum = build()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(iso)
    print(checksum)
    print(OUTPUT_SIGNATURE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
