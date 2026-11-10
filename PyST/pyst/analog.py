"""
Analogeingaenge der Messboards (Arduino Mega, STM32 F446, PyST-Basis): Kanaele benennen und umrechnen.

Eingaenge je Geraet (Stand Firmware Mega 3.1.0, F446 1.1.0, Basis 0.4):
  Arduino Mega   A0 … A15, 10 bit, 0–5 V (AREF = 5 V). Der Sketch liest heute keinen davon.
  STM32 F446     ADC1 = PB0 (ADC1_IN8), ADC2 = PA1 (ADC1_IN1), 12 bit, 0–3,3 V ohne Teiler. Einer davon kann der
                 AFR-Eingang sein (#SET afr_src 1|2, Kennlinie afr_0v/afr_fs); im ASCII-Telegramm steht nur das AFR.
  PyST-Basis     AI1 = X6.1, AI2 = X6.2 (X6.3 = 5 V Sensor, X6.4 = GND), 12 bit x 16, Teiler auf der Platine:
                 Werkszustand 0–10 V (JP60–JP63 offen, ai*_mode 1), JP60/JP62 zu 0–5 V (mode 0), dazu JP61/JP63
                 4–20 mA (mode 2, Wert in mA). Die Basis meldet die Eingangsspannung in '#STAT' (ai1=…V;ai2=…V).

Wie die Werte zu PyST kommen:
  - Vorschlag PST-Diag 1 (alle Geraete): '#CAP;…;ai=AI1,AI2' nennt die Eingaenge, '#AI <hz>' schaltet Zeilen
    'A;<t_ms>;AI1=1.234;AI2=0.501' (Volt an der Klemme bzw. mA, nan = kein Wert) ein, '#AI 0' aus. Nach dem Start aus
    (LabVIEW merkt nichts). Die Firmwares koennen das noch nicht, der Simulator schon (Mega, F446).
  - PyST-Basis heute: '#STAT' etwa jede Sekunde (analog_page.py), nur wenn Kanaele aktiv sind oder die Seite offen ist.
  Der zuletzt empfangene Wert gilt fuer alle folgenden Telegramme (link.Frame.ain), hoechstens HALTEN_S lang.

Ein Kanal = ein Eingang mit Name, Einheit und Umrechnung: keine (Wert = Spannung), 2-Punkt (U1 -> W1, U2 -> W2,
linear) oder Faktor/Offset (W = a * U + b). Gespeichert in einstellungen.json unter "analog" je Geraet.
In roh.csv stehen die Rohwerte (Spalten analog_<Eingang>), lauf.json haelt unter "analog" die zum Lauf gueltige
Konfiguration; alte Laeufe zeigen ihre damalige Umrechnung (oder die aktuelle mit "analog_neu_rechnen").
"""
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

PREFIX = "ain:"                 # Live-Kanal / Zusatzkanal: "ain:" + Eingang, z.B. "ain:AI1"
SPALTE = "analog_"              # Spalte in roh.csv: "analog_" + Eingang (Rohwert, V bzw. mA)
GRUPPE = "Analog"               # Gruppe im Kanal-Menue und in der Auswertung
SUFFIX = " (Analog)"            # Kanalname in der Auswertung: "Öldruck (Analog)"
HALTEN_S = 3.0                  # so lange gilt ein Analogwert fuer die folgenden Telegramme
AI_RATE_HZ = 10                 # Rate der A-Zeilen, die PyST anfordert

MEGA, F446, BASIS = "mega", "f446", "basis"
KEINE, ZWEIPUNKT, FAKTOR = "keine", "2punkt", "faktor"
ARTEN = ((KEINE, "keine (V)"), (ZWEIPUNKT, "2-Punkt"), (FAKTOR, "Faktor/Offset"))
ART_TEXT = dict(ARTEN)


