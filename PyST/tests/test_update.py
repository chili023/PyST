"""Tests Update-Prüfung: Manifest, Versionsvergleich, Download gegen einen lokalen HTTP-Server."""
import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from pyst import bootproto as bp, fw_keys, update  # noqa: E402
import fw2_testdatei as fw2  # noqa: E402

DATA = fw2.format2("0.2.3", size=4096)
SHA = hashlib.sha256(DATA).hexdigest()
SIG = DATA[bp.SIG_OFS:bp.SIG_END].hex()
FREMD = fw2.format2("0.2.3", size=4096, seed=fw2.FREMDER_TESTSCHLUESSEL_ED25519)
UNSIGNIERT = fw2.format1("0.2.3")
MUELL = b"PyST-Firmware" * 100


def manifest(**over):
    e = {"version": "0.2.3", "file": "basis.pystfw", "size": len(DATA), "sha256": SHA, "signature": SIG}
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
        for name, data in (("fremd.pystfw", FREMD), ("format1.pystfw", UNSIGNIERT), ("muell.pystfw", MUELL)):
            with open(os.path.join(cls.dir.name, name), "wb") as fh:
                fh.write(data)
        with open(os.path.join(cls.dir.name, update.MANIFEST_NAME), "w") as fh:
            fh.write(manifest())
        d = cls.dir.name

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=d, **k)

            def do_GET(self):
                if self.path.endswith("abriss.pystfw"):      # Verbindung reisst mitten in der Datei ab
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(DATA)))
                    self.end_headers()
                    self.wfile.write(DATA[:100])
                    self.close_connection = True
                    return
                super().do_GET()

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

    def setUp(self):
        keys = mock.patch.dict(fw_keys.KNOWN_KEYS, {fw2.TESTSCHLUESSEL_ID: fw2.public_key().hex()})
        keys.start()
        self.addCleanup(keys.stop)

    def entry(self, **over):
        return update.parse_manifest(manifest(**over))["basis_stm"]

    def abgelehnt(self, text, **over):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(update.UpdateError, text):
                update.download(self.entry(**over), t, self.base)
            self.assertEqual(os.listdir(t), [])

    def datei(self, data, name):
        return {"file": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "signature": data[bp.SIG_OFS:bp.SIG_END].hex()}

    def test_manifest_signatur_passt_nicht(self):
        self.abgelehnt("passt nicht zur Datei", signature="00" * 64)
        self.abgelehnt("passt nicht zur Datei", signature="kaputt")

    def test_manifest_ohne_signatur(self):
        self.abgelehnt("ohne Signatur", signature="")
        e = json.loads(manifest())
        del e["targets"]["basis_stm"]["signature"]
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(update.UpdateError, "ohne Signatur"):
                update.download(update.parse_manifest(json.dumps(e))["basis_stm"], t, self.base)
            self.assertEqual(os.listdir(t), [])

    def test_signatur_grossgeschrieben(self):
        with tempfile.TemporaryDirectory() as t:
            update.download(self.entry(signature=SIG.upper()), t, self.base)
            self.assertEqual(os.listdir(t), ["basis.pystfw"])

    def test_falsche_signatur(self):
        self.abgelehnt("Signatur ungültig", **self.datei(FREMD, "fremd.pystfw"))

    def test_unbekannter_schluessel(self):
        with mock.patch.dict(fw_keys.KNOWN_KEYS, clear=True):
            self.abgelehnt("Unbekannter Schlüssel")

    def test_unsignierte_datei(self):
        self.abgelehnt("Unsignierte", **self.datei(UNSIGNIERT, "format1.pystfw"))

    def test_keine_update_datei(self):
        self.abgelehnt("keine Update-Datei", **{**self.datei(MUELL, "muell.pystfw"), "signature": SIG})

    def test_cryptography_fehlt(self):
        with mock.patch.dict(sys.modules, {"cryptography.hazmat.primitives.asymmetric.ed25519": None}):
            self.abgelehnt("cryptography")

    def test_abriss_laesst_nichts_liegen(self):
        self.abgelehnt("Größe|fehlgeschlagen", file="abriss.pystfw")

    def test_andere_ziele_bleiben_ohne_signatur(self):
        """Lambda: Weg wie bisher, nur Groesse und SHA-256."""
        e = update.Entry("lambda_f042", "1.0", "format1.pystfw", len(UNSIGNIERT), hashlib.sha256(UNSIGNIERT).hexdigest())
        with tempfile.TemporaryDirectory() as t:
            update.download(e, t, self.base)
            self.assertEqual(os.listdir(t), ["format1.pystfw"])

    def test_vorhandene_datei_bleibt_bei_fehlschlag(self):
        """Eine frueher geladene, gepruefte Datei gleichen Namens wird nicht angefasst."""
        with tempfile.TemporaryDirectory() as t:
            path = update.download(self.entry(), t, self.base)
            with self.assertRaises(update.UpdateError):
                update.download(self.entry(signature="00" * 64), t, self.base)
            self.assertEqual(os.listdir(t), ["basis.pystfw"])
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), DATA)

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


