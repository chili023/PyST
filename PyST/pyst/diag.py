"""
PST-Diag 1: Diagnose-Telegramme, Flanken-Bursts und Software-Trigger (Ereigniserkennung).

Spezifikation: docs/04_PST_Diag_1.md. Dieses Modul hat keine Qt-Abhaengigkeit (Tests, Kommandozeile).

- Burst: Flankenaufnahme ("#BURST ..."), Zeitstempel als uint32-Ticks -> entfalten, Perioden je Flanke,
  Pulsbreiten (Flanke B), Logikanalysator-Spuren.
- EventDetector: Ereignisse im Telegrammstrom fuer ALLE Geraete (auch alter Mega 3.0.0 ohne Diagnose):
  Rollensprung, Uebersetzungssprung, Zuendaussetzer/Timeout, Doppelimpuls, eingefrorener Wert, Schwellwert.
"""
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

# Zusatzfelder des D-Telegramms (nach zyklus;t_ms;rate;ign;roll;egt1;afr;egt2 – siehe link.parse_diag)
DIAG_FIELDS = ("t_ms", "ign_n", "ign_pmin_us", "ign_pmax_us", "ign_rej",
               "roll_n", "roll_pmin_us", "roll_pmax_us", "roll_rej", "flags")

# Bits im Feld flags
FLAG_IGN_TIMEOUT = 1 << 0
FLAG_ROLL_TIMEOUT = 1 << 1
FLAG_DATA_LOST = 1 << 2
FLAG_BURST_ACTIVE = 1 << 3
FLAG_IGN_STALE = 1 << 4
FLAG_ROLL_STALE = 1 << 5
FLAG_BURST_GAP = 1 << 6
FLAG_TEXT = {
    FLAG_IGN_TIMEOUT: "Zündung Timeout", FLAG_ROLL_TIMEOUT: "Rolle Timeout",
    FLAG_DATA_LOST: "Datenverlust im Gerät", FLAG_BURST_ACTIVE: "Burst aktiv",
    FLAG_IGN_STALE: "Zündung ohne neue Periode", FLAG_ROLL_STALE: "Rolle ohne neue Periode",
    FLAG_BURST_GAP: "Telegramme durch Burst-Ausgabe ausgesetzt",
}


def flags_text(flags: int) -> str:
    return ", ".join(t for b, t in FLAG_TEXT.items() if flags & b)


# ---------------------------------------------------------------------------------------------- Burst
@dataclass
class Burst:
    """Eine Flankenaufnahme. edges: (idx, Kanal 'I'/'R', Pegel nach der Flanke 0/1, ticks uint32)."""
    n: int = 0
    tick_ns: float = 62.5
    trig_idx: int = -1
    ch: str = "A"
    lost: int = 0
    edges: List[Tuple[int, str, int, int]] = field(default_factory=list)
    request: Dict[str, str] = field(default_factory=dict)    # Parameter der Anforderung (Kanal, Flanke …)
    t_pc: float = 0.0                                         # PC-Zeit bei Empfang
    complete: bool = False

    def times(self) -> np.ndarray:
        """Zeit jeder Flanke [s], 0 = Trigger (bzw. erste Flanke ohne Trigger)."""
        if not self.edges:
            return np.zeros(0)
        t = unwrap_ticks([e[3] for e in self.edges]) * (self.tick_ns * 1e-9)
        ref = self.trig_idx if 0 <= self.trig_idx < len(t) else 0
        return t - t[ref]

    def channel(self, ch: str) -> Tuple[np.ndarray, np.ndarray]:
        """(Zeiten [s], Pegel) der Flanken eines Kanals."""
        t = self.times()
        sel = np.array([e[1] == ch for e in self.edges], bool)
        lvl = np.array([e[2] for e in self.edges], int)
        return (t[sel], lvl[sel]) if len(t) else (t, lvl)

    def periods(self, ch: str, level: Optional[int] = None) -> Tuple[np.ndarray, np.ndarray]:
        """Periode je Flanke [s] (Abstand zur vorigen Flanke gleicher Richtung).
        level=None: Richtung der ersten Flanke (bei Flanke B also je Richtung getrennt -> beide zusammen).
        -> (Zeitpunkt der Flanke [s], Periode [s])"""
        t, lvl = self.channel(ch)
        out_t, out_p = [], []
        for L in ((0, 1) if level is None else (level,)):
            tt = t[lvl == L]
            if len(tt) >= 2:
                out_t.append(tt[1:])
                out_p.append(np.diff(tt))
        if not out_t:
            return np.zeros(0), np.zeros(0)
        tt, pp = np.concatenate(out_t), np.concatenate(out_p)
        o = np.argsort(tt, kind="stable")
        return tt[o], pp[o]

    def pulse_widths(self, ch: str, level: int = 0) -> Tuple[np.ndarray, np.ndarray]:
        """Pulsbreite (nur bei Flanke B sinnvoll): Dauer des Pegels `level` (0 = Low-Puls) -> (Beginn, Breite [s])."""
        t, lvl = self.channel(ch)
        starts, widths = [], []
        for i in range(len(t) - 1):
            if lvl[i] == level and lvl[i + 1] != level:
                starts.append(t[i])
                widths.append(t[i + 1] - t[i])
        return np.array(starts), np.array(widths)

    def trace(self, ch: str) -> Tuple[np.ndarray, np.ndarray]:
        """Logikanalysator-Spur als Treppe: (x, y) fuer stepMode bzw. Linienzug mit senkrechten Flanken."""
        t, lvl = self.channel(ch)
        if not len(t):
            return np.zeros(0), np.zeros(0)
        x = np.repeat(t, 2)[1:]
        y = np.repeat(lvl, 2)[:-1]
        # vor der ersten Flanke: Gegenpegel
        x = np.concatenate([[t[0] - (t[-1] - t[0]) * 0.02 - 1e-6], [t[0]], x])
        y = np.concatenate([[1 - lvl[0]], [1 - lvl[0]], y])
        return x, y

    def to_rows(self) -> List[List]:
        t = self.times()
        return [[e[0], e[1], e[2], e[3], f"{t[k]:.9f}"] for k, e in enumerate(self.edges)]


