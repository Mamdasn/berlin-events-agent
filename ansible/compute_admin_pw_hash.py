#!/usr/bin/env python3
"""
Canonical pbkdf2_sha256 hash computation for Ansible provisioning.

This is the single source of truth — mirrors src/agent/_password.py::hash_password.
Uses only stdlib so it runs on any control node without agent dependencies.
"""
import hashlib
import os
import secrets


def hash_password(password, iterations=600000):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


if __name__ == "__main__":
    print(hash_password(os.environ["ADMIN_PASSWORD"]))
