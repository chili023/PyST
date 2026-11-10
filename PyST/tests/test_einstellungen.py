"""Einstellungen (Entwurf 08.11.2026): Flash-Wege Arduino Mega (avrdude) und STM32 F446 (Laufwerk NODE_F446RE,
st-flash, dfu-util), unsignierte Manifest-Einträge mega/stm32_f446, Firmware-Seite Kunde/Experte, Zange/Lambda-Teile.

Alles mit Attrappen: kein serieller Port, kein Laufwerk, kein avrdude/st-flash wird geöffnet oder ausgeführt.

    python -m pytest tests/test_einstellungen.py
"""
import hashlib
import importlib
import io
import json
import os
import struct
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from conftest import close_window  # noqa: E402
from pyst import flash, update  # noqa: E402
from pyst.flash import FlashError  # noqa: E402


def ihex_zeile(addr: int, typ: int, data: bytes) -> str:
    rec = bytes([len(data)]) + addr.to_bytes(2, "big") + bytes([typ]) + data
    return ":" + (rec + bytes([(-sum(rec)) & 0xFF])).hex().upper()


def ihex(data: bytes, start: int = 0) -> str:
    lines = []
    for i in range(0, len(data), 16):
        a = start + i
        if i == 0 or a & 0xFFFF == 0:
            lines.append(ihex_zeile(0, 4, (a >> 16).to_bytes(2, "big")))
        lines.append(ihex_zeile(a & 0xFFFF, 0, data[i:i + 16]))
    lines.append(":00000001FF")
    return "\n".join(lines) + "\n"


def f446_bin(version: str = "1.2.0", size: int = 4096) -> bytes:
    head = struct.pack("<II", 0x20020000, 0x08000201)
    body = f"PST-STM32 F446 {version}".encode()
    return (head + body).ljust(size, b"\xff")


def schreibe(folder: str, name: str, data) -> str:
    path = os.path.join(folder, name)
    with open(path, "wb" if isinstance(data, bytes) else "w") as fh:
        fh.write(data)
    return path


class IntelHex(unittest.TestCase):
    def test_lesen(self):
        data = bytes(range(40))
        got, lo, hi = flash.parse_ihex(ihex(data))
        self.assertEqual((got, lo, hi), (data, 0, 40))

    def test_erweiterte_adresse_und_luecke(self):
        text = ihex_zeile(0, 4, b"\x00\x01") + "\n" + ihex_zeile(0x10, 0, b"\xaa\xbb") + "\n" + \
            ihex_zeile(0x14, 0, b"\xcc") + "\n:00000001FF\n"
        got, lo, hi = flash.parse_ihex(text)
        self.assertEqual((lo, hi), (0x10010, 0x10015))
        self.assertEqual(got, b"\xaa\xbb\xff\xff\xcc")

    def test_kaputt(self):
        gut = ihex(b"\x01\x02\x03")
        for text, msg in ((gut.replace(":00000001FF\n", ""), "Endezeile"),
                          (gut.replace("010203F7", "010204F7"), "Prüfsumme"),
                          ("hallo\n", "Keine Intel-HEX"),
                          (":00000001FF\n", "keine Daten")):
            with self.subTest(msg=msg), self.assertRaisesRegex(FlashError, msg):
                flash.parse_ihex(text)


class MegaDatei(unittest.TestCase):
    def test_version_aus_inhalt(self):
        with tempfile.TemporaryDirectory() as t:
            path = schreibe(t, "pst-mega-3.2.0.hex", ihex(b"\x0c\x94" * 50 + b"PST Version 3.1.4 vom"))
            img = flash.load_mega_image(t)
            self.assertEqual((img.target, img.version, img.main), (flash.MEGA, "3.1.4", path))
            img = flash.load_mega_image(path)                      # Datei statt Ordner
            self.assertEqual(img.version, "3.1.4")

    def test_version_aus_name(self):
        with tempfile.TemporaryDirectory() as t:
            schreibe(t, "pst-mega-3.2.0.hex", ihex(b"\x0c\x94" * 50))
            self.assertEqual(flash.load_mega_image(t).version, "3.2.0")

    def test_falsche_adressen(self):
        with tempfile.TemporaryDirectory() as t:
            schreibe(t, "x.hex", ihex(b"\x00" * 32, start=0x3E000))     # im Bootloader
            with self.assertRaisesRegex(FlashError, "ATmega2560"):
                flash.load_mega_image(t)

    def test_keine_datei(self):
        with tempfile.TemporaryDirectory() as t, self.assertRaises(FlashError):
            flash.load_mega_image(t)


