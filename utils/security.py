"""
Encrypted credential storage using Fernet (AES-128-CBC + HMAC-SHA256).
Master password is stretched with PBKDF2-HMAC-SHA256 (600k iterations).
Raw API keys are never written to any plain-text file.

Storage layout (default ~/.binance_bot/):
  credentials.enc  — Fernet-encrypted "api_key\napi_secret"
  salt.bin         — 16-byte random salt for PBKDF2
"""
import base64
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


class AuthenticationError(Exception):
    pass


class CredentialStore:
    ITERATIONS = 600_000

    def __init__(self, storage_dir: str = "~/.binance_bot"):
        self._dir = Path(storage_dir).expanduser()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._enc_path  = self._dir / "credentials.enc"
        self._salt_path = self._dir / "salt.bin"
        self._fernet: Fernet | None = None

    def credentials_exist(self) -> bool:
        return self._enc_path.exists() and self._salt_path.exists()

    def set_master_password(self, password: str) -> None:
        """Derive Fernet key from password. Must be called before save/load."""
        if self._salt_path.exists():
            salt = self._salt_path.read_bytes()
        else:
            salt = secrets.token_bytes(16)
            self._salt_path.write_bytes(salt)
        key = self._derive_key(password, salt)
        self._fernet = Fernet(key)

    def save_credentials(self, api_key: str, api_secret: str) -> None:
        if self._fernet is None:
            raise AuthenticationError("Master password not set — call set_master_password() first")
        plaintext = f"{api_key}\n{api_secret}".encode("utf-8")
        self._enc_path.write_bytes(self._fernet.encrypt(plaintext))

    def load_credentials(self) -> tuple[str, str]:
        if self._fernet is None:
            raise AuthenticationError("Master password not set — call set_master_password() first")
        if not self._enc_path.exists():
            raise AuthenticationError("No credentials stored")
        try:
            plaintext = self._fernet.decrypt(self._enc_path.read_bytes()).decode("utf-8")
        except InvalidToken:
            raise AuthenticationError("Wrong master password or corrupted credentials")
        parts = plaintext.split("\n", 1)
        if len(parts) != 2:
            raise AuthenticationError("Corrupted credentials format")
        return parts[0].strip(), parts[1].strip()

    def clear_credentials(self) -> None:
        self._enc_path.unlink(missing_ok=True)
        self._salt_path.unlink(missing_ok=True)
        self._fernet = None

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self.ITERATIONS,
        )
        raw_key = kdf.derive(password.encode("utf-8"))
        return base64.urlsafe_b64encode(raw_key)