def unwrap_ticks(ticks) -> np.ndarray:
    """uint32-Zeitstempel (laufen ueber) -> fortlaufende int64-Ticks. Die Firmware garantiert Abstaende < 2^31,
    daher wird jede Differenz vorzeichenbehaftet (modulo 2^32) gelesen."""
    a = np.asarray(ticks, dtype=np.int64)
    if not len(a):
        return a
    d = (np.diff(a) + (1 << 31)) % (1 << 32) - (1 << 31)     # vorzeichenbehaftet: |Abstand| < 2^31
    return np.concatenate([[a[0]], a[0] + np.cumsum(d)])


def parse_kv(text: str) -> Dict[str, str]:
    """'#BURST;BEGIN;n=800;tick_ns=62.5' -> {'n': '800', 'tick_ns': '62.5'} (Felder ohne '=' werden ignoriert)."""
    out = {}
    for part in text.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------------------------------------- Ereignisse
EV_ROLL_JUMP = "Rollensprung"
EV_RATIO_JUMP = "Übersetzungssprung"
EV_IGN_MISS = "Zündaussetzer"
EV_DOUBLE = "Doppelimpuls"
EV_FROZEN = "Wert eingefroren"
EV_THRESHOLD = "Schwellwert"
EV_DEVICE = "Gerät meldet"
EVENT_TYPES = [EV_ROLL_JUMP, EV_RATIO_JUMP, EV_IGN_MISS, EV_DOUBLE, EV_FROZEN, EV_THRESHOLD]


@dataclass
class Event:
    t: float            # Zeit in der Messung [s]
    kind: str
    text: str
    index: int = -1     # Telegramm-Nummer in der Messung


@dataclass
class TriggerConfig:
    roll_jump_pct: float = 8.0       # Rolle weicht mehr als x % vom Median der letzten Telegramme ab
    ratio_jump_pct: float = 15.0     # Zuendung/Rolle weicht mehr als x % ab
    ign_drop_pct: float = 40.0       # Zuendung faellt um mehr als x % (oder 0) -> Aussetzer
    double_lo: float = 1.7           # Zuendung springt auf das 1,7…2,3-fache -> Doppelimpuls
    double_hi: float = 2.3
    frozen_s: float = 1.0            # Wert unveraendert, waehrend sich die andere Drehzahl bewegt
    threshold_channel: str = ""      # Schwellwert-Kanal (Name wie im Linienschreiber)
    threshold: float = 0.0
    threshold_above: bool = True
    min_roll: float = 30.0           # Rolle [1/min], darunter keine Sprung-/Aussetzer-Ereignisse
    holdoff_s: float = 0.25          # gleiche Ereignisart fruehestens nach … s wieder
    enabled: Tuple[str, ...] = tuple(EVENT_TYPES[:5])


