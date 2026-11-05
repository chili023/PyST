"""
Firmware flashen: Zange (ESP32-C3 per USB), Basis-STM32 (per USB ueber den eigenen Bootloader, reines Python; fuer die
Erstprogrammierung ST-Link an J10), Basis-ESP (ueber die ESP-Bruecke), Lambda-Controller F042 (rusEFI-Wideband, ueber die
Basis per CAN, LambdaFlasher).

Dieses Repository enthaelt keine Firmware der eigenen Hardware (PyST-HW ist nicht oeffentlich): man waehlt einen
Ordner mit dem Build. Vor dem Flashen wird geprueft, ob der Build zum Ziel passt, damit keine Zangen-Firmware auf dem
Basis-ESP landet (beides ESP32-C3). Die Adressen stammen aus dem Build (flasher_args.json), nicht aus diesem Code.
Hier Pruefen, Befehlszeilen bauen und der Ablauf des USB-Updates (UsbFlasher); esptool und st-flash startet flash_page.py.
"""
import glob
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import sys
import time
import zlib
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

try:
    import serial
    from serial.tools import list_ports
except ImportError:                       # pragma: no cover
    serial = None
    list_ports = None

from . import bootproto as bp
from .basis import ZANGE_USB_ID
from .diag import parse_kv
from .auth import refusal_text
from .link import BASIS_USB_ID, is_esp_bridge

ZANGE, BASIS_STM, BASIS_STLINK, BASIS_ESP, LAMBDA_F042 = "zange", "basis_stm", "basis_stlink", "basis_esp", "lambda_f042"
STM_FLASH_ADDR = 0x08000000
STM_FLASH_MAX = 512 * 1024
STM_SRAM = (0x20000000, 0x20020000)       # 128 KB
ESP_IMAGE_MAGIC = 0xE9
GESAMT_NAME = "pyst-basis-gesamt.bin"
# U902 STM32F042, rusEFI-Wideband (wideband_layout.ld, build_wideband.sh): 6 KB Bootloader, 25 KB Anwendung mit CRC-32 in
# den letzten 4 Byte (Big Endian), 1 KB Konfiguration
F042_FLASH = 0x08000000
F042_BL_SIZE = 6 * 1024
F042_APP_BASE = F042_FLASH + F042_BL_SIZE
F042_APP_SIZE = 25 * 1024
F042_APP_END = F042_APP_BASE + F042_APP_SIZE
F042_RAM = (0x20000000, 0x20001800)       # 6 KB
F042_NAMES = ("wideband_image_with_bl.bin", "wideband_image.bin")
WB_VERSION = "A0"                         # RUSEFI_WIDEBAND_VERSION in den 0x190-Telegrammen
WBF_BLOCK = 16                            # eine #-Zeile der Basis


ESP_BUSY = "Die Basis gibt gerade Messwerte aus: Erst die Messung anhalten, dann den ESP flashen."


class FlashError(Exception):
    pass


@dataclass
class Target:
    key: str
    name: str
    project: str                          # muss im Projektnamen des Builds stehen (ESP)
    baud: int = 0
    hint: str = ""


TARGETS = {
    ZANGE: Target(ZANGE, "Zange", "pyst-zange", 921600,
                  "Akku drin, USB-C an der Zange, T1 bis zum Ende halten. Bleibt die Zange nicht im Download-Modus: "
                  "zuerst T2, dann T1 drücken und beide halten; danach neu einschalten."),
    BASIS_STM: Target(BASIS_STM, "Basis STM32", "", 0,
                      "12 V an, USB-C an J20, nur eine Basis anstecken. Update-Datei (.pystfw) wählen. Einstellungen "
                      "bleiben erhalten. Fällt der Strom aus, setzt die Basis beim nächsten Start selbst fort."),
    BASIS_STLINK: Target(BASIS_STLINK, "Basis STM32 per ST-Link (Erstprogrammierung)", "", 0,
                         "Nur für neue Platinen: Gesamtdatei (Bootloader + Anwendung) über ST-Link an J10, 12 V an X1. "
                         "Danach 12 V einmal aus und ein: der ST-Link lässt den STM32 sonst angehalten, die Basis fällt "
                         "vom USB."),
    BASIS_ESP: Target(BASIS_ESP, "Basis ESP", "basis-esp32c3", 460800,
                      "12 V an, STM32-Firmware läuft, USB-C an J20, keine Messung. Geht nur per USB über die "
                      "ESP-Brücke der Basis, PyST schaltet sie ein. Der WLAN-/Funkbetrieb ruht währenddessen."),
    LAMBDA_F042: Target(LAMBDA_F042, "Lambda (F042) per CAN", "", 0,
                        "12 V an, Basis-Firmware ab 0.2.1, USB-C an J20. Datei wideband_image_with_bl.bin aus dem "
                        "rusEFI-Wideband-Release oder die Anwendung allein. Übertragen wird nur die Anwendung, der "
                        "F042-Bootloader bleibt unangetastet. Messung und Lambda-Heizung müssen aus sein, die Sonde "
                        "bleibt kalt. Bricht es ab: Nochmals flashen."),
}

_BUILD_FILES = {"bootloader": "bootloader.bin", "partition-table": "partitions.bin", "otadata": "ota_data_initial.bin",
                "app": "firmware.bin"}


