"""Tests Update-Prüfung: Manifest, Versionsvergleich, Download gegen einen lokalen HTTP-Server."""
import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyst import update  # noqa: E402

DATA = b"PyST-Firmware" * 100
SHA = hashlib.sha256(DATA).hexdigest()


def manifest(**over):
    e = {"version": "0.2.3", "file": "basis.pystfw", "size": len(DATA), "sha256": SHA, "signature": ""}
    e.update(over)
    return json.dumps({"targets": {"basis_stm": e}})


class Versionen(unittest.TestCase):
    def test_vergleich(self):
        self.assertTrue(update.is_newer("1.4", "1.3"))
        self.assertTrue(update.is_newer("1.10", "1.9"))          # Zahlen, nicht Text
        self.assertFalse(update.is_newer("1.3", "1.3.0"))
        self.assertFalse(update.is_newer("1.2", "1.3"))          # eigener Branch ist neuer als main
        self.assertFalse(update.is_newer("kaputt", "1.3"))


class Manifest(unittest.TestCase):
    def test_gueltig(self):
        e = update.parse_manifest(manifest())["basis_stm"]
        self.assertEqual((e.version, e.file, e.size, e.sha256), ("0.2.3", "basis.pystfw", len(DATA), SHA))

    def test_kaputt(self):
        for text in ("kein json", "{}", '{"targets": 1}'):
            with self.assertRaises(update.UpdateError):
                update.parse_manifest(text)

    def test_ungueltige_eintraege_fallen_weg(self):
        for over in ({"file": "../boese.bin"}, {"file": "a/b"}, {"sha256": "abc"}, {"size": 0},
                     {"size": update.FILE_MAX + 1}, {"version": "neu"}):
            self.assertEqual(update.parse_manifest(manifest(**over)), {}, over)


class Download(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        with open(os.path.join(cls.dir.name, "basis.pystfw"), "wb") as fh:
            fh.write(DATA)
        with open(os.path.join(cls.dir.name, update.MANIFEST_NAME), "w") as fh:
            fh.write(manifest())
        d = cls.dir.name

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=d, **k)

            def log_message(self, *a):
                pass

        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.dir.cleanup()

    def entry(self, **over):
        return update.parse_manifest(manifest(**over))["basis_stm"]

    def test_manifest_und_datei(self):
        got = update.fetch_manifest(base=self.base)
        self.assertEqual(got["basis_stm"].version, "0.2.3")
        with tempfile.TemporaryDirectory() as t:
            seen = []
            path = update.download(got["basis_stm"], t, self.base, progress=lambda a, b: seen.append((a, b)))
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), DATA)
            self.assertEqual(seen[-1], (len(DATA), len(DATA)))
            self.assertEqual(os.listdir(t), ["basis.pystfw"])

    def test_fehlender_branch_bleibt_still(self):
        self.assertIsNone(update.fetch_manifest(base=self.base + "/gibtsnicht"))
        self.assertIsNone(update.fetch_manifest(timeout=0.2, base="http://127.0.0.1:9"))

    def test_falsche_datei_bleibt_nicht_liegen(self):
        for over, text in (({"sha256": "0" * 64}, "Prüfsumme"), ({"size": len(DATA) + 1}, "Größe"),
                           ({"size": 10}, "größer")):
            with tempfile.TemporaryDirectory() as t:
                with self.assertRaisesRegex(update.UpdateError, text):
                    update.download(self.entry(**over), t, self.base)
                self.assertEqual(os.listdir(t), [])

    def test_datei_fehlt(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(update.UpdateError):
                update.download(self.entry(file="fehlt.bin"), t, self.base)
            self.assertEqual(os.listdir(t), [])


class Oberflaeche(unittest.TestCase):
    """Reiter Firmware: Anzeige und Download, im Offscreen-Betrieb gegen den lokalen Server."""

    def test_anzeige_und_download(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        from pyst.flash import BASIS_STM
        from pyst.flash_page import FlashPage
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        saved = []
        page = FlashPage({}, lambda: saved.append(1), lambda: None, lambda: None)
        page.timer.stop()
        row = page.rows[BASIS_STM]
        entries = update.parse_manifest(manifest())
        row.current = "0.2.2"
        page._manifest(entries, None)
        self.assertIn("0.2.3 (Update verfügbar)", row.remote.text())
        self.assertFalse(row.b_load.isHidden())
        row.current = "0.2.3"
        page._show_remote()
        self.assertNotIn("Update", row.remote.text())
        page._manifest(None, None)                       # Branch fehlt: nichts anzeigen
        self.assertTrue(row.remote.isHidden() and row.b_load.isHidden())
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "basis.pystfw")
            with open(path, "wb") as fh:
                fh.write(DATA)
            page._downloaded(BASIS_STM, path, None)
            self.assertEqual(row.path.text(), path)
            self.assertTrue(saved)
            page._downloaded(BASIS_STM, None, update.UpdateError("Prüfsumme stimmt nicht"))
            self.assertIn("fehlgeschlagen", page.status.text())
        seen = []
        page.pyst_update.connect(seen.append)
        page._pyst_seen("1.4", "1.3")
        page._pyst_seen("1.2", "1.3")
        page._pyst_seen(None, "1.3")
        self.assertEqual(seen, ["1.4"])
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
