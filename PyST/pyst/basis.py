"""
Erweiterungen PyST-Basis (PST-Diag 1 + Zange/Funk, Netzwerk, Steckdosen) – Parser und Befehle, ohne Qt.

Oeffentliche Beschreibung: docs/04_PST_Diag_1.md §10 „Erweiterungen PyST-Basis“.

- '#CAP' meldet zusaetzlich 'ign=zange|none' und 'hw=…'. Die Zange direkt per USB meldet 'ch=I', 'tick_ns=1000'.
- D-Telegramm: zwei angehaengte Felder 'ign_peak_mv' (Mittel der Spitzenwerte im Fenster) und 'ign_src'
  (0 keine, 1 Zange-Ferrit, 2 Zange-Probe) – siehe link.parse_diag.
- Ereigniszeilen 'E;<master_ms>;<art>;…' (MARK, ZANGE, DOSE …), Logzeilen der USB-Zange 'L;idx;art;lokal_us;…'.
- Antworten '#ZANGE;…', '#NET;…', '#DOSE;…', '#PAIR;…', '#LOG;…' als Schluessel=Wert-Felder.

Wo die Spezifikation nur den Befehl, nicht aber das genaue Antwortformat festlegt, ist PyST tolerant:
Schluessel in beliebiger Reihenfolge, deutsche und englische Namen, fehlende Felder = unbekannt, eine Antwort
endet mit '#OK…'/'#ERR…' oder (bei Abfragen) mit der eigenen Statuszeile.
"""
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .diag import parse_kv

# USB-Kennung der Zange (ESP32-C3, USB-Serial-JTAG). Dieselbe Kennung haben viele ESP32-Boards –
# ob es wirklich eine Zange ist, entscheidet erst '#CAP' (ch=I, tick_ns=1000, fw=…Zange…).
ZANGE_USB_ID = (0x303A, 0x1001)
BASIS_TCP_PORT = 2323
BASIS_AP_IP = "192.168.4.1"                 # Standard-Adresse eines ESP32-SoftAP
BASIS_MDNS_NAME = "pyst-basis.local"        # Annahme PyST (nicht spezifiziert), siehe docs §10
ZANGE_USB_WARNUNG = ("Zange per USB = galvanische Verbindung zwischen Zange und PC!\n"
                     "Nur zum Auslesen und Testen – nicht am laufenden Motor messen.")

# Schluessel der Zange (#ZANGE SET/GET), Reihenfolge wie pf_key_t -> (Erklaerung, Standard)
ZANGE_KEYS: Dict[str, Tuple[str, str]] = {
    "imp_per_rev_x10": ("Zündimpulse je Umdrehung × 10 (10 = 1 Funke/U)", "10"),
    "deadtime_us": ("Software-Totzeit nach einem Funken (zusätzlich zur Hardware) [µs]", "300"),
    "plaus_lo_pct": ("Plausibilität: kürzeste erlaubte Periode in % der erwarteten", "60"),
    "plaus_hi_pct": ("Plausibilität: längste erlaubte Periode in % der erwarteten", "180"),
    "agc_mode": ("Verstärkungsregelung: 0 aus, 1 ein, 2 automatisch", "2"),
    "probe_enable": ("Probe-Eingang benutzen (0/1)", "0"),
    "probe_edge": ("Probe: 1 Laden beginnt (steigend), 2 Funke (fallend)", "2"),
    "blitz_mode": ("Blitz: 0 aus, 1 Trigger, 2 Funken durchreichen", "0"),
    "blitz_phi_x10": ("Blitz: Verstellung φ in °KW × 10", "0"),
    "blitz_pulse_us": ("Blitz: Pulsdauer [µs] (≤ 25)", "10"),
    "status_rate_hz": ("Statusmeldungen der Zange je Sekunde", "2"),
    "tx_power": ("Sendeleistung (0…84, 0,25 dBm)", "78"),
}
ZANGE_SUBCMDS = ("MARK", "REC 1", "REC 0", "LOG", "IDENT", "OFF")

