"""
Ausrollversuch (Coast-down): Verlustmoment von Rolle, Rad, Kette und Getriebeausgang aus dem Auslaufen der Rolle.
Ohne Qt, von Oberflaeche, Ablauf (runner.py) und Tests gemeinsam genutzt.

Ablauf: Nach dem Lauf (Gas weg) zieht der Fahrer die Kupplung und laesst die Rolle ausrollen. Ohne Antrieb gilt
an der Rolle  J dw/dt = -Mv(n),  Mv = Verlustmoment [Nm] bei Rollendrehzahl n (physics.loss_torque):

    Mv(n) = M0 + n (M1 - M0) / n1 + m2 (n/1000) ((n - n1)/1000)

Auswertung (analyse):
  1. Ausrollphase erkennen: ab dem Laufende (bzw. dem Drehzahlmaximum danach) bis zur unteren Drehzahl, zum
     Stillstand, zum Wiederanstieg der Drehzahl oder zum Ende der Daten.
  2. Kupplung: mit Zuendsignal = erste Stelle, ab der die Zuenddrehzahl dauerhaft unter 85 % von Rolle x i faellt
     (der Motor dreht nicht mehr mit). Ohne Zuendsignal beginnt die Suche am Drehzahlmaximum. Danach wird immer
     UEBERGANG_MIN_S verworfen und anschliessend so lange weiter, bis die Verzoegerung stetig ist: der Anfang
     (Motorbremse, Schalten, Kupplung schleift) weicht sprunghaft vom Verlauf dahinter ab.
  3. Fit ohne numerische Ableitung: w(t) = w0 - 1/J * sum_k c_k * Integral phi_k(n(t)) dt  (lineare kleinste
     Quadrate ueber alle Messpunkte, die Integrale mitteln das Rauschen). phi = 1, n/n1 (, quadratischer Anteil).
  4. Messpunkte fuer die Anzeige und die Fit-Guete: lokale Steigung (Regressionsgerade +-FENSTER_S) -> Mv, nach
     Rollendrehzahl in Klassen gemittelt. R^2 und RMS [Nm] der Klassenmittel gegen den Fit.
Plausibilitaet: zu kurz, Drehzahlbereich zu klein, Rolle verzoegert nicht, Kupplung nicht erkennbar gezogen,
Verlustmoment negativ -> ok = False mit Meldung fuer den Anwender.

Grenzen: J ist die Rollentraegheit (wie in der Leistungsrechnung); die Traegheit von Rad und Getriebeausgang ist
nicht enthalten, ihr Anteil wird beim Beschleunigen wie beim Ausrollen gleich vernachlaessigt.
"""
import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import physics

RAD = physics.RAD_PER_RPM_S
MIN_DAUER_S = 3.0             # ausgewertete Ausrollphase mindestens
MIN_SPANNE = 0.2              # Drehzahlbereich mindestens 20 % der Anfangsdrehzahl ...
MIN_SPANNE_RPM = 100.0        # ... und 100 1/min an der Rolle
UEBERGANG_MIN_S = 0.3         # nach der Kupplung immer verworfen
SUCHE_MAX_S = 8.0             # so lange darf der Uebergang hoechstens dauern
FENSTER_S = 0.25              # Halbbreite der lokalen Steigung (Messpunkte)
KUPPLUNG_SCHLUPF = 0.85       # Zuenddrehzahl < 85 % von Rolle x i = Kupplung gezogen
N_STILL = 30.0                # Rollendrehzahl [1/min], darunter Stillstand
KLASSEN = 30                  # Messpunkte (Drehzahlklassen) fuer Anzeige und Fit-Guete


