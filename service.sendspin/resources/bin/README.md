# Native backend package location

The release ZIP must contain target-compatible executables named
`sendspin-kodi-backend` under architecture directories. Supported directory
names are `x86_64`, `aarch64`, and `armv7`. The service selects the directory
matching `platform.machine()` and falls back to the legacy flat path for
backwards compatibility.

Build and stage a native backend from the repository root with:

```bash
NATIVE_ARCH=x86_64 scripts/build_native_backend_ubuntu_container.sh
```

Run the build on each target architecture, changing `NATIVE_ARCH` to
`aarch64` or `armv7`. `NATIVE_ARCH` controls the staging directory; a
cross-compiler/toolchain is still required when building for a different
architecture.

To build the complete matrix sequentially:

```bash
scripts/build_native_backends_all.sh
```

Build only selected targets with:

```bash
NATIVE_ARCHES="x86_64 aarch64" scripts/build_native_backends_all.sh
```

The native backend is intentionally ignored by Git because binaries are
target-specific build artifacts. The release workflow builds each target
before creating the add-on ZIP.
