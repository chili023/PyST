"""
Verbindung zur Messelektronik (PST-STM32-Firmware 1.x oder Arduino-Mega-Sketch 3.x).

Protokoll: 'v' Info, 'e' Klima "T;P;H", 'm' Start, 's' Stop (nur STM-Firmware),
Telegramm "Zyklus;Messfrequenz;f_Zuend;f_Rolle;EGT1;AFR;EGT2\\n".

PST-Diag 1 (Mega ab 3.1.0, F446 ab 1.1.0, docs/04_PST_Diag_1.md): beim Verbinden "#?" -> "#CAP;…".
Dann zusaetzlich Modus D (Diagnose-Telegramm "D;…"), Flanken-Bursts ("B;…"), "#RUN", "#GET/#SET/#SAVE".
Geraete ohne "#CAP" (Mega 3.0.0, F446 1.0.0) laufen wie bisher nur mit den normalen Telegrammen.
Zeilenende \\n, \\r oder \\r\\n; unbekannte Zeilen und zusaetzliche Felder werden ignoriert.

PyST-Basis (docs/04_PST_Diag_1.md §10, basis.py): '#CAP' mit 'ign=zange|none' und 'hw=…', angehaengte D-Felder
'ign_peak_mv;ign_src', Ereigniszeilen 'E;…', Antworten '#ZANGE/#NET/#DOSE/#PAIR', Logzeilen 'L;…' der USB-Zange.
Ports: seriell (USB), Simulator ('SIM…') oder TCP zur Basis per WLAN ('tcp://192.168.4.1:2323',
'tcp://pyst-basis.local').
"""
import os
import queue
import re
import socket
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # nur Simulator moeglich
    serial = None
    list_ports = None

from .basis import (BASIS_AP_IP, BASIS_MDNS_NAME, BASIS_TCP_PORT, ZANGE_USB_ID, BasisCommands, parse_event,
                    parse_log_line, parse_zange)
from .diag import DIAG_FIELDS, Burst, parse_kv
from .sim import BASIS_FAULTS, FAULT_PRESET, BasisSimSerial, ReplaySource, SimEngine, SimSerial, ZangeUsbSimSerial

SIM_PORT = "SIM"
# Mitgelieferte echte Laeufe fuer den Simulator: Portname -> (Datei, Beschreibung)
SIM_RUNS = {
    "SIM:161802": ("lauf_161802.xml", "Simulator: echter Lauf 26.05.26 16:18 (32,0 PS, Aprilia SR2)"),
}
DEFAULT_SIM_PORT = "SIM:161802"


def resolve_sim_file(port: str) -> str:
    """SIM:<Name> -> mitgelieferte Datei, SIM:<Pfad> -> Pfad."""
    if port in SIM_RUNS:
        return os.path.join(os.path.dirname(__file__), "data", SIM_RUNS[port][0])
    return port[len(SIM_PORT) + 1:]


@dataclass
class Frame:
    t: float            # PC-Zeit beim Empfang [s]
    cycle: int
    rate: float         # Messfrequenz [Hz]
    ign_hz: float
    roll_hz: float
    egt1: float = 0.0
    afr: float = 0.0
    egt2: float = 0.0
    diag: Optional[Dict[str, float]] = None   # Zusatzfelder des D-Telegramms (diag.DIAG_FIELDS), sonst None
                                              # PyST-Basis zusaetzlich 'ign_peak_mv', 'ign_src'


