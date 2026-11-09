"""Rollen-Kalibrierung (Assistent, Rad abgehoben, Rolle allein): analyse ohne Kupplung, Simulator 'SIM:rolle',
Aufnahme und Speichern, Ablauf der Seiten, „Als Rollen-Kalibrierung speichern“ (nicht in M0/M1), Speichern und
Laden der Kalibrierung über den Neustart und Sperre gegen den Leistungslauf (offscreen).

    python -m pytest tests/test_ausroll_assistent.py
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
from pyst import ausrollen, link, physics, storage  # noqa: E402
from pyst import ausroll_assistent as aa  # noqa: E402
from pyst.sim import RadAbEngine, SimSerial, rad_ab_engine  # noqa: E402

RATIO = 6.85


def auslauf(m0=6.0, m1=25.0, n1=1600.0, m2=4.0, inertia=13.5, rate=60.0, n_start=1650.0, n_end=440.0,
            noise=0.002, t_antrieb=0.0, antrieb=30.0, seed=3, t_max=None):
    """Frei auslaufende Rolle ab Index 0 (= „Messung starten“): die ersten t_antrieb Sekunden treibt das Rad noch
    (antrieb Nm, Rad nicht ganz abgehoben). Liefert (n_roll, dt)."""
    rng = np.random.default_rng(seed)
    dt = 1.0 / rate
    w, t, sub = n_start * physics.RAD_PER_RPM_S, 0.0, 20
    n = []
    while True:
        acc = 0.0
        for _ in range(sub):
            mv = physics.loss_torque(w / physics.RAD_PER_RPM_S, m0, m1, n1, m2) - (antrieb if t < t_antrieb else 0.0)
            w = max(0.0, w - mv / inertia * dt / sub)
            t += dt / sub
            acc += w
        nr = acc / sub / physics.RAD_PER_RPM_S
        n.append(nr)
        if nr < n_end or (t_max and t > t_max):
            break
    n = np.array(n) * (1 + rng.normal(0, noise, len(n)))
    return n, np.full(len(n), dt)


def sim_lauf(engine, seconds):
    """Telegramme des Simulators (ohne Echtzeit) und je Telegramm, ob das Rad abgehoben ist."""
    s = SimSerial(engine=engine, realtime=False)
    s.write(b"m\n")
    ab = []
    for _ in range(int(seconds * engine.rate)):
        s.tick()
        ab.append(engine.rad_ab)
    frames = [link.parse_frame(line, 0) for line in s.read(10 ** 9).decode().strip().split("\n")]
    s.close()
    return frames, np.array(ab[:len(frames)])


class Feeder:
    """Simulator 'SIM:rolle' ohne Echtzeit, Telegramme in Paketen (wie der GUI-Takt) an den Assistenten."""

    def __init__(self, dlg):
        self.dlg = dlg
        self.engine = rad_ab_engine()
        self.ser = SimSerial(engine=self.engine, realtime=False)
        self.ser.write(b"m\n")

    def feed(self, seconds=None, bis=None, max_s=400.0):
        n = 0
        while True:
            self.ser.tick()
            n += 1
            if n % 6 == 0:
                lines = self.ser.read(10 ** 9).decode().strip().split("\n")
                self.dlg.telegramme([link.parse_frame(x, time.time()) for x in lines if x])
                self.dlg.takt()
            if seconds is not None and n >= seconds * self.engine.rate:
                return
            if bis is not None and bis(self.engine):
                return
            if n > max_s * self.engine.rate:
                raise AssertionError("Bedingung im Simulator nicht erreicht")

    def n_roll(self):
        return self.engine.w_roll / physics.RAD_PER_RPM_S

    def close(self):
        self.ser.close()


# ================================================================================================ ohne Qt
class OhneKupplung(unittest.TestCase):
    GRID = np.array([500.0, 1000.0, 1500.0])

    def test_quadratisch_bekanntes_modell(self):
        n, dt = auslauf()
        nm = n * RATIO                                     # Zuendung folgt der Rolle: wuerde „Kupplung nicht gezogen“
        r = ausrollen.analyse(n, dt, physics.DynoParams(n1=1600, ratio=RATIO), start=0, n_meas=nm,
                              n_ende=3000.0, kupplung=False)
        self.assertTrue(r.ok, r.meldung)
        self.assertEqual(r.art, ausrollen.ART_ROLLE)
        self.assertTrue(r.rolle_allein)
        self.assertEqual(r.kupplung, "")
        self.assertAlmostEqual(r.m0, 6.0, delta=0.3)
        self.assertAlmostEqual(r.m1, 25.0, delta=0.4)
        self.assertAlmostEqual(r.m2, 4.0, delta=0.5)
        np.testing.assert_allclose(r.mv(self.GRID), physics.loss_torque(self.GRID, 6.0, 25.0, 1600.0, 4.0), rtol=0.02)
        self.assertGreater(r.r2, 0.98)
        self.assertGreater(r.n_bis * RATIO, 2990.0)        # n_ende = Motordrehzahl Rolle x i
        self.assertIn("Rollen-Kalibrierung", r.quelle())
        self.assertNotIn("Zündsignal", " ".join(r.hinweise))
        # mit Kupplung (bisheriger Weg) waere dasselbe nicht auswertbar: Zuendung folgt der Rolle
        rk = ausrollen.analyse(n, dt, physics.DynoParams(n1=1600, ratio=RATIO), start=0, n_meas=nm)
        self.assertFalse(rk.ok)
        self.assertEqual(rk.art, ausrollen.ART_KUPPLUNG)

    def test_linear(self):
        n, dt = auslauf(m2=0.0, seed=5)
        r = ausrollen.analyse(n, dt, physics.DynoParams(n1=1600, ratio=RATIO), start=0, quadratisch=False,
                              kupplung=False)
        self.assertTrue(r.ok, r.meldung)
        self.assertAlmostEqual(r.m0, 6.0, delta=0.15)
        self.assertAlmostEqual(r.m1, 25.0, delta=0.3)
        self.assertEqual(r.m2, 0.0)

    def test_uebergang_am_anfang_verworfen(self):
        """Rad beim „Messung starten“ noch nicht ganz frei: Anfang verworfen (bremst nur halb bzw. treibt an)."""
        for antrieb in (15.0, 40.0):
            with self.subTest(antrieb=antrieb):
                n, dt = auslauf(t_antrieb=1.0, antrieb=antrieb, m2=0.0)
                r = ausrollen.analyse(n, dt, physics.DynoParams(n1=1600, ratio=RATIO), start=0, quadratisch=False,
                                      kupplung=False)
                self.assertTrue(r.ok, r.meldung)
                self.assertGreaterEqual(r.i_von, int(0.8 * 60))            # erst nach dem Uebergang ausgewertet
                self.assertLess(r.i_von, 4 * 60)
                np.testing.assert_allclose(r.mv(self.GRID), physics.loss_torque(self.GRID, 6.0, 25.0, 1600.0),
                                           rtol=0.02)

    def test_fehlerfaelle_texte(self):
        p = physics.DynoParams(ratio=RATIO)
        n, dt = auslauf(t_max=2.0)
        r = ausrollen.analyse(n, dt, p, start=0, kupplung=False)
        self.assertFalse(r.ok)
        self.assertIn("zu kurz", r.meldung)
        self.assertIn("Rolle länger frei auslaufen", r.meldung)
        n, dt = auslauf(m0=-3.0, m1=-6.0, m2=0.0, n_end=0.0, t_max=10.0)
        r = ausrollen.analyse(n, dt, p, start=0, kupplung=False)
        self.assertFalse(r.ok)
        self.assertIn("Rad noch auf der Rolle", r.meldung)
        r = ausrollen.analyse(n[:20], dt[:20], p, start=0, kupplung=False)
        self.assertIn("Messung starten", r.meldung)

    def test_speichern_art(self):
        n, dt = auslauf()
        r = ausrollen.analyse(n, dt, physics.DynoParams(ratio=RATIO), kupplung=False, datum="2026-11-09T18:00:00")
        d = json.loads(json.dumps(r.als_dict(punkte=True)))
        self.assertEqual(d["art"], "rolle")
        r2 = ausrollen.AusrollErgebnis.aus_dict(d)
        self.assertTrue(r2.rolle_allein)
        self.assertEqual(r2.quelle(), r.quelle())
        self.assertTrue(r.quelle().startswith("Rollen-Kalibrierung 09.11.2026 18:00, R² 0,9"))
        alt = {k: v for k, v in d.items() if k != "art"}           # gespeichert von 1.4.1: Kupplung
        self.assertFalse(ausrollen.AusrollErgebnis.aus_dict(alt).rolle_allein)
        k = ausrollen.RollenKalibrierung.aus_ergebnis(r, "/tmp/x")
        self.assertEqual((k.m0, k.m1, k.n1, k.m2, k.datum, k.rohdaten), (r.m0, r.m1, r.n1, r.m2, r.datum, "/tmp/x"))
        self.assertEqual(k.quelle(), r.quelle())


class Simulator(unittest.TestCase):
    def test_rolle_laeuft_frei_aus_mit_bekanntem_verlust(self):
        e = RadAbEngine()
        frames, ab = sim_lauf(e, 125)
        p = physics.DynoParams()
        n = np.array([f.roll_hz for f in frames]) * 60 / p.inkr
        dt = np.array([1 / f.rate for f in frames])
        nm = np.array([f.ign_hz for f in frames]) * 60 / p.imp
        i_ab = int(np.argmax(ab))
        self.assertTrue(ab[i_ab])
        self.assertGreater(n[i_ab] * e.ratio, 0.95 * e.n_lift)      # oben gehalten, dann Rad ab
        j = i_ab + int(3 * e.rate)
        self.assertLess(nm[j], 0.5 * n[j] * e.ratio)                  # Motor im Leerlauf, Rolle laeuft weiter
        start = i_ab + int(e.rate)                                    # „Messung starten“ 1 s nach dem Abheben
        r = ausrollen.analyse(n[start:], dt[start:], p, n_ende=3000.0, kupplung=False)
        self.assertTrue(r.ok, r.meldung)
        g = np.linspace(r.n_bis, r.n_von, 6)
        np.testing.assert_allclose(r.mv(g), [e.loss_rolle(x * physics.RAD_PER_RPM_S) for x in g], rtol=0.02)
        self.assertAlmostEqual(r.m0, e.rolle_m0, delta=0.5)
        self.assertAlmostEqual(r.m2, e.rolle_m2, delta=0.6)
        self.assertAlmostEqual(float(r.mv(e.rolle_n1)), e.rolle_m1, delta=0.6)

    def test_schleife_rad_wieder_drauf(self):
        e = RadAbEngine(t_oben=1.0, n_wieder=6000.0)
        frames, ab = sim_lauf(e, 100)
        wechsel = np.nonzero(np.diff(ab.astype(int)))[0]
        self.assertGreaterEqual(len(wechsel), 2)                      # ab und wieder drauf (Nochmal)

    def test_port(self):
        from pyst.sim import open_sim
        ser, replay = open_sim("SIM:rolle")
        try:
            self.assertIsNone(replay)
            self.assertIsInstance(ser.engine, RadAbEngine)
        finally:
            ser.close()
        self.assertIn("SIM:rolle", link.SIM_NAMES)


class Aufnahme(unittest.TestCase):
    def frames(self, n_roll, rate=60.0):
        p = physics.DynoParams()
        return [link.Frame(t=0.0, cycle=i, rate=rate, ign_hz=25.0, roll_hz=x * p.inkr / 60.0)
                for i, x in enumerate(n_roll)]

    def test_ende(self):
        p = physics.DynoParams(ratio=RATIO)
        a = aa.AuslaufAufnahme(p, 3000.0, 120.0, now=0.0)
        self.assertAlmostEqual(a.n_ende_roll, 3000.0 / RATIO)
        self.assertEqual(a.add(self.frames(np.linspace(1500, 900, 120)), now=1.0), "")
        self.assertAlmostEqual(a.dauer(), 2.0)
        self.assertIn("ausgerollt", a.add(self.frames(np.linspace(900, 400, 120)), now=2.0))
        a = aa.AuslaufAufnahme(p, 0.0, 5.0, now=0.0)                  # bis Stillstand, t_max = Messzeit
        self.assertEqual(a.add(self.frames(np.linspace(1500, 1400, 240)), now=0.5), "")
        self.assertIn("nach 5 s", a.add(self.frames(np.linspace(1400, 1300, 120)), now=0.6))
        a = aa.AuslaufAufnahme(p, 0.0, 120.0, now=0.0)
        a.add(self.frames(np.linspace(1500, 1400, 60)), now=0.5)
        self.assertIn("keine Messdaten", a.add([], now=0.5 + aa.COAST_STILL_S + 0.1))
        a = aa.AuslaufAufnahme(p, 0.0, 120.0, now=0.0)
        a.add(self.frames(np.linspace(1500, 1400, 60)), now=0.1)
        self.assertIn("steigt wieder", a.add(self.frames(np.linspace(1400, 1500, 60)), now=0.2))

    def test_ziel_und_mindest(self):
        self.assertEqual(aa.ziel_standard(3000.0, RATIO), 1100.0)
        self.assertEqual(aa.ziel_standard(0.0, RATIO), 800.0)
        self.assertGreater(aa.ziel_standard(3000.0, RATIO), 2 * 3000.0 / RATIO)
        self.assertAlmostEqual(aa.mindest_drehzahl(1100.0, 3000.0 / RATIO), 770.0)
        self.assertGreaterEqual(aa.mindest_drehzahl(600.0, 438.0), 1.4 * 438.0)

    def test_speichern_und_nachrechnen(self):
        e = RadAbEngine()
        frames, ab = sim_lauf(e, 125)
        start = int(np.argmax(ab)) + int(e.rate)
        p = physics.DynoParams()
        a = aa.AuslaufAufnahme(p, 3000.0, 120.0, now=0.0)
        for k in range(start, len(frames), 6):
            if a.add(frames[k:k + 6], now=0.0):
                break
        self.assertIn("ausgerollt", a.grund)
        r = a.auswerten()
        self.assertTrue(r.ok, r.meldung)
        with tempfile.TemporaryDirectory() as d:
            folder = aa.speichern(d, a, r, {"ziel_rolle": 1100.0})
            self.assertTrue(os.path.basename(folder).endswith("_Rollen-Kalibrierung"))
            with open(os.path.join(folder, "lauf.json"), encoding="utf-8") as fh:
                meta = json.load(fh)
            run = storage.load_any(folder)
        self.assertEqual(meta["art"], aa.ART)
        self.assertEqual(run["kind"], aa.ART)
        self.assertEqual(meta["ausrollen"]["art"], "rolle")
        self.assertEqual(meta["assistent"]["start_index"], 0)
        self.assertEqual(len(run["n_roll"]), len(a.frames))
        r2 = ausrollen.analyse(run["n_roll"], run["dt"], run["params"], start=0,
                               n_ende=meta["assistent"]["n_ende"], kupplung=False)
        self.assertAlmostEqual(r2.m1, r.m1, delta=0.01 * abs(r.m1))
        self.assertAlmostEqual(r2.m0, r.m0, delta=0.05)


# ================================================================================================ Oberflaeche
class FakeLink:
    port = "SIM:rolle"

    def __init__(self):
        self.keepalives = 0

    def streaming(self):
        return True

    def keepalive(self):
        self.keepalives += 1
        return True


class FakeCtrl:
    def __init__(self):
        self.link = FakeLink()
        self.state = "Bereit"


class Seiten(unittest.TestCase):
    """Assistent allein (ohne Hauptfenster): Seitenfolge, Sperren, Abbrechen, Ergebnis, Übernehmen, Nochmal."""

    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctrl = None
        self.lauf = False
        self.cfg = {"n_ende": 3000.0, "t_max": 120.0, "quadratisch": True}
        self.saves = 0
        self.verbunden = 0
        self.d = aa.AusrollAssistent(None, lambda: self.ctrl, lambda: physics.DynoParams(), self.cfg,
                                     self._save, lambda: self.tmp.name, self._verbinden, lambda: self.lauf)
        self.d.timer.stop()                                # Takt kommt vom Feeder
        self.d.show()
        self.uebernommen = []
        self.ausgewertet = []
        self.d.kalib_speichern.connect(lambda r: self.uebernommen.append(r))
        self.d.ausgewertet.connect(lambda r, t: self.ausgewertet.append((r, t)))

    def tearDown(self):
        self.d.close()
        self.d.deleteLater()
        self.app.processEvents()
        self.tmp.cleanup()

    def _save(self):
        self.saves += 1

    def _verbinden(self):
        self.verbunden += 1
        self.ctrl = FakeCtrl()
        return True

    def test_ablauf(self):
        d = self.d
        d.takt()
        self.assertEqual(d.schritt, aa.VORBEREITUNG)
        self.assertFalse(d.b_weiter.isEnabled())            # nicht verbunden
        self.assertIn("Kein Messboard", d.v_status.text())
        self.assertTrue(d.v_verbinden.isVisibleTo(d))
        d.v_verbinden.click()
        self.assertEqual(self.verbunden, 1)
        self.assertFalse(d.b_weiter.isEnabled())            # verbunden, aber noch keine Rollendaten
        self.assertIn("keine Rollendaten", d.v_status.text())
        fd = Feeder(d)
        fd.feed(0.5)
        self.assertTrue(d.b_weiter.isEnabled())
        self.assertIn("Rollendaten kommen", d.v_status.text())
        self.assertIn("438", d.v_einst.text())               # Ende bei Rolle n_ende / i
        d.b_weiter.click()
        self.assertEqual(d.schritt, aa.BESCHLEUNIGEN)
        self.assertEqual(d.ziel.value(), 1100.0)              # Standard aus n_ende
        d.ziel.setValue(1150.0)
        self.assertEqual(self.cfg["assistent_ziel"], 1150.0)
        fd.feed(bis=lambda e: e.phase == "wot" and fd.n_roll() > 800)
        self.assertFalse(d.b_weiter.isEnabled())              # Ziel noch nicht erreicht
        self.assertIn("bis zum Ziel", d.b_status.text())
        d.b_weiter.click()
        self.assertEqual(d.schritt, aa.BESCHLEUNIGEN)
        self.assertNotEqual(d.b_zahlen.rolle.text(), "–")
        fd.feed(bis=lambda e: fd.n_roll() > 1180)
        self.assertTrue(d.b_weiter.isEnabled())
        self.assertIn("Ziel erreicht", d.b_status.text())
        d.b_weiter.click()
        self.assertEqual(d.schritt, aa.RAD_AB)
        self.assertEqual(d.b_weiter.text(), "Messung starten")
        self.assertIn("Vorsicht", d.r_sicher.text())
        fd.feed(bis=lambda e: e.rad_ab and e.t_phase > 1.0)
        d.b_weiter.click()
        self.assertEqual(d.schritt, aa.MESSUNG)
        self.assertFalse(d.b_zurueck.isVisible())
        fd.feed(10.0)
        self.assertGreater(len(d.m_kurve.getData()[0]), 500)
        # Abbrechen: zurueck zum Beschleunigen, nichts gespeichert
        d.b_schliessen.click()
        self.assertEqual(d.schritt, aa.BESCHLEUNIGEN)
        self.assertIsNone(d.aufnahme)
        self.assertEqual(os.listdir(self.tmp.name), [])
        self.assertIn("abgebrochen", d.b_status.text())
        self.assertTrue(d.isVisible())
        # zu langsam: Messung starten unter der Mindestdrehzahl -> zurueck mit Hinweis
        d.zeige(aa.RAD_AB)
        fd.feed(bis=lambda e: fd.n_roll() < d.mindest() - 50)
        d.b_weiter.click()
        self.assertEqual(d.schritt, aa.BESCHLEUNIGEN)
        self.assertIn("Zu langsam", d.b_status.text())
        # nochmal: Rad wieder drauf, beschleunigen, messen bis n_ende
        fd.feed(bis=lambda e: not e.rad_ab and fd.n_roll() > 1180)
        self.assertTrue(d.b_weiter.isEnabled())
        d.b_weiter.click()
        fd.feed(bis=lambda e: e.rad_ab and e.t_phase > 1.0)
        d.b_weiter.click()
        self.assertEqual(d.schritt, aa.MESSUNG)
        fd.feed(bis=lambda e: d.schritt == aa.ERGEBNIS, max_s=200.0)
        fd.close()
        r = d.result
        self.assertTrue(r.ok, r.meldung)
        self.assertTrue(r.rolle_allein)
        e = fd.engine
        g = np.linspace(r.n_bis, r.n_von, 5)
        np.testing.assert_allclose(r.mv(g), [e.loss_rolle(x * physics.RAD_PER_RPM_S) for x in g], rtol=0.03)
        self.assertIn("R²", d.e_werte.text())
        self.assertIn("Rolle allein", d.e_werte.text())
        self.assertIn("M0/M1 bleiben", d.e_physik.text())
        self.assertIn("Rollen-Kalibrierung", d.titel.text())
        self.assertGreater(len(d.e_diagramm.c_punkte.getData()[0]), 10)
        self.assertEqual(len(self.ausgewertet), 1)
        self.assertIs(self.ausgewertet[0][0], r)
        ordner = [x for x in os.listdir(self.tmp.name) if x.endswith("_Rollen-Kalibrierung")]
        self.assertEqual(len(ordner), 1)
        self.assertIn(ordner[0], d.e_datei.text())
        self.assertEqual(r.rohdaten, os.path.join(self.tmp.name, ordner[0]))
        self.assertEqual(d.b_weiter.text(), "Als Rollen-Kalibrierung speichern")
        self.assertTrue(d.b_weiter.isEnabled())
        d.b_weiter.click()
        self.assertEqual(len(self.uebernommen), 1)
        self.assertIs(self.uebernommen[0], r)
        self.assertFalse(d.b_weiter.isEnabled())            # nur einmal
        self.assertIn("Als Rollen-Kalibrierung gespeichert", d.e_meldung.text())
        d.b_zurueck.click()                                  # Nochmal
        self.assertEqual(d.schritt, aa.BESCHLEUNIGEN)

    def test_lauf_sperrt(self):
        d = self.d
        self.ctrl = FakeCtrl()
        fd = Feeder(d)
        fd.feed(0.5)
        self.assertTrue(d.b_weiter.isEnabled())
        self.lauf = True
        d.takt()
        self.assertFalse(d.b_weiter.isEnabled())
        self.assertTrue(d.banner.isVisibleTo(d))
        d.zeige(aa.RAD_AB)
        self.assertFalse(d.messung_starten())
        fd.close()

    def test_esc_in_der_messung_bricht_ab(self):
        from PySide6 import QtCore, QtGui
        d = self.d
        self.ctrl = FakeCtrl()
        fd = Feeder(d)
        fd.feed(bis=lambda e: e.rad_ab and e.t_phase > 1.0)
        d.zeige(aa.RAD_AB)
        self.assertTrue(d.messung_starten())
        ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier)
        d.keyPressEvent(ev)
        self.assertTrue(d.isVisible())
        self.assertEqual(d.schritt, aa.BESCHLEUNIGEN)
        fd.close()


class Hauptfenster(unittest.TestCase):
    """Knopf in Einstellungen › Rolle/Lauf, Sperre gegen den Lauf, Übernehmen wie beim bisherigen Weg."""

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
        self.w.resize(1280, 800)
        self.w.show()

    def tearDown(self):
        if self.w.ausroll_dlg is not None:
            self.w.ausroll_dlg.close()
        close_window(self.w, self.app)
        self.w = None
        self.tmp.cleanup()
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_knopf_sperre_uebernehmen(self):
        w, gui = self.w, self.gui
        k = w.page_kalib
        self.assertTrue(w.page_settings.isAncestorOf(k.assist_btn))
        self.assertEqual(k.assist_btn.text(), "Rollen-Kalibrierung (Assistent) …")
        self.assertIn("noch nicht kalibriert", k.kalib_label.text())
        p_vorher = w.page_settings.params()
        w.page_settings.port_combo.setEditText("SIM:rolle")
        self.assertTrue(w.on_connect())
        w.link.drain_frames = lambda: []                     # Telegramme kommen vom Feeder (ohne Echtzeit)
        self.assertTrue(w.start_btn.isEnabled())
        k.assist_btn.click()
        d = w.ausroll_dlg
        self.assertIsNotNone(d)
        self.assertFalse(d.isModal())
        self.assertTrue(d.isVisible())
        k.assist_btn.click()                                 # schon offen: dasselbe Fenster
        self.assertIs(w.ausroll_dlg, d)
        # solange offen: kein Lauf (Knopf, F1, Taste der Zange)
        self.assertFalse(w.start_btn.isEnabled())
        w.on_start(nachfrage=False)
        self.assertNotIn(w.ctrl.state, gui.ACTIVE)
        self.assertIn("Assistent", w.status.text())
        d.timer.stop()
        fd = Feeder(d)
        fd.feed(0.5)
        d.b_weiter.click()
        fd.feed(bis=lambda e: fd.n_roll() > d.ziel.value() + 30)
        d.b_weiter.click()
        fd.feed(bis=lambda e: e.rad_ab and e.t_phase > 1.0)
        d.b_weiter.click()
        fd.feed(bis=lambda e: d.schritt == 4, max_s=200.0)
        fd.close()
        r = d.result
        self.assertTrue(r.ok, r.meldung)
        self.assertIs(k.result, r)                           # Gruppe „Letzter Ausrollversuch“
        self.assertIn("Rolle allein", k.source_label.text() + k.msg_label.text())
        self.assertEqual(k.apply_btn.text(), "Als Rollen-Kalibrierung speichern")
        self.assertTrue(os.path.isdir(os.path.join(w.db.runs_dir(), os.path.basename(d.ordner))))
        self.assertEqual(w.db.runs(), [])                    # kein Lauf gespeichert
        d.b_weiter.click()                                   # Als Rollen-Kalibrierung speichern
        p = w.page_settings.params()
        # nicht in M0/M1: die Verluste bleiben, wie sie waren
        self.assertEqual((p.m0, p.m1, p.n1, p.m2, p.verlust_quelle),
                         (p_vorher.m0, p_vorher.m1, p_vorher.n1, p_vorher.m2, p_vorher.verlust_quelle))
        # sondern als Rollen-Kalibrierung (DynoParams rk_*, einstellungen.json)
        self.assertAlmostEqual(p.rk_m0, r.m0, places=6)
        self.assertAlmostEqual(p.rk_m1, r.m1, places=6)
        self.assertEqual(p.rk_n1, r.n1)
        self.assertAlmostEqual(p.rk_m2, r.m2, places=6)
        self.assertEqual(p.rk_quelle, r.quelle())
        self.assertIn("vom ", k.kalib_label.text())
        self.assertIn("Nm", k.kalib_label.text())
        self.assertNotIn("Rolle allein", w.page_settings.loss_label.text())
        self.assertIn("Reifen an Rolle", k.current_label.text())
        saved = gui.load_settings()
        kal = saved[ausrollen.KALIB_KEY]
        self.assertAlmostEqual(kal["m1"], r.m1, places=6)
        self.assertEqual(kal["rohdaten"], d.ordner)
        self.assertEqual(kal["datum"], r.datum)
        self.assertNotIn("uebernommen", saved.get("ausrollen", {}))
        d.close()
        self.app.processEvents()
        self.assertIsNone(w.ausroll_dlg)
        self.assertTrue(w.start_btn.isEnabled())
        # Neustart: Kalibrierung wieder da, in params() und in der Anzeige
        close_window(w, self.app)
        self.w = w = gui.MainWindow()
        p2 = w.page_settings.params()
        self.assertAlmostEqual(p2.rk_m1, r.m1, places=6)
        self.assertEqual(p2.rk_quelle, r.quelle())
        self.assertIsNotNone(w.page_kalib.kalib)
        self.assertEqual(w.page_kalib.kalib.rohdaten, d.ordner)
        self.assertIn("vom ", w.page_kalib.kalib_label.text())

    def test_nicht_waehrend_eines_laufs(self):
        w, gui = self.w, self.gui
        w.page_settings.port_combo.setEditText("SIM:rolle")
        self.assertTrue(w.on_connect())
        w.ctrl.state = gui.RUN
        w._update_buttons()
        self.assertFalse(w.page_kalib.assist_btn.isEnabled())
        w.on_ausroll_assistent()
        self.assertIsNone(w.ausroll_dlg)
        w.ctrl.state = gui.IDLE
        w._update_buttons()
        self.assertTrue(w.page_kalib.assist_btn.isEnabled())
        w.on_ausroll_assistent()
        self.assertIsNotNone(w.ausroll_dlg)
        w._taste_lauf()                                      # „Neuer Lauf“ an der Zange: abgelehnt
        self.assertNotIn(w.ctrl.state, gui.ACTIVE)
        self.assertIn("Rollen-Kalibrierung (Assistent) offen", w.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
