"""
Firmware flashen: Zange (ESP32-C3 per USB: Update-Datei ueber #UPD, Erstflash mit esptool), Basis-STM32 (per USB ueber den eigenen Bootloader, reines Python; fuer die
Erstprogrammierung ST-Link an J10), Basis-ESP (Update-Datei ueber #ESPUPD am Messport, Erstflash mit esptool ueber die
ESP-Bruecke), Lambda-Controller F042 (rusEFI-Wideband, ueber die Basis per CAN, LambdaFlasher).

Dieses Repository enthaelt keine Firmware der eigenen Hardware (PyST-HW ist nicht oeffentlich): man waehlt einen
Ordner mit dem Build. Vor dem Flashen wird geprueft, ob der Build zum Ziel passt, damit keine Zangen-Firmware auf dem
Basis-ESP landet (beides ESP32-C3). Die Adressen stammen aus dem Build (flasher_args.json), nicht aus diesem Code.
Hier Pruefen, Befehlszeilen bauen und der Ablauf der USB-Updates (UsbFlasher Basis, ZangeFlasher Zange, BasisEspFlasher
Basis-ESP); esptool und st-flash startet flash_page.py.
Aeltere Messplatinen (am Ende der Datei): Arduino Mega 2560 mit avrdude ueber den Arduino-Bootloader, STM32 F446
(Nucleo-64) durch Kopieren auf das Laufwerk NODE_F446RE (F446Flasher), ersatzweise st-flash oder dfu-util.
"""
import base64
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
from . import fw_keys
from .basis import ZANGE_USB_ID
from .diag import parse_kv
from .auth import refusal_text
from .link import BASIS_USB_ID, is_esp_bridge

ZANGE, BASIS_STM, BASIS_STLINK, BASIS_ESP, LAMBDA_F042 = "zange", "basis_stm", "basis_stlink", "basis_esp", "lambda_f042"
ZANGE_ESPTOOL = "zange_esptool"           # Erstflash und Entwickler: Build-Ordner mit esptool
BASIS_ESP_BRIDGE = "basis_esp_bruecke"    # ebenso fuer den Basis-ESP, ueber die ESP-Bruecke der Basis
# Ziel im Kopf der Update-Datei (tools/pyst_image.py), Schluessel wie im Manifest (update.FILE_TARGETS)
FILE_TARGET = {BASIS_STM: bp.TARGET, ZANGE: bp.TARGET_ZANGE, BASIS_ESP: bp.TARGET_BASIS_ESP}
NO_USB_AT_ENGINE = ("Nie am laufenden Motor per USB: Die Zange ist dann galvanisch mit dem PC verbunden, Zündspannung "
                    "kann PC und Zange zerstören.")
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
    ZANGE: Target(ZANGE, "Zange per USB (Update-Datei)", "pyst-zange", 0,
                  "Update-Datei (.pystfw) wählen. Zange eingeschaltet, Akku drin, USB-C am PC, Zange vom Zündkabel "
                  "genommen. Keine Aufnahme, kein Blitzmodus, Basis aus. Nach dem Neustart prüft die Zange die neue "
                  "Firmware 15 s: so lange nicht ausschalten, sonst kehrt sie zur alten zurück. Ab Zangen-Firmware "
                  "0.4.0, ältere Zangen einmal per Erstflash. " + NO_USB_AT_ENGINE),
    ZANGE_ESPTOOL: Target(ZANGE_ESPTOOL, "Zange Erstflash per esptool (Build-Ordner)", "pyst-zange", 921600,
                          "Für neue Zangen und einmal für 0.4.0 (neuer Bootloader): Ordner mit dem Build wählen. Akku "
                          "drin, USB-C an der Zange, T1 bis zum Ende halten. Bleibt die Zange nicht im Download-Modus: "
                          "zuerst T2, dann T1 drücken und beide halten; danach neu einschalten. " + NO_USB_AT_ENGINE),
    BASIS_STM: Target(BASIS_STM, "Basis STM32", "", 0,
                      "12 V an, USB-C an J20, nur eine Basis anstecken. Update-Datei (.pystfw) wählen. Einstellungen "
                      "bleiben erhalten. Fällt der Strom aus, setzt die Basis beim nächsten Start selbst fort."),
    BASIS_STLINK: Target(BASIS_STLINK, "Basis STM32 per ST-Link (Erstprogrammierung)", "", 0,
                         "Nur für neue Platinen: Gesamtdatei (Bootloader + Anwendung) über ST-Link an J10, 12 V an X1. "
                         "Danach 12 V einmal aus und ein: der ST-Link lässt den STM32 sonst angehalten, die Basis fällt "
                         "vom USB."),
    BASIS_ESP: Target(BASIS_ESP, "Basis-ESP per USB (Update-Datei)", "basis-esp32c3", 0,
                      "Update-Datei (.pystfw) wählen. 12 V an, USB-C an J20, nur eine Basis anstecken, keine Messung. "
                      "Geht über den Messport der Basis (Basis und Basis-ESP ab 0.4.0), nur per USB, nicht über WLAN. "
                      "Nach dem Neustart prüft der Basis-ESP die neue Firmware 30 s mit stehendem Link: so lange die "
                      "Basis eingeschaltet lassen, sonst kehrt er zur alten zurück. Ältere Basis-ESP einmal per "
                      "Erstflash über die Brücke."),
    BASIS_ESP_BRIDGE: Target(BASIS_ESP_BRIDGE, "Basis-ESP Erstflash per Brücke (Build-Ordner)", "basis-esp32c3", 460800,
                             "Für neue Platinen und einmal für Basis-ESP 0.4.0 (ältere können kein Update über den "
                             "STM): Ordner mit dem Build wählen. 12 V an, STM32-Firmware läuft, USB-C an J20, keine "
                             "Messung. Geht nur per USB über die ESP-Brücke der Basis, PyST schaltet sie ein. Der "
                             "WLAN-/Funkbetrieb ruht währenddessen."),
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
    warning: str = ""                                               # darf weiter, aber deutlich anzeigen

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
_IMAGE_VERSION = {ZANGE_ESPTOOL: rb"fw=PyST-Zange (\d+\.\d+\.\d+)", BASIS_ESP_BRIDGE: rb"basis-esp (\d+\.\d+\.\d+)"}


def version_matches(version: str, text: str) -> bool:
    """Steht genau diese Version im Text? Zusaetze wie '0.2.3-rc1' oder 'h12' stoeren nicht, '0.2.31' passt nicht auf '0.2.3'."""
    return re.search(rf"(?<![\d.]){re.escape(version)}(?![\d.])", text) is not None


def image_version(image: Image) -> str:
    """Firmware-Version der gewählten Datei; leer, wenn sie sich nicht auslesen lässt (Gesamtdatei, Lambda)."""
    if image.update is not None or image.target in (MEGA, F446, F446_STLINK):   # Kopf bzw. Kennung in der Datei
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


def device_versions(link, info: str = "") -> dict:
    """Versionen, die das verbundene Gerät meldet: {Ziel: 'x.y.z'}. Lambda meldet keine Build-Version.
    Mega und F446: aus '#CAP' (fw=PST-Mega 3.1.0 …) oder der Antwort auf 'v' (info: "Version 3.1.0 vom …",
    "PST-STM32 … 1.1.0")."""
    out = {}

    def put(target, text):
        hit = _VERSION.search(text or "")
        if hit:
            out[target] = hit.group(0)

    if link.is_zange_usb:
        put(ZANGE, link.caps.get("fw", ""))
        put(ZANGE_ESPTOOL, link.caps.get("fw", ""))
    elif link.is_basis:
        put(BASIS_STM, link.caps.get("fw", ""))
        put(BASIS_STLINK, link.caps.get("fw", ""))
        lines = link.command("#NET", timeout=1.0, done=lambda x: x.startswith(("#NET;", "#ERR")))
        esp = parse_kv(lines[-1]).get("esp_fw", "") if lines else ""
        put(BASIS_ESP, esp)
        put(BASIS_ESP_BRIDGE, esp)
        for z in link.zangen_status():
            if z.id:
                put(ZANGE, z.fw)
                put(ZANGE_ESPTOOL, z.fw)
                break
    else:
        text = f"{link.caps.get('fw', '')}\n{info or ''}"
        if link.is_stm:
            put(F446, text)
            put(F446_STLINK, text)
        else:
            put(MEGA, text)
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


