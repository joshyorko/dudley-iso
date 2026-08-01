# Dudley ISO architecture

The installer has two distinct image roles:

1. `dudley-os:dakota-nvidia` supplies the live desktop and the embedded offline
   container store.
2. The installer catalog selects either `dudley-os:dakota` or
   `dudley-os:dakota-nvidia` for the installed system.

`live/Containerfile` assembles the live environment. `scripts/iso-sd-boot.sh`
squashes the payload, embeds its container store, and calls
`live/src/build-iso.sh` to create a systemd-boot UEFI ISO.

The implementation is derived from Project Bluefin's Dakota ISO builder. The
upstream relationship is architectural; Dudley owns the resulting installer,
configuration, testing, and release decisions.
