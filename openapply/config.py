"""Where things live on disk, and how config files are loaded.

Step by step:
1. PROJECT_ROOT is the folder that holds `config/`, `data/`, `generated/`, `logs/`.
   Override it with the OPENAPPLY_HOME environment variable.
2. `.env` is loaded once so ANTHROPIC_API_KEY, GOOGLE_* settings, etc. are visible.
3. `load_yaml` reads a YAML file; `load_profile` reads config/profile.yaml and falls
   back to the committed example so the tool still runs on a fresh clone.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(os.environ.get("OPENAPPLY_HOME", Path(__file__).resolve().parents[1]))
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
GENERATED_DIR = PROJECT_ROOT / "generated"
LOGS_DIR = PROJECT_ROOT / "logs"
DB_PATH = DATA_DIR / "openapply.db"

load_dotenv(PROJECT_ROOT / ".env")


def load_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_profile() -> dict[str, Any]:
    """Read config/profile.yaml (personal, gitignored) or the example as a fallback."""
    real = CONFIG_DIR / "profile.yaml"
    return load_yaml(real if real.exists() else CONFIG_DIR / "profile.yaml.example")


def load_companies() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "companies.yaml")


def load_filters() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "filters.yaml")
