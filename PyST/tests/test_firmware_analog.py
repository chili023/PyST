"""
Analogeingaenge (PST-Diag 1 §11): die echte Ausgabe der Firmware (Host-Implementierung, ohne Hardware) durch den
Parser von PyST. Gebaut werden der Kern des Mega-Sketches 3.2.0 (test/host_session.cpp, Reihenfolge wie loop()) und
das Protokoll der F446-Firmware 1.2.0 (test/test_protocol.c, Modus "ai"). Ihre Ausgabe laeuft ueber eine
nachgebildete serielle Schnittstelle in link.DynoLink: '#CAP' mit 'ai=', '#OK;AI;10', Telegramme N und D und
dazwischen die Zeilen 'A;…', die PyST an die Telegramme haengt (Frame.ain).
Ohne C-Compiler oder ohne den Ordner firmware/ (Paket ohne Repo) werden die Tests uebersprungen.
"""
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyst import analog, link  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MEGA_DIR = os.path.join(REPO, "firmware", "arduino-mega", "PST_V3.2.0")
F446_DIR = os.path.join(REPO, "firmware", "stm32")
CXX = shutil.which("g++") or shutil.which("c++")
CC = shutil.which("cc") or shutil.which("gcc")


def _baue_und_starte(cmd, cwd, exe, args):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, timeout=120)
    return subprocess.run([exe] + args, check=True, capture_output=True, timeout=60).stdout


def mega_ausgabe(tmp: str) -> bytes:
    exe = os.path.join(tmp, "host_session")
    return _baue_und_starte([CXX, "-std=c++11", "-O1", "-o", exe, "test/host_session.cpp", "pst_core.cpp"],
                            MEGA_DIR, exe, [])


def f446_ausgabe(tmp: str) -> bytes:
    exe = os.path.join(tmp, "pst_tp")
    src = ["src/protocol.c", "src/diag.c", "src/settings.c", "src/burst.c", "src/fmt.c", "test/test_protocol.c"]
    return _baue_und_starte([CC, "-std=c11", "-Iinclude", "-Itest/stub"] + src + ["-lm", "-o", exe], F446_DIR, exe, ["ai"])


class FakeSerial:
    """Liefert die Bytes der Firmware stueckweise, wie ein serieller Port; Geschriebenes wird nur gesammelt."""

    def __init__(self, data: bytes):
        self.data, self.pos, self.written = data, 0, []
        self.lock = threading.Lock()

    @property
    def in_waiting(self):
        return min(64, len(self.data) - self.pos)

    def read(self, n=1):
        with self.lock:
            chunk = self.data[self.pos:self.pos + max(1, min(n, 64))]
            self.pos += len(chunk)
        if not chunk:
            time.sleep(0.01)
        return chunk

    def write(self, b):
        self.written.append(bytes(b))
        return len(b)

    def close(self):
        pass

    def fertig(self):
        return self.pos >= len(self.data)


def durch_pyst(data: bytes):
    """Ausgabe der Firmware -> DynoLink (Lesethread wie am echten Port). Liefert (Link, Telegramme, #-Zeilen)."""
    fake = FakeSerial(data)
    with mock.patch.object(link.serial, "Serial", lambda *a, **k: fake):
        L = link.DynoLink("/dev/ttyFIRMWARE")
    end = time.time() + 10
    while not fake.fertig() and time.time() < end:
        time.sleep(0.01)
    time.sleep(0.1)
    frames = L.drain_frames()
    replies = []
    while not L.replies.empty():
        replies.append(L.replies.get_nowait())
    L._stop = True
    return L, frames, replies


