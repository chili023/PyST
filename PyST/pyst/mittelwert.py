"""
Mittelwert-Lauf: mehrere Laeufe auf einem gemeinsamen Drehzahlraster mitteln. Ohne Qt.

Rechnung (NOTIZEN Runde 3 "Mittelwert-Lauf"):
- je Lauf nur der steigende Teil: bis zur hoechsten Drehzahl, davon nur Punkte ueber dem bisherigen Maximum
- lineare Interpolation auf ein Raster (Standard 50 1/min, Vielfache des Rasters)
- nur der Bereich, den alle Laeufe abdecken, oben fallen 300 1/min weg (Gas-weg-Knick)
- je Kanal Mittelwert, min, max und Standardabweichung; Pmax/Mmax aus der gemittelten Kurve

Gespeichert wird ein Mittelwert wie ein Lauf (db.runs, art="mittelwert") mit eigenem Ordner:
mittelwert.json (Metadaten) und mittelwert.csv (Kurven).
"""
import csv
import datetime as _dt
import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from . import __version__
from . import db as _db

RASTER = 50.0                # Drehzahlraster [1/min]
OBEN_WEG = 300.0             # oben abgeschnitten [1/min]
MIN_LAEUFE = 2
JSON_NAME = "mittelwert.json"
CSV_NAME = "mittelwert.csv"
FORMAT = "pyst-mittelwert"
VERSION = 1
SETUP_IGNORIEREN = ("id", "vehicle_id", "name", "notiz")
ECU_PREFIX = _db.ECU_PREFIX  # Schluessel der ECU-Kanaele in kurven_aus_ergebnis: "ecu:" + rusEFI-Kanal


@dataclass
class MittelwertErgebnis:
    n: np.ndarray                                  # Drehzahlraster [1/min]
    werte: Dict[str, np.ndarray]                   # Kanal -> Mittelwert
    minimum: Dict[str, np.ndarray]
    maximum: Dict[str, np.ndarray]
    sigma: Dict[str, np.ndarray]                   # Standardabweichung (Stichprobe, ddof=1)
    n_lo: float                                    # Beginn des gemeinsamen Bereichs (groesster Start)
    n_hi: float                                    # Ende des gemeinsamen Bereichs (kleinstes Ende, vor oben_weg)
    raster: float = RASTER
    oben_weg: float = OBEN_WEG
    anzahl: int = 0                                # Zahl der Quelllaeufe
    quellen: List[int] = field(default_factory=list)   # Lauf-IDs (vom Aufrufer)
    p_max: float = 0.0
    n_pmax: float = 0.0
    m_max: float = 0.0
    n_mmax: float = 0.0
    fehlende_kanaele: List[str] = field(default_factory=list)   # nicht in allen Laeufen vorhanden
    warnungen: List[str] = field(default_factory=list)
    ka: float = 0.0                                # wie RunResult (db.add_run), beim Mittelwert ohne Bedeutung

    @property
    def kanaele(self) -> List[str]:
        return list(self.werte)

    def kurven(self) -> Dict[str, np.ndarray]:
        """{"n": Raster, Kanal: Mittelwert, ...} wie ein Lauf aus kurven_aus_ergebnis."""
        return {"n": self.n, **self.werte}

    def band(self, kanal: str, art: str = "minmax") -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """(unten, oben) fuer das Band: "minmax" (Standard), "sigma" (Mittel ± σ) oder "" (kein Band -> None)."""
        if kanal not in self.werte or not art:
            return None
        if art == "sigma":
            return self.werte[kanal] - self.sigma[kanal], self.werte[kanal] + self.sigma[kanal]
        return self.minimum[kanal], self.maximum[kanal]


# ---------------------------------------------------------------------------------------------- Rechnung
def steigender_teil(lauf: Dict[str, Any], x: str = "n") -> Dict[str, np.ndarray]:
    """Nur der steigende Teil eines Laufs: NaN in x weg, bis zur hoechsten Drehzahl, davon nur Punkte ueber dem
    bisherigen Maximum (laufendes Maximum, Zacken nach unten und Wiederholungen fallen weg)."""
    if x not in lauf:
        raise ValueError(f"Lauf ohne Drehzahl („{x}“)")
    xs = np.asarray(lauf[x], float)
    ok = np.isfinite(xs)
    idx = np.nonzero(ok)[0]
    if len(idx) == 0:
        return {k: np.zeros(0) for k in lauf}
    xs_ok = xs[idx]
    j = int(np.argmax(xs_ok))
    idx, xs_ok = idx[:j + 1], xs_ok[:j + 1]
    run_max = np.maximum.accumulate(xs_ok)
    keep = np.r_[True, xs_ok[1:] > run_max[:-1]]
    idx = idx[keep]
    out = {}
    for k, v in lauf.items():
        v = np.asarray(v, float)
        if v.shape != xs.shape:
            raise ValueError(f"Kanal „{k}“ hat {len(v)} statt {len(xs)} Werte")
        out[k] = v[idx]
    return out


