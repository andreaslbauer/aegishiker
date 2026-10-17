"""Where AegisHiker keeps its files.

User data lives in ~/.aegishiker/ (one SQLite file per user profile, downloaded regional packs,
persisted interactive hike maps, photos, settings, logs, exports). AEGIS_APP_DIR overrides it —
pytest and the package self-test use that to run against a throwaway folder.

Bundled, version-controlled reference data (starter regional trail packs, gear templates,
safety guidelines) lives next to the code; in a PyInstaller build (the snap) under sys._MEIPASS.
"""
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def get_app_dir() -> Path:
    env = os.environ.get("AEGIS_APP_DIR") or os.environ.get("AEGISHIKER_DATA_DIR")
    return Path(env) if env else Path.home() / ".aegishiker"


APP_DIR = get_app_dir()
PROFILES_DIR = APP_DIR / "profiles"
DEFAULT_PROFILE = "default"
PACKS_USER_DIR = APP_DIR / "packs"
MAPS_DIR = APP_DIR / "maps"
LOGS_DIR = APP_DIR / "logs"
LLM_LOG_PATH = LOGS_DIR / "llm.jsonl"
NETWORK_LOG_PATH = LOGS_DIR / "network.jsonl"
SETTINGS_PATH = APP_DIR / "settings.json"
EXPORTS_DIR = APP_DIR / "exports"
PHOTOS_DIR = APP_DIR / "photos"

FROZEN = bool(getattr(sys, "frozen", False))
REPO_ROOT = (
    Path(getattr(sys, "_MEIPASS"))
    if FROZEN and hasattr(sys, "_MEIPASS")
    else Path(__file__).resolve().parent.parent.parent
)
CONFIG_DIR = REPO_ROOT / "config"
DEFAULTS_CONFIG_PATH = CONFIG_DIR / "defaults.json"
DATA_DIR = REPO_ROOT / "data"
DESTINATIONS_CATALOG_PATH = DATA_DIR / "destinations_catalog.json"
GEAR_TEMPLATES_PATH = DATA_DIR / "gear_templates.json"
SAFETY_GUIDELINES_PATH = DATA_DIR / "safety_guidelines.json"
BUNDLED_PACKS_DIR = DATA_DIR / "packs"

HELP_DIR = REPO_ROOT / "src" / "assets" / "help"
WEB_DIR = REPO_ROOT / "src" / "web"
APP_ICON_PATH = REPO_ROOT / "src" / "assets" / "appicon.svg"
IOS_DIR = REPO_ROOT / "ios" / "AegisHiker"
VERSION_PATH = REPO_ROOT / "version.txt"


def refresh_app_dir() -> None:
    """Re-bind module-level paths if AEGIS_APP_DIR changes during test setup."""
    global APP_DIR, PROFILES_DIR, PACKS_USER_DIR, MAPS_DIR, LOGS_DIR
    global LLM_LOG_PATH, NETWORK_LOG_PATH, SETTINGS_PATH, EXPORTS_DIR, PHOTOS_DIR
    APP_DIR = get_app_dir()
    PROFILES_DIR = APP_DIR / "profiles"
    PACKS_USER_DIR = APP_DIR / "packs"
    MAPS_DIR = APP_DIR / "maps"
    LOGS_DIR = APP_DIR / "logs"
    LLM_LOG_PATH = LOGS_DIR / "llm.jsonl"
    NETWORK_LOG_PATH = LOGS_DIR / "network.jsonl"
    SETTINGS_PATH = APP_DIR / "settings.json"
    EXPORTS_DIR = APP_DIR / "exports"
    PHOTOS_DIR = APP_DIR / "photos"


def profile_db_path(profile: str = DEFAULT_PROFILE) -> Path:
    """One SQLite file per user profile."""
    return PROFILES_DIR / f"{profile}.db"


def ensure_app_dirs() -> None:
    refresh_app_dir()
    for d in (APP_DIR, PROFILES_DIR, PACKS_USER_DIR, MAPS_DIR, LOGS_DIR, EXPORTS_DIR, PHOTOS_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create directory {d}: {e}")
            raise


def app_version() -> str:
    try:
        return VERSION_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.1.0"


def get_hike_maps_dir() -> Path:
    ensure_app_dirs()
    return MAPS_DIR


def get_help_dir() -> Path:
    return HELP_DIR


def get_assets_dir() -> Path:
    return REPO_ROOT / "src" / "assets"

