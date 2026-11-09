"""Ausrollversuch: Erkennung und Fit auf synthetischen Daten, Fehlerfaelle, Rundlauf mit dem Simulator,
Speichern und der Reiter „Kalibrierung“ (offscreen).

    python -m pytest tests/test_ausrollen.py
"""
import importlib
import json
import os
import sys
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from conftest import close_window  # noqa: E402
from pyst import ausrollen, link, physics, runner, storage  # noqa: E402
from pyst.sim import SimEngine, SimSerial, ausroll_engine  # noqa: E402

RATIO = 6.85


def synth(m0=4.0, m1=20.0, n1=1600.0, m2=0.0, inertia=13.5, rate=60.0, n_start=1700.0, n_end=400.0,
          noise=0.002, t_uebergang=1.0, extra=20.0, ign=True, kupplung=True, seed=7, t_max=None):
    """Lauf-Ende + Ausrollen: vorher steigt die Drehzahl, danach t_uebergang Sekunden Motorbremse (extra Nm an der
    Rolle, Zuendung folgt der Rolle), dann Kupplung gezogen (Zuendung Leerlauf). Liefert (n_roll, dt, n_meas, Ende)."""
    rng = np.random.default_rng(seed)
    dt = 1.0 / rate
    k = int(2.0 * rate)
    n = [n_start - (k - j) * 2.0 for j in range(k)]
    nm = [x * RATIO for x in n]
    w, t, sub = n_start * physics.RAD_PER_RPM_S, 0.0, 20
    while True:
        acc = 0.0
        for _ in range(sub):
            mv = physics.loss_torque(w / physics.RAD_PER_RPM_S, m0, m1, n1, m2) + (extra if t < t_uebergang else 0.0)
            w = max(0.0, w - mv / inertia * dt / sub)
            t += dt / sub
            acc += w
        nr = acc / sub / physics.RAD_PER_RPM_S
        n.append(nr)
        nm.append(nr * RATIO if (t < t_uebergang or not kupplung) else 1500.0)
        if nr < n_end or (t_max and t > t_max):
            break
    n = np.array(n) * (1 + rng.normal(0, noise, len(n)))
    nm = np.array(nm) * (1 + rng.normal(0, 2 * noise, len(nm)))
    return n, np.full(len(n), dt), (nm if ign else np.zeros(len(n))), k


def sim_frames(engine, seconds):
    s = SimSerial(engine=engine, realtime=False)
    s.write(b"m\n")
    for _ in range(int(seconds * engine.rate)):
        s.tick()
    frames = [link.parse_frame(line, 0) for line in s.read(10 ** 9).decode().strip().split("\n")]
    s.close()
    return frames