def _raster(lo: float, hi: float, raster: float) -> np.ndarray:
    """Vielfache von raster in [lo, hi] (mit kleiner Toleranz gegen Rundung)."""
    k0 = math.ceil(lo / raster - 1e-9)
    k1 = math.floor(hi / raster + 1e-9)
    if k1 < k0:
        return np.zeros(0)
    return np.arange(k0, k1 + 1, dtype=float) * raster


def mitteln(laeufe: List[Dict[str, Any]], raster: float = RASTER, oben_weg: float = OBEN_WEG, x: str = "n",
            quellen: Optional[List[int]] = None, setups: Optional[List[Optional[dict]]] = None) -> MittelwertErgebnis:
    """Laeufe mitteln. Jeder Lauf: {x: Drehzahl, Kanal: Werte, ...} mit gleicher Laenge (z.B. kurven_aus_ergebnis).
    setups: je Lauf das Setup (db.lauf_setup) -> Warnung bei Abweichungen (mitteln geht trotzdem)."""
    if len(laeufe) < MIN_LAEUFE:
        raise ValueError(f"Für einen Mittelwert braucht es mindestens {MIN_LAEUFE} Läufe.")
    if not raster > 0:
        raise ValueError("Das Drehzahlraster muss größer als 0 sein.")
    oben_weg = max(0.0, float(oben_weg))
    teile = []
    for k, lauf in enumerate(laeufe, 1):
        t = steigender_teil(lauf, x)
        if len(t[x]) < 2:
            raise ValueError(f"Lauf {k} hat keinen steigenden Drehzahlbereich.")
        teile.append(t)
    lo = max(float(t[x][0]) for t in teile)
    hi = min(float(t[x][-1]) for t in teile)
    grid = _raster(lo, hi - oben_weg, float(raster))
    if len(grid) == 0:
        raise ValueError(f"Die Läufe haben keinen gemeinsamen Drehzahlbereich (gemeinsam {lo:.0f}–{hi:.0f} 1/min, "
                         f"oben fallen {oben_weg:.0f} 1/min weg).")
    # Kanaele in der Reihenfolge des ersten Auftretens
    alle = list(dict.fromkeys(k for t in teile for k in t if k != x))
    werte, mins, maxs, sig, fehlend = {}, {}, {}, {}, []
    for kanal in alle:
        kurven = []
        for t in teile:
            v = t.get(kanal)
            if v is None:
                break
            m = np.isfinite(v)
            if m.sum() < 2:
                break
            xs, vs = t[x][m], v[m]
            y = np.interp(grid, xs, vs)
            y[(grid < xs[0]) | (grid > xs[-1])] = np.nan     # nicht ueber die Daten des Kanals hinaus
            kurven.append(y)
        if len(kurven) != len(teile):
            fehlend.append(kanal)
            continue
        mat = np.vstack(kurven)
        werte[kanal] = mat.mean(axis=0)
        mins[kanal] = mat.min(axis=0)
        maxs[kanal] = mat.max(axis=0)
        sig[kanal] = mat.std(axis=0, ddof=1)
    res = MittelwertErgebnis(n=grid, werte=werte, minimum=mins, maximum=maxs, sigma=sig, n_lo=lo, n_hi=hi,
                             raster=float(raster), oben_weg=oben_weg, anzahl=len(laeufe),
                             quellen=[int(i) for i in quellen or []], fehlende_kanaele=fehlend)
    _maxima(res)
    if fehlend:
        res.warnungen.append("Nicht in allen Läufen vorhanden, nicht gemittelt: " + ", ".join(fehlend))
    if setups is not None:
        w = setup_warnung(setups)
        if w:
            res.warnungen.insert(0, w)
    return res


def _maxima(res: MittelwertErgebnis):
    for kanal, attr_w, attr_n in (("ps", "p_max", "n_pmax"), ("nm", "m_max", "n_mmax")):
        v = res.werte.get(kanal)
        if v is not None and np.isfinite(v).any():
            i = int(np.nanargmax(v))
            setattr(res, attr_w, float(v[i]))
            setattr(res, attr_n, float(res.n[i]))


