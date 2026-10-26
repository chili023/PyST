"""Tests PyST 1.3: PST-Diag 1 (Parser, Simulator, Burst), Fehlerkanaele, Dauermessung, .pyst, HTML, Import.

    python -m pytest tests      bzw.  python -m unittest discover -s tests -v

Die echten Kunden-Mitschnitte liegen NICHT im Repo. Zum lokalen Pruefen:
    PYST_KUNDEN_LOGS=/pfad/zum/ordner python -m pytest tests -k Kunden
(erwartet teraterm-2026.10.25-5.log = stark gestoert, -6.log = sauber)
"""
import glob
import os
import queue
import sys
import tempfile
import time
import unittest
import zipfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyst import channels, diag, link, physics, storage  # noqa: E402
from pyst.recorder import Recorder  # noqa: E402
from pyst.sim import FAULT_PRESET, SimEngine, SimSerial  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data")
LOG = os.path.join(DATA, "terminal_mega_beispiel.log")


def lines_of(s: SimSerial):
    return s.read(10 ** 8).decode("latin-1").replace("\r\n", "\n").split("\n")


class ScriptedSerial:
    """Liefert einen festen Bytestrom (in kleinen Stuecken, damit Zeilen ueber Stueckgrenzen gehen)."""

    def __init__(self, data: bytes, chunk: int = 7):
        self.data, self.chunk = data, chunk
        self.written = b""

    @property
    def in_waiting(self):
        return min(self.chunk, len(self.data))

    def read(self, n=1):
        if not self.data:
            time.sleep(0.01)
        d, self.data = self.data[:n], self.data[n:]
        return d

    def write(self, b):
        self.written += b
        return len(b)

    def close(self):
        pass


class Parser(unittest.TestCase):
    def test_line_kinds(self):
        k = lambda line: (link.parse_line(line, 0) or (None,))[0]
        self.assertEqual(k("12;60.00;50.00;725.50;656;0.00;612"), "F")
        self.assertEqual(k("1;41.67;49;869;nan;20.00;740"), "F")          # Mega 3.0.0 mit nan
        self.assertEqual(k("5;61.03;50;725"), "F")
        self.assertEqual(k("5;61.03;50;725;0;20.00;0;99;zukunft"), "F")    # zusaetzliche Felder egal
        self.assertEqual(k("21.5;96512;45.3"), "T")                        # Klima
        self.assertEqual(k("Version 3.0.0 vom 28.03.24;"), "T")
        self.assertEqual(k("#CAP;proto=1;fw=X 1.0;tick_ns=62.5"), "#")
        self.assertEqual(k("D;7;123456;60.00;50.123;725.500;nan;14.70;nan;1;19950.0;20010.5;0;12;1370.1;1380.0;0;1F"), "D")
        self.assertEqual(k("B;3;I;0;4294967200"), "B")
        self.assertEqual(k("B;kaputt"), "T")
        self.assertIsNone(link.parse_line("   ", 0))

    def test_diag_fields(self):
        f = link.parse_diag("D;7;123456;60.00;50.123;725.500;nan;14.70;nan;1;19950.0;20010.5;2;12;1370.1;1380.0;0;1F;x;y", 1.0)
        self.assertEqual((f.cycle, f.rate, f.ign_hz, f.roll_hz, f.afr), (7, 60.0, 50.123, 725.5, 14.7))
        self.assertTrue(np.isnan(f.egt1))
        self.assertEqual(f.diag["flags"], 0x1F)
        self.assertEqual(f.diag["ign_rej"], 2)
        self.assertEqual(f.diag["roll_pmax_us"], 1380.0)
        g = link.parse_diag("D;8;1;60;50;700", 0)                       # nur Pflichtfelder
        self.assertTrue(np.isnan(g.diag["ign_n"]))
        self.assertEqual(g.diag["flags"], 0)
        self.assertIn("Zündung Timeout", diag.flags_text(0x1F))

    def test_reader_line_endings_and_queues(self):
        stream = (b"Version 3.0.0;\r\n21.5;96512;45.3\r"             # \r\n, \r
                  b"1;49.14;55;989;nan;20.00;740\n"                   # Mega: nur \n
                  b"2;49.14;64;1011;nan;20.00;740\r"                  # TeraTerm-Stil: nur \r
                  b"#OK;MODE;D\r\n"
                  b"D;3;100;49.14;64.000;1011.000;nan;20.00;nan;1;15000.0;15000.0;0;10;990.0;990.0;0;0\r\n"
                  b"#OK;BURST;ARMED;ch=I;n=3;edge=F;trig=0;pre=0\r\n"
                  b"#BURST;BEGIN;n=3;tick_ns=62.5;trig_idx=-1;ch=I\r\n"
                  b"B;0;I;0;4294967000\r\nB;1;I;0;100\r\nB;2;I;0;600\r\n#BURST;END;n=3;lost=0\r\n"
                  b"Unsinn ohne Zahlen\r\n")
        lk = link.DynoLink(link.SIM_PORT + ":mega300")
        lk.ser.close()
        time.sleep(0.15)
        lk.ser = ScriptedSerial(stream)
        tap = lk.add_tap()
        time.sleep(0.5)
        frames = lk.drain_frames()
        self.assertEqual([f.cycle for f in frames], [1, 2, 3])
        self.assertIsNotNone(frames[2].diag)
        self.assertEqual(tap.qsize(), 3)
        texts = []
        while not lk.text_lines.empty():
            texts.append(lk.text_lines.get_nowait())
        self.assertIn("21.5;96512;45.3", texts)
        self.assertIn("Unsinn ohne Zahlen", texts)
        replies = []
        while not lk.replies.empty():
            replies.append(lk.replies.get_nowait())
        self.assertEqual(replies, ["#OK;MODE;D", "#OK;BURST;ARMED;ch=I;n=3;edge=F;trig=0;pre=0"])
        b = lk.bursts.get(timeout=1)
        self.assertEqual(len(b.edges), 3)
        np.testing.assert_allclose(b.times() * 1e9 / 62.5, [0, 396, 896])   # Ueberlauf entfaltet
        lk._stop = True


