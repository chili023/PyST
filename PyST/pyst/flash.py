"""
Firmware flashen: Zange (ESP32-C3 per USB), Basis-STM32 (per USB ueber den eigenen Bootloader, reines Python; fuer die
Erstprogrammierung ST-Link an J10), Basis-ESP (ueber die ESP-Bruecke).

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
import shutil
import struct
import sys
import time
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
from .link import BASIS_USB_ID, is_esp_bridge

ZANGE, BASIS_STM, BASIS_STLINK, BASIS_ESP = "zange", "basis_stm", "basis_stlink", "basis_esp"
STM_FLASH_ADDR = 0x08000000
STM_FLASH_MAX = 512 * 1024
STM_SRAM = (0x20000000, 0x20020000)       # 128 KB
ESP_IMAGE_MAGIC = 0xE9
GESAMT_NAME = "pyst-basis-gesamt.bin"


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
                      "12 V an, STM32-Firmware läuft, USB-C an J20. Geht über die ESP-Brücke der Basis, "
                      "der WLAN-/Funkbetrieb ruht währenddessen."),
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

    def summary(self) -> str:
        with open(self.main, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()[:12]
        stamp = time.strftime("%d.%m.%Y %H:%M", time.localtime(os.path.getmtime(self.main)))
        name = " ".join(x for x in (self.project, self.version) if x)
        return f"{name + ', ' if name else ''}{os.path.getsize(self.main):,} Byte".replace(",", ".") + \
               f", {stamp}, SHA {sha}"


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


def load_image(target: str, folder: str) -> Image:
    if not folder:
        raise FlashError("Noch kein Ordner gewählt.")
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
    return {ZANGE: zange_port, BASIS_ESP: bridge_port, BASIS_STM: basis_port}.get(target, lambda: None)()


def build_command(target: str, folder: str, port: Optional[str]) -> Tuple[Image, List[str]]:
    """Alle Pruefungen; -> (Abbild, Befehlszeile) oder FlashError mit Klartext."""
    image = load_image(target, folder)
    if target == BASIS_STM:
        return image, []                  # kein externes Werkzeug: UsbFlasher
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
                    if self.update.version not in str(info).split():
                        raise FlashError(f"Falsche Version: Erwartet {self.update.version}, gemeldet „{info}“.")
                    self.log(f"Anwendung läuft: {info}.")
                    return self.update.version
            time.sleep(0.2)
        raise FlashError("Anwendung meldet sich nicht zurück. Basis einmal stromlos machen; beginnt sie von selbst "
                         "zu kopieren, abwarten.")
