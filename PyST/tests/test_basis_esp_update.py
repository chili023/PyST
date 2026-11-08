"""Basis-ESP per USB: Update-Datei (.pystfw, Ziel basis-esp32c3) ueber #ESPUPD am Messport gegen die Fake-Basis
(sim.BasisEspUpdSimDevice: STM-Seite und ESP-Kern), Zeilen und Ablauf in der Oberflaeche. Nur Testschluessel
(fw2_testdatei), keine echte Firmware, kein echter Port."""
import base64
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from pyst import bootproto as bp, flash, fw_keys, update  # noqa: E402
from pyst.sim import BasisEspUpdSimDevice  # noqa: E402
import fw2_testdatei as fw2  # noqa: E402


def basis(version="0.4.0", rollback=1, key_id=0, **faults):
    return BasisEspUpdSimDevice(fw2.public_key(), fw2.BASIS_ESP_AES, version, rollback, key_id, **faults)


def datei(version="0.4.1", **kw):
    kw.setdefault("target", bp.TARGET_BASIS_ESP)
    return fw2.esp_update(version, **kw)


def bekannt():
    return mock.patch.dict(fw_keys.KNOWN_KEYS, {fw2.TESTSCHLUESSEL_ID: fw2.public_key().hex()})


def schnell(f):
    f.INFO_S, f.REPLY_S, f.BEGIN_S, f.END_S, f.BOOT_S, f.NET_S, f.POLL_S = 0.3, 0.15, 1.0, 1.0, 0.3, 0.3, 0.05
    f.WAIT_APP_S, f.STABLE_S, f.BUSY_S = 3.0, 3.0, 3.0
    return f


class Ziele(unittest.TestCase):
    """Zwei Zeilen: Update-Datei ueber den Messport und Erstflash per Bruecke (esptool, Build-Ordner)."""

    def test_zeilen_und_ports(self):
        self.assertEqual(flash.FILE_TARGET[flash.BASIS_ESP], "basis-esp32c3")
        self.assertEqual(update.FILE_TARGETS["basis_esp"], bp.TARGET_BASIS_ESP)        # Manifest-Schluessel = Zeile
        self.assertEqual((flash.TARGETS[flash.BASIS_ESP].baud, flash.TARGETS[flash.BASIS_ESP_BRIDGE].baud), (0, 460800))
        self.assertIn("Update-Datei", flash.TARGETS[flash.BASIS_ESP].name)
        self.assertIn("Erstflash per Brücke", flash.TARGETS[flash.BASIS_ESP_BRIDGE].name)
        with mock.patch.object(flash, "basis_port", return_value="MESS"), \
                mock.patch.object(flash, "bridge_port", return_value="BRUECKE"):
            self.assertEqual(flash.target_port(flash.BASIS_ESP), "MESS")               # nie der Brueckenport
            self.assertEqual(flash.target_port(flash.BASIS_ESP_BRIDGE), "BRUECKE")
        upd = bp.UpdateFile.parse(datei(), bp.TARGET_BASIS_ESP)
        with mock.patch.object(flash, "basis_ports", return_value=["MESS"]), \
                mock.patch.object(flash, "zange_ports", return_value=["ZANGE"]):
            self.assertEqual(flash.BasisEspFlasher(upd).ports(), ["MESS"])                # Messport, wie link.py
            self.assertEqual(flash.ZangeFlasher(upd).ports(), ["ZANGE"])

    def test_laden_und_befehl(self):
        with tempfile.TemporaryDirectory() as d, bekannt():
            path = os.path.join(d, "basis-esp-0.4.1.pystfw")
            with open(path, "wb") as fh:
                fh.write(datei("0.4.1"))
            img = flash.load_image(flash.BASIS_ESP, d)
            self.assertEqual((img.target, img.update.target, img.warning), (flash.BASIS_ESP, "basis-esp32c3", ""))
            self.assertEqual(flash.image_version(img), "0.4.1")
            self.assertIn("PyST-Basis-ESP 0.4.1", img.summary())
            self.assertEqual(flash.build_command(flash.BASIS_ESP, path, None)[1], [])      # kein externes Werkzeug
            with open(path, "wb") as fh:
                fh.write(fw2.esp_update("0.4.1"))                                         # Datei der Zange
            with self.assertRaisesRegex(flash.FlashError, r"Falsche Firmware.*\(Zange\), nicht für basis-esp32c3"):
                flash.load_image(flash.BASIS_ESP, path)
            with open(path, "wb") as fh:
                fh.write(fw2.format2())                                                   # Datei des STM
            with self.assertRaisesRegex(flash.FlashError, "Falsche Firmware"):
                flash.load_image(flash.BASIS_ESP, path)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(flash.FlashError, "Keine Update-Datei"):
                flash.load_image(flash.BASIS_ESP, d)

    def test_fehlertexte(self):
        t = flash.BASIS_ESP_UPD_TEXT
        self.assertIn("misst gerade", flash.upd_error("#ERR;ESPUPD;20;messung", t)[1])
        self.assertIn("antwortet dem STM nicht", flash.upd_error("#ERR;ESPUPD;30;kein_esp", t)[1])
        self.assertEqual(flash.upd_error("#ERR;ESPUPD;31;zeit", t)[0], 31)
        self.assertIn("neu gestartet", flash.upd_error("#ERR;ESPUPD;32;neustart", t)[1])
        self.assertIn("Erstflash per Brücke", flash.upd_error("#ERR;ESPUPD;33;esp_alt", t)[1])
        self.assertEqual(flash.upd_error("#ERR;ESPUPD;nur_usb", t), (-1, t["nur_usb"]))
        self.assertIn("Prüfung im Basis-ESP", flash.upd_error("#ERR;ESPUPD;9;pruefung", t)[1])
        self.assertIn("Rückfall gesperrt", flash.upd_error("#ERR;ESPUPD;12;rollback", t)[1])
        self.assertIn("Akku", flash.upd_error("#ERR;UPD;21;akku")[1])                   # Vorgabe: Zange


