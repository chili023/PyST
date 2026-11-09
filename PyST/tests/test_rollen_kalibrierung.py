"""Rollen-Kalibrierung (Verlust der Rolle allein, einmal je Prüfstand) und Aufteilung des Ausrollversuchs nach dem Lauf:
gesamt = Rolle + Reifen/Kette/Getriebe, Differenz < 0, ohne Kalibrierung wie bisher, Reifen an Rolle in der
Leistungsrechnung, lauf.json (alt und neu), Simulator SIM:rolle gegen SIM:ausrollen, Anzeige in Einstellungen ›
Rolle/Lauf und Speichern über den Neustart (offscreen).

    python -m pytest tests/test_rollen_kalibrierung.py
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from conftest import close_window  # noqa: E402
from pyst import ausrollen, link, physics, runner, storage  # noqa: E402
from pyst.sim import SimSerial, ausroll_engine, rad_ab_engine  # noqa: E402
from test_ausrollen import RATIO, synth  # noqa: E402

KAL = dict(m0=1.5, m1=8.0, n1=1600.0, m2=1.0)          # Rolle allein, deutlich unter den Verlusten gesamt von synth


def kalib(**kw) -> ausrollen.RollenKalibrierung:
    d = dict(KAL, datum="2026-11-09T18:00:00", r2=0.998, rms_nm=0.12, n_von=1650.0, n_bis=450.0, ratio=RATIO,
             rohdaten="/daten/messungen/261109_180000_Rollen-Kalibrierung")
    d.update(kw)
    return ausrollen.RollenKalibrierung(**d)


def gesamt(**kw) -> ausrollen.AusrollErgebnis:
    """Ausrollversuch nach dem Lauf (Kupplung) mit M0 4 / M1 20 Nm bei 1600 1/min, m2 2."""
    a = dict(m0=4.0, m1=20.0, n1=1600.0, m2=2.0)
    a.update(kw)
    n, dt, nm, k = synth(**a)
    r = ausrollen.analyse(n, dt, physics.DynoParams(n1=1600, ratio=RATIO), start=k, n_meas=nm)
    assert r.ok, r.meldung
    return r


def sim_frames(engine, seconds):
    s = SimSerial(engine=engine, realtime=False)
    s.write(b"m\n")
    for _ in range(int(seconds * engine.rate)):
        s.tick()
    frames = [link.parse_frame(line, 0) for line in s.read(10 ** 9).decode().strip().split("\n")]
    s.close()
    return frames


def arrays(frames, p):
    return (np.array([f.roll_hz for f in frames]) * 60 / p.inkr, np.array([1 / f.rate for f in frames]),
            np.array([f.ign_hz for f in frames]) * 60 / p.imp)


# ================================================================================================ ohne Qt
class Kalibrierung(unittest.TestCase):
    def test_dict_hin_und_zurueck(self):
        k = kalib()
        d = json.loads(json.dumps(k.als_dict()))
        k2 = ausrollen.RollenKalibrierung.aus_dict(d)
        self.assertEqual(asdict(k2), asdict(k))
        self.assertEqual(d["quelle"], "Rollen-Kalibrierung 09.11.2026 18:00, R² 0,998")
        self.assertEqual(k2.rohdaten, k.rohdaten)
        for leer in (None, {}, {"m0": 0, "m1": 0}, {"m0": "x"}, [], "kaputt"):
            with self.subTest(leer=leer):
                self.assertIsNone(ausrollen.RollenKalibrierung.aus_dict(leer))

    def test_kurztext_und_params(self):
        k = kalib()
        t = k.kurztext()
        self.assertTrue(t.startswith("vom 09.11.2026, Verlust bei Rolle 1600 1/min (Motor 10960) = 8,0 Nm / "), t)
        self.assertIn("PS", t)
        p = k.in_params(physics.DynoParams(m0=3.0, m1=7.0, n1=1500.0, verlust_quelle="Ausrollversuch X"))
        self.assertEqual((p.rk_m0, p.rk_m1, p.rk_n1, p.rk_m2), (1.5, 8.0, 1600.0, 1.0))
        self.assertEqual((p.m0, p.m1, p.n1, p.verlust_quelle), (3.0, 7.0, 1500.0, "Ausrollversuch X"))
        self.assertEqual(p.rk_quelle, k.quelle())
        k2 = ausrollen.RollenKalibrierung.aus_params(p)
        np.testing.assert_allclose(k2.mv([0, 800, 1600]), k.mv([0, 800, 1600]))
        self.assertIsNone(ausrollen.RollenKalibrierung.aus_params(physics.DynoParams()))
        self.assertIsNone(ausrollen.RollenKalibrierung.aus_params(None))


class Aufteilung(unittest.TestCase):
    def test_gesamt_gleich_rolle_plus_antrieb(self):
        r, k = gesamt(), kalib()
        a = ausrollen.aufteilen(r, k)
        self.assertIsNotNone(a)
        g = np.linspace(r.n_bis, r.n_von, 7)
        np.testing.assert_allclose(a.mv_rolle(g) + a.mv_antrieb(g), r.mv(g))
        np.testing.assert_allclose(a.mv_antrieb(g), physics.loss_torque(g, 4.0, 20.0, 1600.0, 2.0)
                                   - physics.loss_torque(g, **KAL), atol=0.4)
        self.assertIsNone(a.negativ_bereich())
        self.assertEqual(a.hinweis(), "")
        namen = [n for n, _ in a.bezugspunkte(10000.0)]
        self.assertEqual(namen[0], "bei Pmax")
        self.assertEqual(namen[-1], "oben")
        self.assertAlmostEqual(a.bezugspunkte(10000.0)[0][1], 10000.0 / RATIO)
        w = a.werte(1600.0)
        self.assertAlmostEqual(w["gesamt"], w["rolle"] + w["antrieb"])
        self.assertAlmostEqual(w["rolle"], 8.0)
        self.assertAlmostEqual(w["gesamt_ps"], float(ausrollen.verlust_ps(1600.0, w["gesamt"])))
        t = a.text(10000.0)
        self.assertIn("Rolle", t)
        self.assertIn("Reifen/Kette/Getriebe", t)
        self.assertIn("bei Pmax", t)
        d = json.loads(json.dumps(a.als_dict(10000.0)))
        self.assertEqual(d["rollen_kalibrierung"], k.quelle())
        self.assertEqual(d["punkte"][0]["bezug"], "bei Pmax")

    def test_differenz_negativ(self):
        r = gesamt()
        k = kalib(m0=12.0, m1=24.0)                       # Rolle allein „verliert mehr als alles zusammen“
        a = ausrollen.aufteilen(r, k)
        b = a.negativ_bereich()
        self.assertIsNotNone(b)
        self.assertLess(float(np.min(a.mv_antrieb(np.linspace(r.n_bis, r.n_von, 30)))), 0.0)
        self.assertIn("rechnerisch negativ", a.hinweis())
        self.assertIn("wiederholen", a.hinweis())
        from pyst import kalib_page
        html = kalib_page.werte_html(r, k)
        self.assertIn("&lt; 0", html)

    def test_ohne_kalibrierung_wie_bisher(self):
        r = gesamt()
        self.assertIsNone(ausrollen.aufteilen(r, None))
        rolle = ausrollen.analyse(*synth(m0=2.0, m1=9.0)[:2], physics.DynoParams(ratio=RATIO), kupplung=False)
        self.assertIsNone(ausrollen.aufteilen(rolle, kalib()))   # Rad abgehoben: nichts aufzuteilen
        self.assertIsNone(ausrollen.aufteilen(ausrollen.AusrollErgebnis(ok=False), kalib()))
        from pyst import kalib_page
        self.assertIn(kalib_page.OHNE_KALIB, kalib_page.werte_html(r))
        self.assertNotIn("Aufteilung der Verluste", kalib_page.werte_html(r))
        self.assertIn("Aufteilung der Verluste", kalib_page.werte_html(r, kalib(), 10000.0))
        # Text des Ablaufs: ohne Kalibrierung genau wie bisher
        ctrl = runner.RunController(None, physics.DynoParams())
        ctrl.coast = r
        self.assertNotIn("Reifen/Kette", ctrl.coast_text())
        ctrl.params = kalib().in_params(physics.DynoParams())
        self.assertIn("Reifen/Kette/Getriebe", ctrl.coast_text())


class Leistung(unittest.TestCase):
    """Reifen an Rolle = Rad + Verlust der Rolle; Antriebsverlust = Motor - Reifen an Rolle; Rad/Motor unveraendert."""

    @classmethod
    def setUpClass(cls):
        cls.engine = ausroll_engine()
        cls.frames = sim_frames(cls.engine, 40)

    def test_reifen_an_rolle(self):
        p0 = physics.DynoParams(n_stop=0, m0=10.0, m1=40.0, n1=1600.0, m2=3.0, verlust_quelle="Ausrollversuch T")
        n, dt, nm = arrays(self.frames, p0)
        ohne = physics.evaluate(n, dt, p0, nm)
        self.assertFalse(ohne.rolle_kalibriert)
        self.assertIsNone(ohne.ps_reifen)
        self.assertNotIn("Reifen an Rolle", ohne.summary())
        p = kalib().in_params(p0)
        mit = physics.evaluate(n, dt, p, nm)
        self.assertTrue(mit.rolle_kalibriert)
        np.testing.assert_array_equal(mit.ps, ohne.ps)              # Motor- und Radkurve bleiben
        np.testing.assert_array_equal(mit.ps_rad, ohne.ps_rad)
        self.assertEqual((mit.p_max, mit.p_rad_max), (ohne.p_max, ohne.p_rad_max))
        n_rf = physics.filter_roll(n, p.ma)[mit.segment]
        rolle = ausrollen.verlust_ps(n_rf, physics.loss_torque(n_rf, **KAL))
        np.testing.assert_allclose(mit.ps_reifen, mit.ps_rad + rolle, rtol=1e-9, atol=1e-9)
        self.assertGreater(mit.p_reifen_max, mit.p_rad_max)
        self.assertLess(mit.p_reifen_max, mit.p_max)
        j = int(np.argmax(mit.ps))
        self.assertAlmostEqual(mit.antrieb_ps_pmax, mit.ps[j] - mit.ps_reifen[j])
        self.assertGreater(mit.antrieb_ps_pmax, 0.0)
        s = mit.summary()
        self.assertIn("Reifen an Rolle", s)
        self.assertIn("Antriebsverlust (Reifen, Kette, Getriebe)", s)
        # ohne Verluste M0/M1 nur „Reifen an Rolle“
        r2 = physics.evaluate(n, dt, kalib().in_params(physics.DynoParams(n_stop=0)), nm)
        self.assertIn("Rad + Verlust der Rolle", r2.reifen_text())
        from pyst.channels import run_channels
        ch = run_channels({"n_roll": n, "dt": dt, "n_meas": nm, "afr": np.zeros(len(n)), "egt": np.zeros(len(n))},
                          mit, p)
        self.assertIn("Leistung Reifen an Rolle", ch)
        self.assertIn("Antriebsverlust", ch)

    def test_lauf_json_alt_und_neu(self):
        class FakeLink:
            port, lost = "SIM", 0

        class Ctrl:
            pass

        p = kalib().in_params(physics.DynoParams(n_stop=0, m0=10.0, m1=40.0, n1=1600.0))
        n, dt, nm = arrays(self.frames, p)
        c = Ctrl()
        c.link, c.params, c.frames, c.climate = FakeLink(), p, self.frames, None
        c.result = physics.evaluate(n, dt, p, nm)
        c.coast = gesamt()
        c.arrays = lambda: (n, dt, nm, np.zeros(len(n)), np.zeros(len(n)))
        with tempfile.TemporaryDirectory() as d:
            folder = storage.save_run(c, base_dir=d)
            with open(os.path.join(folder, "lauf.json"), encoding="utf-8") as fh:
                meta = json.load(fh)
            run = storage.load_any(folder)
            self.assertEqual(meta["parameter"]["rk_m1"], 8.0)
            self.assertIn("Rollen-Kalibrierung", meta["ergebnis"]["rollen_kalibrierung"])
            self.assertAlmostEqual(meta["ergebnis"]["p_reifen_max_ps"], c.result.p_reifen_max)
            self.assertIn("Reifen an Rolle", meta["ergebnis"]["text"])
            auf = meta["ausrollen"]["aufteilung"]
            self.assertEqual(auf["punkte"][0]["bezug"], "bei Pmax")
            self.assertEqual(run["params"].rk_m1, 8.0)
            self.assertTrue(storage.recompute(run).rolle_kalibriert)
            # alte Datei ohne die neuen Felder: laedt, ohne Kalibrierung
            for k in [k for k in meta["parameter"] if k.startswith("rk_")]:
                del meta["parameter"][k]
            with open(os.path.join(folder, "lauf.json"), "w", encoding="utf-8") as fh:
                json.dump(meta, fh)
            alt = storage.load_any(folder)
        self.assertEqual((alt["params"].rk_m0, alt["params"].rk_m1, alt["params"].rk_quelle), (0.0, 0.0, ""))
        self.assertFalse(storage.recompute(alt).rolle_kalibriert)


class Simulator(unittest.TestCase):
    def test_rolle_kleiner_als_gesamt(self):
        """SIM:rolle (Kalibrierung) und SIM:ausrollen (Lauf mit Kupplung) haben dieselbe Rolle: die Aufteilung trifft
        den Anteil Reifen/Kette des Simulators."""
        e = rad_ab_engine()
        s = SimSerial(engine=e, realtime=False)
        s.write(b"m\n")
        ab = []
        for _ in range(int(125 * e.rate)):
            s.tick()
            ab.append(e.rad_ab)
        fr = [link.parse_frame(x, 0) for x in s.read(10 ** 9).decode().strip().split("\n")]
        s.close()
        p = physics.DynoParams()
        n, dt, _nm = arrays(fr, p)
        start = int(np.argmax(ab)) + int(e.rate)
        rk = ausrollen.analyse(n[start:], dt[start:], p, n_ende=3000.0, kupplung=False)
        self.assertTrue(rk.ok, rk.meldung)
        k = ausrollen.RollenKalibrierung.aus_ergebnis(rk)

        a = ausroll_engine()
        n, dt, nm = arrays(sim_frames(a, 70), physics.DynoParams(n_stop=0))
        res = physics.evaluate(n, dt, physics.DynoParams(n_stop=0), nm)
        c = ausrollen.analyse(n, dt, physics.DynoParams(n_stop=0), start=res.end_index, n_meas=nm, n_ende=3000.0)
        self.assertTrue(c.ok, c.meldung)
        auf = ausrollen.aufteilen(c, k)
        g = np.linspace(max(c.n_bis, rk.n_bis), min(c.n_von, rk.n_von), 5)
        self.assertTrue(np.all(auf.mv_rolle(g) < auf.mv_gesamt(g)))
        self.assertIsNone(auf.negativ_bereich())
        soll = [a.loss_rad(x * physics.RAD_PER_RPM_S) for x in g]
        np.testing.assert_allclose(auf.mv_antrieb(g), soll, rtol=0.08, atol=0.4)


# ================================================================================================ Oberflaeche
class Oberflaeche(unittest.TestCase):
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
        close_window(self.w, self.app)
        self.w = None
        self.tmp.cleanup()
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_aufteilung_in_der_gruppe(self):
        w, gui = self.w, self.gui
        k = w.page_kalib
        self.assertIsNone(k.kalib)
        self.assertIn("noch nicht kalibriert", k.kalib_label.text())
        r = gesamt()
        k.show_result(r, "Testlauf", n_pmax=10000.0)
        self.assertEqual(k.kalib_label.text(), "Rollen-Kalibrierung: noch nicht kalibriert")
        self.assertIn("nur gesamt", k.values.text())
        self.assertEqual(len(k.c_rolle.getData()[0] or []), 0)
        self.assertEqual(len(k.c_antrieb.getData()[0] or []), 0)
        self.assertEqual(k.apply_btn.text(), "Übernehmen")
        # Rolle aus dem Assistenten: als Kalibrierung speichern statt M0/M1
        rolle = ausrollen.analyse(*synth(m0=1.5, m1=8.0, m2=1.0, n_end=440.0)[:2], physics.DynoParams(ratio=RATIO),
                                  kupplung=False)
        self.assertTrue(rolle.ok, rolle.meldung)
        p0 = w.page_settings.params()
        k.show_result(rolle, "Assistent")
        self.assertEqual(k.apply_btn.text(), "Als Rollen-Kalibrierung speichern")
        k.apply_btn.click()
        p = w.page_settings.params()
        self.assertEqual((p.m0, p.m1, p.m2), (p0.m0, p0.m1, p0.m2))
        self.assertAlmostEqual(p.rk_m1, rolle.m1)
        self.assertIn("vom ", k.kalib_label.text())
        self.assertIn(ausrollen.KALIB_KEY, gui.load_settings())
        # Ausrollversuch nach dem Lauf jetzt aufgeteilt: Werte und drei Kurven
        k.show_result(r, "Testlauf", n_pmax=10000.0)
        self.assertIn("Aufteilung der Verluste", k.values.text())
        self.assertIn("bei Pmax", k.values.text())
        self.assertGreater(len(k.c_fit.getData()[0]), 10)
        self.assertGreater(len(k.c_rolle.getData()[0]), 10)
        self.assertGreater(len(k.c_antrieb.getData()[0]), 10)
        x, y_ges = k.c_fit.getData()
        _x, y_rolle = k.c_rolle.getData()
        _x, y_ab = k.c_antrieb.getData()
        np.testing.assert_allclose(y_rolle + y_ab, y_ges, atol=1e-9)
        namen = [lab.text for _s, lab in k.diagramm.legende.items]
        self.assertIn("gesamt (Fit)", namen)
        self.assertIn("Rolle (Kalibrierung)", namen)
        self.assertIn("Reifen/Kette/Getriebe", namen)
        self.assertEqual(k.apply_btn.text(), "Übernehmen")
        # Übernehmen: Verluste gesamt in M0/M1 wie bisher, Kalibrierung bleibt
        k.apply_btn.click()
        p = w.page_settings.params()
        self.assertAlmostEqual(p.m1, r.m1, places=2)
        self.assertAlmostEqual(p.rk_m1, rolle.m1)
        self.assertIn("Antriebsverlust", k.current_label.text())
        # Differenz < 0: Hinweis, Kurve wie gerechnet mit Erklärung in der Legende
        k.set_kalibrierung(kalib(m0=12.0, m1=24.0))
        self.assertIn("rechnerisch negativ", k.msg_label.text())
        self.assertTrue(k.msg_label.isVisibleTo(k))
        self.assertTrue(any("unter 0" in lab.text for _s, lab in k.diagramm.legende.items))
        # Neustart: gespeicherte Kalibrierung (aus dem Assistenten) und letztes Ergebnis mit n_pmax wieder da
        close_window(w, self.app)
        self.w = w = gui.MainWindow()
        self.assertAlmostEqual(w.page_kalib.kalib.m1, rolle.m1)
        self.assertAlmostEqual(w.page_settings.params().rk_m1, rolle.m1)
        self.assertEqual(w.page_kalib.n_pmax, 10000.0)
        self.assertIn("Aufteilung der Verluste", w.page_kalib.values.text())

    def test_alter_pfad_rolle_nicht_in_m0(self):
        """Auch über _apply_coast (alter Weg) landet ein Ergebnis mit abgehobenem Rad nicht in M0/M1."""
        w = self.w
        rolle = ausrollen.analyse(*synth(m0=1.5, m1=8.0, n_end=440.0)[:2], physics.DynoParams(ratio=RATIO),
                                  kupplung=False)
        p0 = w.page_settings.params()
        w._apply_coast(rolle, None)
        p = w.page_settings.params()
        self.assertEqual((p.m0, p.m1, p.m2, p.verlust_quelle), (p0.m0, p0.m1, p0.m2, p0.verlust_quelle))
        self.assertAlmostEqual(p.rk_m1, rolle.m1)


if __name__ == "__main__":
    unittest.main()
