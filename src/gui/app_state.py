"""Central application state and Qt signals for AegisHiker."""
from typing import List, Optional
from PySide6.QtCore import QObject, Signal

from src.core.agent_engine import AgentEngine
from src.core.database import Database
from src.core.interview_engine import InterviewEngine
from src.core.llm_service import LLMService
from src.core.pack_manager import ensure_starter_packs_loaded
from src.core.settings import Settings
from src.core.trail_server import TrailServer
from src.core.voyager_bridge import VoyagerBridge
from src.gui.busy import busy
from src.hiking.models import (
    ComposedRoute,
    Destination,
    HikeSession,
    POI,
    TrailPhotoPin,
    TrailSegment,
)


class AppState(QObject):
    destination_changed = Signal(str)
    route_changed = Signal(str)
    session_changed = Signal(str)
    trails_updated = Signal()
    hover_distance_changed = Signal(float)
    settings_changed = Signal()
    status_message = Signal(str)

    def __init__(
        self,
        db: Optional[Database] = None,
        settings: Optional[Settings] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings or Settings()
        self.db = db or Database()
        ensure_starter_packs_loaded(self.db)

        self.llm = LLMService(self.settings)
        self.interview_engine = InterviewEngine(self.db, self.llm)
        self.agent_engine = AgentEngine(self.db, self.llm)
        self.voyager_bridge = VoyagerBridge(self.settings.get("voyager_db_path", ""))
        self.trail_server = TrailServer(self.db, port=int(self.settings.get("lan_server_port", 0) or 0))

        self.active_destination_id: Optional[str] = None
        self.active_route_id: Optional[str] = None
        self.active_session_id: Optional[str] = None

        destinations = self.db.list_destinations()
        if destinations:
            # Prefer New Hampshire White Mountains or first available pack
            nh = next((d for d in destinations if d.id == "white_mountains_nh"), destinations[0])
            self.active_destination_id = nh.id
            self.trail_server.active_destination_id = nh.id

        routes = self.db.list_routes(self.active_destination_id)
        if routes:
            self.active_route_id = routes[0].id
            self.trail_server.active_route_id = routes[0].id

        sessions = self.db.list_sessions()
        if sessions:
            self.active_session_id = sessions[0].id

    def set_active_destination(self, destination_id: str) -> None:
        if not destination_id:
            return
        self.active_destination_id = destination_id
        self.trail_server.active_destination_id = destination_id
        routes = self.db.list_routes(destination_id)
        if routes:
            self.active_route_id = routes[0].id
            self.trail_server.active_route_id = routes[0].id
        dest = self.db.get_destination(destination_id)
        with busy(None, f"Loading {dest.name if dest else destination_id}…", title="Loading Region", steps=2) as b:
            self.destination_changed.emit(destination_id)
            b.step("Updating routes and maps…")
            if self.active_route_id:
                self.route_changed.emit(self.active_route_id)
            b.step()

    def set_active_route(self, route_id: str) -> None:
        if not route_id:
            return
        self.active_route_id = route_id
        self.trail_server.active_route_id = route_id
        self.route_changed.emit(route_id)

    def set_active_session(self, session_id: str) -> None:
        if not session_id:
            return
        self.active_session_id = session_id
        self.session_changed.emit(session_id)

    def get_active_destination(self) -> Optional[Destination]:
        if not self.active_destination_id:
            return None
        return self.db.get_destination(self.active_destination_id)

    def get_active_trails(self) -> List[TrailSegment]:
        return self.db.list_trails(self.active_destination_id)

    def get_active_pois(self) -> List[POI]:
        return self.db.list_pois(self.active_destination_id)

    def get_active_route(self) -> Optional[ComposedRoute]:
        if not self.active_route_id:
            return None
        return self.db.get_route(self.active_route_id)

    def get_active_session(self) -> Optional[HikeSession]:
        if not self.active_session_id:
            return None
        return self.db.get_session(self.active_session_id)

    def get_active_route_photos(self) -> List[TrailPhotoPin]:
        if self.active_session_id:
            pins = self.db.list_photo_pins(session_id=self.active_session_id)
            if pins:
                return pins
        if self.active_route_id:
            return self.db.list_photo_pins(route_id=self.active_route_id)
        return []

    def close(self) -> None:
        try:
            self.trail_server.stop()
        except Exception:
            pass
        try:
            self.db.close()
        except Exception:
            pass