# Steckdosen-Typen (wie link_plug_cmd_t / PLUG_*)
PLUG_TYPES = ("SHELLY2", "SHELLY1", "TASMOTA", "ESPHOME")
PLUG_TYPE_TEXT = {"SHELLY2": "Shelly Gen2/3/4 (RPC)", "SHELLY1": "Shelly Gen1", "TASMOTA": "Tasmota",
                  "ESPHOME": "ESPHome (Web-Server)"}
MAX_PLUGS = 8
MAX_LINE = 63                                # PST-Diag 1: Befehlszeile hoechstens 63 Zeichen


def _f(v, default=float("nan")) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _pick(kv: Dict[str, str], *names, default=None):
    for n in names:
        if n in kv and kv[n] != "":
            return kv[n]
    return default


def _bool(v) -> Optional[bool]:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("1", "on", "an", "ein", "true", "ja", "yes"):
        return True
    if s in ("0", "off", "aus", "false", "nein", "no"):
        return False
    return None


# ---------------------------------------------------------------------------------------------- Ereignisse
@dataclass
class BasisEvent:
    """Ereigniszeile 'E;<master_ms>;<art>;<arg…>'."""
    master_ms: float
    kind: str                         # MARK, ZANGE, DOSE, PAIR, … (unbekannte Arten werden durchgereicht)
    args: List[str] = field(default_factory=list)
    t_pc: float = 0.0                 # PC-Zeit (aus master_ms abgebildet, sonst Empfangszeit)

    @property
    def text(self) -> str:
        """Lesbare Beschreibung (fuer Ereignisliste und Marker)."""
        a = self.args
        if self.kind == "MARK":
            quelle = a[0] if a else ""
            txt = ";".join(a[1:]) if len(a) > 1 else ""
            src = {"zange": "Zange", "pyst": "PyST", "taster": "Taster", "basis": "Basis"}.get(quelle.lower(), quelle)
            return f"Marker {src}" + (f": {txt}" if txt else "")
        if self.kind == "ZANGE":
            return "Zange " + (a[0] if a else "?")
        if self.kind == "DOSE":
            nr = a[0] if a else "?"
            st = a[1] if len(a) > 1 else "?"
            st = {"1": "an", "0": "aus"}.get(st, st)
            return f"Steckdose {nr} {st}"
        return f"{self.kind} " + " ".join(a)

    @property
    def is_marker(self) -> bool:
        return self.kind == "MARK"


def parse_event(line: str, t_pc: float = 0.0) -> Optional[BasisEvent]:
    f = line.strip().split(";")
    if len(f) < 3 or f[0] != "E":
        return None
    ms = _f(f[1])
    if ms != ms:
        return None
    return BasisEvent(ms, f[2].strip().upper(), [x.strip() for x in f[3:]], t_pc)


# ---------------------------------------------------------------------------------------------- Zange
@dataclass
class ZangeStatus:
    """Antwort auf '#ZANGE' (§4): '#ZANGE;id=1;mac=..;akku_mv=..;akku_pct=..;vbus=..;rssi=..;funken=..;log=..;
    blitz=..;phi=..;fw=..;sync_us=..'. Fehlende Felder = unbekannt."""
    id: int = 0
    mac: str = ""
    akku_mv: float = float("nan")
    akku_pct: float = float("nan")
    vbus: Optional[bool] = None
    rssi: float = float("nan")
    funken: int = 0
    log: str = ""
    blitz: int = 0
    phi: float = float("nan")
    fw: str = ""
    sync_us: float = float("nan")
    connected: bool = True
    extra: Dict[str, str] = field(default_factory=dict)
    t_pc: float = 0.0

    @property
    def sync_ok(self) -> bool:
        """Zeitabgleich in Ordnung: sync_us bekannt und < 1 ms (Ziel: Funke <-> Rolle < 1 ms)."""
        return self.sync_us == self.sync_us and 0 <= self.sync_us < 1000

    def summary(self) -> str:
        if not self.connected:
            return "Zange: nicht verbunden"
        parts = []
        if self.akku_pct == self.akku_pct:
            parts.append(f"Akku {self.akku_pct:.0f} %" + (" (lädt)" if self.vbus else ""))
        if self.rssi == self.rssi:
            parts.append(f"{self.rssi:.0f} dBm")
        parts.append("Sync ok" if self.sync_ok else "kein Sync")
        parts.append(f"{self.funken} Funken")
        if self.blitz:
            parts.append(f"Blitz φ {self.phi:.1f}°" if self.phi == self.phi else "Blitz an")
        return "Zange: " + " · ".join(parts)


