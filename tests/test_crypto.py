"""Tests for app/crypto.py: file format, DPAPI, the key file and the backup password."""
import json

import pytest

from app import crypto


@pytest.fixture
def fast_scrypt(monkeypatch):
    monkeypatch.setattr(crypto, "SCRYPT", {"n": 2 ** 10, "r": 8, "p": 1})


def test_encrypt_round_trip_and_format():
    key = crypto.new_key()
    blob = crypto.encrypt(key, b"%PDF-1.7 invoice", "facturen/1/a.pdf")
    assert crypto.is_encrypted(blob) and b"invoice" not in blob
    assert crypto.decrypt(key, blob, "facturen/1/a.pdf") == b"%PDF-1.7 invoice"
    assert crypto.encrypt(key, b"same", "x") != crypto.encrypt(key, b"same", "x")  # a new nonce every time
    assert not crypto.is_encrypted(b"%PDF-1.7") and not crypto.is_encrypted(b"\xff\xd8\xff")


@pytest.mark.parametrize("change", ["other key", "other place", "tampered", "truncated", "plain", "version"])
def test_decrypt_refuses(change):
    key = crypto.new_key()
    blob = crypto.encrypt(key, b"data", "facturen/1/a.pdf")
    key, blob, context = {
        "other key": (crypto.new_key(), blob, "facturen/1/a.pdf"),
        "other place": (key, blob, "facturen/2/a.pdf"),  # file moved to another vehicle
        "tampered": (key, blob[:-1] + bytes([blob[-1] ^ 1]), "facturen/1/a.pdf"),
        "truncated": (key, blob[:10], "facturen/1/a.pdf"),
        "plain": (key, b"%PDF-1.7 plain", "facturen/1/a.pdf"),
        "version": (key, blob[:4] + b"\x09" + blob[5:], "facturen/1/a.pdf"),
    }[change]
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(key, blob, context)


def test_dpapi_round_trip():
    key = crypto.new_key()
    protected = crypto.protect(key)
    assert key not in protected and crypto.unprotect(protected) == key
    with pytest.raises(crypto.CryptoError, match="openen"):
        crypto.unprotect(b"not a dpapi blob")


def test_key_file(tmp_path):
    assert crypto.load_master(tmp_path) is None
    key = crypto.new_key()
    crypto.save_master(tmp_path, key)
    assert crypto.load_master(tmp_path) == key
    assert [p.name for p in tmp_path.iterdir()] == [crypto.KEY_FILE]  # no .deel left
    assert key not in (tmp_path / crypto.KEY_FILE).read_bytes()
    (tmp_path / crypto.KEY_FILE).write_bytes(b"broken")  # e.g. from another Windows account
    with pytest.raises(crypto.CryptoError):
        crypto.load_master(tmp_path)


def test_password_round_trip_with_real_settings():
    """The real scrypt settings must fit within maxmem (OpenSSL refuses otherwise)."""
    key = crypto.new_key()
    wrapped = crypto.wrap_with_password(key, "correct horse battery")
    assert key.hex() not in wrapped and "correct horse" not in wrapped
    assert crypto.unwrap_with_password(wrapped, "correct horse battery") == key
    assert crypto.wrapped_fingerprint(wrapped) == crypto.fingerprint(key)


def test_password_refusals(fast_scrypt):
    key = crypto.new_key()
    with pytest.raises(crypto.CryptoError, match="minstens 12"):
        crypto.wrap_with_password(key, "te kort")
    wrapped = crypto.wrap_with_password(key, "correct horse battery")
    with pytest.raises(crypto.CryptoError, match="Verkeerd wachtwoord"):
        crypto.unwrap_with_password(wrapped, "wrong horse battery")
    greedy = json.dumps({**json.loads(wrapped), "n": 2 ** 24})  # tampered backup asking for 16 GB
    for bad in (greedy, "no json", json.dumps({"salt": "zz"}), json.dumps([1])):
        with pytest.raises(crypto.CryptoError, match="onleesbaar|Onbekende"):
            crypto.unwrap_with_password(bad, "correct horse battery")
    assert crypto.wrapped_fingerprint("no json") is None


def test_fingerprint_tells_keys_apart():
    a, b = crypto.new_key(), crypto.new_key()
    assert crypto.fingerprint(a) == crypto.fingerprint(a) != crypto.fingerprint(b)
