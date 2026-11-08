"""Mittelwert-Lauf (pyst.mittelwert): Rechnung, Setup-Warnung, Speichern/Laden, db.runs mit/ohne Mittelwert."""
import glob
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyst import mittelwert as mw  # noqa: E402
from pyst import physics, storage  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data")


def linear(n0, n1, a, b, step=10.0, **extra):
    """Lauf mit n von n0 bis n1 und ps = a + b * n/1000, nm = 2 * ps."""
    n = np.arange(n0, n1 + step / 2, step, dtype=float)
    ps = a + b * n / 1000.0
    d = {"n": n, "ps": ps, "nm": 2 * ps}
    for k, f in extra.items():
        d[k] = f(n)
    return d


class Rechnung(unittest.TestCase):
    def test_mittel_zweier_linearer_kurven_exakt(self):
        a = linear(3000, 9000, 1.0, 2.0)
        b = linear(3000, 9000, 3.0, 4.0)
        r = mw.mitteln([a, b])
        np.testing.assert_allclose(r.werte["ps"], 2.0 + 3.0 * r.n / 1000.0, rtol=0, atol=1e-9)
        np.testing.assert_allclose(r.werte["nm"], 2 * (2.0 + 3.0 * r.n / 1000.0), atol=1e-9)
        self.assertEqual(r.anzahl, 2)
        self.assertEqual(r.kanaele, ["ps", "nm"])

    def test_raster_ueberlappung_oben_weg(self):
        a = linear(3010, 9000, 1.0, 1.0)
        b = linear(2500, 8720, 1.0, 1.0)
        r = mw.mitteln([a, b])
        self.assertEqual(r.n_lo, 3010.0)
        self.assertEqual(r.n_hi, 8720.0)
        self.assertEqual(r.n[0], 3050.0)                  # erstes Vielfaches von 50 ab 3010
        self.assertEqual(r.n[-1], 8400.0)                 # 8720 - 300 = 8420 -> 8400
        np.testing.assert_allclose(np.diff(r.n), 50.0)
        r2 = mw.mitteln([a, b], raster=100.0, oben_weg=0.0)
        self.assertEqual((r2.n[0], r2.n[-1]), (3100.0, 8700.0))
        np.testing.assert_allclose(r2.n % 100.0, 0.0)
        r3 = mw.mitteln([a, b], raster=25.0, oben_weg=500.0)
        self.assertEqual((r3.n[0], r3.n[-1]), (3025.0, 8200.0))
        self.assertEqual((r3.raster, r3.oben_weg), (25.0, 500.0))

    def test_raster_genau_auf_grenze(self):
        a = linear(3000, 8300, 1.0, 1.0)
        b = linear(3000, 8300, 2.0, 1.0)
        r = mw.mitteln([a, b])
        self.assertEqual((r.n[0], r.n[-1]), (3000.0, 8000.0))   # beide Grenzen gehoeren dazu

    def test_abfall_nach_maximum_ignoriert(self):
        a = linear(3000, 9000, 1.0, 2.0)
        # nach 9000: Gas weg, Drehzahl faellt wieder, Leistung negativ -> darf nicht mitgemittelt werden
        n_ab = np.arange(8990, 5000, -10.0)
        a2 = {"n": np.r_[a["n"], n_ab], "ps": np.r_[a["ps"], np.full(len(n_ab), -50.0)],
              "nm": np.r_[a["nm"], np.full(len(n_ab), -50.0)]}
        b = linear(3000, 9000, 1.0, 2.0)
        r = mw.mitteln([a2, b])
        np.testing.assert_allclose(r.werte["ps"], 1.0 + 2.0 * r.n / 1000.0, atol=1e-9)

    def test_zacken_laufendes_maximum(self):
        """Kurzer Einbruch der Drehzahl mitten im Lauf: alle Punkte unter dem bisherigen Maximum fallen weg,
        nicht nur der erste (Nachbarvergleich wuerde 5500 und 5600 behalten)."""
        n = np.array([5000, 5200, 6000, 5500, 5600, 5700, 6100, 6200, 7000], float)
        ps = np.array([1, 2, 3, 99, 99, 99, 4, 5, 6], float)
        t = mw.steigender_teil({"n": n, "ps": ps})
        np.testing.assert_array_equal(t["n"], [5000, 5200, 6000, 6100, 6200, 7000])
        np.testing.assert_array_equal(t["ps"], [1, 2, 3, 4, 5, 6])
        # Wiederholte Drehzahl (gleicher Wert) zaehlt nicht als steigend
        t = mw.steigender_teil({"n": np.array([1.0, 2.0, 2.0, 3.0]), "ps": np.array([1.0, 2.0, 7.0, 3.0])})
        np.testing.assert_array_equal(t["ps"], [1, 2, 3])

    def test_zacken_beeinflussen_mittel_nicht(self):
        a = linear(3000, 9000, 1.0, 2.0)
        i = np.searchsorted(a["n"], 6000)
        a["n"][i:i + 5] = 5000.0                          # Zacke nach unten
        a["ps"][i:i + 5] = 99.0
        b = linear(3000, 9000, 1.0, 2.0)
        r = mw.mitteln([a, b])
        np.testing.assert_allclose(r.werte["ps"], 1.0 + 2.0 * r.n / 1000.0, atol=1e-9)

    def test_nan_in_drehzahl_und_kanal(self):
        a = linear(3000, 9000, 1.0, 2.0, egt1=lambda n: 500 + n / 100)
        b = linear(3000, 9000, 3.0, 2.0, egt1=lambda n: 600 + n / 100)
        a["n"][100:103] = np.nan
        a["ps"][200:210] = np.nan                         # Luecke im Kanal wird linear ueberbrueckt
        b["egt1"][:50] = np.nan                           # EGT erst ab 3500 -> Mittel erst ab dort
        r = mw.mitteln([a, b])
        np.testing.assert_allclose(r.werte["ps"], 2.0 + 2.0 * r.n / 1000.0, atol=1e-9)
        egt = r.werte["egt1"]
        self.assertTrue(np.isnan(egt[r.n < 3500]).all())
        ok = r.n >= 3500
        np.testing.assert_allclose(egt[ok], 550 + r.n[ok] / 100, atol=1e-9)
        self.assertFalse(np.isnan(r.werte["ps"]).any())

    def test_min_max_sigma(self):
        laeufe = [linear(3000, 8000, c, 0.0) for c in (10.0, 12.0, 17.0)]
        r = mw.mitteln(laeufe)
        np.testing.assert_allclose(r.werte["ps"], 13.0)
        np.testing.assert_allclose(r.minimum["ps"], 10.0)
        np.testing.assert_allclose(r.maximum["ps"], 17.0)
        np.testing.assert_allclose(r.sigma["ps"], np.std([10.0, 12.0, 17.0], ddof=1))
        lo, hi = r.band("ps")
        np.testing.assert_allclose((lo[0], hi[0]), (10.0, 17.0))
        lo, hi = r.band("ps", "sigma")
        np.testing.assert_allclose(hi - lo, 2 * np.std([10.0, 12.0, 17.0], ddof=1))
        self.assertIsNone(r.band("ps", ""))
        self.assertIsNone(r.band("gibtsnicht"))

    def test_pmax_aus_gemittelter_kurve(self):
        def buckel(n0, peak):
            n = np.arange(3000, 9001, 10.0)
            ps = 20.0 - ((n - n0) / 1000.0) ** 2 + peak
            return {"n": n, "ps": ps, "nm": ps * 7023.5 / n}
        r = mw.mitteln([buckel(6000, 0.0), buckel(7000, 0.0)])
        # Mittel: 20 - ((n-6000)^2 + (n-7000)^2) / 2e6 -> Maximum bei 6500 mit 20 - 0.25
        self.assertEqual(r.n_pmax, 6500.0)
        self.assertAlmostEqual(r.p_max, 19.75, places=9)
        i = int(np.argmax(r.werte["nm"]))
        self.assertEqual((r.m_max, r.n_mmax), (r.werte["nm"][i], r.n[i]))
        # ohne ps/nm bleiben die Maxima 0
        r = mw.mitteln([{"n": np.arange(3000, 5000.0), "lam": np.ones(2000)}] * 2)
        self.assertEqual((r.p_max, r.m_max), (0.0, 0.0))

    def test_fehlende_kanaele(self):
        a = linear(3000, 9000, 1.0, 2.0, egt1=lambda n: n * 0 + 700, lam=lambda n: n * 0 + 0.9)
        b = linear(3000, 9000, 1.0, 2.0, egt1=lambda n: n * 0 + 800)
        b["lam"] = np.full(len(b["n"]), np.nan)
        b["lam"][10] = 0.9                                # nur 1 endlicher Wert -> zaehlt als fehlend
        c = linear(3000, 9000, 1.0, 2.0, egt1=lambda n: n * 0 + 900, egt2=lambda n: n)
        r = mw.mitteln([a, b, c])
        self.assertEqual(r.kanaele, ["ps", "nm", "egt1"])
        self.assertEqual(r.fehlende_kanaele, ["lam", "egt2"])
        np.testing.assert_allclose(r.werte["egt1"], 800.0)
        self.assertTrue(any("lam" in w for w in r.warnungen))

    def test_fehler(self):
        a = linear(3000, 9000, 1.0, 2.0)
        with self.assertRaisesRegex(ValueError, "mindestens 2"):
            mw.mitteln([a])
        with self.assertRaisesRegex(ValueError, "mindestens 2"):
            mw.mitteln([])
        with self.assertRaisesRegex(ValueError, "keinen gemeinsamen"):
            mw.mitteln([linear(3000, 5000, 1, 1), linear(6000, 9000, 1, 1)])
        with self.assertRaisesRegex(ValueError, "keinen gemeinsamen"):    # Ueberlappung kleiner als oben_weg
            mw.mitteln([linear(3000, 6200, 1, 1), linear(6000, 9000, 1, 1)])
        with self.assertRaisesRegex(ValueError, "raster"):
            mw.mitteln([a, a], raster=0)
        with self.assertRaisesRegex(ValueError, "steigend"):
            mw.mitteln([a, {"n": np.array([5000.0, 4000.0]), "ps": np.array([1.0, 2.0])}])

    def test_quellen_und_setup_warnung(self):
        a, b = linear(3000, 9000, 1.0, 2.0), linear(3000, 9000, 1.0, 2.0)
        s1 = {"id": 1, "vehicle_id": 2, "name": "Serie", "notiz": "x", "zzp": "18", "hauptduese": "135"}
        s2 = {"id": 2, "vehicle_id": 2, "name": "Andere", "notiz": "y", "zzp": "17", "hauptduese": "135"}
        r = mw.mitteln([a, b], quellen=[4, 7], setups=[s1, s2])
        self.assertEqual(r.quellen, [4, 7])
        self.assertEqual(r.warnungen, ["Setups weichen ab: Zündzeitpunkt [° v. OT] 18 / 17"])
        r = mw.mitteln([a, b], setups=[s1, dict(s1, id=9, name="Kopie")])
        self.assertEqual(r.warnungen, [])

    def test_setup_abweichungen(self):
        s = [{"zzp": "18", "hauptduese": "135", "clip": 2.0, "name": "A"},
             {"zzp": 18.0, "hauptduese": "138", "clip": "2", "name": "B"},
             None,
             {"zzp": "18", "hauptduese": "135", "nadel": "J8", "clip": "2"}]
        ab = mw.setup_abweichungen(s)
        self.assertEqual(ab, {"hauptduese": ["135", "138", "?", "135"], "nadel": ["", "", "?", "J8"]})
        self.assertEqual(list(ab), ["hauptduese", "nadel"])          # Reihenfolge wie SETUP_FIELDS
        self.assertEqual(mw.setup_abweichungen(s, felder=["zzp", "clip"]), {})
        self.assertEqual(mw.setup_abweichungen([None, None]), {})
        w = mw.setup_warnung(s)
        self.assertEqual(w, "Setups weichen ab: Hauptdüse 135 / 138 / ? / 135; Nadel – / – / ? / J8")
        many = [{k: "1" for k in ("zzp", "clip", "nadel", "hauptduese")}, {k: "2" for k in ("zzp", "clip", "nadel",
                                                                                             "hauptduese")}]
        self.assertTrue(mw.setup_warnung(many, max_felder=2).endswith("(und 2 weitere)"))
        self.assertEqual(mw.setup_warnung([s[0], s[0]]), "")


