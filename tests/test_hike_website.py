"""Hiked-today workflow: trailhead → hike → photos (review / leave out) → website (open or password
protected) → FTP upload."""
import base64
import ftplib
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List

import pytest
from PIL import Image

from src.core.database import Database
from src.core.settings import Settings
from src.hiking.models import HikePhotoPin, HikeSession
from src.hiking.photo_matcher import match_photos_to_trail
from src.hiking.segment_chain import SegmentChain

PACK = "fitchburg_state_forest"


def _crow_hill_loop(db: Database):
    """Parking → Crow Hill Trail → North Climb → Midstate Trail on the ridge → South Climb → back."""
    pack = db.get_destination_pack(PACK)
    by = {t.name: t for t in pack.trails}
    chain = SegmentChain(pack.trails, [(p.name, p.lon, p.lat) for p in pack.pois])
    chain.start(by["Crow Hill Trail"])
    for name in ("Crow Hill Ledges North Climb", "Midstate Trail (Leominster State Forest)",
                 "Crow Hill Ledges South Climb", "Crow Hill Trail"):
        options = [c for c in chain.candidates() if c.trail_name == name and not c.turnaround]
        assert options, f"{name} continues the hike (options: {[c.trail_name for c in chain.candidates()]})"
        chain.apply(options[0])
    return pack, chain.to_route("", PACK)


def _photos(route, tmp_path: Path, n: int = 6) -> List[HikePhotoPin]:
    rows = []
    for k in range(n):
        i = int((k + 0.5) / n * (len(route.coords_3d) - 1))
        lon, lat, _e = route.coords_3d[i]
        path = tmp_path / f"IMG_{k:04d}.jpg"
        Image.new("RGB", (640, 480), (40 + 30 * k, 120, 160)).save(path)
        rows.append({"voyager_photo_id": 500 + k, "photo_path": str(path), "filename": path.name, "lat": lat,
                     "lon": lon, "date_taken": int(datetime(2026, 10, 18, 9, 30).timestamp()) + 600 * k,
                     "ai_summary": f"View {k}", "people": ["Maria"] if k == 2 else []})
    return match_photos_to_trail(route.coords_3d, rows, corridor_m=60)


def _session(route) -> HikeSession:
    s = HikeSession(route_id=route.id, destination_id=PACK, title="Crow Hill Ledges", status="traced_hiked",
                    device_source="traced_desktop", started_at="2026-10-18T09:30:00", ended_at="2026-10-18T11:10:00",
                    actual_distance_m=route.distance_m, actual_elevation_gain_m=route.elevation_gain_m)
    s.conditions_logged.update({"companions": "Tom", "weather": "Clear sky", "temp_high_c": "10.0"})
    return s


def test_short_climbs_connect_to_the_ridge_and_legs_are_named(seeded_db: Database) -> None:
    from src.hiking.hike_report import build_hike_summary

    pack, route = _crow_hill_loop(seeded_db)
    summary = build_hike_summary(None, route, pack, [])
    names = [t["name"] for t in summary["trails"]]
    assert names == ["Crow Hill Trail", "Crow Hill Ledges North Climb", "Midstate Trail (Leominster State Forest)",
                     "Crow Hill Ledges South Climb", "Crow Hill Trail"]
    assert abs(sum(t["distance_m"] for t in summary["trails"]) - route.distance_m) < 1.0
    assert summary["start_place"] == "Crow Hill Pond Parking"


def test_summary_people_weather_and_times(seeded_db: Database, tmp_path: Path) -> None:
    from src.hiking.hike_report import build_hike_summary

    pack, route = _crow_hill_loop(seeded_db)
    pins = _photos(route, tmp_path)
    summary = build_hike_summary(_session(route), route, pack, pins)
    assert summary["people"] == ["Tom", "Maria"], "entered companions first, then people in the photos"
    assert summary["weather"]["summary"] == "Clear sky" and summary["weather"]["temp_high_c"] == 10.0
    assert summary["elapsed_min"] == 100.0
    assert summary["photo_count"] == len(pins)


