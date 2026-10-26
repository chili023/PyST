"""
Kanaele eines Laufs als Zeitreihen (fuer Auswerter, Cursor, CSV-Export).

Jeder Kanal: Name -> Channel(Werte, Einheit, Gruppe). Rohdaten-Spalten, die hier nicht bekannt sind
(z.B. spaeter rusEFI: tps, map, advance ... oder WSB: strom, kraft), werden automatisch mit
ihrem Spaltennamen uebernommen.
"""
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from . import physics

AFR_STOICH = 14.7


@dataclass
class Channel:
    values: np.ndarray
    unit: str
    group: str = "Messung"


# Spalten der Diagnose-Telegramme in roh.csv -> Kanal
DIAG_COLUMNS = [
    ("diag_ign_n", "Flanken Zündung", ""), ("diag_ign_pmin_us", "Zündung kürzeste Periode", "µs"),
    ("diag_ign_pmax_us", "Zündung längste Periode", "µs"), ("diag_ign_rej", "Zündung verworfen", ""),
    ("diag_roll_n", "Flanken Rolle", ""), ("diag_roll_pmin_us", "Rolle kürzeste Periode", "µs"),
    ("diag_roll_pmax_us", "Rolle längste Periode", "µs"), ("diag_roll_rej", "Rolle verworfen", ""),
    ("diag_flags", "Geräte-Flags", "bits"),
]
# PyST-Basis mit Funk-Zange bzw. Zange per USB: Zuendstaerke (Spitzenwert) und Zuendquelle
ZANGE_COLUMNS = [("diag_ign_peak_mv", "Zündstärke", "mV"), ("diag_ign_src", "Zündquelle", "0/1/2")]

# Spalten der roh.csv, die bereits als eigene Kanaele abgebildet werden
_KNOWN_COLUMNS = {"zyklus", "pc_zeit_s", "messfrequenz_hz", "zuendung_hz", "rolle_hz", "egt1_c", "afr",
                  "egt2_c", "n_rolle_1_min", "dt_s", "n_zuendung_1_min", "diag_t_ms"} | \
    {c for c, _n, _u in DIAG_COLUMNS + ZANGE_COLUMNS}


@dataclass
class FaultConfig:
    """Schwellen der Fehlerkanaele (gleitende Referenzen -> keine Fehlalarme bei Vario/Beschleunigung)."""
    roll_pct: float = 5.0         # Rolle weicht mehr als x % vom gleitenden Median ab = physikalisch unmoeglich
    ign_pct: float = 12.0         # Zuendung weicht mehr als x % ab (von Rolle x Uebersetzung UND vom eigenen Median)
    window_s: float = 0.12        # Halbbreite des kurzen Medians [s]
    ratio_window_s: float = 1.0   # Halbbreite des Medians der Uebersetzung [s] (Vario folgt)
    frozen_s: float = 1.0         # Wert exakt gleich ueber x s, waehrend sich die andere Drehzahl aendert
    frozen_other_pct: float = 3.0
    min_rpm: float = 30.0         # darunter (Stillstand) keine Bewertung [1/min Rolle bzw. Zuendung]


def rolling_median(x: np.ndarray, half: int) -> np.ndarray:
    """Zentrierter gleitender Median (nan werden ignoriert), schnell auch fuer lange Dauermessungen."""
    x = np.asarray(x, float)
    n = len(x)
    if half <= 0 or n == 0:
        return x.copy()
    pad = np.concatenate([np.full(half, np.nan), x, np.full(half, np.nan)])
    win = np.lib.stride_tricks.sliding_window_view(pad, 2 * half + 1)
    out = np.empty(n)
    step = max(1, 2_000_000 // (2 * half + 1))
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)          # Fenster nur aus nan
            for a in range(0, n, step):
                out[a:a + step] = np.nanmedian(win[a:a + step], axis=1)
    return out