class Rechnung(unittest.TestCase):
    GRID = np.array([500.0, 1000.0, 1600.0])

    def assert_mv(self, r, m0, m1, n1, m2, tol=0.02):
        true = physics.loss_torque(self.GRID, m0, m1, n1, m2)
        np.testing.assert_allclose(r.mv(self.GRID), true, rtol=tol)

    def test_linear_mit_rauschen(self):
        n, dt, nm, k = synth()
        r = ausrollen.analyse(n, dt, physics.DynoParams(n1=1600, ratio=RATIO), start=k, n_meas=nm,
                              quadratisch=False)
        self.assertTrue(r.ok, r.meldung)
        self.assertAlmostEqual(r.m0, 4.0, delta=0.08)
        self.assertAlmostEqual(r.m1, 20.0, delta=0.2)
        self.assertEqual(r.n1, 1600.0)
        self.assertEqual(r.m2, 0.0)
        self.assertGreater(r.r2, 0.95)
        self.assertLess(r.rms_nm, 1.0)
        self.assertEqual(r.kupplung, "Zündsignal")
        self.assertGreater(len(r.punkte_n), 10)

    def test_quadratisch_mit_rauschen(self):
        n, dt, nm, k = synth(m2=3.0, noise=0.004, seed=11)
        r = ausrollen.analyse(n, dt, physics.DynoParams(n1=1600, ratio=RATIO), start=k, n_meas=nm,
                              quadratisch=True)
        self.assertTrue(r.ok, r.meldung)
        self.assert_mv(r, 4.0, 20.0, 1600.0, 3.0)
        self.assertAlmostEqual(r.m2, 3.0, delta=0.3)
        self.assertTrue(r.quadratisch)

    def test_n1_automatisch(self):
        n, dt, nm, k = synth()
        r = ausrollen.analyse(n, dt, physics.DynoParams(ratio=RATIO), start=k, n_meas=nm, quadratisch=False)
        self.assertTrue(r.ok, r.meldung)
        self.assertEqual(r.n1, 1700.0)                     # obere Drehzahl auf 100 gerundet
        self.assert_mv(r, 4.0, 20.0, 1600.0, 0.0)

    def test_uebergang_wird_verworfen(self):
        for ign in (True, False):
            with self.subTest(zuendsignal=ign):
                n, dt, nm, k = synth(extra=25.0, t_uebergang=1.5, ign=ign, n_end=900.0)
                r = ausrollen.analyse(n, dt, physics.DynoParams(n1=1600, ratio=RATIO), start=k, n_meas=nm,
                                      quadratisch=False)
                self.assertTrue(r.ok, r.meldung)
                self.assertGreaterEqual(r.verworfen_s, 1.2)     # Motorbremse (1,5 s) nicht ausgewertet
                self.assertLess(r.verworfen_s, 3.0)
                self.assert_mv(r, 4.0, 20.0, 1600.0, 0.0)
                self.assertEqual(r.kupplung, "Zündsignal" if ign else "Verzögerung")
                # ohne Verwerfen waere das Ergebnis deutlich daneben
                c, _p, _rms = ausrollen.fit_integral(ausrollen.zeitachse(dt)[k:r.i_bis],
                                                     n[k:r.i_bis] * physics.RAD_PER_RPM_S,
                                                     ausrollen.glaetten(n, 6)[k:r.i_bis], 13.5, 1600.0, False)
                self.assertGreater(abs(c[0] + c[1] - 20.0), 3 * abs(r.m1 - 20.0))

    def test_bremse_am_ende_wird_verworfen(self):
        n, dt, nm, k = synth(n_end=600.0)
        # die letzten 3 s zusaetzlich gebremst
        t = ausrollen.zeitachse(dt)
        j = len(n) - int(3 * 60)
        w = n[j] * physics.RAD_PER_RPM_S
        for i in range(j, len(n)):
            w -= (physics.loss_torque(w / physics.RAD_PER_RPM_S, 4, 20, 1600, 0) + 30.0) / 13.5 * (t[1] - t[0])
            n[i] = max(w, 0.0) / physics.RAD_PER_RPM_S
        r = ausrollen.analyse(n, dt, physics.DynoParams(n1=1600, ratio=RATIO), start=k, n_meas=nm,
                              quadratisch=False)
        self.assertTrue(r.ok, r.meldung)
        self.assertLessEqual(r.i_bis, j + 30)
        self.assert_mv(r, 4.0, 20.0, 1600.0, 0.0)

    def test_untere_drehzahl(self):
        n, dt, nm, k = synth()
        r = ausrollen.analyse(n, dt, physics.DynoParams(n1=1600, ratio=RATIO), start=k, n_meas=nm,
                              n_ende=800.0 * RATIO, quadratisch=False)
        self.assertTrue(r.ok, r.meldung)
        self.assertGreater(r.n_bis, 790.0)

    # ---- Fehlerfaelle
    def test_keine_ausrolldaten(self):
        n, dt, nm, k = synth()
        r = ausrollen.analyse(n[:k + 10], dt[:k + 10], physics.DynoParams(), start=k, n_meas=nm[:k + 10])
        self.assertFalse(r.ok)
        self.assertIn("Keine Ausrolldaten", r.meldung)
        r = ausrollen.analyse(n, dt, physics.DynoParams(), start=None)
        self.assertIn("Keine Ausrolldaten", r.meldung)

    def test_zu_kurz(self):
        n, dt, nm, k = synth(t_max=3.0)
        r = ausrollen.analyse(n, dt, physics.DynoParams(ratio=RATIO), start=k, n_meas=nm)
        self.assertFalse(r.ok)
        self.assertIn("zu kurz", r.meldung)

    def test_drehzahlbereich_zu_klein(self):
        n, dt, nm, k = synth(m0=0.5, m1=1.0, t_max=12.0)
        r = ausrollen.analyse(n, dt, physics.DynoParams(ratio=RATIO), start=k, n_meas=nm)
        self.assertFalse(r.ok)
        self.assertIn("Drehzahlbereich zu klein", r.meldung)

    def test_rolle_beschleunigt(self):
        n, dt, nm, k = synth(m0=-3.0, m1=-6.0, n_end=0.0, t_max=10.0, ign=False, extra=0.0)
        r = ausrollen.analyse(n, dt, physics.DynoParams(ratio=RATIO), start=k, n_meas=nm)
        self.assertFalse(r.ok)
        self.assertIn("beschleunigt", r.meldung)

    def test_kupplung_nicht_gezogen(self):
        n, dt, nm, k = synth(kupplung=False)
        r = ausrollen.analyse(n, dt, physics.DynoParams(ratio=RATIO), start=k, n_meas=nm)
        self.assertFalse(r.ok)
        self.assertIn("Kupplung nicht gezogen", r.meldung)

    # ---- Modell und Speichern
    def test_modell_rueckwaertskompatibel(self):
        n = np.array([0.0, 500.0, 1000.0])
        np.testing.assert_allclose(physics.loss_torque(n, 1.0, 3.0, 1000.0), [1.0, 2.0, 3.0])
        np.testing.assert_allclose(physics.loss_torque(n, 1.0, 3.0, 1000.0, 0.0), [1.0, 2.0, 3.0])
        q = physics.loss_torque(n, 1.0, 3.0, 1000.0, 2.0)
        self.assertAlmostEqual(q[0], 1.0)                  # M0 und M1 bleiben Stuetzstellen
        self.assertAlmostEqual(q[2], 3.0)
        self.assertAlmostEqual(q[1], 2.0 + 2.0 * 0.5 * -0.5)
        self.assertEqual(physics.DynoParams().m2, 0.0)

    def test_speichern_und_params(self):
        n, dt, nm, k = synth()
        r = ausrollen.analyse(n, dt, physics.DynoParams(n1=1600, ratio=RATIO), start=k, n_meas=nm,
                              datum="2026-11-08T14:30:00")
        d = json.loads(json.dumps(r.als_dict(punkte=True)))
        r2 = ausrollen.AusrollErgebnis.aus_dict(d)
        self.assertEqual((r2.ok, r2.m0, r2.m1, r2.n1, r2.m2), (r.ok, r.m0, r.m1, r.n1, r.m2))
        self.assertEqual(len(r2.punkte_n), len(r.punkte_n))
        self.assertTrue(r.quelle().startswith("Ausrollversuch 08.11.2026 14:30, R² 0,9"))
        p = r.params_fuer(physics.DynoParams(ratio=5.0))
        self.assertEqual((p.m0, p.m1, p.n1, p.m2, p.ratio), (r.m0, r.m1, r.n1, r.m2, 5.0))
        self.assertEqual(p.verlust_quelle, r.quelle())