class Ablauf(unittest.TestCase):
    """BasisEspFlasher gegen die Fake-Basis: #RUN 0, #CAP espupd=1, #NET, INFO, BEGIN, DATA, END, BOOT, #NET bis zum
    Neustart mit der neuen Version, INFO bis ota=gueltig. Der Port bleibt die ganze Zeit offen."""

    def flasher(self, dev, data=None, **kw):
        self.logs = []
        self.seen = []
        self.opened = []

        def opener(port):
            self.opened.append(port)
            return dev.open(port)
        kw.setdefault("ports", dev.ports)
        f = flash.BasisEspFlasher(bp.UpdateFile.parse(data or datei("0.4.1", size=8192 + 96), bp.TARGET_BASIS_ESP),
                                  opener=opener, log=self.logs.append, progress=lambda d, n: self.seen.append((d, n)),
                                  **kw)
        return schnell(f)

    def cmds(self, dev, sub=""):
        return [x for x in dev.lines if x.upper().startswith("#ESPUPD " + sub)]

    def test_gutes_update(self):
        dev = basis("0.4.0")
        self.assertEqual(self.flasher(dev).run(), "0.4.1")
        self.assertEqual((dev.version, dev.running, dev._ota_state(), dev.neustarts), ("0.4.1", "ota_1", "gueltig", 4))
        self.assertEqual(self.seen[-1], (8288, 8288))
        self.assertEqual(len(self.cmds(dev, "DATA")), 33)                 # 32 volle Bloecke + 96 Byte
        self.assertTrue(all(len(base64.b64decode(x.split()[3])) <= 256 for x in self.cmds(dev, "DATA")))
        self.assertTrue(all(len(x) + 2 <= 400 for x in self.cmds(dev)))   # Zeilengrenze des STM fuer #ESPUPD
        self.assertEqual(dev.lines[:2], ["#RUN 0", "#?"])
        self.assertEqual([x.split()[1] for x in self.cmds(dev)][:2], ["INFO", "BEGIN"])
        self.assertIn("#NET", dev.lines)
        self.assertEqual(len(self.opened), 1)                            # nur der ESP startet neu
        self.assertTrue(any("als gültig markiert" in x for x in self.logs))
        self.assertTrue(any("basis-esp 0.4.1" in x for x in self.logs))

    def test_ausgabe_wird_beendet_antworten_fehlen(self):
        dev = basis(run=True, drop={2, 6, 7, 30}, noise=True)            # #RUN 0 vorweg, STM-Antworten gehen verloren
        self.assertEqual(self.flasher(dev).run(), "0.4.1")
        self.assertEqual(dev.version, "0.4.1")

    def test_zeit_31_wird_wiederholt(self):
        dev = basis(late={2, 4, 9, 39})                                   # BEGIN, DATA 0, DATA 1024, END
        self.assertEqual(self.flasher(dev).run(), "0.4.1")
        self.assertEqual(len(self.cmds(dev, "BEGIN")), 2)
        self.assertEqual(len(self.cmds(dev, "END")), 2)                   # zweites END: OK wie beim ersten
        self.assertEqual(dev.version, "0.4.1")

    def test_messung_sperrt(self):
        dev = basis(messung=True)                                         # Burst oder Zange-REC: #RUN 0 hilft nicht
        with self.assertRaisesRegex(flash.FlashError, "Die Basis misst gerade"):
            self.flasher(dev).run()
        self.assertEqual((dev.version, dev.state), ("0.4.0", "frei"))
        dev = basis(messung_at=2048)                                      # Aufnahme beginnt mitten im Update
        with self.assertRaisesRegex(flash.FlashError, "^Die Basis misst gerade"):
            self.flasher(dev).run()
        self.assertEqual((dev.version, dev.state, dev.boot_part), ("0.4.0", "frei", "ota_0"))

    def test_stm_lehnt_ab(self):
        for faults, text in (({"no_esp": True}, "antwortet dem STM nicht"),
                             ({"esp_alt": True}, "älter als 0.4.0.*Erstflash per Brücke"),
                             ({"tcp": True}, "nur per USB"),
                             ({"no_espupd": True}, "Basis STM32“ auf 0.4.0")):
            with self.subTest(faults):
                dev = basis(**faults)
                with self.assertRaisesRegex(flash.FlashError, text):
                    self.flasher(dev).run()
                self.assertEqual(self.cmds(dev, "BEGIN"), [])
                self.assertEqual(dev.version, "0.4.0")

    def test_boot_32_ist_normal(self):
        dev = basis(boot_32=True)
        self.assertEqual(self.flasher(dev).run(), "0.4.1")
        self.assertEqual((dev.version, dev._ota_state()), ("0.4.1", "gueltig"))

    def test_neustart_mitten_im_update(self):
        dev = basis(restart_at=4096)
        with self.assertRaisesRegex(flash.FlashError, "neu gestartet, die alte Firmware bleibt"):
            self.flasher(dev).run()
        time.sleep(0.4)
        self.assertTrue(dev.esp_alive())
        self.assertEqual((dev.version, dev.neustarts, dev.boot_part), ("0.4.0", 4, "ota_0"))

    def test_absturz_der_neuen_firmware(self):
        dev = basis(crash=True)
        t0 = time.time()
        with self.assertRaisesRegex(flash.FlashError, "Er ist zur alten Firmware zurückgekehrt"):
            self.flasher(dev).run()
        self.assertLess(time.time() - t0, 2.5)                           # nicht erst nach WAIT_APP_S
        self.assertEqual((dev.version, dev.running), ("0.4.0", "ota_0"))

    def test_probezeit_vorher_und_nachher(self):
        dev = basis(pending_s=0.3)
        self.assertEqual(self.flasher(dev).run(), "0.4.1")
        self.assertTrue(any("prüft noch seine eigene" in x for x in self.logs))
        dev = basis(stable_s=30)                                          # Link steht nicht: bleibt pruefen
        f = self.flasher(dev)
        f.STABLE_S = 0.8
        with self.assertRaisesRegex(flash.FlashError, "nicht als gültig markiert.*30 s mit stehendem Link"):
            f.run()
        self.assertEqual((dev.version, dev._ota_state()), ("0.4.1", "pruefen"))

    def test_signatur_schluessel_rollback(self):
        dev = basis()
        with self.assertRaisesRegex(flash.FlashError, "BEGIN: Signatur ungültig"):
            self.flasher(dev, datei(seed=fw2.FREMDER_TESTSCHLUESSEL_ED25519)).run()
        with self.assertRaisesRegex(flash.FlashError, "Schlüssel 0 signiert, der Basis-ESP hat Schlüssel 1"):
            self.flasher(basis(key_id=1)).run()
        dev = basis(rollback=3)
        with self.assertRaisesRegex(flash.FlashError, "Rückfall gesperrt.*Datei 2, Basis-ESP 3"):
            self.flasher(dev, datei(rollback=2)).run()
        self.assertEqual(self.cmds(dev, "BEGIN"), [])                    # am PC erkannt, nichts gesendet
        dev = basis(rollback=3, no_rollback=True)
        with self.assertRaisesRegex(flash.FlashError, "BEGIN: Rückfall gesperrt"):
            self.flasher(dev, datei(rollback=2)).run()

    def test_inhalt_prueft_der_esp(self):
        dev = basis()
        with self.assertRaisesRegex(flash.FlashError, "END: Prüfung im Basis-ESP fehlgeschlagen"):
            self.flasher(dev, datei(aes=fw2.ZANGE_AES)).run()           # Schluessel der Zange statt des Basis-ESP
        with self.assertRaisesRegex(flash.FlashError, "END: Datei passt nicht zum Basis-ESP"):
            self.flasher(dev, datei(plain=fw2.esp_image("0.4.1"))).run()  # Abbild der Zange im Kopf des Basis-ESP
        self.assertEqual((dev.version, dev.boot_part), ("0.4.0", "ota_0"))

    def test_abbruch(self):
        dev = basis()
        f = self.flasher(dev, cancelled=lambda: len(self.seen) >= 5)
        with self.assertRaises(flash.FlashCancelled):
            f.run()
        self.assertEqual((dev.version, dev.state), ("0.4.0", "frei"))
        self.assertEqual(len(self.cmds(dev, "ABORT")), 1)

    def test_kabel_weg(self):
        with self.assertRaisesRegex(flash.FlashError, "Verbindung zur Basis verloren.*alte Firmware bleibt"):
            self.flasher(basis(unplug_at=4096)).run()

    def test_ports(self):
        with self.assertRaisesRegex(flash.FlashError, "Basis nicht am USB.*nur per USB"):
            self.flasher(basis(), ports=lambda: []).run()
        with self.assertRaisesRegex(flash.FlashError, "Mehrere Basen"):
            self.flasher(basis(), ports=lambda: ["a", "b"]).run()


