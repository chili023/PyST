"""Regressionstests:  python -m unittest discover -s tests -v"""
import glob
import os
import re
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyst import link, lvxml, physics, storage  # noqa: E402
from pyst.sim import SimEngine, SimSerial  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data")


def sim_run(engine, seconds=30):
    s = SimSerial(engine=engine, realtime=False)
    s.write(b"m\n")
    for _ in range(int(seconds * engine.rate)):
        s.tick()
    frames = [link.parse_frame(l, 0) for l in s.read(10 ** 8).decode().strip().split("\n")]
    s.close()
    return frames


class LabviewReference(unittest.TestCase):
    """Echte LabVIEW-Laeufe vom 26.05.2026: Pmax/n(Pmax) stehen im Dateinamen."""

    def test_matches_labview(self):
        files = sorted(glob.glob(os.path.join(DATA, "*.xml")))
        self.assertTrue(files)
        for f in files:
            m = re.search(r"_ ?(\d+,\d)_(\d+)\.xml$", f)
            ref_p, ref_n = float(m.group(1).replace(",", ".")), int(m.group(2))
            r = lvxml.read_run(f)
            res = physics.evaluate(r["n_roll"], r["dt"], r["params"], r["n_meas"])
            with self.subTest(os.path.basename(f)):
                self.assertEqual(round(res.p_max, 1), ref_p)
                self.assertEqual(round(res.n_pmax), ref_n)


class Simulation(unittest.TestCase):
    def test_torque_recovered_without_losses(self):
        e = SimEngine(loss0=0, loss2=0, noise=0)
        fr = sim_run(e)
        p = physics.DynoParams(n_stop=0)
        n_roll = np.array([f.roll_hz for f in fr]) * 60 / p.inkr
        dt = np.array([1 / f.rate for f in fr])
        res = physics.evaluate(n_roll, dt, p)
        for n in (5000, 7000, 9500, 11000):
            j = int(np.argmin(abs(res.n - n)))
            self.assertAlmostEqual(res.nm[j], e.torque_wot(res.n[j]), delta=0.05)

    def test_n_stop(self):
        fr = sim_run(SimEngine())
        p = physics.DynoParams(n_stop=8000)
        n_roll = np.array([f.roll_hz for f in fr]) * 60 / p.inkr
        dt = np.array([1 / f.rate for f in fr])
        res = physics.evaluate(n_roll, dt, p)
        self.assertIn("n Stop", res.end_reason)
        self.assertLess(res.n.max(), 8000)
        self.assertGreater(res.n.max(), 7900)


class Files(unittest.TestCase):
    def test_labview_xml_roundtrip(self):
        src = sorted(glob.glob(os.path.join(DATA, "*.xml")))[0]
        r = lvxml.read_run(src)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "lauf.xml")
            lvxml.write_run(out, r["params"], r["n_roll"], r["n_meas"], r["afr"], r["egt"], r["dt"],
                            vehicle="Test äöü")
            back = lvxml.read_run(out)
        np.testing.assert_allclose(back["n_roll"], r["n_roll"])
        np.testing.assert_allclose(back["dt"], r["dt"])
        self.assertEqual(back["vehicle"], "Test äöü")
        a = physics.evaluate(r["n_roll"], r["dt"], r["params"])
        b = physics.evaluate(back["n_roll"], back["dt"], back["params"])
        self.assertAlmostEqual(a.p_max, b.p_max, places=6)

    def test_labview_321_variant_xml(self):
        """LabVIEW 3.2.1 speichert den Datenspeicher in <LvVariant>…</LvVariant>."""
        src = sorted(glob.glob(os.path.join(DATA, "*.xml")))[0]
        text = open(src, "rb").read().decode("latin-1")
        head, rest = text.split("<Cluster>", 1)
        body, tail = rest.rsplit("</Cluster>", 1)
        wrapped = head + "<LvVariant>\n<Name>Value</Name>\n<Cluster>" + body + "</Cluster>\n</LvVariant>" + tail
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "lv321.xml")
            with open(out, "wb") as fh:
                fh.write(wrapped.encode("latin-1"))
            a, b = lvxml.read_run(src), lvxml.read_run(out)
        np.testing.assert_allclose(a["n_roll"], b["n_roll"])
        self.assertEqual(a["params"], b["params"])

    def test_parse_frame(self):
        f = link.parse_frame("123;60.00;50.00;725.50;656;0.00;612", 0)
        self.assertEqual((f.cycle, f.rate, f.ign_hz, f.roll_hz, f.egt1, f.egt2), (123, 60.0, 50.0, 725.5, 656, 612))
        f = link.parse_frame("5;61.03;50;725", 0)          # Mega ohne AFR/EGT
        self.assertEqual(f.roll_hz, 725)
        self.assertIsNone(link.parse_frame("21.5;96512;45.3", 0))