class Adapter(unittest.TestCase):
    def test_kurven_aus_echtem_lauf(self):
        f = sorted(glob.glob(os.path.join(DATA, "*.xml")))[0]
        run = storage.load_any(f)
        res = storage.recompute(run)
        k = mw.kurven_aus_ergebnis(res, run)
        for key in ("n", "ps", "nm", "lam", "afr", "egt1"):
            self.assertEqual(len(k[key]), len(res.n), key)
        afr = np.asarray(res.afr, float)
        ok = afr > 0.5
        np.testing.assert_allclose(k["lam"][ok], afr[ok] / 14.7)
        self.assertTrue(np.isnan(k["lam"][~ok]).all())
        # zwei echte Laeufe mitteln: Pmax liegt zwischen den Einzelwerten (grob)
        files = sorted(glob.glob(os.path.join(DATA, "*.xml")))[:2]
        res2 = [storage.recompute(storage.load_any(p)) for p in files]
        r = mw.mitteln([mw.kurven_aus_ergebnis(x) for x in res2])
        self.assertGreater(len(r.n), 10)
        self.assertLessEqual(r.p_max, max(x.p_max for x in res2) + 1e-9)
        self.assertGreater(r.p_max, 0.8 * min(x.p_max for x in res2))

    def test_kurven_mit_spalten(self):
        n = np.linspace(3000, 9000, 50)
        res = physics.RunResult(n=n, ps=n / 1000, nm=n / 500, n_meas=n, afr=np.full(50, 13.23),
                                egt=np.r_[np.zeros(5), np.full(45, 700.0)], ka=1.0, start_index=10, stop=60)
        cols = {"egt2_c": np.r_[np.zeros(10), np.full(50, 650.0), np.zeros(5)],
                "rusefi_coolant": np.r_[np.zeros(10), np.full(50, 85.0), np.zeros(5)],
                "zu_kurz": np.zeros(3)}
        k = mw.kurven_aus_ergebnis(res, {"columns": cols})
        np.testing.assert_allclose(k["lam"], 0.9)
        self.assertTrue(np.isnan(k["egt1"][:5]).all())
        np.testing.assert_allclose(k["egt2"], 650.0)
        np.testing.assert_allclose(k["ecu:coolant"], 85.0)
        self.assertNotIn("zu_kurz", k)
        self.assertNotIn("ps_rad", k)