class Oberflaeche(unittest.TestCase):
    """Reiter „Firmware“: Basis-ESP per Update-Datei (Herunterladen, USB-Ablauf) und Erstflash per Bruecke (esptool)."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        cls.qt = QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def page(self, settings=None):
        from pyst import flash_page
        p = flash_page.FlashPage(settings if settings is not None else {}, lambda: None, lambda: None, lambda: None)
        p.timer.stop()
        return p

    def test_zeilen_und_download(self):
        p = self.page()
        self.assertTrue(p.rows[flash.BASIS_ESP].is_file)
        self.assertFalse(p.rows[flash.BASIS_ESP_BRIDGE].is_file)
        p.remote = {"basis_esp": update.Entry("basis_esp", "0.4.1", "basis-esp-0.4.1.pystfw", 10, "0" * 64)}
        p._show_remote()
        self.assertFalse(p.rows[flash.BASIS_ESP].b_load.isHidden())
        self.assertIn("Online: 0.4.1", p.rows[flash.BASIS_ESP].remote.text())
        self.assertTrue(p.rows[flash.BASIS_ESP_BRIDGE].b_load.isHidden())

    def test_alter_build_ordner_wandert_zur_bruecke(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "flasher_args.json"), "w") as fh:
                json.dump({}, fh)
            s = {"flash_ordner": {"basis_esp": d}}
            self.page(s)
            self.assertEqual(s["flash_ordner"], {"basis_esp_bruecke": d})
            s = {"flash_ordner": {"basis_esp": os.path.join(d, "x.pystfw")}}
            self.page(s)
            self.assertEqual(s["flash_ordner"], {"basis_esp": os.path.join(d, "x.pystfw")})

    def test_start_ohne_bruecke(self):
        p = self.page()
        calls = mock.Mock()
        img = mock.Mock(warning="", update=mock.Mock(image_len=10))
        img.summary.return_value = "PyST-Basis-ESP 0.4.1"
        with mock.patch.object(flash, "build_command", return_value=(img, [])), \
                mock.patch.object(flash, "esp_bridge_on", calls.bruecke), \
                mock.patch.object(p, "run_usb", calls.run_usb), mock.patch.object(p, "run", calls.run), \
                mock.patch.object(self.qt.QMessageBox, "question", calls.frage):
            calls.frage.return_value = self.qt.QMessageBox.Yes
            p.start(flash.BASIS_ESP)
        self.assertEqual([c[0] for c in calls.mock_calls], ["frage", "run_usb"])

    def test_ganzer_lauf_in_der_oberflaeche(self):
        p = self.page()
        dev = basis()
        real = flash.BasisEspFlasher

        def make(upd, **kw):
            return schnell(real(upd, ports=dev.ports, opener=dev.open, **kw))
        with mock.patch.object(flash, "BasisEspFlasher", make):
            data = datei("0.4.1")
            p.run_usb(flash.BASIS_ESP, flash.Image(flash.BASIS_ESP, "", update=bp.UpdateFile.parse(
                data, bp.TARGET_BASIS_ESP)))
            self.assertIn("Basis nicht vom Strom trennen", p.status.text())
            end = time.time() + 20
            while p.busy and time.time() < end:
                self.app.processEvents()
                time.sleep(0.005)
        self.assertFalse(p.busy)
        self.assertIn("0.4.1 läuft und ist als gültig markiert", p.status.text())
        self.assertEqual(dev.version, "0.4.1")


if __name__ == "__main__":
    unittest.main()
