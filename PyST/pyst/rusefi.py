"""
rusEFI-Steuergeraet als zweite Datenquelle (TunerStudio-Protokoll mit CRC-Rahmen).

Das Steuergeraet liefert ueber USB nur die Signatur und einen rohen Datenblock (Output Channels).
Namen, Lage im Block, Datentyp, Skalierung und Einheit jedes Kanals stehen ausschliesslich in der
passenden TunerStudio-.ini. Die holt sich PyST so:
  1. Zwischenspeicher ~/PyST/rusefi_ini/<Signatur>.ini
  2. vom USB-Laufwerk des Steuergeraets ("RUSEFI", Datei rusefi.ini.7z oder *.ini)
  3. von Hand gewaehlte Datei (z.B. aus dem TunerStudio-Projekt)

Rahmen:  [Laenge 2 B, big endian][Nutzdaten][CRC32 der Nutzdaten 4 B, big endian]
Antwort: Nutzdaten = [Status 1 B][Daten]   Status 0 = OK
"""
import collections
import glob
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

RUSEFI_VID = 0x0483          # STM32 Virtual COM Port (so meldet sich rusEFI am USB)
RUSEFI_PID = 0x5740
DEFAULT_RATE_HZ = 50.0
BUFFER_S = 240.0             # so viele Sekunden werden fuer die Laeufe vorgehalten
DEFAULT_CHANNELS = ["RPMValue", "TPSValue", "MAPValue", "lambdaValue", "AFRValue", "ignitionAdvanceCyl1",
                    "coolant", "intake", "VBatt", "actualLastInjection", "injectorDutyCycle"]
COLUMN_PREFIX = "rusefi_"    # Spaltenname in roh.csv = Praefix + Kanalname aus der .ini

# Einheiten der .ini (englisch) -> Anzeige
UNITS = {"deg C": "°C", "deg F": "°F", "deg": "°", "kph": "km/h", "volts": "V", "RPM": "1/min"}

_TYPES = {"U08": "B", "S08": "b", "U16": "H", "S16": "h", "U32": "I", "S32": "i", "F32": "f"}


# ============================================================================================== .ini lesen
@dataclass
class IniChannel:
    name: str
    fmt: str                 # struct-Format (Little Endian)
    offset: int
    unit: str = ""
    scale: float = 1.0
    translate: float = 0.0
    bits: Optional[Tuple[int, int]] = None   # (niedrigstes, hoechstes Bit) bei Bit-Feldern
    label: str = ""

    @property
    def size(self) -> int:
        return struct.calcsize("<" + self.fmt)

    def decode(self, block: bytes) -> float:
        raw = struct.unpack_from("<" + self.fmt, block, self.offset)[0]
        if self.bits:
            lo, hi = self.bits
            return float((raw >> lo) & ((1 << (hi - lo + 1)) - 1))
        return raw * self.scale + self.translate

    @property
    def title(self) -> str:
        return self.label or self.name


def _split_fields(text: str) -> List[str]:
    """Komma-getrennte Felder; Kommas in "..." und {...} gehoeren zum Feld."""
    out, cur, depth, quoted = [], "", 0, False
    for ch in text:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "{":
            depth += 1
        elif not quoted and ch == "}":
            depth -= 1
        if ch == "," and not quoted and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return out


def _strip_comment(line: str) -> str:
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ";" and not quoted:
            return line[:i]
    return line