class Speichern(unittest.TestCase):
    def test_rundreise_und_runs(self):
        from pyst.db import Database, ART_MITTELWERT
        with tempfile.TemporaryDirectory() as d:
            db = Database(os.path.join(d, "pyst.db"))
            vid = db.save_vehicle({"hersteller": "Aprilia", "modell": "RS 125"})
            sid = db.save_setup(vid, "Serie", {"hauptduese": "135"})
            files = sorted(glob.glob(os.path.join(DATA, "*.xml")))[:2]
            ids = [db.import_file(f, vehicle_id=vid, setup_id=sid) for f in files]
            a = linear(3000, 9000, 1.0, 2.0, egt1=lambda n: 700 + n * 0, lam=lambda n: 0.9 + n * 0)
            b = linear(3000, 9000, 3.0, 2.0, egt1=lambda n: 720 + n * 0, lam=lambda n: 0.92 + n * 0)
            b["egt1"][:60] = np.nan
            r = mw.mitteln([a, b], quellen=ids, setups=[{"zzp": "18"}, {"zzp": "17"}])
            werte = [{"feld": "hauptduese", "wert": "135", "vorher": ""}]
            rid = mw.speichern(db, r, "Ø 2 Läufe · Serie HD 135", vehicle_id=vid, setup_id=sid, notiz="Bezug",
                               setup_werte=werte)
            row = db.run(rid)
            self.assertEqual(row.art, ART_MITTELWERT)
            self.assertTrue(row.ist_mittelwert)
            self.assertEqual(row.quelle, "Mittelwert")
            self.assertEqual(row.datei, "mittelwert.json")
            self.assertEqual(row.quellen, ids)
            self.assertEqual(row.fehler, {})                       # Fehler-Nachberechnung ueberspringt ihn
            self.assertEqual(row.setup_werte, werte)
            self.assertEqual((row.vehicle_id, row.setup_id, row.notiz), (vid, sid, "Bezug"))
            self.assertAlmostEqual(row.p_max, r.p_max)
            self.assertAlmostEqual(row.n_pmax, r.n_pmax)
            self.assertTrue(row.ordner.startswith(db.runs_dir()))
            for name in ("mittelwert.json", "mittelwert.csv"):
                self.assertTrue(os.path.exists(os.path.join(row.ordner, name)))
            with open(os.path.join(row.ordner, "mittelwert.csv")) as fh:
                head = fh.readline().strip().split(";")
            self.assertEqual(head[:5], ["n", "ps", "ps_min", "ps_max", "ps_sigma"])
            for quelle in (row, row.ordner, row.path, os.path.join(row.ordner, "mittelwert.csv")):
                g = mw.laden(quelle)
                np.testing.assert_allclose(g.n, r.n)
                for k in r.kanaele:
                    np.testing.assert_allclose(g.werte[k], r.werte[k], rtol=1e-5, equal_nan=True)
                    np.testing.assert_allclose(g.minimum[k], r.minimum[k], rtol=1e-5, equal_nan=True)
                    np.testing.assert_allclose(g.maximum[k], r.maximum[k], rtol=1e-5, equal_nan=True)
                    np.testing.assert_allclose(g.sigma[k], r.sigma[k], rtol=1e-5, atol=1e-9, equal_nan=True)
                self.assertEqual(g.kanaele, r.kanaele)
                self.assertEqual(g.quellen, ids)
                self.assertEqual((g.raster, g.oben_weg, g.n_lo, g.n_hi, g.anzahl),
                                 (r.raster, r.oben_weg, r.n_lo, r.n_hi, 2))
                self.assertEqual(g.warnungen, r.warnungen)
                self.assertAlmostEqual(g.p_max, r.p_max)
            self.assertTrue(np.isnan(g.werte["egt1"][0]))
            # db.runs: ohne mittelwert=True kein Mittelwert (load_any wuerde scheitern), mit ihm alle
            self.assertEqual({x.id for x in db.runs()}, set(ids))
            self.assertEqual({x.id for x in db.runs(vehicle_id=vid)}, set(ids))
            self.assertEqual({x.id for x in db.runs(setup_id=sid)}, set(ids))
            self.assertEqual({x.id for x in db.runs(mittelwert=True)}, set(ids) | {rid})
            self.assertEqual({x.id for x in db.runs(vehicle_id=vid, mittelwert=True)}, set(ids) | {rid})
            for x in db.runs():
                storage.load_any(x.path)
            with self.assertRaises(ValueError):
                storage.load_any(row.path)                         # deshalb die Trennung
            with self.assertRaises(ValueError):
                mw.laden(db.run(ids[0]).ordner)                    # normaler Lauf ist kein Mittelwert
            # Loeschen mit Dateien raeumt den Ordner weg
            db.delete_run(rid, delete_files=True)
            self.assertIsNone(db.run(rid))
            self.assertFalse(os.path.exists(row.ordner))


if __name__ == "__main__":
    unittest.main()
