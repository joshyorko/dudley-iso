# Genuine Quattro installer readiness

The Bluefin DX/Titanoboa ISO from Omarchy run45070729201 is withdrawn from
hands-on qualification. It is not the genuine Omarchy installer and is not a
baseline for this variant. No branding substitution can qualify it.

## Source route

Reuse `omacom/omarchy-iso` at `86c07785cb0f63be78edb1349843d5817b5c0e66`,
the existing Dudley adapter pin. Omarchy bootc's separate source pin
`2d0b3ecdec2dd40c82cb8c3d6034b2ac5ab3ae92` is not interchangeable with this
adapter pin. A rebase requires a fresh interface audit.

`scripts/omarchy_quattro_build.py --prepare-only PATH` checks the exact upstream
HEAD, applies the same deployment patch used by the builder, parses the adapter
Python, rejects tracked changes outside the four integration files, and checks
that the official configurator, root configurator and dashboard are unchanged.
This command performs no image pull, disk installation or ISO build.

The shared preparation passed against the real pinned upstream checkout on
2026-11-24. All 32 local source tests passed, including genuine streamed Cosign
envelopes, wrong source/digest, failed verifier and malformed output. These
results do not prove a bootable ISO or installed guest.

## Preserved payload

- Source: `65a8e151b0bb44762a8cb1b9bdebd03c3a874296`
- Signed immutable image: `ghcr.io/joshyorko/omarchy-bootc@sha256:aca01a25953209b1e38b8aa115fe253283153f159f6f28defaede9c528476386`
- Intended future stream: `ghcr.io/joshyorko/omarchy-bootc:testing`

The owner-authorized unencrypted prototype now enables that exact signed
reference in `image.json`. A serialized hosted native-filesystem probe precedes
the genuine ISO build. The source SHA is checked in the verified attestation
before image pulling. This is execution toward a prototype, not qualification.
Stable is unchanged. No OCI rebuild is needed for the installer work.

## Blocking deployment differences

1. `native_contract.py` explicitly refuses encryption before disk mutation.
   Omarchy's encrypted/deferred path uses Limine/mkinitcpio unlock integration;
   native bootc composefs uses different boot ownership. Removing the rejection
   would not implement secure unlock. Encryption parity is BLOCKED.
2. The adapter assumes the native physical sysroot can support upstream
   arch-chroot user creation and official finalization. Its `/usr` view and
   provisioning must be demonstrated using the signed image in a disposable
   guest, not inferred from source tests or replaced with local provisioning.
3. Omarchy's older parity contract describes OSTree deployment lookup and
   `bootc install finalize`. The current payload/adapter selects native
   composefs. Those commands cannot be transplanted without proving backend
   support. Native finalization/boot artifacts need real verification.
4. The current E2E recipe uses upstream `--install-only` automation. It does not
   prove interactive disk, encryption or account questions. Its former forced
   TCG fallback is removed: a required KVM gate must fail when unavailable.

## Mandatory qualification before owner testing

Retain a source/image/ISO SHA receipt, trusted producer identity, full checksum
and raw QMP PNGs for actual official configurator, disk/encryption and account
questions, dashboard/progress, blank-disk completion, disk-only reboot, SDDM,
password login and Hyprland/Quickshell desktop. Check actual booted digest,
plugin behavior, reboot persistence and update/rollback. Run the corresponding
official baseline scenarios, including encryption, deferred provisioning and
cidata; the latter is supplemental, not an interactive substitute.

Every missing scenario is NOT RUN/BLOCKED. No current ISO is qualified for this
full matrix; no genuine installer screenshot is claimed yet.
