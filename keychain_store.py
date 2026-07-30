"""Small macOS Keychain wrapper for Voice Input credentials."""

from __future__ import annotations

from dataclasses import dataclass


SERVICE_NAME = "com.felix.voiceinput"


class KeychainError(RuntimeError):
    """Raised when macOS Keychain rejects an operation."""


@dataclass(frozen=True)
class KeychainSecretStore:
    service: str = SERVICE_NAME

    def _query(self, account: str) -> dict:
        import Security

        return {
            Security.kSecClass: Security.kSecClassGenericPassword,
            Security.kSecAttrService: self.service,
            Security.kSecAttrAccount: account,
        }

    def get(self, account: str) -> str:
        import Security

        query = self._query(account)
        query.update(
            {
                Security.kSecReturnData: True,
                Security.kSecMatchLimit: Security.kSecMatchLimitOne,
            }
        )
        status, data = Security.SecItemCopyMatching(query, None)
        if status == Security.errSecItemNotFound:
            return ""
        if status != Security.errSecSuccess:
            raise KeychainError(f"读取 Keychain 失败（{status}）")
        return bytes(data).decode("utf-8")

    def set(self, account: str, secret: str) -> None:
        import Security

        secret = secret.strip()
        if not secret:
            self.delete(account)
            return

        query = self._query(account)
        value = {Security.kSecValueData: secret.encode("utf-8")}
        status = Security.SecItemUpdate(query, value)
        if status == Security.errSecItemNotFound:
            item = dict(query)
            item.update(value)
            status, _ = Security.SecItemAdd(item, None)
        if status != Security.errSecSuccess:
            raise KeychainError(f"写入 Keychain 失败（{status}）")

    def delete(self, account: str) -> None:
        import Security

        status = Security.SecItemDelete(self._query(account))
        if status not in (Security.errSecSuccess, Security.errSecItemNotFound):
            raise KeychainError(f"删除 Keychain 项失败（{status}）")


_STORE = KeychainSecretStore()


def get_secret_store() -> KeychainSecretStore:
    return _STORE