def _frozen(v: np.ndarray, other: np.ndarray, min_len: int, other_pct: float, min_val: float) -> np.ndarray:
    """1, wo ein Wert ueber mindestens min_len Telegramme exakt gleich bleibt, obwohl sich `other` um mehr als
    other_pct % aendert (typisch: Messwert haengt, weil keine Flanken mehr ankommen)."""
    n = len(v)
    out = np.zeros(n)
    if n < 2 or min_len < 2:
        return out
    change = np.concatenate([[True], v[1:] != v[:-1]])
    starts = np.flatnonzero(change)
    ends = np.concatenate([starts[1:], [n]])
    for a, b in zip(starts, ends):
        if b - a < min_len or not np.isfinite(v[a]) or v[a] <= min_val:
            continue
        o = other[a:b]
        o = o[np.isfinite(o) & (o > 0)]
        if len(o) and (o.max() / max(o.min(), 1e-9) - 1.0) * 100.0 > other_pct:
            out[a:b] = 1.0
    return out


def fault_channels(n_roll, n_ign, rate: float, cfg: Optional[FaultConfig] = None) -> "OrderedDict[str, Channel]":
    """Fehlerkanaele aus Rolle und Zuendung (fuer Laeufe, Dauermessungen und alte Daten):
    Fehler Rolle      – Rolle weicht mehr als roll_pct vom gleitenden Median ab (Masse der Rolle verhindert das)
    Fehler Zündung    – Zuendung weicht mehr als ign_pct von Rolle x gleitender Uebersetzung ab
                        und zugleich vom eigenen gleitenden Median (Kupplung/Vario allein loesen nichts aus)
    Doppelimpuls      – Zuendung etwa doppelt so hoch wie erwartet
    Wert eingefroren  – Rolle oder Zuendung haengt, obwohl sich die andere Drehzahl aendert
    Das erste Telegramm wird nicht bewertet (Messfenster nach dem Start nur teilweise gefuellt)."""
    cfg = cfg or FaultConfig()
    n_roll = np.asarray(n_roll, float)
    n_ign = np.asarray(n_ign, float)
    rate = rate if rate and rate > 0 else 60.0
    h = max(2, int(round(cfg.window_s * rate)))
    hr = max(h, int(round(cfg.ratio_window_s * rate)))
    med_roll = rolling_median(n_roll, h)
    with np.errstate(invalid="ignore", divide="ignore"):
        roll_ok = med_roll > cfg.min_rpm
        dev_roll = np.where(roll_ok, n_roll / med_roll - 1.0, np.nan)
        err_roll = (np.abs(dev_roll) * 100.0 > cfg.roll_pct)
        # Uebersetzung Zuendung / (geglaettete) Rolle, nur wo die Rolle stimmt; gleitender Median = Referenz
        ratio = np.where(roll_ok & ~err_roll, n_ign / med_roll, np.nan)
        ratio_ref = rolling_median(ratio, hr)
        ref_ign = med_roll * ratio_ref
        med_ign = rolling_median(n_ign, h)
        ign_ok = (med_ign > cfg.min_rpm) | (ref_ign > cfg.min_rpm)
        dev_ref = np.where(ref_ign > cfg.min_rpm, n_ign / ref_ign - 1.0, np.nan)
        dev_own = np.where(med_ign > cfg.min_rpm, n_ign / med_ign - 1.0, np.nan)
        lim = cfg.ign_pct / 100.0
        own_bad = np.abs(dev_own) > lim
        ref_bad = np.where(np.isfinite(dev_ref), np.abs(dev_ref) > lim, True)   # ohne Referenz: nur eigener Median
        err_ign = ign_ok & own_bad & ref_bad
        q = np.where(np.isfinite(dev_ref), dev_ref, dev_own) + 1.0
        double = err_ign & (q > 1.7) & (q < 2.3)
        dev_ign = np.where(np.isfinite(dev_ref), dev_ref, dev_own)
    # erstes Telegramm nach dem Start: Messfenster der Firmware nur teilweise gefuellt -> nicht bewerten
    for a in (err_roll, err_ign, double):
        a[:1] = False
    min_len = max(2, int(round(cfg.frozen_s * rate)))
    frozen = np.maximum(_frozen(n_roll, n_ign, min_len, cfg.frozen_other_pct, cfg.min_rpm),
                        _frozen(n_ign, n_roll, min_len, cfg.frozen_other_pct, cfg.min_rpm))
    ch: "OrderedDict[str, Channel]" = OrderedDict()
    ch["Fehler Rolle"] = Channel(err_roll.astype(float), "0/1", "Fehler")
    ch["Fehler Zündung"] = Channel(err_ign.astype(float), "0/1", "Fehler")
    ch["Doppelimpuls Zündung"] = Channel(double.astype(float), "0/1", "Fehler")
    ch["Wert eingefroren"] = Channel(frozen, "0/1", "Fehler")
    ch["Abweichung Rolle"] = Channel(dev_roll * 100.0, "%", "Fehler")
    ch["Abweichung Zündung"] = Channel(dev_ign * 100.0, "%", "Fehler")
    return ch


