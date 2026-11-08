"""
Bootloader der PyST-Basis: Rahmen und Update-Datei (*.pystfw). Gegenstück in der Firmware: boot_fmt.h, boot_core.c.

Rahmen wie der ESP-Link: COBS(typ seq daten crc16) 0x00, CRC-16/CCITT-FALSE ueber typ..daten, little-endian.
Jeder Befehl bekommt eine Antwort BL_RESP mit derselben seq: [befehl u8][status u8][wert u32][text].
Datei: 256 Byte Kopf + Nutzdaten. Format 1: Abbild im Klartext, unsigniert. Format 2 (Bootloader 2.0.0): Ed25519-signiert,
Nutzdaten AES-256-CTR, Spezifikation docs/STUFE2.md der Firmware. Hier nur die formale Pruefung, die Signatur prueft fw_keys.py.
Dasselbe Format 2 nutzen die ESP-Ziele (Zange zange-c3, Basis-ESP basis-esp32c3): load_addr 0, Laenge Vielfaches von 16,
Nutzdaten mit dem Schluessel des Ziels verschluesselt. Den kennt nur das Geraet, der PC prueft Kopf, CRC und Signatur.
"""
import struct
import zlib
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .felder import parse_kv

BL_PING, BL_START, BL_DATA, BL_END, BL_ABORT, BL_BOOT, BL_RESP = 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x80
(BL_OK, BL_BUSY, BL_E_STATE, BL_E_OFFSET, BL_E_HEADER, BL_E_TARGET, BL_E_UNSUPPORTED, BL_E_LENGTH, BL_E_FLASH,
 BL_E_VERIFY, BL_E_CMD, BL_E_SIGNATURE, BL_E_ROLLBACK) = range(13)
STATUS_TEXT = {
    BL_E_STATE: "Ablauf gestört", BL_E_OFFSET: "Lücke in den Daten", BL_E_HEADER: "Dateikopf ungültig",
    BL_E_TARGET: "Datei passt nicht zu dieser Basis", BL_E_UNSUPPORTED: "Dateiformat zu neu für diesen Bootloader",
    BL_E_LENGTH: "Länge ungültig", BL_E_FLASH: "Flash-Fehler im Gerät", BL_E_VERIFY: "Prüfung im Gerät fehlgeschlagen",
    BL_E_CMD: "Befehl unbekannt",
    BL_E_SIGNATURE: "Signatur ungültig, die Datei stammt nicht von uns oder passt nicht zum Schlüssel dieses Bootloaders",
    BL_E_ROLLBACK: "Rückfall gesperrt, die Datei ist älter als die Firmware auf der Basis zulässt",
}

BLOCK = 512
BLOCK_MAX = 1016
APP_BASE = 0x08008000
APP_MAX = 0x38000
BOOT_SIZE = 0x8000
IMG_HDR_OFS = 0x200
IMG_HDR_LEN = 64
IMG_MAGIC = 0x4D495950
RAM = (0x20000000, 0x20020000)
UPD_MAGIC = b"PYSTUPD1"
UPD_LEN = 256
FORMAT_PLAIN, FORMAT_SIGNED = 1, 2
FLAGS_SIGNED_ENCRYPTED = 3
SIG_ED25519, ENC_AES256_CTR = 1, 1
SIG_OFS, SIG_END = 100, 164                             # signature[64]
RESERVED_OFS = 196                                      # reserved[32..87], in Format 2 Null
TARGET = "basis-g473"
BOARD = "rev31"
TARGET_ZANGE, TARGET_BASIS_ESP = "zange-c3", "basis-esp32c3"
ESP_MAX = {TARGET_ZANGE: 0x140000, TARGET_BASIS_ESP: 0x180000}      # Groesse von ota_0/ota_1 (partitions.csv)
ESP_ALIGN = 16
DEVICE_NAME = {TARGET: "Basis", TARGET_ZANGE: "Zange", TARGET_BASIS_ESP: "Basis-ESP"}


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    out = bytearray()
    block = bytearray()
    for b in data:
        if b:
            block.append(b)
            if len(block) == 254:
                out.append(255)
                out += block
                block.clear()
        else:
            out.append(len(block) + 1)
            out += block
            block.clear()
    out.append(len(block) + 1)
    out += block
    return bytes(out)


def cobs_decode(data: bytes) -> Optional[bytes]:
    out = bytearray()
    i = 0
    while i < len(data):
        code = data[i]
        i += 1
        if code == 0 or i + code - 1 > len(data):
            return None
        out += data[i:i + code - 1]
        i += code - 1
        if code != 255 and i < len(data):
            out.append(0)
    return bytes(out)


def encode_frame(kind: int, seq: int, data: bytes = b"") -> bytes:
    raw = bytes((kind, seq & 0xFF)) + data
    return cobs_encode(raw + struct.pack("<H", crc16(raw))) + b"\0"


class FrameDecoder:
    """Bytestrom -> Rahmen; alles Unlesbare (Text, Teilrahmen, falsche CRC) faellt still weg."""

    def __init__(self):
        self.buf = bytearray()
        self.bad = 0

    def feed(self, data: bytes) -> List[Tuple[int, int, bytes]]:
        frames = []
        for b in data:
            if b:
                if len(self.buf) < 2048:
                    self.buf.append(b)
                continue
            raw = cobs_decode(bytes(self.buf)) if self.buf else b""
            had = bool(self.buf)
            self.buf.clear()
            if raw is None or (had and (len(raw) < 4 or crc16(raw[:-2]) != struct.unpack("<H", raw[-2:])[0])):
                self.bad += 1
            elif had:
                frames.append((raw[0], raw[1], raw[2:-2]))
        return frames


