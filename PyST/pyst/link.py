"""
Verbindung zur Messelektronik (PST-STM32-Firmware 1.0 oder Arduino-Mega-Sketch 3.x).

Protokoll: 'v' Info, 'e' Klima "T;P;H", 'm' Start, 's' Stop (nur STM-Firmware),
Telegramm "Zyklus;Messfrequenz;f_Zuend;f_Rolle;EGT1;AFR;EGT2\\n".
"""
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # nur Simulator moeglich
    serial = None
    list_ports = None

from .sim import ReplaySource, SimEngine, SimSerial

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


def list_serial_ports() -> List[Tuple[str, str]]:
    ports = [(name, desc) for name, (_f, desc) in SIM_RUNS.items()]
    ports.append((SIM_PORT, "Simulator: synthetischer Motor"))
    ports.append((SIM_PORT + ":mega", "Simulator: Arduino-Mega-Sketch 3.0.0"))
    if list_ports:
        for p in list_ports.comports():
            ports.append((p.device, p.description or ""))
    return ports


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


class DynoLink:
    """Serieller Port oder Simulator; ein Lesethread zerlegt die Zeilen."""

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.replay = None
        if port == SIM_PORT:
            self.ser = SimSerial()
        elif port == SIM_PORT + ":mega":
            self.ser = SimSerial(engine=SimEngine(rate=61.0), mega=True)
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
        self.lost = 0
        self.received = 0
        self.last_frame_t = 0.0
        self.is_stm = True      # nach info(): False = Arduino-Mega-Sketch ('m' schaltet um, kein 's')
        self._last_cycle: Optional[int] = None
        self._stop = False
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def close(self):
        try:
            self.ensure_stopped()
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
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("latin-1").rstrip("\r")
                fr = parse_frame(line, time.time())
                # Telegramm = genau 4..7 Zahlenfelder und Zyklus als Ganzzahl
                if fr is not None and line.count(";") >= 3 and "." not in line.split(";")[0]:
                    self._count(fr.cycle)
                    self.last_frame_t = fr.t
                    self.frames.put(fr)
                else:
                    self.text_lines.put(line)

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

    def streaming(self) -> bool:
        return time.time() - self.last_frame_t < 0.5

    def info(self) -> str:
        self._flush()
        self.send("v")
        text = "\n".join(self._collect_text(0.6))
        if not text and not self.port.startswith(SIM_PORT):
            # Arduino Mega startet beim Oeffnen des Ports per DTR neu (Bootloader ~2 s)
            time.sleep(1.8)
            self._flush()
            self.send("v")
            text = "\n".join(self._collect_text(0.6))
        # Mega 1.x antwortet nicht auf 'v', Mega 3.x mit "Version 3...", STM mit "PST-STM32"
        self.is_stm = "PST-STM32" in text or (self.port.startswith(SIM_PORT) and not self.port.endswith(":mega"))
        return text

    def ensure_stopped(self):
        """Ausgabe anhalten. Mega: 'm' schaltet um, also nur senden, wenn er gerade sendet."""
        if self.is_stm:
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

    def start(self):
        """Messung starten (Zyklus- und Verlustzaehler zuruecksetzen)."""
        running = self.streaming()
        self._flush()
        self._last_cycle = None
        self.lost = self.received = 0
        if self.is_stm or not running:
            self.send("m")

    def stop(self):
        self.ensure_stopped()
