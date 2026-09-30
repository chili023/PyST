"""Wertet die Ausgabe von test_protocol wie LabVIEW aus (Stringhandling 0.0.2 bzw. STM-Unflatten)."""
import struct, subprocess, sys

exe = sys.argv[1]
ok = True
def check(name, cond):
    global ok
    print(("OK   " if cond else "FAIL ") + name)
    ok &= bool(cond)

# --- Arduino/LabVIEW 3.2.1 -----------------------------------------------
out = subprocess.run([exe], capture_output=True).stdout.decode("latin-1")
env_line, rest = out.split("\r\n", 1)
# MessungKlimadaten: '.'->',', Feld1 = T, Feld2/100 = mbar
f = env_line.replace(".", ",").split(";")
check("e-Antwort 'T;P;H' = " + env_line, env_line == "21.5;96512;45.3")
check("Druck in mbar = 965,12", float(f[1]) / 100 == 965.12)
lines = [l for l in rest.split("\n") if l]
print("   Telegramme:", lines[:3], "...", len(lines), "gesamt")
fields = [l.split(";") for l in lines]
check("7 Felder je Telegramm", all(len(x) == 7 for x in fields))
check("Zyklus startet bei 1 und zaehlt nach 2. 'm' weiter", [int(x[0]) for x in fields[:6]] == [1, 2, 3, 4, 5, 6])
check("Messfrequenz 60.00", fields[0][1] == "60.00")
check("Zuendung/Rolle 50.00/725.50", fields[0][2:4] == ["50.00", "725.50"])
check("EGT1 656, AFR 0.00 (keine Quelle), EGT2 612", fields[0][4:7] == ["656", "0.00", "612"])
check("Autostopp: nach 5 s ohne Signal keine Telegramme mehr", len(lines) == 3 + 2 + 300)
# deutsches Windows: Zahl bis zum ersten Nicht-Ziffer-Zeichen ('.') -> ganze Hz wie Mega
check("deutsches Windows liest Rolle als 725", int(fields[0][3].split(".")[0]) == 725)

# --- STM-LabVIEW 0.2.0 ----------------------------------------------------
out = subprocess.run([exe, "legacy"], capture_output=True).stdout
check("t_ok", out.startswith(b"t_ok\r\n"))
out = out[len(b"t_ok\r\n"):]
env, out = out.split(b"\r\n", 1)
check("e-Antwort legacy", env == b"21.5;96512;45.3")
check("3 Binaertelegramme a 20 Byte", len(out) == 60)
v = struct.unpack("<10H", out[:20])
print("   Telegramm 1:", v)
check("cycle 1", v[0] == 1)
check("Messfrequenz 66,67 Hz (/256)", abs(v[1] / 256 - 66.667) < 0.01)
check("Zuendung 50 Hz (/8), Rolle 725,5 Hz (/8)", v[2] / 8 == 50 and v[3] / 8 == 725.5)
check("EGT1 655,75 (/16), EGT2 aus = 0", v[4] / 16 == 655.75 and v[5] == 0)
check("AFR aus ADC2 = 12,5 in beiden Feldern (/1024)", v[6] / 1024 == 12.5 and v[7] / 1024 == 12.5)
check("ADC-Rohwerte x16", v[8] == 2048 * 16 and v[9] == 1024 * 16)
print("\nALLE TESTS OK" if ok else "\nFEHLGESCHLAGEN")
sys.exit(0 if ok else 1)
