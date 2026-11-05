"""Arbeit neben der Oberflaeche (pyst/bg.py) und Dialoge, die nicht mehr blockieren (Code-Review M10)."""
import os
import sys
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PySide6 import QtCore, QtWidgets  # noqa: E402

from pyst import bg, link  # noqa: E402
from pyst.hw_page import ZangeSettingsDialog  # noqa: E402
from pyst.oszi import DeviceSettingsDialog  # noqa: E402


def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class BgThread(unittest.TestCase):
    def poll_until(self, b, cond):
        end = time.time() + 2.0
        while time.time() < end and not cond():
            b.poll()
            time.sleep(0.005)

    def test_ergebnis_und_ausnahme(self):
        b = bg.Bg()
        got = []
        b.submit("a", lambda: 41 + 1, lambda r, e: got.append((r, e)))
        b.submit("b", lambda: 1 / 0, lambda r, e: got.append((r, type(e))))
        self.poll_until(b, lambda: len(got) == 2)
        self.assertEqual(got, [(42, None), (None, ZeroDivisionError)])
        self.assertFalse(b.pending)
        b.submit("c", lambda: "lebt", lambda r, e: got.append(r))         # der Thread ueberlebt die Ausnahme
        self.poll_until(b, lambda: len(got) == 3)
        self.assertEqual(got[-1], "lebt")

    def test_gleicher_schluessel_nur_einmal(self):
        b = bg.Bg()
        halt = threading.Event()
        got = []
        b.submit("x", lambda: halt.wait(2), lambda r, e: got.append(1))
        b.submit("x", lambda: None, lambda r, e: got.append(2))
        halt.set()
        self.poll_until(b, lambda: bool(got))
        time.sleep(0.02)
        b.poll()
        self.assertEqual(got, [1])


class WaitInThread(unittest.TestCase):
    def setUp(self):
        self.app = app()

    def test_ergebnis(self):
        self.assertEqual(bg.wait_in_thread(lambda: threading.current_thread() is threading.main_thread()), False)
        self.assertFalse(bg.waiting())

    def test_ausnahme_beim_aufrufer(self):
        with self.assertRaises(ZeroDivisionError):
            bg.wait_in_thread(lambda: 1 / 0)
        self.assertFalse(bg.waiting())

    def test_oberflaeche_laeuft_weiter(self):
        takte = []
        t = QtCore.QTimer()
        t.setInterval(10)
        t.timeout.connect(lambda: takte.append(bg.waiting()))
        t.start()
        ticks = []
        bg.wait_in_thread(lambda: time.sleep(0.2), lambda: ticks.append(1))
        t.stop()
        self.assertGreater(len(takte), 5)
        self.assertTrue(all(takte))
        self.assertGreater(len(ticks), 5)


class Dialoge(unittest.TestCase):
    def setUp(self):
        self.app = app()
        self.l = link.DynoLink("SIM:basis")
        self.l.info()

    def tearDown(self):
        self.l.close()

    def test_zangen_dialog(self):
        d = ZangeSettingsDialog(self.l)
        self.assertEqual(d.table.rowCount(), 12)
        self.assertIn("12 von 12", d.msg.text())
        for r in range(d.table.rowCount()):
            if d.table.item(r, 0).text() == "deadtime_us":
                d.table.item(r, 1).setText("400")
        self.assertTrue(d.apply())
        self.assertIn("1 Einstellung(en) übertragen", d.msg.text())
        self.assertEqual(self.l.ser.z_settings["deadtime_us"], "400")

    def test_zange_weg_bricht_das_lesen_ab(self):
        self.l.ser.z_conn = False
        t = []
        echt = self.l.command

        def zaehlt(cmd, **kw):
            t.append(cmd)
            return []
        self.l.command = zaehlt
        d = ZangeSettingsDialog(self.l)
        self.l.command = echt
        self.assertEqual(len(t), 2)                                       # statt 12 x 2,5 s
        self.assertIn("Keine Antwort", d.msg.text())

    def test_geraete_dialog(self):
        d = DeviceSettingsDialog(self.l)
        self.assertGreater(d.table.rowCount(), 3)
        self.assertTrue(d.apply())                                        # nichts geaendert
        d.save()
        self.assertIn("gespeichert", d.msg.text())


if __name__ == "__main__":
    unittest.main()
