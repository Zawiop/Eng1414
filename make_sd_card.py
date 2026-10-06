#!/usr/bin/env python3
"""
Builds the DFPlayer Mini SD card contents for DJ Bop-It.

Run on a Mac:   python3 make_sd_card.py
Output:         SD_CARD/01 02 03 04 05 ADVERT  -> copy all six folders to the
                ROOT of a FAT32-formatted micro SD card.

  01/      command voice lines ("Volume up!")
  02/      announcer lines, ding, buzzer
  03/      numbers for the score readout
  04/      background music loops (one per level)
  05/      action sound effects (air horn, bass drop, scratch...)
  ADVERT/  copies of the commands and action sounds. The DFPlayer can only
           play one thing at a time, but a file played from ADVERT pauses the
           music, plays, and then the music carries on where it left off.

Voice lines use the built-in macOS `say` command. The music and sound effects
are synthesized here, so there is nothing to download. Files are WAV unless
`lame` is installed (`brew install lame`), in which case they're MP3. To swap
in your own sounds, drop a file with the same number into the same folder
(e.g. 05/002.mp3 for a real air horn), and remember the ADVERT copy too.

The file numbers MUST match the tables in DJBopIt/DJBopIt.ino.
"""
import math
import os
import random
import shutil
import struct
import subprocess
import wave

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SD_CARD")
RATE = 44100
VOICE = None  # e.g. "Samantha" or "Daniel"; None = system default. `say -v ?` lists them.
MUSIC_MIN_SECONDS = 60  # each loop is repeated until it's at least this long

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

# ADVERT numbering: commands at 0001-0009, action sounds at 0011-0019.
ADVERT_COMMAND_BASE = 1
ADVERT_ACTION_BASE = 11

USE_MP3 = shutil.which("lame") is not None


# ------------------------------- Output --------------------------------

def save(samples, path_no_ext):
    """Write float samples (-1..1) as WAV, or MP3 if lame is available."""
    wav_path = path_no_ext + ".wav"
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples))
    return finish(wav_path)


def finish(wav_path):
    if not USE_MP3:
        return wav_path
    mp3_path = wav_path[:-4] + ".mp3"
    subprocess.run(["lame", "--quiet", "-b", "128", wav_path, mp3_path], check=True)
    os.remove(wav_path)
    return mp3_path


def say(text, path_no_ext):
    wav_path = path_no_ext + ".wav"
    cmd = ["say", "-o", wav_path, "--file-format=WAVE", f"--data-format=LEI16@{RATE}"]
    if VOICE:
        cmd += ["-v", VOICE]
    subprocess.run(cmd + [text], check=True)
    return finish(wav_path)


# ------------------------------ Synth bits ------------------------------

