"""
Erweiterungen PyST-Basis (PST-Diag 1 + Zange/Funk, Netzwerk, Steckdosen) – Parser und Befehle, ohne Qt.

Oeffentliche Beschreibung: docs/04_PST_Diag_1.md §10 „Erweiterungen PyST-Basis“.

- '#CAP' meldet zusaetzlich 'ign=zange|none' und 'hw=…'. Die Zange direkt per USB meldet 'ch=I', 'tick_ns=1000'.
- D-Telegramm: zwei angehaengte Felder 'ign_peak_mv' (Mittel der Spitzenwerte im Fenster) und 'ign_src'
  (0 keine, 1 Zange-Ferrit, 2 Zange-Probe) – siehe link.parse_diag.
- Ereigniszeilen 'E;<master_ms>;<art>;…' (MARK, ZANGE, DOSE, LAMBDA, ZTASTE …), Logzeilen der USB-Zange 'L;idx;art;lokal_us;…'.
- Antworten '#ZANGE;…', '#NET;…', '#DOSE;…', '#PAIR;…', '#LOG;…' als Schluessel=Wert-Felder.

Wo die Spezifikation nur den Befehl, nicht aber das genaue Antwortformat festlegt, ist PyST tolerant:
Schluessel in beliebiger Reihenfolge, deutsche und englische Namen, fehlende Felder = unbekannt, eine Antwort
endet mit '#OK…'/'#ERR…' oder (bei Abfragen) mit der eigenen Statuszeile.
"""
import math
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .felder import parse_kv, to_float as _f, to_int as _i

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


# ---------------------------------------------------------------------------------------------- Lambda-Heizung
# Basis-Firmware wb_bedien.h: aus (F042 im Reset), scharf (nur Vorheizen), heizt (Motor erkannt). Die Abschaltzeit
# lambda_off_min laeuft in der Basis, PyST zeigt sie nur an.
LAMBDA_GRUND = {
    "pc": "von PyST ausgeschaltet", "t4": "an der Zange ausgeschaltet", "zeit": "kein Motor, abgeschaltet",
    "kein_can": "F042 meldet sich nicht", "swd": "SWD-Freigabe läuft", "update": "F042-Update läuft",
    "gesperrt_watchdog": "gesperrt nach Watchdog-Reset, nur der Knopf hier löst das",
    "gesperrt_kein_can": "F042 meldet sich nicht", "unterspannung": "12 V zu niedrig", "start": "F042 startet",
}


def _mmss(s: int) -> str:
    return f"{s // 60}:{s % 60:02d}"


@dataclass
class LambdaStatus:
    """'#LAMBDA' bzw. '#STAT': lambda_st, lambda_grund, lambda_rest, lambda_motor, lambda_uv. st leer = Firmware zu alt."""
    st: str = ""
    grund: str = ""
    rest_s: int = 0
    motor: bool = False
    uv: bool = False

    @property
    def known(self) -> bool:
        return bool(self.st)

    @property
    def on(self) -> bool:
        return self.st in ("scharf", "heizt")

    @property
    def low_voltage(self) -> bool:
        return self.on and self.uv

    @property
    def state_text(self) -> str:
        return {"aus": "aus", "scharf": "bereit", "heizt": "heizt"}.get(self.st, self.st or "–")

    @property
    def color(self) -> str:
        return {"scharf": "#ef6c00", "heizt": "#2e7d32"}.get(self.st, "#757575")

    @property
    def detail(self) -> str:
        """Restzeit und Grund in Worten."""
        grund = LAMBDA_GRUND.get(self.grund, self.grund) if self.grund not in ("", "-") else ""
        if self.st == "heizt":
            return grund or "Motor läuft"
        if self.st == "scharf":
            warte = "Motor läuft" if self.motor else "wartet auf Motor"
            return f"{grund or warte}, aus in {_mmss(self.rest_s)}"
        return grund

    @property
    def summary(self) -> str:
        return f"Lambda {self.state_text}" + (f" · {self.detail}" if self.detail else "")


def parse_lambda(kv: Dict[str, str]) -> LambdaStatus:
    return LambdaStatus(kv.get("lambda_st", ""), kv.get("lambda_grund", ""), _i(kv.get("lambda_rest")),
                        kv.get("lambda_motor") == "1", kv.get("lambda_uv") == "1")


