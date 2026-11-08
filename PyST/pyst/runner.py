"""
Ablauf eines Laufs (von GUI und Kommandozeile gemeinsam genutzt).

  BEREIT --start()--> (Klima lesen) --> WARTEN ("GO, Vollgas") --n > n_min--> MESSUNG
  MESSUNG --Verzoegerung oder n >= n_stop--> FERTIG (Auswertung wie LabVIEW)

Ausrollversuch (ausrollen = True): MESSUNG --Laufende--> AUSROLLEN ("Kupplung ziehen, ausrollen lassen"), die
Aufzeichnung laeuft weiter, bis die Rolle unter n_ende (Motordrehzahl Rolle x i) faellt, die Drehzahl wieder
steigt, t_max vergeht, keine Telegramme mehr kommen oder end_coast() (Knopf). Dann FERTIG: Lauf wie bisher
ausgewertet, dazu coast = ausrollen.analyse(...) (Verlustmoment). Ausgeschaltet: Ablauf exakt wie bisher.
"""
import time
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from . import ausrollen as _ausrollen
from . import physics
from .link import DynoLink, Frame

IDLE, WAIT, RUN, DONE, ABORTED = "Bereit", "GO – Vollgas", "Messung", "Fertig", "Abgebrochen"
COAST = "Ausrollen"
ACTIVE = (WAIT, RUN, COAST)          # Lauf laeuft (Aufzeichnung, Bedienung gesperrt)
RUN_TIMEOUT_S = 120.0
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

    # ---------------------------------------------------------------- Steuerung
    def start(self):
        self.result = self.live_result = None
        self.coast, self.coast_reason = None, ""
        self.frames = []
        self.basis_events = []
        self.ecu_data = None
        if self.auto_climate:
            self.climate = self.link.climate()
            if self.climate:
                self.params.temp_c, self.params.p_mbar = self.climate[0], self.climate[1]
                feuchte = f", {self.climate[2]:.0f} %" if self.climate[2] > 0 else ""    # BMP280: keine Feuchte
                self.message = f"Klima: {self.climate[0]:.1f} °C, {self.climate[1]:.1f} mbar{feuchte}"
            else:
                self.message = "Keine Klimadaten – manuelle Werte werden verwendet"
        dm = getattr(self.link, "default_mode", "N")
        if dm != "N" and self.link.mode != dm:
            self.link.set_mode(dm)                    # Basis mit Zange: Zuendstaerke mit aufzeichnen
        self.link.start()
        self._t_start = time.time()
        self.state = WAIT

    def add_event(self, ev):
        """Ereignis der Basis (basis.BasisEvent) – gehoert zum Lauf, solange er laeuft."""
        if self.state in ACTIVE:
            self.basis_events.append(ev)

    def abort(self):
        if self.state == COAST:                      # Lauf ist schon fertig: Ausrollen beenden, nichts verwerfen
            self.end_coast("Ausrollen von Hand beendet")
            return
        if self.state in (WAIT, RUN):
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
        if self.state in (WAIT, RUN):
            self.frames.extend(new)
            self._since_eval += len(new)
            if self.state == WAIT and self.live.n_calc > p.n_min:
                self.state = RUN
            # Live-Kurve ab START berechnen (nicht erst ueber n vom Gas – das ist nur die Ende-Schwelle),
            # hoechstens alle 50 ms
            if time.time() - self._last_eval >= 0.05:
                self._last_eval = time.time()
                self._evaluate_live()
            if time.time() - self._t_start > RUN_TIMEOUT_S:
                self.state = ABORTED
                self.message = f"Kein Laufende nach {RUN_TIMEOUT_S:.0f} s – abgebrochen"
        return new

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
        return (f"Ausrollversuch: Verlust M0 {c.m0:.2f} / M1 {c.m1:.2f} Nm bei {c.n1:.0f} 1/min"
                + (f", m2 {c.m2:.3f}" if c.quadratisch else "") + f", R² {c.r2:.3f} – im Reiter Kalibrierung "
                "übernehmen")

    def finish(self):
        n_roll, dt, n_meas, afr, egt = self.arrays()
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