@dataclass
class Image:
    target: str
    folder: str
    project: str = ""
    version: str = ""
    files: List[Tuple[int, str]] = field(default_factory=list)      # (Adresse, Pfad)
    flash_args: List[str] = field(default_factory=list)
    main: str = ""                                                  # Datei fuer Pruefsumme und Datum
    update: Optional[bp.UpdateFile] = None                          # Basis STM32 per USB
    lambda_app: bytes = b""                                         # F042: nur die Anwendung

    def summary(self) -> str:
        with open(self.main, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()[:12]
        stamp = time.strftime("%d.%m.%Y %H:%M", time.localtime(os.path.getmtime(self.main)))
        name = " ".join(x for x in (self.project, self.version) if x)
        fmt = f", {self.update.format_text}" if self.update else ""
        return f"{name + ', ' if name else ''}{os.path.getsize(self.main):,} Byte".replace(",", ".") + \
               f", {stamp}, SHA {sha}{fmt}"


def _esp_build_dir(folder: str) -> str:
    """Ordner des Builds: der gewaehlte selbst oder, im Projektordner, .pio/build/<env>."""
    if os.path.isfile(os.path.join(folder, "flasher_args.json")):
        return folder
    hits = sorted(glob.glob(os.path.join(folder, ".pio", "build", "*", "flasher_args.json")))
    if len(hits) == 1:
        return os.path.dirname(hits[0])
    if hits:
        raise FlashError("Mehrere Builds im Ordner, bitte den gewünschten direkt wählen.")
    raise FlashError("Kein ESP-Build gefunden (flasher_args.json fehlt).")


_VERSION = re.compile(r"\d+\.\d+\.\d+")
_IMAGE_VERSION = {ZANGE: rb"fw=PyST-Zange (\d+\.\d+\.\d+)", BASIS_ESP: rb"basis-esp (\d+\.\d+\.\d+)"}


def version_matches(version: str, text: str) -> bool:
    """Steht genau diese Version im Text? Zusaetze wie '0.2.3-rc1' oder 'h12' stoeren nicht, '0.2.31' passt nicht auf '0.2.3'."""
    return re.search(rf"(?<![\d.]){re.escape(version)}(?![\d.])", text) is not None


def image_version(image: Image) -> str:
    """Firmware-Version der gewählten Datei; leer, wenn sie sich nicht auslesen lässt (Gesamtdatei, Lambda)."""
    if image.target == BASIS_STM:
        return image.version
    if image.target == BASIS_STLINK:          # Gesamtdatei: Version steht in der Update-Datei daneben
        try:
            return load_update_image(image.folder).version
        except (FlashError, OSError, ValueError):
            return ""
    pattern = _IMAGE_VERSION.get(image.target)
    if pattern is None:
        return ""
    with open(image.main, "rb") as fh:
        hit = re.search(pattern, fh.read())
    return hit.group(1).decode() if hit else ""


def device_versions(link) -> dict:
    """Versionen, die das verbundene Gerät meldet: {Ziel: 'x.y.z'}. Lambda meldet keine Build-Version."""
    out = {}

    def put(target, text):
        hit = _VERSION.search(text or "")
        if hit:
            out[target] = hit.group(0)

    if link.is_zange_usb:
        put(ZANGE, link.caps.get("fw", ""))
    elif link.is_basis:
        put(BASIS_STM, link.caps.get("fw", ""))
        put(BASIS_STLINK, link.caps.get("fw", ""))
        lines = link.command("#NET", timeout=1.0, done=lambda x: x.startswith(("#NET;", "#ERR")))
        put(BASIS_ESP, parse_kv(lines[-1]).get("esp_fw", "") if lines else "")
        for z in link.zangen_status():
            if z.id:
                put(ZANGE, z.fw)
                break
    return out


def version_text(current: str, new: str, stale: bool = False) -> Tuple[str, str]:
    """-> (Text, Zustand) für die Anzeige. Zustand: neu, gleich, aelter oder leer."""
    left = f"Auf dem Gerät: {current}{' (zuletzt gesehen)' if stale else ''}" if current else \
        "Auf dem Gerät: unbekannt (Gerät verbinden)"
    right = f"Neu: {new}" if new else "Neu: Version nicht lesbar"
    if not current or not new:
        return f"{left}  →  {right}", ""
    a, b = (tuple(int(x) for x in v.split(".")) for v in (current, new))
    state = "neu" if b > a else "gleich" if b == a else "aelter"
    note = {"neu": "Update verfügbar", "gleich": "schon aktuell", "aelter": "Datei ist älter als das Gerät"}[state]
    return f"{left}  →  {right}  ({note})", state


def load_esp_image(target: str, folder: str) -> Image:
    t = TARGETS[target]
    build = _esp_build_dir(folder)
    with open(os.path.join(build, "flasher_args.json"), encoding="utf8") as fh:
        args = json.load(fh)
    project, version = "", ""
    desc = os.path.join(build, "project_description.json")
    if os.path.isfile(desc):
        with open(desc, encoding="utf8") as fh:
            d = json.load(fh)
        project, version = d.get("project_name", ""), d.get("project_version", "")
        if d.get("target", "esp32c3") != "esp32c3":
            raise FlashError(f"Build ist für {d.get('target')}, nicht für ESP32-C3.")
    if t.project not in project:
        raise FlashError(f"Falsche Firmware: Der Build heißt „{project or '?'}“, für {t.name} muss es „{t.project}“ sein.")
    files = []
    for part, name in _BUILD_FILES.items():
        offset = args.get(part, {}).get("offset")
        path = os.path.join(build, name)
        if offset is None or not os.path.isfile(path):
            raise FlashError(f"Build unvollständig: {name} fehlt.")
        files.append((int(offset, 16), path))
    fs = args.get("flash_settings", {})
    flash_args = ["--flash_mode", fs.get("flash_mode", "dio"), "--flash_size", fs.get("flash_size", "4MB"),
                  "--flash_freq", fs.get("flash_freq", "80m")]
    return Image(target, build, project, version, sorted(files), flash_args, os.path.join(build, "firmware.bin"))


def _find_file(folder: str, names: Tuple[str, ...], missing: str) -> str:
    """Datei selbst, im Ordner oder im Projektordner unter .pio/build/<env>."""
    if os.path.isfile(folder):
        return folder
    for name in names:
        hits = sorted(glob.glob(os.path.join(glob.escape(folder), name)))
        hits = hits or sorted(glob.glob(os.path.join(glob.escape(folder), ".pio", "build", "*", name)))
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise FlashError("Mehrere passende Dateien im Ordner, bitte die gewünschte direkt wählen.")
    raise FlashError(missing)


def load_update_image(folder: str) -> Image:
    path = _find_file(folder, ("*.pystfw",), "Keine Update-Datei (.pystfw) gefunden.")
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        upd = bp.UpdateFile.parse(data)
    except ValueError as exc:
        raise FlashError(str(exc))
    return Image(BASIS_STM, os.path.dirname(path), "PyST-Basis", upd.version, main=path, update=upd)


def load_stm_image(folder: str) -> Image:
    """ST-Link: Datei fuer 0x08000000, also Gesamtdatei. Die Anwendung allein liefe dort nicht."""
    path = _find_file(folder, (GESAMT_NAME, "firmware.bin"), "Keine Gesamtdatei gefunden.")
    with open(path, "rb") as fh:
        data = fh.read()
    size = len(data)
    if size < 8 or size > STM_FLASH_MAX:
        raise FlashError(f"Größe {size} Byte passt nicht zu U10.")
    if data[0] == ESP_IMAGE_MAGIC:
        raise FlashError("Falsche Firmware: Das ist ein ESP-Abbild, kein STM32.")
    if data[:8] == bp.UPD_MAGIC:
        raise FlashError("Falsche Firmware: Update-Datei, die gehört zu „Basis STM32“ per USB.")
    sp, reset = struct.unpack_from("<II", data)
    if not STM_SRAM[0] < sp <= STM_SRAM[1] or not STM_FLASH_ADDR < reset < STM_FLASH_ADDR + STM_FLASH_MAX:
        raise FlashError("Falsche Firmware: Die Vektortabelle passt nicht zum STM32G473.")
    if reset >= bp.APP_BASE:
        raise FlashError(f"Falsche Firmware: Das ist die Anwendung allein (für {bp.APP_BASE:#x}). An 0x08000000 gehört "
                         f"die Gesamtdatei {GESAMT_NAME} mit Bootloader.")
    project = "ohne Bootloader"
    if data[bp.BOOT_SIZE + bp.IMG_HDR_OFS:bp.BOOT_SIZE + bp.IMG_HDR_OFS + 4] == struct.pack("<I", bp.IMG_MAGIC):
        project = "Bootloader + Anwendung"
    return Image(BASIS_STLINK, os.path.dirname(path), project, files=[(STM_FLASH_ADDR, path)], main=path)


def parse_lambda_image(data: bytes) -> Tuple[bytes, bool]:
    """Datei -> (Anwendung, Bootloader war dabei). Alles, was der F042-Bootloader nicht starten wuerde, wird abgelehnt."""
    if data[:1] == bytes((ESP_IMAGE_MAGIC,)):
        raise FlashError("Falsche Firmware: Das ist ein ESP-Abbild, kein F042.")
    if data[:8] == bp.UPD_MAGIC:
        raise FlashError("Falsche Firmware: Update-Datei, die gehört zu „Basis STM32“.")
    if len(data) == F042_BL_SIZE + F042_APP_SIZE:
        sp, reset = struct.unpack_from("<II", data)
        if not F042_RAM[0] < sp <= F042_RAM[1] or not reset & 1 or not F042_FLASH <= reset < F042_APP_BASE:
            raise FlashError("Falsche Firmware: Vorne steht kein F042-Bootloader.")
        app, with_bl = data[F042_BL_SIZE:], True
    elif len(data) == F042_APP_SIZE:
        app, with_bl = data, False
    else:
        raise FlashError(f"Falsche Firmware: Größe {len(data)} Byte, erwartet {F042_BL_SIZE + F042_APP_SIZE} (mit "
                         f"Bootloader) oder {F042_APP_SIZE} (Anwendung).")
    sp, reset = struct.unpack_from("<II", app)
    if not F042_RAM[0] < sp <= F042_RAM[1] or not reset & 1 or not F042_APP_BASE <= reset < F042_APP_END:
        raise FlashError(f"Falsche Firmware: Die Vektortabelle passt nicht zur Anwendung ab {F042_APP_BASE:#x}.")
    if zlib.crc32(app[:-4]) != int.from_bytes(app[-4:], "big"):
        raise FlashError("Datei beschädigt: Die Prüfsumme der Anwendung stimmt nicht, der F042 würde sie nicht starten.")
    return app, with_bl


def load_lambda_image(folder: str) -> Image:
    path = _find_file(folder, F042_NAMES, "Keine Wideband-Datei gefunden (wideband_image_with_bl.bin).")
    with open(path, "rb") as fh:
        data = fh.read()
    app, with_bl = parse_lambda_image(data)
    project = "rusEFI-Wideband " + ("(Anwendung wird herausgeschnitten)" if with_bl else "(nur Anwendung)")
    return Image(LAMBDA_F042, os.path.dirname(path), project, main=path, lambda_app=app)


def load_image(target: str, folder: str) -> Image:
    if not folder:
        raise FlashError("Noch kein Ordner gewählt.")
    if target == LAMBDA_F042:
        return load_lambda_image(folder)
    if target == BASIS_STM:
        return load_update_image(folder)
    if target == BASIS_STLINK:
        return load_stm_image(folder)
    return load_esp_image(target, folder)


# ---------------------------------------------------------------------------------------------- Werkzeuge
def find_esptool() -> Optional[List[str]]:
    """esptool als Python-Modul (requirements.txt), sonst aus dem PATH oder aus PlatformIO."""
    if importlib.util.find_spec("esptool") is not None:
        return [sys.executable, "-m", "esptool"]
    for name in ("esptool", "esptool.py"):
        exe = shutil.which(name)
        if exe:
            return [exe]
    for path in glob.glob(os.path.expanduser("~/.platformio/packages/tool-esptool/esptool")):
        return [path]
    return None


def find_stflash() -> Optional[List[str]]:
    exe = shutil.which("st-flash") or next((p for p in ("/opt/homebrew/bin/st-flash", "/usr/local/bin/st-flash")
                                            if os.path.isfile(p)), None)
    return [exe] if exe else None


def esp_command(tool: List[str], port: str, image: Image) -> List[str]:
    baud = TARGETS[image.target].baud
    cmd = tool + ["--chip", "esp32c3", "--port", port, "--baud", str(baud), "--before", "default_reset",
                  "--after", "hard_reset", "write_flash"] + image.flash_args
    for addr, path in image.files:
        cmd += [hex(addr), path]
    return cmd


def stm_command(tool: List[str], image: Image) -> List[str]:
    addr, path = image.files[0]
    return tool + ["--connect-under-reset", "--reset", "write", path, hex(addr)]


# ---------------------------------------------------------------------------------------------- Ports
def zange_port() -> Optional[str]:
    if list_ports is None:
        return None
    return next((p.device for p in list_ports.comports() if (p.vid, p.pid) == ZANGE_USB_ID), None)


def bridge_port() -> Optional[str]:
    if list_ports is None:
        return None
    return next((p.device for p in list_ports.comports() if (p.vid, p.pid) == BASIS_USB_ID and is_esp_bridge(p)), None)


def basis_ports() -> List[str]:
    """Messports aller Basen; Anwendung und Bootloader melden sich gleich an."""
    if list_ports is None:
        return []
    return sorted(p.device for p in list_ports.comports() if (p.vid, p.pid) == BASIS_USB_ID and not is_esp_bridge(p))


def basis_port() -> Optional[str]:
    ports = basis_ports()
    return ports[0] if len(ports) == 1 else None


def target_port(target: str) -> Optional[str]:
    return {ZANGE: zange_port, BASIS_ESP: bridge_port, BASIS_STM: basis_port,
            LAMBDA_F042: basis_port}.get(target, lambda: None)()


def build_command(target: str, folder: str, port: Optional[str]) -> Tuple[Image, List[str]]:
    """Alle Pruefungen; -> (Abbild, Befehlszeile) oder FlashError mit Klartext."""
    image = load_image(target, folder)
    if target in (BASIS_STM, LAMBDA_F042):
        return image, []                  # kein externes Werkzeug: UsbFlasher, LambdaFlasher
    if target == BASIS_STLINK:
        tool = find_stflash()
        if tool is None:
            raise FlashError("st-flash nicht gefunden (Mac: brew install stlink, Linux: apt install stlink-tools).")
        return image, stm_command(tool, image)
    tool = find_esptool()
    if tool is None:
        raise FlashError("esptool nicht gefunden (pip install -r requirements.txt).")
    if not port:
        raise FlashError("Zange nicht am USB gefunden (303A:1001)." if target == ZANGE
                         else "ESP-Brücke der Basis nicht gefunden: STM32-Firmware geflasht und USB-C an J20?")
    return image, esp_command(tool, port, image)


# ---------------------------------------------------------------------------------------------- USB-Update
class FlashCancelled(FlashError):
    pass


def open_serial(port: str):
    return serial.Serial(port, 115200, timeout=0.05, write_timeout=2)


def esp_bridge_on(port: Optional[str], timeout: float = 2.0):
    """Bruecke zum Basis-ESP einschalten: '#ESP BRIDGE 1' am Messport, nur per USB. Danach wartet die Basis 30 s auf
    esptool, also erst unmittelbar vor dem Start aufrufen. Lehnt die Basis ab, FlashError mit Klartext."""
    if not port:
        raise FlashError("Messport der Basis nicht gefunden: Den Basis-ESP flasht PyST nur per USB.")
    try:
        ser = open_serial(port)
    except OSError as exc:
        raise FlashError(f"{port} lässt sich nicht öffnen: {exc}") from exc
    try:
        ser.write(b"#ESP BRIDGE 1\r\n")
        buf = b""
        end = time.time() + timeout
        while time.time() < end:
            buf += ser.read(256)
            lines = buf.split(b"\n")
            buf = lines.pop()
            for raw in lines:
                line = raw.decode("ascii", "replace").strip()
                if line.startswith("#OK;ESP;BRIDGE;1"):
                    return
                if line.startswith("#ERR;ESP"):
                    raise FlashError(ESP_BUSY if line.endswith("messung laeuft") else refusal_text(line))
                if line.startswith("#ERR;?"):
                    raise FlashError("Diese Basis-Firmware kennt die ESP-Brücke nicht: Erst die Basis aktualisieren.")
    except OSError as exc:
        raise FlashError(f"Verbindung zur Basis abgerissen: {exc}") from exc
    finally:
        ser.close()
    raise FlashError("Die Basis antwortet nicht auf das Einschalten der ESP-Brücke.")


class UsbFlasher:
    """Update der Basis ueber den Bootloader. run() blockiert (eigener Thread) und liefert die neue Version.
    Der Port wird immer neu gesucht: Sein Name kann sich beim Wechsel Anwendung/Bootloader aendern. Alle
    Ein-/Ausgaben laufen ueber `ports` und `opener`, damit Tests den Simulator einsetzen koennen."""
    WAIT_BOOT_S = 15.0        # Abmelden, Reset, Anmelden
    WAIT_APP_S = 60.0         # Reset, Kopieren (224 KB: etwa 8 s), Start
    START_S = 20.0            # Loeschen des Zwischenspeichers, im Geraet hoechstens 4,5 s
    END_S = 10.0
    REPLY_S = 1.0
    RETRIES = 5

    def __init__(self, update: bp.UpdateFile, ports: Callable[[], List[str]] = basis_ports,
                 opener: Callable[[str], object] = open_serial, log: Callable[[str], None] = lambda _t: None,
                 progress: Callable[[int, int], None] = lambda _d, _n: None,
                 cancelled: Callable[[], bool] = lambda: False):
        self.update = update
        self.ports = ports
        self.opener = opener
        self.log = log
        self.progress = progress
        self.cancelled = cancelled
        self.ser = None
        self.dec = bp.FrameDecoder()
        self.rx = deque()
        self.seq = 0
        self.leaving_app = False          # nach #UPDATE: Abbrechen erst im Bootloader, sonst bliebe die Basis dort

    # --- Ablauf
    def run(self) -> str:
        try:
            ports = self.ports()
            if len(ports) > 1:
                raise FlashError("Mehrere Basen am USB, bitte nur eine anstecken.")
            if not ports:
                raise FlashError("Basis nicht am USB gefunden (1209:0001): 12 V an, USB-C an J20?")
            kind, info = self._identify(ports[0])
            if kind == "app":
                self.log(f"Anwendung läuft ({info}), fordere den Bootloader an.")
                self.leaving_app = True
                self._request_update()
                kind, info = self._wait_for("boot", self.WAIT_BOOT_S)
            elif kind == "boot":
                self.log("Gerät steckt schon im Bootloader.")
            else:
                raise FlashError(f"Keine Antwort von {ports[0]}.")
            self._transfer(info)
            return self._wait_app()
        finally:
            self._close()

    def _close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except (OSError, ValueError):
                pass
            self.ser = None

    def _read(self) -> bytes:
        return self.ser.read(self.ser.in_waiting or 1)   # kehrt beim ersten Byte zurueck, nicht erst nach dem Timeout

    def _check_cancel(self):
        if self.cancelled() and not self.leaving_app:
            raise FlashCancelled("Abgebrochen.")

    # --- Erkennen
    def _identify(self, port: str) -> Tuple[Optional[str], object]:
        """Oeffnet den Port und fragt '#?'. -> ('app', Firmware) | ('boot', Felder) | (None, None)"""
        self._close()
        try:
            self.ser = self.opener(port)
            for _ in range(3):
                self.ser.reset_input_buffer()
                self.ser.write(b"#?\n")
                end = time.time() + 0.5
                buf = b""
                while time.time() < end:
                    buf += self._read()
                    for raw in buf.split(b"\n")[:-1]:
                        line = raw.decode("latin-1").strip()
                        if line.startswith("#BOOTLOADER"):
                            return "boot", bp.parse_info(line)
                        if line.startswith("#CAP"):
                            return "app", bp.parse_info(line).get("fw", "")
                    self._check_cancel()
        except (OSError, ValueError) as exc:         # SerialException ist ein OSError
            self.log(f"{port}: {exc}")
        self._close()
        return None, None

    def _request_update(self):
        self.ser.reset_input_buffer()
        self.ser.write(b"#UPDATE\n")
        end = time.time() + 2.0
        buf = b""
        try:
            while time.time() < end and b"#OK;UPDATE" not in buf and b"#ERR" not in buf:   # Telegramme laufen evtl. weiter
                buf += self._read()
        except OSError:                              # meldet sich gleich nach der Antwort ab
            pass
        text = buf.decode("latin-1")
        self._close()
        if "#OK;UPDATE" not in text:
            if "#ERR" in text:
                raise FlashError("Diese Firmware hat keinen Update-Befehl: Einmalig per ST-Link die Gesamtdatei flashen.")
            raise FlashError("Keine Antwort auf #UPDATE.")

    def _wait_for(self, want: str, seconds: float) -> Tuple[str, object]:
        end = time.time() + seconds
        while time.time() < end:
            self._check_cancel()
            ports = self.ports()
            if len(ports) == 1:
                kind, info = self._identify(ports[0])
                if kind == want:
                    return kind, info
                self._close()
            time.sleep(0.2)
        raise FlashError("Bootloader meldet sich nicht." if want == "boot" else "Anwendung meldet sich nicht.")

    # --- Rahmen
    def _resp(self) -> Optional[Tuple[int, int, int, str]]:
        """Naechste Antwort auf den laufenden Befehl oder None. Ein Lesevorgang kann mehrere Rahmen liefern."""
        if not self.rx:
            self.rx.extend(self.dec.feed(self._read()))
        while self.rx:
            k, seq, body = self.rx.popleft()
            if k == bp.BL_RESP and seq == self.seq:
                return bp.parse_resp(body)
        return None

    def _ask(self, kind: int, data: bytes = b"", timeout: float = REPLY_S, retries: int = RETRIES) -> Tuple[int, int, str]:
        """Befehl mit Wiederholung; -> (status, wert, text). Antwort BUSY verlaengert die Wartezeit."""
        self.seq = (self.seq + 1) & 0xFF
        frame = bp.encode_frame(kind, self.seq, data)
        for _ in range(retries + 1):
            try:
                self.ser.write(frame)
                end = time.time() + timeout
                while time.time() < end:
                    r = self._resp()
                    if r is not None and r[0] == kind:
                        return r[1:]
            except OSError as exc:
                raise FlashError(f"Verbindung verloren: {exc}. Nochmals flashen, die Basis wartet im Bootloader.")
        raise FlashError("Bootloader antwortet nicht mehr. Nochmals flashen, die Basis wartet im Bootloader.")

    def _ok(self, status: int, what: str):
        if status != bp.BL_OK:
            raise FlashError(f"{what}: {bp.STATUS_TEXT.get(status, status)}.")

    def _start(self):
        """Loeschen dauert Sekunden: Das Geraet antwortet erst BUSY, dann OK auf dieselbe seq."""
        status, _value, _text = self._ask(bp.BL_START, self.update.header)
        end = time.time() + self.START_S
        while status == bp.BL_BUSY and time.time() < end:
            self._check_cancel()
            try:
                r = self._resp()
            except OSError as exc:
                raise FlashError(f"Verbindung verloren: {exc}.")
            if r is not None and r[0] == bp.BL_START:
                status = r[1]
        if status == bp.BL_BUSY:
            raise FlashError("Bootloader wird mit dem Löschen nicht fertig.")
        self._ok(status, "Start abgelehnt")

    def _transfer(self, info: dict):
        u = self.update
        if info.get("target", u.target) != u.target or info.get("board", u.board) != u.board:
            raise FlashError(f"Falsche Firmware: Die Basis ist {info.get('target')} {info.get('board')}.")
        self.log(f"Bootloader {info.get('bl', '?')}, Anwendung {info.get('app', '?')}, Grund {info.get('grund', '?')}.")
        self.ser.reset_input_buffer()
        self.ser.write(b"\0")                        # Text von vorher abschliessen
        self.dec = bp.FrameDecoder()
        self.rx.clear()
        status, value, _text = self._ask(bp.BL_PING)
        if status != bp.BL_OK or value != 1:
            raise FlashError(f"Bootloader spricht Protokoll {value}, dieses PyST 1.")
        self.leaving_app = False
        try:
            self._check_cancel()
            self.log("Lösche Zwischenspeicher …")
            self._start()
            self.log(f"Übertrage {u.image_len} Byte …")
            off = 0
            while off < u.image_len:
                self._check_cancel()
                block = u.payload[off:off + bp.BLOCK]
                status, value, _text = self._ask(bp.BL_DATA, struct.pack("<I", off) + block)
                if status == bp.BL_E_OFFSET:         # Geraet ist woanders: dort weitermachen
                    self.log(f"Wiederhole ab {value}.")
                elif status != bp.BL_OK:
                    self._ok(status, f"Block {off}")
                off = value
                self.progress(off, u.image_len)
            self.log("Prüfe im Gerät …")
            self._ok(self._ask(bp.BL_END, timeout=self.END_S, retries=1)[0], "Prüfung")
        except FlashCancelled:
            self._ask(bp.BL_ABORT)
            self._ask(bp.BL_BOOT)                    # alte Anwendung wieder starten
            raise
        self.log("Geprüft, Basis kopiert und startet neu …")
        self._ok(self._ask(bp.BL_BOOT)[0], "Neustart")
        self._close()

    def _wait_app(self) -> str:
        time.sleep(0.5)
        end = time.time() + self.WAIT_APP_S
        while time.time() < end:
            ports = self.ports()
            if len(ports) == 1:
                kind, info = self._identify(ports[0])
                if kind == "boot":
                    raise FlashError(f"Basis bleibt im Bootloader ({info.get('grund', '?')}). Nochmals flashen, sonst "
                                     "ST-Link nötig.")
                if kind == "app":
                    self._close()
                    if not version_matches(self.update.version, str(info)):
                        raise FlashError(f"Falsche Version: Erwartet {self.update.version}, gemeldet „{info}“.")
                    self.log(f"Anwendung läuft: {info}.")
                    return self.update.version
            time.sleep(0.2)
        raise FlashError("Anwendung meldet sich nicht zurück. Basis einmal stromlos machen; beginnt sie von selbst "
                         "zu kopieren, abwarten.")


# ---------------------------------------------------------------------------------------------- Lambda per CAN
WBF_TEXT = {
    "ausgabe": "Die Basis gibt gerade Messdaten aus (USB, WLAN oder Debug): Erst die Messung stoppen.",
    "lambda": "Die Lambda-Heizung ist an: Erst ausschalten (#LAMBDA 0).",
    "heizung": "Die Lambda-Messung ist angefordert oder der F042 läuft: Heizung und Automatik (lambda_heat) ausschalten.",
    "swd": "Der F042 ist gerade für SWD freigegeben (#LAMBDA SWD): 2 Minuten warten oder Basis neu starten.",
    "esp": "Die ESP-Brücke ist aktiv: Erst den ESP fertig flashen.",
    "nur_usb": "Das Update geht nur über USB.",
    "laeuft": "Es läuft schon ein Update.",
    "kein_bootloader": "Der F042 antwortet nicht: 12 V an? F042-Bootloader vorhanden (sonst ST-Link an J902)?",
    "erase_keine_antwort": "Der F042 bestätigt das Löschen nicht.",
    "daten_keine_antwort": "Der F042 bestätigt die Daten nicht.",
    "reboot_keine_antwort": "Der F042 bestätigt den Neustart nicht.",
    "can_voll": "CAN-Sendepuffer voll: Der F042 ist nicht mehr am Bus.",
    "doppelte_antwort": "Doppelte Antwort vom F042.",
    "0x190_im_bootloader": "Der F042 hat den Bootloader verlassen.",
    "gesamt_crc": "Prüfsumme über das ganze Abbild stimmt nicht.",
    "keine_app": "Die neue Anwendung meldet sich nicht (keine 0x190-Telegramme).",
    "pc_schweigt": "Die Basis hat wegen Zeitüberschreitung abgebrochen.",
    "usb_weg": "USB-Verbindung war weg.",
}


class LambdaFlasher:
    """Update des F042 ueber die Basis: PC --USB--> U10 --FDCAN3--> F042-Bootloader, Befehle #WBF (Gegenstueck wbflash.c).
    Die Basis prueft die Antworten des F042 und meldet jeden Block; nach dem Update prueft sie, dass 0x190 mit der
    richtigen Version wiederkommen, und haelt den F042 danach wieder im Reset. run() blockiert (eigener Thread)."""
    INFO_S = 1.0
    START_S = 6.0             # Reset, Fenster, zweites ENTER: im Geraet hoechstens 3 s
    ERASE_S = 5.0             # im Geraet hoechstens 3 s
    BLOCK_S = 1.0
    END_S = 4.0               # im Geraet hoechstens 1,5 s
    RETRIES = 3

    def __init__(self, app: bytes, ports: Callable[[], List[str]] = basis_ports,
                 opener: Callable[[str], object] = open_serial, log: Callable[[str], None] = lambda _t: None,
                 progress: Callable[[int, int], None] = lambda _d, _n: None,
                 cancelled: Callable[[], bool] = lambda: False):
        self.app = app
        self.ports = ports
        self.opener = opener
        self.log = log
        self.progress = progress
        self.cancelled = cancelled
        self.ser = None
        self.buf = b""
        self.started = False                         # #WBF START geschickt, noch nicht fertig
        self.erased = False

    # --- Ablauf
    def run(self) -> str:
        try:
            ports = self.ports()
            if len(ports) > 1:
                raise FlashError("Mehrere Basen am USB, bitte nur eine anstecken.")
            if not ports:
                raise FlashError("Basis nicht am USB gefunden (1209:0001): 12 V an, USB-C an J20?")
            self.ser = self.opener(ports[0])
            self._check_basis(ports[0])
            self.log("Halte den F042 im Bootloader fest …")
            self.started = True
            self._step("#WBF START", "#OK;WBF;START", self.START_S)
            self._check_cancel()
            self.log("F042-Bootloader meldet sich. Lösche die Anwendung …")
            self.erased = True
            self._step("#WBF ERASE", "#OK;WBF;ERASE", self.ERASE_S)
            self.log(f"Übertrage {len(self.app)} Byte …")
            self._transfer()
            self.log("Prüfe im F042, starte die Anwendung …")
            line = self._step(f"#WBF END {zlib.crc32(self.app):08X}", "#OK;WBF;END", self.END_S)
            ver = bp.parse_info(line).get("ver", "?")
            if ver != WB_VERSION:
                raise FlashError(f"Falsche Version: Erwartet {WB_VERSION}, gemeldet {ver}.")
            self.started = False
            self.log(f"0x190-Telegramme mit Version {ver} sind wieder da. Basis hält den F042 wieder im Reset.")
            return ver
        except OSError as exc:                       # SerialException ist ein OSError
            raise FlashError(f"Verbindung verloren: {exc}. Die Basis bricht von selbst ab. " + self._retry_hint())
        finally:
            if self.ser is not None:
                try:
                    if self.started:                 # Sofort zurueck in den Reset, nicht erst nach 5 s Schweigen
                        self._send("#WBF ABORT")
                    self.ser.close()
                except (OSError, ValueError):
                    pass
                self.ser = None

    def _retry_hint(self) -> str:
        if self.erased:
            return "Die Anwendung ist gelöscht, der F042-Bootloader wartet: Nochmals flashen."
        return "Die Anwendung des F042 ist unberührt."

    def _check_cancel(self):
        if self.cancelled():
            self._send("#WBF ABORT")
            try:
                self._wait(("#OK;WBF;ABORT",), self.INFO_S)
            except FlashError:
                pass                                 # sonst bricht die Basis nach 5 s selbst ab
            raise FlashCancelled("Abgebrochen. " + self._retry_hint())

    # --- Zeilen
    def _send(self, line: str):
        self.ser.write(line.encode("ascii") + b"\n")

    def _lines(self):
        self.buf += self.ser.read(self.ser.in_waiting or 1)   # kehrt beim ersten Byte zurueck
        *done, self.buf = self.buf.split(b"\n")
        return [raw.decode("latin-1").strip() for raw in done]

    def _wait(self, wanted: Tuple[str, ...], seconds: float, cancel: bool = False) -> Optional[str]:
        """Erste Zeile mit einem dieser Anfaenge oder None. Telegramme und Ereigniszeilen fallen weg."""
        end = time.time() + seconds
        while time.time() < end:
            for line in self._lines():
                if line.startswith(wanted):
                    return line
            if cancel:
                self._check_cancel()
        return None

    def _error(self, line: str):
        """#ERR;WBF;<grund>;app=…"""
        parts = line.split(";")
        why = parts[2] if len(parts) > 2 else "?"
        text = WBF_TEXT.get(why, why)
        info = bp.parse_info(line)
        if "ver" in info:
            text += f" Der F042 meldet Version {info['ver']}."
        if self.erased:
            text += " " + self._retry_hint()
        raise FlashError(text)

    def _step(self, cmd: str, ok: str, seconds: float) -> str:
        self._send(cmd)
        line = self._wait((ok, "#ERR;WBF"), seconds, cancel=not cmd.startswith("#WBF END"))
        if line is None:
            raise FlashError(f"Basis antwortet nicht auf {cmd.split()[1]}. " + self._retry_hint())
        if line.startswith("#ERR"):
            self._error(line)
        return line

    # --- Pruefungen
    def _check_basis(self, port: str):
        self.ser.reset_input_buffer()
        self._send("#?")
        line = self._wait(("#CAP", "#BOOTLOADER"), self.INFO_S)
        if line is None:
            raise FlashError(f"Keine Antwort von {port}.")
        if line.startswith("#BOOTLOADER"):
            raise FlashError("Die Basis steckt im Bootloader: Erst „Basis STM32“ flashen.")
        self.log(f"Basis: {bp.parse_info(line).get('fw', '?')}.")
        self._send("#RUN 0")                         # PyST ist getrennt: eigene Ausgabe auf diesem Port beenden
        self._wait(("#OK;RUN",), self.INFO_S)
        self._send("#WBF")
        line = self._wait(("#WBF;", "#ERR;?"), self.INFO_S)
        if line is None or line.startswith("#ERR"):
            raise FlashError("Diese Basis-Firmware kennt das Lambda-Update nicht: Erst „Basis STM32“ auf 0.2.1 oder neuer "
                             "flashen.")

    # --- Daten
    def _transfer(self):
        off = 0
        total = len(self.app)
        fails = 0
        while off < total:
            self._check_cancel()
            block = self.app[off:off + WBF_BLOCK]
            self._send(f"#WBF D {off:X} {block.hex()} {zlib.crc32(block):08X}")
            line = self._wait(("#OK;WBF;D;", "#ERR;WBF"), self.BLOCK_S)
            parts = line.split(";") if line else []
            if line is not None and line.startswith("#OK"):
                off = int(parts[3])
                fails = 0
                self.progress(off, total)
            elif line is not None and parts[2] not in ("crc", "format", "ofs"):
                self._error(line)                    # die Basis hat abgebrochen
            else:
                fails += 1                           # Antwort oder Zeile kaputt: Basis wartet weiter
                if fails > self.RETRIES:
                    raise FlashError("Basis nimmt keine Blöcke mehr an. " + self._retry_hint())
                if line is not None:
                    off = int(parts[3])
