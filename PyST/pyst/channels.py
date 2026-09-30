"""
Kanaele eines Laufs als Zeitreihen (fuer Auswerter, Cursor, CSV-Export).

Jeder Kanal: Name -> Channel(Werte, Einheit, Gruppe). Rohdaten-Spalten, die hier nicht bekannt sind
(z.B. spaeter rusEFI: tps, map, advance ... oder WSB: strom, kraft), werden automatisch mit
ihrem Spaltennamen uebernommen.
"""
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from . import physics

AFR_STOICH = 14.7


@dataclass
class Channel:
    values: np.ndarray
    unit: str
    group: str = "Messung"


# Spalten der roh.csv, die bereits als eigene Kanaele abgebildet werden
_KNOWN_COLUMNS = {"zyklus", "pc_zeit_s", "messfrequenz_hz", "zuendung_hz", "rolle_hz", "egt1_c", "afr",
                  "egt2_c", "n_rolle_1_min", "dt_s", "n_zuendung_1_min"}


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
    for name, values in cols.items():
        if name not in _KNOWN_COLUMNS and len(values) == N:
            if name.startswith("rusefi_"):
                m = ecu_meta.get(name, {})
                label = m.get("label") or name[len("rusefi_"):]
                ch[f"{label} (ECU)"] = Channel(np.asarray(values, float), m.get("einheit", ""), "ECU")
            else:
                ch[name] = Channel(np.asarray(values, float), "", "Zusatz")
    return ch