class Database(unittest.TestCase):
    def test_vehicle_setup_run_import(self):
        from pyst.db import Database as DB
        with tempfile.TemporaryDirectory() as d:
            db = DB(os.path.join(d, "t.db"))
            vid = db.save_vehicle({"hersteller": "Aprilia", "modell": "SR2"})
            sid = db.save_setup(vid, "Basis", {"hauptduese": "128"})
            sid2 = db.copy_setup(sid, "HD 130")
            self.assertEqual(db.setup(sid2)["hauptduese"], "128")
            f = sorted(glob.glob(os.path.join(DATA, "*.xml")))[0]
            rid = db.import_file(f, vehicle_id=vid, setup_id=sid)
            row = db.run(rid)
            self.assertAlmostEqual(row.p_max, 32.04, places=2)
            self.assertTrue(row.datum.startswith("2026-05-26T16:18"))
            run = storage.load_any(row.path)
            self.assertEqual(len(run["n_roll"]), 298)
            db.delete_vehicle(vid)
            self.assertIsNone(db.run(rid).vehicle_id)     # Lauf bleibt erhalten


class Plotscale(unittest.TestCase):
    def test_aligned_axes(self):
        from pyst.plots import aligned_scale
        nm_top, nm_step, ps_top, ps_step, k = aligned_scale(22.8, 32.0)
        self.assertAlmostEqual(nm_top / nm_step, ps_top / ps_step, places=6)   # gleiche Anzahl Teilungen
        self.assertGreaterEqual(nm_top, 22.8)
        self.assertGreaterEqual(ps_top, 32.0)

    def test_limits(self):
        from pyst.plots import aligned_scale
        nm_top, nm_step, ps_top, ps_step, k = aligned_scale(25.0, 32.0, min_right=5.0, min_left=5.0)
        self.assertGreaterEqual(ps_top, 32.0)
        self.assertAlmostEqual(5.0 + k * ps_step, ps_top)
        self.assertAlmostEqual(5.0 + k * nm_step, nm_top)
        _, _, ps_top, _, _ = aligned_scale(25.0, 40.0, fixed_left=True)
        self.assertAlmostEqual(ps_top, 40.0)


class Filters(unittest.TestCase):
    def test_seconds_follow_rate(self):
        p = physics.DynoParams(ma_s=1.75, dq_s=0.75)
        for rate, ma, dq in ((20.0, 35, 15), (60.0, 105, 45), (61.0, 107, 46)):
            q = physics.effective(p, rate)
            self.assertEqual((q.ma, q.dq), (ma, dq))
            self.assertEqual((q.ma_s, q.dq_s), (0.0, 0.0))
        lv = physics.DynoParams(ma=35, dq=15)                 # LabVIEW-Lauf: Messpunkte bleiben
        self.assertEqual(physics.effective(lv, 60.0).ma, 35)

    def test_same_time_same_power(self):
        """Gleicher Motor bei 20 und 60 Hz: mit Filtern in Sekunden praktisch gleiche Pmax."""
        p = physics.DynoParams(ma_s=1.75, dq_s=0.75, n_stop=0)
        pmax = {}
        for rate in (20.0, 60.0):
            fr = sim_run(SimEngine(rate=rate, noise=0.0005))
            n_roll = np.array([f.roll_hz for f in fr]) * 60 / p.inkr
            dt = np.array([1 / f.rate for f in fr])
            r = physics.evaluate(n_roll, dt, p)
            self.assertEqual((r.ma_used, r.dq_used), (round(1.75 * rate), round(0.75 * rate)))
            pmax[rate] = r.p_max
        self.assertAlmostEqual(pmax[20.0], pmax[60.0], delta=0.1)

if __name__ == "__main__":
    unittest.main()