FAULT_KEYS = ("Fehler Rolle", "Fehler Zündung", "Doppelimpuls Zündung", "Wert eingefroren")
FAULT_SHORT = {"Fehler Rolle": "Rolle", "Fehler Zündung": "Zündung", "Doppelimpuls Zündung": "Doppelimpuls",
               "Wert eingefroren": "eingefroren"}


def fault_summary(ch: Dict[str, Channel], moving: Optional[np.ndarray] = None) -> Dict[str, float]:
    """Fehleranteil in % je Fehlerkanal (bezogen auf die Telegramme, in denen sich etwas dreht)."""
    out = {}
    for k in FAULT_KEYS:
        c = ch.get(k)
        if c is None or not len(c.values):
            continue
        v = c.values if moving is None else c.values[moving]
        out[k] = float(np.nanmean(v) * 100.0) if len(v) else 0.0
    return out


def fault_text(summary: Dict[str, float]) -> str:
    """'Rolle 12,3 % · Zündung 4,0 %' (nur Kanaele > 0)"""
    parts = [f"{FAULT_SHORT[k]} {v:.1f} %".replace(".", ",") for k, v in summary.items() if v >= 0.05]
    return " · ".join(parts) if parts else "keine"


def run_faults(run: Dict, cfg: Optional[FaultConfig] = None) -> Tuple["OrderedDict[str, Channel]", Dict[str, float]]:
    """Fehlerkanaele + Fehleranteile eines geladenen Laufs (storage.load_any)."""
    dt = np.asarray(run["dt"], float)
    n_roll = np.asarray(run["n_roll"], float)
    ch = fault_channels(n_roll, run["n_meas"], physics.sample_rate(dt), cfg)
    moving = n_roll > (cfg or FaultConfig()).min_rpm
    return ch, fault_summary(ch, moving if moving.any() else None)


def lambda_from_afr(afr: np.ndarray) -> np.ndarray:
    afr = np.asarray(afr, float)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(afr > 0.5, afr / AFR_STOICH, np.nan)