class IniDef:
    """Die fuer PyST relevanten Teile einer TunerStudio-.ini von rusEFI."""

    def __init__(self, path: str):
        self.path = path
        self.signature = ""
        self.block_size = 0
        self.och_command = ""
        self.channels: "collections.OrderedDict[str, IniChannel]" = collections.OrderedDict()
        self._parse()

    def _parse(self):
        with open(self.path, encoding="latin-1") as fh:
            lines = fh.read().splitlines()
        defined, stack, section = set(), [], ""
        labels: Dict[str, str] = {}
        for raw in lines:
            line = _strip_comment(raw).strip()
            if not line:
                continue
            # Praeprozessor: #if/#else/#endif, #set/#define (einfache Variante wie TunerStudio)
            if line.startswith("#"):
                parts = line[1:].split(None, 1)
                kw = parts[0] if parts else ""
                arg = parts[1].strip() if len(parts) > 1 else ""
                if kw in ("set", "define"):
                    defined.add(re.split(r"[\s=]", arg, 1)[0])
                elif kw == "unset":
                    defined.discard(arg)
                elif kw == "if":
                    stack.append(arg in defined)
                elif kw == "else" and stack:
                    stack[-1] = not stack[-1]
                elif kw == "endif" and stack:
                    stack.pop()
                continue
            if not all(stack):
                continue
            if line.startswith("["):
                section = line
                continue
            if "=" not in line:
                continue
            key, val = (x.strip() for x in line.split("=", 1))
            if section == "[MegaTune]" and key == "signature" and not self.signature:
                self.signature = val.strip().strip('"')
            elif section == "[TunerStudio]" and key == "signature":
                self.signature = val.strip().strip('"')
            elif section == "[OutputChannels]":
                self._parse_channel(key, val)
            elif section == "[Datalog]" and key == "entry":
                f = _split_fields(val)
                if len(f) >= 2 and f[1].startswith('"'):
                    labels.setdefault(f[0], f[1].strip('"'))
        for name, lab in labels.items():
            if name in self.channels:
                self.channels[name].label = lab

    def _parse_channel(self, key: str, val: str):
        if key == "ochBlockSize":
            self.block_size = int(float(val))
            return
        if key == "ochGetCommand":
            self.och_command = val.strip('"')
            return
        f = _split_fields(val)
        if len(f) < 3 or f[0] not in ("scalar", "bits") or f[1] not in _TYPES:
            return                                  # Ausdruecke {...} und Arrays werden nicht gelesen
        try:
            offset = int(f[2])
        except ValueError:
            return
        if f[0] == "bits":
            m = re.match(r"\[(\d+):(\d+)\]", f[3] if len(f) > 3 else "")
            if m:
                self.channels[key] = IniChannel(key, _TYPES[f[1]], offset, bits=(int(m.group(1)), int(m.group(2))))
            return
        unit = f[3].strip('"') if len(f) > 3 and f[3].startswith('"') else ""
        unit = UNITS.get(unit.strip(), unit.strip())
        try:
            scale = float(f[4]) if len(f) > 4 else 1.0
            translate = float(f[5]) if len(f) > 5 else 0.0
        except ValueError:
            return                                  # Skalierung als Ausdruck: nicht unterstuetzt
        self.channels[key] = IniChannel(key, _TYPES[f[1]], offset, unit, scale, translate)

    def check(self) -> str:
        """Leerer Text = PyST kann mit dieser .ini Daten lesen."""
        if not self.signature:
            return "keine Signatur in der .ini"
        if not self.block_size:
            return "ochBlockSize fehlt in der .ini"
        if self.och_command and not self.och_command.startswith("O"):
            return f"unbekannter Lesebefehl {self.och_command!r}"
        if not self.channels:
            return "keine Kanaele in [OutputChannels]"
        return ""


# ============================================================================================== .ini finden
def ini_cache_dir() -> str:
    from .db import base_dir
    return os.path.join(base_dir(), "rusefi_ini")