# Simulator-Varianten: Portname -> Beschreibung
SIM_VARIANTS = [
    (SIM_PORT, "Simulator: synthetischer Motor (PST-Diag 1)"),
    (SIM_PORT + ":stoerung", "Simulator: synthetischer Motor mit Störungen (Zündung/Rolle, PST-Diag 1)"),
    (SIM_PORT + ":mega", "Simulator: Arduino-Mega-Sketch 3.1.0 (PST-Diag 1)"),
    (SIM_PORT + ":mega300", "Simulator: alter Arduino-Mega-Sketch 3.0.0 (ohne Diagnose)"),
    (SIM_PORT + ":mega300:stoerung", "Simulator: alter Mega 3.0.0 mit Störungen (ohne Diagnose)"),
    (SIM_PORT + ":basis", "Simulator: PyST-Basis mit gekoppelter Funk-Zange und Steckdosen"),
    (SIM_PORT + ":basis:stoerung", "Simulator: PyST-Basis, Funk mit Paketverlust und Zangen-Abbrüchen"),
    (SIM_PORT + ":basis:ohne", "Simulator: PyST-Basis ohne Zange (Koppeln vorführen)"),
    (SIM_PORT + ":zange", "Simulator: Zange direkt per USB (Zündungs-Oszi, Log auslesen)"),
]
SIM_NAMES = {name for name, _d in SIM_VARIANTS}
TCP_PREFIX = "tcp://"
TCP_DEFAULT = f"{TCP_PREFIX}{BASIS_AP_IP}:{BASIS_TCP_PORT}"
TCP_PORTS = [(TCP_DEFAULT, "PyST-Basis per WLAN (Basis als Access Point „PyST-Basis“)"),
             (f"{TCP_PREFIX}{BASIS_MDNS_NAME}:{BASIS_TCP_PORT}", "PyST-Basis per WLAN (im Werkstattnetz, mDNS)")]


def is_tcp_port(port: str) -> bool:
    return port.lower().startswith(TCP_PREFIX)


def parse_tcp_port(port: str) -> Tuple[str, int]:
    """'tcp://host[:port]' -> (host, port)"""
    rest = port[len(TCP_PREFIX):].strip().rstrip("/")
    if rest.startswith("["):                                 # IPv6 [::1]:2323
        host, _, p = rest[1:].partition("]")
        return host, int(p.lstrip(":") or BASIS_TCP_PORT)
    host, _, p = rest.rpartition(":") if rest.count(":") == 1 else (rest, "", "")
    return host, int(p or BASIS_TCP_PORT)