class F446Datei(unittest.TestCase):
    def test_gut(self):
        with tempfile.TemporaryDirectory() as t:
            path = schreibe(t, "pst-stm32-f446-1.2.0.bin", f446_bin("1.1.7"))
            img = flash.load_f446_image(t)
            self.assertEqual((img.target, img.version), (flash.F446, "1.1.7"))
            self.assertEqual(img.files, [(0x08000000, path)])

    def test_falsch(self):
        esp = b"\xe9" + b"\x00" * 100
        vektor = struct.pack("<II", 0x10000000, 0x08000201).ljust(512, b"\x00")
        ungerade = struct.pack("<II", 0x20020000, 0x08000200).ljust(512, b"\x00")
        for data, msg in ((esp, "ESP"), (vektor, "Vektortabelle"), (ungerade, "Vektortabelle"),
                          (b"\x00" * 4, "Größe"), (b"\x00" * (512 * 1024 + 1), "Größe"),
                          (flash.bp.UPD_MAGIC + b"\x00" * 100, "Update-Datei")):
            with self.subTest(msg=msg), tempfile.TemporaryDirectory() as t:
                schreibe(t, "f.bin", data)
                with self.assertRaisesRegex(FlashError, msg):
                    flash.load_f446_image(t)


class Befehlszeilen(unittest.TestCase):
    def test_avrdude(self):
        img = flash.Image(flash.MEGA, "/x", main="/x/pst-mega.hex")
        cmd = flash.avrdude_command(["avrdude", "-C", "/c/avrdude.conf"], "/dev/ttyACM9", img)
        self.assertEqual(cmd, ["avrdude", "-C", "/c/avrdude.conf", "-p", "m2560", "-c", "wiring", "-P",
                               "/dev/ttyACM9", "-b", "115200", "-D", "-U", "flash:w:/x/pst-mega.hex:i"])

    def test_stflash_und_dfu(self):
        img = flash.Image(flash.F446, "/x", files=[(0x08000000, "/x/f.bin")], main="/x/f.bin")
        self.assertEqual(flash.f446_stflash_command(["st-flash"], img),
                         ["st-flash", "--reset", "write", "/x/f.bin", "0x8000000"])
        self.assertEqual(flash.dfu_command(["dfu-util"], img),
                         ["dfu-util", "-a", "0", "-s", "0x08000000:leave", "-D", "/x/f.bin"])

    def test_avrdude_suchen(self):
        with tempfile.TemporaryDirectory() as t:
            bin_ = os.path.join(t, "avrdude", "8.0", "bin")
            os.makedirs(bin_)
            os.makedirs(os.path.join(t, "avrdude", "8.0", "etc"))
            exe = schreibe(bin_, "avrdude", "")
            conf = schreibe(os.path.join(t, "avrdude", "8.0", "etc"), "avrdude.conf", "")
            with mock.patch.object(flash, "avrdude_kandidaten", lambda: ["/gibt/es/nicht", exe]):
                self.assertEqual(flash.find_avrdude(), [exe, "-C", conf])
            os.remove(conf)
            with mock.patch.object(flash, "avrdude_kandidaten", lambda: [exe]):
                self.assertEqual(flash.find_avrdude(), [exe])
            with mock.patch.object(flash, "avrdude_kandidaten", lambda: ["/gibt/es/nicht"]):
                self.assertIsNone(flash.find_avrdude())

    def test_kandidaten_arduino_ide(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {"HOME": t}), \
                mock.patch.object(flash.shutil, "which", lambda _n: None):
            bin_ = os.path.join(t, ".arduino15", "packages", "arduino", "tools", "avrdude", "6.3.0", "bin")
            os.makedirs(bin_)
            exe = schreibe(bin_, "avrdude", "")
            self.assertIn(exe, flash.avrdude_kandidaten())

    def test_mega_befehl(self):
        with tempfile.TemporaryDirectory() as t:
            hexp = schreibe(t, "pst-mega-3.2.0.hex", ihex(b"\x0c\x94" * 20))
            with mock.patch.object(flash, "find_avrdude", lambda: ["avrdude"]):
                img, cmd = flash.build_command(flash.MEGA, t, "/dev/ttyACM7")
                self.assertEqual(cmd[-1], f"flash:w:{hexp}:i")
                self.assertIn("/dev/ttyACM7", cmd)
                with self.assertRaisesRegex(FlashError, "nicht am USB"):
                    flash.build_command(flash.MEGA, t, None)
            with mock.patch.object(flash, "find_avrdude", lambda: None), \
                    self.assertRaisesRegex(FlashError, "avrdude nicht gefunden"):
                flash.build_command(flash.MEGA, t, "/dev/ttyACM7")

    def test_f446_wege(self):
        with tempfile.TemporaryDirectory() as t:
            schreibe(t, "f.bin", f446_bin())
            with mock.patch.object(flash, "nucleo_laufwerke", lambda muster=None: ["/media/u/NODE_F446RE"]):
                self.assertEqual(flash.build_command(flash.F446, t, None)[1], [])          # kopieren
                with mock.patch.object(flash, "find_stflash", lambda: ["st-flash"]):
                    self.assertEqual(flash.build_command(flash.F446_STLINK, t, None)[1][:3],
                                     ["st-flash", "--reset", "write"])
            with mock.patch.object(flash, "nucleo_laufwerke", lambda muster=None: []), \
                    mock.patch.object(flash, "find_stflash", lambda: None):
                with mock.patch.object(flash, "find_dfu_util", lambda: ["dfu-util"]):
                    self.assertEqual(flash.build_command(flash.F446, t, None)[1][0], "dfu-util")
                with mock.patch.object(flash, "find_dfu_util", lambda: None), \
                        self.assertRaisesRegex(FlashError, "NODE_F446RE"):
                    flash.build_command(flash.F446, t, None)


