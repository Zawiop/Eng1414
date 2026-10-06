#!/usr/bin/env python3
"""
Builds the DFPlayer Mini SD card contents for DJ Bop-It.

Run on a Mac:   python3 make_sd_card.py
Output:         SD_CARD/01, SD_CARD/02, SD_CARD/03  -> copy those three folders
                to the ROOT of a FAT32-formatted micro SD card.

Voice lines use the built-in macOS `say` command. The ding and buzzer are
synthesized here. Everything is 16-bit mono 44.1 kHz WAV, which the DFPlayer
plays the same as MP3. To swap in your own recordings (an MP3 of a real DJ
air horn, etc.), just drop a file with the same number into the same folder,
e.g. 02/002.mp3, and delete the .wav it replaces.

The file numbers MUST match the tables in DJBopIt/DJBopIt.ino.
"""
import math
import os
import shutil
import struct
import subprocess
import wave

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SD_CARD")
RATE = 44100
VOICE = None  # e.g. "Samantha" or "Daniel"; None = system default. `say -v ?` lists them.

# Folder 01: commands. File number = command index + 1 (same order as the enum in the sketch).
COMMANDS = [
    "Drop the beat!",   # 001  button D4
    "Air horn!",        # 002  button D5
    "Flip it!",         # 003  switch D6/D7
    "Spin it right!",   # 004  encoder clockwise
    "Spin it left!",    # 005  encoder counter-clockwise
    "Volume up!",       # 006  slider A0 up
    "Volume down!",     # 007  slider A0 down
    "Tempo up!",        # 008  slider A1 up
    "Tempo down!",      # 009  slider A1 down
]

# Folder 02: sound effects + announcer lines. None = synthesized tone (see below).
SFX = {
    1: "Press a button to play.",
    2: None,  # success ding
    3: None,  # fail buzzer
    4: "Too slow!",
    5: "Wrong move!",
    6: "Your score is",
    7: "New high score!",
    8: "D J Bop It! Let's go!",
    9: "Next one:",                                  # input test mode
    10: "That was",                                  # input test mode
    11: "Skipping that one. Check its wiring.",      # input test mode
    12: "Test complete!",                            # input test mode
}

# Folder 03: numbers for the score readout. File number = score + 1 (001 = "zero").
MAX_NUMBER = 150


def say(text, path):
    cmd = ["say", "-o", path, "--file-format=WAVE", f"--data-format=LEI16@{RATE}"]
    if VOICE:
        cmd += ["-v", VOICE]
    subprocess.run(cmd + [text], check=True)


def write_tone(path, notes, volume=0.6):
    """notes: list of (frequency_hz, seconds, waveform) ; frequency 0 = silence."""
    frames = bytearray()
    for freq, secs, shape in notes:
        n = int(RATE * secs)
        for i in range(n):
            t = i / RATE
            env = min(1.0, i / (RATE * 0.005)) * (1 - i / n) ** 1.5  # quick attack, decay
            if freq == 0:
                s = 0.0
            elif shape == "square":
                s = 0.5 if math.sin(2 * math.pi * freq * t) > 0 else -0.5
            else:
                s = math.sin(2 * math.pi * freq * t) + 0.3 * math.sin(4 * math.pi * freq * t)
            frames += struct.pack("<h", int(max(-1, min(1, s * env * volume)) * 32767))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    for d in ("01", "02", "03"):
        os.makedirs(os.path.join(OUT, d))

    for i, text in enumerate(COMMANDS, start=1):
        say(text, os.path.join(OUT, "01", f"{i:03d}.wav"))

    for num, text in SFX.items():
        path = os.path.join(OUT, "02", f"{num:03d}.wav")
        if num == 2:
            write_tone(path, [(1318.5, 0.12, "sine"), (1975.5, 0.35, "sine")])  # E6 -> B6 "ding-ding"
        elif num == 3:
            write_tone(path, [(110, 0.6, "square")], volume=0.5)  # low buzz
        else:
            say(text, path)

    for n in range(MAX_NUMBER + 1):
        say(str(n), os.path.join(OUT, "03", f"{n + 1:03d}.wav"))

    print(f"Done. Copy the folders inside {OUT} to the root of the SD card.")


if __name__ == "__main__":
    main()
