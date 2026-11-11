"""Die Tests duerfen angesteckte Geraete weder finden noch oeffnen (Sperre in conftest.py)."""
import unittest

import serial
from serial.tools import list_ports

from pyst import flash, link


class EchtePorts(unittest.TestCase):
    def test_keine_ports_sichtbar(self):
        self.assertEqual(list(list_ports.comports()), [])
        self.assertEqual(link.pst_board_ports(), [])
        self.assertEqual(flash.mega_ports(), [])

    def test_echter_port_laesst_sich_nicht_oeffnen(self):
        for port in ("/dev/ttyACM0", "/dev/ttyUSB0", "COM3"):
            with self.assertRaises(serial.SerialException):
                serial.Serial(port, 115200)


if __name__ == "__main__":
    unittest.main()