class Erkennung(unittest.TestCase):
    def test_usb_ids(self):
        class P:
            def __init__(self, dev, vid, pid):
                self.device, self.vid, self.pid = dev, vid, pid

        fake = mock.Mock()
        fake.comports.return_value = [P("/dev/ttyACM0", 0x2341, 0x0042), P("/dev/ttyUSB0", 0x1A86, 0x7523),
                                      P("/dev/ttyACM1", 0x0483, 0x374B), P("/dev/ttyS0", None, None)]
        with mock.patch.object(flash, "list_ports", fake):
            self.assertEqual(flash.mega_ports(), ["/dev/ttyACM0", "/dev/ttyUSB0"])
            self.assertIsNone(flash.mega_port())                   # zwei: nicht raten
            self.assertEqual(flash.f446_ports(), ["/dev/ttyACM1"])
            self.assertEqual(flash.f446_port(), "/dev/ttyACM1")
        with mock.patch.object(flash, "list_ports", None):
            self.assertEqual(flash.mega_ports(), [])

    def test_nucleo_laufwerk(self):
        with tempfile.TemporaryDirectory() as t:
            echt = os.path.join(t, "NODE_F446RE")
            leer = os.path.join(t, "NODE_F446RE1")
            os.makedirs(echt)
            os.makedirs(leer)
            schreibe(echt, "MBED.HTM", "<html>")
            self.assertEqual(flash.nucleo_laufwerke([os.path.join(t, "NODE_F446RE*")]), [echt])
            self.assertFalse(flash.ist_nucleo(leer))

    def test_versionen(self):
        class L:
            is_zange_usb = is_basis = False

            def __init__(self, stm, fw):
                self.is_stm, self.caps = stm, {"fw": fw}

        self.assertEqual(flash.device_versions(L(False, ""), "Version 3.1.0 vom 01.02.2024"),
                         {flash.MEGA: "3.1.0"})
        self.assertEqual(flash.device_versions(L(True, "PST-STM32 1.1.2"), ""),
                         {flash.F446: "1.1.2", flash.F446_STLINK: "1.1.2"})
        self.assertEqual(flash.device_versions(L(False, ""), ""), {})


class F446Kopieren(unittest.TestCase):
    """F446Flasher gegen ein Verzeichnis als „Laufwerk“; Ab- und Anmelden des ST-Link gespielt."""

    def flasher(self, t, fail=False, ports=("/dev/ttyACM3",), cancel=False):
        drive = os.path.join(t, "NODE_F446RE")
        os.makedirs(drive)
        schreibe(drive, "DETAILS.TXT", "Version: 0221")
        src = schreibe(t, "pst-stm32-f446-1.2.0.bin", f446_bin("1.2.0", 40000))
        img = flash.load_f446_image(src)
        state = {"n": 0}

        def laufwerke():
            state["n"] += 1
            if state["n"] == 3 and fail:
                schreibe(drive, "FAIL.TXT", "The interface firmware FAILED")
            return [] if state["n"] == 2 else [drive]               # einmal weg, dann wieder da

        seen = []
        f = flash.F446Flasher(img, laufwerke=laufwerke, ports=lambda: list(ports), progress=lambda d, n: seen.append(d),
                              cancelled=lambda: cancel, sleep=lambda _s: None)
        f.WAIT_GONE_S = f.WAIT_BACK_S = f.WAIT_PORT_S = 0.2
        return f, drive, seen

    def test_erfolg(self):
        with tempfile.TemporaryDirectory() as t:
            f, drive, seen = self.flasher(t)
            self.assertEqual(f.run(), "1.2.0")
            with open(os.path.join(drive, "pst-stm32-f446-1.2.0.bin"), "rb") as fh:
                self.assertEqual(fh.read(), f446_bin("1.2.0", 40000))
            self.assertEqual(seen[-1], 40000)

    def test_fail_txt(self):
        with tempfile.TemporaryDirectory() as t:
            f, _d, _s = self.flasher(t, fail=True)
            with self.assertRaisesRegex(FlashError, "FAILED"):
                f.run()

    def test_abbruch_und_kein_laufwerk(self):
        with tempfile.TemporaryDirectory() as t:
            f, _d, _s = self.flasher(t, cancel=True)
            with self.assertRaises(flash.FlashCancelled):
                f.run()
            f.laufwerke = lambda: []
            with self.assertRaisesRegex(FlashError, "nicht gefunden"):
                f.run()


