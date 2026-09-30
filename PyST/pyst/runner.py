"""
Ablauf eines Laufs (von GUI und Kommandozeile gemeinsam genutzt).

  BEREIT --start()--> (Klima lesen) --> WARTEN ("GO, Vollgas") --n > n_min--> MESSUNG
  MESSUNG --Verzoegerung oder n >= n_stop--> FERTIG (Auswertung wie LabVIEW)
"""
import time
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from . import physics
from .link import DynoLink, Frame

IDLE, WAIT, RUN, DONE, ABORTED = "Bereit", "GO – Vollgas", "Messung", "Fertig", "Abgebrochen"
RUN_TIMEOUT_S = 120.0


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
    def __init__(self, link: DynoLink, params: physics.DynoParams, auto_climate: bool = True):
        self.link = link
        self.params = params
        self.auto_climate = auto_climate
        self.state = IDLE
        self.live = Live()
        self.result: Optional[physics.RunResult] = None
        self.live_result: Optional[physics.RunResult] = None
        self.climate: Optional[tuple] = None
        self.frames: List[Frame] = []
        self._t_start = 0.0
        self._since_eval = 0
        self._last_eval = 0.0
        self.message = ""

    # ---------------------------------------------------------------- Steuerung
    def start(self):
        self.result = self.live_result = None
        self.frames = []
        if self.auto_climate:
            self.climate = self.link.climate()
            if self.climate:
                self.params.temp_c, self.params.p_mbar = self.climate[0], self.climate[1]
                self.message = f"Klima: {self.climate[0]:.1f} °C, {self.climate[1]:.1f} mbar, {self.climate[2]:.0f} %"
            else:
                self.message = "Keine Klimadaten – manuelle Werte werden verwendet"
        self.link.start()
        self._t_start = time.time()
        self.state = WAIT

    def abort(self):
        if self.state in (WAIT, RUN):
            self.state = ABORTED
            self.message = "Lauf abgebrochen"
        self.link.stop()

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
            return new
        p = self.params
        last = new[-1]
        n_roll = last.roll_hz * 60.0 / p.inkr
        self.live = Live(n_calc=n_roll * p.ratio, n_meas=last.ign_hz * 60.0 / p.imp, n_roll=n_roll,
                         v_kmh=n_roll / 60.0 * p.roll_circ * 3.6, rate=last.rate,
                         egt1=last.egt1, afr=last.afr, egt2=last.egt2)

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
            self.finish()

    def finish(self):
        n_roll, dt, n_meas, afr, egt = self.arrays()
        self.result = physics.evaluate(n_roll, dt, self.params, n_meas, afr, egt, require_end=False)
        self.state = DONE
        if self.result is not None:
            self.message = self.result.end_reason
        # Messung laeuft fuer die Live-Anzeige weiter; neuer Lauf mit start()

    def gas_off(self) -> bool:
        """True, sobald n_stop erreicht ist (Hinweis fuer den Fahrer)."""
        return bool(self.params.n_stop) and self.live.n_calc >= self.params.n_stop