@dataclass(frozen=True)
class Eingang:
    id: str                     # Name in A-Zeile und Spalte, z.B. "AI1"
    pin: str                    # Beschriftung, z.B. "AI1 (X6.1)"
    u_max: float                # Messbereich bis … (V), fuer Balken ohne eigene Grenzen
    fest: str = ""              # fest/teilweise belegt: Hinweis in der Tabelle, Name bleibt frei


@dataclass(frozen=True)
class Geraet:
    key: str
    name: str
    bereich: str                # Messbereich und Aufloesung als Text
    eingaenge: Tuple[Eingang, ...]
    hinweis: str                # Stand der Firmware


GERAETE: Dict[str, Geraet] = {
    MEGA: Geraet(MEGA, "Arduino Mega", "0–5 V, 10 bit",
                 tuple(Eingang(f"A{i}", f"A{i}", 5.0) for i in range(16)),
                 "PST-Mega 3.1.0 sendet noch keine Analogwerte (Firmware fehlt). PyST holt sie, sobald die Firmware "
                 "„#AI“ kann (zeigt schon der Simulator SIM:mega)."),
    F446: Geraet(F446, "STM32 F446", "0–3,3 V, 12 bit (ohne Teiler, nicht über 3,3 V)",
                 (Eingang("ADC1", "ADC1 (PB0)", 3.3, "AFR-Eingang, wenn afr_src = 1"),
                  Eingang("ADC2", "ADC2 (PA1)", 3.3, "AFR-Eingang, wenn afr_src = 2")),
                 "PST-STM32 1.1.0 sendet die Spannungen noch nicht, nur das AFR des gewählten Eingangs "
                 "(Lambda › AFR-Eingang). PyST holt sie, sobald die Firmware „#AI“ kann (zeigt schon der Simulator)."),
    BASIS: Geraet(BASIS, "PyST-Basis", "0–10 V (Werkszustand), 0–5 V oder 4–20 mA per Lötbrücke, 12 bit",
                  (Eingang("AI1", "AI1 (X6.1)", 10.0), Eingang("AI2", "AI2 (X6.2)", 10.0)),
                  "Klemme X6: AI1, AI2, 5 V Sensor, GND. Bereich per Lötbrücke JP60–JP63 und ai1_mode/ai2_mode "
                  "(0 = 0–5 V, 1 = 0–10 V, 2 = 4–20 mA, Wert dann in mA). PyST liest die Werte etwa jede Sekunde "
                  "(#STAT)."),
}
REIHENFOLGE = (BASIS, F446, MEGA)

# Vorlagen fuer das Menue der Seite (nur Ausfuellhilfe): (Text, Werte)
VORLAGEN: List[Tuple[str, Dict[str, Any]]] = [
    ("Drucksensor 0,5–4,5 V = 0–10 bar", dict(art=ZWEIPUNKT, u1=0.5, w1=0.0, u2=4.5, w2=10.0, einheit="bar",
                                              lo=0.0, hi=10.0, dez=2)),
    ("Drucksensor 0,5–4,5 V = 0–5 bar", dict(art=ZWEIPUNKT, u1=0.5, w1=0.0, u2=4.5, w2=5.0, einheit="bar",
                                             lo=0.0, hi=5.0, dez=2)),
    ("0–5 V linear = 0–100 %", dict(art=ZWEIPUNKT, u1=0.0, w1=0.0, u2=5.0, w2=100.0, einheit="%",
                                    lo=0.0, hi=100.0, dez=0)),
    ("0–10 V linear = 0–100 %", dict(art=ZWEIPUNKT, u1=0.0, w1=0.0, u2=10.0, w2=100.0, einheit="%",
                                     lo=0.0, hi=100.0, dez=0)),
    ("4–20 mA = 0–10 bar (Basis, 4–20 mA)", dict(art=ZWEIPUNKT, u1=4.0, w1=0.0, u2=20.0, w2=10.0, einheit="bar",
                                                 lo=0.0, hi=10.0, dez=2)),
    ("Spannungsteiler 1:3 (Faktor 3)", dict(art=FAKTOR, a=3.0, b=0.0, einheit="V", lo=0.0, hi=15.0, dez=2)),
    ("Spannung direkt (V)", dict(art=KEINE, einheit="V", lo=None, hi=None, dez=2)),
]


