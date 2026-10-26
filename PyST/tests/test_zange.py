"""Tests PyST-Basis / Funk-Zange / Zange per USB / WLAN-Steckdosen (docs/04_PST_Diag_1.md §10).

    python -m pytest tests/test_zange.py

Steckdosen werden gegen lokale HTTP-Testserver (pyst.plugs.PlugSimDevice) mit den Beispielantworten von
Shelly Gen2/Gen1, Tasmota und ESPHome geprueft. Mit PYST_SCREENSHOTS=<Ordner> legt der Oberflaechentest
Bildschirmfotos ab.
"""
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyst import basis, channels, link, mdns, physics, plugs, storage, zange  # noqa: E402
from pyst.recorder import Recorder  # noqa: E402
from pyst.sim import BasisSimSerial  # noqa: E402

D_BASIS = "D;12;4294966914;60.00;49.991;727.981;nan;0.00;nan;1;20104.7;20104.7;0;12;1366.1;1373.7;0;10;3955;1"


class FakeLink(basis.BasisCommands):
    def __init__(self, caps):
        self.caps = caps


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------------------------------------- Parser
class Parser(unittest.TestCase):
    def test_caps(self):
        caps = link.parse_caps("#CAP;proto=1;fw=PyST-Basis 0.1.0;tick_ns=6.25;ch=I,R;burst_max=4096;modes=N,D;"
                               "set=1;hw=rev31;ign=zange")
        f = FakeLink(caps)
        self.assertTrue(f.is_basis)
        self.assertEqual(f.ign_source, "zange")
        self.assertFalse(f.is_zange_usb)
        self.assertEqual(f.channels, "I,R")
        f = FakeLink(link.parse_caps("#CAP;proto=1;fw=PyST-Basis 0.1.0;tick_ns=6.25;ch=R;burst_max=4096;"
                                     "modes=N,D;set=1;hw=rev31;ign=none"))
        self.assertEqual((f.ign_source, f.channels), ("none", "R"))
        f = FakeLink(link.parse_caps("#CAP;proto=1;fw=X 1;tick_ns=1000;ch=I;burst_max=2000;modes=N,D;set=1"))
        self.assertTrue(f.is_zange_usb)
        self.assertFalse(f.is_basis)
        f = FakeLink(link.parse_caps("#CAP;proto=1;fw=PST-Mega 3.1.0;tick_ns=62.5;ch=I,R;burst_max=800;modes=N,D;set=1"))
        self.assertFalse(f.is_basis or f.is_zange_usb)
        self.assertEqual(f.ign_source, "")

    def test_diag_appended(self):
        fr = link.parse_diag(D_BASIS, 1.0)
        self.assertEqual(fr.diag["ign_peak_mv"], 3955.0)
        self.assertEqual(fr.diag["ign_src"], 1.0)
        self.assertEqual(fr.diag["flags"], 0x10)
        fr = link.parse_diag(D_BASIS + ";99;weiteres", 1.0)            # unbekannte Felder am Ende
        self.assertEqual(fr.diag["ign_peak_mv"], 3955.0)
        fr = link.parse_diag(D_BASIS.rsplit(";", 2)[0], 1.0)            # ohne Zusatzfelder (Mega/F446)
        self.assertNotIn("ign_peak_mv", fr.diag)
        fr = link.parse_diag(D_BASIS.rsplit(";", 1)[0], 1.0)            # nur ign_peak_mv
        self.assertTrue(np.isnan(fr.diag["ign_src"]))

    def test_line_kinds(self):
        k = lambda line: (link.parse_line(line, 0) or (None,))[0]
        self.assertEqual(k("E;1234;MARK;zange;T1"), "E")
        self.assertEqual(k("L;1;F;5000000;0;0;3900"), "L")
        self.assertEqual(k("E;kaputt"), "T")
        self.assertEqual(k("L;x;F;1"), "T")
        self.assertEqual(k("#ZANGE;id=1"), "#")
        self.assertEqual(k(D_BASIS), "D")

    def test_events(self):
        e = basis.parse_event("E;1234;MARK;zange;T1")
        self.assertEqual((e.master_ms, e.kind, e.args), (1234.0, "MARK", ["zange", "T1"]))
        self.assertTrue(e.is_marker)
        self.assertEqual(e.text, "Marker Zange: T1")
        self.assertEqual(basis.parse_event("E;5;ZANGE;verloren").text, "Zange verloren")
        self.assertEqual(basis.parse_event("E;5;DOSE;2;1").text, "Steckdose 2 an")
        self.assertEqual(basis.parse_event("E;5;DOSE;2;aus").text, "Steckdose 2 aus")
        self.assertEqual(basis.parse_event("E;5;NEU;x;y").text, "NEU x y")
        self.assertIsNone(basis.parse_event("E;abc;MARK"))

    def test_zange(self):
        st = basis.parse_zange("#ZANGE;id=1;mac=34:85:18:a1:b2:c3;akku_mv=4082;akku_pct=87;vbus=1;rssi=-61;"
                               "funken=12345;log=52023/452000;blitz=1;phi=12.5;fw=0.1.0;sync_us=180")
        self.assertEqual((st.id, st.akku_pct, st.rssi, st.funken, st.blitz, st.phi), (1, 87, -61, 12345, 1, 12.5))
        self.assertTrue(st.vbus and st.connected and st.sync_ok)
        self.assertIn("Blitz φ 12.5°", st.summary())
        st = basis.parse_zange("#ZANGE;id=1;sync_us=-1;verbunden=0")
        self.assertFalse(st.connected or st.sync_ok)
        st = basis.parse_zange("#ZANGE;id=1;blitz_phi_x10=-35")
        self.assertEqual(st.phi, -3.5)
        self.assertIsNone(basis.parse_zange("#NET;mode=AP"))

    def test_net_dose_pair(self):
        n = basis.parse_net("#NET;mode=sta;kanal=11;ssid=Werkstatt;ip=192.168.178.57;rssi=-63;tcp=1;err=0")
        self.assertEqual((n.mode, n.channel, n.ssid, n.ip, n.rssi, n.tcp), ("STA", 11, "Werkstatt", "192.168.178.57",
                                                                            -63, True))
        self.assertIn("Werkstatt", n.summary())
        d = basis.parse_dose("#DOSE;nr=2;typ=shelly1;host=192.168.4.21;an=1;p_mw=650500;erreichbar=1;rest_s=40;"
                             "ausfall=an;fehler=0")
        self.assertEqual((d.nr, d.typ, d.on, d.power_w, d.rest_s, d.fail_on), (2, "SHELLY1", True, 650.5, 40, True))
        d = basis.parse_dose("#DOSE;nr=1;type=TASMOTA;ip=10.0.0.5;on=0;p_w=0;reachable=0")
        self.assertEqual((d.typ, d.host, d.on, d.power_w, d.reachable, d.state_text()),
                         ("TASMOTA", "10.0.0.5", False, 0.0, False, "nicht erreichbar"))
        d = basis.parse_dose("#DOSE;SCAN;nr=0;typ=SHELLY2;host=shellyplugsg3-a1.local;ip=192.168.4.30")
        self.assertEqual((d.nr, d.host), (0, "shellyplugsg3-a1.local"))
        self.assertTrue(np.isnan(basis.parse_dose("#DOSE;nr=3;an=1").power_w))
        p = basis.parse_pair("#PAIR;id=1;mac=aa;typ=zange;sn=Z-1;fw=0.1.0;verbunden=1")
        self.assertEqual((p.id, p.typ, p.connected), (1, "zange", True))
        self.assertIsNone(basis.parse_pair("#PAIR;mac=aa"))

    def test_log(self):
        e = basis.parse_log_line("L;1001;F;5000123;0;1;3900")
        self.assertEqual((e.idx, e.art, e.local_us, e.num(2)), (1001, "F", 5000123, 3900))
        self.assertEqual(basis.parse_log_line("L;1;funke;5;0;0;1").art, "F")
        self.assertEqual(basis.parse_log_line("L;1;2;5;1").art, "M")
        self.assertEqual(basis.parse_log_line("L;1;SYNC;5;123").art, "Y")
        self.assertEqual(basis.parse_log_line("L;1;X;5").art, "X")      # unbekannt bleibt erhalten
        self.assertIsNone(basis.parse_log_line("L;a;F;5"))
        self.assertEqual(basis.parse_log_info(["#LOG;head=200;tail=100"]), {"head": 200, "tail": 100, "n": 100})
        self.assertEqual(basis.parse_log_info(["#OK;LOG;INFO;head=5;tail=1;n=4"])["n"], 4)
        self.assertEqual(basis.parse_log_info(["#ERR;LOG;x"]), {})

    def test_net_commands(self):
        self.assertEqual(basis.net_commands("AP", "PyST", "geheim123", 6), "#NET AP 6 PyST geheim123")
        self.assertEqual(basis.net_commands("sta", "Werkstatt", "pw"), "#NET STA Werkstatt pw")
        self.assertEqual(basis.net_commands("OFF"), "#NET OFF")
        for args in (("AP", "mit Leer", "12345678"), ("AP", "x", "kurz"), ("STA", "", "x"),
                     ("STA", "s" * 32, "p" * 30), ("XX",)):
            with self.assertRaises(ValueError):
                basis.net_commands(*args)

    def test_tcp_port(self):
        self.assertEqual(link.parse_tcp_port("tcp://192.168.4.1:2323"), ("192.168.4.1", 2323))
        self.assertEqual(link.parse_tcp_port("tcp://pyst-basis.local"), ("pyst-basis.local", 2323))
        self.assertEqual(link.parse_tcp_port("tcp://[::1]:7000"), ("::1", 7000))
        self.assertTrue(link.is_tcp_port("TCP://x"))
        names = [p for p, _d in link.list_serial_ports()]
        self.assertIn(link.TCP_DEFAULT, names)
        self.assertIn("SIM:basis", names)
        self.assertIn("SIM:zange", names)