class Burst(unittest.TestCase):
    def test_unwrap(self):
        t = [4294967000, 4294967290, 5, 400, 2 ** 31 + 10, 2 ** 32 - 1, 3]
        u = diag.unwrap_ticks(t)
        self.assertTrue(np.all(np.diff(u) > 0))
        self.assertEqual(int(u[-1] - u[0]), (3 - 4294967000) % 2 ** 32 + 2 ** 32)
        np.testing.assert_array_equal(diag.unwrap_ticks([100, 90, 120]), [100, 90, 120])   # kleiner Ruecksprung

    def test_periods_and_pulse_width(self):
        b = diag.Burst(n=8, tick_ns=1000.0, trig_idx=-1, ch="I")
        t = 2 ** 32 - 3000                                       # 1 µs-Ticks, Ueberlauf in der Aufnahme
        for k in range(4):
            b.edges.append((2 * k, "I", 0, (t + k * 2000) % 2 ** 32))          # Periode 2 ms
            b.edges.append((2 * k + 1, "I", 1, (t + k * 2000 + 300) % 2 ** 32))   # Low-Puls 0,3 ms
        _ts, per = b.periods("I", 0)
        np.testing.assert_allclose(per, 0.002)
        _ts, w = b.pulse_widths("I", 0)
        np.testing.assert_allclose(w, 0.0003)
        x, y = b.trace("I")
        self.assertEqual(len(x), len(y))


