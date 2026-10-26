"""Create a website for a recorded hike in a local folder, optionally password protected, and
publish it to a web server over FTP/FTPS. See src/core/hike_site.py for what the site contains."""
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.core import paths
from src.core.ftp_upload import FtpTarget, test_connection, upload_folder
from src.core.hike_site import SITE_MARKER, SiteOptions, default_site_dir, export_hike_site, slugify
from src.gui.app_state import AppState
from src.gui.busy import run_in_background
from src.hiking.hike_info import hike_title
from src.hiking.models import ComposedRoute, HikePhotoPin, HikeSession
from src.hiking.photo_matcher import dedupe_pins


def _open_path(path: Path) -> None:
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        webbrowser.open(path.as_uri())


class HikeSiteDialog(QDialog):
    def __init__(self, app_state: AppState, session: HikeSession, route: ComposedRoute,
                 pins: List[HikePhotoPin], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.session = session
        self.route = route
        self.pins = dedupe_pins(list(pins))
        self.title = hike_title(session, route)
        self.slug = slugify(f"{(session.started_at or '')[:10]} {self.title}")
        self.setWindowTitle(f"Hike Website — {self.title}")
        self.resize(760, 640)
        s = app_state.settings
        col = QVBoxLayout(self)

        intro = QLabel(
            "Creates a website about this hike: a summary page with the numbers, trails, people and weather, "
            "<b>Play the Hike</b> (the 3D replay), a photo gallery and an interactive trail map with the "
            "elevation profile. It works opened from the folder or on any web server."
        )
        intro.setWordWrap(True)
        col.addWidget(intro)

        # ── create ────────────────────────────────────────────────────────
        box = QGroupBox("1 · Create the website")
        form = QFormLayout(box)
        saved_dirs = s.get("hike_site_dirs") or {}
        self.folder_edit = QLineEdit(str(saved_dirs.get(session.id) or default_site_dir(self.title, (session.started_at or "")[:10])))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        frow = QHBoxLayout()
        frow.addWidget(self.folder_edit, 1)
        frow.addWidget(browse)
        form.addRow("Folder:", frow)

        self.units_combo = QComboBox()
        self.units_combo.addItem("Miles & feet", "us")
        self.units_combo.addItem("Kilometres & metres", "metric")
        self.units_combo.setCurrentIndex(0 if str(s.get("unit_system", "metric")) != "metric" else 1)
        self.units_combo.setToolTip("What visitors see first; they can switch on the page")
        form.addRow("Units:", self.units_combo)

        self.size_combo = QComboBox()
        for label, px in (("Standard (1800 px, good for most screens)", 1800), ("Large (2560 px)", 2560),
                          ("Small (1280 px, quicker uploads)", 1280)):
            self.size_combo.addItem(label, px)
        form.addRow("Photo size:", self.size_combo)

        self.skip_people_chk = QCheckBox("Leave out photos with people in them")
        self.skip_people_chk.setToolTip(
            "Skips photos where AegisPhotoVoyager recognised someone, or whose AI tags mention a person "
            "(person, hiker, selfie…). Unrecognised faces without such tags can still slip through."
        )
        self.skip_people_chk.setChecked(bool(s.get("hike_site_skip_people", False)))
        form.addRow("", self.skip_people_chk)

        self.protect_chk = QCheckBox("Password protect the website")
        self.protect_chk.setToolTip(
            "The hike, its map and every photo are encrypted; visitors need the password to see anything."
        )
        form.addRow("", self.protect_chk)
        self.pw1 = QLineEdit()
        self.pw1.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw1.setPlaceholderText("Password for visitors")
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw2.setPlaceholderText("Repeat the password")
        prow = QHBoxLayout()
        prow.addWidget(self.pw1)
        prow.addWidget(self.pw2)
        form.addRow("Password:", prow)
        self.pw_note = QLabel("Protected pages must be served over <b>https://</b> (or opened from the folder): "
                              "browsers only decrypt on secure connections. The password is not stored.")
        self.pw_note.setWordWrap(True)
        self.pw_note.setObjectName("Muted")
        form.addRow("", self.pw_note)
        self.protect_chk.toggled.connect(self._sync)

        crow = QHBoxLayout()
        self.create_btn = QPushButton("Create Website")
        self.create_btn.setObjectName("PrimaryButton")
        self.create_btn.clicked.connect(self.create_site)
        self.open_btn = QPushButton("Open in Browser")
        self.open_btn.clicked.connect(lambda: webbrowser.open((self._folder() / "index.html").as_uri()))
        self.folder_btn = QPushButton("Open Folder")
        self.folder_btn.clicked.connect(lambda: _open_path(self._folder()))
        crow.addWidget(self.create_btn)
        crow.addStretch(1)
        crow.addWidget(self.open_btn)
        crow.addWidget(self.folder_btn)
        form.addRow(crow)
        self.result_lbl = QLabel("")
        self.result_lbl.setWordWrap(True)
        form.addRow(self.result_lbl)
        col.addWidget(box)

        # ── publish ───────────────────────────────────────────────────────
        pub = QGroupBox("2 · Publish to your web server (FTP)")
        pform = QFormLayout(pub)
        sites = s.get("hike_site_urls") or {}
        base_url = str(s.get("ftp_site_base_url", "") or "")
        self.url_edit = QLineEdit(str(sites.get(session.id) or (base_url.rstrip("/") + "/" + self.slug + "/" if base_url else "")))
        self.url_edit.setPlaceholderText("https://example.com/hikes/" + self.slug + "/")
        self.url_edit.setToolTip("Where the website will be on the web once uploaded")
        pform.addRow("Website URL:", self.url_edit)
        self.host_edit = QLineEdit(str(s.get("ftp_host", "") or ""))
        self.host_edit.setPlaceholderText("ftp.example.com (blank: the URL's server)")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(s.get("ftp_port", 21) or 21))
        hrow = QHBoxLayout()
        hrow.addWidget(self.host_edit, 1)
        hrow.addWidget(QLabel("Port"))
        hrow.addWidget(self.port_spin)
        pform.addRow("FTP server:", hrow)
        self.user_edit = QLineEdit(str(s.get("ftp_user", "") or ""))
        pform.addRow("Username:", self.user_edit)
        self.ftp_pw = QLineEdit()
        self.ftp_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.ftp_pw.setPlaceholderText("FTP password (used for this upload only, never saved)")
        pform.addRow("FTP password:", self.ftp_pw)
        base_dir = str(s.get("ftp_remote_base", "") or "/")
        remote_dirs = s.get("hike_site_remote_dirs") or {}
        self.remote_edit = QLineEdit(str(remote_dirs.get(session.id) or (base_dir.rstrip("/") + "/" + self.slug)))
        self.remote_edit.setToolTip("Folder on the server the website goes into (created if missing)")
        pform.addRow("Server folder:", self.remote_edit)
        self.tls_chk = QCheckBox("Encrypt the connection (FTPS / FTP over TLS) — recommended")
        self.tls_chk.setChecked(bool(s.get("ftp_tls", True)))
        pform.addRow("", self.tls_chk)
        urow = QHBoxLayout()
        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self.test_ftp)
        self.upload_btn = QPushButton("Upload Website")
        self.upload_btn.setObjectName("PrimaryButton")
        self.upload_btn.clicked.connect(self.upload)
        urow.addWidget(test_btn)
        urow.addStretch(1)
        urow.addWidget(self.upload_btn)
        pform.addRow(urow)
        self.link_lbl = QLabel("")
        self.link_lbl.setOpenExternalLinks(True)
        self.link_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.link_lbl.setWordWrap(True)
        pform.addRow(self.link_lbl)
        col.addWidget(pub)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        col.addLayout(close_row)
        self._sync()

    # ── helpers ───────────────────────────────────────────────────────────
    def _folder(self) -> Path:
        return Path(self.folder_edit.text().strip()).expanduser()

    def _has_site(self) -> bool:
        f = self._folder()
        return (f / "index.html").exists() and (f / SITE_MARKER).exists()

    def _sync(self) -> None:
        on = self.protect_chk.isChecked()
        for w in (self.pw1, self.pw2, self.pw_note):
            w.setEnabled(on)
        has = self._has_site()
        self.open_btn.setEnabled(has)
        self.folder_btn.setEnabled(has)
        self.upload_btn.setEnabled(has)

    def _browse(self) -> None:
        start = self.folder_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Folder for the hike website", start)
        if chosen:
            self.folder_edit.setText(chosen)
            self._sync()

    def _remember(self, key: str, value) -> None:
        mapping = dict(self.app_state.settings.get(key) or {})
        mapping[self.session.id] = value
        self.app_state.settings.set(key, mapping)

    # ── create ────────────────────────────────────────────────────────────
    def create_site(self) -> None:
        password = ""
        if self.protect_chk.isChecked():
            password = self.pw1.text()
            if len(password) < 6:
                QMessageBox.warning(self, "Password", "Use a password of at least 6 characters.")
                return
            if password != self.pw2.text():
                QMessageBox.warning(self, "Password", "The two passwords don't match.")
                return
        folder = self._folder()
        if not self.folder_edit.text().strip():
            return
        pack = self.app_state.db.get_destination_pack(self.route.destination_id)
        options = SiteOptions(password=password, units=str(self.units_combo.currentData()),
                              photo_px=int(self.size_combo.currentData()),
                              skip_people=self.skip_people_chk.isChecked())
        try:
            report = run_in_background(
                self, "Creating the hike website…", export_hike_site, folder, self.route, self.session, pack,
                self.pins, options, title="Hike Website", pass_progress=True, app_version=paths.app_version(),
            )
        except Exception as e:
            QMessageBox.warning(self, "Hike Website", f"Could not create the website:\n\n{e}")
            return
        self._remember("hike_site_dirs", str(folder))
        self.app_state.settings.set("hike_site_skip_people", self.skip_people_chk.isChecked())
        self.app_state.settings.save()
        skipped = report.get("skipped_photos") or []
        lock = " · 🔒 password protected" if report.get("protected") else ""
        text = (f"✓ Created with {report['photos']} photo{'s' if report['photos'] != 1 else ''} "
                f"({report['bytes'] / 1e6:.1f} MB){lock}.<br><span style='color:#98B2A1'>{folder}</span>")
        if report.get("people_photos_left_out"):
            n = report["people_photos_left_out"]
            text += f"<br>{n} photo{'s' if n != 1 else ''} with people left out."
        if skipped:
            text += (f"<br><span style='color:#F39C12'>{len(skipped)} photo(s) could not be read and were left out: "
                     f"{', '.join(skipped[:4])}{'…' if len(skipped) > 4 else ''}</span>")
        self.result_lbl.setText(text)
        self._sync()
        self.app_state.status_message.emit(f"Hike website created in {folder}")

    # ── publish ───────────────────────────────────────────────────────────
    def _target(self) -> Optional[FtpTarget]:
        self._save_ftp_settings()  # remembered even when the login then fails
        host = self.host_edit.text().strip()
        url = self.url_edit.text().strip()
        if not host and url:
            host = urlparse(url if "://" in url else "https://" + url).hostname or ""
        if not host:
            QMessageBox.information(self, "FTP", "Enter the FTP server (or the website URL it belongs to).")
            return None
        if not self.user_edit.text().strip() or not self.ftp_pw.text():
            QMessageBox.information(self, "FTP", "Enter the FTP username and password.")
            return None
        if not self.tls_chk.isChecked():
            answer = QMessageBox.question(
                self, "Unencrypted FTP",
                "Without encryption the FTP password and the files travel over the network in plain text. "
                "Continue anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
        return FtpTarget(host=host, port=int(self.port_spin.value()), username=self.user_edit.text().strip(),
                         password=self.ftp_pw.text(), remote_dir=self.remote_edit.text().strip() or "/",
                         use_tls=self.tls_chk.isChecked())

    def _save_ftp_settings(self) -> None:
        """Remember the server, port, user, encryption, folder and URL (never the password)."""
        s = self.app_state.settings
        s.set("ftp_host", self.host_edit.text().strip())
        s.set("ftp_port", int(self.port_spin.value()))
        s.set("ftp_user", self.user_edit.text().strip())
        s.set("ftp_tls", self.tls_chk.isChecked())
        remote = self.remote_edit.text().strip()
        if remote.rstrip("/").endswith(self.slug):
            s.set("ftp_remote_base", remote.rstrip("/")[: -len(self.slug)].rstrip("/") or "/")
        url = self.url_edit.text().strip()
        if url.rstrip("/").endswith(self.slug):
            s.set("ftp_site_base_url", url.rstrip("/")[: -len(self.slug)].rstrip("/"))
        self._remember("hike_site_urls", url)
        self._remember("hike_site_remote_dirs", remote)
        s.save()

    def test_ftp(self) -> None:
        target = self._target()
        if target is None:
            return
        try:
            welcome = run_in_background(self, f"Connecting to {target.host}…", test_connection, target, title="FTP")
        except Exception as e:
            QMessageBox.warning(self, "FTP", f"Could not log in to {target.host}:\n\n{e}")
            return
        QMessageBox.information(self, "FTP", f"Logged in to {target.host}.\n\n{welcome}")

    def upload(self) -> None:
        if not self._has_site():
            QMessageBox.information(self, "Upload", "Create the website first.")
            return
        target = self._target()
        if target is None:
            return
        try:
            sent = run_in_background(self, f"Uploading the website to {target.host}…", upload_folder,
                                     self._folder(), target, title="Uploading", pass_progress=True)
        except Exception as e:
            QMessageBox.warning(self, "Upload", str(e))
            return
        url = self.url_edit.text().strip()
        if url:
            if "://" not in url:
                url = "https://" + url
            self.link_lbl.setText(f"✓ Uploaded {sent} files. Your hike is online at <a href='{url}'>{url}</a>")
            QGuiApplication.clipboard().setText(url)
            self.app_state.status_message.emit(f"Hike website uploaded — link copied: {url}")
        else:
            self.link_lbl.setText(f"✓ Uploaded {sent} files to {target.host}{target.remote_dir}")

    def done(self, result: int) -> None:  # Close, Esc or the window's X
        self._save_ftp_settings()
        super().done(result)