def _num(x, std: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return std
    return v if math.isfinite(v) else std


def _opt(x) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def zahl(x: Optional[float], dez: int = 2) -> str:
    """1.5 -> "1,5" (ohne Tausenderpunkt, fuer Eingabefelder und Texte); None/nan -> "–"."""
    if x is None or not math.isfinite(x):
        return "–"
    s = f"{x:.{max(0, dez)}f}".replace(".", ",")
    return s


def _kurz(x: float) -> str:
    """Zahl ohne unnoetige Stellen: 0.5 -> "0,5", 10.0 -> "10"."""
    return f"{x:.6g}".replace(".", ",")


@dataclass
class Kanal:
    """Einstellung eines Eingangs."""
    eingang: str
    aktiv: bool = False
    name: str = ""
    einheit: str = "V"
    art: str = KEINE
    u1: float = 0.5
    w1: float = 0.0
    u2: float = 4.5
    w2: float = 10.0
    a: float = 1.0
    b: float = 0.0
    lo: Optional[float] = None        # Balken/Achse, None = aus der Umrechnung
    hi: Optional[float] = None
    dez: int = 2

    # ---- Umrechnung
    def koeffizienten(self) -> Optional[Tuple[float, float]]:
        """(a, b) mit W = a * U + b; None, wenn die Umrechnung nicht geht (2-Punkt mit U1 = U2)."""
        if self.art == FAKTOR:
            return (self.a, self.b) if math.isfinite(self.a) and math.isfinite(self.b) else None
        if self.art == ZWEIPUNKT:
            du = self.u2 - self.u1
            if not math.isfinite(du) or abs(du) < 1e-9 or not (math.isfinite(self.w1) and math.isfinite(self.w2)):
                return None
            a = (self.w2 - self.w1) / du
            return a, self.w1 - a * self.u1
        return 1.0, 0.0

    def fehler(self) -> str:
        """Grund, warum die Umrechnung nicht geht ("" = in Ordnung)."""
        if self.koeffizienten() is not None:
            return ""
        if self.art == ZWEIPUNKT:
            return "U1 und U2 sind gleich – keine Umrechnung möglich"
        return "Faktor oder Offset ungültig"

    def wert(self, u: Optional[float]) -> Optional[float]:
        """Rohwert (V bzw. mA) -> Wert in der Einheit des Kanals; None, wenn kein Wert/keine Umrechnung."""
        k = self.koeffizienten()
        if k is None or u is None or not isinstance(u, (int, float)) or not math.isfinite(u):
            return None
        return k[0] * float(u) + k[1]

    def werte(self, u) -> np.ndarray:
        u = np.asarray(u, float)
        k = self.koeffizienten()
        if k is None:
            return np.full(u.shape, np.nan)
        return k[0] * u + k[1]

    # ---- Anzeige
    def titel(self) -> str:
        return self.name.strip() or self.eingang

    def einheit_text(self) -> str:
        return self.einheit.strip() if self.art != KEINE else (self.einheit.strip() or "V")

    def umrechnung_text(self) -> str:
        """Kurz fuer die Tabelle: „keine (V)“, „0,5 V = 0 · 4,5 V = 10 bar“, „W = 3 · U + 0“."""
        if self.art == ZWEIPUNKT:
            e = self.einheit.strip()
            return f"{_kurz(self.u1)} V = {_kurz(self.w1)} · {_kurz(self.u2)} V = {_kurz(self.w2)}" + (f" {e}" if e
                                                                                                         else "")
        if self.art == FAKTOR:
            return f"W = {_kurz(self.a)} · U {'+' if self.b >= 0 else '−'} {_kurz(abs(self.b))}"
        return ART_TEXT[KEINE]

    def bereich(self, u_max: float = 5.0) -> Tuple[float, float]:
        """Balken-/Achsbereich: eigene Grenzen, sonst die Umrechnung des Messbereichs 0 … u_max."""
        if self.lo is not None and self.hi is not None and self.hi > self.lo:
            return self.lo, self.hi
        if self.art == ZWEIPUNKT and self.w1 != self.w2:
            lo, hi = min(self.w1, self.w2), max(self.w1, self.w2)
        else:
            k = self.koeffizienten() or (1.0, 0.0)
            a, b = k[1], k[0] * u_max + k[1]
            lo, hi = min(a, b), max(a, b)
        if self.lo is not None and self.lo < hi:
            lo = self.lo
        if self.hi is not None and self.hi > lo:
            hi = self.hi
        return (lo, hi) if hi > lo else (lo, lo + 1.0)

    # ---- Speichern
    def als_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def aus_dict(cls, d: Any, eingang: str) -> "Kanal":
        """Robust: unbekannte/kaputte Werte -> Standard."""
        k = cls(eingang)
        if not isinstance(d, dict):
            return k
        k.aktiv = bool(d.get("aktiv", False))
        k.name = str(d.get("name", "") or "")[:40]
        k.einheit = str(d.get("einheit", "V") if d.get("einheit") is not None else "V")[:12]
        k.art = d.get("art") if d.get("art") in ART_TEXT else KEINE
        for f in ("u1", "w1", "u2", "w2", "a", "b"):
            setattr(k, f, _num(d.get(f), getattr(k, f)))
        k.lo, k.hi = _opt(d.get("lo")), _opt(d.get("hi"))
        try:
            k.dez = max(0, min(4, int(d.get("dez", 2))))
        except (TypeError, ValueError):
            k.dez = 2
        return k

    def vorlage(self, werte: Dict[str, Any]):
        """Werte einer Vorlage uebernehmen (Name und Haken bleiben)."""
        for f, v in werte.items():
            setattr(self, f, v)


# =============================================================================================== Einstellungen
_konfig: Dict[str, List[Kanal]] = {}
_neu_rechnen = False


def geraet(key: str) -> Optional[Geraet]:
    return GERAETE.get(key)


def eingang(eid: str) -> Optional[Tuple[str, Eingang]]:
    """Eingang -> (Geraet, Eingang); die Namen sind ueber alle Geraete eindeutig."""
    for g in GERAETE.values():
        for e in g.eingaenge:
            if e.id == eid:
                return g.key, e
    return None


def _liste(raw: Any, g: Geraet) -> List[Kanal]:
    by = {}
    if isinstance(raw, list):
        for d in raw:
            if isinstance(d, dict) and isinstance(d.get("eingang"), str):
                by[d["eingang"]] = d
    return [Kanal.aus_dict(by.get(e.id), e.id) for e in g.eingaenge]


def laden(settings: Dict[str, Any]):
    """Konfiguration aus den Einstellungen in das Modul uebernehmen (beim Start und nach jeder Aenderung)."""
    global _neu_rechnen
    raw = settings.get("analog") if isinstance(settings.get("analog"), dict) else {}
    for key, g in GERAETE.items():
        _konfig[key] = _liste(raw.get(key), g)
    _neu_rechnen = bool(settings.get("analog_neu_rechnen", False))


def speichern(settings: Dict[str, Any], key: str, kanaele: Iterable[Kanal]):
    """Kanaele eines Geraets in die Einstellungen schreiben (Datei schreibt der Aufrufer) und uebernehmen."""
    raw = settings.get("analog") if isinstance(settings.get("analog"), dict) else {}
    raw = dict(raw)
    raw[key] = [k.als_dict() for k in kanaele]
    settings["analog"] = raw
    laden(settings)


def set_neu_rechnen(settings: Dict[str, Any], an: bool):
    settings["analog_neu_rechnen"] = bool(an)
    laden(settings)


def neu_rechnen() -> bool:
    return _neu_rechnen


def kanaele(key: str) -> List[Kanal]:
    """Alle Eingaenge eines Geraets (Kopien)."""
    if key not in _konfig and key in GERAETE:
        _konfig[key] = _liste(None, GERAETE[key])
    return [Kanal(**asdict(k)) for k in _konfig.get(key, [])]


def aktive(key: Optional[str] = None) -> List[Tuple[str, Kanal]]:
    """[(Geraet, Kanal)] aller aktiven Kanaele (key: nur dieses Geraet), Reihenfolge wie REIHENFOLGE."""
    out = []
    for g in REIHENFOLGE:
        if key is None or key == g:
            out += [(g, k) for k in kanaele(g) if k.aktiv]
    return out


def kanal(live_key: str) -> Optional[Tuple[str, Kanal]]:
    """'ain:AI1' -> (Geraet, Kanal) oder None."""
    if not live_key.startswith(PREFIX):
        return None
    eid = live_key[len(PREFIX):]
    ge = eingang(eid)
    if ge is None:
        return None
    for k in kanaele(ge[0]):
        if k.eingang == eid:
            return ge[0], k
    return None


def live_key(eid: str) -> str:
    return PREFIX + eid


def ist_key(key: str) -> bool:
    return key.startswith(PREFIX) and eingang(key[len(PREFIX):]) is not None


def u_max(eid: str) -> float:
    ge = eingang(eid)
    return ge[1].u_max if ge else 5.0


def bereich(live_key_: str) -> Tuple[float, float, int]:
    """(lo, hi, dez) eines Live-Kanals 'ain:…'."""
    gk = kanal(live_key_)
    if gk is None:
        return 0.0, 100.0, 2
    k = gk[1]
    lo, hi = k.bereich(u_max(k.eingang))
    return lo, hi, k.dez


def geraet_von_link(link) -> Optional[str]:
    """Geraeteart der Verbindung: basis, f446, mega oder None (Zange per USB, keine Verbindung)."""
    if link is None or getattr(link, "is_zange_usb", False):
        return None
    if getattr(link, "is_basis", False):
        return BASIS
    return F446 if getattr(link, "is_stm", True) else MEGA


# =============================================================================================== Protokoll
def parse_a_line(line: str) -> Optional[Tuple[float, Dict[str, float]]]:
    """'A;<t_ms>;AI1=1.234;AI2=nan' -> (t_ms, {'AI1': 1.234, 'AI2': nan}). Felder ohne '=' zaehlen nicht."""
    f = line.strip().split(";")
    if len(f) < 2 or f[0] != "A":
        return None
    try:
        t_ms = float(f[1])
    except ValueError:
        return None
    werte: Dict[str, float] = {}
    for x in f[2:]:
        k, sep, v = x.partition("=")
        k = k.strip()
        if not sep or not k:
            continue
        try:
            werte[k] = float(v)
        except ValueError:
            werte[k] = float("nan")
    return t_ms, werte


_STAT_AI = re.compile(r"^ai(\d+)$", re.I)
_ZAHL_EINHEIT = re.compile(r"^\s*([-+0-9.eEnaNinf]+)\s*([A-Za-z]*)\s*$")


def parse_stat(lines: Iterable[str]) -> Tuple[Dict[str, float], Dict[str, str]]:
    """'#STAT;u12=13.80;u5s=4.961;ai1=0.010V;ai2=4.02mA;…' der Basis -> ({'AI1': 0.01, 'AI2': 4.02},
    {'AI1': 'V', 'AI2': 'mA'})."""
    werte: Dict[str, float] = {}
    einheiten: Dict[str, str] = {}
    for line in lines:
        if not line.startswith("#STAT;"):
            continue
        for part in line.split(";")[1:]:
            k, sep, v = part.partition("=")
            m = _STAT_AI.match(k.strip()) if sep else None
            if not m:
                continue
            z = _ZAHL_EINHEIT.match(v)
            try:
                x = float(z.group(1)) if z else float("nan")
            except ValueError:
                x = float("nan")
            eid = f"AI{m.group(1)}"
            werte[eid] = x
            einheiten[eid] = (z.group(2) if z and z.group(2) else "V")
    return werte, einheiten


def caps_eingaenge(caps: Dict[str, str]) -> List[str]:
    """'#CAP;…;ai=AI1,AI2' -> ['AI1', 'AI2'] (leer: Geraet kennt '#AI' nicht)."""
    raw = (caps or {}).get("ai", "") or ""
    return [x.strip() for x in raw.split(",") if x.strip()]


# =============================================================================================== Laeufe
def spalten(frames) -> Dict[str, np.ndarray]:
    """Rohwerte der Telegramme (link.Frame.ain) als Spalten 'analog_<Eingang>' (nan, wo kein Wert)."""
    namen: List[str] = []
    for f in frames:
        d = getattr(f, "ain", None)
        if d:
            for k in d:
                if k not in namen:
                    namen.append(k)
    out: Dict[str, np.ndarray] = {}
    for k in namen:
        out[SPALTE + k] = np.array([(getattr(f, "ain", None) or {}).get(k, np.nan) for f in frames], float)
    return out


def lauf_meta(key: Optional[str], cols: Dict[str, np.ndarray],
              einheiten: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """Eintrag "analog" in lauf.json: Geraet, Eingaenge mit Rohwert-Einheit und die zum Lauf gueltige Konfiguration
    (alle Eingaenge des Geraets, auch die inaktiven: so geht eine Neuauswertung mit geaenderter Umrechnung)."""
    if not cols:
        return None
    eids = [c[len(SPALTE):] for c in cols]
    if key is None:
        ge = eingang(eids[0])
        key = ge[0] if ge else None
    konf = [k.als_dict() for k in kanaele(key)] if key else []
    e = einheiten if isinstance(einheiten, dict) else {}
    return {"geraet": key or "", "eingaenge": eids, "einheiten": {k: str(e.get(k) or "V") for k in eids},
            "kanaele": konf}


@dataclass
class LaufKanal:
    kanal: Kanal
    roh: np.ndarray
    werte: np.ndarray
    aktiv: bool = True
    konfig_vom_lauf: bool = True


def lauf_kanaele(run: Dict[str, Any], alle: bool = False) -> List[LaufKanal]:
    """Analogkanaele eines geladenen Laufs (storage.load_any): Rohspalten mit der Konfiguration aus lauf.json, mit
    "analog_neu_rechnen" (oder ohne gespeicherte Konfiguration) mit der aktuellen. alle=True: auch inaktive."""
    cols = run.get("columns") or {}
    roh = {c[len(SPALTE):]: np.asarray(v, float) for c, v in cols.items() if c.startswith(SPALTE)}
    if not roh:
        return []
    meta = (run.get("meta") or {}).get("analog") or {}
    gespeichert = {}
    if isinstance(meta, dict) and isinstance(meta.get("kanaele"), list):
        for d in meta["kanaele"]:
            if isinstance(d, dict) and isinstance(d.get("eingang"), str):
                gespeichert[d["eingang"]] = d
    out = []
    for eid, u in roh.items():
        vom_lauf = eid in gespeichert and not _neu_rechnen
        if vom_lauf:
            k = Kanal.aus_dict(gespeichert[eid], eid)
        else:
            gk = kanal(live_key(eid))
            k = gk[1] if gk else Kanal(eid)
        if k.aktiv or alle:
            out.append(LaufKanal(k, u, k.werte(u), k.aktiv, vom_lauf))
    return out


def kanal_name(k: Kanal) -> str:
    """Name des umgerechneten Kanals in der Auswertung: „Öldruck (Analog)“."""
    return k.titel() + SUFFIX


def roh_name(eid: str) -> str:
    """Name des Rohwerts in der Auswertung: „AI1 Rohwert (Analog)“."""
    return f"{eid} Rohwert" + SUFFIX
