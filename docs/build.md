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

Release compression is slower and smaller:

```zsh
just compression=release iso dakota
```

Do not run the rootless build through `sudo`.