# ---------------------------------------------------------------------------------------------- Setups
def setup_abweichungen(setups: List[Optional[dict]], felder: Optional[List[str]] = None) -> Dict[str, List[str]]:
    """Felder, in denen sich die Setups der Quelllaeufe unterscheiden: {feld: [wert je Lauf]}.
    Ohne Setup (None) steht "?" und zaehlt nicht als Abweichung; fehlende Felder gelten als "".
    felder None = alle Setup-Felder (Reihenfolge wie SETUP_FIELDS, unbekannte dahinter)."""
    bekannt = [s for s in setups if s is not None]
    if felder is None:
        keys = [k for s in bekannt for k in s if k not in SETUP_IGNORIEREN]
        order = {k: i for i, k in enumerate(_db.all_setup_keys())}
        felder = sorted(dict.fromkeys(keys), key=lambda k: (order.get(k, len(order)), keys.index(k)))
    out = {}
    for f in felder:
        if f in SETUP_IGNORIEREN:
            continue
        vals = [_db.wert_text(s.get(f)) for s in bekannt]
        if len(set(vals)) > 1:
            out[f] = ["?" if s is None else _db.wert_text(s.get(f)) for s in setups]
    return out


def setup_warnung(setups: List[Optional[dict]], felder: Optional[List[str]] = None, max_felder: int = 3) -> str:
    """"Setups weichen ab: Zündzeitpunkt [° v. OT] 18 / 17" ("" ohne Abweichung)."""
    ab = setup_abweichungen(setups, felder)
    if not ab:
        return ""
    teile = [f"{_db.field_label(f)} " + " / ".join(v or "–" for v in vals) for f, vals in list(ab.items())[:max_felder]]
    rest = len(ab) - max_felder
    return "Setups weichen ab: " + "; ".join(teile) + (f" (und {rest} weitere)" if rest > 0 else "")


# ---------------------------------------------------------------------------------------------- Adapter
def kurven_aus_ergebnis(res, run: Optional[Dict[str, Any]] = None, ecu_praefix: str = ECU_PREFIX
                        ) -> Dict[str, np.ndarray]:
    """Kurven eines ausgewerteten Laufs (physics.RunResult) fuer mitteln():
    n, ps, nm, lam (aus AFR wie channels.lambda_from_afr), afr, egt1 (<= 0 -> NaN), ps_rad/nm_rad (mit Verlusten).
    Mit run (storage.load_any): zusaetzlich egt2 und die ECU-Kanaele (ecu_praefix + rusEFI-Name), auf den
    Kurvenbereich res.segment geschnitten."""
    from .channels import lambda_from_afr
    from .rusefi import COLUMN_PREFIX
    n = np.asarray(res.n, float)
    afr = np.asarray(res.afr, float)
    with np.errstate(invalid="ignore"):
        out = {"n": n, "ps": np.asarray(res.ps, float), "nm": np.asarray(res.nm, float),
               "lam": lambda_from_afr(afr), "afr": np.where(afr > 0.5, afr, np.nan)}
        egt = np.asarray(res.egt, float)
        out["egt1"] = np.where(egt > 0, egt, np.nan)
    if getattr(res, "verluste", False) and res.ps_rad is not None and len(res.ps_rad) == len(n):
        out["ps_rad"] = np.asarray(res.ps_rad, float)
        out["nm_rad"] = np.asarray(res.nm_rad, float)
    cols = (run or {}).get("columns") or {}
    seg = res.segment
    for name, v in cols.items():
        v = np.asarray(v, float)[seg]
        if len(v) != len(n):
            continue
        if name == "egt2_c":
            with np.errstate(invalid="ignore"):
                out["egt2"] = np.where(v > 0, v, np.nan)
        elif name.startswith(COLUMN_PREFIX):
            out[ecu_praefix + name[len(COLUMN_PREFIX):]] = v
    return out


# ---------------------------------------------------------------------------------------------- Speichern
def _fmt(x: float) -> str:
    return "nan" if not np.isfinite(x) else f"{x:.6g}"


