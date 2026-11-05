"""Anmeldung am TCP-Port der Basis (pyst/auth.py), Schluesselablage, WLAN-Regeln."""
import hashlib
import importlib
import json
import logging
import os
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyst import auth, basis, link, sim  # noqa: E402
from pyst.auth import AuthError, AuthNoReply, KeyStore  # noqa: E402
from conftest import close_window  # noqa: E402


def hmac_von_hand(key: bytes, msg: bytes) -> str:
    """HMAC-SHA256 nach RFC 2104, unabhaengig vom Modul hmac."""
    k = key.ljust(64, b"\0")
    inner = hashlib.sha256(bytes(b ^ 0x36 for b in k) + msg).digest()
    return hashlib.sha256(bytes(b ^ 0x5C for b in k) + inner).hexdigest()


class Rechnen(unittest.TestCase):
    def test_antwort(self):
        key = bytes(range(32))
        nonce = bytes(range(100, 116))
        soll = hmac_von_hand(key, b"PyST-Auth-1" + nonce + b"ABC123")
        self.assertEqual(auth.response(key.hex(), nonce, "ABC123"), soll)
        self.assertEqual(len(soll), 64)

    def test_zeilen(self):
        key = "ab" * 32
        self.assertEqual(auth.parse_key_line(f"#AUTH;id=U1;key={key.upper()}"), ("U1", key))
        self.assertIsNone(auth.parse_key_line("#AUTH;id=U1;key=abcd"))
        self.assertIsNone(auth.parse_key_line(f"#AUTH;id=;key={key}"))
        self.assertIsNone(auth.parse_key_line(f"#OK;AUTH;id=U1;key={key}"))
        self.assertEqual(auth.parse_challenge("#AUTH;id=U1;nonce=" + "0f" * 16), ("U1", b"\x0f" * 16))
        self.assertIsNone(auth.parse_challenge("#AUTH;id=U1;nonce=0f"))
        self.assertIsNone(auth.parse_challenge("#AUTH;id=U 1;nonce=" + "0f" * 16))
        self.assertIsNone(auth.parse_challenge("#AUTH;id=U1;nonce=" + "zz" * 16))

    def test_ablehnung(self):
        self.assertEqual(auth.refusal_text("#ERR;AUTH;nein"), auth.TEXT_GESPERRT)
        self.assertEqual(auth.refusal_text("#ERR;AUTH;kein_schluessel"), auth.TEXT_BASIS_OHNE)
        self.assertIn("Zufallsgenerator", auth.refusal_text("#ERR;AUTH;rng"))

    def test_redact(self):
        key = "ab" * 32
        self.assertNotIn(key, auth.redact(f"#AUTH;id=U1;key={key}"))
        self.assertIn("U1", auth.redact(f"#AUTH;id=U1;key={key}"))


class Ablage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "unter", "schluessel.json")
        self.store = KeyStore(self.path)

    def test_je_id_und_rechte(self):
        self.assertIsNone(self.store.get("A"))
        self.assertTrue(self.store.put("A", "aa" * 32))
        self.assertTrue(self.store.put("B", "BB" * 32))
        self.assertFalse(self.store.put("B", "bb" * 32))                  # unveraendert
        self.assertEqual(self.store.get("A"), "aa" * 32)
        self.assertEqual(self.store.get("B"), "bb" * 32)
        self.assertTrue(self.store.put("A", "cc" * 32))
        self.assertEqual(json.load(open(self.path)), {"A": "cc" * 32, "B": "bb" * 32})
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["schluessel.json"])     # keine Reste

    def test_zu_weite_rechte_werden_enger(self):
        self.store.put("A", "aa" * 32)
        os.chmod(self.path, 0o644)
        self.assertEqual(self.store.get("A"), "aa" * 32)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_kaputte_datei(self):
        os.makedirs(os.path.dirname(self.path))
        for inhalt in ("{kaputt", "[1, 2]", '{"A": 5, "B": "kurz"}'):
            with open(self.path, "w") as fh:
                fh.write(inhalt)
            self.assertIsNone(self.store.get("A"))
            self.assertIsNone(self.store.get("B"))

    def test_ungueltige_werte(self):
        for uid, key in (("", "aa" * 32), ("A B", "aa" * 32), ("A", "aa"), ("A", "zz" * 32)):
            with self.assertRaises(ValueError):
                self.store.put(uid, key)

    def test_schreibfehler_laesst_alte_datei_stehen(self):
        self.store.put("A", "aa" * 32)
        with mock.patch("os.replace", side_effect=OSError("Platte voll")):
            with self.assertRaises(OSError):
                self.store.put("B", "bb" * 32)
        self.assertEqual(self.store.get("A"), "aa" * 32)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["schluessel.json"])