class Rundlauf(unittest.TestCase):
    """Simulator mit gezogener Kupplung -> Verluste -> Motorleistung = Radleistung + Verlust."""

    @classmethod
    def setUpClass(cls):
        cls.engine = ausroll_engine()
        cls.frames = sim_frames(cls.engine, 90)           # Rolle + Reifen/Kette: nach ~100 s wird gebremst

    def arrays(self, p):
        fr = self.frames
        return (np.array([f.roll_hz for f in fr]) * 60 / p.inkr, np.array([1 / f.rate for f in fr]),
                np.array([f.ign_hz for f in fr]) * 60 / p.imp)

    def test_simulator_rollt_aus(self):
        e = self.engine
        p = physics.DynoParams(n_stop=0)
        n_roll, dt, n_meas = self.arrays(p)
        res = physics.evaluate(n_roll, dt, p, n_meas)
        j = res.end_index + int(3 * e.rate)
        self.assertLess(n_meas[j], 0.5 * n_roll[j] * e.ratio)       # Kupplung gezogen: Motor im Leerlauf
        self.assertGreater(n_roll[-1], 100)                          # Rolle rollt noch

    def test_motor_gleich_rad_plus_verlust(self):
        e = self.engine
        p = physics.DynoParams(n_stop=0)
        n_roll, dt, n_meas = self.arrays(p)
        res = physics.evaluate(n_roll, dt, p, n_meas)
        self.assertFalse(res.verluste)
        np.testing.assert_allclose(res.ps, res.ps_rad)
        c = ausrollen.analyse(n_roll, dt, p, start=res.end_index, n_meas=n_meas)
        self.assertTrue(c.ok, c.meldung)
        g = np.linspace(c.n_bis, c.n_von, 5)
        np.testing.assert_allclose(c.mv(g), [e.loss_total(x * physics.RAD_PER_RPM_S) for x in g], rtol=0.02)
        q = c.params_fuer(p)
        res2 = physics.evaluate(n_roll, dt, q, n_meas)
        self.assertTrue(res2.verluste)
        self.assertIn("Ausrollversuch", res2.verlust_text())
        n_rf = physics.filter_roll(n_roll, q.ma)[res2.segment]
        verlust = ausrollen.verlust_ps(n_rf, physics.loss_torque(n_rf, q.m0, q.m1, q.n1, q.m2))
        np.testing.assert_allclose(res2.ps, res2.ps_rad + verlust, rtol=1e-9, atol=1e-9)
        np.testing.assert_allclose(res2.ps_rad, res.ps, rtol=1e-9)    # Radleistung bleibt
        self.assertGreater(res2.p_max, res2.p_rad_max + 1.0)
        for n in (7000, 9500, 11000):                                  # Motormoment wie im Simulator
            j = int(np.argmin(abs(res2.n - n)))
            self.assertAlmostEqual(res2.nm[j], e.torque_wot(res2.n[j]), delta=0.03 * e.t_max)
        self.assertIn("Motor", res2.summary())
        self.assertIn("Rad", res2.summary())

    def test_controller_und_speichern(self):
        class FakeLink:
            port, lost, mode, default_mode = "SIM", 0, "N", "N"

            def __init__(self, frames):
                self.frames = list(frames)

            def drain_frames(self):
                out, self.frames = self.frames[:30], self.frames[30:]
                return out

            def start(self):
                pass

            def stop(self):
                pass

        for an in (False, True):
            with self.subTest(ausrollen=an):
                ctrl = runner.RunController(FakeLink(self.frames), physics.DynoParams(n_stop=0), auto_climate=False)
                ctrl.ausrollen = an
                ctrl.coast_n_ende, ctrl.coast_t_max = 5000.0, 1e6
                ctrl.start()
                states = set()
                while ctrl.state not in (runner.DONE, runner.ABORTED) and ctrl.link.frames:
                    ctrl._last_eval = 0.0
                    ctrl.poll()
                    states.add(ctrl.state)
                self.assertEqual(ctrl.state, runner.DONE)
                self.assertIsNotNone(ctrl.result)
                if not an:                                  # Verhalten wie bisher: endet mit dem Lauf
                    self.assertNotIn(runner.COAST, states)
                    self.assertIsNone(ctrl.coast)
                    self.assertLess(len(ctrl.frames), ctrl.result.end_index + 200)
                    continue
                self.assertIn(runner.COAST, states)
                self.assertTrue(ctrl.coast.ok, ctrl.coast.meldung)
                self.assertIn("5000", ctrl.message)
                self.assertGreater(ctrl.coast.n_bis * 6.85, 4900)
                with tempfile.TemporaryDirectory() as d:
                    folder = storage.save_run(ctrl, base_dir=d)
                    with open(os.path.join(folder, "lauf.json"), encoding="utf-8") as fh:
                        meta = json.load(fh)
                    run = storage.load_any(folder)
                self.assertTrue(meta["ausrollen"]["ok"])
                self.assertAlmostEqual(meta["ausrollen"]["m1"], ctrl.coast.m1)
                res = storage.recompute(run)
                self.assertEqual(res.end_index, ctrl.result.end_index)   # Ausrolldaten stoeren die Auswertung nicht
                self.assertAlmostEqual(res.p_max, ctrl.result.p_max, delta=0.01)   # roh.csv gerundet
                c = ausrollen.analyse(run["n_roll"], run["dt"], run["params"], start=res.end_index,
                                      n_meas=run["n_meas"], n_ende=5000.0)
                self.assertAlmostEqual(c.m1, ctrl.coast.m1, delta=0.01 * abs(ctrl.coast.m1))

    def test_uebersetzung_aus_den_funken(self):
        """Einstellung Funkenmessung: Uebersetzung am Laufende aus Zuendung/Rolle (ohne Ausrollphase, dort
        Leerlauf), Lauf damit gerechnet; von Hand bleibt der eingestellte Wert."""
        class FakeLink:
            port, lost, mode, default_mode = "SIM", 0, "N", "N"

            def __init__(self, frames):
                self.frames = list(frames)

            def drain_frames(self):
                out, self.frames = self.frames[:30], self.frames[30:]
                return out

            def start(self):
                pass

            def stop(self):
                pass

        faelle = ((SimEngine(ratio=7.2), False, 7.2), (ausroll_engine(ratio=7.2), True, 7.2))
        for engine, aus, soll in faelle:
            for funken in (False, True):
                with self.subTest(ausrollen=aus, funken=funken):
                    frames = sim_frames(engine, 150 if aus else 40)
                    ctrl = runner.RunController(FakeLink(frames), physics.DynoParams(ratio=6.5, n_stop=0),
                                                auto_climate=False)
                    ctrl.ausrollen = aus
                    ctrl.coast_n_ende, ctrl.coast_t_max = 5000.0, 1e6
                    ctrl.ratio_aus_funken = funken
                    ctrl.start()
                    while ctrl.state not in (runner.DONE, runner.ABORTED) and ctrl.link.frames:
                        ctrl._last_eval = 0.0
                        ctrl.poll()
                    if ctrl.state != runner.DONE:
                        ctrl.finish()
                    if not funken:
                        self.assertIsNone(ctrl.ratio_gemessen)
                        self.assertEqual(ctrl.params.ratio, 6.5)
                        continue
                    self.assertIsNotNone(ctrl.ratio_gemessen)
                    self.assertAlmostEqual(ctrl.ratio_gemessen, soll, delta=0.02)
                    self.assertEqual(ctrl.params.ratio, ctrl.ratio_gemessen)
                    self.assertIsNotNone(ctrl.result)
                    self.assertGreater(ctrl.result.p_max, 1.0)
        # ohne Zuendsignal: nicht bestimmbar, eingestellter Wert bleibt
        ctrl = runner.RunController(FakeLink([]), physics.DynoParams(ratio=6.5), auto_climate=False)
        self.assertIsNone(ctrl.ratio_aus_den_funken(np.full(50, 1200.0), np.zeros(50)))

    def test_ohne_ausrollen_unveraendert(self):
        fr = sim_frames(SimEngine(), 30)
        fr2 = sim_frames(SimEngine(), 30)
        self.assertEqual([(f.roll_hz, f.ign_hz) for f in fr], [(f.roll_hz, f.ign_hz) for f in fr2])
        e = SimEngine()
        self.assertFalse(e.ausrollen)
        self.assertEqual(e.loss_total(100.0), 0.5 + 0.0002 * 100.0 ** 2)