def parse_resp(data: bytes) -> Tuple[int, int, int, str]:
    """-> (befehl, status, wert, text)"""
    if len(data) < 6:
        raise ValueError("Antwort zu kurz")
    cmd, status, value = struct.unpack_from("<BBI", data)
    return cmd, status, value, data[6:].decode("latin-1")


parse_info = parse_kv                                   # '#BOOTLOADER;proto=1;bl=1.0.0;…' -> Felder


def _text(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("ascii", "replace")


def _check_format2(h: bytes, flags: int):
    if flags != FLAGS_SIGNED_ENCRYPTED:
        raise ValueError("Update-Datei ungültig: Format 2 muss signiert und verschlüsselt sein.")
    if h[76] != SIG_ED25519 or h[77] != ENC_AES256_CTR:
        raise ValueError("Update-Datei ist zu neu: Dieses PyST kennt das Signatur- oder Verschlüsselungsverfahren nicht.")
    if h[79] or any(h[RESERVED_OFS:252]):               # reserved0 und reserved[32..87]
        raise ValueError("Update-Datei beschädigt (Kopf).")


@dataclass
class UpdateFile:
    header: bytes
    payload: bytes
    target: str
    board: str
    version: str
    image_len: int
    image_crc: int
    format: int = FORMAT_PLAIN
    key_id: int = 0
    rollback: int = 0

    @property
    def signature(self) -> bytes:
        return self.header[SIG_OFS:SIG_END]

    @property
    def signed_data(self) -> bytes:
        """Was die Signatur deckt: alles ausser ihr selbst und hdr_crc."""
        return b"PyST-FW-2\0" + self.header[:SIG_OFS] + self.header[SIG_END:252]

    @property
    def format_text(self) -> str:
        if self.format == FORMAT_PLAIN:
            return "Format 1, unsigniert"
        return f"Format {self.format}, Schlüssel {self.key_id}, Rollback {self.rollback}"

    @classmethod
    def parse(cls, data: bytes, target: str = TARGET) -> "UpdateFile":
        """Vollstaendige Pruefung fuer das erwartete Ziel; ValueError mit Klartext. Format 2 ist verschluesselt:
        Vektortabelle bzw. ESP-Abbild sieht erst das Geraet."""
        esp = target in ESP_MAX
        name = DEVICE_NAME.get(target, target)
        if data[:1] == b"\xe9":
            raise ValueError("Falsche Firmware: Das ist ein rohes ESP-Abbild (firmware.bin), keine Update-Datei. Für die "
                             f"{name} die .pystfw wählen, den Build-Ordner nur beim Erstflash." if esp
                             else "Falsche Firmware: Das ist ein ESP-Abbild, kein STM32.")
        if data[:8] != UPD_MAGIC or len(data) < UPD_LEN:
            raise ValueError(f"Falsche Firmware: Das ist keine Update-Datei (.pystfw) für die {name}.")
        h = data[:UPD_LEN]
        fmt, size, flags = struct.unpack_from("<HHI", h, 8)
        if zlib.crc32(h[:252]) != struct.unpack_from("<I", h, 252)[0] or size != UPD_LEN:
            raise ValueError("Update-Datei beschädigt (Kopf).")
        if fmt == FORMAT_SIGNED:
            _check_format2(h, flags)
        elif fmt != FORMAT_PLAIN or flags:
            raise ValueError("Update-Datei ist zu neu: Dieses PyST kennt das Format nicht.")
        got, board, version = _text(h[16:32]), _text(h[32:40]), _text(h[40:56])
        if got != target or board != BOARD:
            other = DEVICE_NAME.get(got)
            raise ValueError(f"Falsche Firmware: Die Datei ist für {got} {board}" + (f" ({other})" if other else "")
                             + f", nicht für {target} {BOARD} ({name}).")
        if esp and fmt != FORMAT_SIGNED:
            raise ValueError(f"Update-Datei ungültig: Für die {name} gibt es nur signierte Dateien (Format 2).")
        load, ilen, icrc, plen, pcrc = struct.unpack_from("<IIIII", h, 56)
        payload = data[UPD_LEN:]
        if esp:
            bad = load != 0 or not 0 < ilen <= ESP_MAX[target] or ilen % ESP_ALIGN
        else:
            bad = load != APP_BASE or not 0 < ilen <= APP_MAX or ilen % 8
        if bad or plen != ilen or plen != len(payload):
            raise ValueError("Update-Datei beschädigt (Längen).")
        if zlib.crc32(payload) != pcrc:
            raise ValueError("Update-Datei beschädigt (Prüfsumme).")
        if fmt == FORMAT_SIGNED:
            return cls(h, payload, got, board, version, ilen, icrc, fmt, h[78], struct.unpack_from("<I", h, 80)[0])
        sp, pc = struct.unpack_from("<II", payload)
        if not (RAM[0] < sp <= RAM[1] and pc & 1 and APP_BASE <= pc < APP_BASE + ilen):
            raise ValueError("Falsche Firmware: Die Vektortabelle passt nicht zur Basis-Anwendung.")
        return cls(h, payload, got, board, version, ilen, icrc)
