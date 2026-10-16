#!/usr/bin/env python3
"""Passive serial reader for the P4 box.

Opens the port WITHOUT toggling DTR/RTS (no reset, no bootloader entry), reads
for a fixed time, and prints what it sees. Use after a flash + cable replug when
`idf.py monitor`'s reset dance is not wanted.

usage: serial_tail.py [-p PORT] [-b BAUD] [-t SECONDS] [--wait]
  --wait   wait for the port node to appear first (handy right after a replug)
"""
import argparse, os, sys, time

try:
    import serial  # pyserial, present in the ESP-IDF python env
except ImportError:
    sys.exit("pyserial missing: run `source ~/esp/esp-idf/export.sh` first")

ap = argparse.ArgumentParser()
ap.add_argument("-p", "--port", default="/dev/cu.usbmodem5B610404781")
ap.add_argument("-b", "--baud", type=int, default=115200)
ap.add_argument("-t", "--seconds", type=float, default=30)
ap.add_argument("--wait", action="store_true")
a = ap.parse_args()

if a.wait:
    t0 = time.time()
    while not os.path.exists(a.port):
        if time.time() - t0 > 120:
            sys.exit(f"timed out waiting for {a.port}")
        time.sleep(0.1)
    time.sleep(0.3)  # let the driver settle

s = serial.Serial()
s.port = a.port
s.baudrate = a.baud
s.timeout = 0.2
# Leave both lines asserted (pyserial default). On the CH343P auto-reset circuit
# that holds EN and IO0 high, so opening the port does not reset the chip.
s.dtr = True
s.rts = True
s.open()
print(f"--- listening on {a.port} @ {a.baud} for {a.seconds:.0f}s ---", flush=True)
end = time.time() + a.seconds
def clean(raw: bytes) -> str:
    # Drop NUL and other control bytes (the ROM spews garbage at the wrong baud
    # during boot). Keeps the output text-only so greps downstream do not treat
    # the stream as binary and go silent.
    txt = raw.decode("utf-8", "replace")
    return "".join(c for c in txt if c == "\t" or c >= " ")

buf = b""
while time.time() < end:
    chunk = s.read(4096)
    if chunk:
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            print(clean(line), flush=True)
if buf:
    print(clean(buf), flush=True)
s.close()
print("--- done ---", flush=True)
