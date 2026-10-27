"""Encryption of the data files and the backup (P1, P2). Design and reasons: docs/encryption.md.

One random master key, kept in two wrapped forms: with Windows DPAPI in garage.key (only this pc and Windows
account) and with a key derived from the backup password (goes into every backup, so another pc can restore it).
Every file gets its own random key, wrapped with the master key: throwing that key away makes all copies of
the file unreadable, also old OneDrive versions.

Format of everything encrypted here: MAGIC + version (1 byte) + nonce (12 bytes) + AES-256-GCM ciphertext with
tag. The context (e.g. the relative path of a file) is the associated data: a blob only decrypts in its own place.
"""
import ctypes
import hashlib
import hmac
import json
import os
from ctypes import wintypes
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"GRGE"
VERSION = 1
NONCE_SIZE = 12
HEADER_SIZE = len(MAGIC) + 1 + NONCE_SIZE
KEY_FILE = "garage.key"
MIN_PASSWORD = 12
# scrypt: 128 * r * n bytes of memory (128 MB) and one to two seconds, only when setting the password or
# restoring; stored with every wrapped key, so these values can be raised later without breaking old backups
SCRYPT = {"n": 2 ** 17, "r": 8, "p": 1}
SCRYPT_MAX = {"n": 2 ** 18, "r": 8, "p": 2}  # at most 256 MB: a tampered backup must not take all memory
_ENTROPY = b"Garage master key"  # DPAPI: other programs need this as well (not a secret, a namespace)
_UI_FORBIDDEN = 0x1  # DPAPI: never a Windows dialog


class CryptoError(Exception):
    pass


def new_key() -> bytes:
    return AESGCM.generate_key(256)


def is_encrypted(data: bytes) -> bool:
    return data[:len(MAGIC)] == MAGIC


def encrypt(key: bytes, data: bytes, context: str) -> bytes:
    nonce = os.urandom(NONCE_SIZE)
    return MAGIC + bytes([VERSION]) + nonce + AESGCM(key).encrypt(nonce, data, context.encode())


def decrypt(key: bytes, blob: bytes, context: str) -> bytes:
    if len(blob) < HEADER_SIZE or not is_encrypted(blob) or blob[len(MAGIC)] != VERSION:
        raise CryptoError("Onbekend of beschadigd versleuteld bestand.")
    try:
        return AESGCM(key).decrypt(blob[len(MAGIC) + 1:HEADER_SIZE], blob[HEADER_SIZE:], context.encode())
    except InvalidTag:
        raise CryptoError("Ontsleutelen mislukt: verkeerde sleutel, of het bestand is beschadigd of verplaatst.") from None


def fingerprint(key: bytes) -> str:
    """Tells whether two master keys are the same (e.g. a backup from this pc) without storing the key itself."""
    return hmac.new(key, b"Garage key fingerprint", hashlib.sha256).hexdigest()[:16]


# ---------- backup password ----------

def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise CryptoError(f"Kies een wachtwoord van minstens {MIN_PASSWORD} tekens.")


def _password_key(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    if not all(isinstance(v, int) and 0 < v <= SCRYPT_MAX[k] for k, v in (("n", n), ("r", r), ("p", p))):
        raise CryptoError("Onbekende instellingen voor het wachtwoord.")
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=256 * r * n, dklen=32)


def wrap_with_password(master: bytes, password: str) -> str:
    """The master key, encrypted with the backup password, as JSON (for the database and garage-backup.json)."""
    check_password(password)
    salt = os.urandom(16)
    blob = encrypt(_password_key(password, salt, **SCRYPT), master, "master-key")
    return json.dumps({"salt": salt.hex(), **SCRYPT, "key": blob.hex(), "fingerprint": fingerprint(master)})


def unwrap_with_password(wrapped: str, password: str) -> bytes:
    try:
        data = json.loads(wrapped)
        key = _password_key(password, bytes.fromhex(data["salt"]), data["n"], data["r"], data["p"])
        blob = bytes.fromhex(data["key"])
    except (ValueError, KeyError, TypeError):
        raise CryptoError("De sleutel in deze back-up is onleesbaar.") from None
    try:
        return decrypt(key, blob, "master-key")
    except CryptoError:
        raise CryptoError("Verkeerd wachtwoord.") from None


def wrapped_fingerprint(wrapped: str) -> str | None:
    """Fingerprint of a password-wrapped master key, readable without the password."""
    try:
        return json.loads(wrapped).get("fingerprint")
    except (ValueError, AttributeError):
        return None


# ---------- Windows DPAPI ----------

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _in_blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))  # returned as well: must stay alive during the call
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _dpapi(protect: bool, data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob), ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    data_in, keep = _in_blob(data)
    entropy, keep_entropy = _in_blob(_ENTROPY)
    out = _Blob()
    if not function(ctypes.byref(data_in), None, ctypes.byref(entropy), None, None, _UI_FORBIDDEN, ctypes.byref(out)):
        raise CryptoError(f"Windows kan de sleutel niet {'beveiligen' if protect else 'openen'} "
                          f"(fout {ctypes.get_last_error()}).")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(data: bytes) -> bytes:
    return _dpapi(True, data)


def unprotect(data: bytes) -> bytes:
    return _dpapi(False, data)


# ---------- key file ----------

def load_master(folder: Path) -> bytes | None:
    """The master key from garage.key; None if there is none yet. CryptoError if it cannot be opened (other pc,
    other Windows account, Windows reinstalled): then only the backup password can bring it back."""
    path = folder / KEY_FILE
    if not path.exists():
        return None
    key = unprotect(path.read_bytes())
    if len(key) != 32:
        raise CryptoError("Het sleutelbestand is beschadigd.")
    return key


def save_master(folder: Path, key: bytes) -> None:
    """Writes garage.key atomically: a half-written key file would make all data unreadable."""
    path = folder / KEY_FILE
    part = path.with_name(KEY_FILE + ".deel")
    with open(part, "wb") as f:
        f.write(protect(key))
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, path)