def test_removed_photos_stay_out_of_the_hike(seeded_db: Database, tmp_path: Path) -> None:
    from src.hiking.photo_matcher import photo_keys

    _pack, route = _crow_hill_loop(seeded_db)
    seeded_db.upsert_route(route)
    session = _session(route)
    pins = _photos(route, tmp_path)
    seeded_db.save_session(session)
    for p in pins:
        p.session_id = session.id
        seeded_db.save_photo_pin(p)
    dup = pins[0].model_copy(update={"id": "dup-copy"})  # the same photo saved twice
    seeded_db.save_photo_pin(dup)

    seeded_db.remove_photos_from_session(session.id, [pins[0], pins[1]])
    left = seeded_db.get_photo_pins(session.id)
    assert {p.voyager_photo_id for p in left} == {p.voyager_photo_id for p in pins[2:]}
    assert seeded_db.get_session(session.id).stats.photo_count == len(pins) - 2
    excluded = seeded_db.excluded_photo_keys(session.id)
    rescanned = [p for p in pins if not excluded.intersection(photo_keys(p))]
    assert rescanned == pins[2:], "a rescan of AegisPhotoVoyager leaves the removed photos out"


def test_export_open_site(seeded_db: Database, tmp_path: Path) -> None:
    from src.core.hike_site import SiteOptions, export_hike_site

    pack, route = _crow_hill_loop(seeded_db)
    pins = _photos(route, tmp_path)
    out = tmp_path / "site"
    report = export_hike_site(out, route, _session(route), pack, pins, SiteOptions(units="us"))
    assert report["photos"] == len(pins) and not report["skipped_photos"]
    for name in ("index.html", "site.js", "site.css", "hike.js", "relief.png"):
        assert (out / name).exists(), name
    data = json.loads((out / "hike.js").read_text().split("=", 1)[1].rstrip(";\n"))
    assert data["summary"]["title"] == "Crow Hill Ledges"
    assert [t["name"] for t in data["summary"]["trails"]][1] == "Crow Hill Ledges North Climb"
    assert len(data["photos"]) == len(pins) and data["photos"][0]["trail"] == "Crow Hill Trail"
    assert data["replay"]["mesh"]["cols"] > 10 and data["replay"]["stops"]
    light, mesh = data["replay"]["light"], data["replay"]["mesh"]
    assert len(light) >= 2 and all(k["ts"] for k in light), "light keyframes over the hike's morning"
    assert light[0]["elev"] > 0 and isinstance(data["replay"]["tz"], int)
    assert light[0]["cloud"] < 0.1, "the test hike's weather: clear sky"
    clouds = data["replay"]["clouds"]
    assert len(clouds["field"]) == clouds["n"] ** 2 and max(clouds["field"]) == 255
    assert len(mesh["ground"]) == (mesh["cols"] - 1) * (mesh["rows"] - 1) * 3, "unlit colours, lit in the browser"
    veg, mesh = data["replay"]["veg"], data["replay"]["mesh"]
    assert veg["season"] == "autumn", "the hike's month (October) sets the foliage"
    assert len(veg["grid"]) == (mesh["cols"] - 1) * (mesh["rows"] - 1) * veg["sub"] ** 2
    habitats = {veg["habitats"][ord(ch) - 48][0] for ch in veg["grid"]}
    assert habitats and any(h[2] for h in veg["habitats"]), "the browser replay grows region plants"
    assert data["basemap"]["contours"] and data["basemap"]["lines"]
    with Image.open(out / data["photos"][0]["src"]) as im:
        assert not im.getexif().get(0x8825), "no GPS EXIF in published photos"
    assert "Crow Hill Ledges" in (out / "index.html").read_text()

    # Re-exporting into the same folder is fine; a folder with other files is refused
    export_hike_site(out, route, _session(route), pack, pins[:2])
    assert len(list((out / "photos").iterdir())) == 2
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.txt").write_text("mine")
    from src.core.hike_site import SiteExportError

    with pytest.raises(SiteExportError):
        export_hike_site(other, route, _session(route), pack, pins)


def test_export_can_leave_out_photos_with_people(seeded_db: Database, tmp_path: Path) -> None:
    from src.core.hike_site import SiteOptions, export_hike_site
    from src.hiking.hike_info import shows_people

    pack, route = _crow_hill_loop(seeded_db)
    pins = _photos(route, tmp_path)
    pins[4].ai_tags = ["summit", "Hiker"]
    pins[5].ai_tags = ["rock face", "man-made cairn"]
    assert [shows_people(p) for p in pins] == [False, False, True, False, True, False]
    out = tmp_path / "site"
    report = export_hike_site(out, route, _session(route), pack, pins, SiteOptions(skip_people=True))
    assert report["photos"] == 4 and report["people_photos_left_out"] == 2
    data = json.loads((out / "hike.js").read_text().split("=", 1)[1].rstrip(";\n"))
    assert not any(p["people"] for p in data["photos"])
    assert "Maria" not in json.dumps(data["summary"]), "people only seen in left-out photos aren't named"