def _safe(signature: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", signature).strip("_") or "unbekannt"


def cached_ini(signature: str) -> Optional[str]:
    p = os.path.join(ini_cache_dir(), _safe(signature) + ".ini")
    return p if os.path.exists(p) else None


def read_signature(path: str) -> str:
    try:
        return IniDef(path).signature
    except OSError:
        return ""


def store_in_cache(path: str) -> str:
    sig = read_signature(path)
    if not sig:
        raise ValueError(f"{os.path.basename(path)}: keine Signatur gefunden – keine rusEFI-.ini?")
    os.makedirs(ini_cache_dir(), exist_ok=True)
    target = os.path.join(ini_cache_dir(), _safe(sig) + ".ini")
    if os.path.abspath(path) != os.path.abspath(target):
        shutil.copyfile(path, target)
    return target


def _mount_points() -> List[str]:
    if sys.platform == "darwin":
        return glob.glob("/Volumes/*")
    if sys.platform.startswith("win"):
        return [f"{c}:\\" for c in "DEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{c}:\\")]
    user = os.environ.get("USER", "")
    return glob.glob(f"/media/{user}/*") + glob.glob(f"/run/media/{user}/*") + glob.glob("/media/*") + glob.glob("/mnt/*")


def drive_ini_files() -> List[str]:
    """.ini bzw. .ini.7z im Hauptverzeichnis eines rusEFI-USB-Laufwerks."""
    found = []
    for mp in _mount_points():
        try:
            names = os.listdir(mp)
        except OSError:
            continue
        for n in names:
            low = n.lower()
            if low.endswith(".ini.7z") or (low.endswith(".ini") and "rusefi" in (low + os.path.basename(mp).lower())):
                found.append(os.path.join(mp, n))
    return found


def extract_7z(archive: str, dest: str) -> List[str]:
    """Entpackt ein .7z mit py7zr oder einem vorhandenen Programm (bsdtar/tar, 7z)."""
    os.makedirs(dest, exist_ok=True)
    try:
        import py7zr
        with py7zr.SevenZipFile(archive) as z:
            z.extractall(dest)
    except ImportError:
        cmds = [["bsdtar", "-xf", archive, "-C", dest], ["7z", "x", "-y", f"-o{dest}", archive],
                ["7zz", "x", "-y", f"-o{dest}", archive], ["7za", "x", "-y", f"-o{dest}", archive],
                ["tar", "-xf", archive, "-C", dest]]
        for cmd in cmds:
            if shutil.which(cmd[0]) is None:
                continue
            if subprocess.run(cmd, capture_output=True).returncode == 0:
                break
        else:
            raise RuntimeError("Zum Entpacken von rusefi.ini.7z fehlt py7zr (pip install py7zr) oder 7-Zip")
    return [p for p in glob.glob(os.path.join(dest, "**", "*.ini"), recursive=True)]


def ini_from_drive(signature: str = "") -> Optional[str]:
    """Holt die .ini vom USB-Laufwerk in den Zwischenspeicher. Mit Signatur: nur die passende."""
    for f in drive_ini_files():
        candidates = [f]
        tmp = None
        if f.lower().endswith(".7z"):
            tmp = tempfile.mkdtemp(prefix="pyst_ini_")
            try:
                candidates = extract_7z(f, tmp)
            except Exception:
                candidates = []
        try:
            for c in candidates:
                sig = read_signature(c)
                if sig and (not signature or sig == signature):
                    return store_in_cache(c)
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
    return None


def resolve_ini(signature: str, manual: str = "") -> Tuple[Optional[str], str]:
    """(Pfad, Hinweis). Reihenfolge: passende Handauswahl, Zwischenspeicher, USB-Laufwerk, Handauswahl."""
    if manual and os.path.exists(manual) and read_signature(manual) == signature:
        return manual, "gewählte .ini"
    p = cached_ini(signature)
    if p:
        return p, "aus dem Zwischenspeicher"
    p = ini_from_drive(signature)
    if p:
        return p, "vom USB-Laufwerk des Steuergeräts"
    if manual and os.path.exists(manual):
        return manual, "gewählte .ini – Signatur passt NICHT zum Steuergerät"
    return None, "keine passende .ini gefunden"