class Protokoll(unittest.TestCase):
    """Basis-Simulator: USB ('SIM:basis') und TCP-Port ('SIM:basis:wlan') teilen Schluessel und Sperre."""

    def setUp(self):
        sim.SIM_AUTH.neu()
        sim.SIM_AUTH.sperre_s = 0.3
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = KeyStore(os.path.join(self.tmp.name, "schluessel.json"))
        self.links = []

    def tearDown(self):
        for lk in self.links:
            lk.close()
        sim.SIM_AUTH.neu()
        sim.SIM_AUTH.sperre_s = 1.0

    def open(self, port):
        lk = link.DynoLink(port, keys=self.store)
        self.links.append(lk)
        return lk

    def usb_koppeln(self):
        lk = self.open("SIM:basis")
        lk.info()
        self.assertTrue(lk.fetch_key())
        self.assertFalse(lk.fetch_key())                                  # beim zweiten Mal nichts Neues
        return lk

    def test_koppeln_und_anmelden(self):
        self.usb_koppeln()
        self.assertEqual(self.store.get(sim.SIM_AUTH.uid), sim.SIM_AUTH.key.hex())
        lk = self.open("SIM:basis:wlan")
        self.assertIn("PyST-Basis", lk.info())
        self.assertEqual(lk.auth_state, "ok")
        self.assertTrue(lk.ser.authed)
        self.assertTrue(lk.ok(lk.command("#RUN 0")))

    def test_ohne_anmeldung_nur_fragezeichen_und_auth(self):
        lk = self.open("SIM:basis:wlan")
        lk.probe_caps()
        self.assertIn("fw", lk.caps)                                      # '#?' geht
        for word in ("RUN 1", "GET", "LAMBDA 1", "NET", "DOSE", "SAVE", "ZANGE MARK", "GIBTSNICHT"):
            self.assertEqual(lk.command("#" + word, done=lambda x: x.startswith("#ERR"), timeout=0.5),
                             [f"#ERR;{word.split()[0]};anmeldung"])
        lk.send("v")
        lk.send("m")
        time.sleep(0.15)
        self.assertEqual(lk.frames.qsize(), 0)
        self.assertTrue(lk.text_lines.empty())

    def test_nur_usb(self):
        self.usb_koppeln()
        lk = self.open("SIM:basis:wlan")
        lk.info()
        for cmd, word in (("#LAMBDA SWD", "LAMBDA"), ("#WBF", "WBF"), ("#WBF START", "WBF"), ("#UPDATE", "UPDATE"),
                          ("#ESP BRIDGE 1", "ESP"), ("#ESP BOOT", "ESP"), ("#AUTH KEY", "AUTH"), ("#AUTH NEU", "AUTH")):
            self.assertEqual(lk.command(cmd, timeout=0.5), [f"#ERR;{word};nur_usb"])
        self.assertEqual(lk.lambda_status().st, "aus")                    # Abfrage geht
        self.assertIsNone(lk.fetch_key())                                 # PyST versucht es gar nicht
        self.assertFalse(lk.renew_key())

    def test_kein_schluessel(self):
        lk = self.open("SIM:basis:wlan")
        with self.assertRaises(AuthError) as cm:
            lk.info()
        self.assertIn(sim.SIM_AUTH.uid, str(cm.exception))
        self.assertIn("USB", str(cm.exception))
        self.assertEqual(sim.SIM_AUTH.fehler, 0)                          # kein Versuch verbrannt
        self.assertEqual(lk.auth_state, "")

    def test_falscher_schluessel_und_wachsende_sperre(self):
        self.store.put(sim.SIM_AUTH.uid, "11" * 32)
        lk = self.open("SIM:basis:wlan")
        with self.assertRaises(AuthError) as cm:
            lk.info()
        self.assertEqual(str(cm.exception), auth.TEXT_FALSCH)
        self.assertFalse(lk.ser.authed)
        self.assertEqual(sim.SIM_AUTH.fehler, 1)                          # genau ein Versuch, keine Wiederholung
        # waehrend der Sperre gilt auch der richtige Schluessel nicht
        self.store.put(sim.SIM_AUTH.uid, sim.SIM_AUTH.key.hex())
        with self.assertRaises(AuthError) as cm:
            lk.info()
        self.assertEqual(str(cm.exception), auth.TEXT_GESPERRT)
        self.assertEqual(sim.SIM_AUTH.fehler, 1)
        # die Sperre waechst
        self.assertGreater(sim.SIM_AUTH.gesperrt_bis, time.time())
        time.sleep(0.35)
        self.store.put(sim.SIM_AUTH.uid, "22" * 32)
        t0 = time.time()
        with self.assertRaises(AuthError):
            lk.login()
        self.assertEqual(sim.SIM_AUTH.fehler, 2)
        self.assertGreater(sim.SIM_AUTH.gesperrt_bis - t0, 0.55)
        # nach Ablauf klappt es mit dem richtigen Schluessel, die Zaehlung beginnt neu
        time.sleep(0.65)
        self.store.put(sim.SIM_AUTH.uid, sim.SIM_AUTH.key.hex())
        self.assertTrue(lk.login())
        self.assertEqual(sim.SIM_AUTH.fehler, 0)

    def test_antwort_gilt_nur_einmal(self):
        self.usb_koppeln()
        lk = self.open("SIM:basis:wlan")
        lk.probe_caps()
        line = lk.command("#AUTH", done=lambda x: x.startswith("#AUTH;"))[-1]
        uid, nonce = auth.parse_challenge(line)
        antwort = auth.response(self.store.get(uid), nonce, uid)
        self.assertEqual(lk.command(f"#AUTH {antwort}"), ["#OK;AUTH"])
        lk.ser.authed = False
        self.assertEqual(lk.command(f"#AUTH {antwort}"), ["#ERR;AUTH;nein"])

    def test_antwort_gross_geschrieben(self):
        self.usb_koppeln()
        lk = self.open("SIM:basis:wlan")
        lk.probe_caps()
        uid, nonce = auth.parse_challenge(lk.command("#AUTH", done=lambda x: x.startswith("#AUTH;"))[-1])
        self.assertEqual(lk.command("#AUTH " + auth.response(self.store.get(uid), nonce, uid).upper()), ["#OK;AUTH"])

    def test_neuer_schluessel(self):
        usb = self.usb_koppeln()
        alt = self.store.get(sim.SIM_AUTH.uid)
        self.assertTrue(usb.renew_key())
        self.assertNotEqual(self.store.get(sim.SIM_AUTH.uid), alt)
        self.assertEqual(self.store.get(sim.SIM_AUTH.uid), sim.SIM_AUTH.key.hex())
        lk = self.open("SIM:basis:wlan")
        lk.info()
        self.assertEqual(lk.auth_state, "ok")

    def test_firmware_ohne_auth(self):
        lk = self.open("SIM:basis:wlan")
        lk.ser._auth_command = lambda a: lk.ser._out("#ERR;?;unbekannt\r\n")
        lk.ser._tcp_gate = lambda cmd, a: False
        lk.info()
        self.assertEqual(lk.auth_state, "alt")
        self.assertTrue(lk.ok(lk.command("#RUN 0")))

    def test_firmware_mit_kurzer_zeile(self):
        """Die Anmeldezeile hat 70 Zeichen. Bleibt CL_LINE_MAX bei 63, kommt eine verstaendliche Meldung."""
        self.usb_koppeln()
        lk = self.open("SIM:basis:wlan")
        lk.ser.line_max = 63
        with self.assertRaises(AuthError) as cm:
            lk.info()
        self.assertEqual(str(cm.exception), auth.TEXT_ZU_LANG)

    def test_stille_basis(self):
        lk = self.open("SIM:basis:wlan")
        lk.probe_caps()
        lk.ser.write = lambda data: len(data)
        with mock.patch.object(link.DynoLink, "command", return_value=[]):
            with self.assertRaises(AuthError) as cm:
                lk.login()
        self.assertEqual(str(cm.exception), auth.TEXT_KEINE_ANTWORT)

    def test_unlesbare_anfrage(self):
        lk = self.open("SIM:basis:wlan")
        lk.probe_caps()
        with mock.patch.object(link.DynoLink, "command", return_value=["#AUTH;id=U1;nonce=xx"]):
            with self.assertRaises(AuthError) as cm:
                lk.login()
        self.assertEqual(str(cm.exception), auth.TEXT_FORMAT)

    def test_schluessel_nie_im_log(self):
        with self.assertLogs(level=logging.DEBUG) as cm:
            logging.getLogger("pyst").debug("Start")
            usb = self.usb_koppeln()
            key = sim.SIM_AUTH.key.hex()
            usb.ser.vor_antwort = [f"#OK;FREMD;key={key}"]                # fremde Antwort: wird im Log erwaehnt
            usb.command("#GET", timeout=0.3)
            with mock.patch.object(link, "parse_line", side_effect=RuntimeError("x")):
                usb._safe_line(f"#AUTH;id=U1;key={key}")
            lk = self.open("SIM:basis:wlan")
            lk.info()
        self.assertEqual(usb.bad_lines, 1)
        self.assertGreaterEqual(usb.foreign_replies, 1)
        text = "\n".join(cm.output)
        self.assertIn("key=***", text)
        self.assertNotIn(key, text)
        self.assertNotIn(key.upper(), text)


