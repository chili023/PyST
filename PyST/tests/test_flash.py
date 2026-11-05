"""Tests Flash-Werkzeug: Schutz vor falscher Firmware, Befehlszeilen, Erkennung der ESP-Bruecke, USB-Update."""
import json
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyst import bootproto as bp, flash, link  # noqa: E402
from pyst.sim import BootSimDevice  # noqa: E402


def esp_build(folder, project, part_offset="0x8000", ota="0xf000"):
    args = {"flash_settings": {"flash_mode": "dio", "flash_size": "4MB", "flash_freq": "80m"},
            "bootloader": {"offset": "0x0"}, "partition-table": {"offset": part_offset}, "otadata": {"offset": ota},
            "app": {"offset": "0x20000"}}
    with open(os.path.join(folder, "flasher_args.json"), "w") as fh:
        json.dump(args, fh)
    with open(os.path.join(folder, "project_description.json"), "w") as fh:
        json.dump({"project_name": project, "project_version": "abc", "target": "esp32c3"}, fh)
    for name in ("bootloader.bin", "partitions.bin", "ota_data_initial.bin", "firmware.bin"):
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(b"\xe9" + bytes(63))


def stm_bin(folder, sp=0x20020000, reset=0x08000F01):
    with open(os.path.join(folder, "firmware.bin"), "wb") as fh:
        fh.write(struct.pack("<II", sp, reset) + bytes(100))


