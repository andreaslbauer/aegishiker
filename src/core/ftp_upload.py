"""Upload an exported hike website folder to a web server over FTP (or FTPS, FTP over TLS).

The password is used for this upload only and never stored. Every connection is logged to
~/.aegishiker/logs/network.jsonl (without credentials).
"""
import ftplib
import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import llm_log

Progress = Optional[Callable[[str], None]]


@dataclass
class FtpTarget:
    host: str
    username: str
    password: str
    remote_dir: str = "/"
    port: int = 21
    use_tls: bool = True  # explicit FTPS (AUTH TLS); plain FTP sends the password unencrypted
    passive: bool = True
    timeout: float = 30.0


def _connect(target: FtpTarget) -> ftplib.FTP:
    ftp: ftplib.FTP = ftplib.FTP_TLS(timeout=target.timeout) if target.use_tls else ftplib.FTP(timeout=target.timeout)
    ftp.connect(target.host, target.port)
    ftp.login(target.username, target.password)
    if isinstance(ftp, ftplib.FTP_TLS):
        ftp.prot_p()  # encrypt the file transfers too, not just the login
    ftp.set_pasv(target.passive)
    return ftp


def _ensure_dir(ftp: ftplib.FTP, path: str, known: set) -> None:
    """mkdir -p on the server (directories that exist already are fine)."""
    if path in known or path in ("", "/"):
        return
    parent = posixpath.dirname(path.rstrip("/"))
    if parent and parent != path:
        _ensure_dir(ftp, parent, known)
    try:
        ftp.mkd(path)
    except ftplib.error_perm as e:
        if not str(e).startswith("550"):  # 550: already exists (or not allowed: the upload will say)
            raise
    known.add(path)


def site_files(folder: Path) -> List[Tuple[Path, str]]:
    """(local file, path relative to the folder with forward slashes), index.html last so a
    half-finished upload never shows a page whose data isn't there yet."""
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    out = [(p, p.relative_to(folder).as_posix()) for p in files]
    out.sort(key=lambda f: f[1] == "index.html")
    return out


def test_connection(target: FtpTarget) -> str:
    """Log in and return the server's welcome line (raises with the server's message on failure)."""
    ftp = _connect(target)
    try:
        return ftp.getwelcome()
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


def upload_folder(folder: Path, target: FtpTarget, progress: Progress = None) -> int:
    """Upload every file of `folder` below target.remote_dir; returns the number of files sent."""
    folder = Path(folder)
    files = site_files(folder)
    url = f"ftp{'s' if target.use_tls else ''}://{target.host}:{target.port}{target.remote_dir}"
    try:
        ftp = _connect(target)
    except (ftplib.all_errors) as e:  # type: ignore[misc]
        llm_log.log_network_request("ftp_upload", url, error=str(e))
        raise RuntimeError(f"Could not log in to {target.host}: {e}") from e
    sent = 0
    total_bytes = 0
    try:
        base = "/" + target.remote_dir.strip("/") if target.remote_dir.strip("/") else ""
        known: set = set()
        for i, (local, rel) in enumerate(files, 1):
            remote = f"{base}/{rel}" if base else f"/{rel}"
            _ensure_dir(ftp, posixpath.dirname(remote), known)
            if progress:
                progress(f"Uploading {i} of {len(files)}: {rel}")
            with open(local, "rb") as fh:
                ftp.storbinary(f"STOR {remote}", fh)
            sent += 1
            total_bytes += local.stat().st_size
    except ftplib.all_errors as e:  # type: ignore[misc]
        llm_log.log_network_request("ftp_upload", url, error=str(e))
        raise RuntimeError(f"Upload failed after {sent} of {len(files)} files: {e}") from e
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
    llm_log.log_network_request("ftp_upload", url, status_code=226, bytes_received=total_bytes)
    return sent
