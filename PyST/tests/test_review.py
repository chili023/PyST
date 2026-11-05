"""Punkte aus dem Code-Review: Median, CSV-Laden, Cache, Einstellungen, .pyst, Recorder (M11-M15, 2.4, 5.4)."""
import csv
import io
import json
import os
import queue
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyst import channels, link, physics, storage  # noqa: E402


def median_alt(x, half):
    """Die Fassung vor der Vektorisierung, je Sample ein np.median."""
    if half <= 0 or len(x) == 0:
        return x.copy()
    out = np.empty(len(x))
    for j in range(len(x)):
        out[j] = np.median(x[max(0, j - half): j + half + 1])
    return out


def assert_bitgleich(a, b):
    np.testing.assert_array_equal(np.isnan(a), np.isnan(b))
    ok = ~np.isnan(a)
    assert a[ok].tobytes() == b[ok].tobytes()


class Median(unittest.TestCase):
    def test_bitgleich_zur_alten_fassung(self):
        rng = np.random.default_rng(1)
        for n in (0, 1, 2, 3, 4, 5, 6, 7, 10, 11, 50, 1000):
            for half in (0, 1, 2, 3, 5, 7, 12, 40):
                x = rng.normal(5000, 300, n)
                assert_bitgleich(physics.median_centered(x, half), median_alt(x, half))
                if n:
                    y = x.copy()
                    y[rng.integers(0, n, max(1, n // 20))] = np.nan
                    assert_bitgleich(physics.median_centered(y, half), median_alt(y, half))
                    z = x.copy()
                    z[0] = np.inf
                    z[-1] = -np.inf
                    assert_bitgleich(physics.median_centered(z, half), median_alt(z, half))

    def test_gerade_zahl_am_rand_und_ganzzahlen(self):
        x = np.array([1, 2, 4, 8, 16, 32])
        assert_bitgleich(physics.median_centered(x, 2), median_alt(x, 2))
        self.assertEqual(physics.median_centered(x, 2).dtype, np.float64)
        self.assertEqual(physics.median_centered(x, 0).dtype, x.dtype)     # wie zuvor: nur eine Kopie

    def test_blockgrenze(self):
        x = np.random.default_rng(2).normal(0, 1, 5000)                     # Fenster 2001: mehrere Bloecke
        assert_bitgleich(physics.median_centered(x, 1000), median_alt(x, 1000))

    def test_rolling_median_ist_kein_ersatz(self):
        """Der Review-Vorschlag: ohne nan gleich, mit nan nicht. Deshalb bleibt median_centered eigenstaendig."""
        x = np.random.default_rng(3).normal(100, 5, 200)
        assert_bitgleich(channels.rolling_median(x, 3), median_alt(x, 3))
        x[50] = np.nan
        self.assertTrue(np.isnan(median_alt(x, 3)[50]))
        self.assertFalse(np.isnan(channels.rolling_median(x, 3)[50]))

    def test_tempo(self):
        x = np.random.default_rng(4).normal(5000, 100, 180_000)            # 1 h bei 50 Hz
        t0 = time.time()
        physics.median_centered(x, 12)
        self.assertLess(time.time() - t0, 2.0)


class CsvLaden(unittest.TestCase):
    def slow(self, text):
        return storage._read_csv_slow(text)

    def assert_gleich(self, text):
        a, b = storage._read_csv_cols(text), self.slow(text)
        self.assertEqual(list(a), list(b))
        for k in a:
            assert_bitgleich(a[k], b[k])

    def test_zahlen_bitgleich(self):
        rng = np.random.default_rng(5)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["a", "b", "c", "d"])
        for i in range(500):
            w.writerow([i, f"{rng.normal():.4f}", rng.normal() * 1e5, "nan"])
        self.assert_gleich(buf.getvalue())
        self.assert_gleich(buf.getvalue().replace("\n", "\r\n").replace("\r\r\n", "\r\n"))

    def test_sonderfaelle(self):
        for text in ("a;b\n1;2\n", "a;b\n", "", "a\n1\n2\n", "a;b\n1;\n;2\n", "a;b\n1;x\n", "a;b\n1\n2;3\n",
                     "a;b\n1;2;3\n", "a;a\n1;2\n", "a;b\n\n1;2\n\n3;4\n", "a;b\n1.5e3;-inf\n", 'a;b\n"1";2\n'):
            with self.subTest(text=text):
                self.assert_gleich(text)


if __name__ == "__main__":
    unittest.main()
