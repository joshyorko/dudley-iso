# Contributing to dudley-iso

Keep changes focused on Dudley's installer target and offline-install path.

Before opening a pull request:

```zsh
just --list
pytest tests/ -q
pre-commit run --all-files
```

Installer changes also require a fresh ISO build and installed-system boot.
Use Conventional Commits and state exactly which static, build, live-boot, and
install gates were exercised.
