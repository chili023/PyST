"""Tests Bootloader Stufe 2: Update-Dateien Format 2, Signatur am PC, Gegenstelle mit Testschluessel."""
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from pyst import bootproto as bp, flash, fw_keys  # noqa: E402
from pyst.sim import BootSim2Device, BootSimDevice, ctr_decrypt  # noqa: E402
import fw2_testdatei as fw2  # noqa: E402


def setzen(ofs, wert):
    def patch(h):
        h[ofs:ofs + len(wert)] = wert
    return patch


class Format2Lesen(unittest.TestCase):
    def test_gute_datei(self):
        u = bp.UpdateFile.parse(fw2.format2("0.4.0", rollback=7, key_id=3))
        self.assertEqual((u.format, u.key_id, u.rollback, u.version, u.image_len), (2, 3, 7, "0.4.0", 20480))
        self.assertEqual(u.format_text, "Format 2, Schlüssel 3, Rollback 7")
        self.assertEqual(len(u.signature), 64)
        self.assertEqual(len(u.signed_data), 10 + 100 + 88)

    def test_vektortabelle_wird_nicht_geprueft(self):
        """Verschluesselt sieht sie nach Zufall aus, und auch ein Klartext-Abbild mit falscher Vektortabelle faellt erst
        im Bootloader auf."""
        u = bp.UpdateFile.parse(fw2.format2(plain=fw2.image(reset=0x08000301)))
        self.assertEqual(u.format, 2)

    def test_format1_bleibt_wie_es_war(self):
        u = bp.UpdateFile.parse(fw2.format1())
        self.assertEqual((u.format, u.format_text), (1, "Format 1, unsigniert"))
        with self.assertRaisesRegex(ValueError, "Vektortabelle"):
            bp.UpdateFile.parse(fw2.format1(reset=0x08000301))

    def test_fehler_im_kopf(self):
        good = fw2.format2(size=4096)
        ohne_crc = {
            "Magie": (b"PYSTUPD2" + good[8:], "keine Update-Datei"),
            "Kopf-CRC": (good[:50] + b"X" + good[51:], "beschädigt"),
            "Signatur ohne CRC": (good[:120] + b"X" + good[121:], "beschädigt"),
            "payload_crc": (good[:1000] + bytes((good[1000] ^ 1,)) + good[1001:], "beschädigt"),
            "kurz": (good[:-8], "beschädigt"),
            "lang": (good + bytes(8), "beschädigt"),
            "nur Kopf-Anfang": (good[:100], "keine Update-Datei"),
        }
        for name, (data, text) in ohne_crc.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, text):
                bp.UpdateFile.parse(data)

    def test_fehler_in_den_feldern(self):
        faelle = {
            "Ziel": (setzen(16, b"zange-c3\0\0"), "Falsche Firmware"),
            "Platine": (setzen(32, b"rev30"), "Falsche Firmware"),
            "unsigniert": (setzen(12, bytes((2, 0, 0, 0))), "signiert und verschlüsselt"),
            "unverschluesselt": (setzen(12, bytes((1, 0, 0, 0))), "signiert und verschlüsselt"),
            "Flag zuviel": (setzen(12, bytes((7, 0, 0, 0))), "signiert und verschlüsselt"),
            "Format 3": (setzen(8, bytes((3, 0))), "zu neu"),
            "Signaturverfahren": (setzen(76, bytes((2,))), "zu neu"),
            "Verschluesselung": (setzen(77, bytes((2,))), "zu neu"),
            "Reserve": (setzen(240, bytes((1,))), "beschädigt"),
            "reserved0": (setzen(79, bytes((1,))), "beschädigt"),
            "Ladeadresse": (setzen(56, struct.pack("<I", bp.APP_BASE + 8)), "Längen"),
            "image_len": (setzen(60, struct.pack("<I", 4096)), "Längen"),
            "payload_len": (setzen(68, struct.pack("<I", 4096)), "Längen"),
        }
        for name, (patch, text) in faelle.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, text):
                bp.UpdateFile.parse(fw2.format2(patch=patch))

    def test_laengen(self):
        for name, size in (("nicht durch 8", 4100), ("zu gross", bp.APP_MAX + 8)):
            with self.subTest(name), self.assertRaisesRegex(ValueError, "Längen"):
                bp.UpdateFile.parse(fw2.format2(plain=bytes(size)))

    def test_anzeige(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "basis.pystfw")
            with open(path, "wb") as fh:
                fh.write(fw2.format2("0.4.0", rollback=2, key_id=1))
            img = flash.load_image(flash.BASIS_STM, path)
            self.assertEqual(flash.image_version(img), "0.4.0")
            self.assertIn("Format 2, Schlüssel 1, Rollback 2", img.summary())


