#!/usr/bin/env python3
"""Configure a private production .env without printing or replacing credentials."""
import argparse
import os
from pathlib import Path
import re
import secrets
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hostname", help="Public hostname without https:// or a path")
    args = parser.parse_args()
    hostname = args.hostname.rstrip(".").lower()
    if len(hostname) > 253 or "." not in hostname or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in hostname.split(".")
    ):
        parser.error("Supply a valid public DNS hostname, without a scheme or path")

    root = Path(__file__).resolve().parent.parent
    destination = root / ".env"
    source = destination if destination.exists() else root / ".env.example"
    lines = source.read_text().splitlines()
    values = {}
    for line in lines:
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if match:
            values[match[1]] = match[2].strip().strip("\"'")

    updates = {
        "SECRET_KEY": values.get("SECRET_KEY") or secrets.token_hex(32),
        "DB_PASSWORD": values.get("DB_PASSWORD") or secrets.token_hex(24),
        "DB_NAME": values.get("DB_NAME") or "shoeverse",
        "DB_USER": values.get("DB_USER") or "shoeverse",
        "DJANGO_DEBUG": "false",
        "DJANGO_ALLOWED_HOSTS": f"localhost,127.0.0.1,{hostname}",
        "DJANGO_CSRF_TRUSTED_ORIGINS": f"https://{hostname}",
    }
    remaining = dict(updates)
    output = []
    for line in lines:
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match[1] in updates:
            key = match[1]
            # Keep the original spelling/quoting of existing non-empty secrets.
            if key in {"SECRET_KEY", "DB_PASSWORD"} and values.get(key):
                output.append(line)
            else:
                output.append(f"{key}={updates[key]}")
            remaining.pop(key, None)
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in remaining.items())
    with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write("\n".join(output) + "\n")
    try:
        temporary.chmod(0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Configured .env for https://{hostname}; credentials were preserved or generated privately.")


if __name__ == "__main__":
    main()