@dataclass
class AusrollErgebnis:
    ok: bool
    meldung: str = ""                         # Fehler fuer den Anwender (ok = False)
    hinweise: List[str] = field(default_factory=list)
    m0: float = 0.0                           # Verlustmoment an der Rolle bei n = 0 [Nm]
    m1: float = 0.0                           # ... bei n1 [Nm]
    n1: float = 1.0                           # Rollendrehzahl zu M1 [1/min]
    m2: float = 0.0                           # quadratischer Anteil [Nm/(1000/min)^2]
    quadratisch: bool = False
    r2: float = float("nan")                  # Bestimmtheitsmass der Klassenmittel
    rms_nm: float = float("nan")              # RMS der Klassenmittel gegen den Fit [Nm]
    n_von: float = 0.0                        # ausgewerteter Bereich, Rollendrehzahl [1/min]
    n_bis: float = 0.0
    dauer_s: float = 0.0
    i_von: int = 0                            # Indizes in den Rohdaten (i_bis exklusiv)
    i_bis: int = 0
    verworfen_s: float = 0.0                  # vom Drehzahlmaximum bis zum Beginn der Auswertung
    kupplung: str = ""                        # "Zündsignal" oder "Verzögerung"
    ratio: float = 1.0                        # Uebersetzung (Anzeige ueber Motordrehzahl)
    punkte_n: np.ndarray = field(default_factory=lambda: np.zeros(0))   # Klassenmittel Rollendrehzahl
    punkte_m: np.ndarray = field(default_factory=lambda: np.zeros(0))   # Klassenmittel Verlustmoment [Nm]
    datum: str = ""

    def mv(self, n_roll) -> np.ndarray:
        return physics.loss_torque(np.asarray(n_roll, float), self.m0, self.m1, self.n1, self.m2)

    def quelle(self) -> str:
        """Text fuer DynoParams.verlust_quelle, Ergebnis und Bericht."""
        try:
            d = _dt.datetime.fromisoformat(self.datum).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            d = self.datum
        r2 = f", R² {self.r2:.3f}".replace(".", ",") if np.isfinite(self.r2) else ""
        return f"Ausrollversuch {d}{r2}"

    def params_fuer(self, p: physics.DynoParams) -> physics.DynoParams:
        """Kopie von p mit den Verlusten dieses Ausrollversuchs."""
        q = physics.DynoParams(**p.__dict__)
        q.m0, q.m1, q.n1, q.m2 = self.m0, self.m1, self.n1, self.m2
        q.verlust_quelle = self.quelle()
        return q

    def als_dict(self, punkte: bool = False) -> Dict:
        """Fuer einstellungen.json und lauf.json; punkte=True mit den Messpunkten (Klassenmittel)."""
        d = {"ok": self.ok, "meldung": self.meldung, "hinweise": list(self.hinweise), "m0": self.m0,
                "m1": self.m1, "n1": self.n1, "m2": self.m2, "quadratisch": self.quadratisch,
                "r2": _num(self.r2), "rms_nm": _num(self.rms_nm), "n_von": self.n_von, "n_bis": self.n_bis,
                "dauer_s": self.dauer_s, "verworfen_s": self.verworfen_s, "kupplung": self.kupplung,
                "datum": self.datum, "quelle": self.quelle() if self.ok else "", "ratio": self.ratio}
        if punkte:
            d["punkte_n"] = [round(float(x), 2) for x in self.punkte_n]
            d["punkte_m"] = [round(float(x), 4) for x in self.punkte_m]
        return d

    @classmethod
    def aus_dict(cls, d: Dict) -> "AusrollErgebnis":
        """Gegenstueck zu als_dict (unbekannte Schluessel werden uebergangen)."""
        r = cls(ok=bool(d.get("ok")))
        for k in ("meldung", "m0", "m1", "n1", "m2", "quadratisch", "n_von", "n_bis", "dauer_s", "verworfen_s",
                  "kupplung", "datum", "ratio"):
            if d.get(k) is not None:
                setattr(r, k, type(getattr(r, k))(d[k]))
        r.hinweise = list(d.get("hinweise") or [])
        r.r2 = float(d["r2"]) if d.get("r2") is not None else float("nan")
        r.rms_nm = float(d["rms_nm"]) if d.get("rms_nm") is not None else float("nan")
        r.punkte_n = np.asarray(d.get("punkte_n") or [], float)
        r.punkte_m = np.asarray(d.get("punkte_m") or [], float)
        return r


def _num(x: float):
    return float(x) if np.isfinite(x) else None


