"""Punkte aus dem Code-Review: Median, CSV-Laden, Cache, Einstellungen, .pyst, Recorder (M11-M15, 2.4, 5.4)."""
import csv
import io
import json
import os
import queue
import sys
import math
import subprocess
import tempfile
import time
import unittest
import zipfile
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyst import auth, bootproto, channels, diag, felder, flash, link, physics, pruef, runcache, sim, storage  # noqa: E402
from conftest import close_window  # noqa: E402
from pyst.recorder import Recorder  # noqa: E402
from pyst.runcache import RunCache  # noqa: E402


def median_alt(x, half):
    """Die Fassung vor der Vektorisierung, je Sample ein np.median."""
    if half <= 0 or len(x) == 0:
        return x.copy()
    out = np.empty(len(x))
    for j in range(len(x)):
        out[j] = np.median(x[max(0, j - half): j + half + 1])
    return out


def assert_bitgleich(a, b):
    np.testing.assert_array_equal(np.isnan(a), np.isnan(b))
    ok = ~np.isnan(a)
    assert a[ok].tobytes() == b[ok].tobytes()


class Median(unittest.TestCase):
    def test_bitgleich_zur_alten_fassung(self):
        rng = np.random.default_rng(1)
        for n in (0, 1, 2, 3, 4, 5, 6, 7, 10, 11, 50, 1000):
            for half in (0, 1, 2, 3, 5, 7, 12, 40):
                x = rng.normal(5000, 300, n)
                assert_bitgleich(physics.median_centered(x, half), median_alt(x, half))
                if n:
                    y = x.copy()
                    y[rng.integers(0, n, max(1, n // 20))] = np.nan
                    assert_bitgleich(physics.median_centered(y, half), median_alt(y, half))
                    z = x.copy()
                    z[0] = np.inf
                    z[-1] = -np.inf
                    assert_bitgleich(physics.median_centered(z, half), median_alt(z, half))

    def test_gerade_zahl_am_rand_und_ganzzahlen(self):
        x = np.array([1, 2, 4, 8, 16, 32])
        assert_bitgleich(physics.median_centered(x, 2), median_alt(x, 2))
        self.assertEqual(physics.median_centered(x, 2).dtype, np.float64)
        self.assertEqual(physics.median_centered(x, 0).dtype, x.dtype)     # wie zuvor: nur eine Kopie

    def test_blockgrenze(self):
        x = np.random.default_rng(2).normal(0, 1, 5000)                     # Fenster 2001: mehrere Bloecke
        assert_bitgleich(physics.median_centered(x, 1000), median_alt(x, 1000))

    def test_rolling_median_ist_kein_ersatz(self):
        """Der Review-Vorschlag: ohne nan gleich, mit nan nicht. Deshalb bleibt median_centered eigenstaendig."""
        x = np.random.default_rng(3).normal(100, 5, 200)
        assert_bitgleich(channels.rolling_median(x, 3), median_alt(x, 3))
        x[50] = np.nan
        self.assertTrue(np.isnan(median_alt(x, 3)[50]))
        self.assertFalse(np.isnan(channels.rolling_median(x, 3)[50]))

    def test_tempo(self):
        x = np.random.default_rng(4).normal(5000, 100, 180_000)            # 1 h bei 50 Hz
        t0 = time.time()
        physics.median_centered(x, 12)
        self.assertLess(time.time() - t0, 2.0)


class CsvLaden(unittest.TestCase):
    def slow(self, text):
        return storage._read_csv_slow(text)

    def assert_gleich(self, text):
        a, b = storage._read_csv_cols(text), self.slow(text)
        self.assertEqual(list(a), list(b))
        for k in a:
            assert_bitgleich(a[k], b[k])

    def test_zahlen_bitgleich(self):
        rng = np.random.default_rng(5)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["a", "b", "c", "d"])
        for i in range(500):
            w.writerow([i, f"{rng.normal():.4f}", rng.normal() * 1e5, "nan"])
        self.assert_gleich(buf.getvalue())
        self.assert_gleich(buf.getvalue().replace("\n", "\r\n").replace("\r\r\n", "\r\n"))

    def test_sonderfaelle(self):
        for text in ("a;b\n1;2\n", "a;b\n", "", "a\n1\n2\n", "a;b\n1;\n;2\n", "a;b\n1;x\n", "a;b\n1\n2;3\n",
                     "a;b\n1;2;3\n", "a;a\n1;2\n", "a;b\n\n1;2\n\n3;4\n", "a;b\n1.5e3;-inf\n", 'a;b\n"1";2\n'):
            with self.subTest(text=text):
                self.assert_gleich(text)


class Cache(unittest.TestCase):
    def test_lru_anzahl(self):
        c = RunCache(max_entries=3)
        for i in range(3):
            c.put(i, ({"x": np.zeros(10)},))
        c.get(0)                                                  # 0 wurde benutzt, 1 ist jetzt der aelteste
        c.put(3, ({"x": np.zeros(10)},))
        self.assertNotIn(1, c)
        self.assertIn(0, c)
        self.assertEqual(len(c), 3)

    def test_lru_groesse(self):
        c = RunCache(max_bytes=20_000)
        c.put(1, {"x": np.zeros(1000)})
        c.put(2, {"x": np.zeros(1000)})
        self.assertEqual(len(c), 2)
        self.assertEqual(c.total, 16_000)
        c.put(3, {"x": np.zeros(1000)})
        self.assertNotIn(1, c)
        c.put(4, {"x": np.zeros(100_000)})                        # zu gross, bleibt aber als einziger Eintrag
        self.assertEqual(list(c.items), [4])

    def test_pop_und_groesse(self):
        c = RunCache()
        c.put(1, {"x": np.zeros(10)})
        self.assertIsNotNone(c.pop(1))
        self.assertIsNone(c.pop(1))
        self.assertIsNone(c.get(1))
        self.assertEqual(c.total, 0)

    def test_nbytes_zaehlt_gemeinsames_einmal(self):
        a = np.zeros(10)
        self.assertEqual(runcache.nbytes({"a": a, "b": [a, a], "c": (1, "x", None)}), 80)

        class Res:
            def __init__(self):
                self.v = np.zeros(5)
        self.assertEqual(runcache.nbytes((Res(),)), 40)


class Einstellungen(unittest.TestCase):
    def setUp(self):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from pyst import gui
        self.gui = gui
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "unter", "einstellungen.json")
        p = mock.patch.object(gui, "SETTINGS_FILE", self.path)
        p.start()
        self.addCleanup(p.stop)

    def test_atomar(self):
        self.assertTrue(self.gui.save_settings({"a": 1}))
        self.assertEqual(self.gui.load_settings(), {"a": 1})
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["einstellungen.json"])

    def test_schreibfehler_laesst_alte_datei_stehen(self):
        self.gui.save_settings({"a": 1})
        with self.assertLogs("pyst.gui", "WARNING"):
            with mock.patch("os.fsync", side_effect=OSError("Platte voll")):
                self.assertFalse(self.gui.save_settings({"a": 2}))
            with mock.patch("os.replace", side_effect=OSError("gesperrt")):
                self.assertFalse(self.gui.save_settings({"a": 3}))
            self.assertFalse(self.gui.save_settings({"a": object()}))      # nicht als JSON darstellbar
        self.assertEqual(self.gui.load_settings(), {"a": 1})
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["einstellungen.json"])

    def test_ordner_nicht_anlegbar(self):
        sperre = os.path.join(self.tmp.name, "datei")
        open(sperre, "w").close()
        with mock.patch.object(self.gui, "SETTINGS_FILE", os.path.join(sperre, "x.json")):
            with self.assertLogs("pyst.gui", "WARNING"):
                self.assertFalse(self.gui.save_settings({"a": 1}))


