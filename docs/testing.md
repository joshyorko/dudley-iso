# Testing the Dudley installer

Static validation:

```zsh
just --list
pytest tests/ -q
pre-commit run --all-files
```

Runtime proof must always use fresh artifacts. Remove prior ISO, install-disk,
and QEMU state before testing. Build with debug support, boot the live image,
complete an offline btrfs installation, and then boot the installed system.

An ISO file existing or reaching the live desktop is not completion evidence.
The installed Dudley system must boot successfully.
