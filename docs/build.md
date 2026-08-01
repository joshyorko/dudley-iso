# Building the Dudley ISO

Run on the Bluefin host with rootless Podman:

```zsh
just iso-sd-boot dudley
```

The build needs approximately 22 GB and writes `output/dudley-live.iso`.
Do not use `/tmp`; use an explicit output path when the checkout lacks space:

```zsh
just output_dir=/var/mnt/dudley-iso iso-sd-boot dudley
```

Release compression is slower and smaller:

```zsh
just compression=release iso-sd-boot dudley
```

Do not run the rootless build through `sudo`.