class PystEntpacken(unittest.TestCase):
    def pack(self, names):
        path = os.path.join(self.tmp.name, "x.pyst")
        with zipfile.ZipFile(path, "w") as z:
            for n in names:
                z.writestr(n, "Inhalt")
        return path

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = os.path.join(self.tmp.name, "ziel", "lauf")
        os.makedirs(self.target)

    def inhalt(self):
        return sorted(os.path.relpath(os.path.join(r, f), self.tmp.name)
                      for r, _d, fs in os.walk(self.tmp.name) for f in fs if not f.endswith(".pyst"))

    def test_boesartige_namen(self):
        boese = ["01_x/../../raus.txt", "01_x/..\\..\\raus.txt", "01_x/C:/raus.txt", "01_x/C:raus.txt", "01_x//etc/raus",
                 "01_x/./raus.txt", "01_x/a/../../raus.txt", "01_x/..", "01_x/a\\b.txt", "../01_x/raus.txt"]
        path = self.pack(boese + ["01_x/gut.txt", "01_x/bursts/burst_001.csv"])
        storage.extract_pyst_entry(path, {"ordner": "01_x"}, self.target)
        self.assertEqual(self.inhalt(), [os.path.join("ziel", "lauf", "bursts", "burst_001.csv"),
                                         os.path.join("ziel", "lauf", "gut.txt")])

    @unittest.skipUnless(hasattr(os, "symlink"), "braucht symbolische Links")
    def test_symbolischer_link_im_ziel(self):
        draussen = os.path.join(self.tmp.name, "draussen")
        os.makedirs(draussen)
        os.symlink(draussen, os.path.join(self.target, "link"))
        storage.extract_pyst_entry(self.pack(["01_x/link/raus.txt"]), {"ordner": "01_x"}, self.target)
        self.assertEqual(os.listdir(draussen), [])


