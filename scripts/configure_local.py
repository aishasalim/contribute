#!/usr/bin/env python3
"""Create the git-ignored local environment without printing secrets."""

from __future__ import annotations

import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"


def main() -> None:
    if TARGET.exists():
        print(f"{TARGET} already exists; left unchanged")
        return
    password = secrets.token_urlsafe(32)
    api_token = secrets.token_urlsafe(48)
    content = EXAMPLE.read_text()
    content = content.replace("change-me", password)
    content = content.replace("replace-with-a-long-random-token", api_token)
    TARGET.write_text(content)
    TARGET.chmod(0o600)
    print(f"Created {TARGET} with generated local credentials (AUTO_SUBMIT=false)")


if __name__ == "__main__":
    main()
