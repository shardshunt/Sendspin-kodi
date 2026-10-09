# Native backend package location

The release ZIP must contain a target-compatible executable named
`sendspin-kodi-backend` in this directory. Build and stage it from the
repository root with:

```bash
scripts/build_native_backend.sh
```

The native backend is intentionally ignored by Git because binaries are
target-specific build artifacts. The release workflow builds it before
creating the add-on ZIP.