class TcpSerial:
    """TCP-Verbindung zur Basis (Port 2323) mit der Schnittstelle von serial.Serial (read, write, in_waiting).
    Namen '*.local' werden per mDNS aufgeloest."""

    def __init__(self, host: str, port: int = BASIS_TCP_PORT, timeout: float = 3.0):
        from . import mdns
        addr = mdns.resolve(host) if host.endswith(".local") else host
        if not addr:
            raise OSError(f"{host} nicht gefunden (mDNS)")
        self.port = f"{TCP_PREFIX}{host}:{port}"
        self.sock = socket.create_connection((addr, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(0.05)
        self.dead = False

    @property
    def in_waiting(self) -> int:
        return 0 if self.dead else 4096

    def read(self, n: int = 1) -> bytes:
        if self.dead:
            time.sleep(0.05)
            return b""
        try:
            data = self.sock.recv(max(1, n))
        except socket.timeout:
            return b""
        except OSError:
            self.dead = True
            return b""
        if not data:                                           # Gegenstelle hat geschlossen
            self.dead = True
        return data

    def write(self, data: bytes) -> int:
        if self.dead:
            raise OSError("Verbindung zur Basis getrennt")
        try:
            self.sock.sendall(data)
        except OSError:
            self.dead = True
            raise
        return len(data)

    def close(self):
        self.dead = True
        try:
            self.sock.close()
        except OSError:
            pass


def list_serial_ports() -> List[Tuple[str, str]]:
    ports = [(name, desc) for name, (_f, desc) in SIM_RUNS.items()]
    ports += SIM_VARIANTS
    if list_ports:
        for p in list_ports.comports():
            desc = p.description or ""
            if (p.vid, p.pid) == ZANGE_USB_ID:
                desc = "PyST-Zange per USB? (ESP32-USB) – " + desc
            ports.append((p.device, desc))
    ports += TCP_PORTS
    return ports


def zange_usb_ports() -> List[str]:
    """Ports mit der USB-Kennung der Zange (ESP32-C3 USB-Serial-JTAG). Ob es eine Zange ist, zeigt erst '#CAP'."""
    if not list_ports:
        return []
    return [p.device for p in list_ports.comports() if (p.vid, p.pid) == ZANGE_USB_ID]


# USB-Kennungen, unter denen das PST-Messboard auftaucht (fuer das automatische Verbinden)
PST_USB_IDS = {
    (0x0483, 0x374B), (0x0483, 0x374E), (0x0483, 0x374F), (0x0483, 0x3752), (0x0483, 0x3748),  # ST-Link (Nucleo)
}
PST_USB_VENDORS = {0x2341, 0x2A03}                                  # Arduino (Mega)


def pst_board_ports() -> List[str]:
    """Nur Ports, die nach USB-Kennung das PST-Messboard sein koennen (ST-Link des Nucleo, Arduino Mega)."""
    if not list_ports:
        return []
    out = []
    for p in list_ports.comports():
        text = f"{p.manufacturer or ''} {p.product or ''} {p.description or ''}".lower()
        if "rusefi" in text:
            continue
        if (p.vid, p.pid) in PST_USB_IDS or p.vid in PST_USB_VENDORS:
            out.append(p.device)
    return out


def is_pst_firmware(info: str) -> bool:
    """Antwort auf 'v': PST-STM32-Firmware oder Arduino-Mega-Sketch 3.x."""
    return "PST-STM32" in info or info.lstrip().startswith("Version 3") or "\nVersion 3" in info


def guess_port() -> Optional[str]:
    for dev, desc in list_serial_ports()[1:]:
        t = f"{dev} {desc}".lower()
        if "rusefi" in t:                      # Steuergeraet, eigener Reiter
            continue
        if "stlink" in t or "st-link" in t or "usbmodem" in t or "acm" in t:
            return dev
    return None


def parse_frame(line: str, t: float) -> Optional[Frame]:
    f = line.strip().split(";")
    if len(f) < 4:
        return None
    try:
        vals = [float(x) for x in f[:7]]
    except ValueError:
        return None
    while len(vals) < 7:
        vals.append(0.0)
    return Frame(t, int(vals[0]), vals[1], vals[2], vals[3], vals[4], vals[5], vals[6])


def _num(x: str) -> float:
    try:
        return float(x)
    except ValueError:
        return float("nan")


def parse_diag(line: str, t: float) -> Optional[Frame]:
    """D;zyklus;t_ms;rate;ign;roll;egt1;afr;egt2;ign_n;ign_pmin;ign_pmax;ign_rej;roll_n;roll_pmin;roll_pmax;
    roll_rej;flags(hex) -> Frame mit diag. Mindestens bis roll noetig; fehlende Zusatzfelder = nan,
    weitere Felder am Ende werden ignoriert."""
    f = line.strip().split(";")
    if len(f) < 6 or f[0] != "D":
        return None
    try:
        cycle = int(f[1])
        t_ms, rate, ign, roll = float(f[2]), float(f[3]), float(f[4]), float(f[5])
    except ValueError:
        return None
    rest = [_num(x) for x in f[6:9]]
    rest += [0.0] * (3 - len(rest))
    d: Dict[str, float] = {"t_ms": t_ms}
    for k, x in zip(DIAG_FIELDS[1:-1], f[9:17]):
        d[k] = _num(x)
    for k in DIAG_FIELDS[1:-1]:
        d.setdefault(k, float("nan"))
    try:
        d["flags"] = int(f[17], 16) if len(f) > 17 and f[17] else 0
    except ValueError:
        d["flags"] = 0
    # PyST-Basis: angehaengt ign_peak_mv (Mittel der Spitzenwerte im Fenster), ign_src (0/1/2)
    if len(f) > 18 and f[18] != "":
        d["ign_peak_mv"] = _num(f[18])
        d["ign_src"] = _num(f[19]) if len(f) > 19 and f[19] != "" else float("nan")
    return Frame(t, cycle, rate, ign, roll, rest[0], rest[1], rest[2], diag=d)


def parse_burst_edge(line: str) -> Optional[Tuple[int, str, int, int]]:
    """B;idx;ch;lvl;ticks -> (idx, 'I'/'R', 0/1, ticks)"""
    f = line.strip().split(";")
    if len(f) < 5 or f[0] != "B":
        return None
    try:
        return int(f[1]), f[2].strip(), int(f[3]), int(f[4]) & 0xFFFFFFFF
    except ValueError:
        return None


def parse_line(line: str, t: float):
    """Eine empfangene Zeile einordnen -> (Art, Inhalt):
    'F' normales Telegramm (Frame), 'D' Diagnose-Telegramm (Frame mit diag), 'B' Burst-Flanke,
    '#' Antwort/Status (Text), 'E' Ereignis der Basis (basis.BasisEvent), 'L' Logzeile der Zange
    (basis.LogEntry), 'T' sonstiger Text (Info, Klima), None = leer."""
    line = line.strip()
    if not line:
        return None
    if line.startswith("E;"):
        ev = parse_event(line, t)
        return ("E", ev) if ev is not None else ("T", line)
    if line.startswith("L;"):
        le = parse_log_line(line)
        return ("L", le) if le is not None else ("T", line)
    if line.startswith("D;"):
        fr = parse_diag(line, t)
        return ("D", fr) if fr is not None else ("T", line)
    if line.startswith("B;"):
        e = parse_burst_edge(line)
        return ("B", e) if e is not None else ("T", line)
    if line.startswith("#"):
        return "#", line
    fr = parse_frame(line, t)
    # Telegramm = mindestens 4 Zahlenfelder und Zyklus als Ganzzahl (Klima "21.5;96512;45.3" ist keins)
    if fr is not None and line.count(";") >= 3 and "." not in line.split(";")[0]:
        return "F", fr
    return "T", line


def parse_caps(line: str) -> Dict[str, str]:
    """'#CAP;proto=1;fw=PST-Mega 3.1.0;tick_ns=62.5;…' -> Dict"""
    return parse_kv(line)


_EOL = re.compile(rb"\r\n|\r|\n")


class DynoLink(BasisCommands):
    """Serieller Port, TCP (Basis per WLAN) oder Simulator; ein Lesethread zerlegt die Zeilen."""

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.replay = None
        if port in SIM_NAMES and port.split(":")[1:2] in (["basis"], ["zange"]):
            parts = port.split(":")[1:]
            if parts[0] == "zange":
                self.ser = ZangeUsbSimSerial()
            else:
                self.ser = BasisSimSerial(paired="ohne" not in parts,
                                          faults=BASIS_FAULTS if "stoerung" in parts else None)
        elif is_tcp_port(port):
            host, tport = parse_tcp_port(port)
            self.ser = TcpSerial(host, tport)
        elif port in SIM_NAMES:
            # SIM[:mega|:mega300][:stoerung]
            parts = port.split(":")[1:]
            mega = "mega" in parts or "mega300" in parts
            faults = FAULT_PRESET if "stoerung" in parts else None
            self.ser = SimSerial(engine=SimEngine(rate=61.0) if mega else None, mega=mega,
                                 diag="mega300" not in parts, faults=faults)
        elif port.startswith(SIM_PORT + ":"):
            # SIM:<Datei> = gespeicherten Lauf abspielen
            self.replay = ReplaySource(resolve_sim_file(port))
            self.ser = SimSerial(source=self.replay)
        else:
            if serial is None:
                raise RuntimeError("pyserial fehlt: pip install pyserial")
            self.ser = serial.Serial(port, baud, timeout=0.05)
        self.frames: "queue.Queue[Frame]" = queue.Queue()
        self.text_lines: "queue.Queue[str]" = queue.Queue()
        self.replies: "queue.Queue[str]" = queue.Queue()      # '#'-Antworten (ohne #BURST;BEGIN/END)
        self.bursts: "queue.Queue[Burst]" = queue.Queue()     # fertige Flankenaufnahmen
        self.taps: List["queue.Queue[Frame]"] = []            # weitere Empfaenger aller Telegramme (Oszi)
        self.events: "queue.Queue" = queue.Queue()            # Ereigniszeilen der Basis (basis.BasisEvent)
        self.log_lines: "queue.Queue" = queue.Queue()         # Logzeilen der USB-Zange (basis.LogEntry)
        self.zange = None                                     # letzter Zangenstatus (basis.ZangeStatus)
        self._ms_offset: Optional[float] = None               # PC-Zeit - Geraetezeit (aus D-Telegrammen)
        self._ms_last = 0.0                                   # Geraetezeit des letzten D-Telegramms [ms]
        self._cmd_lock = threading.RLock()                    # '#'-Befehle nacheinander (GUI + Hintergrund)
        self.caps: Dict[str, str] = {}                        # Antwort auf '#?' (leer = altes Geraet)
        self.mode = "N"
        self._burst: Optional[Burst] = None                   # Aufnahme, deren B-Zeilen gerade kommen
        self._pending_request: Dict[str, str] = {}
        self._burst_t = 0.0
        self.lost = 0
        self.received = 0
        self.last_frame_t = 0.0
        self.is_stm = True      # nach info(): False = Arduino-Mega-Sketch ('m' schaltet um, kein 's')
        self._last_cycle: Optional[int] = None
        self._stop = False
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    @property
    def has_diag(self) -> bool:
        """Geraet spricht PST-Diag 1 (Modus D, Bursts, Einstellungen)."""
        return bool(self.caps)

    @property
    def is_tcp(self) -> bool:
        return isinstance(self.ser, TcpSerial)

    @property
    def alive(self) -> bool:
        """False, wenn die TCP-Verbindung zur Basis abgerissen ist."""
        return not getattr(self.ser, "dead", False)

    @property
    def default_mode(self) -> str:
        """Telegramm-Modus ausserhalb der Dauermessung. Basis mit Zange: 'D', damit die Zuendstaerke
        (ign_peak_mv) auch in Leistungslaeufen aufgezeichnet wird – die Basis ist ohnehin kein LabVIEW-Geraet."""
        return "D" if self.has_diag and self.ign_source == "zange" else "N"

    def event_pc_time(self, master_ms: float) -> float:
        """Geraetezeit eines Ereignisses (ms) -> PC-Zeit, ueber die Zeitstempel der D-Telegramme."""
        if self._ms_offset is None:
            return time.time()
        # Abstand zur Geraetezeit des letzten D-Telegramms (uint32 ms, laeuft ueber)
        d = (int(master_ms) - int(self._ms_last) + (1 << 31)) % (1 << 32) - (1 << 31)
        t = self._ms_offset + (self._ms_last + d) / 1000.0
        now = time.time()
        if abs(t - now) > 30.0:                                 # Ueberlauf/Neustart: Empfangszeit nehmen
            return now
        return t

    def add_tap(self) -> "queue.Queue[Frame]":
        q: "queue.Queue[Frame]" = queue.Queue()
        self.taps.append(q)
        return q

    def remove_tap(self, q):
        if q in self.taps:
            self.taps.remove(q)

    def close(self):
        try:
            self.ensure_stopped()
            if self.has_diag and self.mode != "N":
                self.send("#MODE N")
        except Exception:
            pass
        self._stop = True
        time.sleep(0.1)
        self.ser.close()

    def send(self, cmd: str):
        self.ser.write((cmd + "\n").encode("latin-1"))

    def _reader(self):
        buf = b""
        while not self._stop:
            try:
                chunk = self.ser.read(self.ser.in_waiting or 1)
            except Exception:
                time.sleep(0.1)
                continue
            if not chunk:
                continue
            buf += chunk
            while True:
                m = _EOL.search(buf)
                if m is None:
                    break
                raw, buf = buf[:m.start()], buf[m.end():]
                self._line(raw.decode("latin-1"))
            if len(buf) > 4096:                     # Muell ohne Zeilenende
                buf = b""

    def _line(self, line: str):
        r = parse_line(line, time.time())
        if r is None:
            return
        kind, obj = r
        if kind in ("F", "D"):
            self._count(obj.cycle)
            self.last_frame_t = obj.t
            if kind == "D" and obj.diag and obj.diag.get("t_ms") == obj.diag.get("t_ms"):
                off = obj.t - obj.diag["t_ms"] / 1000.0           # kleinster Versatz = geringste Laufzeit
                if self._ms_offset is None or off < self._ms_offset or off - self._ms_offset > 1.0:
                    self._ms_offset = off
                else:
                    self._ms_offset += (off - self._ms_offset) * 0.001   # langsame Drift nachfuehren
                self._ms_last = obj.diag["t_ms"]
            self.frames.put(obj)
            for q in list(self.taps):
                q.put(obj)
        elif kind == "B":
            if self._burst is not None:
                self._burst.edges.append(obj)
                self._burst_t = time.time()
        elif kind == "E":
            obj.t_pc = self.event_pc_time(obj.master_ms)
            self.events.put(obj)
        elif kind == "L":
            self.log_lines.put(obj)
        elif kind == "#":
            if obj.startswith("#ZANGE;"):
                st = parse_zange(obj)
                if st is not None and any(k in st.extra for k in ("akku_pct", "rssi", "funken", "akku_mv")):
                    self.zange = st
            if obj.startswith("#BURST;BEGIN") or obj.startswith("#OK;BURST;STOP"):
                self._abort_burst()                     # vorige Ausgabe ohne END = abgebrochen
            if obj.startswith("#BURST;BEGIN"):
                kv = parse_kv(obj)
                req = dict(self._pending_request)
                self._burst_t = time.time()
                self._burst = Burst(n=int(kv.get("n", 0) or 0), tick_ns=float(kv.get("tick_ns", 0) or 0)
                                    or float(self.caps.get("tick_ns", 62.5) or 62.5),
                                    trig_idx=int(kv.get("trig_idx", -1) or -1), ch=kv.get("ch", "A"),
                                    request=req, t_pc=time.time())
            elif obj.startswith("#BURST;END"):
                if self._burst is not None:
                    kv = parse_kv(obj)
                    self._burst.lost = int(kv.get("lost", 0) or 0)
                    self._burst.complete = True
                    self._burst.edges.sort(key=lambda e: e[0])
                    self.bursts.put(self._burst)
                self._burst = None
            else:
                self.replies.put(obj)
        else:
            self.text_lines.put(obj)

    def _abort_burst(self):
        """Burst-Ausgabe ohne #BURST;END (z.B. '#BURST STOP' waehrend der Ausgabe): als unvollstaendig melden."""
        if self._burst is not None:
            b, self._burst = self._burst, None
            b.complete = False
            b.edges.sort(key=lambda e: e[0])
            self.bursts.put(b)

    def check_burst_timeout(self, seconds: float = 3.0):
        """Kommt laenger keine B-Zeile mehr, gilt die Ausgabe als abgebrochen."""
        if self._burst is not None and time.time() - self._burst_t > seconds:
            self._abort_burst()

    def _count(self, cycle: int):
        if self._last_cycle is not None:
            expected = (self._last_cycle + 1) & 0xFFFF
            if cycle != expected and cycle != 1:
                self.lost += (cycle - expected) & 0xFFFF
        self._last_cycle = cycle
        self.received += 1

    def drain_frames(self) -> List[Frame]:
        out = []
        while True:
            try:
                out.append(self.frames.get_nowait())
            except queue.Empty:
                return out

    def _collect_text(self, seconds: float) -> List[str]:
        end = time.time() + seconds
        lines = []
        while time.time() < end:
            try:
                lines.append(self.text_lines.get(timeout=0.05))
            except queue.Empty:
                pass
        return lines

    def _flush(self):
        self.drain_frames()
        while not self.text_lines.empty():
            self.text_lines.get_nowait()
        while not self.replies.empty():
            self.replies.get_nowait()

    def streaming(self) -> bool:
        return time.time() - self.last_frame_t < 0.5

    def info(self) -> str:
        self._flush()
        self.send("v")
        text = "\n".join(self._collect_text(0.6))
        if not text and not self.port.startswith(SIM_PORT) and not self.is_tcp:
            # Arduino Mega startet beim Oeffnen des Ports per DTR neu (Bootloader ~2 s)
            time.sleep(1.8)
            self._flush()
            self.send("v")
            text = "\n".join(self._collect_text(0.6))
        # Mega 1.x antwortet nicht auf 'v', Mega 3.x mit "Version 3...", STM mit "PST-STM32"
        self.is_stm = "PST-STM32" in text or (self.port.startswith(SIM_PORT) and ":mega" not in self.port)
        self.probe_caps()
        return text

    # ------------------------------------------------------------------ PST-Diag 1
    def probe_caps(self, timeout: float = 0.3) -> Dict[str, str]:
        """'#?' senden; antwortet das Geraet nicht binnen `timeout` mit '#CAP', ist es ein altes Geraet."""
        self.caps = {}
        while not self.replies.empty():
            self.replies.get_nowait()
        self.send("#?")
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self.replies.get(timeout=0.02)
            except queue.Empty:
                continue
            if line.startswith("#CAP"):
                self.caps = parse_caps(line)
                self.caps.setdefault("fw", "")
                break
        # alte F446-Firmware antwortet auf das '?' mit der Versionsinfo – nicht als Text liegen lassen
        time.sleep(0.05)
        while not self.text_lines.empty():
            self.text_lines.get_nowait()
        return self.caps

    def command(self, cmd: str, timeout: float = 1.0, done=None, until=None, progress=None) -> List[str]:
        """'#'-Befehl senden und die Antwortzeilen bis einschliesslich '#OK…'/'#ERR…' liefern
        (bei #GET also alle '#SET;…'-Zeilen). Leere Liste = keine Antwort.
        done(line): eigenes Ende der Antwort (z.B. '#ZANGE;…'); until(): zusaetzliche Ende-Bedingung;
        progress(): wird waehrend des Wartens aufgerufen. Befehle aus mehreren Threads laufen nacheinander."""
        if not self.has_diag:
            return []
        done = done or (lambda line: line.startswith("#OK") or line.startswith("#ERR"))
        with self._cmd_lock:
            while not self.replies.empty():
                self.replies.get_nowait()
            try:
                self.send(cmd)
            except OSError:
                return []
            out: List[str] = []
            end = time.time() + timeout
            while time.time() < end:
                if progress:
                    progress()
                try:
                    line = self.replies.get(timeout=0.02)
                except queue.Empty:
                    if until is not None and until():
                        break
                    continue
                out.append(line)
                if done(line):
                    break
            return out

    @staticmethod
    def ok(lines: List[str]) -> bool:
        return bool(lines) and lines[-1].startswith("#OK")

    def set_mode(self, mode: str) -> bool:
        """'N' normal (LabVIEW-kompatibel) oder 'D' Diagnose-Telegramm."""
        if not self.has_diag:
            return mode == "N"
        if self.ok(self.command(f"#MODE {mode}")):
            self.mode = mode
            return True
        return False

    def request_burst(self, ch: str = "I", n: int = 200, edge: str = "F", trig_pct: Optional[float] = None,
                      pre: Optional[int] = None) -> List[str]:
        """Flankenaufnahme anfordern. Das Ergebnis kommt spaeter ueber self.bursts (Burst)."""
        cmd = f"#BURST {ch} {int(n)} {edge}"
        if trig_pct:
            cmd += f" T{trig_pct:g}"
        if pre is not None:
            cmd += f" P{int(pre)}"
        req = {"ch": ch, "n": str(int(n)), "edge": edge, "trig": f"{trig_pct:g}" if trig_pct else "",
               "pre": "" if pre is None else str(int(pre))}
        self._pending_request = req                        # Parameter fuer die kommende Aufnahme merken
        return self.command(cmd)

    def stop_burst(self) -> bool:
        return self.ok(self.command("#BURST STOP"))

    def get_settings(self) -> List[Tuple[str, str]]:
        """#GET -> [(Schluessel, Wert)]"""
        out = []
        for line in self.command("#GET", timeout=1.5):
            if line.startswith("#SET;") and "=" in line:
                k, v = line[5:].split("=", 1)
                out.append((k.strip(), v.strip()))
        return out

    def set_setting(self, key: str, value: str) -> List[str]:
        return self.command(f"#SET {key} {value}")

    def ensure_stopped(self):
        """Ausgabe anhalten. Mega: 'm' schaltet um, also nur senden, wenn er gerade sendet."""
        if self.has_diag:
            self.send("#RUN 0")                  # eindeutig, auch beim Mega
            time.sleep(0.15)
            self.drain_frames()
            self.last_frame_t = 0.0
        elif self.is_stm:
            self.send("s")
            time.sleep(0.15)
        elif self.streaming():
            self.send("m")                       # Mega: Umschalten = aus
            time.sleep(0.3)
            self.drain_frames()
            self.last_frame_t = 0.0              # sonst gilt er noch 0,5 s als "sendet"

    def climate(self) -> Optional[Tuple[float, float, float]]:
        """Klimadaten wie LabVIEW ('e'); stoppt eine laufende Ausgabe. -> (degC, mbar, %)"""
        self.ensure_stopped()
        self._flush()
        self.send("e")
        end = time.time() + 0.7
        while time.time() < end:                      # zurueck, sobald die Antwort da ist
            try:
                line = self.text_lines.get(timeout=0.02)
            except queue.Empty:
                continue
            parts = line.split(";")
            if len(parts) >= 2:
                try:
                    t, p = float(parts[0]), float(parts[1]) / 100.0
                    h = float(parts[2]) if len(parts) > 2 else 0.0
                    return t, p, h
                except ValueError:
                    continue
        return None

    def drain_events(self) -> list:
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out

    def start(self):
        """Messung starten (Zyklus- und Verlustzaehler zuruecksetzen)."""
        running = self.streaming()
        self._flush()
        self._last_cycle = None
        self.lost = self.received = 0
        if self.has_diag:
            self.send("#RUN 1")
        elif self.is_stm or not running:
            self.send("m")

    def stop(self):
        self.ensure_stopped()
