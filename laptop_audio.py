#!/usr/bin/env python3
"""
Plays DJ Bop-It sounds on the laptop instead of the DFPlayer, for testing.

1. In DJBopIt.ino set  #define LAPTOP_AUDIO 1  and upload.
2. Close the Arduino Serial Monitor (only one program can use the port).
3. Run:   python3 laptop_audio.py
   or:    python3 laptop_audio.py /dev/cu.usbserial-XXXX   to pick the port

The Arduino prints lines like "PLAY 1 3"; this script plays SD_CARD/01/003.*
with macOS `afplay`. Every other line from the Arduino (the game log) is
printed as-is, so this also replaces the Serial Monitor. Ctrl+C to quit.

Only uses the Python standard library, no installs needed. macOS only.
"""
import glob
import os
import subprocess
import sys
import termios

BAUD = termios.B115200
SD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SD_CARD")
VOLUME = "1.0"  # afplay volume, 0.0 - 1.0 (and a bit above for louder)


def find_port():
    ports = []
    for pattern in ("/dev/cu.usbserial*", "/dev/cu.usbmodem*", "/dev/cu.wchusbserial*"):
        ports += glob.glob(pattern)
    if not ports:
        sys.exit("No Arduino found. Plug in the Nano (and check the USB cable carries data).")
    if len(ports) > 1:
        print(f"Several ports found, using {ports[0]}. Others: {', '.join(ports[1:])}")
    return ports[0]


def open_serial(port):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY)
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0                                               # iflag: raw input
    attrs[1] = 0                                               # oflag: raw output
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL    # cflag: 8N1
    attrs[3] = 0                                               # lflag: no echo, no line editing
    attrs[4] = attrs[5] = BAUD                                 # in/out speed
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return os.fdopen(fd, "rb", buffering=0)


def find_clip(folder, file):
    for ext in ("mp3", "wav"):
        path = os.path.join(SD_DIR, f"{folder:02d}", f"{file:03d}.{ext}")
        if os.path.exists(path):
            return path
    return None


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else find_port()
    try:
        ser = open_serial(port)
    except OSError as e:
        sys.exit(f"Couldn't open {port}: {e}\nIs the Arduino Serial Monitor still open? Close it and retry.")
    print(f"Listening on {port} (the Nano restarts when this connects). Ctrl+C to quit.\n")

    player = None
    buf = b""
    try:
        while True:
            buf += ser.read(64)
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode(errors="replace").strip()
                parts = line.split()
                if len(parts) == 3 and parts[0] == "PLAY" and parts[1].isdigit() and parts[2].isdigit():
                    path = find_clip(int(parts[1]), int(parts[2]))
                    if path is None:
                        print(f"  [missing file: SD_CARD/{int(parts[1]):02d}/{int(parts[2]):03d}]")
                        continue
                    # Like the DFPlayer: a new sound cuts off the one playing.
                    if player and player.poll() is None:
                        player.terminate()
                    player = subprocess.Popen(["afplay", "-v", VOLUME, path])
                    print(f"  [sound {os.path.relpath(path, SD_DIR)}]")
                elif line:
                    print(line)
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        if player and player.poll() is None:
            player.terminate()
        ser.close()


if __name__ == "__main__":
    main()
