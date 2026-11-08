"""Zange per USB: Update-Datei (.pystfw, Ziel zange-c3) ueber #UPD gegen die Fake-Zange, Download, Oberflaeche.
Nur Testschluessel (fw2_testdatei), keine echte Firmware, kein echter Port."""
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
from pyst.sim import ZangeUpdSimDevice  # noqa: E402
import fw2_testdatei as fw2  # noqa: E402


def zange(version="0.4.0", rollback=1, key_id=0, **faults):
    return ZangeUpdSimDevice(fw2.public_key(), fw2.ZANGE_AES, version, rollback, key_id, **faults)


def bekannt():
    return mock.patch.dict(fw_keys.KNOWN_KEYS, {fw2.TESTSCHLUESSEL_ID: fw2.public_key().hex()})


class Datei(unittest.TestCase):
    """bootproto.UpdateFile kennt die ESP-Ziele: formal, Ziel, Laengen; Inhalt prueft erst das Geraet."""

    def test_gute_datei(self):
        u = bp.UpdateFile.parse(fw2.esp_update("0.4.1", rollback=3), bp.TARGET_ZANGE)
        self.assertEqual((u.target, u.board, u.version, u.rollback, u.key_id), ("zange-c3", "rev31", "0.4.1", 3, 0))
        self.assertEqual(u.image_len, 8192)
        b = bp.UpdateFile.parse(fw2.esp_update(target=bp.TARGET_BASIS_ESP), bp.TARGET_BASIS_ESP)
        self.assertEqual(b.target, "basis-esp32c3")

    def test_falsches_ziel(self):
        with self.assertRaisesRegex(ValueError, r"für zange-c3 rev31 \(Zange\), nicht für basis-g473"):
            bp.UpdateFile.parse(fw2.esp_update())                         # Vorgabe: Basis
        with self.assertRaisesRegex(ValueError, r"\(Basis\), nicht für zange-c3"):
            bp.UpdateFile.parse(fw2.format2(), bp.TARGET_ZANGE)
        with self.assertRaisesRegex(ValueError, "Basis-ESP"):
            bp.UpdateFile.parse(fw2.esp_update(target=bp.TARGET_BASIS_ESP), bp.TARGET_ZANGE)
        with self.assertRaisesRegex(ValueError, "rohes ESP-Abbild"):
            bp.UpdateFile.parse(fw2.esp_image(), bp.TARGET_ZANGE)
        with self.assertRaisesRegex(ValueError, "keine Update-Datei.*Zange"):
            bp.UpdateFile.parse(b"irgendwas" * 40, bp.TARGET_ZANGE)

    def test_laengen_und_format(self):
        def setze(ofs, wert):
            return lambda h: h.__setitem__(slice(ofs, ofs + 4), wert.to_bytes(4, "little"))
        for name, patch in (("load_addr", setze(56, 0x08008000)), ("nicht %16", setze(60, 8190))):
            with self.subTest(name), self.assertRaisesRegex(ValueError, "Längen"):
                bp.UpdateFile.parse(fw2.esp_update(patch=patch), bp.TARGET_ZANGE)
        with self.assertRaisesRegex(ValueError, "Längen"):
            bp.UpdateFile.parse(fw2.esp_update(size=0x140010), bp.TARGET_ZANGE)    # groesser als ota_0
        f1 = bytearray(fw2.format1())
        f1[16:32] = b"zange-c3".ljust(16, b"\0")
        with self.assertRaisesRegex(ValueError, "nur signierte"):
            bp.UpdateFile.parse(fw2.fix_crc(f1[:256]) + bytes(f1[256:]), bp.TARGET_ZANGE)

    def test_basis_unveraendert(self):
        self.assertEqual(bp.UpdateFile.parse(fw2.format2("0.4.0")).target, "basis-g473")

    def test_laden_und_befehl(self):
        with tempfile.TemporaryDirectory() as d, bekannt():
            path = os.path.join(d, "pyst-zange-0.4.1.pystfw")
            with open(path, "wb") as fh:
                fh.write(fw2.esp_update("0.4.1"))
            img = flash.load_image(flash.ZANGE, d)
            self.assertEqual((img.target, img.update.target, img.warning), (flash.ZANGE, "zange-c3", ""))
            self.assertEqual(flash.image_version(img), "0.4.1")
            self.assertIn("PyST-Zange 0.4.1", img.summary())
            self.assertEqual(flash.build_command(flash.ZANGE, path, None)[1], [])          # kein externes Werkzeug
            with open(path, "wb") as fh:
                fh.write(fw2.format2())
            with self.assertRaisesRegex(flash.FlashError, "Falsche Firmware"):
                flash.load_image(flash.ZANGE, path)
            with self.assertRaisesRegex(flash.FlashError, "Falsche Firmware"):
                flash.load_image(flash.BASIS_STM, self._esp(d))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(flash.FlashError, "Keine Update-Datei"):
                flash.load_image(flash.ZANGE, d)

    def _esp(self, d):
        path = os.path.join(d, "z.pystfw")
        with open(path, "wb") as fh:
            fh.write(fw2.esp_update())
        return path

    def test_fremde_signatur_von_hand(self):
        with tempfile.TemporaryDirectory() as d, bekannt():
            path = os.path.join(d, "z.pystfw")
            with open(path, "wb") as fh:
                fh.write(fw2.esp_update(seed=fw2.FREMDER_TESTSCHLUESSEL_ED25519))
            with self.assertRaisesRegex(flash.FlashError, "Signatur ungültig"):
                flash.load_image(flash.ZANGE, path)