# ---------------------------------------------------------------------------------------------- mDNS
class Mdns(unittest.TestCase):
    def test_query_and_response(self):
        q = mdns.build_query([("_shelly._tcp.local", mdns.T_PTR)])
        self.assertEqual(struct.unpack("!H", q[4:6])[0], 1)
        self.assertIn(b"\x07_shelly\x04_tcp\x05local\x00", q)
        # Antwort: PTR -> Instanz, SRV -> Host:80, TXT gen=2, A -> IP (mit Namenskompression)
        name = mdns._encode_name
        inst = name("shellyplugsg3-a1._shelly._tcp.local")
        ans = struct.pack("!HHHHHH", 0, 0x8400, 0, 4, 0, 0)
        ptr_rd = inst
        ans += name("_shelly._tcp.local") + struct.pack("!HHIH", 12, 1, 120, len(ptr_rd)) + ptr_rd
        srv_rd = struct.pack("!HHH", 0, 0, 80) + name("shellyplugsg3-a1.local")
        ans += inst + struct.pack("!HHIH", 33, 0x8001, 120, len(srv_rd)) + srv_rd
        txt_rd = b"\x05gen=2\x08app=Plug"
        ans += inst + struct.pack("!HHIH", 16, 0x8001, 120, len(txt_rd)) + txt_rd
        ans += name("shellyplugsg3-a1.local") + struct.pack("!HHIH", 1, 0x8001, 120, 4) + socket.inet_aton("192.168.4.30")
        recs = mdns.parse_response(ans)
        self.assertEqual(len(recs), 4)
        found = mdns.collect(recs, "_shelly._tcp.local")
        self.assertEqual(found[0]["name"], "shellyplugsg3-a1")
        self.assertEqual((found[0]["ip"], found[0]["port"], found[0]["txt"]["gen"]), ("192.168.4.30", 80, "2"))
        # Kompressionszeiger
        pkt = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
        base = len(pkt)
        pkt += name("pyst-basis.local") + struct.pack("!HHIH", 1, 1, 120, 4) + socket.inet_aton("10.0.0.7")
        pkt2 = struct.pack("!HHHHHH", 0, 0x8400, 0, 2, 0, 0) + pkt[12:] + \
            struct.pack("!H", 0xC000 | base) + struct.pack("!HHIH", 1, 1, 120, 4) + socket.inet_aton("10.0.0.8")
        recs = mdns.parse_response(pkt2)
        self.assertEqual([r[0] for r in recs], ["pyst-basis.local", "pyst-basis.local"])
        self.assertEqual(recs[1][2], "10.0.0.8")

    def test_resolve_ip(self):
        self.assertEqual(mdns.resolve("127.0.0.1"), "127.0.0.1")


