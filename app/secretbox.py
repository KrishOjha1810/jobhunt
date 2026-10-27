"""Tiny symmetric encryption for the one secret we must store recoverably: the user's shared
job-board password (the "one password, every board" feature). The agent needs the plaintext to type
it into a board sign-up, so a one-way hash won't do; we Fernet-encrypt it at rest with a key derived
from SECRET_KEY. If SECRET_KEY is unset (dev default is random per restart), a value encrypted before
a restart will no longer decrypt; set SECRET_KEY in the environment in production (sessions already
require this), and decrypt() fails closed to "" so nothing crashes."""
import base64
import hashlib
from cryptography.fernet import Fernet
from .config import SECRET_KEY


def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256((SECRET_KEY or "").encode()).digest())
    return Fernet(key)


def encrypt(plaintext):
    if not plaintext:
        return ""
    return _fernet().encrypt(str(plaintext).encode()).decode()


def decrypt(token):
    if not token:
        return ""
    try:
        return _fernet().decrypt(str(token).encode()).decode()
    except Exception:
        return ""
