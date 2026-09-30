"""
Lesen und Schreiben der LabVIEW-Lauf-Dateien (Cluster "Datenspeicher", LVData-XML).

Lesen:   LabVIEW 3.x (Arduino) und STM-LabVIEW 0.2.0, auch Konfig.xml.
Schreiben: Layout von LabVIEW 3.2.1, damit PyST-Laeufe in LabVIEW-Recalc
           geoeffnet werden koennen (Datei-Kodierung Latin-1 wie LabVIEW).
"""
import datetime as _dt
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Tuple
from xml.sax.saxutils import escape

import numpy as np

from .physics import DynoParams


def _tag(e) -> str:
    return e.tag.split("}")[-1]


def _value(e) -> Any:
    tag = _tag(e)
    if tag == "Array":
        return np.array([float(c.findtext("{*}Val") or 0) for c in e
                         if _tag(c) not in ("Name", "Dimsize")][: int(e.findtext("{*}Dimsize") or 0)])
    if tag == "Cluster":
        return [(c.findtext("{*}Name"), _value(c)) for c in e if _tag(c) not in ("Name", "NumElts")]
    val = e.findtext("{*}Val") or ""
    if tag in ("DBL", "SGL", "EXT"):
        return float(val)
    if tag in ("U8", "U16", "U32", "I8", "I16", "I32", "EW", "EB", "EL"):
        return int(float(val or 0))
    if tag == "Boolean":
        return val == "1"
    return val


def read_fields(path: str) -> List[Tuple[str, Any]]:
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    if text.startswith("<?xml"):
        text = text[text.index("?>") + 2:]
    root = ET.fromstring(text)
    # STM-LabVIEW: <LVData><Cluster>…; LabVIEW 3.2.1: <LVData><LvVariant><Cluster>…
    clusters = [c for c in root.iter() if _tag(c) == "Cluster"]
    cluster = next((c for c in clusters if (c.findtext("{*}Name") or "") == "Datenspeicher"),
                   clusters[0] if clusters else None)
    if cluster is None:
        raise ValueError(f"{path}: kein LabVIEW-Datenspeicher gefunden")
    return [(c.findtext("{*}Name") or "", _value(c)) for c in cluster if _tag(c) not in ("Name", "NumElts")]


def read_run(path: str) -> Dict[str, Any]:
    """Liefert Parameter und Rohdaten eines LabVIEW-Laufs (Feldpositionen wie LabVIEW)."""
    f = read_fields(path)
    v = [x[1] for x in f]
    if len(v) < 31:
        raise ValueError(f"{path}: unbekanntes Format ({len(v)} Felder)")
    info = str(v[0])
    title = ""
    if "§$" in info:
        info, title = info.split("§$", 1)
    p = DynoParams(
        inkr=float(v[2]), roll_circ=float(v[3]), inertia=float(v[4]), imp=float(v[5]),
        ratio=float(v[30]), ma=int(v[9]), dq=int(v[10]), n_min=float(v[25]), n_stop=0.0,
        m0=float(v[23]), m1=float(v[24]), n1=float(v[15]), temp_c=float(v[14]), p_mbar=float(v[16]),
    )
    return {
        "params": p,
        "vehicle": info.strip(),
        "title": title.strip(),
        "date": str(v[1]).split("|")[0],
        "ratio_mode": int(v[21]),
        "gear": float(v[6]), "tire_circ": float(v[7]),
        "v_min": float(v[31]) if len(v) > 31 else 0.0,
        "n_roll": np.asarray(v[17], float),
        "n_meas": np.asarray(v[18], float),
        "afr": np.asarray(v[19], float),
        "egt": np.asarray(v[20], float),
        "dt": np.asarray(v[26], float),
    }


def _num(x: float) -> str:
    return f"{x:.14f}" if abs(x) >= 1e-3 or x == 0 else f"{x:.14E}"


