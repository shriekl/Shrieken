"""AES-GCM payloads. The key is PBKDF2-SHA256 of the site password.

The browser decrypts the same layout with Web Crypto. Salt and iteration count
travel with the file; they are not secret. The ciphertext is what stays private.
"""
import os, json, base64
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITERATIONS = 100_000


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _unb64(text):
    return base64.b64decode(text)


def derive_key(password, salt, iterations=ITERATIONS):
    if isinstance(password, str):
        password = password.encode("utf-8")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(password)


def encrypt_bytes(plain, password, salt=None, iterations=ITERATIONS):
    salt = salt or os.urandom(16)
    key = derive_key(password, salt, iterations)
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, plain, None)
    return {
        "v": 1,
        "alg": "AES-GCM",
        "kdf": "PBKDF2-SHA256",
        "iter": iterations,
        "salt": _b64(salt),
        "iv": _b64(iv),
        "ct": _b64(ct),
    }


def decrypt_bytes(payload, password):
    salt = _unb64(payload["salt"])
    iv = _unb64(payload["iv"])
    ct = _unb64(payload["ct"])
    key = derive_key(password, salt, int(payload["iter"]))
    return AESGCM(key).decrypt(iv, ct, None)


def encrypt_json(obj, password):
    plain = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return encrypt_bytes(plain, password)


def decrypt_json(payload, password):
    return json.loads(decrypt_bytes(payload, password).decode("utf-8"))


def dump(path, payload):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    os.replace(tmp, path)