def schreiben(ordner: str, res: MittelwertErgebnis, name: str = "") -> str:
    """mittelwert.json und mittelwert.csv in ordner schreiben (wird angelegt). Liefert den Pfad der JSON-Datei."""
    os.makedirs(ordner, exist_ok=True)
    kan = res.kanaele
    with open(os.path.join(ordner, CSV_NAME), "w", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["n"] + [f"{k}{s}" for k in kan for s in ("", "_min", "_max", "_sigma")])
        for i, nn in enumerate(res.n):
            row = [_fmt(nn)]
            for k in kan:
                row += [_fmt(res.werte[k][i]), _fmt(res.minimum[k][i]), _fmt(res.maximum[k][i]),
                        _fmt(res.sigma[k][i])]
            w.writerow(row)
    meta = {
        "format": FORMAT, "version": VERSION, "programm": f"PyST {__version__}",
        "datum": _dt.datetime.now().isoformat(timespec="seconds"), "name": name,
        "quellen": [int(i) for i in res.quellen], "anzahl": res.anzahl,
        "raster": res.raster, "oben_weg": res.oben_weg, "n_lo": res.n_lo, "n_hi": res.n_hi,
        "kanaele": kan, "fehlende_kanaele": list(res.fehlende_kanaele), "warnungen": list(res.warnungen),
        "ergebnis": {"p_max_ps": res.p_max, "n_pmax": res.n_pmax, "m_max_nm": res.m_max, "n_mmax": res.n_mmax},
    }
    path = os.path.join(ordner, JSON_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
    return path


def speichern(db, ergebnis: MittelwertErgebnis, name: str, vehicle_id: Optional[int] = None,
              setup_id: Optional[int] = None, notiz: str = "", setup_werte: Optional[List[dict]] = None,
              farbe: str = "", sichtbar: bool = False) -> int:
    """Mittelwert als Lauf eintragen (art="mittelwert", quelle="Mittelwert", datei="mittelwert.json"); Ordner
    unter db.runs_dir(). fehler={}: die Fehler-Nachberechnung ueberspringt ihn. Liefert die Lauf-ID."""
    stamp = _dt.datetime.now().strftime("%y%m%d_%H%M%S")
    target = db._target(f"{stamp}_Mittelwert")
    schreiben(target, ergebnis, name)
    rid = db.add_run(target, JSON_NAME, _db.QUELLE_MITTELWERT, name, db._now(), ergebnis, params={},
                     rate=0.0, vehicle_id=vehicle_id, setup_id=setup_id, notiz=notiz, farbe=farbe,
                     sichtbar=sichtbar, setup_werte=setup_werte or [], art=_db.ART_MITTELWERT,
                     quellen=ergebnis.quellen)
    db.update_run(rid, fehler={})
    return rid


def laden(pfad_oder_row: Union[str, Any]) -> MittelwertErgebnis:
    """Mittelwert laden: Ordner, mittelwert.json/.csv oder db.RunRow."""
    p = pfad_oder_row.path if hasattr(pfad_oder_row, "ordner") else str(pfad_oder_row)
    ordner = p if os.path.isdir(p) else os.path.dirname(os.path.abspath(p))
    if not os.path.exists(os.path.join(ordner, JSON_NAME)):
        raise ValueError(f"{ordner}: kein Mittelwert-Lauf ({JSON_NAME} fehlt)")
    with open(os.path.join(ordner, JSON_NAME), encoding="utf-8") as fh:
        meta = json.load(fh)
    if meta.get("format") != FORMAT:
        raise ValueError(f"{ordner}: kein Mittelwert-Lauf")
    with open(os.path.join(ordner, CSV_NAME), newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    head = rows[0] if rows else ["n"]
    data = np.array([[float(v) for v in r] for r in rows[1:] if r], float).reshape(-1, len(head))
    col = {h: data[:, i] for i, h in enumerate(head)}
    kan = meta.get("kanaele") or [h for h in head if h != "n" and not h.endswith(("_min", "_max", "_sigma"))]
    erg = meta.get("ergebnis") or {}
    return MittelwertErgebnis(
        n=col.get("n", np.zeros(0)),
        werte={k: col[k] for k in kan}, minimum={k: col[k + "_min"] for k in kan},
        maximum={k: col[k + "_max"] for k in kan}, sigma={k: col[k + "_sigma"] for k in kan},
        n_lo=float(meta.get("n_lo", 0.0)), n_hi=float(meta.get("n_hi", 0.0)),
        raster=float(meta.get("raster", RASTER)), oben_weg=float(meta.get("oben_weg", OBEN_WEG)),
        anzahl=int(meta.get("anzahl", 0)), quellen=[int(i) for i in meta.get("quellen", [])],
        p_max=float(erg.get("p_max_ps", 0.0)), n_pmax=float(erg.get("n_pmax", 0.0)),
        m_max=float(erg.get("m_max_nm", 0.0)), n_mmax=float(erg.get("n_mmax", 0.0)),
        fehlende_kanaele=list(meta.get("fehlende_kanaele", [])), warnungen=list(meta.get("warnungen", [])))