@unittest.skipUnless(CXX and os.path.isfile(os.path.join(MEGA_DIR, "pst_core.cpp")), "kein C++-Compiler oder kein firmware/")
class Mega(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.data = mega_ausgabe(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_zeilen_einzeln(self):
        zeilen = self.data.decode("latin-1").splitlines()
        caps = link.parse_caps(zeilen[0])
        self.assertEqual(caps["fw"], "PST-Mega 3.2.0")
        self.assertEqual(analog.caps_eingaenge(caps), [f"A{i}" for i in range(16)])
        self.assertEqual([e.id for e in analog.GERAETE[analog.MEGA].eingaenge], analog.caps_eingaenge(caps))
        self.assertEqual(zeilen[1], "#OK;AI;10")
        a = [link.parse_line(z, 0.0) for z in zeilen if z.startswith("A;")]
        self.assertEqual(len(a), 25)                               # 2,5 s mit 10 Hz, dann #AI 0
        self.assertTrue(all(k == "A" for k, _ in a))
        t_ms, werte = a[0][1]
        self.assertEqual(list(werte), [f"A{i}" for i in range(16)])
        for i in range(16):                                        # Eingang k liegt auf 1023 - 60 k
            self.assertAlmostEqual(werte[f"A{i}"], (1023 - 60 * i) * 5.0 / 1024, delta=0.0006)
        # Abstand 100 ms, auch ueber den Ueberlauf von millis()
        ts = [int(x[1][0]) for x in a]
        self.assertTrue(all((b - c) % (1 << 32) == 100 for c, b in zip(ts, ts[1:])))
        self.assertIn(0, ts)
        # Telegramme werden weiter als Telegramme erkannt
        arten = {link.parse_line(z, 0.0)[0] for z in zeilen if z and not z.startswith(("#", "A;"))}
        self.assertEqual(arten, {"F", "D"})

    def test_durch_dynolink(self):
        L, frames, replies = durch_pyst(self.data)
        self.assertEqual(L.bad_lines, 0)
        self.assertTrue(any(r.startswith("#CAP;") and "ai=A0," in r for r in replies))
        self.assertIn("#OK;AI;10", replies)
        self.assertIn("#OK;AI;0", replies)
        n_tel = sum(1 for z in self.data.decode("latin-1").splitlines() if z and not z.startswith(("#", "A;")))
        self.assertEqual(n_tel, 188)                               # 'm' bis Ende: 3 s, alle 16 ms
        self.assertEqual(len(frames), n_tel)                       # jedes Telegramm kommt an
        self.assertEqual({f.diag is not None for f in frames}, {False, True})
        mit = [f for f in frames if f.ain]
        self.assertGreater(len(mit), 140)                          # alle nach der ersten A-Zeile
        self.assertEqual(len(mit[-1].ain), 16)
        self.assertAlmostEqual(mit[-1].ain["A0"], 4.995)
        self.assertAlmostEqual(mit[-1].ain["A15"], 0.601)
        self.assertTrue(all(math.isfinite(v) for v in L.analog.values()))
        # Spalten fuer roh.csv
        sp = analog.spalten(frames)
        self.assertIn("analog_A7", sp)
        self.assertEqual(len(sp["analog_A7"]), len(frames))


@unittest.skipUnless(CC and os.path.isfile(os.path.join(F446_DIR, "src", "diag.c")), "kein C-Compiler oder kein firmware/")
class F446(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.data = f446_ausgabe(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_zeilen_einzeln(self):
        zeilen = self.data.decode("latin-1").splitlines()
        caps = link.parse_caps(zeilen[0])
        self.assertEqual(caps["fw"], "PST-STM32 1.2.0")
        self.assertEqual(analog.caps_eingaenge(caps), ["ADC1", "ADC2"])
        self.assertEqual([e.id for e in analog.GERAETE[analog.F446].eingaenge], analog.caps_eingaenge(caps))
        self.assertEqual(zeilen[1], "#OK;AI;10")
        a = [link.parse_line(z, 0.0)[1] for z in zeilen if z.startswith("A;")]
        self.assertEqual(len(a), 20)
        self.assertEqual(a[0][1], {"ADC1": 1.65, "ADC2": 0.825})   # Attrappe: Rohwert 2048 / 1024
        ts = [int(x[0]) for x in a]
        self.assertTrue(all((b - c) % (1 << 32) == 100 for c, b in zip(ts, ts[1:])))
        # Telegramme N (nur '\n') und D unveraendert erkannt
        arten = [link.parse_line(z, 0.0)[0] for z in zeilen if z and not z.startswith(("#", "A;"))]
        self.assertEqual(set(arten), {"F", "D"})
        self.assertEqual(len(arten), 150)

    def test_durch_dynolink(self):
        L, frames, replies = durch_pyst(self.data)
        self.assertEqual(L.bad_lines, 0)
        self.assertIn("#OK;AI;10", replies)
        self.assertIn("#OK;AI;0", replies)
        self.assertEqual(len(frames), 150)
        mit = [f for f in frames if f.ain]
        self.assertGreater(len(mit), 130)
        self.assertEqual(mit[-1].ain, {"ADC1": 1.65, "ADC2": 0.825})


class Schliessen(unittest.TestCase):
    def test_close_schaltet_ai_aus(self):
        """F446 startet beim Oeffnen des Ports nicht neu: PyST sendet beim Trennen '#AI 0'."""
        L = link.DynoLink("SIM")
        try:
            time.sleep(0.2)
            L.probe_caps()
            self.assertTrue(L.analog_ein(10))
            self.assertEqual(L.ai_hz, 10)
            gesendet = []
            alt = L.send
            L.send = lambda c: (gesendet.append(c), alt(c))
        finally:
            L.close()
        self.assertIn("#AI 0", gesendet)
        self.assertEqual(L.ai_hz, 0)

    def test_close_ohne_ai_sendet_nichts_neues(self):
        L = link.DynoLink("SIM")
        try:
            time.sleep(0.2)
            L.probe_caps()
            gesendet = []
            alt = L.send
            L.send = lambda c: (gesendet.append(c), alt(c))
        finally:
            L.close()
        self.assertNotIn("#AI 0", gesendet)

    def test_sim_ai_streng_wie_firmware(self):
        L = link.DynoLink("SIM:mega")
        try:
            time.sleep(0.2)
            L.probe_caps()
            self.assertEqual(L.command("#AI 2.5", timeout=0.5)[-1:], ["#ERR;AI;bereich 0-50"])
            self.assertEqual(L.command("#AI", timeout=0.5)[-1:], ["#ERR;AI;bereich 0-50"])
            self.assertEqual(L.command("#AI 51", timeout=0.5)[-1:], ["#ERR;AI;bereich 0-50"])
            self.assertEqual(L.command("#AI 50", timeout=0.5)[-1:], ["#OK;AI;50"])
            self.assertEqual(L.command("#AI 0", timeout=0.5)[-1:], ["#OK;AI;0"])
        finally:
            L.close()


if __name__ == "__main__":
    unittest.main()
