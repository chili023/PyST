"""
Minimales mDNS (RFC 6762) ohne Zusatzpakete: Namen '*.local' aufloesen und Dienste suchen
(z.B. '_shelly._tcp.local', '_http._tcp.local'). Einmalige Anfrage ("legacy unicast", RFC 6762 §6.7) von einem
beliebigen Port – die Geraete antworten direkt an diesen Port. Reicht fuer Basis und Steckdosen im selben Netz.
"""
import socket
import struct
import time
from typing import Dict, List, Optional, Tuple

MDNS_ADDR = ("224.0.0.251", 5353)
T_A, T_PTR, T_TXT, T_SRV = 1, 12, 16, 33


def _encode_name(name: str) -> bytes:
    out = b""
    for part in name.rstrip(".").split("."):
        b = part.encode("utf-8")
        out += bytes([len(b)]) + b
    return out + b"\x00"


def build_query(names: List[Tuple[str, int]], qid: int = 0) -> bytes:
    """[(Name, Typ)] -> DNS-Anfrage (Bit 'QU' = Antwort per Unicast erwuenscht)."""
    pkt = struct.pack("!HHHHHH", qid, 0, len(names), 0, 0, 0)
    for name, qtype in names:
        pkt += _encode_name(name) + struct.pack("!HH", qtype, 0x8001)
    return pkt


def _read_name(data: bytes, off: int) -> Tuple[str, int]:
    labels, jumped, end, guard = [], False, off, 0
    while True:
        guard += 1
        if off >= len(data) or guard > 64:
            raise ValueError("Name kaputt")
        n = data[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0 == 0xC0:
            ptr = struct.unpack("!H", data[off:off + 2])[0] & 0x3FFF
            if not jumped:
                end = off + 2
            jumped = True
            off = ptr
            continue
        labels.append(data[off + 1:off + 1 + n].decode("utf-8", "replace"))
        off += 1 + n
    return ".".join(labels), (end if jumped else off)


def parse_response(data: bytes) -> List[Tuple[str, int, object]]:
    """DNS-Antwort -> [(Name, Typ, Wert)]; Wert: A -> 'a.b.c.d', PTR -> Name, SRV -> (Ziel, Port),
    TXT -> {Schluessel: Wert}."""
    if len(data) < 12:
        return []
    _id, _fl, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
    off = 12
    for _ in range(qd):
        _n, off = _read_name(data, off)
        off += 4
    out = []
    for _ in range(an + ns + ar):
        name, off = _read_name(data, off)
        rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", data[off:off + 10])
        off += 10
        rd = data[off:off + rdlen]
        val: object = None
        if rtype == T_A and rdlen == 4:
            val = socket.inet_ntoa(rd)
        elif rtype == T_PTR:
            val = _read_name(data, off)[0]
        elif rtype == T_SRV and rdlen >= 7:
            port = struct.unpack("!H", rd[4:6])[0]
            val = (_read_name(data, off + 6)[0], port)
        elif rtype == T_TXT:
            kv, k = {}, 0
            while k < len(rd):
                n = rd[k]
                s = rd[k + 1:k + 1 + n].decode("utf-8", "replace")
                if "=" in s:
                    a, b = s.split("=", 1)
                    kv[a] = b
                k += 1 + n
            val = kv
        out.append((name, rtype, val))
        off += rdlen
    return out


def _ask(questions: List[Tuple[str, int]], timeout: float) -> List[Tuple[str, int, object]]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        s.bind(("", 0))
        s.settimeout(0.1)
        s.sendto(build_query(questions), MDNS_ADDR)
        records = []
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, _addr = s.recvfrom(9000)
            except socket.timeout:
                continue
            try:
                records += parse_response(data)
            except (ValueError, struct.error, IndexError):
                continue
        return records
    except OSError:
        return []
    finally:
        s.close()


def resolve(host: str, timeout: float = 1.5) -> Optional[str]:
    """'pyst-basis.local' -> IPv4-Adresse oder None. Erst das Betriebssystem fragen (macOS, Windows, Linux mit
    nss-mdns koennen das), dann selbst per mDNS."""
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except (OSError, IndexError):
        pass
    if not host.endswith(".local"):
        return None
    for name, rtype, val in _ask([(host, T_A)], timeout):
        if rtype == T_A and name.lower() == host.lower():
            return str(val)
    return None


def browse(service: str, timeout: float = 2.0) -> List[Dict[str, object]]:
    """Dienst suchen, z.B. '_shelly._tcp.local' -> [{'name', 'host', 'ip', 'port', 'txt'}]."""
    return collect(_ask([(service, T_PTR)], timeout), service)


def collect(records: List[Tuple[str, int, object]], service: str) -> List[Dict[str, object]]:
    """Aus den Datensaetzen einer Antwort die Instanzen eines Dienstes zusammenstellen."""
    inst = [str(v) for n, t, v in records if t == T_PTR and n.lower() == service.lower()]
    srv = {n: v for n, t, v in records if t == T_SRV}
    txt = {n: v for n, t, v in records if t == T_TXT}
    a = {n.lower(): v for n, t, v in records if t == T_A}
    out = []
    for name in dict.fromkeys(inst):
        target, port = srv.get(name, ("", 80))
        out.append({"name": name.split("." + service)[0] if service in name else name, "host": target,
                    "ip": a.get(str(target).lower(), ""), "port": port, "txt": txt.get(name, {})})
    return out