# ---------------------------------------------------------------------------------------------- Hilfen
def zeitachse(dt) -> np.ndarray:
    """Zeit der Telegramme wie channels.run_channels: t[0] = 0, danach aufsummierte dT."""
    dt = np.asarray(dt, float)
    return np.concatenate([[0.0], np.cumsum(dt[1:])]) if len(dt) else dt


def glaetten(y: np.ndarray, half: int) -> np.ndarray:
    """Zentrierter gleitender Mittelwert, am Rand mit verkuerztem Fenster."""
    y = np.asarray(y, float)
    n = len(y)
    if half <= 0 or n == 0:
        return y.copy()
    c = np.concatenate([[0.0], np.cumsum(y)])
    j = np.arange(n)
    a, b = np.maximum(0, j - half), np.minimum(n, j + half + 1)
    return (c[b] - c[a]) / (b - a)


def lokale_steigung(t: np.ndarray, y: np.ndarray, half: int) -> np.ndarray:
    """Steigung der Regressionsgeraden durch y(t) im Fenster +-half Messpunkte (am Rand verkuerzt).
    Viel ruhiger als ein Differenzenquotient, auch bei ungleichmaessigen Zeitabstaenden."""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    n = len(y)
    if n < 2:
        return np.zeros(n)
    t = t - t.mean()
    cs = lambda x: np.concatenate([[0.0], np.cumsum(x)])
    s1, st, sy, stt, sty = cs(np.ones(n)), cs(t), cs(y), cs(t * t), cs(t * y)
    j = np.arange(n)
    a, b = np.maximum(0, j - max(1, half)), np.minimum(n, j + max(1, half) + 1)
    S1, St, Sy, Stt, Sty = (x[b] - x[a] for x in (s1, st, sy, stt, sty))
    den = S1 * Stt - St * St
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, (S1 * Sty - St * Sy) / den, 0.0)


def _basis(n: np.ndarray, n1: float, quad: bool) -> np.ndarray:
    """Spalten 1, n/n1 (, n/1000 (n - n1)/1000): Koeffizienten c -> M0 = c0, M1 = c0 + c1, m2 = c2."""
    cols = [np.ones_like(n), n / n1]
    if quad:
        cols.append((n / 1000.0) * ((n - n1) / 1000.0))
    return np.column_stack(cols)


def _koeff(c: np.ndarray) -> Tuple[float, float, float]:
    return float(c[0]), float(c[0] + c[1]), float(c[2]) if len(c) > 2 else 0.0


def fit_integral(t: np.ndarray, w: np.ndarray, n_s: np.ndarray, inertia: float, n1: float, quad: bool,
                 nutzen: slice = slice(None)) -> Tuple[np.ndarray, np.ndarray, float]:
    """Kleinste Quadrate auf  w(t) = w0 - 1/J * sum c_k Int_t0^t phi_k(n) dt  (Integrale ab dem ersten Punkt).
    nutzen: nur diese Zeilen fitten, das Modell gilt trotzdem fuer alle. Liefert (c, w_Modell, RMS [rad/s])."""
    phi = _basis(n_s, n1, quad)
    dt = np.diff(t)[:, None]
    integ = np.vstack([np.zeros((1, phi.shape[1])), np.cumsum(0.5 * (phi[1:] + phi[:-1]) * dt, axis=0)])
    a = np.column_stack([np.ones(len(t)), -integ / inertia])
    scale = np.linalg.norm(a[nutzen], axis=0)
    scale[scale == 0] = 1.0
    sol, *_ = np.linalg.lstsq(a[nutzen] / scale, w[nutzen], rcond=None)
    sol = sol / scale
    pred = a @ sol
    return sol[1:], pred, float(np.sqrt(np.mean((w[nutzen] - pred[nutzen]) ** 2)))


def _mv_c(n: np.ndarray, c: np.ndarray, n1: float) -> np.ndarray:
    return _basis(n, n1, len(c) > 2) @ c