def parse_zange(line: str) -> Optional[ZangeStatus]:
    if not line.startswith("#ZANGE;"):
        return None
    kv = parse_kv(line)
    if not kv:
        return None
    st = ZangeStatus(extra=kv, t_pc=time.time())
    st.id = _i(_pick(kv, "id"), 0)
    st.mac = _pick(kv, "mac", default="")
    st.akku_mv = _f(_pick(kv, "akku_mv", "vbat_mv"))
    st.akku_pct = _f(_pick(kv, "akku_pct", "bat_pct"))
    st.vbus = _bool(_pick(kv, "vbus", "laden"))
    st.rssi = _f(_pick(kv, "rssi"))
    st.funken = _i(_pick(kv, "funken", "sparks"), 0)
    st.log = _pick(kv, "log", default="")
    st.blitz = _i(_pick(kv, "blitz", "blitz_mode"), 0)
    phi = _pick(kv, "phi")
    st.phi = _f(phi) if phi is not None else _f(_pick(kv, "blitz_phi_x10")) / 10.0
    st.fw = _pick(kv, "fw", default="")
    st.sync_us = _f(_pick(kv, "sync_us"))
    con = _bool(_pick(kv, "verbunden", "connected"))
    st.connected = (st.id > 0) if con is None else con
    return st


# ---------------------------------------------------------------------------------------------- Netzwerk
@dataclass
class NetState:
    mode: str = "?"                   # AP, STA, OFF
    channel: int = 0
    ssid: str = ""
    ip: str = ""
    clients: int = 0
    rssi: float = float("nan")
    tcp: Optional[bool] = None
    error: str = ""
    extra: Dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        if self.mode == "OFF":
            return "WLAN aus"
        s = f"{self.mode} „{self.ssid}“" if self.ssid else self.mode
        if self.channel:
            s += f" Kanal {self.channel}"
        if self.ip:
            s += f" · IP {self.ip}"
        if self.mode == "AP":
            s += f" · {self.clients} Teilnehmer"
        elif self.rssi == self.rssi:
            s += f" · {self.rssi:.0f} dBm"
        if self.tcp:
            s += " · PyST per WLAN verbunden"
        if self.error and self.error != "0":
            s += f" · Fehler {self.error}"
        return s


def parse_net(line: str) -> Optional[NetState]:
    if not line.startswith("#NET;"):
        return None
    kv = parse_kv(line)
    st = NetState(extra=kv)
    st.mode = (_pick(kv, "mode", "modus", default="?") or "?").upper()
    st.channel = _i(_pick(kv, "kanal", "channel", "ch"), 0)
    st.ssid = _pick(kv, "ssid", default="")
    st.ip = _pick(kv, "ip", default="")
    st.clients = _i(_pick(kv, "clients", "teilnehmer"), 0)
    st.rssi = _f(_pick(kv, "rssi"))
    st.tcp = _bool(_pick(kv, "tcp", "tcp_connected"))
    st.error = _pick(kv, "err", "fehler", "error", default="")
    return st


def net_commands(mode: str, ssid: str = "", password: str = "", channel: int = 6) -> str:
    """Befehlszeile fuer die Netzwerk-Einstellung. Leerzeichen in SSID/Passwort sind im Zeilenformat nicht
    moeglich (ValueError), ebenso Zeilen ueber 63 Zeichen."""
    mode = mode.upper()
    for x in (ssid, password):
        if any(c in x for c in " ;\r\n"):
            raise ValueError("SSID und Passwort dürfen keine Leerzeichen oder ';' enthalten")
    if mode == "AP":
        if not ssid:
            raise ValueError("SSID fehlt")
        if password and len(password) < 8:
            raise ValueError("WLAN-Passwort: mindestens 8 Zeichen (WPA2)")
        cmd = f"#NET AP {int(channel)} {ssid} {password}".rstrip()
    elif mode == "STA":
        if not ssid:
            raise ValueError("SSID fehlt")
        cmd = f"#NET STA {ssid} {password}".rstrip()
    elif mode == "OFF":
        cmd = "#NET OFF"
    else:
        raise ValueError(f"unbekannter Modus {mode}")
    if len(cmd) > MAX_LINE:
        raise ValueError(f"Befehl zu lang ({len(cmd)} > {MAX_LINE} Zeichen) – SSID/Passwort kürzen")
    return cmd


