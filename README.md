# dudley-iso

`dudley-iso` builds Dudley's bootable, offline installer ISOs for the Dakota
and Bluefin image families.

Each ISO boots its family's Dudley NVIDIA image so the installer works on
NVIDIA and non-NVIDIA hardware. The embedded catalog then selects the standard
or NVIDIA installable image for the detected hardware.

| ISO family | Standard image | NVIDIA/live image | Output |
| --- | --- | --- | --- |
| Dakota | `dudley-os:dakota` | `dudley-os:dakota-nvidia` | `output/dudley-dakota-live.iso` |
| Bluefin | `dudley-os:stable` | `dudley-os:nvidia` | `output/dudley-bluefin-live.iso` |

## Build

Run this from the repository root on the Bluefin host:

```zsh
just iso dakota
just iso bluefin
```

`just iso` defaults to Dakota. The build needs Podman, Buildah, Skopeo,
`mksquashfs`, `xorriso`, systemd-boot tools, and approximately 22 GB of free
disk space per build.

Build Quattro through Omarchy's supported upstream installer UX:

```zsh
just iso omarchy-quattro
```

This writes `output/omarchy-quattro/omarchy-quattro.iso` and the adjacent
`omarchy-quattro.iso.sha256`. The accepted source digest is held in
`omarchy-quattro/image.json`; its repository-owned keyless signer policy is
checked before build output is staged. The prototype uses the reviewed signed
digest recorded in that file and rejects a different image or publisher.
The embedded source digest is separate from the installed OS tracking ref
(`ghcr.io/joshyorko/omarchy-bootc:testing`), so offline installs remain
independent of later tag movement.
The Quattro build requires Just, Git, Python 3.11 or newer, Skopeo, Cosign,
`sha256sum`, and rootless Podman. Arch build tools run inside its privileged
builder container. Use a disk-backed checkout with ample build and cache space;
the small `/tmp` tmpfs on Dakota is unsuitable. The clean-clone recipe and host
tool preflight have been checked on Dakota; a heavy local build has not been run.

This first Quattro prototype is unencrypted and explicitly has no zram swap.
The wizard's hidden swap default is disabled because the accepted image does
not contain `zram-generator`. Explicit zram requests are rejected before disk
mutation; supporting them requires an image-built package, never live pacstrap.
English setup uses the image's existing `C.UTF-8` locale. Setup records both
choices in `/var/lib/omarchy-bootc/native-system-setup.json` on the installed disk.
The prototype is not ready for hardware installation until the same packaged
ISO completes blank-disk KVM installation, disk-only boot and password login.

Use another filesystem when the repository does not have enough space:

```zsh
just output_dir=/var/mnt/dudley-iso iso bluefin
```

## Verification

A successful ISO assembly is not sufficient proof. Before Dudley publishes an
installer, verification must include:

1. A fresh debug ISO build.
2. A successful live boot.
3. A completed offline installation.
4. A successful boot of the installed Dudley system.

The inherited publication and scheduled E2E workflows remain disabled until
that complete path has passed.

## Repository ownership

- `dudley-os` publishes the Dakota, Dakota NVIDIA, stable Bluefin, and Bluefin
  NVIDIA container images.
- `dudley-iso` owns live-media assembly, the offline store, installer identity,
  and ISO verification.
- `dudley-factory` is a separate BuildStream experiment and is not used here.
- Omarchy Quattro uses the pinned upstream ISO configurator and installer flow.

## Upstream foundation

The ISO assembly implementation originated in
[`projectbluefin/dakota-iso`](https://github.com/projectbluefin/dakota-iso).
Project Bluefin remains the source of Dudley's Dakota and Bluefin base images
and installer components. Dudley-specific product language, targets, workflows,
and release ownership live in this repository.
