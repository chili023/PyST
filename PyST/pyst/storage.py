"""
Laeufe speichern und laden.

Ein Lauf = Ordner  messungen/YYMMDD_HHMMSS_<PS>_<n>/  mit
  roh.csv       alle Telegramme (Zyklus, Zeit, Frequenzen, EGT, AFR) + nRolle, dT
                (+ Spalten rusefi_<Kanal>: ECU-Werte auf die Telegramm-Zeitpunkte gelegt)
  rusefi.csv    ECU-Werte in ihrer eigenen Abtastung (nur mit rusEFI)
  kurve.csv     ausgewertete Kurve (n, PS, Nm, n gemessen, AFR, EGT)
  lauf.json     Parameter, Klima, Ergebnis, Firmware-Info
  lauf.xml      dasselbe im LabVIEW-3.2.1-Format (in LabVIEW-Recalc oeffnbar)
Geladen werden PyST-Ordner (bzw. deren lauf.json) und LabVIEW-XML-Dateien.
"""
import csv
import datetime as _dt
import json
import os
from dataclasses import asdict
from typing import Any, Dict

import numpy as np

from . import lvxml, physics, rusefi


def default_dir() -> str:
    from .db import base_dir
    return os.path.join(base_dir(), "messungen")


def save_run(ctrl, vehicle: str = "", firmware: str = "", base_dir: str = "") -> str:
    res = ctrl.result
    p = ctrl.params
    stamp = _dt.datetime.now().strftime("%y%m%d_%H%M%S")
    name = stamp + (f"_{res.p_max:.1f}_{res.n_pmax:.0f}".replace(".", ",") if res else "_ohne_Ergebnis")
    folder = os.path.join(base_dir or default_dir(), name)
    os.makedirs(folder, exist_ok=True)

    n_roll, dt, n_meas, afr, egt = ctrl.arrays()
    t0 = ctrl.frames[0].t if ctrl.frames else 0.0
    ecu = getattr(ctrl, "ecu_data", None)
    ecu_cols = rusefi.resample(ecu, np.array([f.t for f in ctrl.frames])) if ecu and ctrl.frames else {}
    with open(os.path.join(folder, "roh.csv"), "w", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["zyklus", "pc_zeit_s", "messfrequenz_hz", "zuendung_hz", "rolle_hz",
                    "egt1_c", "afr", "egt2_c", "n_rolle_1_min", "dt_s", "n_zuendung_1_min"] + list(ecu_cols))
        for i, (f, nr, d, nm) in enumerate(zip(ctrl.frames, n_roll, dt, n_meas)):
            w.writerow([f.cycle, f"{f.t - t0:.4f}", f.rate, f.ign_hz, f.roll_hz, f.egt1, f.afr, f.egt2,
                        f"{nr:.4f}", f"{d:.6f}", f"{nm:.1f}"] + [f"{v[i]:.6g}" for v in ecu_cols.values()])
    if ecu:
        with open(os.path.join(folder, "rusefi.csv"), "w", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["pc_zeit_s"] + [c["name"] for c in ecu["channels"]])
            for t, row in zip(ecu["t"], ecu["values"]):
                w.writerow([f"{t - t0:.4f}"] + [f"{v:.6g}" for v in row])
    if res is not None:
        with open(os.path.join(folder, "kurve.csv"), "w", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["n_1_min", "leistung_ps", "drehmoment_nm", "n_zuendung_1_min", "afr", "egt_c"])
            for row in zip(res.n, res.ps, res.nm, res.n_meas, res.afr, res.egt):
                w.writerow([f"{x:.3f}" for x in row])

    meta: Dict[str, Any] = {
        "programm": "PyST",
        "datum": _dt.datetime.now().isoformat(timespec="seconds"),
        "fahrzeug": vehicle,
        "firmware": firmware,
        "port": ctrl.link.port,
        "parameter": asdict(p),
        "klima": ctrl.climate,
        "com_verloren": ctrl.link.lost,
        "telegramme": len(ctrl.frames),
        "rusefi": None if not ecu else {
            "signatur": ecu["signature"], "ini": os.path.basename(ecu["ini"]),
            "abtastrate_hz": round(ecu["rate_hz"], 1), "werte": len(ecu["t"]),
            "kanaele": {rusefi.COLUMN_PREFIX + c["name"]: {"name": c["name"], "label": c["label"],
                                                           "einheit": c["unit"]} for c in ecu["channels"]},
        },
        "ergebnis": None if res is None else {
            "p_max_ps": res.p_max, "n_pmax": res.n_pmax, "m_max_nm": res.m_max, "n_mmax": res.n_mmax,
            "v_max_kmh": res.v_max, "ka": res.ka, "strecke_m": res.distance_m, "ende": res.end_reason,
            "text": res.summary(),
        },
    }
    with open(os.path.join(folder, "lauf.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
    # LabVIEW-Datei mit Filtern in Messpunkten (LabVIEW kennt keine Sekunden)
    lvxml.write_run(os.path.join(folder, "lauf.xml"), physics.effective(p, physics.sample_rate(dt)),
                    n_roll, n_meas, afr, egt, dt,
                    vehicle=vehicle, distance_m=res.distance_m if res else 0.0, port=ctrl.link.port)
    return folder


def load_any(path: str) -> Dict[str, Any]:
    """Laedt LabVIEW-XML, PyST-Ordner oder lauf.json -> Rohdaten + Parameter."""
    if os.path.isdir(path):
        path = os.path.join(path, "lauf.json")
    if path.lower().endswith(".xml"):
        r = lvxml.read_run(path)
        r["source"] = "LabVIEW"
        r["name"] = os.path.basename(path)
        return r
    with open(path, encoding="utf-8") as fh:
        meta = json.load(fh)
    folder = os.path.dirname(path)
    cols: Dict[str, list] = {}
    with open(os.path.join(folder, "roh.csv"), newline="") as fh:
        rd = csv.DictReader(fh, delimiter=";")
        for row in rd:
            for k, v in row.items():
                cols.setdefault(k, []).append(float(v))
    p = physics.DynoParams(**meta["parameter"])
    return {
        "params": p, "vehicle": meta.get("fahrzeug", ""), "title": "", "date": meta.get("datum", ""),
        "n_roll": np.array(cols["n_rolle_1_min"]), "dt": np.array(cols["dt_s"]),
        "n_meas": np.array(cols["n_zuendung_1_min"]), "afr": np.array(cols["afr"]),
        "egt": np.array(cols["egt1_c"]), "source": "PyST", "name": os.path.basename(folder),
        "meta": meta, "columns": {k: np.array(v) for k, v in cols.items()},
    }


def recompute(run: Dict[str, Any], override: physics.DynoParams = None):
    p = override or run["params"]
    return physics.evaluate(run["n_roll"], run["dt"], p, run["n_meas"], run["afr"], run["egt"],
                            require_end=False)