# ---------------------------------------------------------------------------------------------- Steckdosen
@dataclass
class PlugState:
    nr: int = 0
    typ: str = ""
    host: str = ""
    on: Optional[bool] = None
    power_w: float = float("nan")
    reachable: Optional[bool] = None
    rest_s: float = float("nan")      # verbleibende Totmann-Zeit
    fail_on: Optional[bool] = None    # True = „bleibt an“, False = „fällt aus“
    error: str = ""
    name: str = ""
    extra: Dict[str, str] = field(default_factory=dict)

    def state_text(self) -> str:
        if self.reachable is False:
            return "nicht erreichbar"
        return {True: "an", False: "aus", None: "?"}[self.on]


def parse_dose(line: str) -> Optional[PlugState]:
    """'#DOSE;nr=1;typ=SHELLY2;host=…;an=1;p_w=12.5;erreichbar=1;rest_s=40;ausfall=aus;fehler=0'.
    Auch: on=, power_mw=, leistung=, reachable=, ok=, fallback_s=. Fundstellen von '#DOSE SCAN' haben nr=0
    (bzw. die Zeile '#DOSE;SCAN;…')."""
    if not line.startswith("#DOSE;"):
        return None
    kv = parse_kv(line)
    st = PlugState(extra=kv)
    st.nr = _i(_pick(kv, "nr", "id"), 0)
    st.typ = (_pick(kv, "typ", "type", default="") or "").upper()
    st.host = _pick(kv, "host", "ip", "name", default="")
    st.name = _pick(kv, "bez", "label", default="")
    st.on = _bool(_pick(kv, "an", "on", "zustand", "state"))
    p_w = _pick(kv, "p_w", "leistung", "power_w", "w")
    if p_w is not None:
        st.power_w = _f(p_w)
    else:
        mw = _f(_pick(kv, "power_mw", "p_mw"))
        st.power_w = mw / 1000.0 if mw == mw and mw >= 0 else float("nan")
    st.reachable = _bool(_pick(kv, "erreichbar", "reachable", "ok"))
    st.rest_s = _f(_pick(kv, "rest_s", "fallback_s", "fallback_left_s"))
    ausfall = _pick(kv, "ausfall", "fail", "fail_on")
    if ausfall is not None:
        st.fail_on = ausfall.lower() in ("an", "1", "on", "bleibt")
    st.error = _pick(kv, "fehler", "err", "error", default="")
    return st


# ---------------------------------------------------------------------------------------------- Koppeln
@dataclass
class PairEntry:
    id: int
    mac: str = ""
    typ: str = ""
    sn: str = ""
    fw: str = ""
    connected: Optional[bool] = None


def parse_pair(line: str) -> Optional[PairEntry]:
    if not line.startswith("#PAIR;"):
        return None
    kv = parse_kv(line)
    if "id" not in kv:
        return None
    return PairEntry(_i(kv["id"]), kv.get("mac", ""), kv.get("typ", kv.get("type", "")),
                     kv.get("sn", kv.get("serial", "")), kv.get("fw", ""),
                     _bool(_pick(kv, "verbunden", "connected")))


# ---------------------------------------------------------------------------------------------- Log der Zange
LOG_SPARK, LOG_MARK, LOG_SYNC, LOG_STATUS = "F", "M", "Y", "S"
_LOG_ART = {"F": LOG_SPARK, "FUNKE": LOG_SPARK, "SPARK": LOG_SPARK, "1": LOG_SPARK,
            "M": LOG_MARK, "MARK": LOG_MARK, "MARKER": LOG_MARK, "2": LOG_MARK,
            "Y": LOG_SYNC, "SYNC": LOG_SYNC, "3": LOG_SYNC,
            "S": LOG_STATUS, "STATUS": LOG_STATUS, "4": LOG_STATUS}