def write_run(path: str, p: DynoParams, n_roll, n_meas, afr, egt, dt, vehicle: str = "",
              title: str = "", distance_m: float = 0.0, port: str = "COM1") -> None:
    """Schreibt einen Lauf im Datenspeicher-Layout von LabVIEW 3.2.1."""
    out: List[str] = []
    w = out.append

    def scalar(tag: str, name: str, val: str) -> None:
        w(f"<{tag}>\n<Name>{escape(name)}</Name>\n<Val>{val}</Val>\n</{tag}>")

    def array(name: str, data, elem_name: str = "") -> None:
        data = np.asarray(data, float)
        w(f"<Array>\n<Name>{escape(name)}</Name>\n<Dimsize>{len(data)}</Dimsize>")
        for x in data:
            w(f"<DBL>\n<Name>{elem_name}</Name>\n<Val>{_num(float(x))}</Val>\n</DBL>")
        w("</Array>")

    now = _dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    w("<?xml version='1.0' standalone='yes' ?>")
    w('<LVData xmlns="http://www.ni.com/LVData">\n<Version>14.0.1f11</Version>')
    w("<Cluster>\n<Name>Datenspeicher</Name>\n<NumElts>32</NumElts>")
    scalar("String", "Fahrzeugdaten", escape(f"{vehicle}§${title}"))
    scalar("String", "Auto-Pfad", f"{now}|")
    scalar("U16", "Rolle [Inkr/U]", str(int(round(p.inkr))))
    scalar("DBL", "Rollenumfang [m]", _num(p.roll_circ))
    scalar("DBL", "Rollenträgheit [kgm²]", _num(p.inertia))
    scalar("DBL", "Zündung [Imp/U]", _num(p.imp))
    scalar("DBL", "Getriebe [1:]", _num(0.0))
    scalar("DBL", "Reifenumfang [m] ", _num(0.0))
    scalar("DBL", "Grossanzeige", _num(0.0))
    scalar("U8", "Gl. Mittelwert", str(int(p.ma)))
    scalar("U8", "Differenzenquotient", str(int(p.dq)))
    scalar("U8", "AFR,EGT", "0")
    scalar("DBL", "dT [s]", _num(float(np.mean(dt)) if len(dt) else 0.0))
    scalar("DBL", "Strecke [m]", _num(distance_m))
    scalar("DBL", "Lufttemperatur [°C]", _num(p.temp_c))
    scalar("DBL", "Mv: n1 [1/min]", _num(p.n1))
    scalar("DBL", "Luftdruck [mbar]", _num(p.p_mbar))
    array("nRolle", n_roll)
    array("nWelle", n_meas)
    array("AFR", afr)
    array("EGT", egt)
    scalar("U8", "Übersetzung ermitteln aus", "2")          # 2 = nKuWe/nRolle direkt
    scalar("DBL", "[s]", _num(5.0))
    scalar("DBL", "Mv: M0 [Nm]", _num(p.m0))
    scalar("DBL", "Mv: M1 [Nm]", _num(p.m1))
    scalar("DBL", "n vom Gas [1/min]", _num(p.n_min))
    array("dT-Array [s]", dt, "dT")
    w("<Array>\n<Name></Name>\n<Dimsize>0</Dimsize>\n<I32>\n<Name></Name>\n<Val></Val>\n</I32>\n</Array>")
    scalar("Boolean", "Boolesch", "0")
    w(f"<Refnum>\n<Name>Serielle Schnittstelle</Name>\n<RefKind>VISA</RefKind>\n<Val>{escape(port)}</Val>\n</Refnum>")
    scalar("DBL", "nKuWe/nRolle", _num(p.ratio))
    scalar("DBL", "v vom Gas [km/h]", _num(40.0))
    w("</Cluster>\n</LVData>")
    with open(path, "wb") as fh:
        fh.write("\n".join(out).encode("latin-1", "replace"))