class Gui(unittest.TestCase):
    """Reiter Kalibrierung: vorhanden, Schalter wird gespeichert, Übernehmen setzt die Parameter."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("HOME", "QT_QPA_PLATFORM")}
        os.environ["HOME"] = self.tmp.name
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6 import QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from pyst import gui
        importlib.reload(gui)
        self.gui = gui
        self.w = gui.MainWindow()
        self.w.resize(1480, 900)
        self.w.show()

    def tearDown(self):
        close_window(self.w, self.app)
        self.w = None
        self.tmp.cleanup()
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_gui(self, sec, cond=None):
        end = time.time() + sec
        while time.time() < end:
            self.app.processEvents()
            if cond is not None and cond():
                return True
            time.sleep(0.01)
        return cond is None

    def test_reiter_schalter_uebernehmen(self):
        w, gui = self.w, self.gui
        k = w.page_kalib
        self.assertTrue(w.page_settings.isAncestorOf(k))                # Gruppe „Verluste – Ausrollversuch“
        self.assertEqual(w.settings_tabs.tabText(w.settings_tabs.indexOf(w.page_settings)), "Rolle/Lauf")
        self.assertFalse(k.enabled())                      # Vorgabe: aus, alles wie bisher
        k.an.setChecked(True)
        k.n_ende.setValue(4500)
        self.assertTrue(gui.load_settings()["ausrollen"]["an"])
        self.assertEqual(gui.load_settings()["ausrollen"]["n_ende"], 4500)
        self.assertFalse(k.apply_btn.isEnabled())

        n, dt, nm, kk = synth(m2=2.0)
        p0 = w.page_settings.params()
        res = ausrollen.analyse(n, dt, physics.DynoParams(n1=1600, ratio=RATIO), start=kk, n_meas=nm)
        self.assertTrue(res.ok, res.meldung)
        k.show_result(res, "Testlauf")
        self.assertTrue(k.apply_btn.isEnabled())
        self.assertIn("R²", k.values.text())
        self.assertGreater(len(k.c_punkte.getData()[0]), 10)
        self.assertEqual(w.page_settings.params().m0, p0.m0)  # erst auf Knopfdruck
        k.apply_btn.click()
        p = w.page_settings.params()
        self.assertAlmostEqual(p.m0, res.m0, places=2)
        self.assertAlmostEqual(p.m1, res.m1, places=2)
        self.assertEqual(p.n1, res.n1)
        self.assertAlmostEqual(p.m2, res.m2, places=3)
        self.assertEqual(p.verlust_quelle, res.quelle())
        saved = gui.load_settings()
        self.assertAlmostEqual(saved["params"]["m1"], res.m1, places=2)
        self.assertEqual(saved["params"]["verlust_quelle"], res.quelle())
        self.assertTrue(saved["ausrollen"]["uebernommen"]["ok"])
        self.assertIn("Ausrollversuch", k.current_label.text())
        self.assertIn("Ausrollversuch", w.page_settings.loss_label.text())
        # von Hand geaendert -> Quelle "von Hand"
        w.page_settings.fields["m0"].setValue(1.0)
        self.assertEqual(w.page_settings.params().verlust_quelle, "")
        self.assertIn("von Hand", k.current_label.text())
        # nach dem Neustart: Schalter, letztes Ergebnis und Verluste wieder da
        close_window(w, self.app)
        self.w = gui.MainWindow()
        self.assertTrue(self.w.page_kalib.enabled())
        self.assertIsNotNone(self.w.page_kalib.result)
        self.assertAlmostEqual(self.w.page_settings.params().m1, res.m1, places=2)

    def test_alter_reiterstand(self):
        w = self.w
        w.settings["fenster"] = {"reiter_neu": [5, 3]}         # Version ohne Kalibrierung: 5 = Einstellungen
        w.restore_window_layout()
        self.assertIs(w.tabs.currentWidget(), w.settings_tabs)
        self.assertIs(w.current_page(), w.page_hw)                     # Tasten stecken im Unterreiter Zange
        w.show_page(w.page_kalib)
        w.save_window_layout()
        w.show_page(w.page_db)
        w.restore_window_layout()
        self.assertIs(w.current_page(), w.page_settings)               # Ausrollversuch steckt in Rolle/Lauf

    def test_lauf_mit_ausrollen(self):
        """Ganzer Ablauf gegen SIM:ausrollen: Hinweis, Ausrollen, Auswertung im Reiter Kalibrierung."""
        w, gui = self.w, self.gui
        w.page_kalib.an.setChecked(True)
        w.page_kalib.t_max.setValue(5)                      # nur kurz ausrollen: Ergebnis = Meldung
        w.page_settings.fields["n_stop"].setValue(0)
        w.page_settings.port_combo.setEditText("SIM:ausrollen")
        self.assertTrue(w.on_connect())
        w.on_start()
        self.assertTrue(self.run_gui(60, lambda: w.ctrl.state == gui.COAST))
        self.run_gui(0.3)
        self.assertIn("KUPPLUNG ZIEHEN", w.status.text())
        self.assertIn("Ausrollen beenden", w.start_btn.text())     # START ist im Lauf der Abbrechen-Knopf
        self.assertTrue(w.start_btn.isEnabled())
        self.assertTrue(self.run_gui(20, lambda: w.ctrl.state == gui.DONE and w._done_handled))
        self.assertIsNotNone(w.ctrl.coast)
        self.assertIs(w.page_kalib.result, w.ctrl.coast)
        self.assertIn("Ausrollversuch", w.result_label.text())
        meta_path = os.path.join(w.db.runs()[0].path)
        folder = meta_path if os.path.isdir(meta_path) else os.path.dirname(meta_path)
        with open(os.path.join(folder, "lauf.json"), encoding="utf-8") as fh:
            self.assertIn("ausrollen", json.load(fh))
        self.assertEqual(w.page_kalib.result_rid, w.db.runs()[0].id)
        self.assertEqual(w.start_btn.text(), "START  (F1)")


if __name__ == "__main__":
    unittest.main()