@dataclass
class LogEntry:
    """Logzeile der USB-Zange 'L;idx;art;lokal_us;…'. art: F Funke (src;flags;peak_mv), M Marker (taste),
    Y Sync-Paar (master_us), S Status (akku_mv;akku_pct;…). Unbekannte Arten bleiben mit art = Originaltext."""
    idx: int
    art: str
    local_us: int
    rest: List[str] = field(default_factory=list)

    def num(self, k: int, default: float = float("nan")) -> float:
        return _f(self.rest[k]) if k < len(self.rest) else default


def parse_log_line(line: str) -> Optional[LogEntry]:
    f = line.strip().split(";")
    if len(f) < 4 or f[0] != "L":
        return None
    try:
        idx = int(f[1])
        local = int(float(f[3]))
    except ValueError:
        return None
    art = f[2].strip().upper()
    return LogEntry(idx, _LOG_ART.get(art, art), local, [x.strip() for x in f[4:]])


def parse_log_info(lines: List[str]) -> Dict[str, int]:
    """'#LOG;head=..;tail=..' (bzw. '#OK;LOG;INFO;head=..;tail=..') -> {'head', 'tail', 'n'}"""
    for line in lines:
        if line.startswith("#LOG;") or line.startswith("#OK;LOG"):
            kv = parse_kv(line)
            if "head" in kv or "n" in kv:
                head = _i(kv.get("head"), 0)
                tail = _i(kv.get("tail"), 0)
                n = _i(kv.get("n"), head - tail)
                return {"head": head, "tail": tail, "n": n}
    return {}


# ---------------------------------------------------------------------------------------------- Befehle
def _done_prefix(*prefixes: str) -> Callable[[str], bool]:
    return lambda line: line.startswith("#OK") or line.startswith("#ERR") or any(line.startswith(p) for p in prefixes)


