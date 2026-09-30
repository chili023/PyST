"""
Leistungsberechnung – Nachbildung von WildBugChilGru LabVIEW 3.2.1.

Aus den Blockdiagrammen rekonstruiert (WildBugChilGru.vi, Messschleife):

  nRolle[k] = 60 * f_Rolle / Inkr            Rollendrehzahl je Telegramm
  dT[k]     = 1 / Messfrequenz
  Rolle gefiltert: gleitender Mittelwert, Dreieck, Halbbreite floor(MA/2),
                   vorne MA-mal nRolle[1] aufgefuellt, danach die ersten MA entfernt
  dT gefiltert:    Rechteck, Halbbreite dq, vorne (2dq+1)-mal dT[1] aufgefuellt
  n    = nRolle_f * i                        Motordrehzahl (feste Uebersetzung)
  w    = n * 2*pi/60
  a[j] = (w[j+k] - w[j-k]) / (2 k dT_f[j]),  k = min(dq, j, N-1-j)
  M    = Ka * J / i^2 * a + Mv(nRolle_f) / i
  P    = w * M,  PS = 0.00135962 * P
  Ende:   n_min ("n vom Gas") ist nur die Schwelle fuer die Ende-Erkennung:
          erstes j mit a <= 0 (Drehzahl faellt), n > n_min, dq <= j <= N-1-dq
  Anfang: nichts wird bei n_min abgeschnitten. Entfernt wird nur der Vorlauf
          (LabVIEW-Kommentar "Beginn mit n(i)<nSchwelle & a(i)<0 wegschneiden"):
          von hinten die letzte Stelle mit a < 0 und n < n_min suchen, danach beginnt die Kurve
          = Beginn des Beschleunigens nach dem Halten

Erweiterung PyST: optional n_stop – der Lauf endet auch, sobald n >= n_stop.
"""
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

PS_PER_W = 0.00135962
RAD_PER_RPM_S = 0.10471975511965978   # 2*pi/60


def din70020(temp_c: float, p_mbar: float) -> float:
    """Korrekturfaktor DIN 70020 (absoluter Druck, ohne Dampfdruck), wie LabVIEW."""
    return (1013.0 / p_mbar) * ((temp_c + 273.15) / 293.15) ** 0.5


def loss_torque(n_roll: np.ndarray, m0: float, m1: float, n1: float) -> np.ndarray:
    """Verlustmoment der Rolle, linear: Mv = M0 + n * (M1 - M0) / n1."""
    n1 = max(n1, 1.0)
    return m0 + n_roll * (m1 - m0) / n1


def smoothing_coeffs(half_width: int, triangle: bool) -> np.ndarray:
    """LabVIEW 'Smoothing Filter Coefficients' (moving average), normiert."""
    k = np.arange(-half_width, half_width + 1)
    w = (half_width + 1 - np.abs(k)).astype(float) if triangle else np.ones(2 * half_width + 1)
    return w / w.sum()


