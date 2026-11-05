"""Lesethread und Antwortzuordnung von link.DynoLink gegen kaputte und fremde Zeilen (Code-Review H2, H3)."""
import logging
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyst import link  # noqa: E402

KAPUTT = [
    "#BURST;BEGIN;n=?;tick_ns=62,5;trig_idx=x;ch=I",
    "#BURST;BEGIN;n=inf;tick_ns=nan;trig_idx=1e999",
    "#BURST;BEGIN;tick_ns=-1",
    "#BURST;BEGIN",
    "B;1;I;x;y",
    "B;;;;",
    "B;1;I;1;nan",
    "#BURST;END;lost=?",
    "#BURST;END;lost=inf",
    "#BURST;END",
    "D;x;y;z;1;2",
    "D;1;nan;inf;nan;inf",
    "D;1;inf;1;2;3;;;;;;;;;;;;zz;nan;nan",
    "D;" + "9" * 3000,
    "1e999;1;2;3",
    "inf;1;2;3",
    "nan;nan;nan;nan",
    "E;inf;MARK;zange",
    "E;nan;MARK",
    "E;;",
    "L;x;F;1",
    "L;1;F;inf",
    "L;1;F;nan;a;b",
    "#ZANGE;id=inf;akku_mv=x;funken=inf;blitz=nan;rssi=?",
    "#ZANGE;",
    "#NET;kanal=inf",
    "#DOSE;nr=inf",
    "#PAIR;id=inf",
    "#",
    ";;;;;;;;",
    "\x00\xff\xfe;\x80",
]


def wait_until(cond, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


class Lesethread(unittest.TestCase):
    def setUp(self):
        self.l = link.DynoLink(link.SIM_PORT)
        self.l.info()

    def tearDown(self):
        self.l.close()

    def assert_lebt(self):
        self.assertTrue(self.l._thread.is_alive())
        self.assertEqual(self.l.probe_caps().get("proto"), "1")
        self.l.start()
        self.l.drain_frames()
        self.assertTrue(wait_until(lambda: self.l.frames.qsize() > 2), "keine Telegramme mehr")

    def test_kaputte_zeilen(self):
        for line in KAPUTT:
            self.l.ser._out(line + "\r\n")
        self.assertTrue(wait_until(lambda: not self.l.ser.in_waiting))
        self.assert_lebt()
        self.assertEqual(self.l.bad_lines, 0, "die Parser sollen schon selbst tolerant sein")

    def test_fuzz(self):
        rng = random.Random(4711)
        vorlagen = KAPUTT + ["#BURST;BEGIN;n=10;tick_ns=62.5;trig_idx=2;ch=I", "#BURST;END;lost=0", "D;1;2;3;4;5",
                             "#OK;RUN;1", "#ERR;?;unbekannt", "E;1;MARK;zange;T1", "L;1;F;2;0;0;100", "B;0;I;1;5"]
        zeichen = "#;=,.-+eEnaif0123456789 BDELZ\t\x00\xff"
        for _ in range(3000):
            line = rng.choice(vorlagen)
            art = rng.randrange(4)
            if art == 1 and line:                                 # ein Zeichen austauschen
                i = rng.randrange(len(line))
                line = line[:i] + rng.choice(zeichen) + line[i + 1:]
            elif art == 2 and line:                               # abschneiden
                line = line[:rng.randrange(len(line))]
            elif art == 3:                                        # reiner Muell
                line = "".join(rng.choice(zeichen) for _ in range(rng.randrange(40)))
            self.l.ser._out(line + rng.choice(["\r\n", "\n", "\r"]))
        self.assertTrue(wait_until(lambda: not self.l.ser.in_waiting, 10.0))
        self.assert_lebt()

    def test_ausnahme_toetet_den_thread_nicht(self):
        """Auch ein Fehler, den kein Parser abfaengt, kostet nur die eine Zeile."""
        echt = link.parse_line

        def wirft(line, t):
            if "BOESE" in line:
                raise RuntimeError("unerwartet")
            return echt(line, t)

        link.parse_line = wirft
        self.addCleanup(setattr, link, "parse_line", echt)
        with self.assertLogs("pyst.link", logging.WARNING) as cm:
            self.l.ser._out("BOESE\r\n")
            self.assertTrue(wait_until(lambda: self.l.bad_lines == 1))
        self.assertIn("RuntimeError", cm.output[0])
        self.assert_lebt()

    def test_logzeilen_gedeckelt(self):
        with self.assertLogs("pyst.link", logging.WARNING) as cm:
            for _ in range(link.LOG_BAD_LINES + 5):
                self.l._safe_line(None)                           # wirft in _line()
        self.assertEqual(self.l.bad_lines, link.LOG_BAD_LINES + 5)
        self.assertEqual(len(cm.output), link.LOG_BAD_LINES)

    def test_burst_kopf_tolerant(self):
        self.l.caps["tick_ns"] = "6.25"
        self.l.ser._out("#BURST;BEGIN;n=?;tick_ns=62,5;trig_idx=x;ch=I\r\nB;0;I;1;5\r\n#BURST;END;lost=?\r\n")
        b = self.l.bursts.get(timeout=2)
        self.assertEqual((b.n, b.tick_ns, b.trig_idx, b.lost, len(b.edges), b.complete), (0, 6.25, -1, 0, 1, True))


class Zahlen(unittest.TestCase):
    def test_nicht_endliche_telegramme(self):
        self.assertIsNone(link.parse_frame("inf;1;2;3", 0.0))
        self.assertIsNone(link.parse_frame("1;nan;2;3", 0.0))
        self.assertIsNotNone(link.parse_frame("1;2;3;4;nan;nan;nan", 0.0))      # Sensoren duerfen fehlen
        self.assertIsNone(link.parse_diag("D;1;2;nan;4;5", 0.0))
        self.assertIsNotNone(link.parse_diag("D;1;nan;3;4;5", 0.0))


if __name__ == "__main__":
    unittest.main()
