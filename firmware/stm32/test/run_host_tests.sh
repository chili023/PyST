#!/bin/sh
# Alle Host-Tests der Firmware-Logik (ohne Hardware). Aufruf: sh test/run_host_tests.sh
set -e
cd "$(dirname "$0")/.."
T=${TMPDIR:-/tmp}
CC=${CC:-cc}
SRC="src/protocol.c src/diag.c src/settings.c src/burst.c src/fmt.c"
$CC -std=c11 -O2 -Iinclude src/freq_calc.c test/test_freq_calc.c -lm -o "$T/pst_tfc"
"$T/pst_tfc"
$CC -std=c11 -Iinclude -Itest/stub $SRC test/test_protocol.c -lm -o "$T/pst_tp"
python3 test/check_protocol.py "$T/pst_tp"
$CC -std=c11 -Iinclude -Itest/stub $SRC test/test_diag.c -lm -o "$T/pst_td"
"$T/pst_td"
