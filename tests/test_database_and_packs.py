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
        "acton_boxborough_harvard",
        "pioneer_valley",
    }
    assert expected.issubset(pack_ids)

    mon = seeded_db.get_destination("mount_monadnock")
    assert mon is not None and len(mon.trails) >= 30
    names = {t.name for t in mon.trails}
    assert {"White Dot Trail", "White Cross Trail", "Pumpelly Trail", "Dublin Trail"} <= names
    assert not any(t.dogs_allowed for t in mon.trails), "dogs are not allowed in Monadnock State Park"

    abh = seeded_db.get_destination("acton_boxborough_harvard")
    assert abh is not None and len(abh.trails) >= 100
    assert {"Holy Hill (yellow blaze)", "Nashua Acton & Boston Railroad Trail"} <= {t.name for t in abh.trails}

    fsf = seeded_db.get_destination("fitchburg_state_forest")
    assert fsf is not None and len(fsf.trails) >= 150
    fsf_names = {t.name for t in fsf.trails}
    assert {"Crow Hill Trail", "Crow Hill Ledges North Climb", "Harrington Trail", "Old Indian Trail"} <= fsf_names
    assert sum(1 for n in fsf_names if n.startswith("Midstate Trail (")) == 5
    assert fsf.min_lat <= 42.395 and fsf.min_lon <= -71.965, "covers all of Princeton"

    pv = seeded_db.get_destination("pioneer_valley")
    assert pv is not None and len(pv.trails) >= 50
    assert any(t.name.startswith("Metacomet-Monadnock") or "New England" in t.name for t in pv.trails)
    assert {"Mount Tom", "Mount Holyoke", "Mount Toby"} <= {p.name for p in pv.pois}

    nh = seeded_db.get_destination("white_mountains_nh")
    assert nh is not None
    assert nh.max_lat >= 44.51, "reaches north to Mount Cabot"
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

