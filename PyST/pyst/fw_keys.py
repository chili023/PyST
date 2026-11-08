"""
Signatur von Update-Dateien (Format 2) am PC vorab pruefen. Das letzte Wort hat immer der Bootloader, diese Pruefung
erspart nur die Uebertragung und haelt die Download-Ablage sauber.

Regeln:
  aus dem Netz   bekannter Schluessel und gueltige Signatur, sonst abgelehnt
  von Hand       bekannter Schluessel: Signatur muss stimmen. Unbekannter key_id: darf weiter, mit Hinweis.

Die Signatur deckt den Kopf, und der enthaelt SHA-256 des Klartexts. Den kann nur der Bootloader nachrechnen: Der PC hat
keinen AES-Schluessel, fuer die verschluesselten Nutzdaten bleibt hier nur die CRC-32.
"""
from typing import Dict

from .bootproto import FORMAT_PLAIN, UpdateFile

# key_id -> oeffentlicher Ed25519-Schluessel als 64 Hex-Zeichen. Nur oeffentliche Schluessel, nie ein Testschluessel.
KNOWN_KEYS: Dict[int, str] = {
    2: "e89869c2a26bd9ae9019db56ad98d82c0e16d7d2996052f00d047984336e663b",   # Auslieferung, angelegt 08.11.2026
}

NO_LIBRARY = ("Paket „cryptography“ fehlt, PyST kann die Signatur nicht prüfen: pip install -r requirements.txt "
              "(oder den Ordner .venv löschen, das Startskript richtet ihn neu ein).")
GOOD, BAD, UNKNOWN_KEY, NOT_CHECKED = "gut", "falsch", "unbekannt", "ungeprueft"


class SignatureError(ValueError):
    pass


def verify(update: UpdateFile) -> str:
    """Signatur gegen die Tabelle: GOOD, BAD, UNKNOWN_KEY oder NOT_CHECKED, wenn `cryptography` fehlt."""
    public = KNOWN_KEYS.get(update.key_id)
    if public is None:
        return UNKNOWN_KEY
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return NOT_CHECKED
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public)).verify(update.signature, update.signed_data)
    except InvalidSignature:
        return BAD
    return GOOD


def _bad(update: UpdateFile) -> str:
    return f"Signatur ungültig: Die Datei passt nicht zu Schlüssel {update.key_id}, sie ist beschädigt oder gefälscht."


def check_download(update: UpdateFile) -> None:
    """Datei aus dem Netz: SignatureError, wenn sie nicht nachweislich von uns stammt."""
    if update.format == FORMAT_PLAIN:
        raise SignatureError("Unsignierte Datei (Format 1): Aus dem Netz nimmt PyST nur signierte Dateien.")
    result = verify(update)
    if result == UNKNOWN_KEY:
        raise SignatureError(f"Unbekannter Schlüssel {update.key_id}: Aus dem Netz nimmt PyST nur Dateien mit bekanntem "
                             "Schlüssel.")
    if result == NOT_CHECKED:
        raise SignatureError(NO_LIBRARY)
    if result == BAD:
        raise SignatureError(_bad(update))


def check_local(update: UpdateFile) -> str:
    """Von Hand gewaehlte Datei: -> Hinweis fuer die Anzeige, leer wenn alles passt. Falsche Signatur: SignatureError."""
    if update.format == FORMAT_PLAIN:
        return ""
    result = verify(update)
    if result == BAD:
        raise SignatureError(_bad(update))
    if result == UNKNOWN_KEY:
        return (f"Achtung: Schlüssel {update.key_id} ist diesem PyST nicht bekannt, die Signatur ist ungeprüft. "
                "Ob die Datei angenommen wird, entscheidet der Bootloader.")
    if result == NOT_CHECKED:
        return f"Achtung: {NO_LIBRARY} Ob die Datei angenommen wird, entscheidet der Bootloader."
    return ""
