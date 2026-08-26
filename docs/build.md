# Building the Dudley ISO

Run on the Bluefin host with rootless Podman:

```zsh
just iso dakota
just iso bluefin
```

`just iso` defaults to Dakota. Each build needs approximately 22 GB and writes
one of:

```text
output/dudley-dakota-live.iso
output/dudley-bluefin-live.iso
```

Do not use `/tmp`; use an explicit output path when the checkout lacks space:

```zsh
just output_dir=/var/mnt/dudley-iso iso bluefin
```

The upstream Omarchy Quattro installer is a separate build path and requires
an immutable signed image reference:

```zsh
OMARCHY_QUATTRO_IMAGE_REF=ghcr.io/joshyorko/omarchy-bootc@sha256:<digest> \
  just iso omarchy-quattro
```

It writes `output/omarchy-quattro/omarchy-quattro.iso`. The command clones
the pinned upstream `quattro` source into that output directory, applies the
small backend adapter, embeds the OCI archive, and uses the upstream
installer. It does not invoke `iso-sd-boot` or modify the existing Dudley
variant outputs.

Release compression is slower and smaller:

```zsh
just compression=release iso dakota
```

Do not run the rootless build through `sudo`.