def run_channels(run: Dict, res: Optional[physics.RunResult], p: physics.DynoParams) -> "OrderedDict[str, Channel]":
    dt = np.asarray(run["dt"], float)
    n_roll = np.asarray(run["n_roll"], float)
    N = len(dt)
    p = physics.effective(p, physics.sample_rate(dt))
    t = np.concatenate([[0.0], np.cumsum(dt[1:])]) if N else dt
    pc = run.get("columns", {}).get("pc_zeit_s")
    if run.get("kind") == "dauermessung" and pc is not None and len(pc) == N:
        t = np.asarray(pc, float) - (pc[0] if N else 0.0)   # PC-Zeit: Luecken und Marker stimmen
    ch: "OrderedDict[str, Channel]" = OrderedDict()
    ch["Zeit"] = Channel(t, "s", "Zeit")
    if res is not None and res.full_n is not None:
        ch["Drehzahl Motor"] = Channel(res.full_n, "1/min")
    ch["Drehzahl Motor (roh)"] = Channel(n_roll * p.ratio, "1/min")
    ch["Drehzahl Zündung"] = Channel(np.asarray(run["n_meas"], float), "1/min")
    ch["Drehzahl Rolle"] = Channel(n_roll, "1/min")
    ch["Geschwindigkeit"] = Channel(n_roll / 60.0 * p.roll_circ * 3.6, "km/h")
    # Uebersetzung aus Zuendung und Rolle (bei CVT/Vario aendert sie sich waehrend des Laufs;
    # Spruenge auf ~2x zeigen doppelte Zuendimpulse)
    n_meas = np.asarray(run["n_meas"], float)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio_raw = np.where(n_roll > 1.0, n_meas / n_roll, np.nan)
        # Zaehler und Nenner gleich (mittig) filtern: der Rollenfilter der Leistungsrechnung (filter_roll)
        # hinkt nach und wuerde die Uebersetzung beim Beschleunigen zu gross machen
        half = max(1, int(p.ma) // 2)
        n_roll_f = physics.median_centered(n_roll, half)
        n_meas_f = physics.median_centered(n_meas, half)
        ratio_f = np.where(n_roll_f > 1.0, n_meas_f / n_roll_f, np.nan)
    ch["Übersetzung Zündung/Rolle"] = Channel(ratio_raw, "nKW/nR", "Übersetzung")
    ch["Übersetzung gefiltert"] = Channel(ratio_f, "nKW/nR", "Übersetzung")
    ch["Übersetzung eingestellt"] = Channel(np.full(N, float(p.ratio)), "nKW/nR", "Übersetzung")
    # Uebersetzung aus der ECU-Drehzahl (unabhaengig vom Zuendabnehmer des Messboards)
    rpm = run.get("columns", {}).get("rusefi_RPMValue")
    if rpm is not None and len(rpm) == N:
        rpm = np.asarray(rpm, float)
        with np.errstate(invalid="ignore", divide="ignore"):
            ch["Übersetzung ECU/Rolle"] = Channel(np.where(n_roll > 1.0, rpm / n_roll, np.nan), "nKW/nR",
                                                  "Übersetzung")
            rpm_f = physics.median_centered(rpm, half)
            ch["Übersetzung ECU/Rolle gefiltert"] = Channel(np.where(n_roll_f > 1.0, rpm_f / n_roll_f, np.nan),
                                                            "nKW/nR", "Übersetzung")
    if res is not None and res.full_ps is not None:
        with np.errstate(invalid="ignore"):
            ch["Leistung"] = Channel(res.full_ps, "PS", "Auswertung")
            ch["Drehmoment"] = Channel(res.full_nm, "Nm", "Auswertung")
            ch["Beschleunigung Motor"] = Channel(res.full_accel * 60.0 / (2 * np.pi), "1/min/s", "Auswertung")
    afr = np.asarray(run["afr"], float)
    ch["Lambda"] = Channel(lambda_from_afr(afr), "λ", "Gemisch")
    ch["AFR"] = Channel(np.where(afr > 0.5, afr, np.nan), "AFR", "Gemisch")
    ch["EGT 1"] = Channel(np.asarray(run["egt"], float), "°C", "Temperatur")
    cols = run.get("columns", {})
    ecu_meta = ((run.get("meta") or {}).get("rusefi") or {}).get("kanaele", {})
    if "egt2_c" in cols:
        ch["EGT 2"] = Channel(cols["egt2_c"], "°C", "Temperatur")
    ch["Messfrequenz"] = Channel(1.0 / np.where(dt > 0, dt, np.nan), "Hz", "System")
    # Fehlerkanaele (auch fuer alte Laeufe, aus Rolle und Zuendung berechnet)
    if N:
        ch.update(fault_channels(n_roll, n_meas, physics.sample_rate(dt)))
    # Diagnose-Telegramm (PST-Diag 1): Flanken je Telegramm, kuerzeste/laengste Periode, Flags
    for col, name, unit in DIAG_COLUMNS:
        if col in cols and len(cols[col]) == N:
            ch[name] = Channel(np.asarray(cols[col], float), unit, "Diagnose")
    for col, name, unit in ZANGE_COLUMNS:
        if col in cols and len(cols[col]) == N:
            v = np.asarray(cols[col], float)
            if col == "diag_ign_peak_mv":
                v = np.where(v > 0, v, np.nan)                  # 0 = kein Funke im Fenster
            ch[name] = Channel(v, unit, "Zange")
    for name, values in cols.items():
        if name not in _KNOWN_COLUMNS and len(values) == N:
            if name.startswith("rusefi_"):
                m = ecu_meta.get(name, {})
                label = m.get("label") or name[len("rusefi_"):]
                ch[f"{label} (ECU)"] = Channel(np.asarray(values, float), m.get("einheit", ""), "ECU")
            else:
                ch[name] = Channel(np.asarray(values, float), "", "Zusatz")
    return ch
