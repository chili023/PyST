"""
Ablauf eines Laufs (von GUI und Kommandozeile gemeinsam genutzt).

  BEREIT --start()--> KLIMA ("Klima wird gemessen …", nur mit auto_climate) --> WARTEN ("GO – Vollgas!")
  WARTEN --Drehzahl steigt start_anstieg_s lang (anstieg_beginn)--> MESSUNG (ab dem Anfang des Anstiegsfensters)
  MESSUNG --Verzoegerung oberhalb n vom Gas oder n >= n_stop--> FERTIG (Auswertung wie LabVIEW)

Klima: start() fragt im Hintergrund eine frische Messung an ('e', die Firmware liest den BME/BMP280 dabei neu, die
Antwort ist also juenger als START). Antwortet nichts binnen klima_timeout_s, rechnet der Lauf mit den eingetragenen
Werten (klima_quelle sagt, woher T/p kommen). Ohne auto_climate entfaellt die Phase.
Laufbeginn: Waehrend WARTEN sammelt der Vorpuffer die Telegramme. Steigt die Motordrehzahl (Rolle x i) ueber ein
Fenster von start_anstieg_s robust und um mindestens start_anstieg_min (anstieg_beginn), beginnt die Messung am
Anfang dieses Fensters – die Daten des Fensters bleiben, alles davor (Halten) wird verworfen. „n vom Gas“ (n_min) ist
nur noch die Schwelle, ab der auf fallende Drehzahl (= Laufende) geprueft wird. Kommt nach GO binnen go_timeout_s
kein Anstieg, wird abgebrochen.

Ausrollversuch (ausrollen = True): MESSUNG --Laufende--> AUSROLLEN ("Kupplung ziehen, ausrollen lassen"), die
Aufzeichnung laeuft weiter, bis die Rolle unter n_ende (Motordrehzahl Rolle x i) faellt, die Drehzahl wieder
steigt, t_max vergeht, keine Telegramme mehr kommen oder end_coast() (Knopf). Dann FERTIG: Lauf wie bisher
ausgewertet, dazu coast = ausrollen.analyse(...) (Verlustmoment). Ausgeschaltet: Ablauf exakt wie bisher.

Uebersetzung aus den Funken (ratio_aus_funken = True, Einstellung „uebersetzung_art“ = "funken"): beim Laufende wird
die Uebersetzung aus Zuendung/Rolle des Laufs bestimmt (getrimmter Mittelwert wie „Übersetzung einmessen …“,
physics.trimmed_ratio; nur Messpunkte vor dem Ausrollen mit Rolle >= 30 % ihres Hoechstwerts) und der Lauf damit
ausgewertet (ratio_gemessen). Live-Kurve, Laufbeginn, „n vom Gas“ und n Stop rechnen bis dahin mit der eingestellten
Uebersetzung. Ohne brauchbares Zuendsignal bleibt die eingestellte Uebersetzung (ratio_gemessen = None).
"""
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from . import ausrollen as _ausrollen
from . import physics
from .link import DynoLink, Frame

IDLE, WAIT, RUN, DONE, ABORTED = "Bereit", "GO – Vollgas", "Messung", "Fertig", "Abgebrochen"
COAST = "Ausrollen"
KLIMA = "Klima wird gemessen"
ACTIVE = (KLIMA, WAIT, RUN, COAST)   # Lauf laeuft (Aufzeichnung, Bedienung gesperrt)
RUN_TIMEOUT_S = 120.0                # Messung (ab Laufbeginn) hoechstens [s]
GO_TIMEOUT_S = 60.0                  # nach GO so lange auf den Drehzahlanstieg warten [s]
KLIMA_TIMEOUT_S = 3.0                # so lange auf die Antwort des Klimasensors warten [s]
START_ANSTIEG_S = 1.0                # Laufbeginn: Drehzahl steigt so lange ... [s]
START_ANSTIEG_MIN = 150.0            # ... und um mindestens so viel (Motor) [1/min]
ANSTIEG_BLOECKE = 5                  # Teilstrecken des Fensters, jede muss steigen
ANSTIEG_ANTEIL = 0.25                # ... um mindestens diesen Anteil ihres gleichmaessigen Anteils am Anstieg
ANSTIEG_SIGNIFIKANZ = 5.0            # Steigung mindestens so viele Standardfehler (aus dem Rauschen im Fenster)
ANSTIEG_PUNKTE_MAX = 120             # mehr Punkte im Fenster werden fuer die Steigung ausgeduennt (Rechenzeit)