class SimulatorDiag(unittest.TestCase):
    def test_protocol(self):
        s = SimSerial(realtime=False)
        s.write(b"#?\n")
        caps = link.parse_caps(lines_of(s)[0])
        self.assertEqual(caps["proto"], "1")
        self.assertEqual(caps["modes"], "N,D")
        s.write(b"#MODE D\r#RUN 1\r\n#GET\n#SET ign_lockout_pct 50\n#SET gibtsnicht 1\n#FOO\n#SAVE\n#DEFAULTS\n")
        out = [l for l in lines_of(s) if l]
        self.assertEqual(out[:2], ["#OK;MODE;D", "#OK;RUN;1"])
        self.assertIn("#SET;edge_ign=F", out)
        self.assertIn("#OK;GET", out)
        self.assertIn("#OK;SET;ign_lockout_pct=50", out)
        self.assertTrue(any(l.startswith("#ERR;SET") for l in out))
        self.assertIn("#ERR;?;unbekannt", out)
        self.assertIn("#ERR;SAVE;messung laeuft", out)                     # F446/Mega: nur angehalten
        self.assertEqual(s.settings["ign_lockout_pct"], "0")              # DEFAULTS
        s.write(b"#RUN 0\n#SET ign_lockout_pct 40\n#SAVE\n#RUN 1\n#MODE D\n")
        self.assertIn("#OK;SAVE", lines_of(s))
        self.assertEqual(s.saved["ign_lockout_pct"], "40")
        for _ in range(120):
            s.tick()
        d = [link.parse_diag(l, 0) for l in lines_of(s) if l.startswith("D;")]
        self.assertEqual(len(d), 120)
        self.assertTrue(all(f.diag["roll_n"] > 0 for f in d[5:]))
        self.assertGreater(d[60].diag["ign_pmax_us"], 0)
        s.write(b"#RATE 5000\n#MODE N\n")
        out = lines_of(s)
        self.assertTrue(out[0].startswith("#ERR;RATE"))
        s.tick()
        self.assertTrue(lines_of(s)[0][0].isdigit())                         # wieder normales Telegramm
        s.close()

    def test_old_devices_ignore_hash(self):
        s = SimSerial(engine=SimEngine(rate=61.0), realtime=False, mega=True, diag=False)
        s.write(b"#?\n")
        self.assertEqual(lines_of(s), [""])                                 # alter Mega: keine Antwort
        s.write(b"m")
        s.tick()
        self.assertEqual(len(lines_of(s)[0].split(";")), 6)
        s.close()
        f = SimSerial(realtime=False, diag=False)                          # F446 1.0.0: '?' = Versionsinfo
        f.write(b"#?\n")
        self.assertTrue(lines_of(f)[0].startswith("PST-STM32"))
        f.close()

    def test_burst_trigger_catches_double_pulse(self):
        s = SimSerial(realtime=False, faults={"sim_doppel": 120.0}, seed=3)
        s.write(b"#RUN 1\n#BURST I 60 F T30 P20\n")
        for _ in range(600):
            s.tick()
            if s.burst is None:
                break
        lk = link.DynoLink(link.SIM_PORT + ":mega300")
        lk.ser.close()
        time.sleep(0.15)
        lk.ser = ScriptedSerial(s.read(10 ** 8))
        b = lk.bursts.get(timeout=3)
        lk._stop = True
        self.assertEqual(len(b.edges), 60)
        self.assertEqual(b.trig_idx, 20)
        _t, per = b.periods("I", 0)
        k = int(np.argmin(per))
        self.assertLess(per[k], 0.5 * np.median(per))                      # Doppelimpuls am Trigger
        self.assertTrue(np.all(np.diff(b.times()) > 0))

    def test_dynolink_diag_and_old(self):
        lk = link.DynoLink(link.SIM_PORT + ":stoerung")
        try:
            lk.info()
            self.assertTrue(lk.has_diag)
            self.assertTrue(lk.set_mode("D"))
            lk.start()
            time.sleep(0.6)
            fr = lk.drain_frames()
            self.assertGreater(len(fr), 10)
            self.assertIsNotNone(fr[-1].diag)
            self.assertTrue(lk.ok(lk.request_burst("A", 100, "B")))
            b = lk.bursts.get(timeout=3)
            self.assertEqual(len(b.edges), 100)
            self.assertEqual({e[1] for e in b.edges}, {"I", "R"})
            keys = dict(lk.get_settings())
            self.assertEqual(keys["sim_doppel"], f"{FAULT_PRESET['sim_doppel']:g}")
            self.assertTrue(lk.ok(lk.set_setting("sim_wackel", "0")))
        finally:
            lk.close()
        old = link.DynoLink(link.SIM_PORT + ":mega300")
        try:
            t = time.time()
            old.info()
            self.assertFalse(old.has_diag)
            self.assertLess(time.time() - t, 2.0)
            old.start()
            time.sleep(0.4)
            fr = old.drain_frames()
            self.assertGreater(len(fr), 5)
            self.assertIsNone(fr[-1].diag)
        finally:
            old.close()


