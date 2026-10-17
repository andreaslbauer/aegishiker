"""Pytest fixtures for AegisHiker."""
import sqlite3
from pathlib import Path
import pytest

from src.core.database import Database
from src.core.pack_manager import seed_packs_into_db
from src.core.settings import Settings


@pytest.fixture()
def temp_app_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    app_dir = tmp_path / "aegishiker_test_data"
    app_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AEGIS_APP_DIR", str(app_dir))
    monkeypatch.setenv("AEGISHIKER_DATA_DIR", str(app_dir))
    return app_dir


@pytest.fixture()
def seeded_db(temp_app_dir: Path) -> Database:
    db = Database(temp_app_dir / "test.db")
    seed_packs_into_db(db)
    yield db
    db.close()


@pytest.fixture()
def mock_voyager_db(temp_app_dir: Path) -> Path:
    v_dir = temp_app_dir / ".AegisPhotoVoyager"
    v_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir = v_dir / "thumbnails"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    (thumbs_dir / "501_300.jpg").write_bytes(b"\xff\xd8\xff\xe0JFIF")

    db_file = v_dir / "voyager.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(
        """
        CREATE TABLE photos (
            id INTEGER PRIMARY KEY,
            name TEXT,
            full_path TEXT,
            date_taken INTEGER,
            camera_model TEXT,
            lat REAL,
            lon REAL,
            is_starred INTEGER DEFAULT 0,
            is_hidden INTEGER DEFAULT 0,
            is_deleted INTEGER DEFAULT 0
        );
        CREATE TABLE ai_analysis (
            photo_id INTEGER PRIMARY KEY,
            summary TEXT,
            tags_json TEXT,
            mood TEXT
        );
        CREATE TABLE persons (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE faces (id INTEGER PRIMARY KEY, photo_id INTEGER, person_id INTEGER, is_ignored INTEGER DEFAULT 0);

        INSERT INTO photos (id, name, full_path, date_taken, camera_model, lat, lon, is_starred)
        VALUES
            (501, 'little_haystack_ridge.jpg', '/photos/little_haystack_ridge.jpg', 1760700000, 'Canon R5', 44.1405, -71.6446, 1),
            (502, 'mt_lafayette_summit.jpg', '/photos/mt_lafayette_summit.jpg', 1760703600, 'Canon R5', 44.1606, -71.6410, 1),
            (503, 'far_away_beach.jpg', '/photos/far_away_beach.jpg', 1760707200, 'iPhone 16 Pro', 25.7617, -80.1918, 0);

        INSERT INTO ai_analysis (photo_id, summary, tags_json, mood)
        VALUES
            (501, 'Hikers on Little Haystack Mountain along Franconia Ridge', '["ridge", "white_mountains", "granite"]', 'adventurous'),
            (502, 'Summit cairn on Mount Lafayette above treeline', '["summit", "alpine", "nh"]', 'triumphant');

        INSERT INTO persons (id, name) VALUES (1, 'Alice'), (2, 'Bob');
        INSERT INTO faces (id, photo_id, person_id, is_ignored) VALUES (1, 501, 1, 0), (2, 502, 2, 0);
        """
    )
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture()
def test_settings(temp_app_dir: Path) -> Settings:
    return Settings(settings_file=temp_app_dir / "settings.json")