def lambda_event_text(a: List[str]) -> str:
    """'E;…;LAMBDA;scharf;t4;180', 'aus;zeit', 'heizt;motor', 'abgelehnt;gesperrt_watchdog'."""
    st = a[0] if a else "?"
    why = a[1] if len(a) > 1 else ""
    if st == "aus":
        return "Lambda aus" + (f": {LAMBDA_GRUND.get(why, why)}" if why not in ("", "-") else "")
    if st == "abgelehnt":
        return f"Lambda abgelehnt: {LAMBDA_GRUND.get(why, why)}"
    if st == "heizt":
        return "Lambda heizt"
    quelle = {"pc": "von PyST", "t4": "an der Zange", "auto": "Automatik", "motor_weg": "Motor weg",
              "unterspannung": "12 V zu niedrig", "kein_can": "F042 meldet sich nicht"}.get(why, why)
    return f"Lambda bereit ({quelle})"


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
            return "Zange " + {"neustart": "neu gestartet"}.get(a[0] if a else "?", a[0] if a else "?")
        if self.kind == "DOSE":
            nr = a[0] if a else "?"
            st = a[1] if len(a) > 1 else "?"
            st = {"1": "an", "0": "aus"}.get(st, st)
            return f"Steckdose {nr} {st}"
        if self.kind == "LAMBDA":
            return lambda_event_text(a)
        if self.kind == "ZTASTE":
            return "Zange: Taste " + {"lauf": "Neuer Lauf", "uebersetzung": "Übersetzung"}.get(a[0] if a else "?", a[0] if a else "?")
        return f"{self.kind} " + " ".join(a)

    @property
    def is_marker(self) -> bool:
        return self.kind == "MARK"

    def marker_text(self, nr: int) -> str:
        """Marker von Taste oder F3 als „Marker <nr>“. Die Nummer zählt der Aufrufer je Lauf bzw. Aufzeichnung."""
        a = self.args
        if not self.is_marker or not a or a[0].lower() not in ("zange", "pyst"):
            return self.text
        zange = a[1].split("/Z")[1] if len(a) > 1 and "/Z" in a[1] else ""
        return f"Marker {nr}" + (f" (Zange {zange})" if zange else "")


def parse_event(line: str, t_pc: float = 0.0) -> Optional[BasisEvent]:
    f = line.strip().split(";")
    if len(f) < 3 or f[0] != "E":
        return None
    ms = _f(f[1])
    if not math.isfinite(ms):
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


