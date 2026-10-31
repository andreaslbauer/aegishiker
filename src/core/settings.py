"""User settings, persisted as JSON in the app directory.

Kept Qt-free so the hiking core, tests, and CLI tools can read/write settings.
"""
import json
import logging
import threading
from typing import Any, Dict

from . import paths

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen2.5:7b"
FALLBACK_MODEL = "qwen2.5:3b"

DEFAULTS: Dict[str, Any] = {
    "ollama_host": "",                  # blank = localhost:11434
    "allow_remote_ollama": False,       # refuse non-local host unless user opts in
    "interview_model": DEFAULT_MODEL,
    "chat_model": DEFAULT_MODEL,
    "update_check": False,              # never calls home
    "profile": "default",
    "active_pack_id": "white_mountains_nh",
    "last_route_id": "",
    "last_session_id": "",
    "units": "us",                      # us (mi, ft, lb, °F) | metric (km, m, kg, °C)
    "paper_size": "letter",             # letter | a4
    "theme": "dark",                    # dark | light
    "font_scale": 1.0,                  # UI font scale multiplier (0.9..1.3)
    "hiker_name": "Trail Hiker",
    "flat_speed_kmh": 4.8,
    "climb_rate_m_per_hr": 600.0,
    "body_weight_kg": 75.0,
    "pack_weight_kg": 6.0,
    "fitness_level": "moderate",        # leisurely | moderate | athletic | mountaineer
    "elevation_hysteresis_m": 3.0,
    "off_trail_threshold_m": 50.0,
    "photo_corridor_m": 250.0,
    "voyager_db_path": "",              # blank = auto-detect ~/.AegisPhotoVoyager/voyager.db
    "trail_server_port": 0,             # 0 = pick a free LAN port each session
    "phone_idle_hours": 8,              # keep LAN trail server open for up to 8 hours
    "unit_system": "metric",
    "base_speed_kmh": 4.5,
    "default_pack_weight_kg": 6.0,
    "hiker_weight_kg": 75.0,
    "photo_match_corridor_m": 250.0,
    "lan_server_port": 0,
    "ollama_model": DEFAULT_MODEL,
    "emergency_contact": "",
    # Window & UI state restored on the next launch (written by the GUI)
    "window_geometry": "",              # base64 QMainWindow.saveGeometry()
    "window_state": "",                 # base64 QMainWindow.saveState() (docks, toolbars)
    "active_tab": 0,
    "ui_state": {},                     # splitter positions and per-panel selections
    "include_minor_paths": True,        # route builder: offer minor paths too
    "replay_show_photos": True,         # 3D replays stop at photos
    "replay_include_people": True,      # …including photos with people in them
    "replay_one_per_cluster": False,    # one photo per spot of many similar photos (Voyager data)
    "replay_speed": 1.0,                # playback speed a replay starts at
    # Hike websites and their FTP publishing (never the FTP password)
    "hike_site_skip_people": False,
    "hike_site_dirs": {},               # session id -> local website folder
    "hike_site_urls": {},               # session id -> website URL
    "hike_site_remote_dirs": {},        # session id -> server folder
    "ftp_host": "",
    "ftp_port": 21,
    "ftp_user": "",
    "ftp_tls": True,
    "ftp_remote_base": "/",             # new websites go into a folder under this
    "ftp_site_base_url": "",            # …and are on the web under this URL
}

CHOICES: Dict[str, tuple] = {
    "units": ("us", "metric"),
    "unit_system": ("us", "metric", "imperial"),
    "paper_size": ("letter", "a4"),
    "theme": ("dark", "light"),
    "fitness_level": ("leisurely", "moderate", "athletic", "mountaineer"),
    "replay_speed": (0.5, 1.0, 2.0, 4.0),
}

_lock = threading.Lock()


def load() -> Dict[str, Any]:
    """Current settings with defaults filled in. Corrupt file falls back to defaults."""
    paths.refresh_app_dir()
    values = dict(DEFAULTS)
    try:
        if paths.SETTINGS_PATH.exists():
            stored = json.loads(paths.SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                values.update({k: v for k, v in stored.items() if k in DEFAULTS})
            else:
                logger.warning(f"{paths.SETTINGS_PATH} is not an object; using defaults.")
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Could not read {paths.SETTINGS_PATH} ({e}); using defaults.")
    for key in ("interview_model", "chat_model"):
        if not str(values.get(key) or "").strip():
            values[key] = DEFAULTS[key]
    for key, allowed in CHOICES.items():
        if values.get(key) not in allowed:
            values[key] = DEFAULTS[key]
    return values


def get(key: str, default: Any = None) -> Any:
    vals = load()
    if key in vals:
        return vals[key]
    return DEFAULTS.get(key, default)


def save(**changes: Any) -> Dict[str, Any]:
    paths.refresh_app_dir()
    with _lock:
        values = load()
        values.update(changes)
        try:
            paths.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            paths.SETTINGS_PATH.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
        except OSError as e:
            logger.error(f"Could not write {paths.SETTINGS_PATH}: {e}")
    return values


class Settings:
    """Object-oriented settings wrapper backed by settings.json."""

    def __init__(self, settings_file: Any = None) -> None:
        self._file = paths.Path(settings_file) if settings_file else None
        self._data: Dict[str, Any] = load()
        if self._file and self._file.exists():
            try:
                self._data.update(json.loads(self._file.read_text(encoding="utf-8")))
            except Exception:
                pass

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            val = self._data[key]
            if key == "unit_system" and val == "imperial":
                return "us"
            return val
        return DEFAULTS.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        if key == "unit_system":
            self._data["units"] = "us" if value in ("us", "imperial") else "metric"
        elif key == "units":
            self._data["unit_system"] = value
        elif key == "voyager_db_path":
            save(voyager_db_path=value)

    def save(self) -> None:
        target = self._file or paths.SETTINGS_PATH
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(self._data, indent=2) + "\n", encoding="utf-8")
        except OSError as e:
            logger.error(f"Could not save settings to {target}: {e}")

