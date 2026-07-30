import json
import stat
import tempfile
import unittest
from pathlib import Path

from credential_store import PrivateFileSecretStore


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "credentials.json"
        self.store = PrivateFileSecretStore(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_missing_credential_is_empty(self):
        self.assertEqual(self.store.get("missing"), "")

    def test_round_trip(self):
        self.store.set("asr-api-key", "test-" + "secret")
        self.assertEqual(
            self.store.get("asr-api-key"), "test-" + "secret"
        )

    def test_file_is_owner_only(self):
        self.store.set("llm-api-key", "test-" + "secret")
        self.assertEqual(
            stat.S_IMODE(self.path.stat().st_mode),
            0o600,
        )

    def test_delete_removes_only_selected_credential(self):
        self.store.set("asr-api-key", "asr-" + "secret")
        self.store.set("llm-api-key", "llm-" + "secret")
        self.store.delete("asr-api-key")
        self.assertEqual(self.store.get("asr-api-key"), "")
        self.assertEqual(self.store.get("llm-api-key"), "llm-" + "secret")

    def test_secret_file_remains_valid_json(self):
        self.store.set("asr-api-key", "test-" + "secret")
        values = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(values["asr-api-key"], "test-" + "secret")


if __name__ == "__main__":
    unittest.main()
