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
