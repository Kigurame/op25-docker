"""Authentication: pbkdf2 password verification + HMAC-signed session tokens."""
import base64
import hashlib
import hmac
import json
import os
import time

from .config_store import read_json

SESSION_SECRET = os.environ.get("OP25_SESSION_SECRET", "op25-docker-insecure-secret-change-me")
TOKEN_TTL = 12 * 3600


def verify_password(password, stored):
    """stored format: pbkdf2_sha256$<iterations>$<salt>$<hexdigest>"""
    try:
        algo, iterations, salt, digest = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations))
        return hmac.compare_digest(computed.hex(), digest)
    except (ValueError, AttributeError):
        return False


def hash_password(password):
    salt = base64.urlsafe_b64encode(os.urandom(9)).decode().rstrip("=")
    iterations = 260000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, salt, digest)


def authenticate(username, password):
    try:
        users = read_json("users.json").get("users", [])
    except (OSError, ValueError):
        return None
    for u in users:
        if u.get("username") == username and verify_password(password, u.get("password_hash", "")):
            return u
    return None


def make_token(payload):
    payload = {**payload, "e": int(time.time()) + TOKEN_TTL}
    return _sign(payload)


def verify_token(token):
    if not token:
        return None
    try:
        payload = _unsign(token)
    except Exception:
        return None
    if payload.get("e", 0) < time.time():
        return None
    return payload


def _sign(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return "%s.%s" % (body, sig)


def _unsign(token):
    body, sig = token.rsplit(".", 1)
    expect = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig):
        raise ValueError("bad signature")
    pad = "=" * (-len(body) % 4)
    return json.loads(base64.urlsafe_b64decode(body + pad).decode())