def anstieg_beginn(n, rate: float, fenster_s: float = START_ANSTIEG_S,
                   min_anstieg: float = START_ANSTIEG_MIN) -> Optional[int]:
    """Laufbeginn: steigt die Drehzahl n (Motor, je Telegramm) ueber die letzten fenster_s Sekunden? Liefert den Index
    des Fensteranfangs in n, sonst None. Robust gegen Messrauschen und einzelne Ausreisser, alle drei Bedingungen:
      1. Steigung nach Theil-Sen (Median aller Punktpaar-Steigungen) x Fensterlaenge >= min_anstieg,
      2. die Steigung liegt mindestens ANSTIEG_SIGNIFIKANZ Standardfehler ueber 0; das Rauschen kommt robust aus dem
         Fenster selbst (MAD der Reste um die Gerade bzw. der Punkt-zu-Punkt-Spruenge, das Groessere),
      3. „durchgehend“: das Fenster in ANSTIEG_BLOECKE Teilstrecken geteilt, steigt der Median jeder Teilstrecke
         gegenueber der vorigen um mindestens ANSTIEG_ANTEIL ihres Anteils am Anstieg – ein Fenster, das zur Haelfte
         noch im Halten liegt, loest also nicht aus, Rauschen allein auch nicht."""
    rate = float(rate) if rate and rate > 0 else 20.0
    N = max(6, int(round(fenster_s * rate)))
    if len(n) < N or fenster_s <= 0:
        return None
    w = np.asarray(n[-N:], dtype=float)
    if not np.all(np.isfinite(w)):
        return None
    t = np.arange(N) / rate
    step = max(1, int(np.ceil(N / ANSTIEG_PUNKTE_MAX)))
    ws, ts = w[::step], t[::step]
    i, j = np.triu_indices(len(ws), 1)
    s = float(np.median((ws[j] - ws[i]) / (ts[j] - ts[i])))      # Theil-Sen-Steigung [1/min pro s]
    anstieg = s * N / rate
    if anstieg < min_anstieg or s <= 0:
        return None
    rest = w - s * t
    sig = 1.4826 * float(np.median(np.abs(rest - np.median(rest))))
    d = np.diff(w)
    sig = max(sig, 1.4826 * float(np.median(np.abs(d - np.median(d)))) / np.sqrt(2.0), 1e-9)
    if s < ANSTIEG_SIGNIFIKANZ * sig / np.sqrt(np.sum((t - t.mean()) ** 2)):
        return None
    k = min(ANSTIEG_BLOECKE, N // 2)
    m = np.array([np.median(b) for b in np.array_split(w, k)])
    if not np.all(np.diff(m) > ANSTIEG_ANTEIL * anstieg / k):
        return None
    return len(n) - N
COAST_N_ENDE = 3000.0                # Ausrollen bis Motordrehzahl (Rolle x i) [1/min]
COAST_T_MAX = 120.0                  # Ausrollen hoechstens [s]
COAST_STILL_S = 2.0                  # so lange keine Telegramme = Ende der Daten


@dataclass
class Live:
    n_calc: float = 0.0      # Motordrehzahl aus Rolle x Uebersetzung [1/min]
    n_meas: float = 0.0      # Motordrehzahl aus Zuendung [1/min]
    n_roll: float = 0.0      # Rollendrehzahl [1/min]
    v_kmh: float = 0.0
    rate: float = 0.0
    egt1: float = 0.0
    afr: float = 0.0
    egt2: float = 0.0


class RunController:
    def __init__(self, link: DynoLink, params: physics.DynoParams, auto_climate: bool = True, ecu=None):
        self.link = link
        self.ecu = ecu                 # rusefi.RusefiLink oder None
        self.ecu_data = None           # Ausschnitt der ECU-Werte zum letzten Lauf (rusefi.RusefiLink.snapshot)
        self.params = params
        self.auto_climate = auto_climate
        self.state = IDLE
        self.live = Live()
        self.result: Optional[physics.RunResult] = None
        self.live_result: Optional[physics.RunResult] = None
        self.climate: Optional[tuple] = None
        self.frames: List[Frame] = []
        self.basis_events: list = []     # Ereignisse der Basis waehrend des Laufs (Marker der Zange …)
        self._t_start = 0.0
        self._since_eval = 0
        self._last_eval = 0.0
        self.message = ""
        # Ausrollversuch
        self.ausrollen = False
        self.coast_n_ende = COAST_N_ENDE
        self.coast_t_max = COAST_T_MAX
        self.coast_quadratisch = True
        self.coast: Optional[_ausrollen.AusrollErgebnis] = None
        self.coast_reason = ""
        self._coast_t0 = 0.0
        self._coast_last = 0.0
        self._coast_min = float("inf")
        self._coast_hist: List[float] = []
        self._coast_i = 0              # erstes Telegramm des Ausrollens (0 = kein Ausrollen)
        # Uebersetzung aus den Funken des Laufs
        self.ratio_aus_funken = False
        self.ratio_gemessen: Optional[float] = None
        # Klima vor dem Lauf (Phase KLIMA) und Laufbeginn (Drehzahlanstieg nach GO)
        self.klima_timeout_s = KLIMA_TIMEOUT_S
        self.go_timeout_s = GO_TIMEOUT_S
        self.klima_quelle = ""             # woher T/p des Laufs kommen (lauf.json, Ergebnis)
        self.klima_zeit = 0.0              # Zeitpunkt der Sensorantwort (0 = kein Sensorwert)
        self._klima_antwort: Optional[tuple] = None
        self._klima_fertig = threading.Event()
        self._t_go = 0.0
        self._t_run = 0.0
        self.run_start_t = 0.0             # PC-Zeit des Laufbeginns (Anfang des Anstiegsfensters)

    # ---------------------------------------------------------------- Steuerung
    def start(self):
        """START: mit auto_climate erst die Phase KLIMA (frische Messung im Hintergrund, poll() geht danach zu
        WARTEN), sonst gleich WARTEN („GO – Vollgas!“)."""
        self.result = self.live_result = None
        self.coast, self.coast_reason = None, ""
        self._coast_i = 0
        self.ratio_gemessen = None
        self.frames = []
        self.basis_events = []
        self.ecu_data = None
        self.climate = None
        self.klima_zeit = 0.0
        self.run_start_t = 0.0
        self._t_start = time.time()
        if self.auto_climate:
            self.message = "Klima wird gemessen …"
            self.klima_quelle = ""
            self._klima_antwort = None
            self._klima_fertig = fertig = threading.Event()
            self.state = KLIMA
            threading.Thread(target=self._klima_lesen, args=(fertig,), daemon=True, name="pyst-klima").start()
            return
        self.klima_quelle = "eingetragen (Sensor aus)"
        self.message = ""
        self._messung_starten()
        self._go()

    def _klima_lesen(self, fertig: threading.Event):
        """Im Hintergrund: frische Klimamessung anfordern ('e' liest den Sensor neu), danach die Messung starten."""
        try:
            try:
                c = self.link.climate(timeout=self.klima_timeout_s)
            except TypeError:                         # Gegenstelle ohne timeout (Tests)
                c = self.link.climate()
            if fertig is self._klima_fertig:
                self._klima_antwort = (c, time.time()) if c else None
                if self.state == KLIMA:
                    self._messung_starten()
        except Exception as exc:                      # Verbindung weg: poll() geht ohne Sensorwert weiter
            self._klima_antwort = None
            self.message = f"Klima nicht gelesen: {exc}"
        finally:
            fertig.set()

    def _messung_starten(self):
        dm = getattr(self.link, "default_mode", "N")
        if dm != "N" and self.link.mode != dm:
            self.link.set_mode(dm)                    # Basis mit Zange: Zuendstaerke mit aufzeichnen
        self.link.start()

    def _klima_uebernehmen(self):
        """Ende der Phase KLIMA: Sensorwert in die Rechnung, sonst mit den eingetragenen Werten weiter."""
        a = self._klima_antwort
        if a:
            self.climate, self.klima_zeit = a
            self.params.temp_c, self.params.p_mbar = self.climate[0], self.climate[1]
            feuchte = f", {self.climate[2]:.0f} %" if self.climate[2] > 0 else ""    # BMP280: keine Feuchte
            self.klima_quelle = "Sensor " + time.strftime("%H:%M:%S", time.localtime(self.klima_zeit))
            self.message = f"Klima: {self.climate[0]:.1f} °C, {self.climate[1]:.1f} mbar{feuchte} vom Sensor"
        else:
            self.climate = None
            self.klima_quelle = "eingetragen (kein Sensorwert)"
            self.message = (f"Klima: eingetragener Wert {self.params.temp_c:.1f} °C, {self.params.p_mbar:.1f} mbar "
                            "– kein Sensorwert")
        self._go()

    def _go(self):
        del self.frames[:]
        self._t_go = time.time()
        self.state = WAIT

    def klima_text(self) -> str:
        """Kurz fuer Ergebnis und Statuszeile: woher T/p des Laufs kommen ("" vor dem ersten Lauf)."""
        p = self.params
        if self.climate:
            return f"Klima: {p.temp_c:.1f} °C, {p.p_mbar:.0f} mbar vom Sensor ({self.klima_quelle[7:]})"
        if self.klima_quelle:
            grund = "Sensor aus" if "Sensor aus" in self.klima_quelle else "kein Sensorwert"
            return f"Klima: eingetragener Wert {p.temp_c:.1f} °C, {p.p_mbar:.0f} mbar ({grund})"
        return ""

    def add_event(self, ev):
        """Ereignis der Basis (basis.BasisEvent) – gehoert zum Lauf, solange er laeuft."""
        if self.state in ACTIVE:
            self.basis_events.append(ev)

    def abort(self):
        if self.state == COAST:                      # Lauf ist schon fertig: Ausrollen beenden, nichts verwerfen
            self.end_coast("Ausrollen von Hand beendet")
            return
        if self.state in (KLIMA, WAIT, RUN):
            self.state = ABORTED
            self.message = "Lauf abgebrochen"
        self.link.stop()

    def end_coast(self, reason: str = "Ausrollen von Hand beendet"):
        if self.state == COAST:
            self.coast_reason = reason
            self.finish()

    # ---------------------------------------------------------------- Datenfluss
    def arrays(self):
        p = self.params
        fr = self.frames
        n_roll = np.array([f.roll_hz for f in fr]) * 60.0 / p.inkr
        dt = np.array([1.0 / f.rate if f.rate > 0 else 0.0 for f in fr])
        n_meas = np.array([f.ign_hz for f in fr]) * 60.0 / p.imp
        afr = np.array([f.afr for f in fr])
        egt = np.array([f.egt1 for f in fr])
        return n_roll, dt, n_meas, afr, egt

    def poll(self) -> List[Frame]:
        """Regelmaessig aufrufen (z.B. 20x pro Sekunde). Liefert neue Telegramme."""
        if self.state == KLIMA:
            if self._klima_fertig.is_set():
                self.link.drain_frames()              # Telegramme von vor der Klimamessung gehoeren nicht zum Lauf
                self._klima_uebernehmen()
            elif time.time() - self._t_start > self.klima_timeout_s + 5.0:
                self.state = ABORTED                  # Messboard haengt (Klimaabfrage kommt nicht zurueck)
                self.message = "Messboard antwortet nicht (Klima) – abgebrochen"
            return []
        new = self.link.drain_frames()
        if not new:
            self.check_stale()
            return new
        p = self.params
        last = new[-1]
        n_roll = last.roll_hz * 60.0 / p.inkr
        self.live = Live(n_calc=n_roll * p.ratio, n_meas=last.ign_hz * 60.0 / p.imp, n_roll=n_roll,
                         v_kmh=n_roll / 60.0 * p.roll_circ * 3.6, rate=last.rate,
                         egt1=last.egt1, afr=last.afr, egt2=last.egt2)

        if self.state == COAST:
            self.frames.extend(new)
            self._coast_tick(time.time(), new)
            return new
        if self.state == WAIT:
            self.frames.extend(new)                   # Vorpuffer: die Daten des Anstiegsfensters gehen nicht verloren
            if not self._laufbeginn_pruefen():
                if time.time() - self._t_go > self.go_timeout_s:
                    self.state = ABORTED
                    self.message = f"Kein Drehzahlanstieg nach {self.go_timeout_s:.0f} s – abgebrochen"
                return new
        elif self.state == RUN:
            self.frames.extend(new)
        if self.state == RUN:
            self._since_eval += len(new)
            # Live-Kurve ab Laufbeginn, hoechstens alle 50 ms
            if time.time() - self._last_eval >= 0.05:
                self._last_eval = time.time()
                self._evaluate_live()
            if self.state == RUN and time.time() - self._t_run > RUN_TIMEOUT_S:
                self.state = ABORTED
                self.message = f"Kein Laufende nach {RUN_TIMEOUT_S:.0f} s – abgebrochen"
        return new

    def _laufbeginn_pruefen(self) -> bool:
        """WARTEN: steigt die Motordrehzahl start_anstieg_s lang (anstieg_beginn), beginnt die Messung am Anfang des
        Fensters, davor (Halten) wird verworfen. Sonst bleibt der Vorpuffer auf wenige Sekunden begrenzt."""
        p = self.params
        fr = self.frames
        if not fr:
            return False
        rate = float(np.median([f.rate for f in fr[-20:]]))
        rate = rate if rate > 0 else 20.0
        fenster = max(0.1, float(p.start_anstieg_s))
        keep = int(rate * max(5.0, 3.0 * fenster))
        if len(fr) > keep:
            del fr[:len(fr) - keep]                   # in place: dieselbe Liste (die Oberflaeche haengt daran)
        k = 60.0 / p.inkr * p.ratio
        i0 = anstieg_beginn([f.roll_hz * k for f in fr], rate, fenster, float(p.start_anstieg_min))
        if i0 is None:
            return False
        del fr[:i0]
        self.state = RUN
        self._t_run = time.time()
        self.run_start_t = fr[0].t if fr else self._t_run
        self._last_eval = 0.0
        self.message = ""
        return True

    def _evaluate_live(self):
        n_roll, dt, _, afr, egt = self.arrays()
        res = physics.evaluate(n_roll, dt, self.params, afr_raw=afr, egt_raw=egt, require_end=False)
        self.live_result = res
        if res is not None and res.end_index >= 0:
            if self.ausrollen and self.state == RUN:
                self._start_coast()
            else:
                self.finish()

    # ---------------------------------------------------------------- Ausrollversuch
    def _start_coast(self):
        self.state = COAST
        self.message = "Kupplung ziehen, ausrollen lassen"
        self._coast_t0 = self._coast_last = time.time()
        self._coast_min = float("inf")
        self._coast_hist = []
        self._coast_i = len(self.frames)

    def _coast_tick(self, now: float, new: List[Frame]):
        """Ende des Ausrollens pruefen (bei jedem poll mit neuen Telegrammen waehrend AUSROLLEN).
        Drehzahl = Median der letzten 0,3 s (Rolle x i), damit einzelne Ausreisser nichts beenden."""
        p = self.params
        self._coast_last = now
        keep = max(3, int(round(0.3 * max(self.live.rate, 1.0))))
        self._coast_hist = (self._coast_hist + [f.roll_hz * 60.0 / p.inkr * p.ratio for f in new])[-keep:]
        n_med = float(np.median(self._coast_hist))
        self._coast_min = min(self._coast_min, n_med)
        if self.coast_n_ende > 0 and n_med < self.coast_n_ende:
            self.end_coast(f"ausgerollt bis {self.coast_n_ende:.0f} 1/min")
        elif n_med > self._coast_min * 1.03 + 50.0:
            self.end_coast("Drehzahl steigt wieder – Ausrollen beendet")
        elif n_med < 30.0 * p.ratio:
            self.end_coast("Rolle steht")
        elif now - self._coast_t0 > self.coast_t_max:
            self.end_coast(f"Ausrollen nach {self.coast_t_max:.0f} s beendet")

    def check_stale(self, now: Optional[float] = None) -> bool:
        """Waehrend AUSROLLEN: kommen keine Telegramme mehr (Firmware-Autostopp bei Stillstand), Ausrollen beenden."""
        now = time.time() if now is None else now
        if self.state == COAST and now - self._coast_last > COAST_STILL_S:
            self.end_coast("keine Messdaten mehr – Rolle steht")
            return True
        return False

    def coast_text(self) -> str:
        """Kurzfassung des Ausrollversuchs fuer Ergebnis-Zeile und Statusleiste."""
        c = self.coast
        if c is None:
            return ""
        if not c.ok:
            return f"Ausrollversuch: {c.meldung}"
        auf = _ausrollen.aufteilen(c, _ausrollen.RollenKalibrierung.aus_params(self.params))
        n_pmax = self.result.n_pmax if self.result is not None else None
        return (f"Ausrollversuch: Verlust M0 {c.m0:.2f} / M1 {c.m1:.2f} Nm bei {c.n1:.0f} 1/min"
                + (f", m2 {c.m2:.3f}" if c.quadratisch else "") + f", R² {c.r2:.3f}"
                + (f" – {auf.text(n_pmax)}" if auf is not None else "")
                + " – unter Einstellungen › Rolle/Lauf › Verluste übernehmen")

    def ratio_aus_den_funken(self, n_roll: np.ndarray, n_meas: np.ndarray) -> Optional[float]:
        """Uebersetzung nKW/nRolle aus Zuendung und Rolle des Laufs (ohne Ausrollen), None = nicht bestimmbar."""
        ende = self._coast_i or len(n_roll)
        nr, nm = np.asarray(n_roll[:ende], float), np.asarray(n_meas[:ende], float)
        ok = np.isfinite(nr) & np.isfinite(nm) & (nr > 30.0) & (nm > 0)
        if ok.sum() < 5:
            return None
        ok &= nr >= 0.3 * float(np.max(nr[ok]))
        r = physics.trimmed_ratio(nm[ok], nr[ok])
        return r if r is not None and 0.01 <= r <= 100.0 else None

    def finish(self):
        n_roll, dt, n_meas, afr, egt = self.arrays()
        if self.ratio_aus_funken:
            self.ratio_gemessen = self.ratio_aus_den_funken(n_roll, n_meas)
            if self.ratio_gemessen is not None:
                self.params.ratio = self.ratio_gemessen
        self.result = physics.evaluate(n_roll, dt, self.params, n_meas, afr, egt, require_end=False)
        if self.state == COAST:
            start = self.result.end_index if self.result is not None and self.result.end_index >= 0 else None
            self.coast = _ausrollen.analyse(n_roll, dt, self.params, start=start, n_meas=n_meas,
                                            n_ende=self.coast_n_ende, quadratisch=self.coast_quadratisch)
        self.state = DONE
        self.ecu_data = None
        if self.ecu is not None and self.frames:
            self.ecu_data = self.ecu.snapshot(self.frames[0].t - 1.0, self.frames[-1].t + 1.0)
        if self.result is not None:
            self.message = self.result.end_reason + (f" | {self.coast_reason}" if self.coast_reason else "")
        # Messung laeuft fuer die Live-Anzeige weiter; neuer Lauf mit start()

    def gas_off(self) -> bool:
        """True, sobald n_stop erreicht ist (Hinweis fuer den Fahrer)."""
        return bool(self.params.n_stop) and self.live.n_calc >= self.params.n_stop