class RecorderBytes(unittest.TestCase):
    def test_mitgezaehlt_wie_auf_der_platte(self):
        fr = [link.parse_frame(f"{i};60;50;700", 100.0 + i / 60) for i in range(1, 200)]
        with tempfile.TemporaryDirectory() as tmp:
            rec = Recorder(tmp, physics.DynoParams())
            rec.add_frames(fr)
            rec.add_marker("Kabel bewegt, Größe")
            rec._fh.flush()

            def platte():
                return sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(rec.folder) for f in fs
                           if f.startswith("teil_") or f == "marker.csv")
            self.assertEqual(rec.size_bytes(), platte())
            rec.close()

    def test_kein_walk_und_kein_fsync_je_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = Recorder(tmp, physics.DynoParams())
            rec.add_frames([link.parse_frame("1;60;50;700", 100.0)])
            with mock.patch("os.walk", side_effect=AssertionError), mock.patch("os.fsync") as sync:
                rec.size_bytes()
                rec.add_marker("x")
                sync.assert_not_called()
            self.assertEqual(len(rec._side_dirty), 1)
            with mock.patch("os.fsync") as sync:
                rec._sync_side_files()
                sync.assert_called_once()
            rec.close()


class Warteschlangen(unittest.TestCase):
    def test_aeltestes_faellt_weg(self):
        q = queue.Queue(2)
        self.assertTrue(link._put(q, 1))
        self.assertTrue(link._put(q, 2))
        self.assertFalse(link._put(q, 3))
        self.assertEqual([q.get_nowait(), q.get_nowait()], [2, 3])

    def test_alle_gedeckelt(self):
        lk = link.DynoLink(link.SIM_PORT)
        try:
            for q in (lk.frames, lk.text_lines, lk.replies, lk.bursts, lk.events, lk.log_lines, lk.add_tap()):
                self.assertGreater(q.maxsize, 0)
        finally:
            lk.close()


