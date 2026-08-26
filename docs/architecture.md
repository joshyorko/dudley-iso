# Dudley ISO architecture

Each installer family has two distinct image roles:

1. The NVIDIA image supplies the live desktop and embedded offline container
   store: `dudley-os:dakota-nvidia` for Dakota or `dudley-os:nvidia` for
   Bluefin.
2. The installer catalog selects the standard or NVIDIA image for the installed
   system: `dakota`/`dakota-nvidia` or `stable`/`nvidia`.

The public `just iso <family>` interface maps `dakota` and `bluefin` to the
internal `dudley-dakota` and `dudley-bluefin` build targets. Those target
records keep the source image tag separate from the installer catalog variant.

`live/Containerfile` assembles the live environment. `scripts/iso-sd-boot.sh`
squashes the payload, embeds its container store, and calls
`live/src/build-iso.sh` to create a systemd-boot UEFI ISO.

The implementation is derived from Project Bluefin's Dakota ISO builder and
its Bluefin target contract. The upstream relationship is architectural;
Dudley owns the resulting installer, configuration, testing, and release
decisions.

## Omarchy Quattro adapter

`just iso omarchy-quattro` follows a separate path under `omarchy-quattro/`.
The host builder checks out exactly the recorded `omacom-io/omarchy-iso`
revision, applies `upstream.patch`, injects `bootc_backend.py`, and runs the
upstream `bin/omarchy-iso-make` with a rootless Podman runtime. The Quattro OCI
is saved as an archive and embedded into the ISO's live root, so installation
does not fetch the target OS from the network.

The patched upstream orchestrator keeps its configurator, partitioning and
encryption flow, cidata autoinstall, dashboard, user inputs, official user
finalizer, SDDM setup, and acceptance harness. Only the package/Limine
deployment phase is selected through `OMARCHY_INSTALL_BACKEND=bootc`; it runs
the target image's `bootc install to-filesystem`, locates the active
deployment, applies installer-collected machine state, and finishes with
`bootc install finalize`.

The Quattro adapter never enters the existing `live/Containerfile` or
`scripts/iso-sd-boot.sh` path. Existing Dakota and Bluefin targets remain the
only consumers of those files.
