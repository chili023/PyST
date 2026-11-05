"""
Anmeldung am TCP-Port der Basis (2323), ohne Qt.

USB ist das Koppeln: '#AUTH KEY' (nur per USB) liefert '#AUTH;id=<UID>;key=<64 hex>', '#AUTH NEU' vergibt einen neuen
Schluessel. PyST legt ihn je id in ~/PyST/schluessel.json ab (nur fuer den Benutzer lesbar, atomar geschrieben).

Am TCP-Port gelten ohne Anmeldung nur '#?' und '#AUTH':
    PC    #AUTH
    Basis #AUTH;id=<UID>;nonce=<32 hex>
    PC    #AUTH <64 hex>      HMAC-SHA256(key, "PyST-Auth-1" || nonce_bytes || id_ascii)
    Basis #OK;AUTH            oder '#ERR;AUTH;nein', dann mit wachsender Sperre

Der Schluessel darf nie ins Log, in Fehlermeldungen oder in die Oberflaeche.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import stat
import tempfile
from typing import Dict, Optional, Tuple

from .db import base_dir
from .diag import parse_kv

log = logging.getLogger("pyst.auth")

AUTH_LABEL = b"PyST-Auth-1"
KEY_FILE = "schluessel.json"
_ID = re.compile(r"[0-9A-Za-z_.:-]{1,64}")
_KEY = re.compile(r"[0-9A-Fa-f]{64}")
_NONCE = re.compile(r"[0-9A-Fa-f]{32}")
_KEY_FIELD = re.compile(r"(key=)[0-9A-Fa-f]+")
# ohne 0/O, 1/l/I: das Passwort wird abgelesen und am Handy eingetippt
_PW_ZEICHEN = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

TEXT_KEIN_SCHLUESSEL = ("Für diese Basis ({id}) ist kein Schlüssel gespeichert.\n"
                        "Verbinde sie einmal per USB, dann holt PyST den Schlüssel selbst.")
TEXT_FALSCH = ("Die Basis hat die Anmeldung abgelehnt: Der gespeicherte Schlüssel passt nicht.\n"
               "Verbinde sie einmal per USB, um ihn neu zu holen. Jeder Fehlversuch verlängert die Sperre der Basis.")
TEXT_GESPERRT = ("Die Basis lehnt die Anmeldung gerade ab (Sperre nach Fehlversuchen).\n"
                 "Warte etwas oder verbinde sie per USB, um den Schlüssel neu zu holen.")
TEXT_BASIS_OHNE = "Die Basis hat noch keinen Schlüssel. Verbinde sie einmal per USB."
TEXT_KEINE_ANTWORT = "Die Basis antwortet nicht auf die Anmeldung."
TEXT_FORMAT = "Die Antwort der Basis auf die Anmeldung ist nicht lesbar."
TEXT_ZU_LANG = "Die Firmware der Basis nimmt die Anmeldezeile nicht an (zu lang): Firmware aktualisieren."


class AuthError(Exception):
    """Anmeldung gescheitert; der Text ist fuer die Oberflaeche bestimmt."""


def refusal_text(line: str) -> str:
    """Text zur Ablehnung der Anfrage '#AUTH': 'nein' = Sperre, 'kein_schluessel', 'rng' (kein Zufall in der Basis)."""
    why = line.split(";")[2] if line.count(";") >= 2 else ""
    if why == "nein":
        return TEXT_GESPERRT
    if why == "kein_schluessel":
        return TEXT_BASIS_OHNE
    return f"Die Basis lehnt die Anmeldung ab ({why or 'ohne Grund'})."


def redact(line: str) -> str:
    """Zeile fuer das Log: Schluessel unkenntlich."""
    return _KEY_FIELD.sub(r"\1***", line)


def response(key_hex: str, nonce: bytes, uid: str) -> str:
    """Antwort auf die Anfrage der Basis als 64 Hexziffern."""
    msg = AUTH_LABEL + nonce + uid.encode("ascii")
    return hmac.new(bytes.fromhex(key_hex), msg, hashlib.sha256).hexdigest()


def parse_key_line(line: str) -> Optional[Tuple[str, str]]:
    """'#AUTH;id=…;key=…' -> (id, key) oder None."""
    if not line.startswith("#AUTH;"):
        return None
    kv = parse_kv(line)
    uid, key = kv.get("id", ""), kv.get("key", "")
    return (uid, key.lower()) if _ID.fullmatch(uid) and _KEY.fullmatch(key) else None


def parse_challenge(line: str) -> Optional[Tuple[str, bytes]]:
    """'#AUTH;id=…;nonce=…' -> (id, nonce_bytes) oder None."""
    if not line.startswith("#AUTH;"):
        return None
    kv = parse_kv(line)
    uid, nonce = kv.get("id", ""), kv.get("nonce", "")
    return (uid, bytes.fromhex(nonce)) if _ID.fullmatch(uid) and _NONCE.fullmatch(nonce) else None


def wlan_password(n: int = 16) -> str:
    """Vorschlag fuer das WLAN-Passwort der Basis (WPA2, passt in die 63 Zeichen des '#NET'-Befehls)."""
    return "".join(secrets.choice(_PW_ZEICHEN) for _ in range(n))


class KeyStore:
    """Schluessel je Basis in ~/PyST/schluessel.json: {"<id>": "<64 hex>"}."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(base_dir(), KEY_FILE)

    def _load(self) -> Dict[str, str]:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            if os.name == "posix" and stat.S_IMODE(os.stat(self.path).st_mode) & 0o077:
                log.warning("Rechte von %s waren zu weit, setze 600", self.path)
                os.chmod(self.path, 0o600)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("%s nicht lesbar: %s", self.path, type(exc).__name__)
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, str) and _KEY.fullmatch(v)}

    def get(self, uid: str) -> Optional[str]:
        return self._load().get(uid)

    def put(self, uid: str, key: str) -> bool:
        """Schluessel ablegen. True = neu oder geaendert. Schreibfehler (OSError) gehen an den Aufrufer."""
        if not _ID.fullmatch(uid) or not _KEY.fullmatch(key):
            raise ValueError("Schlüsselzeile der Basis ungültig")
        data = self._load()
        if data.get(uid) == key.lower():
            return False
        data[uid] = key.lower()
        folder = os.path.dirname(self.path)
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=KEY_FILE, suffix=".tmp", dir=folder)    # mkstemp legt mit 600 an
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
