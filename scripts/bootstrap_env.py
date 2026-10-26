#!/usr/bin/env python3
"""Fill the required secrets in `.env` with random values.

Usage:
    cp .env.example .env
    python scripts/bootstrap_env.py

Only empty values are filled; anything you already typed is left untouched.
Run it once, then keep the resulting `.env` somewhere safe.
"""

from __future__ import annotations

import re
import secrets
import string
import sys
from pathlib import Path

REQUIRED = ("POSTGRES_PASSWORD", "SECRET_KEY", "CREDENTIAL_ENCRYPTION_KEY")


def _password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main() -> int:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        print("error: .env not found. Copy the template first:", file=sys.stderr)
        print("    cp .env.example .env", file=sys.stderr)
        return 1

    text = env_path.read_text(encoding="utf-8")
    generated: list[tuple[str, str]] = []

    for name in REQUIRED:
        pattern = re.compile(rf"(?m)^{name}=(.*)$")
        match = pattern.search(text)
        if not match:
            print(f"warning: no {name}= line in .env; skipping", file=sys.stderr)
            continue
        if match.group(1).strip():
            continue
        value = secrets.token_hex(32)
        text = pattern.sub(f"{name}={value}", text, count=1)
        generated.append((name, value))

    admin_pattern = re.compile(r"(?m)^ADMIN_PASSWORD=(.*)$")
    admin_match = admin_pattern.search(text)
    admin_password = ""
    if admin_match and not admin_match.group(1).strip():
        admin_password = _password()
        text = admin_pattern.sub(f"ADMIN_PASSWORD={admin_password}", text, count=1)

    env_path.write_text(text, encoding="utf-8")

    if generated:
        print("Generated:")
        for name, _value in generated:
            print(f"  {name}")
    else:
        print("Required secrets were already set; nothing to do.")
    if admin_password:
        print(f"\nAdministrator password: {admin_password}")
        print("Write it down now; it is not stored anywhere else.")
    print(f"\nWrote {env_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