class Wlan(unittest.TestCase):
    def test_offen_abgelehnt(self):
        for mode in ("AP", "STA"):
            with self.assertRaises(ValueError) as cm:
                basis.net_commands(mode, "PyST", "")
            self.assertIn("Offenes WLAN", str(cm.exception))
            with self.assertRaises(ValueError):
                basis.net_commands(mode, "PyST", "kurz")

    def test_vorschlag(self):
        a, b = auth.wlan_password(), auth.wlan_password()
        self.assertNotEqual(a, b)
        self.assertEqual(len(a), 16)
        self.assertFalse(set(a) & set("0O1lI ;"))
        self.assertLessEqual(len(basis.net_commands("AP", "PyST-Basis", a, 13)), basis.MAX_LINE)

    def test_simulator_lehnt_offen_ab(self):
        lk = link.DynoLink("SIM:basis")
        try:
            lk.info()
            for cmd in ("#NET AP 6 PyST", "#NET STA PyST", "#NET AP 6 PyST kurz"):
                self.assertEqual(lk.command(cmd), ["#ERR;NET;passwort"])
            self.assertEqual(lk.command("#NET OFF"), ["#OK;NET;OFF"])
        finally:
            lk.close()


class Oberflaeche(unittest.TestCase):
    def setUp(self):
        sim.SIM_AUTH.neu()
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp.name
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6 import QtWidgets
        self.qt = QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from pyst import gui
        importlib.reload(gui)
        self.w = gui.MainWindow()

    def tearDown(self):
        close_window(self.w, self.app)
        self.w = None
        os.environ["HOME"] = self.old
        self.tmp.cleanup()

    def verbinden(self, port):
        self.w.page_settings.port_combo.setEditText(port)
        return self.w.on_connect()

    def test_ohne_schluessel_meldung_mit_schluessel_verbunden(self):
        with mock.patch.object(self.qt.QMessageBox, "critical") as box:
            self.assertFalse(self.verbinden("SIM:basis:wlan"))
        box.assert_called_once()
        self.assertEqual(box.call_args[0][1], "Anmeldung an der Basis")
        self.assertIn("USB", box.call_args[0][2])
        self.assertIsNone(self.w.link)
        self.assertTrue(self.verbinden("SIM:basis"))              # USB: holt den Schluessel selbst
        self.assertEqual(KeyStore().get(sim.SIM_AUTH.uid), sim.SIM_AUTH.key.hex())
        self.w.on_connect()
        self.assertTrue(self.verbinden("SIM:basis:wlan"))
        self.assertEqual(self.w.link.auth_state, "ok")

    def test_falscher_schluessel(self):
        KeyStore().put(sim.SIM_AUTH.uid, "11" * 32)
        with mock.patch.object(self.qt.QMessageBox, "critical") as box:
            self.assertFalse(self.verbinden("SIM:basis:wlan"))
        self.assertEqual(box.call_args[0][2], auth.TEXT_FALSCH)

    def test_schluessel_nicht_speicherbar(self):
        with mock.patch.object(KeyStore, "put", side_effect=OSError(28, "Platte voll")):
            self.assertTrue(self.verbinden("SIM:basis"))
        self.assertIn("nicht gespeichert", self.w.statusBar().currentMessage())

    def abriss(self):
        self.w.link.ser.dead = True                               # ESP trennt nach Neustart der Basis
        self.w._auto_connect()
        self.assertIsNone(self.w.link)
        self.assertEqual(self.w._tcp_again, "SIM:basis:wlan")

    def test_neu_verbinden_nach_abriss(self):
        self.verbinden("SIM:basis")
        self.w.on_connect()
        self.assertTrue(self.verbinden("SIM:basis:wlan"))
        alt = self.w.link
        self.abriss()
        self.w._auto_connect()
        self.assertIsNot(self.w.link, alt)
        self.assertEqual(self.w.link.auth_state, "ok")
        self.assertTrue(self.w.link.ser.authed)
        self.assertEqual(self.w._tcp_again, "")

    def test_basis_startet_noch_dann_neuer_versuch(self):
        self.verbinden("SIM:basis")
        self.w.on_connect()
        self.verbinden("SIM:basis:wlan")
        self.abriss()
        for fehler in (OSError("nicht erreichbar"), AuthNoReply(auth.TEXT_KEINE_ANTWORT)):
            with mock.patch.object(self.w, "_open_link", side_effect=fehler):
                self.w._auto_connect()
            self.assertIsNone(self.w.link)
            self.assertEqual(self.w._tcp_again, "SIM:basis:wlan")
        self.w._auto_connect()
        self.assertEqual(self.w.link.auth_state, "ok")

    def test_abgelehnte_anmeldung_wird_nicht_wiederholt(self):
        self.verbinden("SIM:basis")
        self.w.on_connect()
        self.verbinden("SIM:basis:wlan")
        self.abriss()
        sim.SIM_AUTH.neu()                                        # Schluessel passt nicht mehr
        with mock.patch.object(self.qt.QMessageBox, "critical") as box:
            self.w._auto_connect()
            self.w._auto_connect()
            self.w._auto_connect()
        box.assert_called_once()
        self.assertEqual(sim.SIM_AUTH.fehler, 1)
        self.assertEqual(self.w._tcp_again, "")

    def test_trennen_von_hand_beendet_das_neu_verbinden(self):
        self.verbinden("SIM:basis")
        self.w.on_connect()
        self.verbinden("SIM:basis:wlan")
        self.abriss()
        self.w._tcp_again = "SIM:basis:wlan"
        self.assertTrue(self.verbinden("SIM"))                    # Anwender verbindet etwas anderes
        self.assertEqual(self.w._tcp_again, "")

    def test_passwort_vorschlag_und_offenes_wlan(self):
        self.verbinden("SIM:basis")
        hw = self.w.page_hw
        hw._check_link()
        hw.net_mode.setCurrentIndex(hw.net_mode.findData("STA"))
        hw.net_pw.clear()
        hw.net_mode.setCurrentIndex(hw.net_mode.findData("AP"))
        self.assertEqual(len(hw.net_pw.text()), 16)               # vorgeschlagen
        self.assertEqual(hw.net_pw.echoMode(), self.qt.QLineEdit.Normal)
        hw.net_pw.clear()
        self.assertFalse(hw.net_apply())
        self.assertIn("Offenes WLAN", hw.net_msg.text())
        hw.b_net_pw.click()
        self.assertTrue(hw.net_apply())


if __name__ == "__main__":
    unittest.main()
