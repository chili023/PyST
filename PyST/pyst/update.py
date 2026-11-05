"""
Nach neuer Software und Firmware suchen, ohne Oberfläche.

PyST: Version aus `main` des öffentlichen Repositorys, nur Meldung, installiert wird nichts.
Firmware: `manifest.json` im Branch `firmware`: je Ziel Version, Datei, Größe, SHA-256 und Platz für die Signatur.
Fehlt der Branch oder das Netz, liefern die Abfragen None: PyST bleibt still.
Heruntergeladene Dateien gelten erst nach Prüfung von Größe und SHA-256; geflasht wird nur auf Knopfdruck.
"""
import hashlib
import json
import os
import re
import ssl
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

RAW = "https://raw.githubusercontent.com/chili023/PyST"
VERSION_URL = f"{RAW}/main/PyST/pyst/__init__.py"
FIRMWARE_URL = f"{RAW}/firmware"
MANIFEST_NAME = "manifest.json"
MANIFEST_MAX = 64 * 1024
FILE_MAX = 8 * 1024 * 1024
TIMEOUT_S = 4.0
_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_VERSION = re.compile(r"^\d+(\.\d+)*$")


class UpdateError(Exception):
    pass


@dataclass
class Entry:
    target: str
    version: str
    file: str
    size: int
    sha256: str
    signature: str = ""          # kommt mit Stufe 2, hier noch nicht geprüft


def version_key(version: str) -> Tuple[int, ...]:
    """'1.3' und '1.3.0' sind gleich neu."""
    parts = [int(x) for x in version.split(".")]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    try:
        return version_key(remote) > version_key(local)
    except (ValueError, AttributeError):
        return False


def parse_manifest(text: str) -> Dict[str, Entry]:
    """Ungültige Einträge werden übersprungen; kaputtes JSON ergibt UpdateError."""
    try:
        data = json.loads(text)
        targets = data["targets"]
        if not isinstance(targets, dict):
            raise TypeError
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError(f"Manifest nicht lesbar: {exc}") from exc
    out: Dict[str, Entry] = {}
    for name, e in targets.items():
        try:
            entry = Entry(name, str(e["version"]), str(e["file"]), int(e["size"]), str(e["sha256"]).lower(),
                          str(e.get("signature", "")))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if (_VERSION.match(entry.version) and _FILE_NAME.match(entry.file) and 0 < entry.size <= FILE_MAX
                and re.fullmatch(r"[0-9a-f]{64}", entry.sha256)):
            out[name] = entry
    return out


def _context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi                         # auf macOS fehlen dem python.org-Build sonst die Zertifikate
        ctx.load_verify_locations(certifi.where())
    except ImportError:
        pass
    return ctx


def _fetch(url: str, limit: int, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "PyST"})
    with urllib.request.urlopen(req, timeout=timeout, context=_context()) as resp:
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("Antwort zu groß")
    return data


def latest_pyst(timeout: float = TIMEOUT_S) -> Optional[str]:
    """Version von PyST in `main`; None ohne Netz."""
    try:
        text = _fetch(VERSION_URL, MANIFEST_MAX, timeout).decode("utf8", "replace")
    except (OSError, UpdateError):
        return None
    hit = re.search(r'^__version__\s*=\s*"([0-9.]+)"', text, re.M)
    return hit.group(1) if hit else None


def fetch_manifest(timeout: float = TIMEOUT_S, base: str = FIRMWARE_URL) -> Optional[Dict[str, Entry]]:
    """None, solange es keinen Branch `firmware` gibt, ohne Netz oder bei kaputtem Manifest."""
    try:
        return parse_manifest(_fetch(f"{base}/{MANIFEST_NAME}", MANIFEST_MAX, timeout).decode("utf8"))
    except (OSError, UnicodeDecodeError, UpdateError):
        return None


def check_file(path: str, entry: Entry) -> None:
    if os.path.getsize(path) != entry.size:
        raise UpdateError(f"{entry.file}: falsche Größe")
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    if h.hexdigest() != entry.sha256:
        raise UpdateError(f"{entry.file}: Prüfsumme stimmt nicht")


def download(entry: Entry, folder: str, base: str = FIRMWARE_URL, timeout: float = 20.0,
             progress: Optional[Callable[[int, int], None]] = None) -> str:
    """Datei laden und prüfen; -> Pfad. Eine unvollständige oder falsche Datei bleibt nie liegen."""
    if not _FILE_NAME.match(entry.file):
        raise UpdateError("Ungültiger Dateiname im Manifest")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, entry.file)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".part")
    try:
        req = urllib.request.Request(f"{base}/{entry.file}", headers={"User-Agent": "PyST"})
        done = 0
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(req, timeout=timeout, context=_context()) as resp:
            while True:
                block = resp.read(65536)
                if not block:
                    break
                done += len(block)
                if done > entry.size:
                    raise UpdateError(f"{entry.file}: größer als angegeben")
                out.write(block)
                if progress:
                    progress(done, entry.size)
        check_file(tmp, entry)
        os.replace(tmp, dest)
        return dest
    except OSError as exc:
        raise UpdateError(f"Download fehlgeschlagen: {exc}") from exc
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