def update_file(version="0.2.0", size=4096, target=b"basis-g473", board=b"rev31", flags=0, reset=bp.APP_BASE + 0x301):
    """Kuenstliches Update (keine echte Firmware im oeffentlichen Repository), Aufbau wie tools/pyst_image.py."""
    img = bytearray(struct.pack("<II", 0x20020000, reset) + bytes(range(256)) * (size // 256 + 1))[:size]
    h = bytearray(bp.UPD_LEN)
    h[0:8] = bp.UPD_MAGIC
    struct.pack_into("<HHI", h, 8, 1, bp.UPD_LEN, flags)
    h[16:16 + len(target)] = target
    h[32:32 + len(board)] = board
    h[40:40 + len(version)] = version.encode()
    struct.pack_into("<IIIII", h, 56, bp.APP_BASE, size, 0x1234, size, zlib.crc32(bytes(img)))
    struct.pack_into("<I", h, 252, zlib.crc32(bytes(h[:252])))
    return bytes(h) + bytes(img)


class Bilder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_zange_passt(self):
        esp_build(self.d, "pyst-zange")
        img = flash.load_image(flash.ZANGE, self.d)
        self.assertEqual([a for a, _ in img.files], [0x0, 0x8000, 0xF000, 0x20000])
        self.assertIn("pyst-zange", img.summary())

    def test_adressen_kommen_aus_dem_build(self):
        esp_build(self.d, "basis-esp32c3", "0x10000", "0x17000")
        img = flash.load_image(flash.BASIS_ESP, self.d)
        self.assertEqual([a for a, _ in img.files], [0x0, 0x10000, 0x17000, 0x20000])

    def test_falsche_firmware_wird_abgelehnt(self):
        esp_build(self.d, "pyst-zange")
        with self.assertRaisesRegex(flash.FlashError, "Falsche Firmware"):
            flash.load_image(flash.BASIS_ESP, self.d)
        with self.assertRaisesRegex(flash.FlashError, "Falsche Firmware"):
            flash.load_image(flash.BASIS_STLINK, self.d)      # ESP-Abbild auf dem STM32
        esp_build(self.d, "basis-esp32c3")
        with self.assertRaisesRegex(flash.FlashError, "Falsche Firmware"):
            flash.load_image(flash.ZANGE, self.d)

    def test_projektordner_findet_den_build(self):
        b = os.path.join(self.d, ".pio", "build", "zange_c3")
        os.makedirs(b)
        esp_build(b, "pyst-zange")
        self.assertEqual(flash.load_image(flash.ZANGE, self.d).folder, b)

    def test_unvollstaendig_und_leer(self):
        with self.assertRaises(flash.FlashError):
            flash.load_image(flash.ZANGE, "")
        with self.assertRaises(flash.FlashError):
            flash.load_image(flash.ZANGE, self.d)
        esp_build(self.d, "pyst-zange")
        os.remove(os.path.join(self.d, "partitions.bin"))
        with self.assertRaisesRegex(flash.FlashError, "partitions.bin"):
            flash.load_image(flash.ZANGE, self.d)

    def test_stm(self):
        stm_bin(self.d)
        img = flash.load_image(flash.BASIS_STLINK, self.d)
        self.assertEqual(img.files[0][0], flash.STM_FLASH_ADDR)
        cmd = flash.stm_command(["st-flash"], img)
        self.assertEqual(cmd[1:4], ["--connect-under-reset", "--reset", "write"])
        self.assertEqual(cmd[-1], "0x8000000")
        self.assertEqual(flash.build_command(flash.BASIS_STM, self._upd(), None)[1], [])    # kein Werkzeug
        stm_bin(self.d, reset=0x42000000)
        with self.assertRaisesRegex(flash.FlashError, "Vektortabelle"):
            flash.load_image(flash.BASIS_STLINK, self.d)

    def _upd(self, data=None, name="firmware.pystfw"):
        path = os.path.join(self.d, name)
        with open(path, "wb") as fh:
            fh.write(update_file() if data is None else data)
        return path

    def test_st_link_nimmt_die_anwendung_allein_nicht(self):
        """Eine ab 0x08008000 gelinkte firmware.bin an 0x08000000 ergaebe ein Geraet, das nicht startet."""
        stm_bin(self.d, reset=bp.APP_BASE + 0x1001)
        with self.assertRaisesRegex(flash.FlashError, "Anwendung allein"):
            flash.load_image(flash.BASIS_STLINK, self.d)

    def test_st_link_gesamtdatei(self):
        boot = struct.pack("<II", 0x20020000, 0x08000401) + bytes(100)
        app = update_file()[bp.UPD_LEN:]
        app = app[:bp.IMG_HDR_OFS] + struct.pack("<I", bp.IMG_MAGIC) + app[bp.IMG_HDR_OFS + 4:]
        with open(os.path.join(self.d, flash.GESAMT_NAME), "wb") as fh:
            fh.write(boot + b"\xff" * (bp.BOOT_SIZE - len(boot)) + app)
        stm_bin(self.d)                                       # daneben: wird nicht genommen
        img = flash.load_image(flash.BASIS_STLINK, self.d)
        self.assertEqual(os.path.basename(img.main), flash.GESAMT_NAME)
        self.assertEqual(img.project, "Bootloader + Anwendung")
        self._upd()
        with self.assertRaisesRegex(flash.FlashError, "Update-Datei"):
            flash.load_image(flash.BASIS_STLINK, os.path.join(self.d, "firmware.pystfw"))

    def test_update_datei(self):
        path = self._upd()
        for where in (path, self.d):
            img = flash.load_image(flash.BASIS_STM, where)
            self.assertEqual(img.update.version, "0.2.0")
        self.assertIn("0.2.0", img.summary())

    def test_update_im_projektordner(self):
        b = os.path.join(self.d, ".pio", "build", "basis_rev31")
        os.makedirs(b)
        with open(os.path.join(b, "firmware.pystfw"), "wb") as fh:
            fh.write(update_file())
        self.assertEqual(flash.load_image(flash.BASIS_STM, self.d).folder, b)

    def test_falsche_update_dateien(self):
        good = update_file()
        bad = {
            "zange": (update_file(target=b"zange-c3"), "Falsche Firmware"),
            "platine": (update_file(board=b"rev30"), "Falsche Firmware"),
            "esp": (b"\xe9" + bytes(400), "ESP-Abbild"),
            "bin": (struct.pack("<II", 0x20020000, 0x08008301) + bytes(400), "keine Update-Datei"),
            "kurz": (good[:-8], "beschädigt"),
            "bit": (good[:1000] + bytes((good[1000] ^ 1,)) + good[1001:], "beschädigt"),
            "kopf": (good[:50] + b"X" + good[51:], "beschädigt"),
            "signiert": (update_file(flags=1), "zu neu"),
            "vektor": (update_file(reset=0x08000301), "Vektortabelle"),
        }
        for name, (data, text) in bad.items():
            with self.subTest(name), self.assertRaisesRegex(flash.FlashError, text):
                flash.load_image(flash.BASIS_STM, self._upd(data, name + ".pystfw"))
        with self.assertRaisesRegex(flash.FlashError, "Mehrere"):
            flash.load_image(flash.BASIS_STM, self.d)

    def test_esp_befehl_und_port(self):
        esp_build(self.d, "pyst-zange")
        cmd = flash.esp_command(["esptool"], "/dev/x", flash.load_image(flash.ZANGE, self.d))
        self.assertEqual(cmd[cmd.index("--port") + 1], "/dev/x")
        self.assertEqual(cmd[cmd.index("--baud") + 1], "921600")
        self.assertIn("0x8000", cmd)
        with self.assertRaisesRegex(flash.FlashError, "nicht am USB"):
            flash.build_command(flash.ZANGE, self.d, None)


class Bruecke(unittest.TestCase):
    def port(self, device, location="0-1.2.2", hwid=""):
        return SimpleNamespace(device=device, vid=0x1209, pid=0x0001, location=location, hwid=hwid, interface=None,
                               description="PyST Basis rev31")

    def test_macos(self):
        self.assertTrue(link.is_esp_bridge(self.port("/dev/cu.usbmodem12203")))
        self.assertFalse(link.is_esp_bridge(self.port("/dev/cu.usbmodem12201")))

    def test_linux_windows(self):
        self.assertTrue(link.is_esp_bridge(self.port("/dev/ttyACM1", "1-1:1.2")))
        self.assertFalse(link.is_esp_bridge(self.port("/dev/ttyACM0", "1-1:1.0")))
        self.assertTrue(link.is_esp_bridge(self.port("COM7", "", "USB VID:PID=1209:0001 MI_02")))


class Rahmen(unittest.TestCase):
    def test_wie_die_firmware(self):
        """Dieselben Bytes wie in test_boot.c (link_encode)."""
        self.assertEqual(bp.encode_frame(bp.BL_DATA, 7, b"\x00\x01\x02"),
                         bytes((0x03, 0x03, 0x07, 0x05, 0x01, 0x02, 0x80, 0xBD, 0x00)))
        self.assertEqual(bp.crc16(b"123456789"), 0x29B1)

    def test_hin_und_zurueck(self):
        for data in (b"", b"\0", b"\0\0\0", bytes(range(256)) * 3, b"\xff" * 600, bytes(1000)):
            frames = bp.FrameDecoder().feed(bp.encode_frame(5, 9, data))
            self.assertEqual(frames, [(5, 9, data)])

    def test_muell_und_kippbits(self):
        d = bp.FrameDecoder()
        good = bp.encode_frame(1, 2, b"abc")
        self.assertEqual(d.feed(b"#BOOTLOADER;proto=1\r\n\0" + good), [(1, 2, b"abc")])
        bad = bytearray(good)
        bad[3] ^= 1
        self.assertEqual(d.feed(bytes(bad)), [])
        self.assertEqual(d.feed(good), [(1, 2, b"abc")])

    def test_kennzeile(self):
        i = bp.parse_info("#BOOTLOADER;proto=1;bl=1.0.0;board=rev31;app=keine;grund=kein_abbild\r\n")
        self.assertEqual((i["bl"], i["board"], i["grund"]), ("1.0.0", "rev31", "kein_abbild"))


class UsbUpdate(unittest.TestCase):
    """Ende zu Ende gegen den simulierten Bootloader."""

    def flasher(self, dev, data=None, **kw):
        self.logs = []
        self.prog = []
        upd = bp.UpdateFile.parse(data or update_file(size=20480))
        f = flash.UsbFlasher(upd, ports=dev.ports, opener=dev.open, log=self.logs.append,
                             progress=lambda a, b: self.prog.append((a, b)), **kw)
        f.WAIT_BOOT_S, f.WAIT_APP_S, f.START_S, f.REPLY_S = 3.0, 3.0, 3.0, 0.1
        return f

    def run_until_app(self, dev, version):
        end = time.time() + 3
        while time.time() < end and not (dev.ports() and dev.mode == "app"):
            time.sleep(0.01)
        self.assertEqual((dev.mode, dev.version), ("app", version))

    def test_von_der_anwendung_aus(self):
        dev = BootSimDevice("0.1.0")
        name = dev.ports()[0]
        self.assertEqual(self.flasher(dev).run(), "0.2.0")
        self.assertEqual((dev.mode, dev.version, dev.commit), ("app", "0.2.0", None))
        self.assertEqual(self.prog[-1], (20480, 20480))
        self.assertEqual(len(self.prog), 40)
        self.assertNotEqual(dev.ports()[0], name)             # Portname hat gewechselt
        self.assertEqual(dev.settings, {"rate_hz": "60"})

    def test_steckt_schon_im_bootloader(self):
        for dev in (BootSimDevice("0.1.0", mode="boot"), BootSimDevice(app=False)):
            self.assertEqual(self.flasher(dev).run(), "0.2.0")
            self.assertTrue(any("schon im Bootloader" in t for t in self.logs))

    def test_bruecke_ist_kein_messport(self):
        dev = BootSimDevice("0.1.0")
        real = flash.list_ports
        flash.list_ports = SimpleNamespace(comports=dev.comports)
        try:
            self.assertEqual(flash.basis_ports(), [dev.ports()[0]])
            self.assertEqual(flash.basis_port(), dev.ports()[0])
        finally:
            flash.list_ports = real

    def test_verlorene_und_kaputte_antworten(self):
        dev = BootSimDevice("0.1.0", drop={4, 9, 10}, corrupt={6, 20})
        self.assertEqual(self.flasher(dev).run(), "0.2.0")

    def test_geraet_fordert_daten_nochmals_an(self):
        dev = BootSimDevice("0.1.0", rewind=4096)
        self.assertEqual(self.flasher(dev).run(), "0.2.0")
        self.assertTrue(any("Wiederhole ab 3072" in t for t in self.logs))
        self.assertEqual(dev.version, "0.2.0")

    def test_falsche_datei_wird_vom_geraet_abgelehnt(self):
        dev = BootSimDevice("0.1.0", mode="boot")
        with self.assertRaisesRegex(flash.FlashError, "Falsche Firmware"):
            f = self.flasher(dev)
            f.update.board = "rev99"                          # PyST-Pruefung umgangen
            f.update.target = "zange"
            f.run()
        self.assertEqual(dev.version, "0.1.0")

    def test_kabel_weg_und_neuer_versuch(self):
        dev = BootSimDevice("0.1.0", unplug=10240)
        with self.assertRaisesRegex(flash.FlashError, "Bootloader antwortet nicht|Verbindung verloren"):
            self.flasher(dev).run()
        self.assertEqual((dev.mode, dev.version, dev.commit), ("boot", "0.1.0", None))
        dev.plug_in()
        self.assertEqual(self.flasher(dev).run(), "0.2.0")    # steckt noch im Bootloader, geht direkt weiter
        self.assertTrue(any("schon im Bootloader" in t for t in self.logs))

    def test_stromausfall_in_der_uebertragung(self):
        dev = BootSimDevice("0.1.0", power=4096)
        with self.assertRaises(flash.FlashError):
            self.flasher(dev).run()
        self.run_until_app(dev, "0.1.0")                      # alte Anwendung unversehrt
        self.assertEqual(self.flasher(dev).run(), "0.2.0")

    def test_stromausfall_beim_kopieren(self):
        dev = BootSimDevice("0.1.0", copy_cut=True)
        self.assertEqual(self.flasher(dev).run(), "0.2.0")    # setzt selbst fort
        self.assertGreater(dev.boots, 2)

    def test_abbruch_durch_den_anwender(self):
        dev = BootSimDevice("0.1.0")
        stop = threading.Event()
        f = self.flasher(dev, cancelled=stop.is_set)
        f.progress = lambda a, b: stop.set() if a >= 4096 else None
        with self.assertRaises(flash.FlashCancelled):
            f.run()
        self.run_until_app(dev, "0.1.0")
        self.assertIsNone(dev.commit)
        self.assertEqual(self.flasher(dev).run(), "0.2.0")

    def test_abbruch_beim_warten_auf_den_bootloader(self):
        """Nach #UPDATE darf die Basis nicht im Bootloader liegen bleiben."""
        dev = BootSimDevice("0.1.0")
        f = self.flasher(dev, cancelled=lambda: dev.mode != "app")
        with self.assertRaises(flash.FlashCancelled):
            f.run()
        self.run_until_app(dev, "0.1.0")

    def test_kopieren_misslingt(self):
        dev = BootSimDevice("0.1.0", copy_fail=True)
        with self.assertRaisesRegex(flash.FlashError, "kopierfehler"):
            self.flasher(dev).run()

    def test_falsche_version_nach_dem_update(self):
        dev = BootSimDevice("0.1.0", wrong_version="0.1.9")
        with self.assertRaisesRegex(flash.FlashError, "Falsche Version"):
            self.flasher(dev).run()

    def test_alte_firmware_ohne_update_befehl(self):
        dev = BootSimDevice("0.1.0", no_update=True)
        with self.assertRaisesRegex(flash.FlashError, "ST-Link"):
            self.flasher(dev).run()
        self.assertEqual(dev.mode, "app")

    def test_keine_oder_mehrere_basen(self):
        with self.assertRaisesRegex(flash.FlashError, "nicht am USB"):
            flash.UsbFlasher(bp.UpdateFile.parse(update_file()), ports=list, opener=None).run()
        with self.assertRaisesRegex(flash.FlashError, "Mehrere"):
            flash.UsbFlasher(bp.UpdateFile.parse(update_file()), ports=lambda: ["a", "b"], opener=None).run()

    def test_anwendung_gibt_gerade_telegramme_aus(self):
        dev = BootSimDevice("0.1.0", streaming=True)
        self.assertEqual(self.flasher(dev).run(), "0.2.0")


class FlashSeite(unittest.TestCase):
    """Reiter „Firmware“: Update laeuft im Hintergrund, Fortschritt kommt an, Port bleibt reserviert."""

    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from pyst import flash_page
        self.flash_page = flash_page
        self.real = flash.UsbFlasher
        self.addCleanup(lambda: setattr(flash, "UsbFlasher", self.real))
        self.page = flash_page.FlashPage({}, lambda: None, lambda: None, lambda: None)
        self.page.timer.stop()

    def wait(self):
        end = time.time() + 20
        while self.page.busy and time.time() < end:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertFalse(self.page.busy)

    def run_page(self, dev, cancel_at=None):
        def make(update, **kw):
            f = self.real(update, ports=dev.ports, opener=dev.open, **kw)
            f.REPLY_S = 0.1
            return f
        flash.UsbFlasher = make
        seen = []
        data = update_file(size=20480)
        img = flash.Image(flash.BASIS_STM, "", update=bp.UpdateFile.parse(data))
        self.page.run_usb(flash.BASIS_STM, img)
        self.assertTrue(self.page.busy)
        self.assertFalse(self.page.rows[flash.BASIS_STM].b_go.isEnabled())
        self.page.bar.valueChanged.connect(seen.append)
        if cancel_at:
            self.page.bar.valueChanged.connect(lambda v: self.page._cancel() if v >= cancel_at else None)
        self.wait()
        return seen

    def test_ziele(self):
        self.assertEqual(set(self.page.rows), {flash.ZANGE, flash.BASIS_STM, flash.BASIS_STLINK, flash.BASIS_ESP})

    def test_erfolg(self):
        seen = self.run_page(BootSimDevice("0.1.0"))
        self.assertEqual(seen[-1], 20480)
        self.assertIn("0.2.0", self.page.status.text())
        self.assertTrue(self.page.rows[flash.BASIS_STM].b_go.isEnabled())
        self.assertTrue(self.page.bar.isHidden())

    def test_fehler_und_abbruch(self):
        self.run_page(BootSimDevice("0.1.0", copy_fail=True))
        self.assertIn("kopierfehler", self.page.status.text())
        dev = BootSimDevice("0.1.0", slow=0.01)
        self.run_page(dev, cancel_at=4096)
        self.assertIn("abgebrochen", self.page.status.text())
        self.assertIsNone(dev.commit)


class LinkImBootloader(unittest.TestCase):
    def test_kennzeile_wird_erkannt(self):
        l = link.DynoLink.__new__(link.DynoLink)
        l._bootloader = ""
        l._line("#BOOTLOADER;proto=1;bl=1.0.0\r\n")
        self.assertTrue(l.bootloader.startswith("#BOOTLOADER"))


if __name__ == "__main__":
    unittest.main()