def bekannt(seed=fw2.TESTSCHLUESSEL_ED25519, key_id=fw2.TESTSCHLUESSEL_ID):
    """Testschluessel nur fuer die Dauer eines Tests in die Tabelle."""
    return mock.patch.dict(fw_keys.KNOWN_KEYS, {key_id: fw2.public_key(seed).hex()})


def ohne_cryptography():
    return mock.patch.dict(sys.modules, {"cryptography.hazmat.primitives.asymmetric.ed25519": None})


FORMAT1 = bp.UpdateFile(b"", b"", "", "", "", 0, 0)


class Signatur(unittest.TestCase):
    def setUp(self):
        self.gut = bp.UpdateFile.parse(fw2.format2())
        self.fremd = bp.UpdateFile.parse(fw2.format2(seed=fw2.FREMDER_TESTSCHLUESSEL_ED25519))

    def test_tabelle_enthaelt_keine_testschluessel(self):
        self.assertNotIn(fw2.TESTSCHLUESSEL_ID, fw_keys.KNOWN_KEYS)
        for key_id, public in fw_keys.KNOWN_KEYS.items():
            self.assertTrue(1 <= key_id <= 255)
            self.assertEqual(len(bytes.fromhex(public)), 32)
            self.assertNotIn(bytes.fromhex(public), (fw2.public_key(), fw2.public_key(fw2.FREMDER_TESTSCHLUESSEL_ED25519)))

    def test_pruefung(self):
        self.assertEqual(fw_keys.verify(self.gut), fw_keys.UNKNOWN_KEY)
        with bekannt():
            self.assertEqual(fw_keys.verify(self.gut), fw_keys.GOOD)
            self.assertEqual(fw_keys.verify(self.fremd), fw_keys.BAD)

    def test_signatur_deckt_den_ganzen_kopf(self):
        """Jede Stelle ausser Signatur und hdr_crc: Die CRC laesst sich nachrechnen, die Signatur nicht."""
        data = fw2.format2(size=4096)
        with bekannt():
            for ofs in (0, 40, 72, 79, 80, 84, 99, 164, 195, 251):
                h = bytearray(data[:bp.UPD_LEN])
                h[ofs] ^= 1
                u = bp.UpdateFile(bytes(h), b"", "", "", "", 0, 0, 2)
                self.assertEqual(fw_keys.verify(u), fw_keys.BAD, ofs)

    def test_regel_netz(self):
        with self.assertRaisesRegex(fw_keys.SignatureError, "Unbekannter Schlüssel 0"):
            fw_keys.check_download(self.gut)
        with self.assertRaisesRegex(fw_keys.SignatureError, "Unsignierte"):
            fw_keys.check_download(FORMAT1)
        with bekannt():
            fw_keys.check_download(self.gut)
            with self.assertRaisesRegex(fw_keys.SignatureError, "Signatur ungültig"):
                fw_keys.check_download(self.fremd)
            with ohne_cryptography(), self.assertRaisesRegex(fw_keys.SignatureError, "cryptography"):
                fw_keys.check_download(self.gut)

    def test_regel_von_hand(self):
        self.assertIn("nicht bekannt", fw_keys.check_local(self.gut))
        self.assertEqual(fw_keys.check_local(FORMAT1), "")
        with bekannt():
            self.assertEqual(fw_keys.check_local(self.gut), "")
            with self.assertRaisesRegex(fw_keys.SignatureError, "Signatur ungültig"):
                fw_keys.check_local(self.fremd)
            with ohne_cryptography():
                self.assertIn("cryptography", fw_keys.check_local(self.gut))

    def _datei(self, d, data):
        path = os.path.join(d, "basis.pystfw")
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_laden(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._datei(d, fw2.format2())
            self.assertIn("nicht bekannt", flash.load_image(flash.BASIS_STM, path).warning)
            with bekannt():
                self.assertEqual(flash.load_image(flash.BASIS_STM, path).warning, "")
                self._datei(d, fw2.format2(seed=fw2.FREMDER_TESTSCHLUESSEL_ED25519))
                with self.assertRaisesRegex(flash.FlashError, "Signatur ungültig"):
                    flash.load_image(flash.BASIS_STM, path)

    def test_anzeige_in_der_oberflaeche(self):
        from PySide6 import QtWidgets
        from pyst import farben
        from pyst.flash_page import FlashRow
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as d:
            row = FlashRow(flash.TARGETS[flash.BASIS_STM], self._datei(d, fw2.format2()), lambda *_a: None)
            self.assertIn("nicht bekannt", row.info.text())
            self.assertIn(farben.WARNUNG, row.info.styleSheet())
        app.processEvents()


class Ablauf(unittest.TestCase):
    def flasher(self, dev, data, **kw):
        self.logs = []
        f = flash.UsbFlasher(bp.UpdateFile.parse(data), ports=dev.ports, opener=dev.open, log=self.logs.append, **kw)
        f.WAIT_BOOT_S, f.WAIT_APP_S, f.START_S, f.REPLY_S = 3.0, 3.0, 3.0, 0.1
        return f

    def wieder_in_der_anwendung(self, dev, version="0.1.0"):
        end = time.time() + 3
        while time.time() < end and not (dev.ports() and dev.mode == "app"):
            time.sleep(0.01)
        self.assertEqual((dev.mode, dev.version), ("app", version))


class Protokoll(Ablauf):
    """Bootloader 1 und 2 erkennen, nur die passende Datei uebertragen."""

    def test_wartezeit(self):
        self.assertEqual(flash.UsbFlasher.START_S, 30.0)

    def test_passend(self):
        dev = BootSimDevice("0.1.0", proto=2)
        self.assertEqual(self.flasher(dev, fw2.format2("0.4.0")).run(), "0.4.0")
        self.assertTrue(any("Bootloader 2.0.0, Schlüssel 0" in t for t in self.logs))
        dev = BootSimDevice("0.1.0")
        self.assertEqual(self.flasher(dev, fw2.format1("0.3.0")).run(), "0.3.0")

    def test_fehlkombination_vor_jeder_uebertragung(self):
        for proto, data, text in ((1, fw2.format2(), "nimmt nur Format 1"), (2, fw2.format1(), "nur signierte")):
            with self.subTest(proto=proto):
                dev = BootSimDevice("0.1.0", proto=proto)
                with self.assertRaisesRegex(flash.FlashError, text) as ctx:
                    self.flasher(dev, data).run()
                self.assertIn("Bootloader 2.0.0 kommt", str(ctx.exception))
                self.assertIn("ST-Link", str(ctx.exception))
                self.assertEqual(dev.starts, 0)
                self.wieder_in_der_anwendung(dev)

    def test_wer_schon_im_bootloader_war_bleibt_dort(self):
        dev = BootSimDevice("0.1.0", mode="boot")
        with self.assertRaisesRegex(flash.FlashError, "ST-Link"):
            self.flasher(dev, fw2.format2()).run()
        self.assertEqual((dev.mode, dev.starts), ("boot", 0))

    def test_unbekanntes_protokoll(self):
        dev = BootSimDevice("0.1.0", proto=3)
        with self.assertRaisesRegex(flash.FlashError, "Protokoll 3"):
            self.flasher(dev, fw2.format2()).run()
        self.assertEqual(dev.starts, 0)

    def test_anderer_schluessel_im_bootloader(self):
        dev = BootSimDevice("0.1.0", proto=2, key_id=1)
        with self.assertRaisesRegex(flash.FlashError, "Schlüssel 3 signiert.*Schlüssel 1"):
            self.flasher(dev, fw2.format2(key_id=3)).run()
        self.assertEqual(dev.starts, 0)
        self.wieder_in_der_anwendung(dev)

    def test_alte_zeile_ohne_key(self):
        self.assertEqual(flash.fit_problem(2, bp.UpdateFile.parse(fw2.format2(key_id=3)), {}), "")


class StartGeduldig(Ablauf):
    def test_langsame_pruefung(self):
        dev = BootSimDevice("0.1.0", proto=2, start_s=0.5)
        self.assertEqual(self.flasher(dev, fw2.format2("0.4.0")).run(), "0.4.0")
        self.assertTrue(any("Signatur im Gerät" in t for t in self.logs))

    def test_wiederholung_holt_verlorene_schlussantwort(self):
        dev = BootSimDevice("0.1.0", proto=2, start_s=0.1, drop={3})      # 1 PING, 2 BUSY, 3 OK
        f = self.flasher(dev, fw2.format2("0.4.0"))
        f.START_RESEND_S = 0.3
        self.assertEqual(f.run(), "0.4.0")
        self.assertEqual(dev.starts, 2)

    def test_wiederholung_waehrend_der_pruefung_antwortet_busy(self):
        dev = BootSimDevice("0.1.0", proto=2, start_s=0.6)
        f = self.flasher(dev, fw2.format2("0.4.0"))
        f.START_RESEND_S = 0.2
        self.assertEqual(f.run(), "0.4.0")
        self.assertGreater(dev.starts, 1)

    def test_wird_nicht_fertig(self):
        dev = BootSimDevice("0.1.0", proto=2, start_s=2.0)
        f = self.flasher(dev, fw2.format2())
        f.START_S = 0.3
        with self.assertRaisesRegex(flash.FlashError, "nicht fertig"):
            f.run()

    def test_abbruch_in_der_pruefung(self):
        dev = BootSimDevice("0.1.0", proto=2, start_s=0.5)
        stop = threading.Event()
        f = self.flasher(dev, fw2.format2(), cancelled=stop.is_set)
        f.log = lambda t: stop.set() if "Signatur" in t else None
        with self.assertRaises(flash.FlashCancelled):
            f.run()
        self.wieder_in_der_anwendung(dev)

    def test_ablehnung_laesst_die_firmware_stehen(self):
        for status, text in ((bp.BL_E_SIGNATURE, "Signatur ungültig"), (bp.BL_E_ROLLBACK, "Rückfall gesperrt")):
            with self.subTest(status):
                dev = BootSimDevice("0.1.0", proto=2, start_status=status)
                with self.assertRaisesRegex(flash.FlashError, f"Start abgelehnt: {text}"):
                    self.flasher(dev, fw2.format2()).run()
                self.wieder_in_der_anwendung(dev)

    def test_versionspruefung_bleibt(self):
        dev = BootSimDevice("0.1.0", proto=2, wrong_version="0.3.9")
        with self.assertRaisesRegex(flash.FlashError, "Falsche Version"):
            self.flasher(dev, fw2.format2("0.4.0")).run()


def basis2(version="0.1.0", **kw):
    return BootSim2Device(fw2.public_key(), fw2.TESTSCHLUESSEL_AES, version, **kw)


class Verschluesselung(unittest.TestCase):
    def test_schluesselstrom_ab_jedem_8_byte_offset(self):
        plain = fw2.image(size=4096)
        for nonce in (bytes(range(16)), b"\xff" * 16, b"\xff" * 15 + b"\xf0", bytes(8) + b"\xff" * 8):
            cipher = fw2.encrypt(plain, nonce)
            for off in range(0, 4096, 8 * 67):
                got = ctr_decrypt(fw2.TESTSCHLUESSEL_AES, nonce, off, cipher[off:off + 40])
                self.assertEqual(got, plain[off:off + 40], (nonce.hex(), off))


class Gegenstelle(Ablauf):
    """Ende zu Ende gegen den simulierten Bootloader 2.0.0 mit Testschluessel."""

    def test_gutes_update(self):
        dev = basis2()
        data = fw2.format2("0.4.0", rollback=2)
        self.assertEqual(self.flasher(dev, data).run(), "0.4.0")
        self.assertEqual((dev.mode, dev.version, dev.rollback, dev.commit), ("app", "0.4.0", 2, None))
        self.assertEqual(bytes(dev.stage), fw2.image("0.4.0", rollback=2))    # Klartext im Zwischenspeicher
        self.assertEqual(dev.settings, {"rate_hz": "60"})

    def test_mit_bekanntem_schluessel_von_der_platte(self):
        with tempfile.TemporaryDirectory() as d, bekannt():
            path = os.path.join(d, "basis.pystfw")
            with open(path, "wb") as fh:
                fh.write(fw2.format2("0.4.0"))
            img = flash.load_image(flash.BASIS_STM, path)
            self.assertEqual(img.warning, "")
            dev = basis2()
            f = flash.UsbFlasher(img.update, ports=dev.ports, opener=dev.open)
            self.assertEqual(f.run(), "0.4.0")

    def test_falsche_signatur(self):
        """Unbekannter Schluessel kommt am PC durch, der Bootloader lehnt ab und zerstoert nichts."""
        dev = basis2(start_s=0.2)
        dev.stage = bytearray(b"alt")
        data = fw2.format2("0.4.0", seed=fw2.FREMDER_TESTSCHLUESSEL_ED25519)
        with self.assertRaisesRegex(flash.FlashError, "Start abgelehnt: Signatur ungültig"):
            self.flasher(dev, data).run()
        self.assertEqual(bytes(dev.stage), b"alt")
        self.wieder_in_der_anwendung(dev)

    def test_gekippter_kopf_am_pc_vorbei(self):
        """Mit nachgerechneter CRC bleibt die Datei formal gut, der Bootloader sieht die Faelschung."""
        data = bytearray(fw2.format2("0.4.0"))
        data[84] ^= 1                                        # nonce
        h = fw2.fix_crc(data[:bp.UPD_LEN])
        dev = basis2()
        with self.assertRaisesRegex(flash.FlashError, "Signatur ungültig"):
            self.flasher(dev, h + bytes(data[bp.UPD_LEN:])).run()
        self.wieder_in_der_anwendung(dev)

    def test_rollback(self):
        dev = basis2(rollback=5)
        with self.assertRaisesRegex(flash.FlashError, "Start abgelehnt: Rückfall gesperrt"):
            self.flasher(dev, fw2.format2("0.4.0", rollback=4)).run()
        self.wieder_in_der_anwendung(dev)
        self.assertEqual(self.flasher(dev, fw2.format2("0.4.0", rollback=5)).run(), "0.4.0")

    def test_ohne_anwendung_gilt_jede_signierte_datei(self):
        dev = basis2(app=False, rollback=5)
        self.assertEqual(self.flasher(dev, fw2.format2("0.4.0", rollback=1)).run(), "0.4.0")
        self.assertEqual(dev.rollback, 1)

    def test_rollback_im_abbild_muss_passen(self):
        data = fw2.format2("0.4.0", rollback=3, plain=fw2.image("0.4.0", rollback=2))
        dev = basis2()
        with self.assertRaisesRegex(flash.FlashError, "Prüfung"):
            self.flasher(dev, data).run()
        self.assertEqual(dev.version, "0.1.0")

    def test_falscher_sha256_faellt_am_ende_auf(self):
        """Signatur gut, Inhalt passt nicht: Erst der Bootloader kann das sehen, die Anwendung bleibt."""
        dev = basis2()
        with self.assertRaisesRegex(flash.FlashError, "Prüfung"):
            self.flasher(dev, fw2.format2("0.4.0", patch=setzen(164, b"\xaa" * 32))).run()
        self.assertEqual((dev.version, dev.commit), ("0.1.0", None))

    def test_fehlkombination(self):
        dev = basis2()
        with self.assertRaisesRegex(flash.FlashError, "nur signierte"):
            self.flasher(dev, fw2.format1()).run()
        self.assertEqual(dev.starts, 0)
        self.wieder_in_der_anwendung(dev)

    def test_format1_kaeme_nicht_durch(self):
        """Auch ohne die Pruefung in PyST: Bootloader 2.0.0 nimmt nichts Unsigniertes."""
        dev = basis2(mode="boot")
        self.assertEqual(dev._head_status(fw2.format1()[:bp.UPD_LEN]), bp.BL_E_UNSUPPORTED)

    def test_langsame_pruefung_mit_busy(self):
        dev = basis2(start_s=0.5)
        f = self.flasher(dev, fw2.format2("0.4.0"))
        f.START_RESEND_S = 0.2
        self.assertEqual(f.run(), "0.4.0")
        self.assertGreater(dev.starts, 1)

    def test_daten_nochmals_ab_offset(self):
        dev = basis2(rewind=4096)
        self.assertEqual(self.flasher(dev, fw2.format2("0.4.0")).run(), "0.4.0")
        self.assertEqual(bytes(dev.stage), fw2.image("0.4.0"))

    def test_stromausfall_beim_kopieren(self):
        dev = basis2(copy_cut=True)
        self.assertEqual(self.flasher(dev, fw2.format2("0.4.0", rollback=2)).run(), "0.4.0")
        self.assertEqual(dev.rollback, 2)


if __name__ == "__main__":
    unittest.main()
