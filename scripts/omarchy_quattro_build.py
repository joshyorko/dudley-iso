"""Build and verify Dudley's offline Omarchy Quattro installer ISO."""

from __future__ import annotations

import hashlib
import base64
import argparse
import ast
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


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_archive(archive: Path, checksum_file: Path) -> None:
    fields = checksum_file.read_text().split()
    if len(fields) < 1 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
        raise ValueError("embedded OCI archive checksum is malformed")
    actual = file_sha256(archive)
    if actual != fields[0]:
        raise ValueError("embedded OCI archive checksum does not match")


def verify_signer_status(returncode: int, detail: str) -> None:
    if returncode:
        raise ValueError(f"image signer verification failed: {detail.strip()}")


def verify_attestation_status(
    returncode: int, detail: str, expected_ref: str, expected_digest: str, expected_source_sha: str
) -> list[dict[str, object]]:
    if returncode:
        raise ValueError(f"image attestation verification failed: {detail.strip()}")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_source_sha):
        raise ValueError("accepted image source SHA must be exact")
    try:
        # Cosign emits individual verified DSSE envelopes, not necessarily an
        # array. Parse the complete stream so trailing garbage fails closed.
        decoder = json.JSONDecoder()
        remaining = detail.strip()
        attestations = []
        while remaining:
            value, end = decoder.raw_decode(remaining)
            entries = value if isinstance(value, list) else [value]
            if not all(isinstance(entry, dict) for entry in entries):
                raise ValueError("image attestation contains a non-envelope value")
            attestations.extend(entries)
            remaining = remaining[end:].lstrip()
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
            and predicate.get("source_sha") == expected_source_sha
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


def prepare_upstream_adapter(source_dir: Path) -> None:
    """Apply only the deployment seam to the exact official source, without images."""
    if _run(["git", "rev-parse", "HEAD"], cwd=source_dir, capture=True).strip() != UPSTREAM_REVISION:
        raise ValueError("upstream checkout does not match the reviewed installer revision")
    _run(["git", "diff", "--exit-code", "HEAD"], cwd=source_dir, capture=True)
    surfaces = ["bin/omarchy-iso-configurator", "configs/airootfs/root/configurator",
                "configs/airootfs/usr/local/bin/omarchy-install-dashboard"]
    originals = {name: (source_dir / name).read_bytes() for name in surfaces}
    _run(["git", "apply", "--unidiff-zero", str(REPO / "omarchy-quattro/upstream.patch")], cwd=source_dir)
    directory = source_dir / "configs/airootfs/usr/share/omarchy-iso/orchestrator"
    for source, name in [(REPO / "omarchy-quattro/bootc_backend.py", "bootc_backend.py"),
                         (REPO / "scripts/native_finalize.py", "native_finalize.py"),
                         (REPO / "omarchy-quattro/native_contract.py", "native_contract.py")]:
        shutil.copy2(source, directory / name)
        ast.parse((directory / name).read_text())
    ast.parse((directory / "main.py").read_text())
    changed = set(_run(["git", "diff", "--name-only"], cwd=source_dir, capture=True).splitlines())
    allowed = {"bin/omarchy-iso-make", "builder/build-iso.sh", "configs/airootfs/root/.automated_script.sh",
               "configs/airootfs/usr/share/omarchy-iso/orchestrator/main.py"}
    if changed - allowed or any((source_dir / name).read_bytes() != data for name, data in originals.items()):
        raise ValueError("adapter modified an official UI surface outside the deployment seam")
    _run(["git", "diff", "--check"], cwd=source_dir)


def build() -> tuple[Path, Path]:
    config = json.loads(IMAGE_CONFIG.read_text())
    source_ref, digest, identity, issuer = verify_image_config(config)
    source_sha = config.get("accepted_source_sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("accepted image source SHA must be exact")
    for tool in ("git", "podman", "skopeo", "cosign", "sha256sum"):
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
        source_sha,
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
        prepare_upstream_adapter(source_dir)

        archive = temporary_dir / "omarchy-quattro-image.oci.tar"
        tagged = "localhost/omarchy-quattro:embedded"
        _run(["podman", "tag", source_ref, tagged])
        _run(["skopeo", "copy", "--preserve-digests", f"containers-storage:{tagged}", f"oci-archive:{archive}:{tagged}"])
        raw_manifest = _run(["skopeo", "inspect", "--raw", f"oci-archive:{archive}"], capture=True)
        verify_pulled_digest(digest, "sha256:" + hashlib.sha256(raw_manifest.encode()).hexdigest())
        checksum_file = temporary_dir / "omarchy-quattro-image.oci.tar.sha256"
        checksum_file.write_text(file_sha256(archive) + "  " + archive.name + "\n")
        verify_archive(archive, checksum_file)
        signature_receipt = temporary_dir / "omarchy-quattro-image.signature.json"
        signature_receipt.write_text(json.dumps({
            "schema": "omarchy-bootc.image-signature/v1",
            "source_ref": source_ref,
            "source_digest": digest,
            "source_sha": source_sha,
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
        checksum = file_sha256(staged_iso)
        staged_checksum.write_text(f"{checksum}  {OUTPUT_ISO.name}\n")
        staged_signature = temporary_dir / OUTPUT_SIGNATURE.name
        shutil.copy2(signature_receipt, staged_signature)
        os.replace(staged_iso, OUTPUT_ISO)
        os.replace(staged_checksum, OUTPUT_SHA256)
        os.replace(staged_signature, OUTPUT_SIGNATURE)
        (OUTPUT_DIR / "omarchy-quattro.iso.receipt.json").write_text(json.dumps({
            "schema": "dudley.quattro-prototype-iso/v1",
            "builder_source_sha": os.environ.get("GITHUB_SHA", _run(["git", "rev-parse", "HEAD"], capture=True).strip()),
            "upstream_revision": UPSTREAM_REVISION, "payload_source_sha": source_sha,
            "image_ref": source_ref, "tracking_ref": config["tracking_ref"],
            "iso_sha256": checksum, "run_id": os.environ.get("GITHUB_RUN_ID"),
            "qualification": "prototype only; actual UI/install/desktop and encryption gates pending",
        }, indent=2) + "\n")
    return OUTPUT_ISO, OUTPUT_SHA256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", type=Path, help="Validate/apply adapter to an existing pinned source checkout; no OCI or ISO operations")
    args = parser.parse_args()
    try:
        if args.prepare_only is not None:
            prepare_upstream_adapter(args.prepare_only)
            print("Prepared exact official installer source; configurator/dashboard unchanged; no guest acceptance claimed")
            return 0
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
