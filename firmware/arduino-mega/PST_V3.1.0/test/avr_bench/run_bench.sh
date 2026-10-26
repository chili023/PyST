#!/bin/sh
# Pruefungen auf dem AVR im Simulator simavr (ohne Hardware). Braucht die PlatformIO-Pakete
# toolchain-atmelavr, framework-arduino-avr und tool-simavr
# (pio pkg install -g -t platformio/tool-simavr).
#   bench.cpp    : Taktzahlen der Capture-ISR (pst_isr.h)
#   printcmp.cpp : pst_out_float / pst_fmt_normal gegen echtes Arduino Print (byte-identisch)
set -e
cd "$(dirname "$0")"
P=${PLATFORMIO_PACKAGES:-$HOME/.platformio/packages}
A=$P/framework-arduino-avr
T=${TMPDIR:-/tmp}
CXX="$P/toolchain-atmelavr/bin/avr-g++ -mmcu=atmega2560 -DF_CPU=16000000UL -Os -fno-exceptions"
SIM="$P/tool-simavr/bin/simavr -m atmega2560 -f 16000000"
$CXX -flto -o "$T/pst_bench.elf" bench.cpp ../../pst_core.cpp
$CXX -DARDUINO=10808 -DARDUINO_ARCH_AVR -fno-threadsafe-statics -I"$A/cores/arduino" -I"$A/variants/mega" \
  -o "$T/pst_printcmp.elf" printcmp.cpp ../../pst_core.cpp "$A/cores/arduino/Print.cpp" "$A/cores/arduino/WString.cpp"
for e in pst_bench pst_printcmp; do
  timeout 600 $SIM "$T/$e.elf" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -v "^Loaded\|sleeping\|simavr" || true
done