# ---------------------------------------------------------------------------------------------- Simulator
class BasisSim(unittest.TestCase):
    def setUp(self):
        self.l = link.DynoLink("SIM:basis")
        self.l.info()

    def tearDown(self):
        self.l.close()

    def test_caps_and_telegram(self):
        l = self.l
        self.assertEqual((l.ign_source, l.default_mode, l.caps["hw"], l.caps["tick_ns"]), ("zange", "D", "rev31", "6.25"))
        self.assertTrue(l.set_mode("D"))
        l.start()
        self.assertTrue(wait_until(lambda: l.frames.qsize() > 20))
        fr = l.drain_frames()
        self.assertTrue(all("ign_peak_mv" in f.diag for f in fr))
        self.assertTrue(any(f.diag["ign_peak_mv"] > 3000 and f.diag["ign_src"] == 1 for f in fr))
        self.assertGreater(fr[-1].ign_hz, 30)

    def test_zange_commands(self):
        l = self.l
        st = l.zange_status()
        self.assertTrue(st.connected and st.sync_ok)
        self.assertEqual(st.id, 1)
        self.assertEqual(l.zange_get("deadtime_us"), "300")
        self.assertTrue(l.ok(l.zange_set("blitz_phi_x10", 125)))
        self.assertEqual(l.zange_get("blitz_phi_x10"), "125")
        self.assertFalse(l.ok(l.zange_set("blitz_pulse_us", 99)))          # > 25 µs
        self.assertFalse(l.ok(l.zange_set("gibts_nicht", 1)))
        self.assertEqual(len(l.zange_get_all()), len(basis.ZANGE_KEYS))
        l.set_mode("D")
        l.start()
        wait_until(lambda: l.frames.qsize() > 5)
        self.assertTrue(l.ok(l.zange_cmd("MARK")))
        self.assertTrue(wait_until(lambda: not l.events.empty()))
        ev = l.drain_events()[0]
        self.assertEqual((ev.kind, ev.args[0]), ("MARK", "pyst"))
        self.assertLess(abs(ev.t_pc - time.time()), 1.0)                 # Geraetezeit -> PC-Zeit
        for sub in ("REC 1", "REC 0", "IDENT", "LOG"):
            self.assertTrue(l.ok(l.zange_cmd(sub)), sub)
        self.assertEqual(l.pair_list()[0].typ, "zange")
        self.assertTrue(l.ok(l.zange_cmd("OFF")))
        self.assertTrue(wait_until(lambda: any(e.text == "Zange verloren" for e in l.drain_events())))
        self.assertFalse(l.zange_status().connected)
        self.assertIsNotNone(l.zange)                                    # letzter Status bleibt gemerkt

    def test_net_and_plugs(self):
        l = self.l
        self.assertEqual(l.net_state().mode, "AP")
        self.assertTrue(l.ok(l.net_set("STA", "Werkstatt", "geheim123")))
        n = l.net_state()
        self.assertEqual((n.mode, n.ssid, n.ip), ("STA", "Werkstatt", "192.168.178.57"))
        self.assertTrue(l.ok(l.net_set("AP", "PyST-Basis", "", 11)))
        self.assertEqual(l.net_state().channel, 11)
        self.assertEqual(len(l.dose_list()), 2)
        self.assertTrue(l.ok(l.dose_add(3, "tasmota", "192.168.4.22")))
        self.assertFalse(l.ok(l.dose_add(9, "TASMOTA", "x")))
        self.assertFalse(l.ok(l.dose_add(4, "FRITZ", "x")))
        self.assertTrue(l.ok(l.dose_switch(3, True, 60)))
        d = {p.nr: p for p in l.dose_list()}[3]
        self.assertTrue(d.on)
        self.assertGreater(d.power_w, 1500)
        self.assertGreater(d.rest_s, 30)
        self.assertTrue(l.ok(l.dose_switch(3, "TOGGLE")))
        self.assertFalse({p.nr: p for p in l.dose_list()}[3].on)
        self.assertTrue(l.ok(l.dose_delete(3)))
        self.assertEqual(len(l.dose_list()), 2)
        self.assertEqual(len(l.dose_scan()), 3)
        evs = [e for e in l.drain_events() if e.kind == "DOSE"]
        self.assertEqual([e.text for e in evs], ["Steckdose 3 an", "Steckdose 3 aus"])