class Download(unittest.TestCase):
    """update.check_file: Eintrag „zange“ braucht eine signierte Datei fuer zange-c3, „basis_esp“ eine fuer basis-esp32c3."""

    def entry(self, d, data, target="zange", version="0.4.1"):
        path = os.path.join(d, "f.pystfw")
        with open(path, "wb") as fh:
            fh.write(data)
        import hashlib
        u = bp.UpdateFile.parse(data, update.FILE_TARGETS.get(target, bp.TARGET))
        return path, update.Entry(target, version, "f.pystfw", len(data), hashlib.sha256(data).hexdigest(),
                                  u.signature.hex())

    def test_ziele(self):
        self.assertEqual(update.SIGNED_TARGETS, ("basis_stm", "zange", "basis_esp"))
        self.assertEqual(update.FILE_TARGETS["zange"], bp.TARGET_ZANGE)

    def test_gut_und_abgelehnt(self):
        with tempfile.TemporaryDirectory() as d:
            path, e = self.entry(d, fw2.esp_update("0.4.1"))
            with self.assertRaisesRegex(update.UpdateError, "Unbekannter Schlüssel"):
                update.check_file(path, e)
            with bekannt():
                update.check_file(path, e)
                path, e = self.entry(d, fw2.esp_update("0.4.1", target=bp.TARGET_BASIS_ESP), "basis_esp")
                update.check_file(path, e)
                path, e = self.entry(d, fw2.esp_update("0.4.2"))
                with self.assertRaisesRegex(update.UpdateError, "Version im Manifest"):
                    update.check_file(path, e)
                path, e = self.entry(d, fw2.format2("0.4.1"), "basis_stm")
                e.target = "zange"                                         # Datei der Basis unter „zange“
                with self.assertRaisesRegex(update.UpdateError, "Falsche Firmware"):
                    update.check_file(path, e)
                path, e = self.entry(d, fw2.esp_update("0.4.1", seed=fw2.FREMDER_TESTSCHLUESSEL_ED25519))
                with self.assertRaisesRegex(update.UpdateError, "Signatur ungültig"):
                    update.check_file(path, e)