def is_esp_build(folder: str) -> bool:
    try:
        _esp_build_dir(folder)
        return True
    except (FlashError, OSError):
        return False


def load_update_image(folder: str, target: str = BASIS_STM) -> Image:
    """Update-Datei (.pystfw) fuer Basis-STM, Zange oder Basis-ESP: formal, Ziel im Kopf und Signatur (fw_keys.check_local).
    Den Inhalt prueft erst das Geraet."""
    path = _find_file(folder, ("*.pystfw",), "Keine Update-Datei (.pystfw) gefunden.")
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        upd = bp.UpdateFile.parse(data, FILE_TARGET[target])
        warning = fw_keys.check_local(upd)
    except ValueError as exc:
        raise FlashError(str(exc))
    project = {ZANGE: "PyST-Zange", BASIS_ESP: "PyST-Basis-ESP"}.get(target, "PyST-Basis")
    return Image(target, os.path.dirname(path), project, upd.version, main=path, update=upd, warning=warning)


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
    if target == MEGA:
        return load_mega_image(folder)
    if target in (F446, F446_STLINK):
        return load_f446_image(folder)
    if target == LAMBDA_F042:
        return load_lambda_image(folder)
    if target in FILE_TARGET:
        return load_update_image(folder, target)
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
    return {ZANGE: zange_port, ZANGE_ESPTOOL: zange_port, BASIS_ESP: basis_port, BASIS_ESP_BRIDGE: bridge_port,
            BASIS_STM: basis_port, LAMBDA_F042: basis_port, MEGA: mega_port, F446: f446_port,
            F446_STLINK: f446_port}.get(target, lambda: None)()


