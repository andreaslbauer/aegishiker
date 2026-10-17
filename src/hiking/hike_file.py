""".aegishike bundle export and import, with optional Fernet password encryption."""
import base64
import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from .models import ComposedRoute, HikePhotoPin, HikeSession, TelemetryPoint

FORMAT_ID = "aegishiker.bundle.v1"


def _derive_fernet_key(password: str, salt: bytes) -> bytes:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000, dklen=32)
    return base64.urlsafe_b64encode(dk)


def export_hike_bundle(
    route: Optional[ComposedRoute] = None,
    sessions: Optional[List[HikeSession]] = None,
    telemetry_by_session: Optional[Dict[str, List[TelemetryPoint]]] = None,
    photos_by_session: Optional[Dict[str, List[HikePhotoPin]]] = None,
    password: str = "",
) -> str:
    payload: Dict[str, Any] = {
        "format": FORMAT_ID,
        "route": route.model_dump() if route else None,
        "sessions": [s.model_dump() for s in (sessions or [])],
        "telemetry_by_session": {
            sid: [pt.model_dump() for pt in pts]
            for sid, pts in (telemetry_by_session or {}).items()
        },
        "photos_by_session": {
            sid: [pin.model_dump() for pin in pins]
            for sid, pins in (photos_by_session or {}).items()
        },
    }
    raw_json = json.dumps(payload, indent=2)
    if not password:
        return raw_json

    from cryptography.fernet import Fernet

    salt = os.urandom(16)
    f = Fernet(_derive_fernet_key(password, salt))
    token = f.encrypt(raw_json.encode("utf-8"))
    envelope = {
        "format": FORMAT_ID,
        "encrypted": True,
        "salt": base64.b64encode(salt).decode("ascii"),
        "ciphertext": token.decode("ascii"),
    }
    return json.dumps(envelope, indent=2)


def import_hike_bundle(text: str, password: str = "") -> Dict[str, Any]:
    outer = json.loads(text)
    if outer.get("encrypted"):
        if not password:
            raise ValueError("Password required to unlock encrypted .aegishike bundle.")
        from cryptography.fernet import Fernet, InvalidToken

        salt = base64.b64decode(outer["salt"])
        f = Fernet(_derive_fernet_key(password, salt))
        try:
            decrypted = f.decrypt(outer["ciphertext"].encode("ascii")).decode("utf-8")
        except InvalidToken as e:
            raise ValueError("Incorrect password for encrypted .aegishike bundle.") from e
        outer = json.loads(decrypted)

    if outer.get("format") != FORMAT_ID:
        raise ValueError(f"Unsupported bundle format: {outer.get('format')}")

    route = ComposedRoute.model_validate(outer["route"]) if outer.get("route") else None
    sessions = [HikeSession.model_validate(s) for s in (outer.get("sessions") or [])]
    telemetry_by_session = {
        sid: [TelemetryPoint.model_validate(p) for p in pts]
        for sid, pts in (outer.get("telemetry_by_session") or {}).items()
    }
    photos_by_session = {
        sid: [HikePhotoPin.model_validate(p) for p in pins]
        for sid, pins in (outer.get("photos_by_session") or {}).items()
    }
    return {
        "route": route,
        "sessions": sessions,
        "telemetry_by_session": telemetry_by_session,
        "photos_by_session": photos_by_session,
    }


def save_hike_archive(
    path: Any,
    route: ComposedRoute,
    destination: Any = None,
    gear_items: Optional[List] = None,
    photos: Optional[List[HikePhotoPin]] = None,
    password: str = "",
) -> None:
    from pathlib import Path

    if gear_items is not None:
        route.gear_checklist = list(gear_items)
    photos_map = {route.id: list(photos or [])} if photos else {}
    text = export_hike_bundle(route=route, photos_by_session=photos_map, password=password)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def load_hike_archive(path: Any, password: str = "") -> Dict[str, Any]:
    from pathlib import Path

    text = Path(path).read_text(encoding="utf-8")
    data = import_hike_bundle(text, password=password)
    route = data.get("route")
    all_photos: List[HikePhotoPin] = []
    for pins in (data.get("photos_by_session") or {}).values():
        all_photos.extend(pins)
    return {
        "route": route,
        "gear_items": route.gear_checklist if route else [],
        "photos": all_photos,
        "sessions": data.get("sessions", []),
    }

