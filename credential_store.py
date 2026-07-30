"""Prompt-free, user-private credential storage for Voice Input."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path


def default_credentials_path() -> Path:
    configured = os.environ.get("VOICE_INPUT_CREDENTIALS_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "VoiceInput"
        / "credentials.json"
    )


class PrivateFileSecretStore:
    """Store secrets in an atomic JSON file readable only by its owner."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_credentials_path()
        self._lock = threading.RLock()

    def get(self, account: str) -> str:
        with self._lock:
            values = self._load()
            return str(values.get(account, "") or "")

    def set(self, account: str, secret: str) -> None:
        with self._lock:
            values = self._load()
            secret = secret.strip()
            if secret:
                values[account] = secret
            else:
                values.pop(account, None)
            self._write(values)

    def delete(self, account: str) -> None:
        with self._lock:
            values = self._load()
            if account in values:
                del values[account]
                self._write(values)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        with self.path.open(encoding="utf-8") as handle:
            values = json.load(handle)
        if not isinstance(values, dict):
            raise ValueError("凭据文件必须是 JSON 对象")
        return values

    def _write(self, values: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(values, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
            self.path.chmod(0o600)
        except Exception:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise


_STORE = PrivateFileSecretStore()


def get_secret_store() -> PrivateFileSecretStore:
    return _STORE