class BasisSimPairing(unittest.TestCase):
    def test_pair_and_faults(self):
        l = link.DynoLink("SIM:basis:ohne")
        try:
            l.info()
            self.assertEqual((l.ign_source, l.channels, l.default_mode), ("none", "R", "N"))
            self.assertFalse(l.zange_status().connected)
            l.set_mode("D")
            l.start()
            wait_until(lambda: l.frames.qsize() > 5)
            fr = l.drain_frames()[-1]
            self.assertEqual(fr.ign_hz, 0.0)
            self.assertTrue(fr.diag["flags"] & 1)                       # Zuendung: Timeout
            self.assertTrue(l.pair(True))
            self.assertTrue(wait_until(lambda: any(e.kind == "ZANGE" for e in l.drain_events()), 6.0))
            l.probe_caps()
            self.assertEqual((l.ign_source, l.channels), ("zange", "I,R"))
            # Funkstoerungen: Paketverlust -> 'stale', Zange zeitweise weg
            self.assertTrue(l.ok(l.set_setting("sim_funkverlust", "50")))
            l.drain_frames()
            wait_until(lambda: l.frames.qsize() > 40)
            self.assertTrue(any(f.diag["flags"] & 0x10 for f in l.drain_frames()))
            self.assertTrue(l.ok(l.set_setting("sim_zange_weg", "3000")))
            self.assertTrue(wait_until(lambda: any(e.text == "Zange verloren" for e in l.drain_events()), 4.0))
            self.assertTrue(l.pair_delete(1))
            l.probe_caps()
            self.assertEqual(l.ign_source, "none")
        finally:
            l.close()


