"""Tests Bootloader Stufe 2: Update-Dateien Format 2, Signatur am PC, Gegenstelle mit Testschluessel."""
import os
import struct
import sys
import tempfile
import unittest
import zlib
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from pyst import bootproto as bp, flash, fw_keys  # noqa: E402
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
        h = bytearray(bp.UPD_LEN)
        h[0:8] = bp.UPD_MAGIC
        struct.pack_into("<HHI", h, 8, 1, bp.UPD_LEN, 0)
        h[16:26] = b"basis-g473"
        h[32:37] = b"rev31"
        h[40:45] = b"0.3.0"
        img = fw2.image("0.3.0", size=4096)
        struct.pack_into("<IIIII", h, 56, bp.APP_BASE, 4096, 1, 4096, zlib.crc32(img))
        u = bp.UpdateFile.parse(fw2.fix_crc(h) + img)
        self.assertEqual((u.format, u.format_text), (1, "Format 1, unsigniert"))
        bad = fw2.image("0.3.0", size=4096, reset=0x08000301)
        struct.pack_into("<I", h, 72, zlib.crc32(bad))
        with self.assertRaisesRegex(ValueError, "Vektortabelle"):
            bp.UpdateFile.parse(fw2.fix_crc(h) + bad)

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


if __name__ == "__main__":
    unittest.main()
