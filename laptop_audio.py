#!/usr/bin/env python3
"""
Plays DJ Bop-It sounds on the laptop instead of the DFPlayer, for testing.

1. In DJBopIt.ino set  #define LAPTOP_AUDIO 1  and upload.
2. Close the Arduino Serial Monitor (only one program can use the port).
3. Run:   python3 laptop_audio.py
   or:    python3 laptop_audio.py /dev/cu.usbserial-XXXX   to pick the port

The Arduino prints audio commands; this script plays the matching files from
SD_CARD with macOS `afplay`, behaving like the DFPlayer does:
  PLAY f n   play SD_CARD/<f>/<n> (stops the music, like the DFPlayer)
  MUSIC n    loop SD_CARD/04/<n> as background music
  AD n       pause the music, play SD_CARD/ADVERT/<n>, then resume the music
  STOP       stop everything
Every other line from the Arduino (the game log) is printed as-is, so this
also replaces the Serial Monitor. Ctrl+C to quit.

Only uses the Python standard library, no installs needed. macOS only.
"""
import glob
import os
import select
import signal
import subprocess
import sys
import termios

BAUD = termios.B115200
SD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SD_CARD")
VOLUME = "1.0"        # afplay volume for voice and effects, 0.0 - 1.0
MUSIC_VOLUME = "0.6"  # background music volume


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
    return fd


def find_file(folder, name):
    for ext in ("mp3", "wav"):
        path = os.path.join(SD_DIR, folder, f"{name}.{ext}")
        if os.path.exists(path):
            return path
    print(f"  [missing file: SD_CARD/{folder}/{name}]")
    return None


class Player:
    """Mimics the DFPlayer: one main track (music or clip) plus advert clips that pause it."""

    def __init__(self):
        self.main = None        # afplay process for the current track
        self.music_path = None  # set while the main track is looping music
        self.music_paused = False
        self.ad = None

    @staticmethod
    def _kill(proc):
        if proc and proc.poll() is None:
            proc.send_signal(signal.SIGCONT)  # a stopped process can't die until resumed
            proc.terminate()

    def stop(self):
        self._kill(self.ad)
        self._kill(self.main)
        self.ad = self.main = self.music_path = None
        self.music_paused = False

    def play(self, path):
        self.stop()
        self.main = subprocess.Popen(["afplay", "-v", VOLUME, path])

    def music(self, path):
        self.stop()
        self.music_path = path
        self.main = subprocess.Popen(["afplay", "-v", MUSIC_VOLUME, path])

    def advert(self, path):
        if not (self.main and self.main.poll() is None):
            print("  [advert ignored: nothing playing (the DFPlayer would ignore it too)]")
            return
        self._kill(self.ad)
        if not self.music_paused:
            self.main.send_signal(signal.SIGSTOP)
            self.music_paused = True
        self.ad = subprocess.Popen(["afplay", "-v", VOLUME, path])

    def tick(self):
        """Resume music after an advert, and loop music when it ends."""
        if self.ad and self.ad.poll() is not None:
            self.ad = None
            if self.music_paused and self.main:
                self.main.send_signal(signal.SIGCONT)
                self.music_paused = False
        if self.music_path and not self.music_paused and self.main.poll() is not None:
            self.main = subprocess.Popen(["afplay", "-v", MUSIC_VOLUME, self.music_path])


def handle(line, player):
    parts = line.split()
    if len(parts) == 3 and parts[0] == "PLAY" and parts[1].isdigit() and parts[2].isdigit():
        path = find_file(f"{int(parts[1]):02d}", f"{int(parts[2]):03d}")
        if path:
            player.play(path)
            print(f"  [sound {os.path.relpath(path, SD_DIR)}]")
    elif len(parts) == 2 and parts[0] == "MUSIC" and parts[1].isdigit():
        path = find_file("04", f"{int(parts[1]):03d}")
        if path:
            player.music(path)
            print(f"  [music {os.path.relpath(path, SD_DIR)}]")
    elif len(parts) == 2 and parts[0] == "AD" and parts[1].isdigit():
        path = find_file("ADVERT", f"{int(parts[1]):04d}")
        if path:
            player.advert(path)
            print(f"  [over music {os.path.relpath(path, SD_DIR)}]")
    elif parts == ["STOP"]:
        player.stop()
    elif line:
        print(line)


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else find_port()
    try:
        fd = open_serial(port)
    except OSError as e:
        sys.exit(f"Couldn't open {port}: {e}\nIs the Arduino Serial Monitor still open? Close it and retry.")
    print(f"Listening on {port} (the Nano restarts when this connects). Ctrl+C to quit.\n")

    player = Player()
    buf = b""
    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                buf += os.read(fd, 256)
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    handle(raw.decode(errors="replace").strip(), player)
            player.tick()
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        player.stop()
        os.close(fd)


if __name__ == "__main__":
    main()