def main_zange(zangen: List[ZangeStatus]) -> Optional[ZangeStatus]:
    """Hauptzange aus der Liste: erste verbundene, sonst die erste."""
    for st in zangen:
        if st.connected:
            return st
    return zangen[0] if zangen else None


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
    moeglich (ValueError), ebenso Zeilen ueber 63 Zeichen. Ein offenes WLAN wird abgelehnt: auf einem Port ohne
    Verschluesselung saehe jeder in Funkreichweite den Messbetrieb."""
    mode = mode.upper()
    for x in (ssid, password):
        if any(c in x for c in " ;\r\n"):
            raise ValueError("SSID und Passwort dürfen keine Leerzeichen oder ';' enthalten")
    if mode in ("AP", "STA"):
        if not ssid:
            raise ValueError("SSID fehlt")
        if not password:
            raise ValueError("Offenes WLAN wird abgelehnt: Passwort eintragen")
        if len(password) < 8:
            raise ValueError("WLAN-Passwort: mindestens 8 Zeichen (WPA2)")
    if mode == "AP":
        cmd = f"#NET AP {int(channel)} {ssid} {password}"
    elif mode == "STA":
        cmd = f"#NET STA {ssid} {password}"
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
LOG_SPARK, LOG_MARK, LOG_SYNC, LOG_STATUS, LOG_BOOT, LOG_OFF = "F", "M", "Y", "S", "B", "A"
_LOG_ART = {"F": LOG_SPARK, "FUNKE": LOG_SPARK, "SPARK": LOG_SPARK, "1": LOG_SPARK,
            "M": LOG_MARK, "MARK": LOG_MARK, "MARKER": LOG_MARK, "2": LOG_MARK,
            "Y": LOG_SYNC, "SYNC": LOG_SYNC, "3": LOG_SYNC,
            "S": LOG_STATUS, "STATUS": LOG_STATUS, "4": LOG_STATUS,
            "B": LOG_BOOT, "BOOT": LOG_BOOT, "5": LOG_BOOT,
            "A": LOG_OFF, "AUS": LOG_OFF, "6": LOG_OFF}


@dataclass
class LogEntry:
    """Logzeile der USB-Zange 'L;idx;art;lokal_us;…'. art: F Funke (src;flags;peak_mv), M Marker (taste),
    Y Sync-Paar (master_us), S Status (akku_mv;akku_pct;…), B Start der Zange (lokal_us beginnt neu), A geordnetes Aus (grund;akku_mv). Unbekannte Arten bleiben mit art = Originaltext."""
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
    except (ValueError, OverflowError):
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
LOG_TRIES = 4


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

    # --- Lambda-Heizung
    def lambda_status(self, timeout: float = 1.0) -> Optional[LambdaStatus]:
        """Zustand der Basis; None = keine Antwort. Aeltere Firmware antwortet ohne lambda_st (known = False)."""
        for line in self.command("#LAMBDA", timeout=timeout):
            if line.startswith("#OK;LAMBDA"):
                return parse_lambda(parse_kv(line))
        return None

    def lambda_set(self, on: bool) -> List[str]:
        return self.command(f"#LAMBDA {1 if on else 0}")

    # --- Zange
    def zangen_status(self, timeout: float = 1.5) -> List[ZangeStatus]:
        """Alle gekoppelten Zangen: je Zange eine '#ZANGE;id=…'-Zeile, Ende '#OK;ZANGE;n=…' bzw. '#ERR;ZANGE;…'.
        Ohne Abschlusszeile (aeltere Basis-Firmware) endet die Antwort erst nach timeout."""
        lines = self.command("#ZANGE", timeout=timeout)
        out = [st for st in (parse_zange(line) for line in lines) if st is not None]
        if not out and lines and lines[-1].startswith("#ERR"):
            return [ZangeStatus(connected=False, extra={"fehler": lines[-1]})]
        return out

    def zange_status(self, timeout: float = 1.5) -> Optional[ZangeStatus]:
        """Hauptzange (erste verbundene, sonst erste gekoppelte)."""
        return main_zange(self.zangen_status(timeout))

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
        """Alle Schluessel. Antwortet die Zange zweimal nacheinander nicht, ist sie weg: sonst 12 x 2,5 s Wartezeit."""
        out = []
        schweigt = 0
        for k in ZANGE_KEYS:
            v = self.zange_get(k)
            if v is not None:
                out.append((k, v))
                schweigt = 0
            else:
                schweigt += 1
                if schweigt == 2:
                    break
        return out

    def zange_cmd(self, sub: str) -> List[str]:
        """'MARK', 'REC 1', 'REC 0', 'LOG', 'IDENT', 'OFF', 'LED <muster>'"""
        return self.command(f"#ZANGE {sub}", timeout=2.5)

    def zange_tasten(self, wert: int) -> List[str]:
        """Tasten-Setup (tasten.py) über die Basis oder direkt per USB; '#OK…' = die Zange hat es."""
        return self.set_setting("tasten", wert) if self.is_zange_usb else self.zange_set("tasten", wert)

    def zange_led(self, muster: int) -> List[str]:
        """Rückmeldung auf eine Tasten-Aktion. Die Basis hat einen Befehlsplatz: belegt heißt hier gleich noch einmal."""
        lines: List[str] = []
        for _ in range(3):
            lines = self.zange_cmd(f"LED {muster}")
            if not (lines and "belegt" in lines[-1]):
                break
            time.sleep(0.1)
        return lines

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

    def dose_scan(self, timeout: float = 8.0) -> List[PlugState]:
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
        """'#LOG GET von n' -> L-Zeilen bis '#OK…'/'#LOG;END'/'#ERR…'. self.log_complete sagt danach, ob die Abschlusszeile
        kam und ihre Zeilenzahl stimmt. n zaehlt Nummern, wegen der Startluecken kommen meist weniger Zeilen."""
        while not self.log_lines.empty():
            self.log_lines.get_nowait()
        lines = self.command(f"#LOG GET {int(start)} {int(n)}", timeout=timeout,
                             done=_done_prefix("#LOG;END"),
                             progress=lambda: progress(self.log_lines.qsize()) if progress else None)
        out = []
        while not self.log_lines.empty():
            e = self.log_lines.get_nowait()
            if e is not None:
                out.append(e)
        if lines and lines[-1].startswith("#ERR") and not out:
            raise RuntimeError(lines[-1])
        m = re.search(r"\bn=(\d+)", lines[-1]) if lines else None
        self.log_complete = bool(m) and int(m.group(1)) == len(out)
        return out

    def log_block_size(self) -> int:
        """Bis Firmware 0.3.0 haengt die USB-Ausgabe, sobald mehr als 2 KB anstehen: dort nur kleine Bloecke."""
        v = tuple(int(x) for x in re.findall(r"\d+", self.caps.get("fw", ""))[:3])
        return 4000 if not v or v >= (0, 3, 1) else 40

    def log_read_all(self, block: Optional[int] = None,
                     progress: Optional[Callable[[int, int], None]] = None) -> List[LogEntry]:
        """Ganzes Log der Zange in Bloecken lesen. Jeder Block wird geprueft und bei Bedarf neu geholt, leere Bloecke sind
        normal (Startluecken). Was auch nach LOG_TRIES Versuchen fehlt, steht in self.log_gaps als (von, n)."""
        block = block or self.log_block_size()
        info = self.log_info()
        if not info:
            raise RuntimeError("Keine Antwort auf #LOG INFO")
        tail, head = info["tail"], info["head"]
        found: Dict[int, LogEntry] = {}
        self.log_gaps: List[Tuple[int, int]] = []
        k = tail
        while k < head:
            n = min(block, head - k)
            for _ in range(LOG_TRIES):
                got = self.log_get(k, n, timeout=max(5.0, n / 400.0))
                if self.log_complete:
                    break
            found.update((e.idx, e) for e in got)
            if not self.log_complete:
                self.log_gaps.append((k, n))
            k += n
            if progress:
                progress(k - tail, head - tail)
        return [found[i] for i in sorted(found)]
