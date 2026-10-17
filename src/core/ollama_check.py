"""Startup check for the local AI runtime: is Ollama installed, is its server answering,
and are the models chosen under Settings actually pulled?
"""
import os
import platform
import shutil
from dataclasses import dataclass, field
from typing import List, Optional

import requests

DEFAULT_HOST = "http://localhost:11434"
DOWNLOAD_URL = "https://ollama.com/download"


@dataclass
class OllamaStatus:
    host: str
    binary_found: bool
    server_running: bool
    installed_models: List[str] = field(default_factory=list)
    interview_model: str = ""
    chat_model: str = ""
    error: str = ""

    @property
    def interview_model_ok(self) -> bool:
        return self.server_running and model_installed(self.interview_model, self.installed_models)

    @property
    def chat_model_ok(self) -> bool:
        return self.server_running and model_installed(self.chat_model, self.installed_models)

    @property
    def ready(self) -> bool:
        return self.interview_model_ok and self.chat_model_ok

    @property
    def missing_models(self) -> List[str]:
        missing = []
        for m in (self.interview_model, self.chat_model):
            if m and not model_installed(m, self.installed_models) and m not in missing:
                missing.append(m)
        return missing


def resolve_host(configured: Optional[str] = None) -> str:
    host = (configured or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST).strip()
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    if host.count(":") == 1:
        host += ":11434"
    return host.rstrip("/")


def model_installed(wanted: str, installed: List[str]) -> bool:
    if not wanted:
        return False
    want = wanted.strip().lower()
    if ":" not in want:
        want += ":latest"
    names = [n.strip().lower() for n in installed]
    return any(n == want or n.startswith(want + "-") for n in names)


def find_binary() -> bool:
    if shutil.which("ollama"):
        return True
    system = platform.system()
    if system == "Darwin":
        candidates = ["/usr/local/bin/ollama", "/opt/homebrew/bin/ollama", "/Applications/Ollama.app"]
    elif system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [os.path.join(local, "Programs", "Ollama", "ollama.exe")]
    else:
        candidates = ["/usr/local/bin/ollama", "/usr/bin/ollama", "/snap/bin/ollama"]
    return any(os.path.exists(c) for c in candidates)


def check_ollama(
    host: Optional[str] = None,
    interview_model: Optional[str] = None,
    chat_model: Optional[str] = None,
    timeout: float = 3.0,
) -> OllamaStatus:
    if host is None or interview_model is None or chat_model is None:
        from src.core import settings

        cfg = settings.load()
        host = host if host is not None else cfg.get("ollama_host")
        interview_model = interview_model or cfg.get("interview_model")
        chat_model = chat_model or cfg.get("chat_model")

    resolved = resolve_host(host)
    status = OllamaStatus(
        host=resolved,
        binary_found=find_binary(),
        server_running=False,
        interview_model=interview_model or "",
        chat_model=chat_model or "",
    )
    try:
        resp = requests.get(f"{resolved}/api/tags", timeout=timeout)
        resp.raise_for_status()
        status.server_running = True
        status.installed_models = [
            m.get("name") or m.get("model") or "" for m in resp.json().get("models", [])
        ]
    except requests.RequestException as e:
        status.error = str(e)
    except ValueError as e:
        status.error = f"Unexpected response from {resolved}: {e}"
    status.binary_found = status.binary_found or status.server_running
    return status


@dataclass
class ModelInfo:
    name: str
    size_bytes: int = 0
    parameter_size: str = ""
    quantization: str = ""
    family: str = ""
    capabilities: List[str] = field(default_factory=list)

    @property
    def is_chat_model(self) -> bool:
        if self.capabilities:
            return "completion" in self.capabilities
        return "embed" not in self.name.lower()

    @property
    def is_remote(self) -> bool:
        return self.name.endswith(":cloud") or "-cloud" in self.name

    def label(self) -> str:
        parts = [p for p in (self.parameter_size, self.quantization) if p]
        if self.size_bytes:
            parts.append(f"{self.size_bytes / 1e9:.1f} GB")
        return f"{self.name}  ({', '.join(parts)})" if parts else self.name


def list_models(host: Optional[str] = None, timeout: float = 3.0) -> List[ModelInfo]:
    resolved = resolve_host(host)
    resp = requests.get(f"{resolved}/api/tags", timeout=timeout)
    resp.raise_for_status()
    out = []
    for m in resp.json().get("models", []):
        d = m.get("details") or {}
        out.append(
            ModelInfo(
                name=m.get("name") or m.get("model") or "",
                size_bytes=int(m.get("size") or 0),
                parameter_size=d.get("parameter_size", ""),
                quantization=d.get("quantization_level", ""),
                family=d.get("family", ""),
                capabilities=list(m.get("capabilities") or []),
            )
        )
    return sorted(out, key=lambda x: x.name.lower())

