"""Laufstart (1.4.2): nach START „Klima wird gemessen …“ (frischer Sensorwert, Timeout, Schalter aus), dann GO und
Laufbeginn, sobald die Motordrehzahl start_anstieg_s lang steigt (runner.anstieg_beginn); die Kurve beginnt am Anfang
des Anstiegsfensters, „n vom Gas“ ist nur noch die Ende-Schwelle."""
import os
import sys
import threading
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pyst import link, physics, runner  # noqa: E402
from pyst.link import Frame  # noqa: E402
from pyst.sim import SimEngine, SimSerial  # noqa: E402


def erkennung(n, rate, **kw):
    """Gleitend wie im Lauf: erstes Fenster, das ausloest -> (Ende-Index exklusiv, Fensteranfang) oder None."""
    N = max(6, int(round(kw.get("fenster_s", 1.0) * rate)))
    for i in range(N, len(n) + 1):
        i0 = runner.anstieg_beginn(n[:i], rate, **kw)
        if i0 is not None:
            return i, i0
    return None


def rauschen(rate, sig, sekunden, rng, ausreisser=True, n0=4000.0):
    L = int(rate * sekunden)
    x = n0 + rng.normal(0.0, sig, L)
    if ausreisser:                                   # 2 % einzelne Ausreisser mit 8 sigma
        idx = rng.integers(0, L, L // 50)
        x[idx] += rng.choice([-1.0, 1.0], len(idx)) * 8 * sig
    return x


def sim_frames(engine, seconds):
    s = SimSerial(engine=engine, realtime=False)
    s.write(b"m\n")
    for _ in range(int(seconds * engine.rate)):
        s.tick()
    frames = [link.parse_frame(line, 0) for line in s.read(10 ** 9).decode().strip().split("\n")]
    s.close()
    for k, f in enumerate(frames):                  # Zeitstempel wie beim Empfang
        f.t = 1.7e9 + k / engine.rate
    return frames


class FakeLink:
    """Gegenstelle ohne Hardware: liefert Telegramme in Happen, Klima wie eingestellt (Wert, Verzoegerung)."""
    port, lost, mode, default_mode = "FAKE", 0, "N", "N"

    def __init__(self, frames=(), klima=(18.4, 987.6, 0.0), klima_dauer=0.05, happen=30):
        self.frames = list(frames)
        self.klima = klima
        self.klima_dauer = klima_dauer
        self.happen = happen
        self.klima_aufrufe = []                     # (Zeit, timeout)
        self.starts = 0

    def climate(self, timeout=0.7):
        self.klima_aufrufe.append((time.time(), timeout))
        time.sleep(min(self.klima_dauer, timeout))
        return self.klima if self.klima_dauer <= timeout else None

    def drain_frames(self):
        out, self.frames = self.frames[:self.happen], self.frames[self.happen:]
        return out

    def start(self):
        self.starts += 1

    def stop(self):
        pass


def bis(cond, sek=5.0):
    end = time.time() + sek
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class Anstieg(unittest.TestCase):
    def test_rauschen_loest_nicht_aus(self):
        """Halten mit Messrauschen wie an echten Laeufen (20 Hz bis 65 1/min, 60 Hz bis 150 1/min, Ausreisser) und
        langsame Drift loesen in 2 Minuten nie aus."""
        rng = np.random.default_rng(7)
        for rate, sig in ((20, 65.0), (20, 120.0), (40, 100.0), (60, 150.0), (60, 20.0)):
            with self.subTest(rate=rate, sigma=sig):
                x = rauschen(rate, sig, 120, rng)
                self.assertIsNone(erkennung(x, rate))
        x = rauschen(20, 30.0, 60, rng, ausreisser=False) + 30.0 * np.arange(1200) / 20   # Drift 30 1/min pro s
        self.assertIsNone(erkennung(x, 20))

    def test_echter_anstieg_nach_etwa_einer_sekunde(self):
        """Vollgas nach 3 s Halten: Erkennung ~1 s nach Beginn des Anstiegs, Fensteranfang nahe am Anstieg."""
        rng = np.random.default_rng(3)
        for rate, sig, acc in ((20, 35.0, 230.0), (20, 65.0, 1000.0), (60, 150.0, 500.0), (60, 20.0, 3000.0)):
            with self.subTest(rate=rate, sigma=sig, anstieg=acc):
                t = np.arange(rate * 10) / rate
                x = rauschen(rate, sig, 10, rng, ausreisser=False) + np.where(t > 3.0, acc * (t - 3.0), 0.0)
                r = erkennung(x, rate)
                self.assertIsNotNone(r)
                verzoegerung, anfang = r[0] / rate - 3.0, r[1] / rate - 3.0
                self.assertGreater(verzoegerung, 0.4)
                self.assertLess(verzoegerung, 1.8)
                self.assertGreater(anfang, -0.5)          # Fenster liegt hoechstens kurz im Halten
                self.assertLess(anfang, 0.9)

    def test_mindestanstieg_und_fenster(self):
        rate = 50
        t = np.arange(rate * 4) / rate
        langsam = 4000.0 + 100.0 * t                      # 100 1/min pro s, ohne Rauschen
        self.assertIsNone(erkennung(langsam, rate))                          # < 150 1/min in 1 s
        self.assertIsNotNone(erkennung(langsam, rate, min_anstieg=80.0))
        r = erkennung(4000.0 + 200.0 * t, rate)
        self.assertEqual(r, (rate, 0))                    # sofort nach dem ersten vollen Fenster, ab Anfang
        r = erkennung(4000.0 + 200.0 * t, rate, fenster_s=2.0)
        self.assertEqual(r, (2 * rate, 0))                # laengeres Fenster: entsprechend spaeter
        # zu wenig Daten, fallende Drehzahl, Treppe mit flacher Teilstrecke
        self.assertIsNone(runner.anstieg_beginn([4000.0] * 3, rate))
        self.assertIsNone(erkennung(9000.0 - 500.0 * t, rate))
        treppe = np.concatenate([np.full(25, 4000.0), np.full(25, 4400.0)])
        self.assertIsNone(runner.anstieg_beginn(treppe, rate))


class Ablauf(unittest.TestCase):
    def _lauf(self, ctrl, frames_bis=None):
        zustaende = []
        while ctrl.state not in (runner.DONE, runner.ABORTED) and (ctrl.link.frames or ctrl.state == runner.KLIMA):
            ctrl._last_eval = 0.0
            ctrl.poll()
            if not zustaende or zustaende[-1] != ctrl.state:
                zustaende.append(ctrl.state)
            if ctrl.state == runner.KLIMA:
                time.sleep(0.005)
        return zustaende

    def test_laufbeginn_am_fensteranfang(self):
        """Simulator: 3 s Halten bei 3000 1/min, dann Vollgas. Die Messung beginnt erst mit dem Anstieg (auch weit
        unter n vom Gas), das Halten ist verworfen, die Daten beginnen am Anfang des Anstiegsfensters."""
        e = SimEngine(noise=0.01)
        frames = sim_frames(e, 30)
        n = np.array([f.roll_hz for f in frames]) * 60.0 / 100.0 * 6.85
        knick = int(np.argmax(n > 3150.0))                # hier ist der Anstieg sicher unterwegs
        p = physics.DynoParams(n_stop=0, n_min=9000.0)
        L = FakeLink(frames, happen=3)
        ctrl = runner.RunController(L, p, auto_climate=False)
        ctrl.start()
        self.assertEqual(ctrl.state, runner.WAIT)
        n_bei_start = None
        while ctrl.state == runner.WAIT and L.frames:
            ctrl.poll()
            n_bei_start = ctrl.live.n_calc
        self.assertEqual(ctrl.state, runner.RUN)
        self.assertLess(n_bei_start, 6000.0)              # Laufbeginn weit unter n vom Gas (9000)
        erstes = ctrl.frames[0].cycle
        idx = next(k for k, f in enumerate(frames) if f.cycle == erstes)
        self.assertGreater(idx, int(2.0 * e.rate))        # Halten verworfen
        self.assertLess(abs(idx - (knick - 0.2 * e.rate)), 0.6 * e.rate)   # Fensteranfang am Anstieg
        self.assertEqual([f.cycle for f in ctrl.frames], [f.cycle for f in frames[idx:idx + len(ctrl.frames)]])
        self.assertGreater(ctrl.run_start_t, 1.7e9)
        self._lauf(ctrl)
        self.assertEqual(ctrl.state, runner.DONE)
        self.assertGreater(ctrl.result.p_max, 30.0)
        self.assertLess(ctrl.result.n[0], 3400.0)         # Kurve beginnt unten

    def test_kein_anstieg_bricht_ab(self):
        f = [Frame(1.7e9 + k / 20, k, 20.0, 50.0, 730.0) for k in range(200)]
        ctrl = runner.RunController(FakeLink(f), physics.DynoParams(), auto_climate=False)
        ctrl.go_timeout_s = 0.2
        ctrl.start()
        ctrl.poll()
        self.assertEqual(ctrl.state, runner.WAIT)
        time.sleep(0.25)
        ctrl.poll()
        self.assertEqual(ctrl.state, runner.ABORTED)
        self.assertIn("Kein Drehzahlanstieg", ctrl.message)

    def test_klima_frischer_wert(self):
        """Mit Klima vom Sensor: erst Phase KLIMA (Anfrage nach START), dann GO; Wert geht in Rechnung und
        Quelle."""
        L = FakeLink(sim_frames(SimEngine(), 25), klima_dauer=0.15)
        p = physics.DynoParams(n_stop=0, temp_c=30.0, p_mbar=900.0)
        ctrl = runner.RunController(L, p, auto_climate=True)
        t0 = time.time()
        ctrl.start()
        self.assertEqual(ctrl.state, runner.KLIMA)
        self.assertIn(ctrl.state, runner.ACTIVE)
        self.assertIn("Klima wird gemessen", ctrl.message)
        self.assertEqual(ctrl.poll(), [])                 # waehrend KLIMA keine Telegramme fuer den Lauf
        self.assertTrue(bis(lambda: (ctrl.poll() or True) and ctrl.state != runner.KLIMA))
        self.assertEqual(ctrl.state, runner.WAIT)
        self.assertEqual(len(L.klima_aufrufe), 1)
        self.assertGreaterEqual(L.klima_aufrufe[0][0], t0)          # angefordert nach START
        self.assertEqual(L.klima_aufrufe[0][1], runner.KLIMA_TIMEOUT_S)
        self.assertEqual(L.starts, 1)                                # Messung danach gestartet
        self.assertGreaterEqual(ctrl.klima_zeit, t0)                 # Sensorwert juenger als START
        self.assertEqual((p.temp_c, p.p_mbar), (18.4, 987.6))
        self.assertTrue(ctrl.klima_quelle.startswith("Sensor "))
        self.assertIn("vom Sensor", ctrl.klima_text())
        self._lauf(ctrl)
        self.assertEqual(ctrl.state, runner.DONE)
        self.assertAlmostEqual(ctrl.result.ka, physics.din70020(18.4, 987.6))
        import json
        import tempfile
        from pyst import storage
        with tempfile.TemporaryDirectory() as d:
            ordner = storage.save_run(ctrl, base_dir=d)
            with open(os.path.join(ordner, "lauf.json"), encoding="utf-8") as fh:
                meta = json.load(fh)
        self.assertEqual(meta["klima"][:2], [18.4, 987.6])
        self.assertTrue(meta["klima_quelle"].startswith("Sensor "))
        self.assertTrue(meta["klima_zeit"])
        self.assertTrue(meta["laufbeginn"])
        self.assertEqual(meta["parameter"]["start_anstieg_s"], 1.0)

    def test_klima_timeout_ohne_sensor(self):
        """Keine Antwort binnen klima_timeout_s: mit dem eingetragenen Wert weiter, Hinweis."""
        L = FakeLink(sim_frames(SimEngine(), 2), klima_dauer=10.0)
        p = physics.DynoParams(temp_c=24.0, p_mbar=1001.0)
        ctrl = runner.RunController(L, p, auto_climate=True)
        ctrl.klima_timeout_s = 0.2
        t0 = time.time()
        ctrl.start()
        self.assertTrue(bis(lambda: (ctrl.poll() or True) and ctrl.state != runner.KLIMA, 3.0))
        self.assertLess(time.time() - t0, 1.5)
        self.assertEqual(ctrl.state, runner.WAIT)
        self.assertIsNone(ctrl.climate)
        self.assertEqual((p.temp_c, p.p_mbar), (24.0, 1001.0))
        self.assertEqual(ctrl.klima_quelle, "eingetragen (kein Sensorwert)")
        self.assertIn("eingetragener Wert", ctrl.message)
        self.assertIn("eingetragener Wert", ctrl.klima_text())
        self.assertIn("kein Sensorwert", ctrl.klima_text())
        ctrl.abort()

    def test_klima_aus_keine_phase(self):
        L = FakeLink([])
        ctrl = runner.RunController(L, physics.DynoParams(), auto_climate=False)
        ctrl.start()
        self.assertEqual(ctrl.state, runner.WAIT)
        self.assertEqual(L.klima_aufrufe, [])
        self.assertEqual(ctrl.klima_quelle, "eingetragen (Sensor aus)")
        self.assertIn("Sensor aus", ctrl.klima_text())

    def test_abbrechen_waehrend_klima(self):
        L = FakeLink([], klima_dauer=0.3)
        ctrl = runner.RunController(L, physics.DynoParams(), auto_climate=True)
        ctrl.start()
        ctrl.abort()
        self.assertEqual(ctrl.state, runner.ABORTED)
        time.sleep(0.4)
        ctrl.poll()
        self.assertEqual(ctrl.state, runner.ABORTED)
        self.assertEqual(L.starts, 0)                     # nach dem Abbruch nicht mehr gestartet

    def test_simulator_lauf_komplett(self):
        """Echter Simulator-Port: START -> Klima -> GO -> Messung -> Fertig."""
        L = link.DynoLink(link.SIM_PORT)
        try:
            ctrl = runner.RunController(L, physics.DynoParams(n_stop=0), auto_climate=True)
            ctrl.start()
            zustaende = [ctrl.state]
            t0 = time.time()
            while ctrl.state not in (runner.DONE, runner.ABORTED) and time.time() - t0 < 60:
                ctrl.poll()
                if zustaende[-1] != ctrl.state:
                    zustaende.append(ctrl.state)
                time.sleep(0.02)
        finally:
            L.close()
        self.assertEqual(zustaende, [runner.KLIMA, runner.WAIT, runner.RUN, runner.DONE])
        self.assertIsNotNone(ctrl.climate)
        self.assertGreater(ctrl.result.p_max, 30.0)
        self.assertLess(ctrl.result.n[0], 3400.0)


if __name__ == "__main__":
    unittest.main()