# ============================================================================================== Verbindung
def list_rusefi_ports() -> List[Tuple[str, str]]:
    out = []
    if not list_ports:
        return out
    for p in list_ports.comports():
        text = f"{p.manufacturer or ''} {p.product or ''} {p.description or ''}"
        if "rusefi" in text.lower() or (p.vid == RUSEFI_VID and p.pid == RUSEFI_PID):
            out.append((p.device, (p.product or p.description or "").strip()))
    return out


def is_rusefi_port(device: str) -> bool:
    return any(d == device for d, _ in list_rusefi_ports())


class Protocol:
    """CRC-gerahmte Befehle ueber eine serielle Schnittstelle (oder ein Objekt mit read/write)."""

    def __init__(self, ser):
        self.ser = ser

    def request(self, payload: bytes) -> bytes:
        self.ser.write(struct.pack(">H", len(payload)) + payload + struct.pack(">I", zlib.crc32(payload)))
        hdr = self._read(2)
        n = struct.unpack(">H", hdr)[0]
        body = self._read(n)
        crc = struct.unpack(">I", self._read(4))[0]
        if crc != zlib.crc32(body):
            raise IOError("CRC-Fehler in der Antwort")
        if not body or body[0] != 0:
            raise IOError(f"Steuergerät meldet Status {body[0] if body else '–'}")
        return body[1:]

    def _read(self, n: int) -> bytes:
        data = self.ser.read(n)
        if len(data) != n:
            raise IOError("keine Antwort vom Steuergerät")
        return data

    def signature(self) -> str:
        return self.request(b"S").rstrip(b"\x00").decode("latin-1")

    def output_block(self, offset: int, count: int) -> bytes:
        return self.request(b"O" + struct.pack("<HH", offset, count))


