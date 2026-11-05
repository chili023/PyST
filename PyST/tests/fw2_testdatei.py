"""
Update-Dateien Format 2 fuer Tests, Aufbau nach docs/STUFE2.md der Firmware. Kuenstliches Abbild, keine echte Firmware.

Die Schluessel hier sind TESTSCHLUESSEL: oeffentlich aus festem Text abgeleitet, key_id 0 ist laut Spezifikation den
Host-Tests vorbehalten. Sie stehen nie in fw_keys.KNOWN_KEYS und in keinem Bootloader.
"""
import hashlib
import os
import struct
import zlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from pyst import bootproto as bp

TESTSCHLUESSEL_ID = 0
TESTSCHLUESSEL_ED25519 = hashlib.sha256(b"PyST TESTSCHLUESSEL Ed25519, nie fuer echte Firmware").digest()
TESTSCHLUESSEL_AES = hashlib.sha256(b"PyST TESTSCHLUESSEL AES, nie fuer echte Firmware").digest()
FREMDER_TESTSCHLUESSEL_ED25519 = hashlib.sha256(b"PyST fremder TESTSCHLUESSEL Ed25519").digest()


def public_key(seed: bytes = TESTSCHLUESSEL_ED25519) -> bytes:
    return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def image(version: str = "0.4.0", size: int = 20480, rollback: int = 1, reset: int = bp.APP_BASE + 0x301) -> bytes:
    """Abbild mit Vektortabelle und Abbild-Kopf wie tools/pyst_image.py."""
    img = bytearray(struct.pack("<II", 0x20020000, reset) + bytes(range(256)) * (size // 256 + 1))[:size]
    h = bytearray(bp.IMG_HDR_LEN)
    struct.pack_into("<IHHIII", h, 0, bp.IMG_MAGIC, 1, bp.IMG_HDR_LEN, bp.APP_BASE, size, 0)
    h[20:20 + len(version)] = version.encode()
    h[36:41] = bp.BOARD.encode()
    struct.pack_into("<I", h, 44, rollback)
    img[bp.IMG_HDR_OFS:bp.IMG_HDR_OFS + bp.IMG_HDR_LEN] = h
    end = bp.IMG_HDR_OFS + bp.IMG_HDR_LEN
    crc = zlib.crc32(bytes(img[end:]), zlib.crc32(bytes(img[:bp.IMG_HDR_OFS])))
    struct.pack_into("<I", h, 16, crc)
    struct.pack_into("<I", h, 60, zlib.crc32(bytes(h[:60])))
    img[bp.IMG_HDR_OFS:end] = h
    return bytes(img)


def format1(version: str = "0.3.0", size: int = 4096, reset: int = bp.APP_BASE + 0x301) -> bytes:
    img = image(version, size, reset=reset)
    h = bytearray(bp.UPD_LEN)
    h[0:8] = bp.UPD_MAGIC
    struct.pack_into("<HHI", h, 8, bp.FORMAT_PLAIN, bp.UPD_LEN, 0)
    h[16:16 + len(bp.TARGET)] = bp.TARGET.encode()
    h[32:32 + len(bp.BOARD)] = bp.BOARD.encode()
    h[40:40 + len(version)] = version.encode()
    image_crc = struct.unpack_from("<I", img, bp.IMG_HDR_OFS + 16)[0]
    struct.pack_into("<IIIII", h, 56, bp.APP_BASE, size, image_crc, size, zlib.crc32(img))
    return fix_crc(h) + img


def encrypt(plain: bytes, nonce: bytes, aes: bytes = TESTSCHLUESSEL_AES) -> bytes:
    enc = Cipher(algorithms.AES(aes), modes.CTR(nonce)).encryptor()
    return enc.update(plain) + enc.finalize()


def seal(h: bytearray, seed: bytes = TESTSCHLUESSEL_ED25519) -> bytes:
    """Signiert den Kopf und traegt hdr_crc ein."""
    msg = b"PyST-FW-2\0" + bytes(h[:bp.SIG_OFS]) + bytes(h[bp.SIG_END:252])
    h[bp.SIG_OFS:bp.SIG_END] = Ed25519PrivateKey.from_private_bytes(seed).sign(msg)
    return fix_crc(h)


def fix_crc(h: bytearray) -> bytes:
    struct.pack_into("<I", h, 252, zlib.crc32(bytes(h[:252])))
    return bytes(h)


def format2(version: str = "0.4.0", size: int = 20480, rollback: int = 1, key_id: int = TESTSCHLUESSEL_ID,
            seed: bytes = TESTSCHLUESSEL_ED25519, plain: bytes = b"", patch=None) -> bytes:
    """patch(kopf) aendert den Kopf vor dem Signieren, fuer Dateien mit gueltiger Signatur und falschem Inhalt."""
    img = plain or image(version, size, rollback)
    nonce = os.urandom(16)
    payload = encrypt(img, nonce)
    h = bytearray(bp.UPD_LEN)
    h[0:8] = bp.UPD_MAGIC
    struct.pack_into("<HHI", h, 8, bp.FORMAT_SIGNED, bp.UPD_LEN, bp.FLAGS_SIGNED_ENCRYPTED)
    h[16:16 + len(bp.TARGET)] = bp.TARGET.encode()
    h[32:32 + len(bp.BOARD)] = bp.BOARD.encode()
    h[40:40 + len(version)] = version.encode()
    image_crc = struct.unpack_from("<I", img, bp.IMG_HDR_OFS + 16)[0]
    struct.pack_into("<IIIII", h, 56, bp.APP_BASE, len(img), image_crc, len(payload), zlib.crc32(payload))
    h[76], h[77], h[78] = bp.SIG_ED25519, bp.ENC_AES256_CTR, key_id
    struct.pack_into("<I", h, 80, rollback)
    h[84:100] = nonce
    h[164:196] = hashlib.sha256(img).digest()
    if patch:
        patch(h)
    return seal(h, seed) + payload