class Niedrig(unittest.TestCase):
    def test_version_im_text(self):
        self.assertTrue(flash.version_matches("0.2.3", "PyST-Basis 0.2.3 rev31"))
        self.assertTrue(flash.version_matches("0.2.3", "fw=0.2.3-rc1"))
        self.assertTrue(flash.version_matches("0.2.3-rc1", "PyST-Basis 0.2.3-rc1"))
        self.assertTrue(flash.version_matches("0.2.0", "basis-esp 0.2.0 h12"))
        self.assertFalse(flash.version_matches("0.2.3", "PyST-Basis 0.2.31"))
        self.assertFalse(flash.version_matches("0.2.3", "PyST-Basis 10.2.3"))
        self.assertFalse(flash.version_matches("0.2.3", "PyST-Basis 0.2.4"))

    def test_keine_temp_datei_bleibt(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml = os.path.join(os.path.dirname(__file__), "data", "260526_161802_ 32,0_9972.xml")
            path = os.path.join(tmp, "x.pyst")
            with zipfile.ZipFile(path, "w") as z:
                z.write(xml, "01_x/lauf.xml")
                z.writestr("inhalt.json", json.dumps({"format": "pyst", "laeufe": [{"ordner": "01_x", "name": "x"}]}))
            old = tempfile.tempdir
            tempfile.tempdir = tmp
            try:
                runs = storage.load_pyst(path)
            finally:
                tempfile.tempdir = old
            self.assertEqual(len(runs), 1)
            self.assertEqual(sorted(os.listdir(tmp)), ["x.pyst"])


class Stand030(unittest.TestCase):
    """Zange und Basis-ESP 0.3.0"""

    def test_esp_fw_mit_zusatz(self):
        lk = link.DynoLink("SIM:basis")
        try:
            lk.info()
            self.assertEqual(flash.device_versions(lk)[flash.BASIS_ESP], "0.3.0")
        finally:
            lk.close()
        self.assertEqual(flash._VERSION.search("basis-esp 0.3.0 h255").group(0), "0.3.0")
        self.assertEqual(flash.version_text("0.3.0", "0.3.0")[1], "gleich")

    def test_stat_der_zange(self):
        lk = link.DynoLink("SIM:zange")
        try:
            lk.info()
            lines = pruef.read_stat_zange(lk)
            self.assertEqual(len(lines), 2)                       # #STAT;tx_ok bleibt die letzte Zeile
            self.assertTrue(lines[-1].startswith("#STAT;tx_ok"))
            c = {x.name: x for x in pruef.run_all(lk)}
            self.assertIn("Funken ISR / RMT", c)
            self.assertEqual(c["Log-Verlust"].ergebnis, pruef.OK)
            self.assertIn("61240", c["Heap frei / kleinster"].wert)
        finally:
            lk.close()

    def test_stat_alter_und_kuenftiger_zangen(self):
        caps = {"fw": "PyST-Zange 0.1.3"}
        z = ["#ZANGE;vbus=1;akku_mv=4100;akku_pct=90;empf_mv=200", "#ZANGE;rec=0;gekoppelt=1;sync=1"]
        alt = pruef.check_zange_usb(caps, z, ["#STAT;funken=1;verworfen=0", "#STAT;tx_ok=1;tx_fehler=0;brownouts=0;reset=usb"])
        self.assertNotIn("Log-Verlust", [c.name for c in alt])
        neu = pruef.check_zange_usb(caps, z, ["#STAT;funken=1;gibtsnoch=7;isr_funken=x;rmt_funken=y",
                                              "#STAT;tx_ok=1;reset=task_wdt;heap_min=?;log_verlust=3;weiter"])
        c = {x.name: x for x in neu}
        self.assertEqual(c["Log-Verlust"].ergebnis, pruef.WARN)
        self.assertEqual(c["Letzter Reset"].wert, "task_wdt")


class FakeBasis:
    """Messport, der auf '#ESP BRIDGE 1' mit festen Zeilen antwortet."""

    def __init__(self, text=b""):
        self.text, self.sent, self.closed = text, b"", False

    def write(self, data):
        self.sent += data

    def read(self, n):
        out, self.text = self.text[:n], self.text[n:]
        return out

    def close(self):
        self.closed = True


class Basis030(unittest.TestCase):
    def raw(self, ser, line):
        ser.write(line.encode() + b"\r\n")
        time.sleep(0.02)
        return ser.read(4096).decode()

    def test_tcp_ohne_anmeldung(self):
        ser = sim.BasisSimSerial(tcp=True, auth=sim.AuthSim())
        self.addCleanup(ser.close)
        self.assertEqual(self.raw(ser, "v"), "")                  # Einzelzeichen bleiben ohne Antwort
        self.assertIn("#CAP", self.raw(ser, "#?"))
        self.assertIn("#ERR;RUN;anmeldung", self.raw(ser, "#RUN 1"))

    def test_hex_gross_und_klein(self):
        key = "ab" * 32
        self.assertEqual(auth.response(key, b"1" * 16, "X"), auth.response(key.upper(), b"1" * 16, "X"))
        for nonce in ("AB" * 16, "ab" * 16):
            self.assertEqual(auth.parse_challenge(f"#AUTH;id=A1;nonce={nonce}"), ("A1", bytes.fromhex("ab" * 16)))
        self.assertEqual(auth.parse_key_line("#AUTH;id=A1;key=" + key.upper()), ("A1", key))

    def test_auth_ohne_ok_abschluss(self):
        self.assertFalse(link.is_foreign_reply("AUTH", "#AUTH;id=A1;nonce=" + "AB" * 16))
        self.assertFalse(link.is_foreign_reply("AUTH", "#ERR;AUTH;nein"))
        self.assertTrue(link.is_foreign_reply("AUTH", "#ERR;RUN;anmeldung"))
        self.assertTrue(link.is_foreign_reply("AUTH", "#OK;RUN;1"))

    def test_fehlertexte(self):
        for line, teil in (("#ERR;RUN;anmeldung", "Anmeldung"), ("#ERR;WBF;nur_usb", "nur per USB"),
                           ("#ERR;AUTH;nein", "Sperre"), ("#ERR;AUTH;kein_schluessel", "keinen Schlüssel"),
                           ("#ERR;AUTH;rng", "Zufallsgenerator"), ("#ERR;AUTH;flash", "Flash"),
                           ("#ERR;AUTH;messung laeuft", "Messung"), ("#ERR;AUTH;syntax", "versteht"),
                           ("#ERR;NET;passwort", "offenes WLAN"), ("#ERR;ESP;messung laeuft", "Messung"),
                           ("#ERR;XY;neu", "(neu)"), ("#ERR", "ohne Grund")):
            with self.subTest(line):
                self.assertIn(teil, auth.refusal_text(line))

    def test_neuer_schluessel_waehrend_messung(self):
        lk = link.DynoLink("SIM:basis")
        self.addCleanup(lk.close)
        lk.info()
        self.assertTrue(lk.start())
        with self.assertRaisesRegex(auth.AuthError, "Messung"):
            lk.renew_key()

    def test_sperre_verdoppelt_bis_60_s(self):
        a = sim.AuthSim(sperre_s=1.0)
        lange = []
        for _ in range(9):
            a.gesperrt_bis = 0.0
            a.pruefe(b"1" * 16, "00")
            lange.append(round(a.gesperrt_bis - time.time()))
        self.assertEqual(lange, [1, 2, 4, 8, 16, 32, 60, 60, 60])

    def test_in_der_sperre_auch_die_anfrage_abgelehnt(self):
        a = sim.AuthSim(sperre_s=30.0)
        ser = sim.BasisSimSerial(tcp=True, auth=a)
        self.addCleanup(ser.close)
        self.assertIn("#AUTH;id=", self.raw(ser, "#AUTH"))
        self.assertIn("#ERR;AUTH;nein", self.raw(ser, "#AUTH " + "0" * 64))
        self.assertIn("#ERR;AUTH;nein", self.raw(ser, "#AUTH"))
        self.assertEqual(a.fehler, 1)                             # die Anfrage allein verlaengert nichts

    def test_stat_der_basis(self):
        lk = link.DynoLink("SIM:basis")
        self.addCleanup(lk.close)
        lk.info()
        c = {x.name: x for x in pruef.run_all(lk)}
        self.assertEqual(c["Anmeldung: Fehlversuche"].ergebnis, pruef.OK)
        self.assertEqual(c["Verluste Link/Ausgabe/DMA/CAN"].ergebnis, pruef.OK)
        self.assertEqual(c["Kleinster Stack-Rest"].wert, "idle: 96 B")
        self.assertIn("12 V", c)                                  # die Zeile u12 kam trotz der neuen Zeilen davor

    def bruecke(self, ser, **kw):
        with mock.patch.object(flash, "open_serial", return_value=ser):
            flash.esp_bridge_on("X", **kw)

    def test_bruecke_ein(self):
        ser = FakeBasis(b"123;4;5;6\r\n#OK;ESP;BRIDGE;1\r\n")
        self.bruecke(ser)
        self.assertEqual(ser.sent, b"#ESP BRIDGE 1\r\n")
        self.assertTrue(ser.closed)

    def test_bruecke_abgelehnt(self):
        for text, teil in ((b"#ERR;ESP;messung laeuft\r\n", "Messung"), (b"#ERR;?;unbekannt\r\n", "aktualisieren"),
                           (b"#ERR;ESP;nur_usb\r\n", "nur per USB"), (b"", "antwortet nicht")):
            ser = FakeBasis(text)
            with self.subTest(text), self.assertRaisesRegex(flash.FlashError, teil):
                self.bruecke(ser, timeout=0.05)
            self.assertTrue(ser.closed)

    def test_bruecke_nur_per_usb(self):
        with self.assertRaisesRegex(flash.FlashError, "nur per USB"):
            flash.esp_bridge_on(None)
        with mock.patch.object(flash, "open_serial", side_effect=OSError("belegt")):
            with self.assertRaisesRegex(flash.FlashError, "belegt"):
                flash.esp_bridge_on("X")

    def test_bruecke_am_simulator(self):
        ser = sim.BasisSimSerial(auth=sim.AuthSim())
        self.bruecke(ser, timeout=0.5)
        self.assertTrue(ser.esp_bridge)
        ser = sim.BasisSimSerial(auth=sim.AuthSim())
        self.raw(ser, "#RUN 1")
        with self.assertRaisesRegex(flash.FlashError, "Messung"):
            self.bruecke(ser, timeout=0.5)
        self.assertFalse(ser.esp_bridge)


class FlashSeiteEsp(unittest.TestCase):
    """Die Bruecke wartet nur 30 s auf esptool: erst nach der Rueckfrage einschalten, nie ohne Anwender."""

    def setUp(self):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6 import QtWidgets
        from pyst import gui
        self.qt = QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.w = gui.MainWindow()
        self.addCleanup(close_window, self.w, self.app)
        self.page = self.w.page_flash
        self.calls = mock.Mock()
        for p in (mock.patch.object(flash, "build_command", return_value=(mock.Mock(), ["esptool"])),
                  mock.patch.object(flash, "basis_port", return_value="X"),
                  mock.patch.object(flash, "esp_bridge_on", self.calls.bruecke),
                  mock.patch.object(self.page, "run", self.calls.run),
                  mock.patch.object(self.page, "run_usb", self.calls.run_usb)):
            p.start()
            self.addCleanup(p.stop)

    def starte(self, antwort, key=flash.BASIS_ESP):
        with mock.patch.object(self.qt.QMessageBox, "question", self.calls.frage):
            self.calls.frage.return_value = antwort
            self.page.start(key)
        return [c[0] for c in self.calls.mock_calls]

    def test_erst_frage_dann_bruecke_dann_esptool(self):
        self.assertEqual(self.starte(self.qt.QMessageBox.Yes), ["frage", "bruecke", "run"])
        self.calls.bruecke.assert_called_once_with("X")

    def test_nein_schaltet_nichts(self):
        self.assertEqual(self.starte(self.qt.QMessageBox.No), ["frage"])

    def test_abgelehnt_startet_kein_esptool(self):
        self.calls.bruecke.side_effect = flash.FlashError(flash.ESP_BUSY)
        self.assertEqual(self.starte(self.qt.QMessageBox.Yes), ["frage", "bruecke"])
        self.assertIn("anhalten", self.page.status.text())

    def test_zange_braucht_keine_bruecke(self):
        self.assertEqual(self.starte(self.qt.QMessageBox.Yes, flash.ZANGE), ["frage", "run"])


class Abhaengigkeiten(unittest.TestCase):
    def test_link_laedt_den_simulator_nicht(self):
        code = "import sys; import pyst.link; sys.exit('pyst.sim' in sys.modules)"
        root = os.path.join(os.path.dirname(__file__), "..")
        self.assertEqual(subprocess.run([sys.executable, "-c", code], cwd=root).returncode, 0)

    def test_simulator_kommt_bei_bedarf(self):
        for port, typ in (("SIM", sim.SimSerial), ("SIM:zange", sim.ZangeUsbSimSerial), ("SIM:basis", sim.BasisSimSerial)):
            lk = link.DynoLink(port)
            self.addCleanup(lk.close)
            self.assertIsInstance(lk.ser, typ)
            self.assertIsNone(lk.replay)
        lk = link.DynoLink("SIM:161802")
        self.addCleanup(lk.close)
        self.assertIsNotNone(lk.replay)


class Felder(unittest.TestCase):
    def test_kv(self):
        self.assertEqual(bootproto.parse_info("#BOOTLOADER;proto=1;bl=1.0.0;x\r\n"), {"proto": "1", "bl": "1.0.0"})
        self.assertIs(diag.parse_kv, felder.parse_kv)

    def test_zahlen(self):
        self.assertEqual(felder.to_float("1.5"), 1.5)
        self.assertTrue(math.isnan(felder.to_float("x")))
        self.assertTrue(math.isnan(felder.to_float(None)))
        self.assertEqual(felder.to_int("12.9"), 12)
        for bad in ("x", None, "inf", "nan"):
            self.assertEqual(felder.to_int(bad, -1), -1)


if __name__ == "__main__":
    unittest.main()
