Vendor helper
==============

This folder contains `vendor_libs.py`, a small helper to vendor Python
packages into the addon `resources/lib` directory so the addon can import
them without installing into system Python.

Usage
-----

Run from the repository root (or adjust `--target`):

```sh
python service.sendspin/tools/get_libs.py
```

Options:
- `--clean` : remove the target directory before vendoring

Notes
-----