class ManifestUnsigniert(unittest.TestCase):
    """mega und stm32_f446 stehen ohne Signatur im Manifest: Größe und SHA-256 genügen."""

    def manifest(self, data_mega, data_f446):
        def e(name, data, version):
            return {"version": version, "file": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        return json.dumps({"targets": {"mega": e("pst-mega-3.2.0.hex", data_mega, "3.2.0"),
                                       "stm32_f446": e("pst-stm32-f446-1.2.0.bin", data_f446, "1.2.0")}})

    def laden(self, entry, data):
        class Antwort(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with tempfile.TemporaryDirectory() as t, \
                mock.patch.object(update.urllib.request, "urlopen", lambda *a, **k: Antwort(data)):
            path = update.download(entry, t, "https://beispiel.invalid/firmware")
            with open(path, "rb") as fh:
                return fh.read()

    def test_laden_und_pruefen(self):
        m = ihex(b"\x0c\x94" * 20).encode()
        f = f446_bin()
        got = update.parse_manifest(self.manifest(m, f))
        self.assertEqual(set(got), {"mega", "stm32_f446"})
        self.assertNotIn("mega", update.SIGNED_TARGETS)
        self.assertNotIn("stm32_f446", update.SIGNED_TARGETS)
        self.assertEqual(got["mega"].target, flash.MEGA)
        self.assertEqual(got["stm32_f446"].target, flash.F446)
        self.assertEqual(self.laden(got["mega"], m), m)
        self.assertEqual(self.laden(got["stm32_f446"], f), f)

    def test_falsche_pruefsumme_und_groesse(self):
        m, f = ihex(b"\x01" * 20).encode(), f446_bin()
        got = update.parse_manifest(self.manifest(m, f))
        with self.assertRaisesRegex(update.UpdateError, "Prüfsumme"):
            self.laden(got["mega"], m[:-1] + b"X")
        with self.assertRaisesRegex(update.UpdateError, "Größe|größer"):
            self.laden(got["stm32_f446"], f + b"\x00")


class FakeLink:
    is_basis = is_tcp = is_zange_usb = bootloader = False
    ign_source = ""

    def __init__(self, stm=False, port="/dev/ttyACM0", fw=""):
        self.is_stm, self.port, self.caps = stm, port, {"fw": fw}


class FirmwareSeite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from pyst import flash_page
        self.flash_page = flash_page
        self.saved = []
        self.settings = {}
        self.link = None
        self.page = flash_page.FlashPage(self.settings, lambda: self.saved.append(1), lambda: self.link,
                                         lambda: None)
        self.page.timer.stop()

    def remote(self, **versionen):
        names = {flash.MEGA: "pst-mega-{}.hex", flash.F446: "pst-stm32-f446-{}.bin", flash.BASIS_STM: "b-{}.pystfw",
                 flash.BASIS_ESP: "e-{}.pystfw", flash.ZANGE: "z-{}.pystfw"}
        self.page.remote = {k: update.Entry(k, v, names[k].format(v), 10, "0" * 64) for k, v in versionen.items()}
        self.page.stand = "jetzt"

    def test_expertenmodus_gespeichert(self):
        p = self.page
        self.assertFalse(p.experte_an)
        self.assertTrue(p.experte.isHidden())
        p.schalter.setChecked(True)
        self.assertTrue(p.settings["firmware_experte"])
        self.assertTrue(p.kunde.isHidden())
        self.assertFalse(p.experte.isHidden())
        self.assertEqual(self.saved, [1])
        p2 = self.flash_page.FlashPage({"firmware_experte": True}, lambda: None, lambda: None, lambda: None)
        p2.timer.stop()
        self.assertTrue(p2.experte_an)
        self.assertTrue(p2.kunde.isHidden())

    def test_karten(self):
        p = self.page
        self.assertEqual(set(p.karten), {"basis", "zange", "mega", "f446"})
        self.remote(**{flash.MEGA: "3.2.0", flash.F446: "1.2.0"})
        self.link = FakeLink(stm=False)
        p.installiert = {flash.MEGA: "3.1.0"}
        p._anzeigen()
        k = p.karten["mega"]
        self.assertIn("erkannt · USB", k.erkannt.text())
        self.assertEqual((k.installiert.text(), k.verfuegbar.text()), ("3.1.0", "3.2.0"))
        self.assertFalse(k.neu.isHidden())
        self.assertTrue(k.b_los.isEnabled())
        self.assertEqual(p.updates_offen(), [flash.MEGA])
        self.assertIn("(1)", p.b_alle.text())
        f = p.karten["f446"]
        self.assertIn("nicht angeschlossen", f.erkannt.text())
        self.assertTrue(f.neu.isHidden())
        p.installiert = {flash.MEGA: "3.2.0"}
        p._anzeigen()
        self.assertEqual(k.b_los.text(), "Aktuell ✔")
        self.assertFalse(k.b_los.isEnabled())
        self.assertEqual(p.updates_offen(), [])

    def test_erkennen(self):
        p = self.page
        p.ports = {"mega": ["/dev/ttyACM1"], "nucleo": ["/media/u/NODE_F446RE"]}
        er = p._erkennen(None)
        self.assertEqual((er["mega"], er["f446"], er["basis"], er["zange"]), ("USB", "USB", "", ""))
        er = p._erkennen(FakeLink(stm=True, port="SIM:stm"))
        self.assertEqual(er["f446"], "USB (Simulator)")

    def angeschlossen(self):
        return {k: v.angeschlossen for k, v in self.page.karten.items()}

    def test_ohne_geraete_ausgegraut(self):
        """Nichts angeschlossen: alle Karten ausgegraut, nur die verfügbare Version, Knopf aus mit Tooltip."""
        p = self.page
        self.remote(**{flash.MEGA: "3.2.0", flash.BASIS_STM: "0.4.0"})
        p.installiert = {flash.MEGA: "3.1.0"}                  # zuletzt gesehen, jetzt abgezogen
        p._ports({}, None)
        self.assertEqual(self.angeschlossen(), dict.fromkeys(("basis", "zange", "mega", "f446"), False))
        for key, k in p.karten.items():
            self.assertIn("nicht angeschlossen", k.erkannt.text())
            self.assertFalse(k.b_los.isEnabled(), key)
            self.assertEqual(k.b_los.toolTip(), self.flash_page.ANSCHLIESSEN[key])
            self.assertIn("Nicht angeschlossen", k.toolTip())
            self.assertTrue(k.neu.isHidden())
        self.assertEqual(p.karten["basis"].b_los.toolTip(), "Basis per USB anschließen")
        k = p.karten["mega"]
        self.assertEqual((k.installiert.text(), k.verfuegbar.text()), ("–", "3.2.0"))
        self.assertIn("Nicht angeschlossen", k.unten.text())
        self.assertEqual(p.updates_offen(), [])
        self.assertFalse(p.b_alle.isEnabled())

    def test_ausgrauen_ueber_das_thema(self):
        """Ausgegraut: Fensterfläche und Schrift „dim“ des aktiven Themas, angeschlossen wieder Kachel und Text."""
        from pyst import theme
        k = self.page.karten["mega"]
        self.page._ports({}, None)
        T = theme.tokens()
        self.assertIn(T["win"], k.styleSheet())
        self.assertIn(T["dim"], k.titel.styleSheet())
        self.assertIn(T["dim"], k.verfuegbar.styleSheet())
        self.page._ports({"mega": ["/dev/ttyACM1"]}, None)
        self.assertIn(T["panel"], k.styleSheet())
        self.assertIn(T["text"], k.titel.styleSheet())
        self.assertGreaterEqual(theme.kontrast(T["dim"], T["win"]), 4.5)

    def test_angeschlossen_aus_ports(self):
        """Zustand je Gerät aus den (gepatchten) Port-Listen; die Ports werden nur aufgelistet, nicht geöffnet."""
        p = self.page
        self.remote(**{flash.MEGA: "3.2.0", flash.F446: "1.2.0", flash.BASIS_STM: "0.4.0", flash.ZANGE: "0.4.0"})
        listen = {"basis_ports": [], "zange_ports": [], "mega_ports": [], "f446_ports": []}
        patches = [mock.patch.object(flash, name, lambda name=name: list(listen[name])) for name in listen]
        patches += [mock.patch.object(flash, "nucleo_laufwerke", lambda muster=None: list(listen.get("nucleo", []))),
                    mock.patch.object(flash, "open_serial", lambda *_a, **_k: self.fail("Port geöffnet"))]
        for pa in patches:
            pa.start()
            self.addCleanup(pa.stop)

        def lesen():
            p._ports(p._suche_ports(), None)
            return self.angeschlossen()

        self.assertFalse(any(lesen().values()))
        listen["basis_ports"] = ["/dev/ttyACM0"]
        self.assertEqual(lesen(), {"basis": True, "zange": False, "mega": False, "f446": False})
        listen["zange_ports"] = ["/dev/ttyACM2"]
        self.assertTrue(lesen()["zange"])
        listen["mega_ports"] = ["/dev/ttyACM1"]
        self.assertTrue(lesen()["mega"])
        self.assertTrue(p.karten["mega"].b_los.isEnabled())
        self.assertFalse(lesen()["f446"])
        listen["nucleo"] = ["/media/u/NODE_F446RE"]                    # nur das Laufwerk
        self.assertTrue(lesen()["f446"])
        listen["nucleo"], listen["f446_ports"] = [], ["/dev/ttyACM3"]   # nur der ST-Link-Port
        self.assertTrue(lesen()["f446"])
        self.assertEqual(p.karten["f446"].b_los.toolTip(), "")

    def test_angeschlossen_aus_verbindung(self):
        """Verbindung des Hauptfensters: Basis per WLAN, Zange per Funk (gekoppelt), Simulator als Mega."""
        p = self.page
        p.ports = {}
        basis = FakeLink(port="tcp://pyst-basis.local:2323")
        basis.is_basis, basis.is_tcp = True, True
        self.link = basis
        p._anzeigen()
        self.assertEqual(self.angeschlossen(), {"basis": True, "zange": False, "mega": False, "f446": False})
        self.assertIn("WLAN", p.karten["basis"].erkannt.text())
        self.assertIn("nur per USB", p.karten["basis"].hinweis.text())
        p.get_zange_state = lambda: "ok"                               # Zange per Funk da
        p.get_zange = lambda: "Zange 1"
        p._anzeigen()
        self.assertTrue(p.karten["zange"].angeschlossen)
        self.assertIn("Funk (Zange 1)", p.karten["zange"].erkannt.text())
        p.get_zange_state = lambda: "weg"
        p._anzeigen()
        self.assertFalse(p.karten["zange"].angeschlossen)
        basis.ign_source = "zange"                                     # gekoppelt laut Basis
        p._anzeigen()
        self.assertTrue(p.karten["zange"].angeschlossen)
        self.link = FakeLink(stm=False, port="SIM:161802")
        p._anzeigen()
        self.assertEqual(self.angeschlossen(), {"basis": False, "zange": False, "mega": True, "f446": False})
        self.assertIn("Simulator", p.karten["mega"].erkannt.text())

    def test_experte_zeilen_abgeblendet_aber_bedienbar(self):
        p = self.page
        p.schalter.setChecked(True)
        self.remote(**{flash.MEGA: "3.2.0", flash.F446: "1.2.0"})
        p._ports({}, None)
        for key, z in p.zeilen.items():
            self.assertFalse(z.angeschlossen, key)
            self.assertEqual(z.name.property("rolle"), "dim")
            self.assertTrue(z.b_datei.isEnabled())
            if z.erst:
                self.assertTrue(z.b_erst.isEnabled())
        self.assertTrue(p.zeilen["mega"].b_los.isEnabled())             # Erstflash ohne Verbindung
        self.assertTrue(p.zeilen["f446"].b_los.isEnabled())
        self.assertIn("nicht angeschlossen", p.zeilen["basis_stm"].zustand.text())
        p._ports({"mega": ["/dev/ttyACM1"], "basis": ["/dev/ttyACM0"]}, None)
        for key in ("mega", "basis_stm", "basis_esp", "lambda"):
            self.assertTrue(p.zeilen[key].angeschlossen, key)
            self.assertEqual(p.zeilen[key].name.property("rolle"), "")
        self.assertFalse(p.zeilen["f446"].angeschlossen)

    def test_software_kachel(self):
        from pyst import __version__
        p = self.page
        k = p.pyst_karte
        self.assertEqual(k.titel.text(), "PyST (Software)")
        self.assertEqual(k.installiert.text(), __version__)
        self.assertIn("Noch nicht gesucht", k.unten.text())
        self.assertTrue(k.b_los.isEnabled())
        p.set_datenordner(os.path.join(os.path.expanduser("~"), "PyST"))
        self.assertIn("Datenordner ~", k.unter.text())
        p._pyst_seen("99.0.0", __version__)
        self.assertIn("Update verfügbar: 99.0.0", k.erkannt.text())
        self.assertEqual(k.verfuegbar.text(), "99.0.0")
        self.assertFalse(k.neu.isHidden())
        self.assertIn("git pull", k.unten.text())
        self.assertIn("ZIP", k.unten.text())
        self.assertEqual(k.b_los.property("rolle"), "primaer")
        p._pyst_seen(__version__, __version__)
        self.assertIn("aktuell", k.erkannt.text())
        self.assertTrue(k.neu.isHidden())
        p._pyst_seen(None, __version__)
        self.assertIn("nicht abrufbar", k.unten.text())
        self.assertEqual(k.installiert.text(), __version__)
        p.schalter.setChecked(True)                                    # auch im Expertenmodus sichtbar
        self.assertFalse(k.isHidden())

    def test_verbindungswechsel(self):
        p = self.page
        p.resize(1200, 700)
        p.show()
        self.app.processEvents()
        aufrufe = []
        with mock.patch.object(p, "_refresh", lambda: aufrufe.append("refresh")), \
                mock.patch.object(p, "_anzeigen", lambda: aufrufe.append("anzeigen")):
            self.link = FakeLink()
            p.verbindung_geaendert()
            p.verbindung_geaendert("ok")                               # gleiche Verbindung, nur Zange
            p.hide()
            p.verbindung_geaendert()                                   # Reiter nicht sichtbar: nichts
        self.assertEqual(aufrufe, ["refresh", "anzeigen"])

    def test_ablauf_laden_dann_flashen(self):
        p = self.page
        self.remote(**{flash.MEGA: "3.2.0"})
        geladen = []
        with tempfile.TemporaryDirectory() as t:
            src = schreibe(t, "pst-mega-3.2.0.hex", ihex(b"\x0c\x94" * 20 + b"Version 3.2.0"))
            gestartet = []
            with mock.patch.object(update, "download", lambda e, folder, base: geladen.append(e.file) or src), \
                    mock.patch.object(p, "start", lambda key, frage=True, erzwingen=None:
                                      gestartet.append((key, frage, erzwingen))):
                p.aktualisieren([flash.MEGA], frage=False)
                end = time.time() + 5
                while not gestartet and time.time() < end:
                    p.bg.poll()
                    self.app.processEvents()
                    time.sleep(0.01)
            self.assertEqual(geladen, ["pst-mega-3.2.0.hex"])
            self.assertEqual(gestartet, [(flash.MEGA, False, False)])
            self.assertEqual(p.rows[flash.MEGA].path.text(), src)
            self.assertEqual(p.queue, [])

    def test_ohne_repo_eintrag(self):
        p = self.page
        p.aktualisieren([flash.F446], frage=False)
        self.assertIn("Keine Firmware im Repo", p.status.text())
        self.assertEqual(p.queue, [])

    def test_erzwingen(self):
        p = self.page
        p.installiert = {flash.MEGA: "3.2.0"}
        img = flash.Image(flash.MEGA, "", version="3.1.0")
        self.assertIn("nicht neuer", p._erzwingen_noetig(flash.MEGA, img))
        self.assertEqual(p._erzwingen_noetig(flash.MEGA, flash.Image(flash.MEGA, "", version="3.3.0")), "")
        self.assertIn("Signatur ungeprüft", p._erzwingen_noetig(flash.MEGA, flash.Image(flash.MEGA, "", warning="x")))
        befehle = []
        with tempfile.TemporaryDirectory() as t:
            schreibe(t, "pst-mega-3.1.0.hex", ihex(b"\x0c\x94" * 20 + b"Version 3.1.0"))
            p.rows[flash.MEGA].path.setText(t)
            with mock.patch.object(flash, "find_avrdude", lambda: ["avrdude"]), \
                    mock.patch.object(p, "_port_fuer", lambda _k: "/dev/ttyACM5"), \
                    mock.patch.object(p, "run", lambda key, cmd: befehle.append(cmd)):
                p.start(flash.MEGA, frage=False)
                self.assertEqual(befehle, [])
                self.assertIn("erzwingen", p.status.text())
                p.start(flash.MEGA, frage=False, erzwingen=True)
        self.assertEqual(len(befehle), 1)
        self.assertEqual(befehle[0][:5], ["avrdude", "-p", "m2560", "-c", "wiring"])
        self.assertIn("/dev/ttyACM5", befehle[0])

    def test_f446_ueber_laufwerk(self):
        p = self.page
        usb = []
        with tempfile.TemporaryDirectory() as t:
            schreibe(t, "f.bin", f446_bin("1.3.0"))
            p.rows[flash.F446].path.setText(t)
            with mock.patch.object(flash, "nucleo_laufwerke", lambda muster=None: ["/media/u/NODE_F446RE"]), \
                    mock.patch.object(p, "run_usb", lambda key, image: usb.append((key, image.version))), \
                    mock.patch.object(p, "run", lambda key, cmd: self.fail("kein externes Werkzeug")):
                p.start(flash.F446, frage=False)
        self.assertEqual(usb, [(flash.F446, "1.3.0")])

    def test_log_speichern(self):
        p = self.page
        p._log("Zeile eins\r\nZeile zwei")
        self.assertEqual(p.log.toPlainText().count("Zeile"), 2)
        with tempfile.TemporaryDirectory() as t:
            path = p.log_speichern(os.path.join(t, "log.txt"))
            with open(path, encoding="utf-8") as fh:
                self.assertIn("Zeile zwei", fh.read())


class ZangeTeile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_log_belegt(self):
        from pyst.hw_page import log_belegt
        pct, rest = log_belegt("113000/452000")
        self.assertAlmostEqual(pct, 25.0)
        self.assertEqual(rest, "113.000 von 452.000")
        self.assertEqual(log_belegt("12,5 % · 3 Läufe"), (12.5, "3 Läufe"))
        self.assertEqual(log_belegt(""), (None, ""))
        self.assertEqual(log_belegt("1/0"), (None, ""))

    def test_werteformular(self):
        from pyst.hw_page import ZANGE_FELDER, WerteFormular
        f = WerteFormular(ZANGE_FELDER, {"deadtime_us": "800", "blitz_phi_x10": "0", "agc_mode": "2"}, {})
        f.fuellen({"deadtime_us": "1200", "blitz_phi_x10": "-35", "agc_mode": "1"}.items())
        self.assertEqual(f.wert("deadtime_us"), "1200")
        self.assertEqual(f.wert("blitz_phi_x10"), "-35")
        self.assertEqual(list(f.aenderungen()), [])
        f.setze("deadtime_us", "900")
        self.assertEqual(list(f.aenderungen()), [("deadtime_us", "900")])
        f.standardwerte()
        self.assertEqual(f.wert("deadtime_us"), "800")
        self.assertEqual(f.wert("agc_mode"), "2")

    def test_pruefen_zuschnitt(self):
        from pyst.pruef import Check
        from pyst.pruef_page import ALLE, LAMBDA, ZANGE, gehoert_zu
        z = Check("Zange (Funk)", "Verbindung", "ok")
        w = Check("Basis", "Wideband-Controller", "ok")
        b = Check("Basis", "Klima BME280", "ok")
        self.assertEqual([gehoert_zu(c, ZANGE) for c in (z, w, b)], [True, False, False])
        self.assertEqual([gehoert_zu(c, LAMBDA) for c in (z, w, b)], [False, True, False])
        self.assertTrue(all(gehoert_zu(c, ALLE) for c in (z, w, b)))


class Unterreiter(unittest.TestCase):
    """Hauptfenster: sieben Unterreiter, Schalter „vor START nachfragen“ (Einstellung start_nachfrage_setup)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("HOME", "QT_QPA_PLATFORM")}
        os.environ["HOME"] = self.tmp.name
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6 import QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from pyst import gui
        importlib.reload(gui)
        for name in ("list_serial_ports", "pst_board_ports", "zange_usb_ports"):
            p = mock.patch.object(gui, name, lambda: [])
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(flash, "list_ports", None)
        p.start()
        self.addCleanup(p.stop)
        self.gui = gui
        self.w = gui.MainWindow()

    def tearDown(self):
        close_window(self.w, self.app)
        self.w = None
        self.tmp.cleanup()
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_reiter(self):
        t = self.w.settings_tabs
        self.assertEqual([t.tabText(i) for i in range(t.count())],
                         ["Rolle/Lauf", "Verbindung", "Zange", "Lambda", "Analogeingänge", "ECU", "Software && Firmware",
                          "WiFi"])
        self.assertIs(t.widget(6), self.w.page_flash)
        self.assertIs(self.w.seiten["firmware"], self.w.page_flash)     # Schlüssel bleibt (gespeicherter Reiter)
        from pyst import __version__
        k = self.w.page_flash.pyst_karte
        self.assertEqual(k.installiert.text(), __version__)
        self.assertIn("Datenordner ~", k.unter.text())                  # Datenordner des Hauptfensters, kurz
        self.w._zange_state = "ok"                                      # Zustand der Seite Zange kommt an
        self.assertEqual(self.w.page_flash.get_zange_state(), "ok")
        self.w.show_page(self.w.page_kalib)                         # Unterseite: Reiter Rolle/Lauf
        self.assertIs(t.currentWidget(), self.w.page_settings)

    def test_start_nachfrage(self):
        s = self.w.page_settings
        self.assertTrue(self.w.settings["start_nachfrage_setup"])
        self.assertTrue(s.start_nachfrage.isChecked())
        s.start_nachfrage.setChecked(False)
        self.assertFalse(self.w.settings["start_nachfrage_setup"])
        with open(self.gui.SETTINGS_FILE, encoding="utf-8") as fh:
            self.assertFalse(json.load(fh)["start_nachfrage_setup"])

    def test_ausgegraut(self):
        hw = self.w.page_hw
        self.assertFalse(hw.b_haupt.isEnabled())
        self.assertTrue(hw.b_haupt.toolTip())
        self.assertFalse(self.w.page_pruef_zange.b_tasten.isEnabled())


if __name__ == "__main__":
    unittest.main()