class RusefiLink:
    """Liest den Datenblock zyklisch in einem Thread.

    Gelesen wird nur der Bytebereich, in dem die gewaehlten Kanaele liegen.
    latest    letzte Werte der gewaehlten Kanaele (Live-Anzeige)
    Puffer    (Zeit, Werte der gewaehlten Kanaele) der letzten BUFFER_S Sekunden fuer die Laeufe
    """

    def __init__(self, port: str, ini: Optional[IniDef] = None, channels: Optional[List[str]] = None,
                 rate_hz: float = DEFAULT_RATE_HZ, ser=None):
        if ser is None:
            if serial is None:
                raise RuntimeError("pyserial fehlt: pip install pyserial")
            ser = serial.Serial(port, 115200, timeout=0.5)
            time.sleep(0.05)
            ser.reset_input_buffer()
        self.port = port
        self.ser = ser
        self.proto = Protocol(ser)
        self.signature = self.proto.signature()
        self.ini: Optional[IniDef] = None
        self.rate_hz = rate_hz
        self.error = ""
        self.latest: Dict[str, float] = {}
        self.latest_t = 0.0
        self._range = (0, 0)             # gelesener Bytebereich (Anfang, Laenge)
        self.polls = 0
        self.measured_hz = 0.0
        self._lock = threading.Lock()
        self._chans: List[IniChannel] = []
        self._buf: "collections.deque" = collections.deque()
        self._stop = False
        self._thread: Optional[threading.Thread] = None
        if ini is not None:
            self.set_ini(ini, channels)

    # ---------------------------------------------------------------- Einstellungen
    @property
    def signature_ok(self) -> bool:
        return self.ini is not None and self.ini.signature == self.signature

    def set_ini(self, ini: IniDef, channels: Optional[List[str]] = None):
        with self._lock:
            self.ini = ini
        self.set_channels(channels if channels is not None else DEFAULT_CHANNELS)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def set_channels(self, names: List[str]):
        with self._lock:
            self._chans = [self.ini.channels[n] for n in names if self.ini and n in self.ini.channels]
            self._buf = collections.deque(maxlen=int(BUFFER_S * max(self.rate_hz, 1.0)))
            self.latest = {}
            if self._chans:
                lo = min(c.offset for c in self._chans)
                hi = max(c.offset + c.size for c in self._chans)
                self._range = (lo, hi - lo)
            else:
                self._range = (0, 0)

    def set_rate(self, hz: float):
        self.rate_hz = max(1.0, float(hz))
        self.set_channels(self.channel_names())

    def channel_names(self) -> List[str]:
        return [c.name for c in self._chans]

    def close(self):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=1.0)
        try:
            self.ser.close()
        except Exception:
            pass

    # ---------------------------------------------------------------- Lesen
    def _run(self):
        next_t = time.time()
        rate_t0, rate_n = time.time(), 0
        while not self._stop:
            try:
                with self._lock:
                    (lo, n), chans = self._range, list(self._chans)
                if not n:
                    time.sleep(0.1)
                    continue
                t_req = time.time()
                part = self.proto.output_block(lo, n)
                t = (t_req + time.time()) / 2.0          # Mitte zwischen Anfrage und Antwort
                if len(part) < n:
                    raise IOError("Antwort zu kurz")
                block = bytes(lo) + part                 # Offsets der .ini gelten ab Blockanfang
                vals = tuple(c.decode(block) for c in chans)
                with self._lock:
                    if chans == self._chans:             # Auswahl inzwischen nicht geaendert
                        self.latest = {c.name: v for c, v in zip(chans, vals)}
                        self.latest_t = t
                        self._buf.append((t, vals))
                self.polls += 1
                rate_n += 1
                self.error = ""
            except Exception as exc:
                self.error = str(exc)
                time.sleep(0.5)
                try:
                    self.ser.reset_input_buffer()
                except Exception:
                    pass
            if time.time() - rate_t0 >= 1.0:
                self.measured_hz = rate_n / (time.time() - rate_t0)
                rate_t0, rate_n = time.time(), 0
            next_t += 1.0 / self.rate_hz
            delay = next_t - time.time()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.time()

    def alive(self) -> bool:
        return time.time() - self.latest_t < 1.0

    def value(self, name: str) -> Optional[float]:
        """Aktueller Wert eines gewaehlten Kanals."""
        with self._lock:
            return self.latest.get(name) if self.alive() else None

    def snapshot(self, t_from: float, t_to: float) -> Optional[Dict]:
        """Werte der gewaehlten Kanaele zwischen zwei PC-Zeiten (fuer das Speichern eines Laufs)."""
        with self._lock:
            rows = [r for r in self._buf if t_from <= r[0] <= t_to]
            chans = list(self._chans)
        if not rows or not chans:
            return None
        return {
            "t": np.array([r[0] for r in rows]),
            "values": np.array([r[1] for r in rows], float).reshape(len(rows), len(chans)),
            "channels": [{"name": c.name, "label": c.title, "unit": c.unit, "bits": c.bits is not None}
                         for c in chans],
            "signature": self.signature,
            "ini": self.ini.path if self.ini else "",
            "rate_hz": self.measured_hz or self.rate_hz,
        }


def resample(snap: Dict, t: np.ndarray) -> Dict[str, np.ndarray]:
    """Kanaele auf die Zeitpunkte t (Telegramme des Pruefstands) legen.
    Analogwerte linear, Bit-Felder mit dem letzten Wert; ausserhalb der Aufzeichnung NaN."""
    ts, vals = snap["t"], snap["values"]
    out = {}
    outside = (t < ts[0]) | (t > ts[-1])
    for j, ch in enumerate(snap["channels"]):
        if ch["bits"]:
            idx = np.clip(np.searchsorted(ts, t, side="right") - 1, 0, len(ts) - 1)
            v = vals[idx, j].astype(float)
        else:
            v = np.interp(t, ts, vals[:, j])
        v[outside] = np.nan
        out[COLUMN_PREFIX + ch["name"]] = v
    return out