def _fir(b: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Kausaler FIR-Filter y[n] = sum b[k] x[n-k] (LabVIEW IIR Filter mit a = [1])."""
    return np.convolve(x, b)[: len(x)]


def filter_roll(n_roll: np.ndarray, ma: int) -> np.ndarray:
    if len(n_roll) == 0:
        return n_roll.astype(float)
    pad_val = n_roll[1] if len(n_roll) > 1 else n_roll[0]
    x = np.concatenate([np.full(ma, pad_val), n_roll])
    return _fir(smoothing_coeffs(ma // 2, triangle=True), x)[ma:]


def filter_dt(dt: np.ndarray, dq: int) -> np.ndarray:
    if len(dt) == 0:
        return dt.astype(float)
    pad_val = dt[1] if len(dt) > 1 else dt[0]
    pad = 2 * dq + 1
    x = np.concatenate([np.full(pad, pad_val), dt])
    return _fir(smoothing_coeffs(dq, triangle=False), x)[pad:]


def median_centered(x: np.ndarray, half: int) -> np.ndarray:
    if half <= 0 or len(x) == 0:
        return x.copy()
    out = np.empty(len(x))
    for j in range(len(x)):
        out[j] = np.median(x[max(0, j - half): j + half + 1])
    return out


@dataclass
class DynoParams:
    # Standardwerte = Pruefstand aus den Laeufen vom 26.05.2026
    inkr: float = 100.0           # Rollengeber Inkremente pro Umdrehung
    roll_circ: float = 1.57       # Rollenumfang [m]
    inertia: float = 13.5         # Rollentraegheit [kgm^2]
    imp: float = 1.0              # Zuendimpulse pro Kurbelwellenumdrehung
    ratio: float = 6.85           # nKurbelwelle / nRolle (fest)
    ma: int = 35                  # Gleitender Mittelwert [Messpunkte] (wie LabVIEW)
    dq: int = 15                  # Differenzenquotient [Messpunkte] (wie LabVIEW)
    ma_s: float = 0.0             # > 0: Glaettung in Sekunden, MA = ma_s * Messfrequenz (ersetzt ma)
    dq_s: float = 0.0             # > 0: Differenz +- in Sekunden, dq = dq_s * Messfrequenz (ersetzt dq)
    n_min: float = 4000.0         # "n vom Gas": Start-/Endschwelle [1/min], ueber Haltedrehzahl!
    n_stop: float = 8000.0        # Lauf endet bei dieser Motordrehzahl (0 = aus, wie LabVIEW)
    m0: float = 0.0               # Verlustmoment bei n=0 [Nm]
    m1: float = 0.0               # Verlustmoment bei n1 [Nm]
    n1: float = 1.0               # Rollendrehzahl zu M1 [1/min]
    temp_c: float = 20.0
    p_mbar: float = 1013.0


@dataclass
class RunResult:
    n: np.ndarray                 # Motordrehzahl [1/min] (Kurve, beschnitten)
    ps: np.ndarray
    nm: np.ndarray
    n_meas: np.ndarray            # gemessene Motordrehzahl (Zuendung) [1/min]
    afr: np.ndarray
    egt: np.ndarray
    ka: float
    p_max: float = 0.0
    n_pmax: float = 0.0
    m_max: float = 0.0
    n_mmax: float = 0.0
    v_max: float = 0.0
    distance_m: float = 0.0
    end_index: int = -1           # Index Laufende in den Rohdaten (-1 = nicht gefunden)
    start_index: int = 0
    end_reason: str = ""
    warnings: List[str] = field(default_factory=list)
    # ungeschnittene Arrays ueber alle Telegramme (fuer den Auswerter)
    full_n: Optional[np.ndarray] = None       # Motordrehzahl gefiltert (Rolle x i)
    full_ps: Optional[np.ndarray] = None
    full_nm: Optional[np.ndarray] = None
    full_accel: Optional[np.ndarray] = None   # Winkelbeschleunigung Motor [rad/s^2]
    stop: int = 0                             # Ende des Kurvenbereichs (exklusiv)
    ma_used: int = 0                          # tatsaechlich verwendete Filter [Messpunkte]
    dq_used: int = 0

    @property
    def segment(self) -> slice:
        return slice(self.start_index, self.stop)

    def summary(self) -> str:
        return (f"{self.p_max:.1f} PS bei {self.n_pmax:.0f} 1/min   |   "
                f"{self.m_max:.1f} Nm bei {self.n_mmax:.0f} 1/min   |   vmax {self.v_max:.0f} km/h\n"
                f"DIN 70020 k = {self.ka:.3f}   |   {self.distance_m:.0f} m")


def sample_rate(dt) -> float:
    dt = np.asarray(dt, float)
    return 1.0 / float(np.mean(dt)) if len(dt) and np.mean(dt) > 0 else 0.0


def effective(p: DynoParams, rate: float) -> DynoParams:
    """Filter in Sekunden (ma_s/dq_s) fuer die Messfrequenz `rate` in Messpunkte aufloesen.
    Ohne Sekunden-Angabe bleiben ma/dq unveraendert (LabVIEW-Laeufe rechnen damit identisch)."""
    if (p.ma_s <= 0 and p.dq_s <= 0) or rate <= 0:
        return p
    q = DynoParams(**p.__dict__)
    if p.ma_s > 0:
        q.ma = int(max(1, round(p.ma_s * rate)))
    if p.dq_s > 0:
        q.dq = int(max(1, round(p.dq_s * rate)))
    q.ma_s = q.dq_s = 0.0
    return q


def filter_text(p: DynoParams, rate: float) -> str:
    """Kurzbeschreibung der Filter, z.B. 'MA 1,75 s / dq ±0,75 s (105/45 bei 60 Hz)'."""
    q = effective(p, rate)
    if p.ma_s > 0 or p.dq_s > 0:
        return (f"MA {q.ma / rate if rate > 0 else p.ma_s:.2f} s / dq ±{q.dq / rate if rate > 0 else p.dq_s:.2f} s "
                f"({q.ma}/{q.dq} bei {rate:.0f} Hz)")
    return f"MA {p.ma} / dq {p.dq} Messpunkte" + (f" bei {rate:.0f} Hz" if rate > 0 else "")


def filters_to_seconds(p: DynoParams, rate: float) -> DynoParams:
    """Messpunkt-Filter eines Laufs in Sekunden umrechnen (fuer "mit aktuellen Einstellungen")."""
    q = DynoParams(**p.__dict__)
    if rate > 0 and q.ma_s <= 0:
        q.ma_s = p.ma / rate
    if rate > 0 and q.dq_s <= 0:
        q.dq_s = p.dq / rate
    return q


def scale_filters(p: DynoParams, rate_from: float, rate_to: float) -> DynoParams:
    """Filter (in Messpunkten!) auf gleiche Glaettungs-ZEIT bei anderer Messfrequenz umrechnen.
    Beispiel: MA35/dq15 bei 20 Hz  ->  MA105/dq45 bei 60 Hz. Filter in Sekunden bleiben unveraendert."""
    if rate_from <= 0 or rate_to <= 0 or p.ma_s > 0 or p.dq_s > 0:
        return p
    f = rate_to / rate_from
    q = DynoParams(**p.__dict__)
    q.ma = int(min(255, max(1, round(p.ma * f))))
    q.dq = int(min(255, max(1, round(p.dq * f))))
    return q


def check_params(p: DynoParams) -> str:
    """Leerer String = in Ordnung, sonst Fehlermeldung fuer den Benutzer."""
    if p.n_stop and p.n_stop <= p.n_min:
        return (f"n Stop ({p.n_stop:.0f}) muss groesser als n vom Gas ({p.n_min:.0f}) sein – "
                f"dazwischen liegt die Messkurve.")
    if p.ratio <= 0 or p.inkr <= 0 or p.imp <= 0 or p.inertia <= 0:
        return "Uebersetzung, Inkremente, Zuendimpulse und Traegheit muessen > 0 sein."
    if p.ma_s < 0 or p.dq_s < 0 or (p.ma_s <= 0 and p.ma < 1) or (p.dq_s <= 0 and p.dq < 1):
        return "Filterwerte muessen > 0 sein."
    return ""


def evaluate(n_roll_raw, dt_raw, p: DynoParams, n_meas_raw=None, afr_raw=None, egt_raw=None,
             require_end: bool = True) -> Optional[RunResult]:
    """Wertet einen Lauf aus. Rohdaten wie im LabVIEW-XML (nRolle, dT-Array, nWelle, AFR, EGT).
    require_end=False: auch ohne erkanntes Laufende auswerten (Live-Anzeige)."""
    n_roll_raw = np.asarray(n_roll_raw, dtype=float)
    dt_raw = np.asarray(dt_raw, dtype=float)
    N = len(n_roll_raw)
    if N < 3 or len(dt_raw) != N:
        return None
    p = effective(p, sample_rate(dt_raw))          # Filter in Sekunden -> Messpunkte
    zeros = np.zeros(N)
    have_meas = n_meas_raw is not None
    n_meas_raw = np.asarray(n_meas_raw, dtype=float) if have_meas else zeros
    afr_raw = np.asarray(afr_raw, dtype=float) if afr_raw is not None else zeros
    egt_raw = np.asarray(egt_raw, dtype=float) if egt_raw is not None else zeros

    ka = din70020(p.temp_c, p.p_mbar)
    i = p.ratio
    dq = int(p.dq)
    n_roll_f = filter_roll(n_roll_raw, int(p.ma))
    dt_f = filter_dt(dt_raw, dq)
    n_e = n_roll_f * i
    w = n_e * RAD_PER_RPM_S

    idx = np.arange(N)
    k = np.minimum(np.minimum(dq, idx), N - 1 - idx)
    with np.errstate(divide="ignore", invalid="ignore"):
        a = (w[idx + k] - w[idx - k]) / (k * 2.0 * dt_f)
    mv = loss_torque(n_roll_f, p.m0, p.m1, p.n1)
    m = ka * p.inertia / i ** 2 * a + mv / i
    with np.errstate(invalid="ignore"):
        ps = PS_PER_W * w * m

    # Laufende wie LabVIEW (erste Verzoegerung oberhalb n_min) ...
    with np.errstate(invalid="ignore"):
        cond = (a <= 0) & (n_e > p.n_min) & (idx >= dq) & (idx <= N - 1 - dq)
    e = int(np.argmax(cond)) if cond.any() else -1
    reason = "Gas weg (Verzoegerung)" if e >= 0 else ""
    # ... oder frueher bei n_stop (PyST)
    if p.n_stop and p.n_stop > 0:
        hit = np.nonzero((n_e >= p.n_stop) & (idx >= dq))[0]
        if len(hit) and (e < 0 or hit[0] < e):
            e = int(hit[0])
            reason = f"n Stop {p.n_stop:.0f} 1/min erreicht"
    warnings = []
    if e < 0:
        if require_end:
            return None
        e = N
        reason = "noch kein Laufende"

    # Anfang: Vorlauf bis zur letzten Verzoegerung unterhalb n_min entfernen
    start = 1
    for j in range(e - 1, -1, -1):
        if a[j] < 0 and n_e[j] < p.n_min:
            start = j + 1
            break
    else:
        start = 1
    if start >= e:
        return None

    sl = slice(start, e)
    n_meas_f = median_centered(n_meas_raw, int(p.ma) // 2) if have_meas else zeros
    res = RunResult(n=n_e[sl], ps=ps[sl], nm=m[sl], n_meas=n_meas_f[sl], afr=afr_raw[sl], egt=egt_raw[sl],
                    ka=ka, end_index=e if reason != "noch kein Laufende" else -1, start_index=start,
                    end_reason=reason, warnings=warnings, full_n=n_e, full_ps=ps, full_nm=m, full_accel=a,
                    stop=e, ma_used=int(p.ma), dq_used=int(p.dq))
    finite = np.isfinite(res.ps)
    if finite.any():
        jp = int(np.nanargmax(np.where(finite, res.ps, -np.inf)))
        jm = int(np.nanargmax(np.where(np.isfinite(res.nm), res.nm, -np.inf)))
        res.p_max, res.n_pmax = float(res.ps[jp]), float(res.n[jp])
        res.m_max, res.n_mmax = float(res.nm[jm]), float(res.n[jm])
        res.v_max = float(np.max(res.n) / i * p.roll_circ * 0.06)
    res.distance_m = float(np.sum(n_roll_raw / 60.0 * p.roll_circ * dt_raw))
    return res


def trimmed_ratio(n_meas: np.ndarray, n_roll: np.ndarray, trim_pct: float = 20.0) -> Optional[float]:
    """Uebersetzung aus Zuendsignal wie LabVIEW: getrimmter Mittelwert von nZuend/nRolle,
    auf 3 Nachkommastellen gerundet."""
    n_meas = np.asarray(n_meas, float)
    n_roll = np.asarray(n_roll, float)
    ok = n_roll > 0
    r = np.sort(n_meas[ok] / n_roll[ok])
    if len(r) < 5:
        return None
    cut = int(len(r) * trim_pct / 100.0 / 2.0)
    r = r[cut: len(r) - cut] if len(r) - 2 * cut > 0 else r
    return round(float(np.mean(r)), 3)
