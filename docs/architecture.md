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

## Omarchy Quattro installer

`just iso omarchy-quattro` builds the live `quattro` branch commit
`86c07785cb0f63be78edb1349843d5817b5c0e66` from
`https://github.com/omacom/omarchy-iso.git` in a disposable source checkout.
The patch selects a separate OS backend while retaining the upstream
configurator, install dashboard, disk selection, and user interaction.

The builder obtains the embedded source from the accepted immutable digest in
`omarchy-quattro/image.json`, verifies it with the repository-owned keyless
signer identity and OIDC issuer, and embeds that OCI archive in the ISO. The
installed system's future tracking ref is separately fixed to
`ghcr.io/joshyorko/omarchy-bootc:testing`. The archive is the install source,
so later movement of the tracking tag cannot change an offline install.

The backend invokes native composefs `bootc install to-filesystem`. Its
physical target (`ctx.target`, normally `/mnt`) is the sysroot. Installer
machine-local state is written below that sysroot's `/etc` and `/var`; the
image owns `/usr`. The adapter does not enumerate OSTree deployments or invoke
`bootc install finalize`, which is OSTree-only. It removes the root entry from
the fstab archinstall generates and keeps non-root mount entries, following
bootc's composefs guidance to use root kernel arguments instead.
The adapter performs the native path's deferred filesystem finalization after
the upstream finalizers and validation: `fstrim` when supported, remount the
physical sysroot read-only, then `fsfreeze` when supported.

| Install concern | Owner and write path |
| --- | --- |
| User, password, sudo | Upstream archinstall account writers under physical sysroot `/etc` and `/home`; deferred installs strip accounts and use first-boot state under `/var` |
| Timezone and time sync | archinstall writes timezone state below sysroot `/etc` and enables the target time service |
| Partitioning | Upstream configurator and archinstall filesystem handler; bootc receives the resulting mounted physical sysroot |
| Encryption | Rejected before live preparation or disk mutation; native systemd-boot unlock is not implemented, and upstream deferred-encryption hooks target Limine/mkinitcpio |
| Root filesystem table | archinstall writes `/etc/fstab`; adapter removes the `/` row and keeps other mounts |
| Network and DNS | Image NetworkManager DHCP defaults; upstream helper writes `/etc/resolv.conf` as the systemd-resolved symlink |
| SSH | Upstream optional `authorized_keys` setup uses sysroot `/home`, `/etc`, and `/var` |
| Machine identity | bootc provenance metadata on physical sysroot; fresh-system machine-id remains first-boot generated; adapter adds `/var/lib/omarchy-bootc/installer-origin` |
| Deferred provisioning | Upstream provisioning files and service under `/var`; first-boot owner provisioning remains upstream-owned |
| Official Omarchy setup | Upstream system and user finalizers; Limine-specific boot finalization and factory snapshot are omitted because systemd-boot and bootc own those responsibilities |
| Native filesystem finalization | After all writes, `fstrim` when supported, read-only remount, and `fsfreeze` when supported; the upstream mount cleanup then unwinds the sysroot |

This is a static contract only; no physical-disk installation or installed VM
boot is claimed. The accepted OS digest remains a post-merge input. Gate 6
requires that digest plus fresh VM evidence. Once the digest is populated, the
Quattro workflow builds the ISO and runs an unencrypted QEMU installation; while
the digest is blank it reports that gate as skipped. CI also runs the pinned
upstream VM-free suite, which includes CIDATA autoinstall-loading tests; those
do not constitute a native Quattro CIDATA install. CI does not exercise
encrypted installs, which are rejected, or deferred first-boot provisioning;
the adapter checks the upstream pending-service and owner-payload contract when
deferred provisioning is selected.
