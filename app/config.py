"""
Centralized configuration and path resolution for DataWeb.

All path resolution logic lives here. Other modules import from this module
instead of computing paths from __file__.
"""
import os
from pathlib import Path
from dotenv import load_dotenv


def find_project_root(start: Path | None = None) -> Path:
    """
    Walk up from __file__ until we find pyproject.toml (project marker).
    This works regardless of where the calling module lives.
    """
    cur = (start or Path(__file__)).resolve()
    for p in [cur] + list(cur.parents):
        if (p / "pyproject.toml").exists():
            return p
    raise RuntimeError(
        "Project root not found. Ensure pyproject.toml exists in project root."
    )


# Project root directory (contains pyproject.toml)
ROOT = find_project_root()

# Load environment from project_root/env/.env
ENV_PATH = ROOT / "env" / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


def _resolve_env_path(key: str, default: str) -> Path:
    """Resolve a path from environment variable, relative to ROOT."""
    raw = os.getenv(key, default)
    p = Path(raw)
    return (ROOT / p).resolve() if not p.is_absolute() else p.resolve()


# ─── Folder paths ──────────────────────────────────────────────
INPUT_FOLDER = _resolve_env_path("input_folder", "input_files")
OUTPUT_FOLDER = _resolve_env_path("output_folder", "output_files")
STORAGE_FOLDER = _resolve_env_path("storage_folder", "storage")

# ─── Storage file paths ────────────────────────────────────────
METADATA_PATH = _resolve_env_path("metadata_path", "storage/metadata.json")
DF_REGISTRY_PATH = _resolve_env_path("df_registry_path", "storage/registry.json")
OPERATIONS_PATH = _resolve_env_path("operations_path", "storage/operations.json")
CURRENT_DF_KEY_PATH = _resolve_env_path("current_df_key_path", "storage/current_key.json")
USERS_PATH = _resolve_env_path("users_path", "storage/users.json")

# ─── Security ──────────────────────────────────────────────────
SECRET_KEY = os.getenv("secret_key")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY environment variable is required. Set it in env/.env "
        "(run 'dataweb setup' to generate)."
    )

# ─── Template / static paths ───────────────────────────────────
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"

# ─── Ensure runtime directories exist ──────────────────────────
for d in (INPUT_FOLDER, OUTPUT_FOLDER, STORAGE_FOLDER):
    d.mkdir(parents=True, exist_ok=True)

# ─── Export all as a settings object for convenience ───────────
class Settings:
    """Immutable-like settings container."""
    ROOT = ROOT
    INPUT_FOLDER = INPUT_FOLDER
    OUTPUT_FOLDER = OUTPUT_FOLDER
    STORAGE_FOLDER = STORAGE_FOLDER
    METADATA_PATH = METADATA_PATH
    DF_REGISTRY_PATH = DF_REGISTRY_PATH
    OPERATIONS_PATH = OPERATIONS_PATH
    CURRENT_DF_KEY_PATH = CURRENT_DF_KEY_PATH
    USERS_PATH = USERS_PATH
    SECRET_KEY = SECRET_KEY
    TEMPLATES_DIR = TEMPLATES_DIR
    ENV_PATH = ENV_PATH
    STATIC_DIR = STATIC_DIR

    def __setattr__(self, name, value):
        if name in self.__dict__:
            raise AttributeError(f"Settings.{name} is read-only")
        super().__setattr__(name, value)


settings = Settings()


# ─── Backward-compatible constants (for gradual migration) ─────
# These match the old os.getenv keys used in main.py
BASE_DIR = ROOT