def vario_run(seconds=12.0, rate=60.0, mega=False, seed=1):
    """Synthetische Messung mit stetig veraenderlicher Uebersetzung (Vario), ohne Stoerungen."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, seconds, 1 / rate)
    n_eng = 3000 + 6000 * np.clip(t / 3, 0, 1) + 300 * np.sin(t)              # Motor dreht hoch und haelt
    ratio = 15.0 - 10.0 * np.clip((t - 1) / (seconds - 2), 0, 1)              # Vario schaltet hoch
    n_roll = n_eng / ratio
    n_roll = n_roll * (1 + rng.normal(0, 0.002, len(t)))
    n_ign = n_eng * (1 + rng.normal(0, 0.004, len(t)))
    if mega:                                                                    # ganze Hz
        n_roll = np.floor(n_roll * 100 / 60) * 60 / 100
        n_ign = np.floor(n_ign / 60) * 60
    return t, n_roll, n_ign


class FaultChannels(unittest.TestCase):
    def test_vario_without_false_alarm(self):
        for mega in (False, True):
            t, n_roll, n_ign = vario_run(mega=mega)
            ch = channels.fault_channels(n_roll, n_ign, 60.0)
            s = channels.fault_summary(ch)
            with self.subTest(mega=mega):
                self.assertEqual(s["Fehler Rolle"], 0.0)
                self.assertEqual(s["Fehler Zündung"], 0.0)
                self.assertEqual(s["Wert eingefroren"], 0.0)
                ev = diag.detect_events(t, n_roll, n_ign)
                self.assertEqual([e.kind for e in ev], [])

    def test_injected_faults_found(self):
        t, n_roll, n_ign = vario_run()
        n_roll[200:202] *= 3.0                            # Rollensprung (Stoerimpulse)
        n_ign[400:404] *= 2.0                             # Doppelimpuls
        n_ign[500:503] *= 0.4                             # Aussetzer
        n_roll[600:700] = n_roll[600]                     # Rolle eingefroren, Motor dreht weiter
        n_ign[600:700] = np.linspace(n_ign[600], n_ign[600] * 1.1, 100)
        ch = channels.fault_channels(n_roll, n_ign, 60.0)
        self.assertTrue(ch["Fehler Rolle"].values[200:202].all())
        self.assertTrue(ch["Fehler Zündung"].values[400:404].all())
        self.assertTrue(ch["Doppelimpuls Zündung"].values[400:404].all())
        self.assertTrue(ch["Fehler Zündung"].values[500:503].all())
        self.assertTrue(ch["Wert eingefroren"].values[600:700].all())
        self.assertLess(ch["Fehler Rolle"].values[:190].sum() + ch["Fehler Zündung"].values[:390].sum(), 1)
        kinds = {e.kind for e in diag.detect_events(t, n_roll, n_ign)}
        self.assertTrue({diag.EV_ROLL_JUMP, diag.EV_DOUBLE, diag.EV_IGN_MISS, diag.EV_FROZEN} <= kinds)

    def test_clean_sim_run(self):
        s = SimSerial(engine=SimEngine(), realtime=False)
        s.write(b"m")
        for _ in range(60 * 20):
            s.tick()
        fr = [link.parse_frame(l, 0) for l in lines_of(s) if l]
        p = physics.DynoParams()
        n_roll = np.array([f.roll_hz for f in fr]) * 60 / p.inkr
        n_ign = np.array([f.ign_hz for f in fr]) * 60 / p.imp
        s_ = channels.fault_summary(channels.fault_channels(n_roll, n_ign, 60.0), n_roll > 30)
        self.assertLess(max(s_.values()), 0.5, s_)

    def test_rolling_median_long(self):
        x = np.arange(200000, dtype=float)
        t = time.time()
        m = channels.rolling_median(x, 60)
        self.assertLess(time.time() - t, 5.0)
        self.assertEqual(m[1000], 1000.0)

    @unittest.skipUnless(os.environ.get("PYST_KUNDEN_LOGS"), "Kunden-Mitschnitte nur lokal (PYST_KUNDEN_LOGS)")
    def test_kunden_logs(self):
        d = os.environ["PYST_KUNDEN_LOGS"]
        r5 = storage.load_any(os.path.join(d, "teraterm-2026.10.25-5.log"))
        r6 = storage.load_any(os.path.join(d, "teraterm-2026.10.25-6.log"))
        s5, s6 = channels.run_faults(r5)[1], channels.run_faults(r6)[1]
        print("\nLauf 5:", channels.fault_text(s5), "\nLauf 6:", channels.fault_text(s6))
        self.assertGreater(s5["Fehler Rolle"], 30.0)
        self.assertGreater(s5["Fehler Zündung"], 5.0)
        self.assertLess(max(s6.values()), 1.0)


class TerminalImport(unittest.TestCase):
    def test_teraterm_with_timestamps(self):
        self.assertTrue(storage.is_terminal_log(LOG))
        r = storage.load_any(LOG)
        self.assertEqual(r["source"], "Terminal")
        self.assertEqual(len(r["dt"]), 400)
        self.assertIn("Version 3.0.0", r["meta"]["firmware"])
        self.assertAlmostEqual(r["params"].temp_c, 21.5)
        self.assertAlmostEqual(r["params"].p_mbar, 965.12)
        self.assertTrue(r["date"].startswith("2026-01-15T10:00"))
        self.assertIn("Standard", r["meta"]["hinweis"])
        s = channels.run_faults(r)[1]
        self.assertGreater(s["Fehler Rolle"] + s["Fehler Zündung"], 1.0)   # Beispiel enthaelt Stoerungen

    def test_cr_only_without_timestamps_and_params(self):
        import re
        text = open(LOG, encoding="latin-1").read()
        text = re.sub(r"\[[^\]]*\] ", "", text).replace("\r\n", "\r")
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "mitschnitt.txt")
            with open(f, "w", newline="", encoding="latin-1") as fh:
                fh.write(text)
            p = physics.DynoParams(inkr=60.0, ratio=7.5)
            r = storage.parse_terminal_log(f, p)
            self.assertEqual(len(r["dt"]), 400)
            self.assertEqual(r["params"].inkr, 60.0)
            self.assertEqual(r["meta"]["hinweis"], "")
            self.assertAlmostEqual(float(r["columns"]["pc_zeit_s"][1]), 1 / 49.2, places=3)
            from pyst.db import Database
            db = Database(os.path.join(d, "t.db"))
            rid = db.import_file(f, params=p)
            row = db.run(rid)
            self.assertEqual(row.quelle, "Terminal")
            self.assertEqual(row.params["inkr"], 60.0)
            self.assertIsNotNone(row.fehler)
            run = storage.load_any(row.path)
            self.assertEqual(len(run["n_roll"]), 400)


def _sim_frames(n=300, diag_mode=True):
    s = SimSerial(realtime=False, faults={"sim_doppel": 30.0})
    s.write(b"#MODE D\n#RUN 1\n" if diag_mode else b"m")
    for _ in range(n):
        s.tick()
    out = []
    for k, l in enumerate(lines_of(s)):
        r = link.parse_line(l, 1000.0 + k / 60)
        if r and r[0] in ("F", "D"):
            out.append(r[1])
    return out


class DauerAndShare(unittest.TestCase):
    def test_recorder_sections_crash_and_close(self):
        fr = _sim_frames()
        with tempfile.TemporaryDirectory() as d:
            import pyst.recorder as rec_mod
            old = rec_mod.ABSCHNITT_S
            rec_mod.ABSCHNITT_S = 1.0                      # viele Abschnitte
            try:
                rec = Recorder(d, physics.DynoParams(), port="SIM", diag=True)
                rec.add_frames(fr[:200])
                rec.add_marker("Kabel bewegt", 1.5)
                rec.add_event(diag.Event(2.0, diag.EV_DOUBLE, "test"))
                b = diag.Burst(n=2, tick_ns=62.5, edges=[(0, "I", 0, 2 ** 32 - 10), (1, "I", 0, 20)],
                               request={"edge": "F"}, t_pc=1001.0)
                rec.add_burst(b)
                rec._fh.flush()
                # "Absturz": Ordner ohne lauf.json laden
                crash = storage.load_any(rec.folder)
                self.assertEqual(len(crash["dt"]), 200)
                self.assertEqual(crash["kind"], "dauermessung")
                self.assertGreater(len([f for f in os.listdir(rec.folder) if f.startswith("teil_")]), 2)
                rec.add_frames(fr[200:])
                folder = rec.close()
            finally:
                rec_mod.ABSCHNITT_S = old
            run = storage.load_any(folder)
            self.assertEqual(len(run["dt"]), len(fr))
            self.assertEqual(run["markers"], [(1.5, "Kabel bewegt")])
            self.assertEqual(run["events"][0][1], diag.EV_DOUBLE)
            self.assertEqual(len(run["bursts"]), 1)
            np.testing.assert_allclose(run["bursts"][0].times(), [0, 30 * 62.5e-9])
            ch = channels.run_channels(run, None, run["params"])
            self.assertIn("Flanken Zündung", ch)
            self.assertIn("Fehler Rolle", ch)
            self.assertFalse(any(f.startswith("teil_") for f in os.listdir(folder)))

    def _db_with_runs(self, d):
        from pyst.db import Database
        db = Database(os.path.join(d, "quelle", "pyst.db"))
        vid = db.save_vehicle({"hersteller": "Aprilia", "modell": "SR2", "notiz": "Kunde Müller"})
        sid = db.save_setup(vid, "HD 128", {"hauptduese": "128"})
        ids = [db.import_file(f, vehicle_id=vid, setup_id=sid) for f in sorted(glob.glob(os.path.join(DATA, "*.xml")))[:2]]
        rec = Recorder(db.runs_dir(), physics.DynoParams(), diag=True)
        rec.add_frames(_sim_frames(200))
        rec.add_marker("Kabel bewegt", 1.0)
        ids.append(db.add_folder(rec.close(), vid, sid))
        return db, ids

    def test_pyst_roundtrip(self):
        from pyst.db import Database
        with tempfile.TemporaryDirectory() as d:
            db, ids = self._db_with_runs(d)
            items = []
            for rid in ids:
                r = db.run(rid)
                items.append({"ordner": r.ordner, "name": r.name, "datum": r.datum, "quelle": r.quelle,
                              "fahrzeug": db.vehicle(r.vehicle_id), "setup": db.setup(r.setup_id), "notiz": "Notiz äöü",
                              "farbe": "#123456", "parameter": r.params, "fehler": r.fehler})
            path = storage.export_pyst(os.path.join(d, "kunde"), items)
            self.assertTrue(path.endswith(".pyst"))
            with zipfile.ZipFile(path) as z:
                self.assertIn("inhalt.json", z.namelist())
            runs = storage.load_pyst(path)
            self.assertEqual(len(runs), 3)
            self.assertEqual(runs[2]["kind"], "dauermessung")
            self.assertEqual(runs[2]["markers"], [(1.0, "Kabel bewegt")])
            a = storage.recompute(storage.load_any(db.run(ids[0]).path))
            b = storage.recompute(runs[0])
            self.assertAlmostEqual(a.p_max, b.p_max, places=6)
            # beim Kunden: leere Datenbank
            kd = Database(os.path.join(d, "kunde", "pyst.db"))
            new = kd.import_pyst(path)
            self.assertEqual(len(new), 3)
            self.assertEqual(kd.import_pyst(path), new)                          # nicht doppelt
            row = kd.run(new[0])
            self.assertEqual(kd.vehicle(row.vehicle_id)["modell"], "SR2")
            self.assertEqual(kd.setup(row.setup_id)["hauptduese"], "128")
            self.assertEqual(row.notiz, "Notiz äöü")
            self.assertAlmostEqual(row.p_max, db.run(ids[0]).p_max, places=6)
            self.assertEqual(kd.run(new[2]).quelle, "Dauermessung")
            self.assertEqual(len(storage.load_any(kd.run(new[2]).path)["markers"]), 1)

    def test_html_export(self):
        from pyst import htmlexport
        with tempfile.TemporaryDirectory() as d:
            db, ids = self._db_with_runs(d)
            payload = []
            for rid in ids:
                r = db.run(rid)
                run = storage.load_any(r.path)
                p = physics.DynoParams(**r.params)
                res = None if r.quelle == "Dauermessung" else storage.recompute(run, p)
                ch = channels.run_channels(run, res, p)
                payload.append(htmlexport.run_payload(r.name, run, res, ch, p, vehicle="Aprilia SR2",
                                                      fehler=r.fehler))
            out = os.path.join(d, "ansicht.html")
            size = htmlexport.export_html(out, payload, "Aprilia SR2 </script>")
            text = open(out, encoding="utf-8").read()
            self.assertEqual(size, os.path.getsize(out))
            self.assertLess(size, 2_000_000)
            self.assertIn("uPlot", text)
            self.assertIn("MIT", text)
            self.assertIn('"pmax":32.0', text)
            self.assertNotIn("src=\"http", text)
            self.assertEqual(text.count("</script>"), 3)                       # Daten brechen nichts auf
            self.assertFalse(payload[2]["main"])                               # Dauermessung: ueber der Zeit


class GuiOszi(unittest.TestCase):
    """Oszi-Reiter mit Simulator (offscreen): Dauermessung, Marker, Burst, Ereignisse, .pyst oeffnen."""

    def test_oszi_page(self):
        with tempfile.TemporaryDirectory() as home:
            old = {k: os.environ.get(k) for k in ("HOME", "QT_QPA_PLATFORM")}
            os.environ["HOME"] = home
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            try:
                from PySide6 import QtWidgets
                app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
                import importlib
                from pyst import gui
                importlib.reload(gui)
                w = gui.MainWindow()

                def run(sec):
                    end = time.time() + sec
                    while time.time() < end:
                        app.processEvents()
                        time.sleep(0.01)
                w.page_settings.port_combo.setEditText("SIM:stoerung")
                self.assertTrue(w.on_connect())
                o = w.page_oszi
                o.rec_btn.setChecked(True)
                self.assertEqual(w.link.mode, "D")
                run(1.0)
                o.marker_edit.setText("Kabel bewegt")
                o.add_marker()
                o.b_trig.setValue(0)
                o.b_n.setValue(50)
                o.request_burst()
                run(2.0)
                self.assertGreater(o.rec.n, 60)
                self.assertEqual(len(o.bursts), 1)
                o.rec_btn.setChecked(False)
                self.assertEqual(w.link.mode, "N")
                rows = w.db.runs()
                self.assertEqual(rows[0].quelle, "Dauermessung")
                path = w.on_share(path=os.path.join(home, "x.pyst"), scope="sichtbar" if rows[0].sichtbar else "tag",
                                  day=rows[0].datum[:10], fmt=0)
                self.assertTrue(os.path.exists(path))
                html = w.on_share(path=os.path.join(home, "x.html"), scope="tag", day=rows[0].datum[:10], fmt=2)
                self.assertTrue(os.path.getsize(html) > 50000)
                w.close()
                app.processEvents()
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