def klassen(n: np.ndarray, m: np.ndarray, anzahl: int = KLASSEN, min_punkte: int = 3):
    """Messpunkte nach Drehzahl in gleich breite Klassen mitteln -> (n_mittel, m_mittel)."""
    if len(n) == 0:
        return np.zeros(0), np.zeros(0)
    edges = np.linspace(np.min(n), np.max(n), anzahl + 1)
    k = np.clip(np.searchsorted(edges, n, side="right") - 1, 0, anzahl - 1)
    cnt = np.bincount(k, minlength=anzahl)
    sn = np.bincount(k, weights=n, minlength=anzahl)
    sm = np.bincount(k, weights=m, minlength=anzahl)
    ok = cnt >= min_punkte
    return sn[ok] / cnt[ok], sm[ok] / cnt[ok]


# ---------------------------------------------------------------------------------------------- Erkennung
def erkennen(n_roll: np.ndarray, dt: np.ndarray, start: int = 0, n_meas: Optional[np.ndarray] = None,
             ratio: float = 1.0, n_ende_roll: float = 0.0) -> Dict:
    """Ausrollphase in den Rohdaten finden. Liefert {i0 (Maximum), ik (Kupplung), s, e, kupplung, fehler}."""
    n_roll = np.asarray(n_roll, float)
    N = len(n_roll)
    rate = physics.sample_rate(dt)
    out = {"i0": start, "ik": start, "s": start, "e": start, "kupplung": "", "fehler": ""}
    if rate <= 0 or N - start < 3:
        out["fehler"] = "Keine Ausrolldaten nach dem Lauf."
        return out
    n_s = glaetten(n_roll, int(round(0.1 * rate)))
    # Drehzahlmaximum kurz nach dem Laufende (das Laufende ist die erste fallende Stelle)
    look = min(N, start + max(3, int(round(2.0 * rate))))
    i0 = start + int(np.argmax(n_s[start:look]))
    # Ende: untere Drehzahl/Stillstand, Wiederanstieg der Drehzahl, Ende der Daten
    lo = max(n_ende_roll, N_STILL)
    seg = n_s[i0:]
    under = np.nonzero(seg < lo)[0]
    e = i0 + (int(under[0]) if len(under) else len(seg))
    runmin = np.minimum.accumulate(n_s[i0:e]) if e > i0 else np.zeros(0)
    up = np.nonzero(n_s[i0:e] > runmin + np.maximum(5.0, 0.01 * runmin))[0]
    if len(up):
        e = i0 + int(up[0])
        out["wieder"] = True
    out.update(i0=i0, e=e)
    # Kupplung ueber das Zuendsignal: Motor dreht langsamer als Rolle x i
    ik = i0
    ign_ok = False
    if n_meas is not None and len(n_meas) == N and ratio > 0:
        nm = physics.median_centered(np.asarray(n_meas, float), int(round(0.1 * rate)))
        vor = slice(max(0, i0 - int(rate)), i0 + 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            q = nm[vor] / (n_s[vor] * ratio)
        q = q[np.isfinite(q)]
        ign_ok = len(q) > 3 and 0.8 < float(np.median(q)) < 1.25       # Zuendsignal passt zur Rolle
        if ign_ok:
            frei = nm[i0:e] < KUPPLUNG_SCHLUPF * n_s[i0:e] * ratio
            halt = max(1, int(round(0.3 * rate)))
            # erste Stelle, ab der die Kupplung mindestens 0,3 s lang offen bleibt
            c = np.concatenate([[0], np.cumsum(frei)])
            j = np.arange(len(frei) - halt + 1) if len(frei) >= halt else np.zeros(0, int)
            dauer = c[j + halt] - c[j]
            hit = np.nonzero(dauer >= halt)[0]
            if not len(hit):
                out["fehler"] = ("Kupplung nicht gezogen erkennbar: Die Zünddrehzahl folgt der Rolle, der Motor "
                                 "dreht mit. Nach dem Gas-Wegnehmen Kupplung ziehen und halten.")
                out.update(ik=i0, s=i0, kupplung="Zündsignal")
                return out
            ik = i0 + int(hit[0])
    out["kupplung"] = "Zündsignal" if ign_ok else "Verzögerung"
    out["ik"] = ik
    out["s"] = min(e, ik + int(round(UEBERGANG_MIN_S * rate)))
    return out


# ---------------------------------------------------------------------------------------------- Auswertung
def analyse(n_roll, dt, p: physics.DynoParams, start: int = 0, n_meas=None, n_ende: float = 0.0,
            quadratisch: bool = True, n1: Optional[float] = None, datum: str = "") -> AusrollErgebnis:
    """Ausrollversuch auswerten. start = Laufende (Index in den Rohdaten), n_ende = untere Motordrehzahl
    (Rolle x i, 0 = bis Stillstand), n1 = Bezugsdrehzahl der Rolle fuer M1 (None: p.n1, wenn > 100, sonst
    obere Drehzahl der Ausrollphase auf 100 gerundet)."""
    n_roll = np.asarray(n_roll, float)
    dt = np.asarray(dt, float)
    datum = datum or _dt.datetime.now().isoformat(timespec="seconds")
    res = AusrollErgebnis(ok=False, quadratisch=quadratisch, ratio=p.ratio, datum=datum)
    N = len(n_roll)
    rate = physics.sample_rate(dt)
    if N < 3 or len(dt) != N or rate <= 0 or start is None or start < 0 or N - start < max(3, int(rate)):
        res.meldung = ("Keine Ausrolldaten nach dem Lauf: Die Aufzeichnung endete mit dem Lauf. Ausrollversuch "
                       "einschalten und nach dem Lauf die Kupplung ziehen, bis die Rolle ausgerollt ist.")
        return res
    t = zeitachse(dt)
    ratio = p.ratio if p.ratio > 0 else 1.0
    er = erkennen(n_roll, dt, start, n_meas, ratio, n_ende / ratio if n_ende > 0 else 0.0)
    res.kupplung = er["kupplung"]
    if er["fehler"]:
        res.meldung = er["fehler"]
        return res
    i0, s, e = er["i0"], er["s"], er["e"]
    s0 = s
    w = n_roll * RAD
    n_s = glaetten(n_roll, int(round(0.1 * rate)))
    half = max(2, int(round(FENSTER_S * rate)))
    d = -p.inertia * lokale_steigung(t, w, half)                # Verlustmoment je Messpunkt [Nm]

    def zu_kurz(a, b) -> str:
        if b - a < 3 or t[b - 1] - t[a] < MIN_DAUER_S:
            dauer = t[b - 1] - t[a] if b - a >= 2 else 0.0
            return (f"Ausrollphase zu kurz ({dauer:.1f} s, mindestens {MIN_DAUER_S:.0f} s): Kupplung gezogen "
                    f"halten, bis die Rolle ausgerollt ist.").replace(".", ",", 1)
        return ""

    beschleunigt = ("Die Rolle verzögert nicht, sie beschleunigt: Gas noch offen oder Kupplung nicht "
                    "gezogen? Ausrollen ohne Antrieb wiederholen.")
    if er.get("wieder") and e - i0 < MIN_DAUER_S * rate:     # Drehzahl steigt gleich nach dem Laufende wieder
        res.meldung = beschleunigt
        return res
    msg = zu_kurz(s, e)
    if msg:
        res.meldung = msg
        return res
    if np.mean(d[s:e]) <= 0 or n_s[e - 1] >= n_s[s]:
        res.meldung = beschleunigt
        return res

    # Uebergang am Anfang (Motorbremse, Kupplung schleift) und Bremsen am Ende verwerfen, bis die Verzoegerung
    # stetig ist: Das Modell aus den Daten dahinter (bzw. davor) muss w(t) im Randfenster W treffen. Das Pruefen
    # an w(t) statt an der Verzoegerung ist viel empfindlicher; quadratisches Modell, damit eine Kruemmung nicht
    # als Uebergang zaehlt.
    n1_auto = float(max(100.0, round(n_s[s] / 100.0) * 100.0))
    n1_fit = float(n1) if n1 and n1 > 0 else (p.n1 if p.n1 > 100 else n1_auto)
    W = max(5, int(round(0.5 * rate)))
    s_max = s0 + int(round(SUCHE_MAX_S * rate))
    e_min = s0 + (e - s0) // 2
    kopf_ok = False
    for _ in range(1000):
        if e - s < 4 * W:
            break
        _c, pred, rms = fit_integral(t[s:e], w[s:e], n_s[s:e], p.inertia, n1_fit, True, slice(W, None))
        thr = 3.0 * rms / np.sqrt(W) + 1e-9
        if abs(float(np.mean(w[s:s + W] - pred[:W]))) > thr:
            if s + W // 2 > s_max:
                break
            s += W // 2
            continue
        kopf_ok = True
        _c, pred, rms = fit_integral(t[s:e], w[s:e], n_s[s:e], p.inertia, n1_fit, True, slice(0, e - s - W))
        thr = 3.0 * rms / np.sqrt(W) + 1e-9
        if abs(float(np.mean(w[e - W:e] - pred[-W:]))) > thr and e - W // 2 > e_min:
            e -= W // 2
            continue
        break
    if not kopf_ok and e - s >= 4 * W:
        res.meldung = ("Der Übergang nach dem Gas-Wegnehmen klingt nicht ab (Motorbremse?): Kupplung nicht "
                       "gezogen erkennbar. Kupplung gleich nach dem Lauf ziehen und gezogen halten.")
        return res
    msg = zu_kurz(s, e)
    if msg:
        res.meldung = msg
        return res
    n_von, n_bis = float(n_s[s]), float(n_s[e - 1])
    if n_von - n_bis < max(MIN_SPANNE * n_von, MIN_SPANNE_RPM):
        res.meldung = (f"Drehzahlbereich zu klein (Rolle {n_bis:.0f}–{n_von:.0f} 1/min, Motor "
                       f"{n_bis * ratio:.0f}–{n_von * ratio:.0f} 1/min): länger ausrollen lassen oder die untere "
                       f"Drehzahl kleiner einstellen.")
        return res

    # Fit mit dem gewaehlten Modell
    c, _pred, _rms = fit_integral(t[s:e], w[s:e], n_s[s:e], p.inertia, n1_fit, quadratisch)
    res.m0, res.m1, res.m2 = _koeff(c)
    res.n1 = n1_fit
    res.i_von, res.i_bis = int(s), int(e)
    res.n_von, res.n_bis = n_von, n_bis
    res.dauer_s = float(t[e - 1] - t[s])
    res.verworfen_s = float(t[s] - t[i0])
    pn, pm = klassen(n_s[s:e], d[s:e])
    res.punkte_n, res.punkte_m = pn, pm
    if len(pn) >= 3:
        fit = res.mv(pn)
        ss_res = float(np.sum((pm - fit) ** 2))
        ss_tot = float(np.sum((pm - np.mean(pm)) ** 2))
        res.rms_nm = float(np.sqrt(ss_res / len(pm)))
        res.r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    grid = np.linspace(n_bis, n_von, 50)
    if np.min(res.mv(grid)) <= 0:
        res.meldung = ("Verlustmoment wird negativ: Die Rolle beschleunigt in der Ausrollphase. Gas oder "
                       "Kupplung? Ausrollen ohne Antrieb wiederholen.")
        return res
    if er["kupplung"] == "Verzögerung":
        res.hinweise.append("Kein passendes Zündsignal: Kupplung über die Verzögerung erkannt.")
    if er.get("wieder"):
        res.hinweise.append("Drehzahl stieg am Ende wieder an: Auswertung davor beendet.")
    if np.isfinite(res.r2) and res.r2 < 0.9:
        res.hinweise.append(f"Fit-Güte gering (R² {res.r2:.2f}): Messung wiederholen.".replace(".", ",", 1))
    if n_bis * 1.0 > 0.5 * n_von:
        res.hinweise.append("Unterer Drehzahlbereich fehlt: M0 ist aus dem Fit hochgerechnet.")
    res.ok = True
    return res


def verlust_ps(n_roll, mv_nm) -> np.ndarray:
    """Verlustleistung [PS] aus Rollendrehzahl [1/min] und Verlustmoment an der Rolle [Nm]."""
    return physics.PS_PER_W * np.asarray(mv_nm, float) * np.asarray(n_roll, float) * RAD