class Zweige(unittest.TestCase):
    """Update-Quelle: Branch fuer Software und Firmware (Expertenmodus)."""

    def test_namen_und_adressen(self):
        for ok in ("main", "firmware", "release/1.4.2", "ui6/start", "v1.4.1", "feature_x-2"):
            self.assertTrue(update.zweig_ok(ok), ok)
        for falsch in ("", "../main", "a..b", "/main", "main/", "x y", "main?a=1", "a\nb", "x.lock", "-main"):
            self.assertFalse(update.zweig_ok(falsch), falsch)
        self.assertEqual(update.version_url(), update.VERSION_URL)
        self.assertEqual(update.firmware_url(), update.FIRMWARE_URL)
        self.assertEqual(update.version_url("release/1.4.2"),
                         "https://raw.githubusercontent.com/chili023/PyST/release/1.4.2/PyST/pyst/__init__.py")
        self.assertIsNone(update.latest_pyst(zweig="../x"))               # ungueltig: gar nicht erst abfragen

    def test_zweige_von_github(self):
        class Antwort:
            def __init__(self, data):
                self.data = data

            def read(self, n):
                return self.data[:n]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        data = json.dumps([{"name": "main"}, {"name": "release/1.4.2"}, {"name": "../boese"}, {"x": 1},
                           {"name": "firmware"}]).encode()
        with mock.patch.object(update.urllib.request, "urlopen", lambda *a, **k: Antwort(data)):
            self.assertEqual(update.zweige(), ["firmware", "main", "release/1.4.2"])
        with mock.patch.object(update.urllib.request, "urlopen", lambda *a, **k: Antwort(b"kein json")):
            self.assertIsNone(update.zweige())

    def test_seite_einstellen_speichern_und_alte_ergebnisse_verwerfen(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        from pyst.flash_page import FlashPage
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        settings, saved = {}, []
        page = FlashPage(settings, lambda: saved.append(1), lambda: None, lambda: None)
        page.timer.stop()
        page.check_updates = lambda v: None                               # kein Netz im Test
        self.assertEqual((page.sw_zweig, page.fw_zweig), ("main", "firmware"))
        self.assertIn("› firmware", page.quelle.text())
        self.assertTrue(page.set_zweige("release/1.4.2", "firmware-test"))
        self.assertEqual(settings["update_zweig"], {"software": "release/1.4.2", "firmware": "firmware-test"})
        self.assertEqual(page.update_base, update.firmware_url("firmware-test"))
        self.assertIn("PyST aus release/1.4.2", page.quelle.text())
        self.assertIn("Test-Branch", page.zweig_hinweis.text())
        self.assertTrue(saved)
        self.assertFalse(page.set_zweige("../x", "firmware"))            # ungueltig: bleibt beim alten
        self.assertEqual(page.sw_zweig, "release/1.4.2")
        self.assertEqual(page.zweig_sw.currentText(), "release/1.4.2")
        self.assertIn("Ungültig", page.zweig_hinweis.text())
        # Ergebnis aus dem alten Branch kommt zu spaet: verwerfen, neu suchen
        neu = []
        page.check_updates = lambda v: neu.append(v)
        page._pyst_ergebnis(("main", "9.9.9"), "1.4.2")
        self.assertEqual(neu, ["1.4.2"])
        self.assertIsNone(page.pyst_remote)
        page._pyst_ergebnis(("release/1.4.2", "1.4.3"), "1.4.2")
        self.assertEqual(page.pyst_remote, "1.4.3")
        self.assertIn("git checkout release/1.4.2", page.pyst_karte.unten.text())
        # nach Neustart wieder da, Standard-Knopf loescht den Eintrag
        page2 = FlashPage(settings, lambda: None, lambda: None, lambda: None)
        page2.timer.stop()
        page2.check_updates = lambda v: None
        self.assertEqual((page2.sw_zweig, page2.fw_zweig), ("release/1.4.2", "firmware-test"))
        page2.b_zweig_std.click()
        self.assertNotIn("update_zweig", settings)
        self.assertEqual(page2.update_base, update.FIRMWARE_URL)
        app.processEvents()


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