def test_export_password_protected_site(seeded_db: Database, tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from src.core.hike_site import SiteOptions, export_hike_site

    pack, route = _crow_hill_loop(seeded_db)
    pins = _photos(route, tmp_path)
    out = tmp_path / "locked"
    export_hike_site(out, route, _session(route), pack, pins, SiteOptions(password="trail-mix"))
    hike_js = (out / "hike.js").read_text()
    assert "Crow Hill" not in hike_js and "Crow Hill" not in (out / "index.html").read_text()
    assert not list((out / "photos").glob("*.jpg")), "only encrypted photos are published"
    locked = json.loads(hike_js.split("=", 1)[1].rstrip(";\n"))
    key = hashlib.pbkdf2_hmac("sha256", b"trail-mix", base64.b64decode(locked["salt"]), locked["iter"], dklen=32)
    data = json.loads(AESGCM(key).decrypt(base64.b64decode(locked["iv"]), base64.b64decode(locked["data"]), None))
    assert data["summary"]["title"] == "Crow Hill Ledges" and data["meta"]["protected"]
    blob_js = (out / (data["photos"][0]["src"] + ".js")).read_text()
    blob = json.loads(blob_js[blob_js.index(",") + 1: blob_js.rindex(")")])
    jpeg = AESGCM(key).decrypt(base64.b64decode(blob["iv"]), base64.b64decode(blob["data"]), None)
    assert jpeg[:2] == b"\xff\xd8"
    wrong = hashlib.pbkdf2_hmac("sha256", b"wrong", base64.b64decode(locked["salt"]), locked["iter"], dklen=32)
    with pytest.raises(Exception):
        AESGCM(wrong).decrypt(base64.b64decode(locked["iv"]), base64.b64decode(locked["data"]), None)


class _FakeFTP:
    instances: list = []

    def __init__(self, timeout=None):
        self.stored: list = []
        self.dirs: list = []
        _FakeFTP.instances.append(self)

    def connect(self, host, port):
        self.host = host

    def login(self, user, pw):
        if pw != "secret":
            raise ftplib.error_perm("530 Login incorrect")

    def prot_p(self):
        self.protected = True

    def set_pasv(self, val):
        pass

    def mkd(self, path):
        if path in self.dirs:
            raise ftplib.error_perm("550 exists")
        self.dirs.append(path)

    def storbinary(self, cmd, fh):
        self.stored.append((cmd[5:], len(fh.read())))

    def getwelcome(self):
        return "220 Fake FTP"

    def quit(self):
        pass

    close = quit


def test_ftp_upload_sends_every_file_index_last(tmp_path: Path, monkeypatch) -> None:
    from src.core import ftp_upload

    site = tmp_path / "site"
    (site / "photos").mkdir(parents=True)
    (site / "index.html").write_text("<html>")
    (site / "hike.js").write_text("x")
    (site / "photos" / "p001.jpg").write_bytes(b"\xff\xd8")
    monkeypatch.setattr(ftp_upload.ftplib, "FTP_TLS", _FakeFTP)
    target = ftp_upload.FtpTarget(host="example.com", username="me", password="secret", remote_dir="/www/hikes/crow")
    assert ftp_upload.upload_folder(site, target) == 3
    ftp = _FakeFTP.instances[-1]
    assert ftp.protected, "transfers are encrypted too"
    assert [p for p, _n in ftp.stored][-1] == "/www/hikes/crow/index.html"
    assert "/www/hikes/crow/photos" in ftp.dirs
    with pytest.raises(RuntimeError, match="log in"):
        ftp_upload.upload_folder(site, ftp_upload.FtpTarget(host="example.com", username="me", password="nope"))


def test_weather_summary_for_the_hike_hours() -> None:
    from src.core.weather import summarize_hourly

    hours = [f"2026-10-18T{h:02d}:00" for h in range(24)]
    data = {"hourly": {"time": hours, "temperature_2m": [h * 0.5 for h in range(24)],
                       "precipitation": [0.2 if h == 10 else 0.0 for h in range(24)],
                       "weather_code": [61 if h == 10 else 1 for h in range(24)],
                       "wind_speed_10m": [5 + h for h in range(24)]}}
    out = summarize_hourly(data, datetime(2026, 10, 18, 9, 30), datetime(2026, 10, 18, 12, 0))
    assert out["temp_low_c"] == "4.5" and out["temp_high_c"] == "6.0"
    assert out["precip_mm"] == "0.2" and out["wind_kmh"] == "17"
    assert out["weather"] == "Mainly clear, light rain at times"


def test_explore_trailhead_starts_hike_in_composer(seeded_db: Database, tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.app_state import AppState
    from src.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    state = AppState(db=seeded_db, settings=Settings(settings_file=tmp_path / "settings.json"))
    state.set_active_destination(PACK)
    win = MainWindow(app_state=state)
    win.show()
    explore = win.explore_panel
    labels = [lbl for lbl in explore._places if "Crow Hill Pond Parking" in lbl]
    assert labels, "places are offered in the type-ahead"
    assert explore.select_place(labels[0])
    names = [t.name for t in explore._filtered_trails]
    assert names and names[0] == "Crow Hill Trail", "the trail starting at the parking lot comes first"
    explore._start_at_place()
    app.processEvents()
    builder = win.composer_panel.segment_builder
    assert win.tabs.currentWidget() is win.composer_panel
    assert [leg.trail.name for leg in builder.chain.legs] == ["Crow Hill Trail"]
    assert any(c.trail_name == "Crow Hill Ledges North Climb" for c in builder.chain.candidates())
    win.hide()
    win.deleteLater()


def test_photo_review_leaves_photos_out(seeded_db: Database, tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from src.gui.dialogs.photo_review_dialog import PhotoReviewDialog

    app = QApplication.instance() or QApplication([])
    _pack, route = _crow_hill_loop(seeded_db)
    pins = _photos(route, tmp_path)
    dlg = PhotoReviewDialog(pins, "us", "Crow Hill")
    dlg.show()
    app.processEvents()
    dlg.grid.item(1).setCheckState(Qt.CheckState.Unchecked)
    dlg.grid.setCurrentRow(3)
    dlg._set_selected(False)
    assert [p.id for p in dlg.removed_pins()] == [pins[1].id, pins[3].id]
    assert len(dlg.kept_pins()) == len(pins) - 2
    dlg.set_kept({p.id for p in pins})
    assert not dlg.removed_pins()
    dlg.deleteLater()


def test_ftp_settings_are_remembered_but_not_the_password(seeded_db: Database, tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.app_state import AppState
    from src.gui.dialogs.hike_site_dialog import HikeSiteDialog

    QApplication.instance() or QApplication([])
    _pack, route = _crow_hill_loop(seeded_db)
    settings_file = tmp_path / "settings.json"
    state = AppState(db=seeded_db, settings=Settings(settings_file=settings_file))
    session = _session(route)
    dlg = HikeSiteDialog(state, session, route, [])
    dlg.host_edit.setText("ftp.example.com")
    dlg.port_spin.setValue(2121)
    dlg.user_edit.setText("hiker")
    dlg.ftp_pw.setText("s3cret-pw")
    dlg.tls_chk.setChecked(False)
    dlg.url_edit.setText(f"https://example.com/hikes/{dlg.slug}/")
    dlg.remote_edit.setText(f"/www/hikes/{dlg.slug}")
    dlg.reject()  # closed without testing or uploading
    dlg.deleteLater()

    assert "s3cret-pw" not in settings_file.read_text(), "the FTP password is never saved"
    again = HikeSiteDialog(AppState(db=seeded_db, settings=Settings(settings_file=settings_file)), session, route, [])
    assert (again.host_edit.text(), again.port_spin.value(), again.user_edit.text()) == ("ftp.example.com", 2121, "hiker")
    assert not again.tls_chk.isChecked() and not again.ftp_pw.text()
    assert again.url_edit.text() == f"https://example.com/hikes/{dlg.slug}/"
    assert again.remote_edit.text() == f"/www/hikes/{dlg.slug}"
    again.deleteLater()
