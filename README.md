# PyST Firmware

Signierte, verschlüsselte Update-Dateien für die PyST-Hardware. PyST lädt `manifest.json`, zeigt „Update verfügbar“,
lädt die Datei, prüft Größe, SHA-256 und Signatur und flasht erst auf Knopfdruck.

| Ziel | Datei | Weg |
|---|---|---|
| Basis STM32G473 (`basis_stm`) | `pyst-basis-<version>.pystfw` | USB, Bootloader 2.0.0 |
| Basis ESP32-C3 (`basis_esp`) | `pyst-basis-esp-<version>.pystfw` | USB über den STM (`#ESPUPD`) |
| Zange ESP32-C3 (`zange`) | `pyst-zange-<version>.pystfw` | USB an der Zange (`#UPD`), nie am laufenden Motor |

Format 2: 256 Byte Kopf, Ed25519-signiert (Schlüssel 2), Nutzdaten AES-256-CTR. Die Geräte prüfen Signatur, Ziel,
Rollback und SHA-256 des Klartexts selbst. Ohne passenden Schlüssel im Gerät wird nichts angenommen.

Voraussetzung: einmal die Auslieferungs-Firmware per ST-Link (Basis STM) bzw. per esptool (Basis-ESP über die
ESP-Brücke, Zange über USB) aufgespielt. Ältere Stände können diese Dateien nicht annehmen.

## Lizenzhinweise der enthaltenen Drittsoftware

| Software | Lizenz | in |
|---|---|---|
| TinyUSB 0.20.0 (hathach/tinyusb) | MIT | Basis STM |
| FreeRTOS-Kernel V11.2.0 | MIT | Basis STM |
| STM32CubeG4 1.6.3 (HAL/LL, CMSIS) | BSD-3-Clause bzw. Apache-2.0 | Basis STM |
| TweetNaCl 20140427 (Prüfpfad) | public domain | alle |
| tiny-AES-c (kokke) | Unlicense | alle |
| ESP-IDF 6.1 mit FreeRTOS, mbedTLS, lwIP, newlib und Espressif-Bibliotheken | Apache-2.0, MIT, BSD und Espressif-Lizenz | Basis-ESP, Zange |
| espressif/mdns | Apache-2.0 | Basis-ESP |

Die Lizenztexte stehen bei den jeweiligen Projekten. Der eigene Quellcode der Firmware ist nicht veröffentlicht.
