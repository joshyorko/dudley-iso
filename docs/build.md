# Building the Dudley ISO

Run on the Bluefin host with rootless Podman:

```zsh
just iso dakota
just iso bluefin
just iso omarchy-quattro
```

`just iso` defaults to Dakota. Each build needs approximately 22 GB and writes
one of:

```text
output/dudley-dakota-live.iso
output/dudley-bluefin-live.iso
output/omarchy-quattro/omarchy-quattro.iso
output/omarchy-quattro/omarchy-quattro.iso.sha256
```

Do not use `/tmp`; use an explicit output path when the checkout lacks space:

```zsh
just output_dir=/var/mnt/dudley-iso iso bluefin
```

Release compression is slower and smaller:

```zsh
just compression=release iso dakota
```

Do not run the rootless build through `sudo`.

Quattro uses `omarchy-quattro/image.json`. Populate `accepted_source_ref` with
the reviewed `ghcr.io/joshyorko/omarchy-bootc@sha256:<digest>` after image
verification; do not put a moving tag there. The signer identity and issuer
are repository-owned policy in the same file. The builder rejects an absent
digest or failed signer verification before cloning sources or producing an
ISO. The recipe needs no arguments or environment variables.
