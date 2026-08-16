#!/usr/bin/env python3
"""Replace the admin password in users.json with a random one.

Runs from the entrypoint on first boot, after conf/ defaults have been seeded
into the mounted volume, so a fresh install never ships with the well-known
`admin123` default. Prints the generated credentials once to stdout (the
container log); you can change it later in the web UI (Config -> Change my
password) or by editing conf/users.json.

Usage:
  set_admin_password.py [--users-file PATH] [--length N]

The PBKDF2 format matches control-plane/app/auth.py so the web app can verify
logins with it.
"""
import argparse
import base64
import hashlib
import json
import os
import secrets

PBKDF2_ITERATIONS = 260000


def hash_password(password):
    salt = base64.urlsafe_b64encode(os.urandom(9)).decode().rstrip("=")
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()
    return "pbkdf2_sha256$%d$%s$%s" % (PBKDF2_ITERATIONS, salt, digest)


def _atomic_write(path, data):
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=4, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users-file", default=os.environ.get("OP25_CONF_DIR", "/opt/op25/conf") + "/users.json")
    ap.add_argument("--length", type=int, default=16)
    args = ap.parse_args()

    with open(args.users_file, "r", encoding="utf-8") as fh:
        users = json.load(fh)

    admins = [u for u in users.get("users", []) if u.get("role") == "admin"]
    if not admins:
        print("set_admin_password: no admin user in users.json - leaving unchanged", flush=True)
        return 0

    password = secrets.token_urlsafe(args.length)[: args.length]
    admin = admins[0]
    admin["password_hash"] = hash_password(password)
    admin.pop("created", None)

    _atomic_write(args.users_file, users)

    print("=" * 60, flush=True)
    print("op25-docker: generated admin login (change it after signing in):", flush=True)
    print("    username: %s" % admin.get("username", "admin"), flush=True)
    print("    password: %s" % password, flush=True)
    print("=" * 60, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
