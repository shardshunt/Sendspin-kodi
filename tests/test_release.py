import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import release


class TestNativeBackendMatrix(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.backend_root = self.root / "bin"
        self.manifest_path = self.root / "matrix.json"
        self.source_root = self.root / "source"
        (self.source_root / "native").mkdir(parents=True)
        (self.source_root / "native" / "backend.cpp").write_text("int main() {}\n", encoding="utf-8")
        for input_path in release.NATIVE_BUILD_INPUTS:
            target = self.source_root / input_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("build input\n", encoding="utf-8")
        for architecture in release.NATIVE_ARCHITECTURES:
            backend = self.backend_root / architecture / "sendspin-kodi-backend"
            backend.parent.mkdir(parents=True, exist_ok=True)
            backend.write_bytes(f"backend-{architecture}".encode("ascii"))
            backend.chmod(0o755)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_missing_manifest_is_rejected(self):
        errors = release.native_backend_matrix_errors(self.backend_root, self.manifest_path, self.source_root)

        self.assertTrue(any("build_native_backends_all.sh" in error for error in errors))

    def test_complete_build_manifest_validates(self):
        release.write_native_backend_matrix_manifest(self.backend_root, self.manifest_path, self.source_root)

        self.assertEqual(
            release.native_backend_matrix_errors(self.backend_root, self.manifest_path, self.source_root), []
        )

    def test_manifest_rejects_binary_changes(self):
        release.write_native_backend_matrix_manifest(self.backend_root, self.manifest_path, self.source_root)
        backend = self.backend_root / "x86_64" / "sendspin-kodi-backend"
        backend.write_bytes(b"changed backend")

        errors = release.native_backend_matrix_errors(self.backend_root, self.manifest_path, self.source_root)

        self.assertTrue(any("x86_64" in error for error in errors))

    def test_manifest_rejects_native_source_changes(self):
        release.write_native_backend_matrix_manifest(self.backend_root, self.manifest_path, self.source_root)
        (self.source_root / "native" / "backend.cpp").write_text("changed source\n", encoding="utf-8")

        errors = release.native_backend_matrix_errors(self.backend_root, self.manifest_path, self.source_root)

        self.assertTrue(any("stale" in error for error in errors))

    def test_manifest_cannot_be_recorded_for_incomplete_matrix(self):
        (self.backend_root / "armv7" / "sendspin-kodi-backend").unlink()

        with self.assertRaises(release.ReleaseError):
            release.write_native_backend_matrix_manifest(self.backend_root, self.manifest_path, self.source_root)

        self.assertFalse(self.manifest_path.exists())

    def test_zip_creation_requires_full_matrix_manifest(self):
        with patch.object(release, "native_backend_matrix_errors", return_value=["matrix missing"]):
            with self.assertRaisesRegex(release.ReleaseError, "matrix missing"):
                release.create_zip()


if __name__ == "__main__":
    unittest.main()
