# Dudley ISO agent instructions

This repository owns Dudley's offline installer ISO. `dudley-os` owns the
installable container images; `dudley-factory` is unrelated to this build path.

## Product contract

| Family | Standard payload | NVIDIA/live payload | Bootloader | Composefs | Command |
| --- | --- | --- | --- | --- | --- |
| Dakota | `dudley-os:dakota` | `dudley-os:dakota-nvidia` | systemd-boot | enabled | `just iso dakota` |
| Bluefin | `dudley-os:stable` | `dudley-os:nvidia` | GRUB2 | disabled | `just iso bluefin` |

Both families default to btrfs and automatically select the standard or NVIDIA
installed image. `just iso` defaults to Dakota. Outputs are
`output/dudley-dakota-live.iso` and `output/dudley-bluefin-live.iso`.

Project Bluefin may appear only as an upstream technical dependency or explicit
attribution. User-facing identity, examples, workflow names, and releases must
be Dudley-owned.

## Safety gates

1. Add or update a static contract test before changing installer behavior.
2. Run `just --list`, `pytest tests/ -q`, and pre-commit before committing.
3. Never claim an ISO works from static tests alone.
4. A release requires a fresh ISO build, live boot, completed offline install,
   and successful installed-system boot.
5. Do not enable scheduled builds, E2E jobs, uploads, or release promotion until
   Josh explicitly approves them after local E2E evidence.
6. Never overwrite an existing published ISO manually.

## Git

- Use Conventional Commits.
- Push product work only to `joshyorko/dudley-iso`.
- Keep unrelated upstream product targets, release machinery, and automatic
  syncs out of this repo. Upstream changes enter only through deliberate manual
  ports.

## Host behavior

Build rootless on the Bluefin host. Do not use `/tmp`; the build needs roughly
22 GB. Prefer an explicit output directory on XFS or Btrfs when space is tight.
