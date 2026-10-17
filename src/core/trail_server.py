"""Local-First LAN Sync & Offline PWA Server (src/core/trail_server.py).

Modeled on AegisKitchenCoach's kitchen_server.py:
* Listens on this computer's LAN address only (or 127.0.0.1 in self-test) and refuses any
  client outside the local network.
* Every URL carries the session's cryptographic random token (/h/<token>); anything else gets 404.
* Serves the offline-capable PWA (`hiker.html`, `sw.js`, `manifest.json`), full Regional
  Destination Packs + Composed Routes (`/h/<token>/pack`), and accepts completed hike GPS
  telemetry, field condition updates, and trail photos back from the phone (`POST /h/<token>/sync`).
"""
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

from . import paths

logger = logging.getLogger(__name__)

MAX_SYNC_BYTES = 16_000_000
CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src data: blob:; connect-src 'self'; "
    "manifest-src 'self'; worker-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


@dataclass
class TrailSyncSession:
    token: str
    title: str
    get_pack_payload: Callable[[Dict[str, str]], Dict[str, Any]]
    on_sync_telemetry: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    on_condition_update: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    synced_sessions_count: int = 0
    last_client_ip: str = ""

    def path(self) -> str:
        return f"/h/{self.token}"


def lan_ip() -> str:
    """Return this computer's LAN IPv4 address (loopback if offline)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def local_network(ip: str) -> ipaddress.IPv4Network:
    addr = ipaddress.ip_address(ip)
    if addr.is_loopback:
        return ipaddress.ip_network("127.0.0.0/8")
    prefix = 24 if ip.startswith("192.168.") else 16
    return ipaddress.ip_network(f"{ip}/{prefix}", strict=False)


def client_allowed(client_ip: str, server_ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(client_ip.split("%")[0])
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if addr.is_loopback:
        return True
    if not addr.is_private or addr.version != 4:
        return False
    return addr in local_network(server_ip)


def qr_png(url: str, scale: int = 6) -> bytes:
    """Render a PNG QR code for `url` using pure-Python `segno`."""
    import segno

    qr = segno.make(url, error="m")
    buf = BytesIO()
    qr.save(buf, kind="png", scale=scale, border=2, dark="#102019", light="#e8f2ec")
    return buf.getvalue()


class TrailServer:
    """Embedded LAN HTTP server for Desktop <-> iPhone regional pack & GPS telemetry sync."""

    def __init__(self, host: Optional[Any] = None, port: int = 0):
        self.db = None
        if host is not None and not isinstance(host, str) and hasattr(host, "list_destination_packs"):
            self.db = host
            self.host = lan_ip()
        else:
            self.host = host or lan_ip()
        self.requested_port = port
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self.sessions: Dict[str, TrailSyncSession] = {}
        self.token: str = secrets.token_urlsafe(16)
        self.active_destination_id: Optional[str] = "white_mountains_nh"
        self.active_route_id: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int:
        return self._httpd.server_port if self._httpd else 0

    def get_pairing_url(self) -> str:
        return f"http://{self.host}:{self.port}/h/{self.token}/hiker.html"

    def _default_pack_payload(self, dest_id: Optional[str] = None) -> Dict[str, Any]:
        if not self.db:
            return {}
        target_id = dest_id or self.active_destination_id or "white_mountains_nh"
        pack = self.db.get_destination_pack(target_id)
        if not pack:
            packs = self.db.list_destination_packs()
            pack = packs[0] if packs else None
        if not pack:
            return {}
        return {
            "destination": {
                "id": pack.id,
                "name": pack.name,
                "region": pack.state_or_region,
                "base_elevation_m": pack.base_ele_m,
            },
            "trails": [
                {
                    "id": t.id,
                    "name": t.name,
                    "difficulty": t.difficulty.value if hasattr(t.difficulty, "value") else str(t.difficulty),
                    "condition": t.condition_status.value if hasattr(t.condition_status, "value") else str(t.condition_status),
                    "length_m": t.distance_m,
                    "elevation_gain_m": t.elevation_gain_m,
                    "coordinates": t.coordinates,
                }
                for t in pack.trails
            ],
            "pois": [p.model_dump() for p in pack.pois],
        }

    def _default_state_payload(self) -> Dict[str, Any]:
        if not self.db:
            return {"destinations": [], "routes": []}
        packs = self.db.list_destination_packs()
        routes = self.db.list_routes()
        active_route = (
            self.db.get_route(self.active_route_id)
            if self.active_route_id
            else (routes[0] if routes else None)
        )
        return {
            "destinations": [
                {"id": p.id, "name": p.name, "region": p.state_or_region}
                for p in packs
            ],
            "routes": [
                {
                    "id": r.id,
                    "name": r.name,
                    "distance_m": r.distance_m,
                    "elevation_gain_m": r.elevation_gain_m,
                    "coordinates": r.coordinates,
                    "elevation_profile": [ep.model_dump() for ep in r.elevation_profile],
                }
                for r in routes
            ],
            "active_destination": self._default_pack_payload(self.active_destination_id),
            "active_route": (
                {
                    "id": active_route.id,
                    "name": active_route.name,
                    "distance_m": active_route.distance_m,
                    "elevation_gain_m": active_route.elevation_gain_m,
                    "coordinates": active_route.coordinates,
                    "elevation_profile": [ep.model_dump() for ep in active_route.elevation_profile],
                }
                if active_route
                else None
            ),
        }

    def _default_sync_handler(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from src.hiking.models import HikeSession, TelemetryPoint

        raw_pts = payload.get("telemetry_points") or []
        session = HikeSession(
            route_id=payload.get("route_id"),
            pack_id=payload.get("destination_id") or self.active_destination_id,
            title="Mobile Companion Synced Hike",
            status="completed",
            notes=str(payload.get("notes") or "Synced from mobile companion"),
        )
        pts = [
            TelemetryPoint(
                seq=idx,
                session_id=session.id,
                timestamp=str(p.get("timestamp") or ""),
                lat=float(p.get("lat") or 0.0),
                lon=float(p.get("lon") or 0.0),
                gps_ele_m=float(p.get("ele_m") or p.get("gps_ele_m") or 0.0),
                horizontal_accuracy_m=float(p.get("accuracy_m") or 5.0),
                speed_mps=float(p.get("speed_mps") or 1.2),
            )
            for idx, p in enumerate(raw_pts)
        ]
        if self.db is not None:
            self.db.save_session(session, telemetry=pts)
        return {"status": "ok", "session_id": session.id, "points_saved": len(pts)}

    def start(self) -> None:
        with self._lock:
            if self._httpd is not None:
                return
            if self.token not in self.sessions:
                self.sessions[self.token] = TrailSyncSession(
                    token=self.token,
                    title="Default Mobile Companion Session",
                    get_pack_payload=lambda q: self._default_pack_payload(q.get("id")),
                    on_sync_telemetry=self._default_sync_handler,
                )
            server_ref = self

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, fmt: str, *args: Any) -> None:
                    logger.debug("TrailServer %s - " + fmt, self.client_address[0], *args)

                def _send_bytes(
                    self,
                    status: int,
                    body: bytes,
                    content_type: str = "application/json; charset=utf-8",
                ) -> None:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Content-Security-Policy", CSP)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)

                def _send_json(self, status: int, data: Any) -> None:
                    self._send_bytes(status, json.dumps(data, ensure_ascii=False).encode("utf-8"))

                def _resolve_session(self) -> Optional[tuple[TrailSyncSession, str, Dict[str, str]]]:
                    client_ip = self.client_address[0]
                    if not client_allowed(client_ip, server_ref.host):
                        self._send_bytes(403, b"Forbidden", "text/plain")
                        return None
                    parsed = urlparse(self.path)
                    parts = [p for p in parsed.path.split("/") if p]
                    if len(parts) < 2 or parts[0] != "h":
                        self._send_bytes(404, b"Not Found", "text/plain")
                        return None
                    token = parts[1]
                    subpath = "/" + "/".join(parts[2:]) if len(parts) > 2 else ""
                    sess = server_ref.sessions.get(token)
                    if not sess:
                        self._send_bytes(404, b"Not Found", "text/plain")
                        return None
                    sess.last_seen = time.time()
                    sess.last_client_ip = client_ip
                    query = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}
                    return (sess, subpath, query)

                def do_GET(self) -> None:
                    resolved = self._resolve_session()
                    if not resolved:
                        return
                    sess, subpath, query = resolved
                    if subpath in ("", "/", "/hiker.html"):
                        html_path = paths.WEB_DIR / "hiker.html"
                        body = html_path.read_bytes() if html_path.exists() else b"<h1>AegisHiker</h1>"
                        self._send_bytes(200, body, "text/html; charset=utf-8")
                    elif subpath == "/sw.js":
                        sw_path = paths.WEB_DIR / "sw.js"
                        body = sw_path.read_bytes() if sw_path.exists() else b""
                        self._send_bytes(200, body, "application/javascript; charset=utf-8")
                    elif subpath == "/manifest.json":
                        man_path = paths.WEB_DIR / "manifest.json"
                        body = man_path.read_bytes() if man_path.exists() else b"{}"
                        self._send_bytes(200, body, "application/manifest+json; charset=utf-8")
                    elif subpath == "/state":
                        self._send_json(200, server_ref._default_state_payload())
                    elif subpath == "/pack":
                        try:
                            payload = sess.get_pack_payload(query)
                            self._send_json(200, {"ok": True, "data": payload, **payload})
                        except Exception as e:
                            logger.error(f"TrailServer /pack error: {e}")
                            self._send_json(500, {"ok": False, "error": str(e)})
                    elif subpath.startswith("/pack/"):
                        dest_id = subpath.split("/pack/", 1)[1].strip("/")
                        payload = server_ref._default_pack_payload(dest_id)
                        self._send_json(200, payload)
                    else:
                        self._send_bytes(404, b"Not Found", "text/plain")

                def do_POST(self) -> None:
                    resolved = self._resolve_session()
                    if not resolved:
                        return
                    sess, subpath, _ = resolved
                    length = int(self.headers.get("Content-Length") or 0)
                    if length <= 0 or length > MAX_SYNC_BYTES:
                        self._send_json(413, {"ok": False, "error": "Payload size out of range"})
                        return
                    raw_body = self.rfile.read(length)
                    try:
                        payload = json.loads(raw_body.decode("utf-8"))
                    except Exception:
                        self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                        return

                    if subpath == "/sync":
                        if not sess.on_sync_telemetry:
                            self._send_json(400, {"ok": False, "error": "Sync handler not configured"})
                            return
                        try:
                            res = sess.on_sync_telemetry(payload)
                            sess.synced_sessions_count += 1
                            out = {"ok": True, "result": res}
                            if isinstance(res, dict):
                                out.update(res)
                            self._send_json(200, out)
                        except Exception as e:
                            logger.error(f"TrailServer /sync error: {e}")
                            self._send_json(500, {"ok": False, "error": str(e)})
                    elif subpath == "/condition":
                        if not sess.on_condition_update:
                            self._send_json(400, {"ok": False, "error": "Condition handler not configured"})
                            return
                        try:
                            res = sess.on_condition_update(payload)
                            self._send_json(200, {"ok": True, "result": res})
                        except Exception as e:
                            self._send_json(500, {"ok": False, "error": str(e)})
                    else:
                        self._send_bytes(404, b"Not Found", "text/plain")

            bind_host = "127.0.0.1" if self.host == "127.0.0.1" else "0.0.0.0"
            self._httpd = ThreadingHTTPServer((bind_host, self.requested_port), Handler)
            self._thread = threading.Thread(
                target=self._httpd.serve_forever,
                name="AegisHikerTrailServer",
                daemon=True,
            )
            self._thread.start()
            logger.info(f"TrailServer listening on http://{self.host}:{self.port}")

    def open_session(
        self,
        title: str,
        get_pack_payload: Callable[[Dict[str, str]], Dict[str, Any]],
        on_sync_telemetry: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        on_condition_update: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> TrailSyncSession:
        with self._lock:
            self.start()
            token = secrets.token_urlsafe(16)
            sess = TrailSyncSession(
                token=token,
                title=title,
                get_pack_payload=get_pack_payload,
                on_sync_telemetry=on_sync_telemetry,
                on_condition_update=on_condition_update,
            )
            self.sessions[token] = sess
            return sess

    def url(self, session: TrailSyncSession) -> str:
        return f"http://{self.host}:{self.port}{session.path()}"

    def close_session(self, token: str) -> None:
        with self._lock:
            self.sessions.pop(token, None)
            if not self.sessions:
                self.stop()

    def stop(self) -> None:
        with self._lock:
            if self._httpd is not None:
                try:
                    self._httpd.shutdown()
                    self._httpd.server_close()
                except Exception:
                    pass
                self._httpd = None
                self._thread = None
            self.sessions.clear()