def mtof(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def saw(phase):
    return 2.0 * (phase % 1.0) - 1.0


def silence(secs):
    return [0.0] * int(RATE * secs)


def mix_into(buf, sig, at_secs, gain=1.0, wrap=True):
    """Add sig into buf starting at at_secs. With wrap, tails wrap to the start (seamless loops)."""
    start = int(at_secs * RATE)
    n = len(buf)
    for i, v in enumerate(sig):
        j = start + i
        if j >= n:
            if not wrap:
                break
            j %= n
        buf[j] += v * gain


def normalize(buf, peak=0.85, drive=1.3):
    m = max(abs(v) for v in buf) or 1.0
    k = math.tanh(drive)
    return [math.tanh(drive * v / m) / k * peak for v in buf]


def fade_edges(sig, ms=5):
    n = min(len(sig) // 2, int(RATE * ms / 1000))
    for i in range(n):
        sig[i] *= i / n
        sig[-1 - i] *= i / n
    return sig


# Drums
def kick(dur=0.35, top=95, bottom=45):
    out, ph = [], 0.0
    for i in range(int(RATE * dur)):
        t = i / RATE
        ph += (bottom + top * math.exp(-t * 35)) / RATE
        out.append(math.sin(2 * math.pi * ph) * math.exp(-t * 7))
    return out


def snare(dur=0.2, rng=random.Random(1)):
    out = []
    for i in range(int(RATE * dur)):
        t = i / RATE
        out.append((rng.uniform(-1, 1) * 0.8 + 0.4 * math.sin(2 * math.pi * 190 * t)) * math.exp(-t * 22))
    return out


def hat(dur=0.05, decay=70, rng=random.Random(2)):
    out, prev = [], 0.0
    for i in range(int(RATE * dur)):
        x = rng.uniform(-1, 1)
        out.append((x - prev) * 0.5 * math.exp(-i / RATE * decay))  # crude high-pass
        prev = x
    return out


# Melodic voices
def bass(freq, dur, shape="saw", cutoff=0.06, detune=0.0):
    n = int(RATE * dur)
    out, p1, p2, y = [], 0.0, 0.0, 0.0
    for i in range(n):
        p1 += freq / RATE
        p2 += freq * (1 + detune) / RATE
        if shape == "sine":
            x = math.sin(2 * math.pi * p1) + 0.2 * math.sin(4 * math.pi * p1)
        elif shape == "square":
            x = 1.0 if p1 % 1 < 0.5 else -1.0
        else:
            x = saw(p1) if not detune else 0.5 * (saw(p1) + saw(p2))
        y += cutoff * (x - y)
        env = min(1.0, i / (0.004 * RATE)) * min(1.0, (n - i) / (0.02 * RATE))
        out.append(y * env)
    return out


def keys(freqs, dur, decay=1.5):
    """Electric-piano-ish chord: sines with a little 2nd harmonic, slow decay."""
    n = int(RATE * dur)
    out = []
    for i in range(n):
        t = i / RATE
        s = sum(math.sin(2 * math.pi * f * t) + 0.25 * math.sin(4 * math.pi * f * t) for f in freqs)
        out.append(s / len(freqs) * math.exp(-t * decay) * min(1.0, (n - i) / (0.03 * RATE)))
    return out


def stab(freqs, dur=0.18, cutoff=0.25):
    n = int(RATE * dur)
    phases = [0.0] * len(freqs)
    out, y = [], 0.0
    for i in range(n):
        x = 0.0
        for k, f in enumerate(freqs):
            phases[k] += f / RATE
            x += saw(phases[k])
        y += cutoff * (x / len(freqs) - y)
        out.append(y * math.exp(-i / RATE * 14))
    return out


def pluck(freq, dur=0.15):
    n = int(RATE * dur)
    out, ph, y = [], 0.0, 0.0
    for i in range(n):
        ph += freq / RATE
        x = 1.0 if ph % 1 < 0.5 else -1.0
        y += 0.3 * (x - y)
        out.append(y * math.exp(-i / RATE * 18))
    return out


def pad(freqs, dur):
    n = int(RATE * dur)
    phases = [0.0] * (len(freqs) * 2)
    out, y = [], 0.0
    for i in range(n):
        x = 0.0
        for k, f in enumerate(freqs):
            phases[2 * k] += f / RATE
            phases[2 * k + 1] += f * 1.007 / RATE
            x += saw(phases[2 * k]) + saw(phases[2 * k + 1])
        y += 0.03 * (x / (2 * len(freqs)) - y)
        env = min(1.0, i / (0.15 * RATE)) * min(1.0, (n - i) / (0.15 * RATE))
        out.append(y * env)
    return out


# ------------------------------- Music ---------------------------------

class Loop:
    """Helper for laying out a loop on a 16-steps-per-bar grid."""

    def __init__(self, bpm, bars=8, swing=0.0):
        self.step = 60.0 / bpm / 4
        self.bar = self.step * 16
        self.swing = swing
        self.drums = silence(self.bar * bars)
        self.music = silence(self.bar * bars)
        self.kicks = []

    def t(self, bar, step):
        offset = self.swing * self.step if step % 4 == 2 else 0.0
        return bar * self.bar + step * self.step + offset

    def pattern(self, bar, pattern, sound, gain, layer="drums"):
        for step, ch in enumerate(pattern):
            if ch in "xX":
                when = self.t(bar, step)
                mix_into(getattr(self, layer), sound, when, gain * (1.0 if ch == "x" else 1.4))
                if sound is self._kick:
                    self.kicks.append(when)

    def render(self, pump=0.0, music_gain=1.0):
        music = self.music
        if pump:  # sidechain "pumping": duck the music on every kick
            n = len(music)
            duck = [1.0] * n
            for k in self.kicks:
                start = int(k * RATE)
                for i in range(int(0.25 * RATE)):
                    j = (start + i) % n
                    duck[j] = min(duck[j], 1 - pump * math.exp(-i / RATE * 14))
            music = [m * d for m, d in zip(music, duck)]
        out = [d + m * music_gain for d, m in zip(self.drums, music)]
        out = normalize(out, peak=0.8)
        reps = math.ceil(MUSIC_MIN_SECONDS / (len(out) / RATE))
        return out * reps


def track_warm_up():
    """Level 1: laid-back hip hop, 90 BPM, A minor."""
    L = Loop(90, swing=0.3)
    L._kick = kick()
    chords = [[57, 60, 64, 67], [53, 57, 60, 64], [55, 59, 60, 64], [55, 59, 62, 67]]
    roots = [45, 41, 48, 43]
    sn, hh = snare(), hat()
    for bar in range(8):
        c = bar % 4
        L.pattern(bar, "x.....x...x.....", L._kick, 1.0)
        L.pattern(bar, "....x.......x...", sn, 0.55)
        L.pattern(bar, "x.x.x.x.x.x.x.xx", hh, 0.35)
        mix_into(L.music, keys([mtof(m) for m in chords[c]], L.bar), L.t(bar, 0), 0.5)
        for step, length in ((0, 5), (6, 4), (10, 6)):
            mix_into(L.music, bass(mtof(roots[c]), L.step * length, "sine", 0.2), L.t(bar, step), 0.6)
    return L.render()


def track_house():
    """Level 2: house, 122 BPM, F minor, pumping offbeat bass and stabs."""
    L = Loop(122)
    L._kick = kick(top=80)
    chords = [[53, 56, 60], [53, 56, 61], [56, 60, 63], [55, 58, 63]]
    roots = [41, 37, 44, 39]
    clap, closed, open_hat = snare(0.15), hat(0.04), hat(0.18, decay=18)
    for bar in range(8):
        c = bar % 4
        L.pattern(bar, "x...x...x...x...", L._kick, 1.0)
        L.pattern(bar, "....x.......x...", clap, 0.45)
        L.pattern(bar, "..x...x...x...x.", open_hat, 0.3)
        L.pattern(bar, "x.x.x.x.x.x.x.x.", closed, 0.15)
        st = stab([mtof(m + 12) for m in chords[c]])
        L.pattern(bar, "...x..x....x..x.", st, 0.5, "music")
        b = bass(mtof(roots[c]), L.step * 1.8, "saw", 0.08)
        L.pattern(bar, "..x...x...x...x.", b, 0.8, "music")
    return L.render(pump=0.6)


def track_electro():
    """Level 3: electro, 128 BPM, E minor, rolling 16th bass and an arpeggio."""
    L = Loop(128)
    L._kick = kick(top=110)
    chords = [[52, 55, 59], [52, 55, 60], [55, 59, 62], [54, 57, 62]]
    roots = [40, 36, 43, 38]
    clap, closed = snare(0.15), hat(0.03)
    for bar in range(8):
        c = bar % 4
        L.pattern(bar, "x...x...x...x...", L._kick, 1.0)
        L.pattern(bar, "....x.......x..x", clap, 0.45)
        L.pattern(bar, "xxxxxxxxxxxxxxxx", closed, 0.13)
        for step in range(16):
            note = roots[c] + (12 if step % 2 else 0)
            mix_into(L.music, bass(mtof(note), L.step * 0.9, "saw", 0.12), L.t(bar, step), 0.45)
        arp = chords[c] + [chords[c][0] + 12]
        for step in range(0, 16, 2):
            mix_into(L.music, pluck(mtof(arp[(step // 2) % 4] + 12)), L.t(bar, step), 0.3)
    return L.render(pump=0.5)


def track_drum_and_bass():
    """Level 4: drum & bass, 172 BPM, D minor, reese bass and a pad."""
    L = Loop(172)
    L._kick = kick(top=120)
    chords = [[62, 65, 69], [62, 65, 70], [60, 65, 69], [60, 64, 67]]
    roots = [38, 34, 41, 36]
    sn, closed = snare(0.18), hat(0.03)
    for bar in range(8):
        c = bar % 4
        L.pattern(bar, "x.........x.....", L._kick, 1.0)
        L.pattern(bar, "....x.......x..x", sn, 0.6)
        L.pattern(bar, "x.xxx.x.x.xxx.x.", closed, 0.2)
        mix_into(L.music, bass(mtof(roots[c]), L.bar, "saw", 0.04, detune=0.012), L.t(bar, 0), 0.9)
        if bar % 2 == 0:
            mix_into(L.music, pad([mtof(m) for m in chords[c]], L.bar * 2), L.t(bar, 0), 0.35)
    return L.render()


MUSIC = [track_warm_up, track_house, track_electro, track_drum_and_bass]


# -------------------------- Action sound effects --------------------------

def air_horn():
    """Classic DJ air horn: three short blasts and a long one."""
    rng = random.Random(3)
    out = []
    for dur, on in [(0.09, 1), (0.04, 0), (0.09, 1), (0.04, 0), (0.09, 1), (0.04, 0), (0.45, 1)]:
        n = int(RATE * dur)
        p1 = p2 = p3 = 0.0
        for i in range(n):
            if not on:
                out.append(0.0)
                continue
            t = i / RATE
            f = 466 * (0.93 + 0.07 * min(1.0, t / 0.03))  # quick pitch scoop up
            p1 += f / RATE
            p2 += f * 1.008 / RATE
            p3 += f * 0.5 / RATE
            x = saw(p1) + saw(p2) + 0.6 * saw(p3) + 0.15 * rng.uniform(-1, 1)
            env = min(1.0, i / (0.004 * RATE)) * min(1.0, (n - i) / (0.015 * RATE))
            out.append(math.tanh(2.5 * x) * env)
    return out


def bass_drop():
    """Deep 808 boom that falls in pitch."""
    rng = random.Random(4)
    out, ph = [], 0.0
    for i in range(int(RATE * 0.9)):
        t = i / RATE
        ph += (38 + 110 * math.exp(-t * 4)) / RATE
        x = math.tanh(2.2 * math.sin(2 * math.pi * ph)) * math.exp(-t * 2.8)
        x += rng.uniform(-1, 1) * math.exp(-t * 150) * 0.6  # click at the start
        out.append(x)
    return out


def whoosh():
    """Quick filtered-noise swoosh ending in a click, for flipping the switch."""
    rng = random.Random(5)
    dur = 0.35
    n = int(RATE * dur)
    out, y = [], 0.0
    for i in range(n):
        t = i / n
        cutoff = 0.02 + 0.3 * math.sin(math.pi * t)
        y += cutoff * (rng.uniform(-1, 1) - y)
        out.append(y * math.sin(math.pi * t) * 2.0)
    out += [math.sin(2 * math.pi * 1800 * i / RATE) * math.exp(-i / RATE * 120) for i in range(int(RATE * 0.05))]
    return out


def scratch(direction):
    """Record scratch: a chord+noise 'record' read back and forth at varying speed."""
    rng = random.Random(6)
    src_len = RATE
    src, y = [], 0.0
    for i in range(src_len):
        t = i / RATE
        x = saw(220 * t) + saw(277 * t) + saw(330 * t) + rng.uniform(-1, 1)
        y += 0.15 * (x - y)
        src.append(y)
    out, pos = [], src_len / 2
    dur = 0.45
    for i in range(int(RATE * dur)):
        t = i / RATE
        rate = 3.0 * math.sin(2 * math.pi * 6.5 * t) + 1.2 * direction
        pos += rate
        out.append(src[int(pos) % src_len] * min(1.0, abs(rate) / 1.5))
    return fade_edges(out)


def riser(up):
    """Pitch sweep up or down."""
    dur = 0.5
    n = int(RATE * dur)
    out, ph, y = [], 0.0, 0.0
    for i in range(n):
        t = i / n
        k = t if up else 1 - t
        f = 150 * (12 ** k)  # 150 Hz -> 1800 Hz
        ph += f / RATE
        y += 0.3 * (saw(ph) - y)
        amp = (0.3 + 0.7 * t) if up else (1.0 - 0.7 * t)
        out.append(y * amp)
    return fade_edges(out)


def drum_roll(speed_up):
    """Tom hits that speed up (tempo up) or slow down (tempo down)."""
    gaps = [0.16, 0.12, 0.09, 0.07, 0.055, 0.045, 0.04]
    if not speed_up:
        gaps = gaps[::-1]
    out = silence(sum(gaps) + 0.25)
    tom = kick(0.2, top=160, bottom=110)
    t = 0.0
    for g in gaps:
        mix_into(out, tom, t, 1.0, wrap=False)
        t += g
    mix_into(out, tom, t, 1.0, wrap=False)
    return out


# Folder 05 / ADVERT 0011+: one sound per command, same order as COMMANDS.
ACTIONS = [
    bass_drop,                    # 001  Drop the beat
    air_horn,                     # 002  Air horn
    whoosh,                       # 003  Flip it
    lambda: scratch(+1),          # 004  Spin right
    lambda: scratch(-1),          # 005  Spin left
    lambda: riser(True),          # 006  Volume up
    lambda: riser(False),         # 007  Volume down
    lambda: drum_roll(True),      # 008  Tempo up
    lambda: drum_roll(False),     # 009  Tempo down
]


def ding():
    out = []
    for freq, secs in [(1318.5, 0.12), (1975.5, 0.35)]:
        n = int(RATE * secs)
        for i in range(n):
            t = i / RATE
            env = min(1.0, i / (RATE * 0.005)) * (1 - i / n) ** 1.5
            out.append((math.sin(2 * math.pi * freq * t) + 0.3 * math.sin(4 * math.pi * freq * t)) * env * 0.6)
    return out


def buzzer():
    n = int(RATE * 0.6)
    return [(0.5 if math.sin(2 * math.pi * 110 * i / RATE) > 0 else -0.5) * (1 - i / n) ** 1.5 * 0.5
            for i in range(n)]


# --------------------------------- Main ---------------------------------

def copy_to_advert(src, number):
    ext = os.path.splitext(src)[1]
    shutil.copy(src, os.path.join(OUT, "ADVERT", f"{number:04d}{ext}"))


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    for d in ("01", "02", "03", "04", "05", "ADVERT"):
        os.makedirs(os.path.join(OUT, d))
    print("Format:", "MP3" if USE_MP3 else "WAV (install lame for MP3: brew install lame)")

    print("Voice lines...")
    for i, text in enumerate(COMMANDS, start=1):
        path = say(text, os.path.join(OUT, "01", f"{i:03d}"))
        copy_to_advert(path, ADVERT_COMMAND_BASE + i - 1)

    for num, text in SFX.items():
        path = os.path.join(OUT, "02", f"{num:03d}")
        if num == 2:
            save(ding(), path)
        elif num == 3:
            save(buzzer(), path)
        else:
            say(text, path)

    print("Numbers...")
    for n in range(MAX_NUMBER + 1):
        say(str(n), os.path.join(OUT, "03", f"{n + 1:03d}"))

    print("Action sounds...")
    for i, make in enumerate(ACTIONS, start=1):
        path = save(normalize(make(), peak=0.9, drive=1.0), os.path.join(OUT, "05", f"{i:03d}"))
        copy_to_advert(path, ADVERT_ACTION_BASE + i - 1)

    for i, make in enumerate(MUSIC, start=1):
        print(f"Music track {i}/{len(MUSIC)} ({make.__doc__.split(':')[0].strip()})...")
        save(make(), os.path.join(OUT, "04", f"{i:03d}"))

    print(f"Done. Copy the folders inside {OUT} to the root of the SD card.")


if __name__ == "__main__":
    main()