def build_command(target: str, folder: str, port: Optional[str]) -> Tuple[Image, List[str]]:
    """Alle Pruefungen; -> (Abbild, Befehlszeile) oder FlashError mit Klartext."""
    if target in (MEGA, F446, F446_STLINK):
        return build_command_alt(target, folder, port)
    image = load_image(target, folder)
    if target in (BASIS_STM, ZANGE, BASIS_ESP, LAMBDA_F042):
        return image, []                  # kein externes Werkzeug: UsbFlasher, ZangeFlasher, BasisEspFlasher, LambdaFlasher
    if target == BASIS_STLINK:
        tool = find_stflash()
        if tool is None:
            raise FlashError("st-flash nicht gefunden (Mac: brew install stlink, Linux: apt install stlink-tools).")
        return image, stm_command(tool, image)
    tool = find_esptool()
    if tool is None:
        raise FlashError("esptool nicht gefunden (pip install -r requirements.txt).")
    if not port:
        raise FlashError("Zange nicht am USB gefunden (303A:1001)." if target == ZANGE_ESPTOOL
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


PROTO_FORMAT = {1: bp.FORMAT_PLAIN, 2: bp.FORMAT_SIGNED}      # Bootloader-Protokoll -> Dateiformat, das er annimmt
# Abgelehnt, bevor etwas geloescht wurde: Die Anwendung ist unberuehrt
START_REFUSED = (bp.BL_E_HEADER, bp.BL_E_TARGET, bp.BL_E_UNSUPPORTED, bp.BL_E_LENGTH, bp.BL_E_SIGNATURE, bp.BL_E_ROLLBACK)


def fit_problem(proto: int, update: bp.UpdateFile, info: dict) -> str:
    """Passt die Datei zum Bootloader? -> leer oder Klartext; Pruefung vor jeder Uebertragung."""
    bl = info.get("bl", "?")
    if proto not in PROTO_FORMAT:
        return f"Bootloader spricht Protokoll {proto}, dieses PyST 1 und 2."
    if update.format != PROTO_FORMAT[proto] and proto == 1:
        return (f"Die Datei ist signiert (Format {update.format}), Bootloader {bl} der Basis nimmt nur Format 1. "
                "Bootloader 2.0.0 kommt einmalig per ST-Link: Gesamtdatei an J10 flashen, danach geht es per USB weiter.")
    if update.format != PROTO_FORMAT[proto]:
        return (f"Die Datei ist unsigniert (Format {update.format}), Bootloader {bl} der Basis nimmt nur signierte "
                "Dateien (Format 2). Bootloader 2.0.0 kommt per ST-Link und ist hier schon drauf: Eine Datei im Format 2 "
                "wählen.")
    key = info.get("key", "")
    if proto == 2 and key.isdigit() and int(key) != update.key_id:
        return f"Die Datei ist mit Schlüssel {update.key_id} signiert, der Bootloader der Basis hat Schlüssel {key}."
    return ""


class UsbFlasher:
    """Update der Basis ueber den Bootloader. run() blockiert (eigener Thread) und liefert die neue Version.
    Der Port wird immer neu gesucht: Sein Name kann sich beim Wechsel Anwendung/Bootloader aendern. Alle
    Ein-/Ausgaben laufen ueber `ports` und `opener`, damit Tests den Simulator einsetzen koennen."""
    WAIT_BOOT_S = 15.0        # Abmelden, Reset, Anmelden
    WAIT_APP_S = 60.0         # Reset, Pruefen, Kopieren (224 KB: etwa 8 s), Start
    START_S = 30.0            # Signaturpruefung (mehrere Sekunden) und Loeschen (hoechstens 4,5 s) im Geraet
    START_RESEND_S = 5.0      # Geht die Schlussantwort verloren, holt die wiederholte Anfrage sie nach
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
        self.from_app = False             # PyST hat die Basis in den Bootloader geschickt

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
                self.leaving_app = self.from_app = True
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
        """Pruefen und Loeschen dauern Sekunden: Das Geraet antwortet erst BUSY, dann das Ergebnis auf dieselbe seq.
        Waehrend der Signaturpruefung kann es schweigen, darum auch die erste Antwort geduldig abwarten."""
        status, _value, _text = self._ask(bp.BL_START, self.update.header, timeout=self.REPLY_S,
                                          retries=int(self.START_S / self.REPLY_S))
        frame = bp.encode_frame(bp.BL_START, self.seq, self.update.header)
        end = time.time() + self.START_S
        resend = time.time() + self.START_RESEND_S
        while status == bp.BL_BUSY and time.time() < end:
            self._check_cancel()
            try:
                if time.time() >= resend:
                    resend += self.START_RESEND_S
                    self.ser.write(frame)
                r = self._resp()
            except OSError as exc:
                raise FlashError(f"Verbindung verloren: {exc}.")
            if r is not None and r[0] == bp.BL_START:
                status = r[1]
        if status == bp.BL_BUSY:
            raise FlashError("Bootloader wird mit Prüfen und Löschen nicht fertig.")
        if status in START_REFUSED:
            self._back_to_app()
        self._ok(status, "Start abgelehnt")

    def _back_to_app(self):
        """Nichts wurde geloescht: Hat PyST die Basis in den Bootloader geschickt, die alte Anwendung wieder starten."""
        if not self.from_app:
            return
        try:
            self._ask(bp.BL_BOOT, retries=1)
            self.log("Die vorhandene Firmware bleibt, die Basis startet sie wieder.")
        except FlashError:
            pass                                     # der Fehler, der hierher fuehrte, zaehlt

    def _transfer(self, info: dict):
        u = self.update
        if info.get("target", u.target) != u.target or info.get("board", u.board) != u.board:
            raise FlashError(f"Falsche Firmware: Die Basis ist {info.get('target')} {info.get('board')}.")
        key = f", Schlüssel {info['key']}" if "key" in info else ""
        self.log(f"Bootloader {info.get('bl', '?')}{key}, Anwendung {info.get('app', '?')}, Grund {info.get('grund', '?')}.")
        self.ser.reset_input_buffer()
        self.ser.write(b"\0")                        # Text von vorher abschliessen
        self.dec = bp.FrameDecoder()
        self.rx.clear()
        status, value, _text = self._ask(bp.BL_PING)
        problem = "Bootloader antwortet nicht auf PING." if status != bp.BL_OK else fit_problem(value, u, info)
        if problem:
            self._back_to_app()
            raise FlashError(problem)
        self.leaving_app = False
        try:
            self._check_cancel()
            self.log("Prüfe die Signatur im Gerät, das dauert einige Sekunden …" if u.format == bp.FORMAT_SIGNED
                     else "Lösche Zwischenspeicher …")
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
            status = self._ask(bp.BL_END, timeout=self.END_S, retries=1)[0]
            if status == bp.BL_E_VERIFY:             # kein Beleg geschrieben, die Anwendung ist unberuehrt
                self._back_to_app()
            self._ok(status, "Prüfung")
        except FlashCancelled:
            self._ask(bp.BL_ABORT, retries=int(self.START_S))   # steckt das Geraet in der Signaturpruefung, schweigt es
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


# ------------------------------------------------------------------------------------ Zange und Basis-ESP (#UPD, #ESPUPD)
def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


_OTA_TEXT = {        # Codes aus fwupd.h, gleich fuer Zange und Basis-ESP; {in} = „in der Zange“, {gen} = „der Zange“
    bp.BL_E_STATE: "Ablauf gestört",
    bp.BL_E_OFFSET: "Lücke in den Daten",
    bp.BL_E_HEADER: "Dateikopf ungültig",
    bp.BL_E_TARGET: "Datei passt nicht",
    bp.BL_E_UNSUPPORTED: "Dateiformat zu neu für die Firmware {gen}",
    bp.BL_E_LENGTH: "Länge ungültig, die Datei passt nicht in die Partition {gen}",
    bp.BL_E_FLASH: "Flash-Fehler {in}",
    bp.BL_E_VERIFY: "Prüfung {in} fehlgeschlagen (Inhalt, Prüfsumme oder Version)",
    bp.BL_E_CMD: "Befehl unbekannt",
    bp.BL_E_SIGNATURE: "Signatur ungültig: Die Datei stammt nicht von uns oder passt nicht zum Schlüssel {gen}",
    bp.BL_E_ROLLBACK: "Rückfall gesperrt: Die Datei ist älter, als die Firmware {gen} zulässt (Rollback-Wert)",
}


def _ota_text(gen: str, inside: str, more: dict) -> dict:
    return {**{k: v.format(gen=gen, **{"in": inside}) for k, v in _OTA_TEXT.items()}, **more}


ZANGE_UPD_TEXT = _ota_text(
    "der Zange", "in der Zange",
    {bp.BL_E_STATE: "Ablauf gestört (oder die Zange prüft gerade noch eine neue Firmware, 15 s nach dem Start)",
       bp.BL_E_TARGET: "Datei passt nicht zu dieser Zange",
       20: "Die Zange misst gerade: Aufnahme oder Blitzmodus läuft, in den letzten 5 s kamen Funken, oder sie ist mit "
           "einer Basis gekoppelt und synchron. Aufnahme und Blitzmodus beenden, die Zange vom Zündkabel nehmen und die "
           "Basis ausschalten, dann nochmals",
       21: "Akku zu schwach für das Update (unter 3,6 V ohne USB-Spannung): Akku laden oder die Zange direkt am PC "
           "anstecken",
       "nicht eingebaut": "Diese Zangen-Firmware hat kein USB-Update: einmal per Erstflash (esptool) 0.4.0 oder neuer "
                          "aufspielen"})
# Basis-ESP ueber den STM (SCHNITTSTELLEN.md, #ESPUPD): 2 … 21 vom ESP, 20 auch und 30 … 33 nur vom STM
BASIS_ESP_UPD_TEXT = _ota_text(
    "des Basis-ESP", "im Basis-ESP",
    {bp.BL_E_STATE: "Ablauf gestört (oder der Basis-ESP prüft gerade noch eine neue Firmware, 30 s nach dem Start)",
       bp.BL_E_TARGET: "Datei passt nicht zum Basis-ESP",
       20: "Die Basis misst gerade: Ausgabe (Messung, LabVIEW), Burst-Aufnahme oder Aufnahme der Zange läuft. Messung "
           "beenden, dann nochmals",
       21: "Versorgung zu schwach für das Update",
       30: "Der Basis-ESP antwortet dem STM nicht (kein Lebenszeichen, ESP-Brücke aktiv oder Puffer voll): Brücke beenden "
           "bzw. die Basis aus- und einschalten, dann nochmals",
       31: "Der Basis-ESP hat nicht rechtzeitig geantwortet",
       32: "Der Basis-ESP ist während des Updates neu gestartet, die alte Firmware bleibt: nochmals flashen",
       33: "Die Firmware des Basis-ESP ist älter als 0.4.0 und kann kein Update: einmal per „Basis-ESP Erstflash per "
           "Brücke“ (Build-Ordner, esptool) flashen, danach geht es mit der Update-Datei",
       "nur_usb": "Das Update des Basis-ESP geht nur per USB, nicht über WLAN"})
UPD_BLOCK = ZANGE_UPD_BLOCK = 256        # FWU_BLOCK_MAX in zange-c3/src/core/fwupd.h, im Basis-ESP derselbe Kern


def zange_ports() -> List[str]:
    if list_ports is None:
        return []
    return sorted(p.device for p in list_ports.comports() if (p.vid, p.pid) == ZANGE_USB_ID)


def upd_error(line: str, texts: dict = None) -> Tuple[int, str]:
    """'#ERR;UPD;20;messung;next=0' bzw. '#ERR;ESPUPD;33;esp_alt' -> (Code, Klartext). Ohne Zahl ('#ERR;UPD;nicht
    eingebaut', '#ERR;ESPUPD;nur_usb') -> (-1, Klartext). Vorgabe: Texte der Zange."""
    texts = ZANGE_UPD_TEXT if texts is None else texts
    parts = line.split(";")
    word = parts[2] if len(parts) > 2 else "?"
    if not word.isdigit():
        return -1, texts.get(word, texts.get("nicht eingebaut", f"Fehler {word}"))
    code = int(word)
    return code, texts.get(code, f"Fehler {code} ({parts[3] if len(parts) > 3 else '?'})")


class UpdFlasher:
    """Update ueber die Textbefehle von zange-c3/src/core/proto_upd.h: INFO, BEGIN, DATA in Bloecken zu 256 Byte mit
    Wiederholung, END, BOOT, danach die neue Version und ihre Freigabe (ota=gueltig) abwarten. Eine Anfrage, eine
    Antwortzeile; vor einer neuen Anfrage wird die alte abgewartet. Gewertet werden nur Zeilen mit '#<PREFIX>;' oder
    '#ERR;<PREFIX>;', alles andere (#ESP-Log, Telegramme) faellt weg. Das Geraet prueft Signatur und Inhalt selbst, der
    PC kennt den AES-Schluessel nicht. Die Unterklassen legen Praefix, Port, Zeiten, Texte und den Weg nach dem Neustart
    fest. run() blockiert (eigener Thread); Ports und Oeffnen ueber `ports` und `opener`, damit Tests Fake-Geraete
    (sim.ZangeUpdSimDevice, sim.BasisEspUpdSimDevice) einsetzen koennen."""
    PREFIX = "UPD"
    CAP_FLAG = "upd"
    TEXTS = ZANGE_UPD_TEXT
    FATAL = (20, 21, -1)      # ohne Befehlswort melden
    RETRY = ()                # wie keine Antwort: dieselbe Anfrage nochmals (BEGIN ersetzt, END und DATA wiederholen)
    # Woerter fuer die Meldungen
    W = dict(geraet="Zange", nom="die Zange", gen="der Zange", im="in der Zange", er="sie", sein="ihre", strom="Zange",
             probe="15 s")
    INFO_S = 1.5
    BEGIN_S = 15.0            # Ed25519 im Geraet, geschaetzt 1 s
    REPLY_S = 2.0             # ein Block, Sektor loeschen eingeschlossen
    END_S = 15.0              # esp_ota_end liest das ganze Abbild
    BOOT_S = 1.5
    RETRIES = 5
    WAIT_APP_S = 30.0         # Neustart, bis die neue Version sich meldet
    STABLE_S = 30.0           # so lange auf ota=gueltig warten
    BUSY_S = 25.0             # laeuft selbst noch zur Probe (ota=pruefen): so lange auf gueltig warten
    POLL_S = 1.0

    def __init__(self, update: bp.UpdateFile, ports: Callable[[], List[str]] = None,
                 opener: Callable[[str], object] = open_serial, log: Callable[[str], None] = lambda _t: None,
                 progress: Callable[[int, int], None] = lambda _d, _n: None,
                 cancelled: Callable[[], bool] = lambda: False):
        self.update = update
        self.ports = ports or self.default_ports
        self.opener = opener
        self.log = log
        self.progress = progress
        self.cancelled = cancelled
        self.ser = None
        self.buf = b""
        self.session = False                  # BEGIN angenommen, noch nicht BOOT
        self.booted = False

    @staticmethod
    def default_ports() -> List[str]:
        return zange_ports()

    # --- Ablauf
    def run(self) -> str:
        u, w = self.update, self.W
        try:
            self._open()
            info = self._check_device()
            self._begin()
            self.log(f"Übertrage {u.image_len} Byte in Blöcken zu {UPD_BLOCK} …")
            self._transfer()
            self.log(f"Prüfe {w['im']} …")
            line = self._ask("END", ("OK;END",), self.END_S, retries=2)
            got = bp.parse_info(line).get("version", "")
            if got != u.version:
                self._abort()
                raise FlashError(f"Falsche Version: {_cap(w['nom'])} meldet {got or '?'}, die Datei ist {u.version}.")
            if self.cancelled():
                self._abort()                         # stellt die Startpartition zurueck
                raise FlashCancelled("Abgebrochen, die alte Firmware bleibt.")
            self.log(f"Geprüft. {w['geraet']} startet mit {u.version} neu (vorher {info.get('fw', '?')}) …")
            self._boot()
            self.booted = True
            return self._wait_app(info.get("fw", ""))
        except OSError as exc:                    # SerialException ist ein OSError
            if self.booted:
                raise FlashError(f"Verbindung zur {w['strom']} verloren: {exc}. Die neue Firmware läuft vielleicht noch "
                                 f"zur Probe: {w['strom']} {w['probe']} eingeschaltet lassen, dann die Version "
                                 "prüfen.") from exc
            raise FlashError(f"Verbindung zur {w['strom']} verloren: {exc}. Die alte Firmware bleibt, {w['nom']} "
                             "verwirft die Sitzung nach 30 s. Nochmals flashen.") from exc
        finally:
            self._close()

    # --- Port und Zeilen
    def _open(self):
        ports = self.ports()
        if len(ports) > 1:
            raise FlashError(self.many_text())
        if not ports:
            raise FlashError(self.missing_text())
        self._close()
        self.ser = self.opener(ports[0])
        self.buf = b""

    def many_text(self) -> str:
        return "Mehrere Zangen am USB, bitte nur eine anstecken."

    def missing_text(self) -> str:
        return "Zange nicht am USB gefunden (303A:1001): eingeschaltet und USB-C am PC?"

    def _close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except (OSError, ValueError):
                pass
            self.ser = None

    def _send(self, line: str):
        self.ser.write(line.encode("ascii") + b"\r\n")

    def _lines(self) -> List[str]:
        self.buf += self.ser.read(self.ser.in_waiting or 1)       # kehrt beim ersten Byte zurueck
        *done, self.buf = self.buf.split(b"\n")
        return [raw.decode("latin-1").strip() for raw in done]

    def _wait(self, wanted: Tuple[str, ...], seconds: float) -> Optional[str]:
        end = time.time() + seconds
        while time.time() < end:
            for line in self._lines():
                if line.startswith(wanted):
                    return line
        return None

    @property
    def _err(self) -> str:
        return f"#ERR;{self.PREFIX};"

    def _ask(self, cmd: str, ok: Tuple[str, ...], seconds: float, retries: int = 1) -> str:
        """'#<PREFIX> <cmd>' -> Zeile '#<PREFIX>;<ok…>' oder FlashError. Ohne Antwort (oder mit einem Code aus RETRY)
        noch einmal, erst nach Ablauf der Wartezeit: eine neue Anfrage ersetzt sonst die alte."""
        word = cmd.split()[0]
        for _ in range(retries + 1):
            self._send(f"#{self.PREFIX} {cmd}")
            line = self._wait(tuple(f"#{self.PREFIX};{x}" for x in ok) + (self._err,), seconds)
            if line is None:
                continue
            if line.startswith(self._err):
                code, text = upd_error(line, self.TEXTS)
                if code in self.RETRY:
                    continue
                raise FlashError(text + "." if code in self.FATAL else f"{word}: {text}.")
            return line
        raise FlashError(f"{_cap(self.W['nom'])} antwortet nicht auf {word}.")

    # --- Pruefen vor der Uebertragung
    def _caps(self) -> dict:
        self.ser.reset_input_buffer()
        self._send("#RUN 0")                      # eigene Telegramme beenden: mit laufender Ausgabe kein BEGIN
        line = None
        for _ in range(2):
            self._send("#?")
            line = self._wait(("#CAP", "#BOOTLOADER"), self.INFO_S)
            if line is not None:
                break
        if line is None:
            raise FlashError(f"Keine Antwort von der {self.W['strom']}.")
        if line.startswith("#BOOTLOADER"):
            raise FlashError("Die Basis steckt im Bootloader: Erst „Basis STM32“ flashen, dann den Basis-ESP.")
        return bp.parse_info(line)

    def no_update_text(self, caps: dict) -> str:
        return (f"Diese Zangen-Firmware ({caps.get('fw', '?')}) hat kein USB-Update: einmal per „Zange Erstflash“ "
                "(esptool, Build-Ordner) 0.4.0 oder neuer aufspielen.")

    def _check_device(self) -> dict:
        w = self.W
        caps = self._caps()
        if caps.get(self.CAP_FLAG) != "1":
            raise FlashError(self.no_update_text(caps))
        self._before(caps)
        info = self._info()
        end = time.time() + self.BUSY_S
        while info.get("ota") == "pruefen" and time.time() < end:
            if self.cancelled():
                raise FlashCancelled("Abgebrochen, nichts übertragen.")
            self.log(f"{_cap(w['nom'])} prüft noch {w['sein']} eigene neue Firmware ({w['probe']} nach dem Start), "
                     "warte …")
            time.sleep(self.POLL_S)
            info = self._info()
        self.log(f"{w['geraet']}: {info.get('fw', '?')}, Schlüssel {info.get('key', '?')}, Rollback "
                 f"{info.get('rollback', '?')}, läuft aus {info.get('part', '?')} ({info.get('ota', '?')}).")
        problem = self._fit(info)
        if problem:
            raise FlashError(problem)
        return info

    def _before(self, caps: dict):
        """Stand vor dem Update merken (Basis-ESP: Neustartzaehler aus #NET)."""

    def _info(self) -> dict:
        return bp.parse_info(self._ask("INFO", ("INFO",), self.INFO_S, retries=2))

    def _fit(self, info: dict) -> str:
        u, w = self.update, self.W
        if info.get("ota") == "pruefen":
            return (f"{_cap(w['nom'])} gibt {w['sein']} eigene neue Firmware nicht frei. {w['strom']} aus- und "
                    f"einschalten, {w['probe']} warten, nochmals.")
        if info.get("ziel", u.target) != u.target or info.get("board", u.board) != u.board:
            return (f"Falsche Firmware: {_cap(w['nom'])} ist {info.get('ziel')} {info.get('board')}, die Datei für "
                    f"{u.target} {u.board}.")
        key = info.get("key", "")
        if key.isdigit() and int(key) != u.key_id:
            return f"Die Datei ist mit Schlüssel {u.key_id} signiert, {w['nom']} hat Schlüssel {key}."
        rb = info.get("rollback", "")
        if rb.isdigit() and u.rollback < int(rb):
            return f"{self.TEXTS[bp.BL_E_ROLLBACK]}: Datei {u.rollback}, {w['geraet']} {rb}."
        size = info.get("max", "")
        if size.isdigit() and u.image_len > int(size):
            return f"Die Datei ({u.image_len} Byte) ist größer als die Partition {w['gen']} ({size} Byte)."
        return ""

    # --- Uebertragung
    def _begin(self):
        if self.cancelled():
            raise FlashCancelled("Abgebrochen, nichts übertragen.")
        self.log(f"{self.W['geraet']} prüft die Signatur …")
        hdr = base64.b64encode(self.update.header).decode("ascii")
        self._ask(f"BEGIN {hdr}", ("OK;BEGIN",), self.BEGIN_S, retries=1)
        self.session = True

    def _transfer(self):
        u = self.update
        off, tries = 0, 0
        while off < u.image_len:
            if self.cancelled():
                self._abort()
                raise FlashCancelled("Abgebrochen, die alte Firmware bleibt.")
            block = u.payload[off:off + UPD_BLOCK]
            self._send(f"#{self.PREFIX} DATA {off} {base64.b64encode(block).decode('ascii')}")
            line = self._wait((f"#{self.PREFIX};OK;DATA", self._err), self.REPLY_S)
            nxt = bp.parse_info(line).get("next", "") if line else ""
            if line is not None and line.startswith(self._err):
                code, text = upd_error(line, self.TEXTS)
                if code in self.RETRY:
                    nxt = ""                          # wie keine Antwort
                elif code != bp.BL_E_OFFSET or not nxt.isdigit():
                    self.session = False              # das Geraet hat die Sitzung verworfen
                    raise FlashError(text + "." if code in self.FATAL else f"Block {off}: {text}.")
                else:
                    self.log(f"{self.W['geraet']} erwartet {nxt}, mache dort weiter.")
            if nxt.isdigit() and 0 <= int(nxt) <= u.image_len and int(nxt) > off:
                off, tries = int(nxt), 0
                self.progress(off, u.image_len)
                continue
            if nxt.isdigit() and int(nxt) <= u.image_len:
                off = int(nxt)                        # Geraet ist zurueck: dort weiter
            tries += 1                                # keine Antwort oder kein Fortschritt: denselben Block nochmals
            if tries > self.RETRIES:
                self._abort()
                raise FlashError(f"{_cap(self.W['nom'])} nimmt Block {off} nicht an. Die alte Firmware bleibt, "
                                 "nochmals flashen.")

    def _abort(self):
        """Sitzung im Geraet verwerfen; ohne Antwort verfaellt sie dort nach 30 s von selbst."""
        if not self.session or self.ser is None:
            return
        self.session = False
        try:
            self._send(f"#{self.PREFIX} ABORT")
            self._wait((f"#{self.PREFIX};OK;ABORT", self._err), self.INFO_S)
        except OSError:
            pass

    def _boot(self):
        self._send(f"#{self.PREFIX} BOOT")
        try:
            line = self._wait((f"#{self.PREFIX};OK;BOOT", self._err), self.BOOT_S)
        except OSError:                           # meldet sich gleich nach der Antwort ab
            line = None
        if line is not None and line.startswith(self._err) and not self._boot_ok(line):
            raise FlashError(f"Neustart: {upd_error(line, self.TEXTS)[1]}.")
        self.session = False
        self._after_boot()

    def _boot_ok(self, line: str) -> bool:
        """Fehlerzeile auf BOOT, die trotzdem den Neustart bedeutet (Basis-ESP: 32 neustart)."""
        return False

    def _after_boot(self):
        self._close()                             # die Zange meldet sich am USB neu an

    def _wait_app(self, old: str) -> str:
        u = self.update
        time.sleep(0.5)
        end = time.time() + self.WAIT_APP_S
        caps = None
        while time.time() < end and caps is None:
            try:
                self._open()
                self.ser.reset_input_buffer()
                self._send("#?")
                line = self._wait(("#CAP",), self.INFO_S)
                caps = bp.parse_info(line) if line else None
            except (OSError, FlashError):
                self._close()
                time.sleep(0.2)
        if caps is None:
            raise FlashError("Zange meldet sich nach dem Neustart nicht. Einschalten, USB prüfen; startet sie mit der "
                             "alten Firmware, hat sie die neue verworfen.")
        if not version_matches(u.version, caps.get("fw", "")):
            raise FlashError(f"Die Zange läuft mit {caps.get('fw', '?')}, nicht mit {u.version}: Sie ist zur alten "
                             "Firmware zurückgekehrt (Neustart in den ersten 15 s oder Startfehler).")
        self.log(f"Zange läuft mit {caps.get('fw')}.")
        return self._wait_valid(old)

    def _wait_valid(self, old: str) -> str:
        """Die neue Firmware laeuft zur Probe: warten, bis sie sich als gueltig markiert (INFO ota=gueltig)."""
        u, w = self.update, self.W
        self.log(f"{w['geraet']} prüft die neue Firmware {w['probe']}: nicht ausschalten …")
        end = time.time() + self.STABLE_S
        while time.time() < end:
            try:
                info = self._info()
            except FlashError:
                info = {}                             # gleich nach dem Neustart noch keine Antwort: nochmals fragen
            if info.get("ota") == "gueltig" and not version_matches(u.version, info.get("fw", "")):
                raise FlashError(f"{_cap(w['nom'])} läuft mit {info.get('fw', '?')}, nicht mit {u.version}: "
                                 f"{_cap(w['er'])} ist zur alten Firmware zurückgekehrt (Neustart in der Probezeit "
                                 "oder Startfehler).")
            if info.get("ota") == "gueltig":
                self.log(f"Neue Firmware als gültig markiert (vorher {old or '?'}).")
                return u.version
            time.sleep(self.POLL_S)
        raise FlashError(f"{u.version} läuft, ist aber nach {self.STABLE_S:.0f} s nicht als gültig markiert. "
                         f"{w['strom']} eingeschaltet lassen; startet {w['er']} vorher neu, kehrt {w['er']} zur alten "
                         f"Firmware zurück.{self.valid_hint()}")

    def valid_hint(self) -> str:
        return ""


class ZangeFlasher(UpdFlasher):
    """Zange direkt per USB (USB-Serial-JTAG 303A:1001), Befehle #UPD. Nach BOOT meldet sich die Zange am USB neu an."""


class BasisEspFlasher(UpdFlasher):
    """Basis-ESP ueber den STM der Basis (Basis ab 0.4.0, '#CAP;…;espupd=1'), Befehle #ESPUPD am Messport der Basis
    per USB-CDC wie die normale Verbindung (nie ueber WLAN, nie ueber den Brueckenport). Der STM reicht jede Anfrage an
    den ESP weiter und wartet bei BEGIN und END bis 10 s, sonst 3 s; PyST wartet je 2 s laenger. Nach BOOT startet nur
    der ESP neu, der Port bleibt offen: '#NET' zeigt esp=1, mehr esp_neustarts und die neue esp_fw, danach meldet INFO
    ota=pruefen und nach 30 s mit Link ota=gueltig."""
    PREFIX = "ESPUPD"
    CAP_FLAG = "espupd"
    TEXTS = BASIS_ESP_UPD_TEXT
    FATAL = (20, 21, 30, 33, -1)
    RETRY = (31,)             # Antwort kam zu spaet: der STM hat sie verworfen, die Anfrage wiederholen
    W = dict(geraet="Basis-ESP", nom="der Basis-ESP", gen="des Basis-ESP", im="im Basis-ESP", er="er", sein="seine",
             strom="Basis", probe="30 s")
    INFO_S = 5.0              # STM 3 s + 2 s
    BEGIN_S = 12.0            # STM 10 s + 2 s
    REPLY_S = 5.0
    END_S = 12.0
    BOOT_S = 5.0
    NET_S = 2.0
    WAIT_APP_S = 30.0         # ESP startet neu, Link steht wieder (HELLO 1/s)
    STABLE_S = 60.0           # gueltig nach 30 s mit Link
    BUSY_S = 45.0

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.restarts = None                  # esp_neustarts vor dem Update

    @staticmethod
    def default_ports() -> List[str]:
        return basis_ports()

    def many_text(self) -> str:
        return "Mehrere Basen am USB, bitte nur eine anstecken."

    def missing_text(self) -> str:
        return ("Basis nicht am USB gefunden (1209:0001): 12 V an, USB-C an J20? Den Basis-ESP aktualisiert PyST nur per "
                "USB, nicht über WLAN.")

    def no_update_text(self, caps: dict) -> str:
        return (f"Diese Basis-Firmware ({caps.get('fw', '?')}) reicht kein Update an den Basis-ESP weiter: Erst „Basis "
                "STM32“ auf 0.4.0 oder neuer bringen. Ein Basis-ESP vor 0.4.0 braucht außerdem einmal „Basis-ESP "
                "Erstflash per Brücke“.")

    def _net(self) -> dict:
        self._send("#NET")
        line = self._wait(("#NET;", "#ERR;NET"), self.NET_S)
        return bp.parse_info(line) if line and line.startswith("#NET;") else {}

    @staticmethod
    def _count(net: dict) -> Optional[int]:
        n = net.get("esp_neustarts", "")
        return int(n) if n.isdigit() else None

    def _before(self, caps: dict):
        net = self._net()
        self.restarts = self._count(net)
        self.log(f"Basis: {caps.get('fw', '?')}, ESP: {net.get('esp_fw', '?')}, ESP-Neustarts "
                 f"{self.restarts if self.restarts is not None else '?'}.")

    def _boot(self):
        n = self._count(self._net())                  # Stand unmittelbar vor dem Neustart
        if n is not None:
            self.restarts = n
        super()._boot()

    def _boot_ok(self, line: str) -> bool:
        return upd_error(line, self.TEXTS)[0] == 32           # der Neustart kam vor der Antwort: normal

    def _after_boot(self):
        """Der Port bleibt offen, nur der ESP startet neu."""

    def _restarted(self, net: dict) -> bool:
        n = self._count(net)
        return net.get("esp") == "1" and n is not None and (self.restarts is None or n > self.restarts)

    def _wait_app(self, old: str) -> str:
        u = self.update
        self.log("Basis-ESP startet neu, warte auf den Link …")
        end = time.time() + self.WAIT_APP_S
        net = {}
        while time.time() < end:
            net = self._net() or net
            if self._restarted(net):              # esp_fw kommt mit demselben HELLO wie der Neustartzaehler
                break
            time.sleep(self.POLL_S)
        if self._restarted(net) and version_matches(u.version, net.get("esp_fw", "")):
            self.log(f"Basis-ESP läuft mit {net.get('esp_fw')} (ESP-Neustarts {net.get('esp_neustarts')}).")
            return self._wait_valid(old)
        if self._restarted(net):
            raise FlashError(f"Der Basis-ESP läuft mit {net.get('esp_fw', '?')}, nicht mit {u.version}: Er ist zur "
                             "alten Firmware zurückgekehrt (Startfehler der neuen).")
        raise FlashError("Der Basis-ESP meldet sich nach dem Neustart nicht (in #NET fehlen esp=1 und ein weiterer "
                         "Neustart). Basis eingeschaltet lassen, dann unter Einstellungen → „Zange · Netz · Steckdosen“ "
                         "die ESP-Version prüfen.")

    def valid_hint(self) -> str:
        return (" Der Basis-ESP gibt sich erst nach 30 s mit stehendem Link zum STM frei; die Version zeigt Einstellungen "
                "→ „Zange · Netz · Steckdosen“.")


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


# ============================================================================================== ältere Messplatinen
# Arduino Mega 2560 (PST 3.x, Sketch) und STM32 F446 (Nucleo-64 mit ST-LINK/V2-1, PST-STM32). Die Dateien liegen
# unsigniert im Firmware-Repo (manifest.json: "mega" -> pst-mega-<v>.hex, "stm32_f446" -> pst-stm32-f446-<v>.bin).
# Mega: avrdude über den Arduino-Bootloader (stk500v2, 115200 Bd). F446: .bin auf das Laufwerk NODE_F446RE des
# Nucleo kopieren (der ST-Link flasht selbst), ersatzweise st-flash bzw. dfu-util an 0x08000000.
MEGA, F446, F446_STLINK = "mega", "stm32_f446", "stm32_f446_stlink"
MEGA_USB_IDS = {(0x2341, 0x0042), (0x2341, 0x0010), (0x2A03, 0x0042), (0x2A03, 0x0010),   # Arduino Mega 2560
                (0x1A86, 0x7523)}                                                           # Nachbau mit CH340
MEGA_FLASH_MAX = 256 * 1024 - 8 * 1024    # ATmega2560 ohne den 8-KB-Bootloader
MEGA_BAUD = 115200
F446_FLASH = 0x08000000
F446_FLASH_MAX = 512 * 1024
F446_SRAM = (0x20000000, 0x20020000)      # 128 KB
NUCLEO_LABEL = "NODE_F446RE"
NUCLEO_MERKMAL = ("MBED.HTM", "DETAILS.TXT")
_MEGA_VERSION = re.compile(rb"Version (\d+\.\d+\.\d+)")
_F446_VERSION = re.compile(rb"PST-STM32(?:[ _-]?F446(?:RE)?)?[^0-9\r\n]{0,24}(\d+\.\d+\.\d+)")
_NAME_VERSION = re.compile(r"(\d+\.\d+\.\d+)")

TARGETS[MEGA] = Target(MEGA, "Arduino Mega (PST 3.x)", "", MEGA_BAUD,
                       "Messplatine mit Arduino Mega 2560 per USB anstecken. PyST trennt die Messverbindung und flasht "
                       "mit avrdude über den Arduino-Bootloader (115200 Bd); die Einstellungen im EEPROM bleiben. "
                       "Dauer ca. 30 s, danach startet der Mega neu und PyST verbindet sich wieder.")
TARGETS[F446] = Target(F446, "STM32 (F446) per USB", "", 0,
                       "Nucleo-64 (F446RE) per USB anstecken. PyST kopiert die Datei auf das Laufwerk NODE_F446RE, der "
                       "ST-Link flasht selbst und startet neu. Ohne Laufwerk: st-flash oder dfu-util an 0x08000000. "
                       "Dauer ca. 30 s.")
TARGETS[F446_STLINK] = Target(F446_STLINK, "STM32 (F446) Erstflash per ST-Link/DFU", "", 0,
                              "Für leere oder verkonfigurierte Platinen: st-flash über den ST-Link an 0x08000000, ohne "
                              "st-flash dfu-util (BOOT0 auf 1, Reset), danach BOOT0 wieder auf 0.")


def parse_ihex(text: str) -> Tuple[bytes, int, int]:
    """Intel-HEX -> (Daten ab der kleinsten Adresse, kleinste Adresse, größte Adresse + 1). Prüft jede Zeile."""
    mem = {}
    base, ende = 0, False
    for nr, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith(":") or len(line) < 11:
            raise FlashError(f"Keine Intel-HEX-Datei (Zeile {nr}).")
        try:
            rec = bytes.fromhex(line[1:])
        except ValueError:
            raise FlashError(f"Intel-HEX: Zeile {nr} ist kein Hex.")
        n, addr, typ = rec[0], int.from_bytes(rec[1:3], "big"), rec[3]
        if len(rec) != n + 5 or sum(rec) & 0xFF:
            raise FlashError(f"Intel-HEX: Prüfsumme oder Länge in Zeile {nr} falsch, Datei beschädigt.")
        data = rec[4:4 + n]
        if typ == 0:
            for i, b in enumerate(data):
                mem[base + addr + i] = b
        elif typ == 1:
            ende = True
            break
        elif typ == 2:
            base = int.from_bytes(data, "big") << 4
        elif typ == 4:
            base = int.from_bytes(data, "big") << 16
    if not ende:
        raise FlashError("Intel-HEX: Endezeile fehlt, Datei unvollständig.")
    if not mem:
        raise FlashError("Intel-HEX: keine Daten.")
    lo, hi = min(mem), max(mem) + 1
    return bytes(mem.get(a, 0xFF) for a in range(lo, hi)), lo, hi


def _version_aus(data: bytes, pattern, path: str) -> str:
    hit = pattern.search(data)
    if hit:
        return hit.group(1).decode()
    hit = _NAME_VERSION.search(os.path.basename(path))
    return hit.group(1) if hit else ""


def load_mega_image(folder: str) -> Image:
    path = _find_file(folder, ("pst-mega-*.hex", "*.hex"), "Keine Mega-Datei (.hex) gefunden.")
    if not path.lower().endswith(".hex"):
        raise FlashError("Falsche Datei: Für den Arduino Mega gehört eine .hex-Datei (Intel-HEX).")
    with open(path, encoding="ascii", errors="replace") as fh:
        data, lo, hi = parse_ihex(fh.read())
    if lo != 0 or hi > MEGA_FLASH_MAX:
        raise FlashError(f"Falsche Firmware: Adressen {lo:#x}…{hi:#x} passen nicht zum ATmega2560 "
                         f"(0…{MEGA_FLASH_MAX:#x} ohne Bootloader).")
    return Image(MEGA, os.path.dirname(path), "PST-Mega", _version_aus(data, _MEGA_VERSION, path),
                 files=[(0, path)], main=path)


def load_f446_image(folder: str) -> Image:
    path = _find_file(folder, ("pst-stm32-f446-*.bin", "*.bin"), "Keine F446-Datei (.bin) gefunden.")
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 8 or len(data) > F446_FLASH_MAX:
        raise FlashError(f"Größe {len(data)} Byte passt nicht zum STM32F446 (höchstens 512 KB).")
    if data[0] == ESP_IMAGE_MAGIC:
        raise FlashError("Falsche Firmware: Das ist ein ESP-Abbild, kein STM32.")
    if data[:8] == bp.UPD_MAGIC:
        raise FlashError("Falsche Firmware: Update-Datei der PyST-Basis, nicht für den F446.")
    if len(data) > bp.BOOT_SIZE + bp.IMG_HDR_OFS + 4 and \
            data[bp.BOOT_SIZE + bp.IMG_HDR_OFS:bp.BOOT_SIZE + bp.IMG_HDR_OFS + 4] == struct.pack("<I", bp.IMG_MAGIC):
        raise FlashError("Falsche Firmware: Das ist die Gesamtdatei der PyST-Basis (STM32G473), nicht der F446.")
    sp, reset = struct.unpack_from("<II", data)
    if not F446_SRAM[0] < sp <= F446_SRAM[1] or not reset & 1 or not F446_FLASH < reset < F446_FLASH + len(data):
        raise FlashError("Falsche Firmware: Die Vektortabelle passt nicht zu einem STM32F446 ab 0x08000000.")
    return Image(F446, os.path.dirname(path), "PST-STM32", _version_aus(data, _F446_VERSION, path),
                 files=[(F446_FLASH, path)], main=path)


# ---------------------------------------------------------------------------------------------- Werkzeuge Mega/F446
def _windows() -> bool:
    return sys.platform.startswith("win")


def avrdude_kandidaten() -> List[str]:
    """Wo avrdude liegt: PATH, Homebrew, Arduino-IDE (1.x und 2.x), PlatformIO."""
    home = os.path.expanduser("~")
    exe = "avrdude.exe" if _windows() else "avrdude"
    out = [p for p in (shutil.which("avrdude"),) if p]
    out += ["/opt/homebrew/bin/avrdude", "/usr/local/bin/avrdude", "/usr/bin/avrdude"]
    for root in (os.path.join(home, ".arduino15"), os.path.join(home, "Library", "Arduino15"),
                 os.path.join(os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local")), "Arduino15")):
        out += sorted(glob.glob(os.path.join(root, "packages", "arduino", "tools", "avrdude", "*", "bin", exe)),
                      reverse=True)
    out += ["/Applications/Arduino.app/Contents/Java/hardware/tools/avr/bin/avrdude",
            "/usr/share/arduino/hardware/tools/avr/bin/avrdude",
            r"C:\Program Files (x86)\Arduino\hardware\tools\avr\bin\avrdude.exe",
            r"C:\Program Files\Arduino\hardware\tools\avr\bin\avrdude.exe",
            os.path.join(home, ".platformio", "packages", "tool-avrdude", exe),
            os.path.join(home, ".platformio", "packages", "tool-avrdude", "bin", exe)]
    return out


def avrdude_conf(exe: str) -> str:
    """avrdude.conf zum gefundenen avrdude (Arduino: ../etc, PlatformIO: daneben); leer = eingebaute nehmen."""
    d = os.path.dirname(exe)
    for p in (os.path.join(d, "avrdude.conf"), os.path.join(d, "..", "etc", "avrdude.conf"),
              os.path.join(d, "..", "avrdude.conf")):
        if os.path.isfile(p):
            return os.path.normpath(p)
    return ""


def find_avrdude() -> Optional[List[str]]:
    """-> [avrdude] bzw. [avrdude, -C, conf] oder None."""
    for exe in avrdude_kandidaten():
        if exe and os.path.isfile(exe):
            conf = avrdude_conf(exe)
            return [exe, "-C", conf] if conf else [exe]
    return None


def avrdude_command(tool: List[str], port: str, image: Image) -> List[str]:
    """Mega 2560 mit Arduino-Bootloader (stk500v2): -c wiring -p m2560 -b 115200 -D (der Bootloader löscht selbst)."""
    return tool + ["-p", "m2560", "-c", "wiring", "-P", port, "-b", str(MEGA_BAUD), "-D",
                   "-U", f"flash:w:{image.main}:i"]


def find_dfu_util() -> Optional[List[str]]:
    exe = shutil.which("dfu-util") or next((p for p in ("/opt/homebrew/bin/dfu-util", "/usr/local/bin/dfu-util")
                                            if os.path.isfile(p)), None)
    return [exe] if exe else None


def f446_stflash_command(tool: List[str], image: Image) -> List[str]:
    """--connect-under-reset: der ST-LINK haelt NRST, bis er verbunden ist. Ohne das meldete st-flash am Nucleo mit
    fremder Firmware „Failed to enter SWD mode“ (10.11.2026); beim Nucleo ist NRST immer verdrahtet."""
    addr, path = image.files[0]
    return tool + ["--connect-under-reset", "--reset", "write", path, hex(addr)]


def dfu_command(tool: List[str], image: Image) -> List[str]:
    addr, path = image.files[0]
    return tool + ["-a", "0", "-s", f"{addr:#010x}:leave", "-D", path]


def mega_ports() -> List[str]:
    if list_ports is None:
        return []
    return sorted(p.device for p in list_ports.comports() if (p.vid, p.pid) in MEGA_USB_IDS)


def f446_ports() -> List[str]:
    """Virtueller COM-Port des ST-LINK/V2-1 auf dem Nucleo."""
    from .link import PST_USB_IDS
    if list_ports is None:
        return []
    return sorted(p.device for p in list_ports.comports() if (p.vid, p.pid) in PST_USB_IDS)


def mega_port() -> Optional[str]:
    ports = mega_ports()
    return ports[0] if len(ports) == 1 else None


def f446_port() -> Optional[str]:
    ports = f446_ports()
    return ports[0] if len(ports) == 1 else None


def _windows_label(root: str) -> str:
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(261)
        if ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), buf, 261, None, None, None, None, 0):
            return buf.value
    except (AttributeError, OSError, ValueError):
        pass
    return ""


def ist_nucleo(pfad: str) -> bool:
    """Massenspeicher des ST-Link: MBED.HTM oder DETAILS.TXT im Wurzelverzeichnis."""
    try:
        namen = {n.upper() for n in os.listdir(pfad)}
    except OSError:
        return False
    return any(m in namen for m in NUCLEO_MERKMAL)


def nucleo_laufwerke(muster: Optional[List[str]] = None) -> List[str]:
    """Laufwerke NODE_F446RE: Linux /media/<user>/…, /run/media/<user>/…, macOS /Volumes/…, Windows je Buchstabe
    mit diesem Namen; immer nur, wenn MBED.HTM oder DETAILS.TXT darauf liegt."""
    if muster is None and _windows():
        out = []
        for buchstabe in "DEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{buchstabe}:\\"
            if os.path.isdir(root) and ist_nucleo(root) and _windows_label(root).upper().startswith(NUCLEO_LABEL):
                out.append(root)
        return out
    if muster is None:
        muster = [f"/media/*/{NUCLEO_LABEL}*", f"/run/media/*/{NUCLEO_LABEL}*", f"/media/{NUCLEO_LABEL}*",
                  f"/mnt/{NUCLEO_LABEL}*", f"/Volumes/{NUCLEO_LABEL}*"]
    out = []
    for m in muster:
        out += [p for p in sorted(glob.glob(m)) if os.path.isdir(p) and ist_nucleo(p)]
    return out


def f446_weg() -> str:
    """Wie der F446 geflasht wird: laufwerk, stflash, dfu oder leer (nichts gefunden)."""
    if nucleo_laufwerke():
        return "laufwerk"
    if find_stflash():
        return "stflash"
    if find_dfu_util():
        return "dfu"
    return ""


def build_command_alt(target: str, folder: str, port: Optional[str]) -> Tuple[Image, List[str]]:
    """Mega und F446: Prüfen und Befehlszeile; F446 über das Laufwerk -> [] (F446Flasher kopiert)."""
    if target == MEGA:
        image = load_mega_image(folder)
        tool = find_avrdude()
        if tool is None:
            raise FlashError("avrdude nicht gefunden (Arduino-IDE oder PlatformIO installieren; Linux: apt install "
                             "avrdude, Mac: brew install avrdude).")
        if not port:
            raise FlashError("Arduino Mega nicht am USB gefunden (2341:0042, 2A03:0042 oder CH340 1A86:7523).")
        return image, avrdude_command(tool, port, image)
    image = load_f446_image(folder)
    weg = "" if target == F446_STLINK else ("laufwerk" if nucleo_laufwerke() else "")
    if weg == "laufwerk":
        return image, []
    tool = find_stflash()
    if tool:
        return image, f446_stflash_command(tool, image)
    tool = find_dfu_util()
    if tool:
        return image, dfu_command(tool, image)
    raise FlashError(f"Weder das Laufwerk {NUCLEO_LABEL} noch st-flash oder dfu-util gefunden. Nucleo per USB "
                     "anstecken (ST-Link-Seite) oder st-flash installieren (Mac: brew install stlink, Linux: apt "
                     "install stlink-tools).")


class F446Flasher:
    """F446 über das Massenspeicher-Laufwerk des ST-Link: .bin hinkopieren, warten, bis der ST-Link geflasht hat
    (Laufwerk verschwindet und kommt wieder, FAIL.TXT = Fehler), dann auf den COM-Port warten. run() blockiert."""
    WAIT_GONE_S = 8.0
    WAIT_BACK_S = 30.0
    WAIT_PORT_S = 15.0
    BLOCK = 16384

    def __init__(self, image: Image, laufwerke: Callable[[], List[str]] = nucleo_laufwerke,
                 ports: Callable[[], List[str]] = f446_ports, log: Callable[[str], None] = lambda _t: None,
                 progress: Callable[[int, int], None] = lambda _d, _n: None,
                 cancelled: Callable[[], bool] = lambda: False, sleep: Callable[[float], None] = time.sleep):
        self.image = image
        self.laufwerke = laufwerke
        self.ports = ports
        self.log = log
        self.progress = progress
        self.cancelled = cancelled
        self.sleep = sleep

    def _warte(self, cond: Callable[[], bool], sekunden: float) -> bool:
        end = time.time() + sekunden
        while time.time() < end:
            if cond():
                return True
            self.sleep(0.2)
        return cond()

    def run(self) -> str:
        drives = self.laufwerke()
        if not drives:
            raise FlashError(f"Laufwerk {NUCLEO_LABEL} nicht gefunden: Nucleo per USB (ST-Link-Seite) anstecken.")
        if len(drives) > 1:
            raise FlashError("Mehrere Nucleo-Laufwerke gefunden: nur einen F446 anstecken.")
        drive = drives[0]
        src = self.image.main
        dest = os.path.join(drive, os.path.basename(src))
        size = os.path.getsize(src)
        self.log(f"Kopiere {os.path.basename(src)} ({size} Byte) nach {drive} …")
        done = 0
        with open(src, "rb") as fin, open(dest, "wb") as fout:
            while True:
                if self.cancelled():
                    raise FlashCancelled("Abgebrochen, bevor die Datei vollständig war: Der ST-Link flasht nichts.")
                block = fin.read(self.BLOCK)
                if not block:
                    break
                fout.write(block)
                done += len(block)
                self.progress(done, size)
            fout.flush()
            os.fsync(fout.fileno())
        self.log("Datei übergeben, der ST-Link flasht …")
        # Der ST-Link meldet das Laufwerk zum Flashen ab und danach wieder an
        self._warte(lambda: drive not in self.laufwerke(), self.WAIT_GONE_S)
        if not self._warte(lambda: drive in self.laufwerke(), self.WAIT_BACK_S):
            raise FlashError(f"Das Laufwerk {NUCLEO_LABEL} kam nicht wieder. USB ab- und wieder anstecken.")
        fail = os.path.join(drive, "FAIL.TXT")
        if os.path.isfile(fail):
            with open(fail, encoding="ascii", errors="replace") as fh:
                raise FlashError(f"Der ST-Link meldet einen Fehler: {fh.read().strip()}")
        if not self._warte(lambda: bool(self.ports()), self.WAIT_PORT_S):
            self.log("COM-Port des Nucleo noch nicht da – PyST verbindet sich, sobald er erscheint.")
        self.log(f"F446 geflasht ({self.image.version or 'Version unbekannt'}).")
        return self.image.version
