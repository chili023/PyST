"""Regressionstests:  python -m unittest discover -s tests -v"""
import glob
import os
import re
import sys
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyst import link, lvxml, physics, runner, rusefi, storage  # noqa: E402
from pyst.channels import run_channels  # noqa: E402
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


class FakeEcu:
    """Antwortet wie ein rusEFI auf 'S' und 'O' (CRC-Rahmen); Werte haengen von der Zeit ab."""

    def __init__(self, signature="rusEFI test.2026.09.30.sim.1"):
        self.sig = signature
        self.out = b""
        self.t0 = time.time()

    def block(self):
        import struct
        t = time.time() - self.t0
        return struct.pack("<IHhhHf", 1 << 5, int(3000 + 1000 * t), int(4250), int(8512), 9800, 13.8) \
            + bytes(16)

    def write(self, data):
        import struct, zlib
        n = struct.unpack(">H", data[:2])[0]
        p = data[2:2 + n]
        if p[:1] == b"S":
            body = b"\x00" + self.sig.encode() + b"\x00"
        elif p[:1] == b"O":
            off, cnt = struct.unpack("<HH", p[1:5])
            body = b"\x00" + self.block()[off:off + cnt]
        else:
            body = b"\x83"
        self.out += struct.pack(">H", len(body)) + body + struct.pack(">I", zlib.crc32(body))

    def read(self, n):
        d, self.out = self.out[:n], self.out[n:]
        return d

    def reset_input_buffer(self):
        self.out = b""

    def close(self):
        pass


class Rusefi(unittest.TestCase):
    INI = os.path.join(DATA, "rusefi_mini.ini")

    def test_ini(self):
        ini = rusefi.IniDef(self.INI)
        self.assertEqual(ini.signature, "rusEFI test.2026.09.30.sim.1")
        self.assertEqual(ini.block_size, 32)
        self.assertEqual(ini.check(), "")
        self.assertNotIn("someSetting", ini.channels)          # nur [OutputChannels]
        self.assertNotIn("calcChannel", ini.channels)          # Ausdruecke werden nicht gelesen
        self.assertEqual(ini.channels["coolant"].unit, "°C")     # #if/#else, Einheit uebersetzt
        self.assertEqual(ini.channels["TPSValue"].title, "TPS")
        self.assertEqual(ini.channels["VBatt"].title, "VBatt")   # ohne Datalog-Eintrag: Kanalname

    def test_link_and_save(self):
        ini = rusefi.IniDef(self.INI)
        ecu = rusefi.RusefiLink("FAKE", ini, ["RPMValue", "TPSValue", "coolant", "checkEngine", "gibtsnicht"],
                                rate_hz=100, ser=FakeEcu())
        try:
            time.sleep(0.4)
            self.assertTrue(ecu.signature_ok)
            self.assertEqual(ecu.channel_names(), ["RPMValue", "TPSValue", "coolant", "checkEngine"])
            self.assertEqual(ecu._range, (0, 10))               # nur der benoetigte Bytebereich
            self.assertAlmostEqual(ecu.value("TPSValue"), 42.5)
            self.assertAlmostEqual(ecu.value("coolant"), 85.12)
            self.assertEqual(ecu.value("checkEngine"), 1.0)
            self.assertIsNone(ecu.value("VBatt"))               # nicht gewaehlt

            # Lauf mit dem Pruefstands-Simulator, ECU-Werte werden mitgespeichert
            dyno = link.DynoLink(link.SIM_PORT)
            ctrl = runner.RunController(dyno, physics.DynoParams(n_stop=0), auto_climate=False, ecu=ecu)
            ctrl.start()
            t0 = time.time()
            while time.time() - t0 < 1.0:
                ctrl.poll()
                time.sleep(0.02)
            self.assertGreater(len(ctrl.frames), 20)
            ctrl.finish()
            dyno.close()
            self.assertIsNotNone(ctrl.ecu_data)
            with tempfile.TemporaryDirectory() as d:
                folder = storage.save_run(ctrl, base_dir=d)
                self.assertTrue(os.path.exists(os.path.join(folder, "rusefi.csv")))
                run = storage.load_any(folder)
            self.assertEqual(run["meta"]["rusefi"]["signatur"], ini.signature)
            ch = run_channels(run, storage.recompute(run), run["params"])
            self.assertEqual(ch["TPS (ECU)"].group, "ECU")
            self.assertEqual(ch["TPS (ECU)"].unit, "%")
            tps = ch["TPS (ECU)"].values
            self.assertTrue(np.nanmin(tps) > 42.4 and np.nanmax(tps) < 42.6)
            rpm = ch["RPM (ECU)"].values
            self.assertTrue(np.all(np.diff(rpm[np.isfinite(rpm)]) >= 0))   # steigt mit der Zeit
            self.assertNotIn("VBatt (ECU)", ch)
        finally:
            ecu.close()

    def test_wrong_signature(self):
        ecu = rusefi.RusefiLink("FAKE", rusefi.IniDef(self.INI), ser=FakeEcu("rusEFI anders.1"))
        self.assertFalse(ecu.signature_ok)
        ecu.close()

    def test_resample_outside_is_nan(self):
        snap = {"t": np.array([1.0, 2.0]), "values": np.array([[0.0, 1.0], [10.0, 0.0]]),
                "channels": [{"name": "a", "bits": False}, {"name": "b", "bits": True}]}
        out = rusefi.resample(snap, np.array([0.5, 1.5, 1.9, 2.5]))
        np.testing.assert_allclose(out["rusefi_a"], [np.nan, 5.0, 9.0, np.nan])
        np.testing.assert_allclose(out["rusefi_b"], [np.nan, 1.0, 1.0, np.nan])