class TcpBasis(unittest.TestCase):
    """Basis per WLAN: DynoLink('tcp://…') gegen einen lokalen TCP-Server, der den Basis-Simulator anbindet."""

    def test_tcp(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        sim = BasisSimSerial()
        stop = threading.Event()

        def serve():
            conn, _ = srv.accept()
            conn.settimeout(0.02)
            while not stop.is_set():
                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    sim.write(data)
                except socket.timeout:
                    pass
                out = sim.read(65536)
                if out:
                    conn.sendall(out)
            conn.close()
        th = threading.Thread(target=serve, daemon=True)
        th.start()
        l = link.DynoLink(f"tcp://127.0.0.1:{port}")
        try:
            self.assertTrue(l.is_tcp)
            t0 = time.time()
            self.assertIn("PyST-Basis", l.info())
            self.assertLess(time.time() - t0, 1.5)                       # keine Wartezeit wie beim Mega-Reset
            self.assertEqual(l.ign_source, "zange")
            l.set_mode("D")
            l.start()
            self.assertTrue(wait_until(lambda: l.frames.qsize() > 10))
            self.assertIn("ign_peak_mv", l.drain_frames()[-1].diag)
            self.assertTrue(l.zange_status().connected)
            self.assertTrue(l.alive)
            stop.set()
            th.join(2)
            self.assertTrue(wait_until(lambda: not l.alive, 3.0))        # Abriss wird erkannt
        finally:
            stop.set()
            l.close()
            sim.close()
            srv.close()

    def test_tcp_refused(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(OSError):
            link.DynoLink(f"tcp://127.0.0.1:{port}")


# ---------------------------------------------------------------------------------------------- Zange per USB
class ZangeUsb(unittest.TestCase):
    def test_usb_and_log_to_pyst(self):
        l = link.DynoLink("SIM:zange")
        try:
            self.assertIn("PyST-Zange", l.info())
            self.assertTrue(l.is_zange_usb)
            self.assertEqual((l.channels, l.caps["tick_ns"], l.default_mode), ("I", "1000", "N"))
            self.assertIn("imp_per_rev_x10", dict(l.get_settings()))
            l.set_mode("D")
            l.start()
            self.assertTrue(wait_until(lambda: l.frames.qsize() > 20))
            fr = l.drain_frames()
            self.assertTrue(all(f.roll_hz == 0 for f in fr))
            self.assertTrue(any(f.ign_hz > 20 for f in fr))
            ok = l.request_burst("I", 20, "F")
            self.assertTrue(l.ok(ok))
            b = l.bursts.get(timeout=5)
            self.assertEqual((b.tick_ns, len(b.edges)), (1000.0, 20))
            l.ensure_stopped()
            info = l.log_info()
            self.assertGreater(info["n"], 1000)
            seen = []
            entries = l.log_read_all(block=1500, progress=lambda a, b: seen.append(a))
            self.assertEqual(len(entries), info["n"])
            self.assertEqual([e.idx for e in entries], list(range(info["tail"], info["head"])))
            self.assertGreater(len(seen), 1)
            settings = dict(l.get_settings())
        finally:
            l.close()
        with tempfile.TemporaryDirectory() as tmp:
            folder = zange.write_log_folder(entries, os.path.join(tmp, "z"), physics.DynoParams(imp=2.0),
                                            firmware="PyST-Zange 0.1.0 SIM", settings=settings)
            run = storage.load_any(folder)
            self.assertEqual(run["kind"], "dauermessung")
            self.assertEqual(run["params"].imp, 1.0)                     # aus imp_per_rev_x10 = 10
            self.assertEqual(len(run["markers"]), 2)
            self.assertEqual(run["markers"][0][1], "Taste T1")
            self.assertAlmostEqual(run["markers"][0][0], 12.0, delta=0.1)
            n = run["n_meas"]
            self.assertGreater(np.nanmax(n), 10000)
            self.assertLess(np.nanmax(n), 12500)
            self.assertTrue(np.all(run["n_roll"] == 0))
            ch = channels.run_channels(run, None, run["params"])
            zs = ch["Zündstärke"].values
            self.assertGreater(np.nanmedian(zs), 3000)
            self.assertEqual(ch["Zündstärke"].group, "Zange")
            self.assertIn("zange_akku_pct", ch)                         # Zusatzspalte
            self.assertTrue(os.path.exists(os.path.join(folder, "zange_log.csv")))
            meta = run["meta"]["zange_log"]
            self.assertEqual(meta["funken"] + meta["verworfen"], sum(e.art == "F" for e in entries))
            path = storage.export_pyst(os.path.join(tmp, "zange"), [{"ordner": folder, "name": "Zange-Log",
                                                                     "art": "dauermessung"}])
            back = storage.load_pyst(path)[0]
            self.assertEqual(len(back["n_meas"]), len(run["n_meas"]))
            self.assertIn("diag_ign_peak_mv", back["columns"])

    def test_log_gap_and_timeout(self):
        # zwei Aufnahmen mit 1 h Pause: keine Stunde leerer Telegramme
        lines = [f"L;{i};F;{1_000_000 + i * 10_000};0;0;4000" for i in range(100)]
        lines += [f"L;{100 + i};F;{3_601_000_000 + i * 20_000};0;0;3000" for i in range(50)]
        res = zange.log_to_rows([basis.parse_log_line(x) for x in lines], physics.DynoParams())
        self.assertLess(len(res["rows"]), 200)
        hz = np.array([float(r[3]) for r in res["rows"]])
        self.assertAlmostEqual(float(np.median(hz[:40])), 100.0, delta=1)
        self.assertIn(0.0, hz)                                          # Timeout nach der ersten Aufnahme
        self.assertAlmostEqual(float(np.median(hz[-20:])), 50.0, delta=1)
        with self.assertRaises(ValueError):
            zange.log_to_rows([], physics.DynoParams())


# ---------------------------------------------------------------------------------------------- Speichern
class StorageZange(unittest.TestCase):
    def test_recorder_and_run_events(self):
        fr = [link.parse_diag(D_BASIS.replace("D;12;", f"D;{i};"), 100.0 + i / 60) for i in range(1, 30)]
        with tempfile.TemporaryDirectory() as tmp:
            rec = Recorder(tmp, physics.DynoParams(), diag=True, zange=True)
            rec.add_frames(fr)
            folder = rec.close()
            run = storage.load_any(folder)
            self.assertEqual(run["columns"]["diag_ign_peak_mv"][0], 3955.0)
            self.assertIn("Zündstärke", channels.run_channels(run, None, run["params"]))
            # Lauf mit Ereignissen der Basis
            evs = [basis.parse_event("E;1;MARK;zange;T1", 100.2), basis.parse_event("E;2;DOSE;1;1", 100.3),
                   basis.parse_event("E;3;ZANGE;verloren", 100.4)]
            os.makedirs(os.path.join(tmp, "lauf"))
            self.assertEqual(storage.write_side_events(os.path.join(tmp, "lauf"), 100.0, evs), (1, 2))
            side = storage._read_side_files(storage._folder_reader(os.path.join(tmp, "lauf"))[0], [])
            self.assertEqual(side["markers"], [(0.2, "Marker Zange: T1")])
            self.assertEqual([e[1] for e in side["events"]], ["Steckdose", "Zange"])
            hdr = storage.ROH_HEADER + storage.DIAG_HEADER + storage.ZANGE_HEADER
            self.assertEqual(len(storage.frame_row(fr[0], 100.0, physics.DynoParams(), True, True)), len(hdr))


# ---------------------------------------------------------------------------------------------- Steckdosen
class PlugHttp(unittest.TestCase):
    def setUp(self):
        self.devs = []

    def tearDown(self):
        for d in self.devs:
            d.close()

    def dev(self, typ, **kw):
        d = plugs.PlugSimDevice(typ, 1000.0, **kw)
        self.devs.append(d)
        return d

    def test_types(self):
        for typ in plugs.PLUG_TYPES:
            with self.subTest(typ=typ):
                d = self.dev(typ)
                self.assertEqual(plugs.detect_type(d.host), typ)
                cfg = plugs.PlugConfig(1, typ, d.host, esphome_id="relay")
                drv = plugs.PlugDriver(cfg)
                note = drv.init_defaults()
                if typ == "ESPHOME":
                    self.assertIn("restore_mode", note)
                else:
                    self.assertEqual(note, "")
                    self.assertIn(d.initial, ("off", "0"))
                st = drv.status()
                self.assertTrue(st.reachable)
                self.assertFalse(st.on)
                st = drv.set(True, 30)
                self.assertTrue(d.on and st.on)
                self.assertAlmostEqual(st.power_w, 1000.0, delta=20)
                if typ == "ESPHOME":
                    self.assertIsNone(d.off_at)                          # kein Rueckfall-Timer
                else:
                    self.assertAlmostEqual(d.off_at - time.time(), 30, delta=2)
                if typ in ("SHELLY2", "SHELLY1"):
                    self.assertTrue(st.rest_s > 25)
                st = drv.set(False, 30)
                self.assertFalse(d.on or st.on)
                self.assertEqual(st.power_w, 0.0)
        reqs = {d.typ: d.requests for d in self.devs}
        self.assertIn("/rpc/Switch.Set?id=0&on=true&toggle_after=30", reqs["SHELLY2"])
        self.assertIn("/rpc/Switch.Set?id=0&on=false", reqs["SHELLY2"])       # Timer nur mit "ein"
        self.assertIn("/relay/0?turn=on&timer=30", reqs["SHELLY1"])
        self.assertIn("/cm?cmnd=PulseTime1%20130", reqs["TASMOTA"])          # 30 s = 130
        self.assertIn("/cm?cmnd=PowerOnState%200", reqs["TASMOTA"])

    def test_tasmota_pulsetime_short_and_none(self):
        d = self.dev("TASMOTA")
        drv = plugs.PlugDriver(plugs.PlugConfig(1, "TASMOTA", d.host))
        drv.set(True, 5)
        self.assertEqual(d.pulsetime, 50)                               # 0,1-s-Schritte
        drv.set(True, None)
        self.assertEqual(d.pulsetime, 0)                                # „bleibt an“: kein Rueckfall
        self.assertIsNone(d.off_at)

    def test_reject_and_unreachable(self):
        with self.assertRaises(plugs.PlugRejected):
            plugs.detect_type(self.dev("SHELLY2", auth=True).host)
        with self.assertRaises(plugs.PlugRejected):
            plugs.detect_type(self.dev("SHELLY2", relay=False).host)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        st = plugs.PlugDriver(plugs.PlugConfig(1, "SHELLY2", f"127.0.0.1:{port}"), timeout=0.5).status()
        self.assertFalse(st.reachable)
        self.assertEqual(st.state_text(), "nicht erreichbar")

    def test_manager_direct_and_totmann(self):
        d1, d2 = self.dev("SHELLY2"), self.dev("ESPHOME")
        bad = self.dev("SHELLY2", relay=False)
        m = plugs.PlugManager(mode="direkt")
        try:
            m.add(plugs.PlugConfig(1, "", d1.host, "Heizlüfter", totmann_s=6))
            m.add(plugs.PlugConfig(2, "ESPHOME", d2.host, "Licht", fail_on=True))
            m.add(plugs.PlugConfig(3, "SHELLY2", bad.host, "PM"))
            self.assertTrue(m.wait_idle())
            self.assertEqual(m.configs[1].typ, "SHELLY2")                # erkannt
            self.assertNotIn(3, m.configs)                               # ohne Relais abgewiesen
            self.assertTrue(any("kein Relais" in x for x in m.messages))
            m.switch(1, True)
            m.switch(2, True)
            self.assertTrue(m.wait_idle())
            self.assertTrue(m.states[1].on and m.states[2].on)
            self.assertTrue(d1.on and d2.on)
            n0 = sum("toggle_after" in r for r in d1.requests)
            time.sleep(2.2)
            m.poll()                                                     # Totmann erneuern (alle 2 s)
            self.assertTrue(m.wait_idle())
            self.assertGreater(sum("toggle_after" in r for r in d1.requests), n0)
            self.assertFalse(any("toggle_after" in r for r in d2.requests))
            m.switch(1, "TOGGLE")
            self.assertTrue(m.wait_idle())
            self.assertFalse(d1.on)
            self.assertEqual(len(m.to_settings()), 2)
            m.delete(2)
            m.refresh()
            self.assertTrue(m.wait_idle())
            self.assertEqual(set(m.states), {1})
        finally:
            m.close()

    def test_manager_basis(self):
        l = link.DynoLink("SIM:basis")
        m = plugs.PlugManager(mode="basis", get_link=lambda: l)
        try:
            l.info()
            m.refresh()
            self.assertTrue(m.wait_idle())
            self.assertEqual(set(m.configs), {1, 2})
            m.configs[1].fail_on = False
            m.configs[1].totmann_s = 45
            m.switch(1, True)
            self.assertTrue(m.wait_idle())
            self.assertTrue(m.states[1].on)
            self.assertGreater(m.states[1].rest_s, 30)                   # Totmann an die Basis gegeben
            m.add(plugs.PlugConfig(5, "ESPHOME", "werkstatt-licht.local", "Licht"))
            self.assertTrue(m.wait_idle())
            self.assertIn(5, m.states)
            m.scan()
            self.assertTrue(m.wait_idle())
            self.assertEqual(len(m.found), 3)
        finally:
            m.close()
            l.close()

    def test_automation(self):
        a = plugs.PlugAutomation()
        cfgs = [plugs.PlugConfig(1, auto_lauf=True, nachlauf_s=30), plugs.PlugConfig(2, auto_dauer=True, nachlauf_s=0),
                plugs.PlugConfig(3)]
        self.assertEqual(a.update(cfgs, False, False, 0), [])
        self.assertEqual(a.update(cfgs, True, False, 1), [(1, True)])
        self.assertEqual(a.update(cfgs, True, False, 2), [])            # nur einmal schalten
        self.assertEqual(a.update(cfgs, False, False, 10), [])          # Nachlauf beginnt
        self.assertEqual(a.pending_text(1, 10), "aus in 30 s")
        self.assertEqual(a.update(cfgs, False, False, 39), [])
        self.assertEqual(a.update(cfgs, True, False, 39.5), [])         # neuer Lauf im Nachlauf: bleibt an
        self.assertEqual(a.update(cfgs, False, False, 50), [])
        self.assertEqual(a.update(cfgs, False, False, 80), [(1, False)])
        self.assertEqual(a.update(cfgs, False, True, 81), [(2, True)])
        self.assertEqual(a.update(cfgs, False, False, 82), [(2, False)])  # ohne Nachlauf


# ---------------------------------------------------------------------------------------------- Oberflaeche
class GuiZange(unittest.TestCase):
    """Reiter „Zange · Netz · Steckdosen“ offscreen: Basis-Simulator, Demo-Steckdosen mit Automatik,
    Marker der Zange in Oszi und Lauf, Zange per USB mit Warnhinweis und Log -> .pyst."""

    def test_gui(self):
        shots = os.environ.get("PYST_SCREENSHOTS", "")
        with tempfile.TemporaryDirectory() as home:
            old = {k: os.environ.get(k) for k in ("HOME", "QT_QPA_PLATFORM")}
            os.environ["HOME"] = home
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            try:
                from PySide6 import QtWidgets
                app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
                import importlib
                from pyst import gui
                importlib.reload(gui)
                w = gui.MainWindow()
                w.resize(1480, 900)

                def run(sec, cond=None):
                    end = time.time() + sec
                    while time.time() < end:
                        app.processEvents()
                        if cond is not None and cond():
                            return True
                        time.sleep(0.01)
                    return cond is None

                def shot(name, widget=None):
                    if shots:
                        os.makedirs(shots, exist_ok=True)
                        (widget or w).grab().save(os.path.join(shots, name))

                w.page_settings.port_combo.setEditText("SIM:basis")
                self.assertTrue(w.on_connect())
                self.assertEqual(w.link.mode, "D")
                hw = w.page_hw
                w.tabs.setCurrentWidget(hw)
                hw.start_demo()
                self.assertTrue(run(5, lambda: "Akku" in w.zange_status.text()))
                self.assertTrue(run(5, lambda: len(hw.plugs.states) == 4))
                self.assertTrue(hw.b_mark.isEnabled())
                # Lauf: Automatik schaltet Gebläse und Absaugung ein, Marker der Zange gehoert zum Lauf
                w.on_start()
                self.assertTrue(run(5, lambda: hw.plugs.states.get(1) is not None and hw.plugs.states[1].on))
                hw.mark()
                self.assertTrue(run(3, lambda: any(e.is_marker for e in w.ctrl.basis_events)))
                w.tabs.setCurrentWidget(hw)
                run(1.0)
                shot("zange_netz_steckdosen.png")
                w.on_abort()
                run(0.5)
                self.assertTrue(hw.plugs.automation.pending_text(1).startswith("aus in"))
                # Oszi: Dauermessung mit Zuendstaerke und Marker der Zange
                o = w.page_oszi
                w.tabs.setCurrentWidget(o)
                o._toggle_channel("Zündstärke", True)
                o.rec_btn.setChecked(True)
                run(2.0)
                hw.mark()
                self.assertTrue(run(3, lambda: any("Marker" in m[1] for m in o.markers)))
                run(1.0)
                self.assertGreater(np.nanmax(o.store.v["Zündstärke"][:o.store.n]), 3000)
                shot("zange_oszi.png")
                o.rec_btn.setChecked(False)
                self.assertEqual(w.link.mode, "D")                       # Basis mit Zange bleibt in D
                folder = w.db.runs()[0].path
                folder = folder if os.path.isdir(folder) else os.path.dirname(folder)
                run_ = storage.load_any(folder)
                self.assertIn("diag_ign_peak_mv", run_["columns"])
                self.assertTrue(any("Marker" in t for _x, t in run_["markers"]))
                # Zange per USB
                w.on_connect()
                w.page_settings.port_combo.setEditText("SIM:zange")
                self.assertTrue(w.on_connect())
                self.assertIsNotNone(w._warn_box)
                self.assertIn("galvanische", w._warn_box.text())
                w._warn_box.close()
                w.tabs.setCurrentWidget(hw)
                run(0.5)
                self.assertFalse(hw.banner.isHidden())
                self.assertTrue(hw.b_log.isEnabled())
                shot("zange_usb.png")
                folder = hw.read_log(save_pyst=os.path.join(home, "zange.pyst"))
                self.assertTrue(os.path.exists(os.path.join(home, "zange.pyst")))
                rid = w.db.runs()[0].id
                self.assertEqual(w.db.run(rid).notiz, "Zange-Log")
                w._open_in_viewer(rid)
                run(1.0)
                shot("zange_log_auswertung.png", w.viewer)
                w.close()
                app.processEvents()
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
