# dudley-iso

`dudley-iso` builds Dudley's bootable, offline installer ISO.

The live environment boots the Dudley Dakota NVIDIA image so the installer
works on NVIDIA and non-NVIDIA hardware. The embedded catalog selects
`ghcr.io/joshyorko/dudley-os:dakota` on standard hardware and
`ghcr.io/joshyorko/dudley-os:dakota-nvidia` when NVIDIA support is required.

## Build

Run this from the repository root on the Bluefin host:

```zsh
just iso-sd-boot dudley
```

The build needs Podman, Buildah, Skopeo, `mksquashfs`, `xorriso`, systemd-boot
tools, and approximately 22 GB of free disk space. It writes:

```text
output/dudley-live.iso
```

Use another filesystem when the repository does not have enough space:

```zsh
just output_dir=/var/mnt/dudley-iso iso-sd-boot dudley
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

- `dudley-os` publishes the two installable Dakota container images.
- `dudley-iso` owns live-media assembly, the offline store, installer identity,
  and ISO verification.
- `dudley-factory` is a separate BuildStream experiment and is not used here.

## Upstream foundation

The ISO assembly implementation originated in
[`projectbluefin/dakota-iso`](https://github.com/projectbluefin/dakota-iso).
Project Bluefin remains the source of Dudley's Dakota base images and installer
components. Dudley-specific product language, targets, workflows, and release
ownership live in this repository.