class Ablauf(unittest.TestCase):
    """ZangeFlasher gegen die Fake-Zange: INFO, BEGIN, DATA 256 Byte mit Wiederholung, END, BOOT, Version und Freigabe."""

    def flasher(self, dev, data=None, **kw):
        self.logs = []
        self.seen = []
        data = data or fw2.esp_update("0.4.1", size=8192 + 96)
        kw.setdefault("ports", dev.ports)
        f = flash.ZangeFlasher(bp.UpdateFile.parse(data, bp.TARGET_ZANGE), opener=dev.open, log=self.logs.append,
                               progress=lambda d, n: self.seen.append((d, n)), **kw)
        f.INFO_S, f.REPLY_S, f.BEGIN_S, f.END_S, f.POLL_S = 0.3, 0.15, 1.0, 1.0, 0.05
        f.WAIT_APP_S, f.STABLE_S, f.BUSY_S = 3.0, 3.0, 3.0
        return f

    def upd(self, dev, sub):
        return [x for x in dev.lines if x.upper().startswith("#UPD " + sub)]

    def test_gutes_update(self):
        dev = zange("0.4.0")
        self.assertEqual(self.flasher(dev).run(), "0.4.1")
        self.assertEqual((dev.version, dev.running, dev._ota_state()), ("0.4.1", "ota_1", "gueltig"))
        self.assertEqual(self.seen[-1], (8288, 8288))
        self.assertEqual(len(self.upd(dev, "DATA")), 33)                  # 32 volle Bloecke + 96 Byte
        self.assertTrue(all(len(base64.b64decode(x.split()[3])) <= 256 for x in self.upd(dev, "DATA")))
        self.assertEqual([x.split()[1] for x in dev.lines if x.startswith("#UPD")][:2], ["INFO", "BEGIN"])
        self.assertTrue(any("als gültig markiert" in x for x in self.logs))
        # nochmals mit derselben Version: geht, die Zange startet dann aus ota_0
        self.assertEqual(self.flasher(dev).run(), "0.4.1")
        self.assertEqual(dev.running, "ota_0")

    def test_verlorene_antworten_und_esp_log(self):
        dev = zange(drop={2, 5, 6, 20}, noise=True)
        self.assertEqual(self.flasher(dev).run(), "0.4.1")
        self.assertEqual(dev.version, "0.4.1")

    def test_sperre_messung_und_akku(self):
        for code, text in ((20, "misst gerade"), (21, "Akku zu schwach")):
            with self.subTest(code):
                dev = zange(gate=code)
                with self.assertRaisesRegex(flash.FlashError, text):
                    self.flasher(dev).run()
                self.assertEqual((dev.version, dev.state), ("0.4.0", "frei"))
        dev = zange(gate=20, gate_at=2048)                                # Aufnahme beginnt mitten im Update
        with self.assertRaisesRegex(flash.FlashError, "Basis ausschalten"):
            self.flasher(dev).run()
        self.assertEqual((dev.version, dev.state, dev.boot_part), ("0.4.0", "frei", "ota_0"))

    def test_rollback(self):
        dev = zange(rollback=3)
        with self.assertRaisesRegex(flash.FlashError, "Rückfall gesperrt.*Datei 2, Zange 3"):
            self.flasher(dev, fw2.esp_update("0.4.1", rollback=2)).run()
        self.assertEqual(self.upd(dev, "BEGIN"), [])                     # am PC erkannt, nichts gesendet
        dev = zange(rollback=3, no_rollback=True)                         # sonst lehnt die Zange selbst ab
        with self.assertRaisesRegex(flash.FlashError, "BEGIN: Rückfall gesperrt"):
            self.flasher(dev, fw2.esp_update("0.4.1", rollback=2)).run()
        self.assertEqual(self.flasher(zange(rollback=2), fw2.esp_update("0.4.1", rollback=2)).run(), "0.4.1")

    def test_signatur_und_schluessel(self):
        dev = zange()
        with self.assertRaisesRegex(flash.FlashError, "BEGIN: Signatur ungültig"):
            self.flasher(dev, fw2.esp_update(seed=fw2.FREMDER_TESTSCHLUESSEL_ED25519)).run()
        dev = zange(key_id=1)
        with self.assertRaisesRegex(flash.FlashError, "Schlüssel 0 signiert, die Zange hat Schlüssel 1"):
            self.flasher(dev).run()
        self.assertEqual(dev.version, "0.4.0")

    def test_inhalt_prueft_die_zange(self):
        dev = zange()
        with self.assertRaisesRegex(flash.FlashError, "END: Prüfung in der Zange fehlgeschlagen"):
            self.flasher(dev, fw2.esp_update(aes=fw2.TESTSCHLUESSEL_AES)).run()    # Rohschluessel statt Zangen-Schluessel
        with self.assertRaisesRegex(flash.FlashError, "Prüfung"):
            self.flasher(dev, fw2.esp_update(patch=lambda h: h.__setitem__(slice(164, 196), bytes(32)))).run()
        with self.assertRaisesRegex(flash.FlashError, "passt nicht zu dieser Zange"):     # Basis-ESP-Abbild
            self.flasher(dev, fw2.esp_update(plain=fw2.esp_image("0.4.1") [:0x50] + b"basis-esp32c3".ljust(32, b"\0")
                                             + fw2.esp_image("0.4.1")[0x70:])).run()
        self.assertEqual((dev.version, dev.boot_part), ("0.4.0", "ota_0"))

    def test_alte_zange_ohne_update(self):
        with self.assertRaisesRegex(flash.FlashError, "kein USB-Update.*Erstflash"):
            self.flasher(zange("0.3.1", no_upd=True)).run()

    def test_probezeit_vorher_abwarten(self):
        dev = zange(pending_s=0.3)
        self.assertEqual(self.flasher(dev).run(), "0.4.1")
        self.assertTrue(any("prüft noch ihre eigene" in x for x in self.logs))

    def test_absturz_der_neuen_firmware(self):
        dev = zange(crash=True)
        with self.assertRaisesRegex(flash.FlashError, "zur alten Firmware zurückgekehrt"):
            self.flasher(dev).run()
        self.assertEqual((dev.version, dev.running), ("0.4.0", "ota_0"))

    def test_abbruch(self):
        dev = zange()
        f = self.flasher(dev, cancelled=lambda: len(self.seen) >= 5)
        with self.assertRaises(flash.FlashCancelled):
            f.run()
        self.assertEqual((dev.version, dev.state), ("0.4.0", "frei"))
        self.assertEqual(len(self.upd(dev, "ABORT")), 1)

    def test_kabel_weg(self):
        dev = zange(unplug_at=4096)
        with self.assertRaisesRegex(flash.FlashError, "Verbindung zur Zange verloren.*alte Firmware bleibt"):
            self.flasher(dev).run()

    def test_ports(self):
        with self.assertRaisesRegex(flash.FlashError, "nicht am USB"):
            self.flasher(zange(), ports=lambda: []).run()
        with self.assertRaisesRegex(flash.FlashError, "Mehrere Zangen"):
            self.flasher(zange(), ports=lambda: ["a", "b"]).run()

    def test_fehlertexte(self):
        self.assertIn("misst gerade", flash.upd_error("#ERR;UPD;20;messung")[1])
        self.assertIn("Akku", flash.upd_error("#ERR;UPD;21;akku")[1])
        self.assertIn("Rückfall gesperrt", flash.upd_error("#ERR;UPD;12;rollback")[1])
        self.assertIn("Signatur ungültig", flash.upd_error("#ERR;UPD;11;signatur")[1])
        self.assertEqual(flash.upd_error("#ERR;UPD;3;luecke;next=512")[0], 3)
        self.assertEqual(flash.upd_error("#ERR;UPD;nicht eingebaut")[0], -1)


