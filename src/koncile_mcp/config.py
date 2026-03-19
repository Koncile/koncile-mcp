"""Configuration loaded from environment variables or config file."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_API_URL = "https://api.koncile.ai"
CONFIG_DIR = Path.home() / ".config" / "koncile"
CONFIG_FILE = CONFIG_DIR / "config"


def _read_config_file() -> dict[str, str]:
    """Read key=value pairs from ~/.config/koncile/config."""
    if not CONFIG_FILE.is_file():
        return {}
    values: dict[str, str] = {}
    for line in CONFIG_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


@dataclass(frozen=True)
class Config:
    api_url: str
    api_key: str
    request_timeout: float

    @classmethod
    def from_env(cls) -> Config:
        file_values = _read_config_file()

        api_url = os.environ.get("KONCILE_API_URL", file_values.get("KONCILE_API_URL", DEFAULT_API_URL)).rstrip("/")
        api_key = os.environ.get("KONCILE_API_KEY", file_values.get("KONCILE_API_KEY", ""))
        timeout = float(os.environ.get("KONCILE_REQUEST_TIMEOUT", file_values.get("KONCILE_REQUEST_TIMEOUT", "120")))

        if not api_key:
            print(
                "Error: KONCILE_API_KEY not found. Set it via environment variable "
                f"or in {CONFIG_FILE}",
                file=sys.stderr,
            )
            sys.exit(1)

        return cls(api_url=api_url, api_key=api_key, request_timeout=timeout)
