"""Tests for SQLite database, FTS5 search, and all 8 bundled regional destination packs."""
from src.core.database import Database


def test_seven_starter_packs_including_white_mountains(seeded_db: Database) -> None:
    packs = seeded_db.list_destinations()
    pack_ids = {p.id for p in packs}
    expected = {
        "white_mountains_nh",
        "yosemite_valley",
        "acadia_np",
        "shenandoah_central",
        "rocky_mountain_bear_lake",
        "zion_canyon",
        "mount_rainier_paradise",
        "mount_monadnock",
    }
    assert expected.issubset(pack_ids)

    mon = seeded_db.get_destination("mount_monadnock")
    assert mon is not None and len(mon.trails) >= 30
    names = {t.name for t in mon.trails}
    assert {"White Dot Trail", "White Cross Trail", "Pumpelly Trail", "Dublin Trail"} <= names
    assert not any(t.dogs_allowed for t in mon.trails), "dogs are not allowed in Monadnock State Park"

    nh = seeded_db.get_destination("white_mountains_nh")
    assert nh is not None
    assert "White Mountains" in nh.name
    assert len(nh.trails) >= 6
    assert len(nh.pois) >= 5


def test_fts5_search_and_condition_updates(seeded_db: Database) -> None:
    hits = seeded_db.search_trails("Franconia", "white_mountains_nh")
    assert len(hits) >= 1

    target = hits[0]
    seeded_db.update_trail_condition(target.id, "muddy", "Spring snowmelt mud")
    updated = [t for t in seeded_db.list_trails("white_mountains_nh") if t.id == target.id][0]
    assert updated.condition.value == "muddy"
    assert "snowmelt" in updated.condition_note