class Oberflaeche(unittest.TestCase):
    """Reiter „Firmware“: Zeile Zange (Update-Datei, Herunterladen) und Zange Erstflash (Build-Ordner, esptool)."""

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

    def test_zeilen_und_hinweis(self):
        p = self.page()
        self.assertTrue(p.rows[flash.ZANGE].is_file)
        self.assertFalse(p.rows[flash.ZANGE_ESPTOOL].is_file)
        for key in (flash.ZANGE, flash.ZANGE_ESPTOOL):
            self.assertIn("Nie am laufenden Motor per USB", flash.TARGETS[key].hint)
        p.remote = {"zange": update.Entry("zange", "0.4.1", "pyst-zange-0.4.1.pystfw", 10, "0" * 64)}
        p._show_remote()
        self.assertFalse(p.rows[flash.ZANGE].b_load.isHidden())
        self.assertIn("Online: 0.4.1", p.rows[flash.ZANGE].remote.text())
        self.assertTrue(p.rows[flash.ZANGE_ESPTOOL].b_load.isHidden())

    def test_alter_build_ordner_wandert_zum_erstflash(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "flasher_args.json"), "w") as fh:
                json.dump({}, fh)
            s = {"flash_ordner": {"zange": d}}
            self.page(s)
            self.assertEqual(s["flash_ordner"], {"zange_esptool": d})
            s = {"flash_ordner": {"zange": os.path.join(d, "x.pystfw")}}
            self.page(s)
            self.assertEqual(s["flash_ordner"], {"zange": os.path.join(d, "x.pystfw")})

    def test_start_nimmt_den_usb_weg(self):
        p = self.page()
        calls = mock.Mock()
        img = mock.Mock(warning="", update=mock.Mock(image_len=10))
        img.summary.return_value = "PyST-Zange 0.4.1"
        with mock.patch.object(flash, "build_command", return_value=(img, [])), \
                mock.patch.object(p, "run_usb", calls.run_usb), mock.patch.object(p, "run", calls.run), \
                mock.patch.object(self.qt.QMessageBox, "question", calls.frage):
            calls.frage.return_value = self.qt.QMessageBox.Yes
            p.start(flash.ZANGE)
        self.assertEqual([c[0] for c in calls.mock_calls], ["frage", "run_usb"])
        self.assertIn("Nie am laufenden Motor", calls.frage.call_args[0][2])

    def test_ganzer_lauf_in_der_oberflaeche(self):
        p = self.page()
        dev = zange()
        real = flash.ZangeFlasher

        def make(upd, **kw):
            f = real(upd, ports=dev.ports, opener=dev.open, **kw)
            f.INFO_S, f.REPLY_S, f.POLL_S = 0.3, 0.15, 0.05
            return f
        with mock.patch.object(flash, "ZangeFlasher", make):
            data = fw2.esp_update("0.4.1")
            p.run_usb(flash.ZANGE, flash.Image(flash.ZANGE, "", update=bp.UpdateFile.parse(data, bp.TARGET_ZANGE)))
            self.assertIn("nicht ausschalten", p.status.text())
            end = time.time() + 20
            while p.busy and time.time() < end:
                self.app.processEvents()
                time.sleep(0.005)
        self.assertFalse(p.busy)
        self.assertIn("0.4.1 läuft und ist als gültig markiert", p.status.text())
        self.assertEqual(dev.version, "0.4.1")


if __name__ == "__main__":
    unittest.main()
