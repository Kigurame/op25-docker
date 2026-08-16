"""Authentication: pbkdf2 password verification + HMAC-signed session tokens."""
import base64
import hashlib
import hmac
import json
import os
import time

from .config_store import read_json

# The default secret that shipped with early releases. Sessions signed with it
# are forgeable by anyone who reads this repository, so we refuse to run with
# it instead of silently trusting it. Deployments must either set
# OP25_SESSION_SECRET or accept that sessions reset on every (re)start.
INSECURE_DEFAULT_SECRET = "op25-docker-insecure-secret-change-me"
TOKEN_TTL = 12 * 3600


def _load_session_secret():
    secret = os.environ.get("OP25_SESSION_SECRET")
    if not secret:
        # No secret configured: generate one per process so the default build
        # cannot be forged. Sessions are invalidated whenever the web app
        # (re)starts; set OP25_SESSION_SECRET for sessions that survive restarts.
        secret = base64.urlsafe_b64encode(os.urandom(48)).decode().rstrip("=")
    elif secret == INSECURE_DEFAULT_SECRET:
        raise RuntimeError(
            "OP25_SESSION_SECRET is set to the known-insecure default "
            "(%r). Set it to a random value, e.g. `openssl rand -hex 32`."
            % INSECURE_DEFAULT_SECRET
        )
    return secret


SESSION_SECRET = _load_session_secret()


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