class BasisCommands:
    """Befehle der Basis-Erweiterung. Wird von link.DynoLink geerbt (braucht command(), ok(), caps, log_lines)."""

    caps: Dict[str, str]

    # --- Geraeteart
    @property
    def ign_source(self) -> str:
        """'zange' (Funk-Zange gekoppelt), 'none' (Basis ohne Zuendquelle) oder '' (anderes Geraet)."""
        return (self.caps.get("ign", "") or "").lower()

    @property
    def is_basis(self) -> bool:
        return "ign" in self.caps or "basis" in self.caps.get("fw", "").lower()

    @property
    def is_zange_usb(self) -> bool:
        """Zange direkt per USB: '#CAP' mit ch=I (keine Rolle) und tick_ns=1000 bzw. 'Zange' im Namen."""
        c = self.caps
        if not c:
            return False
        if "zange" in c.get("fw", "").lower() or c.get("hw", "").lower().startswith("zange"):
            return True
        return c.get("ch", "") == "I" and abs(_f(c.get("tick_ns"), 0) - 1000.0) < 1e-6

    @property
    def channels(self) -> str:
        """Kanaele laut '#CAP' ('I,R', 'R', 'I')."""
        return self.caps.get("ch", "I,R") if self.caps else "I,R"

    # --- Zange
    def zange_status(self, timeout: float = 1.5) -> Optional[ZangeStatus]:
        lines = self.command("#ZANGE", timeout=timeout, done=_done_prefix("#ZANGE;"))
        for line in lines:
            st = parse_zange(line)
            if st is not None:
                return st
        if lines and lines[-1].startswith("#ERR"):
            return ZangeStatus(connected=False, extra={"fehler": lines[-1]})
        return None

    def zange_set(self, key: str, value) -> List[str]:
        return self.command(f"#ZANGE SET {key} {value}", timeout=2.5)

    def zange_get(self, key: str) -> Optional[str]:
        lines = self.command(f"#ZANGE GET {key}", timeout=2.5, done=_done_prefix("#ZANGE;"))
        for line in lines:
            kv = parse_kv(line)
            if key in kv:
                return kv[key]
        return None

    def zange_get_all(self) -> List[Tuple[str, str]]:
        out = []
        for k in ZANGE_KEYS:
            v = self.zange_get(k)
            if v is not None:
                out.append((k, v))
        return out

    def zange_cmd(self, sub: str) -> List[str]:
        """'MARK', 'REC 1', 'REC 0', 'LOG', 'IDENT', 'OFF'"""
        return self.command(f"#ZANGE {sub}", timeout=2.5)

    # --- Koppeln
    def pair(self, on: bool) -> bool:
        return self.ok(self.command(f"#PAIR {1 if on else 0}"))

    def pair_list(self) -> List[PairEntry]:
        out = []
        for line in self.command("#PAIR LIST", timeout=1.5):
            e = parse_pair(line)
            if e is not None:
                out.append(e)
        return out

    def pair_delete(self, dev_id: int) -> bool:
        return self.ok(self.command(f"#PAIR DEL {int(dev_id)}"))

    # --- Netzwerk
    def net_state(self) -> Optional[NetState]:
        for line in self.command("#NET", timeout=1.5, done=_done_prefix("#NET;")):
            st = parse_net(line)
            if st is not None:
                return st
        return None

    def net_set(self, mode: str, ssid: str = "", password: str = "", channel: int = 6) -> List[str]:
        return self.command(net_commands(mode, ssid, password, channel), timeout=3.0)

    # --- Steckdosen (ueber die Basis)
    def dose_list(self) -> List[PlugState]:
        out = []
        for line in self.command("#DOSE", timeout=2.0):
            st = parse_dose(line)
            if st is not None and st.nr > 0:
                out.append(st)
        return out

    def dose_add(self, nr: int, typ: str, host: str) -> List[str]:
        return self.command(f"#DOSE ADD {int(nr)} {typ.upper()} {host}", timeout=2.0)

    def dose_switch(self, nr: int, on, seconds: Optional[float] = None) -> List[str]:
        """on: True/False oder 'TOGGLE'; seconds = Totmann-/Rueckfallzeit"""
        val = on if isinstance(on, str) else ("1" if on else "0")
        cmd = f"#DOSE {int(nr)} {val}" + (f" {int(seconds)}" if seconds else "")
        return self.command(cmd, timeout=2.5)

    def dose_delete(self, nr: int) -> List[str]:
        return self.command(f"#DOSE DEL {int(nr)}", timeout=2.0)

    def dose_scan(self, timeout: float = 6.0) -> List[PlugState]:
        out = []
        for line in self.command("#DOSE SCAN", timeout=timeout):
            st = parse_dose(line)
            if st is not None and st.host:
                out.append(st)
        return out

    # --- Log der USB-Zange
    def log_info(self) -> Dict[str, int]:
        return parse_log_info(self.command("#LOG INFO", timeout=1.5, done=_done_prefix("#LOG;")))

    def log_get(self, start: int, n: int, timeout: float = 10.0,
                progress: Optional[Callable[[int], None]] = None) -> List[LogEntry]:
        """'#LOG GET von n' -> L-Zeilen bis '#OK…'/'#LOG;END'/'#ERR…' (oder bis n Zeilen da sind)."""
        while not self.log_lines.empty():
            self.log_lines.get_nowait()
        lines = self.command(f"#LOG GET {int(start)} {int(n)}", timeout=timeout,
                             done=_done_prefix("#LOG;END"), until=lambda: self.log_lines.qsize() >= n,
                             progress=lambda: progress(self.log_lines.qsize()) if progress else None)
        out = []
        while not self.log_lines.empty():
            e = self.log_lines.get_nowait()
            if e is not None:
                out.append(e)
        if lines and lines[-1].startswith("#ERR") and not out:
            raise RuntimeError(lines[-1])
        return out

    def log_read_all(self, block: int = 2000, progress: Optional[Callable[[int, int], None]] = None) -> List[LogEntry]:
        """Ganzes Log der Zange in Bloecken lesen."""
        info = self.log_info()
        if not info:
            raise RuntimeError("Keine Antwort auf #LOG INFO")
        tail, head = info["tail"], info["head"]
        out: List[LogEntry] = []
        k = tail
        while k < head:
            n = min(block, head - k)
            got = self.log_get(k, n, timeout=max(5.0, n / 400.0))
            if not got:
                break
            out += got
            k = max(e.idx for e in got) + 1
            if progress:
                progress(k - tail, head - tail)
        return out