class EventDetector:
    """Software-Trigger auf den Telegrammstrom. feed() je Telegramm mit einem Werte-Dict:
    t [s], n_roll [1/min], n_ign [1/min] (aus der Zuendung), optional flags, ign_pmin_us, und beliebige
    weitere Kanaele (Schwellwert). Liefert die erkannten Ereignisse."""

    def __init__(self, cfg: Optional[TriggerConfig] = None, history: int = 8):
        self.cfg = cfg or TriggerConfig()
        self.roll = deque(maxlen=history)
        self.ign = deque(maxlen=history)
        self.ratio = deque(maxlen=2 * history)
        self.last_t: Dict[str, float] = {}
        self.frozen: Dict[str, Tuple[float, float, float]] = {}   # Kanal -> (Wert, seit t, andere Drehzahl dann)
        self.prev_thr: Optional[bool] = None
        self.prev_flags = 0
        self.index = 0

    def _emit(self, out: List[Event], t: float, kind: str, text: str):
        if kind != EV_DEVICE and kind not in self.cfg.enabled and kind != EV_THRESHOLD:
            return
        if t - self.last_t.get(kind, -1e9) < self.cfg.holdoff_s:
            return
        self.last_t[kind] = t
        out.append(Event(t, kind, text, self.index))

    def feed(self, v: Dict[str, float]) -> List[Event]:
        c = self.cfg
        out: List[Event] = []
        t = float(v.get("t", 0.0))
        roll = float(v.get("n_roll", 0.0))
        ign = float(v.get("n_ign", 0.0))
        moving = roll > c.min_roll
        # Rollensprung: physikalisch unmoeglich schnelle Aenderung der Rollendrehzahl
        if moving and len(self.roll) >= 4:
            ref = float(np.median(self.roll))
            if ref > c.min_roll and abs(roll / ref - 1.0) * 100.0 > c.roll_jump_pct:
                self._emit(out, t, EV_ROLL_JUMP, f"Rolle {roll:.0f} statt ~{ref:.0f} 1/min ({(roll / ref - 1) * 100:+.0f} %)")
        # Zuendung: Doppelimpuls (~2x) / Aussetzer (Einbruch oder 0)
        if len(self.ign) >= 4:
            ref_i = float(np.median(self.ign))
            if ref_i > 0:
                q = ign / ref_i
                if c.double_lo <= q <= c.double_hi:
                    self._emit(out, t, EV_DOUBLE, f"Zündung {ign:.0f} statt ~{ref_i:.0f} 1/min (×{q:.2f})")
                elif moving and q < 1.0 - c.ign_drop_pct / 100.0:
                    self._emit(out, t, EV_IGN_MISS, f"Zündung {ign:.0f} statt ~{ref_i:.0f} 1/min")
        pmin = v.get("ign_pmin_us")
        if pmin and ign > 0 and EV_DOUBLE in c.enabled:
            period_us = 60e6 / (ign * float(v.get("imp", 1.0)))
            if 0 < pmin < 0.6 * period_us:              # Geraet sieht eine zu kurze Einzelperiode
                self._emit(out, t, EV_DOUBLE, f"kürzeste Zündperiode {pmin:.0f} µs statt ~{period_us:.0f} µs")
        # Uebersetzungssprung (Zuendung/Rolle), gleitende Referenz -> Vario ohne Fehlalarm
        if moving and ign > 0:
            r = ign / roll
            if len(self.ratio) >= 6:
                ref_r = float(np.median(self.ratio))
                if ref_r > 0 and abs(r / ref_r - 1.0) * 100.0 > c.ratio_jump_pct:
                    self._emit(out, t, EV_RATIO_JUMP, f"Übersetzung {r:.3f} statt ~{ref_r:.3f}")
            self.ratio.append(r)
        # eingefrorener Wert: exakt gleich, waehrend sich die andere Drehzahl deutlich aendert
        for name, val, other in (("Rolle", roll, ign), ("Zündung", ign, roll)):
            st = self.frozen.get(name)
            if st is None or val != st[0]:
                self.frozen[name] = (val, t, other)
            elif val > 0 and t - st[1] >= c.frozen_s and st[2] > 0 and abs(other / st[2] - 1.0) > 0.03:
                self._emit(out, t, EV_FROZEN, f"{name} seit {t - st[1]:.1f} s unverändert {val:.0f} 1/min")
                self.frozen[name] = (val, t, other)          # erst nach weiteren frozen_s wieder melden
        # Schwellwert
        if c.threshold_channel and c.threshold_channel in v:
            x = v[c.threshold_channel]
            if x is not None and np.isfinite(x):
                above = x > c.threshold if c.threshold_above else x < c.threshold
                if above and self.prev_thr is False:
                    rel = ">" if c.threshold_above else "<"
                    self._emit(out, t, EV_THRESHOLD, f"{c.threshold_channel} = {x:.4g} {rel} {c.threshold:.4g}")
                self.prev_thr = above
        # Meldungen des Geraets (D-Telegramm): Timeout, Datenverlust
        fl = int(v.get("flags", 0) or 0)
        new = fl & ~self.prev_flags & (FLAG_IGN_TIMEOUT | FLAG_ROLL_TIMEOUT | FLAG_DATA_LOST)
        if new & FLAG_IGN_TIMEOUT and moving:
            self._emit(out, t, EV_IGN_MISS, "Gerät: Zündung Timeout")
        if new & (FLAG_ROLL_TIMEOUT | FLAG_DATA_LOST):
            self._emit(out, t, EV_DEVICE, flags_text(new & (FLAG_ROLL_TIMEOUT | FLAG_DATA_LOST)))
        self.prev_flags = fl
        self.roll.append(roll)
        self.ign.append(ign)
        self.index += 1
        return out


def detect_events(t, n_roll, n_ign, cfg: Optional[TriggerConfig] = None, extra: Optional[Dict] = None) -> List[Event]:
    """Ereignisse in gespeicherten Daten (gleiche Logik wie live)."""
    det = EventDetector(cfg)
    out: List[Event] = []
    extra = extra or {}
    for i in range(len(t)):
        v = {"t": t[i], "n_roll": n_roll[i], "n_ign": n_ign[i]}
        for k, a in extra.items():
            v[k] = a[i]
        out += det.feed(v)
    return out
