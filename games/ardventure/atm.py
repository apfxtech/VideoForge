import argparse
import os
import re
import shutil
import subprocess
import wave

import numpy as np


LOGICAL_HZ = 31250
DEFAULT_SECONDS = 30.0
MASTER_GAIN_Q8 = int(1.8 * 256.0 + 0.5)

NOTE_TABLE = (
    0, 262, 277, 294, 311, 330, 349, 370, 392, 415,
    440, 466, 494, 523, 554, 587, 622, 659, 698, 740,
    784, 831, 880, 932, 988, 1047, 1109, 1175, 1245, 1319,
    1397, 1480, 1568, 1661, 1760, 1865, 1976, 2093, 2217, 2349,
    2489, 2637, 2794, 2960, 3136, 3322, 3520, 3729, 3951, 4186,
    4435, 4699, 4978, 5274, 5588, 5920, 6272, 6645, 7040, 7459,
    7902, 8372, 8870, 9397,
)

SONG_NAMES = (
    "titleSong",
    "nameSong",
    "badNews",
    "fieldSong",
    "youDied",
    "darkForest",
    "battleSong",
    "swampSong",
    "canyonSong",
)


def _source_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", ".sources", "arduventure", "game", "songs.h"))


def _strip_comments(text):
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _byte_expr(expr):
    expr = expr.strip().replace("(uint8_t)", "")
    if not expr:
        raise ValueError("empty byte expression")
    return int(eval(expr, {"__builtins__": {}}, {})) & 0xFF


def load_songs(path=None):
    path = path or _source_path()
    text = _strip_comments(open(path, encoding="utf-8", errors="ignore").read())
    songs = {}
    for match in re.finditer(r"\bSong\s+([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*\]\s*=\s*\{", text):
        name = match.group(1)
        i = match.end()
        depth = 1
        j = i
        while j < len(text) and depth:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        parts = [p.strip() for p in text[i:j - 1].split(",") if p.strip()]
        songs[name] = [_byte_expr(p) for p in parts]
    return songs


def _i8(value):
    value &= 0xFF
    return value - 256 if value & 0x80 else value


def _u8(value):
    return value & 0xFF


def _u16(value):
    return value & 0xFFFF


def _clamp(value, lo, hi):
    return lo if value < lo else hi if value > hi else value


def _tick_div_from_rate(tick_rate):
    tick_rate = max(1, tick_rate & 0xFF)
    return max(1, LOGICAL_HZ // tick_rate)


class _Reader:
    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos

    def u8(self):
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u16_le(self):
        lo = self.u8()
        hi = self.u8()
        return lo | (hi << 8)

    def vle(self):
        q = 0
        while True:
            d = self.u8()
            q = (q << 7) | (d & 0x7F)
            if not (d & 0x80):
                return q


class _Channel:
    def __init__(self):
        self.ptr = 0
        self.note = 0
        self.stack_pointer = [0] * 7
        self.stack_counter = [0] * 7
        self.stack_track = [0] * 7
        self.stack_index = 0
        self.repeat_point = 0
        self.delay = 0
        self.counter = 0
        self.track = 0
        self.freq = 0
        self.vol = 0
        self.vol_fre_slide = 0
        self.vol_fre_config = 0
        self.vol_fre_count = 0
        self.arp_notes = 0
        self.arp_timing = 0
        self.arp_count = 0
        self.re_config = 0
        self.re_count = 0
        self.trans_config = 0
        self.trevi_depth = 0
        self.trevi_config = 0
        self.trevi_count = 0
        self.glis_config = 0
        self.glis_count = 0


class _Osc:
    def __init__(self):
        self.vol = 0
        self.freq = 0
        self.phase = 0


class Synth:
    def __init__(self, song):
        self.song = song
        self.track_count = song[0]
        self.track_list_pos = 1
        self.track_base = 1 + self.track_count * 2 + 4
        self.channels = [_Channel() for _ in range(4)]
        self.osc = [_Osc() for _ in range(4)]
        self.channel_active_mute = 0b11110000
        self.tick_rate = 25
        self.tick_div = _tick_div_from_rate(self.tick_rate)
        self.tick_acc = 0
        self.running = True
        self.osc[3].freq = 1
        self.channels[3].freq = 1

        entry_pos = 1 + self.track_count * 2
        for n in range(4):
            track = song[entry_pos + n]
            self.channels[n].track = track
            self.channels[n].ptr = self.track_pointer(track)

    def track_pointer(self, track):
        i = self.track_list_pos + track * 2
        offset = self.song[i] | (self.song[i + 1] << 8)
        return self.track_base + offset

    def _reader(self, channel):
        return _Reader(self.song, channel.ptr)

    def _set_reader(self, channel, reader):
        channel.ptr = reader.pos

    def _read_vle_at_channel(self, channel):
        reader = self._reader(channel)
        value = reader.vle()
        self._set_reader(channel, reader)
        return value

    def _read_u16_at_channel(self, channel):
        reader = self._reader(channel)
        value = reader.u16_le()
        self._set_reader(channel, reader)
        return value

    def playroutine(self):
        for n in range(4):
            ch = self.channels[n]

            if ch.re_config:
                if ch.re_count >= (ch.re_config & 0x03):
                    self.osc[n].freq = NOTE_TABLE[ch.re_config >> 2]
                    ch.re_count = 0
                else:
                    ch.re_count = _u8(ch.re_count + 1)

            if ch.glis_config:
                if ch.glis_count >= (ch.glis_config & 0x7F):
                    if ch.glis_config & 0x80:
                        ch.note = _u8(ch.note - 1)
                    else:
                        ch.note = _u8(ch.note + 1)
                    if ch.note < 1:
                        ch.note = 1
                    elif ch.note > 63:
                        ch.note = 63
                    ch.freq = NOTE_TABLE[ch.note]
                    ch.glis_count = 0
                else:
                    ch.glis_count = _u8(ch.glis_count + 1)

            if ch.vol_fre_slide:
                if not ch.vol_fre_count:
                    vf = ch.freq if (ch.vol_fre_config & 0x40) else ch.vol
                    vf += ch.vol_fre_slide
                    if not (ch.vol_fre_config & 0x80):
                        if vf < 0:
                            vf = 0
                        elif ch.vol_fre_config & 0x40:
                            if vf > 9397:
                                vf = 9397
                        elif vf > 63:
                            vf = 63
                    if ch.vol_fre_config & 0x40:
                        ch.freq = _u16(vf)
                    else:
                        ch.vol = _u8(vf)
                ch.vol_fre_count = _u8(ch.vol_fre_count + 1)
                if ch.vol_fre_count >= (ch.vol_fre_config & 0x3F):
                    ch.vol_fre_count = 0

            if ch.arp_notes and ch.note:
                if (ch.arp_count & 0x1F) < (ch.arp_timing & 0x1F):
                    ch.arp_count = _u8(ch.arp_count + 1)
                else:
                    if (ch.arp_count & 0xE0) == 0x00:
                        ch.arp_count = 0x20
                    elif (ch.arp_count & 0xE0) == 0x20 and not (ch.arp_timing & 0x40) and ch.arp_notes != 0xFF:
                        ch.arp_count = 0x40
                    else:
                        ch.arp_count = 0x00

                    arp_note = ch.note
                    if (ch.arp_count & 0xE0) != 0x00:
                        if ch.arp_notes == 0xFF:
                            arp_note = 0
                        else:
                            arp_note = _u8(arp_note + (ch.arp_notes >> 4))
                    if (ch.arp_count & 0xE0) == 0x40:
                        arp_note = _u8(arp_note + (ch.arp_notes & 0x0F))

                    idx = _clamp(arp_note + ch.trans_config, 0, 63)
                    ch.freq = NOTE_TABLE[idx]

            if ch.trevi_depth:
                vt = ch.freq if (ch.trevi_config & 0x40) else ch.vol
                vt = vt + ch.trevi_depth if (ch.trevi_count & 0x80) else vt - ch.trevi_depth
                if vt < 0:
                    vt = 0
                elif ch.trevi_config & 0x40:
                    if vt > 9397:
                        vt = 9397
                elif vt > 63:
                    vt = 63

                if ch.trevi_config & 0x40:
                    ch.freq = _u16(vt)
                else:
                    ch.vol = _u8(vt)

                if (ch.trevi_count & 0x1F) < (ch.trevi_config & 0x1F):
                    ch.trevi_count = _u8(ch.trevi_count + 1)
                else:
                    ch.trevi_count = 0 if (ch.trevi_count & 0x80) else 0x80

            if ch.delay:
                if ch.delay != 0xFFFF:
                    ch.delay = _u16(ch.delay - 1)
            else:
                while ch.delay == 0:
                    cmd = self.song[ch.ptr]
                    ch.ptr += 1

                    if cmd < 64:
                        ch.note = cmd
                        if ch.note:
                            ch.note = _u8(ch.note + ch.trans_config)
                        ni = _clamp(ch.note, 0, 63)
                        ch.freq = NOTE_TABLE[ni]
                        if not ch.vol_fre_config:
                            ch.vol = ch.re_count
                        if ch.arp_timing & 0x20:
                            ch.arp_count = 0
                    elif cmd < 160:
                        fx = cmd - 64
                        if fx == 0:
                            ch.vol = self.song[ch.ptr]
                            ch.ptr += 1
                            ch.re_count = ch.vol
                        elif fx in (1, 4):
                            ch.vol_fre_slide = _i8(self.song[ch.ptr])
                            ch.ptr += 1
                            ch.vol_fre_config = 0x00 if fx == 1 else 0x40
                        elif fx in (2, 5):
                            ch.vol_fre_slide = _i8(self.song[ch.ptr])
                            ch.vol_fre_config = self.song[ch.ptr + 1]
                            ch.ptr += 2
                        elif fx in (3, 6):
                            ch.vol_fre_slide = 0
                        elif fx == 7:
                            ch.arp_notes = self.song[ch.ptr]
                            ch.arp_timing = self.song[ch.ptr + 1]
                            ch.ptr += 2
                        elif fx == 8:
                            ch.arp_notes = 0
                        elif fx == 9:
                            ch.re_config = self.song[ch.ptr]
                            ch.ptr += 1
                        elif fx == 10:
                            ch.re_config = 0
                        elif fx == 11:
                            ch.trans_config = _i8(_u8(ch.trans_config + _i8(self.song[ch.ptr])))
                            ch.ptr += 1
                        elif fx == 12:
                            ch.trans_config = _i8(self.song[ch.ptr])
                            ch.ptr += 1
                        elif fx == 13:
                            ch.trans_config = 0
                        elif fx in (14, 16):
                            depth = self._read_u16_at_channel(ch)
                            cfg = self._read_u16_at_channel(ch)
                            ch.trevi_depth = depth & 0xFF
                            ch.trevi_config = (cfg & 0xFF) + (0x00 if fx == 14 else 0x40)
                        elif fx in (15, 17):
                            ch.trevi_depth = 0
                        elif fx == 18:
                            ch.glis_config = self.song[ch.ptr]
                            ch.ptr += 1
                        elif fx == 19:
                            ch.glis_config = 0
                        elif fx == 20:
                            ch.arp_notes = 0xFF
                            ch.arp_timing = self.song[ch.ptr]
                            ch.ptr += 1
                        elif fx == 21:
                            ch.arp_notes = 0
                        elif fx == 92:
                            self.tick_rate = _u8(self.tick_rate + self.song[ch.ptr])
                            ch.ptr += 1
                            if self.tick_rate < 1:
                                self.tick_rate = 1
                            self.tick_div = _tick_div_from_rate(self.tick_rate)
                        elif fx == 93:
                            self.tick_rate = self.song[ch.ptr]
                            ch.ptr += 1
                            if self.tick_rate < 1:
                                self.tick_rate = 1
                            self.tick_div = _tick_div_from_rate(self.tick_rate)
                        elif fx == 94:
                            for i in range(4):
                                self.channels[i].repeat_point = self.song[ch.ptr]
                                ch.ptr += 1
                        elif fx == 95:
                            self.channel_active_mute ^= 1 << (n + 4)
                            ch.vol = 0
                            ch.delay = 0xFFFF
                    elif cmd < 224:
                        ch.delay = cmd - 159
                    elif cmd == 224:
                        ch.delay = self._read_vle_at_channel(ch) + 65
                    elif cmd == 252 or cmd == 253:
                        new_counter = 0 if cmd == 252 else self.song[ch.ptr]
                        if cmd == 253:
                            ch.ptr += 1
                        new_track = self.song[ch.ptr]
                        ch.ptr += 1

                        if new_track != ch.track:
                            if ch.stack_index < 7:
                                ch.stack_counter[ch.stack_index] = ch.counter
                                ch.stack_track[ch.stack_index] = ch.track
                                ch.stack_pointer[ch.stack_index] = ch.ptr - self.track_base
                                ch.stack_index += 1
                            ch.track = new_track
                        ch.counter = new_counter
                        ch.ptr = self.track_pointer(ch.track)
                    elif cmd == 254:
                        if ch.counter > 0 or ch.stack_index == 0:
                            if ch.counter:
                                ch.counter = _u8(ch.counter - 1)
                            ch.ptr = self.track_pointer(ch.track)
                        else:
                            if ch.stack_index == 0:
                                ch.delay = 0xFFFF
                            else:
                                ch.stack_index -= 1
                                ch.ptr = ch.stack_pointer[ch.stack_index] + self.track_base
                                ch.counter = ch.stack_counter[ch.stack_index]
                                ch.track = ch.stack_track[ch.stack_index]
                    elif cmd == 255:
                        ch.ptr += self._read_vle_at_channel(ch)

                if ch.delay != 0xFFFF:
                    ch.delay = _u16(ch.delay - 1)

            if not (self.channel_active_mute & (1 << n)):
                if n == 3:
                    self.osc[n].vol = ch.vol >> 1
                else:
                    self.osc[n].freq = ch.freq
                    self.osc[n].vol = ch.vol

            if not (self.channel_active_mute & 0xF0):
                repeat_song = sum(c.repeat_point for c in self.channels) & 0xFF
                if repeat_song:
                    for k in range(4):
                        self.channels[k].ptr = self.track_pointer(self.channels[k].repeat_point)
                        self.channels[k].delay = 0
                    self.channel_active_mute = 0b11110000
                else:
                    self.running = False

    def render_sample_u8(self):
        o2 = self.osc[2]
        o2.phase = _u16(o2.phase + o2.freq)
        phase2 = _i8(o2.phase >> 8)
        if phase2 < 0:
            phase2 = _i8(~phase2)
        phase2 = _i8(phase2 << 1)
        phase2 = _i8(phase2 - 128)
        c2 = _i8((((phase2 * _i8(o2.vol)) << 1) >> 8) & 0xFF)
        mix = c2

        o0 = self.osc[0]
        o0.phase = _u16(o0.phase + o0.freq)
        c0 = _i8(o0.vol)
        if o0.phase >= 0xC000:
            c0 = -c0
        mix += c0

        o1 = self.osc[1]
        o1.phase = _u16(o1.phase + o1.freq)
        c1 = _i8(o1.vol)
        if o1.phase & 0x8000:
            c1 = -c1
        mix += c1

        o3 = self.osc[3]
        freq = _u16(o3.freq << 1)
        if freq & 0x8000:
            freq ^= 1
        if freq & 0x4000:
            freq ^= 1
        o3.freq = freq
        c3 = _i8(o3.vol)
        if freq & 0x8000:
            c3 = -c3
        mix += c3

        centered = (mix * MASTER_GAIN_Q8) >> 9
        centered = _clamp(centered, -127, 127)
        outv = _clamp(128 + centered, 0, 255)

        self.tick_acc += 1
        if self.tick_acc >= self.tick_div:
            self.tick_acc = 0
            if self.running:
                self.playroutine()

        return outv


def render_u8(song, seconds=DEFAULT_SECONDS):
    synth = Synth(song)
    samples = int(round(seconds * LOGICAL_HZ))
    out = np.empty(samples, dtype=np.uint8)
    for i in range(samples):
        out[i] = synth.render_sample_u8()
    return out


def write_wav(path, song, seconds=DEFAULT_SECONDS):
    audio = render_u8(song, seconds)
    pcm = ((audio.astype(np.int16) - 128) << 8).astype("<i2")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(LOGICAL_HZ)
        wav.writeframes(pcm.tobytes())
    return path


class _ProcessPreview:
    def __init__(self, proc):
        self.proc = proc

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class _NullPreview:
    def stop(self):
        pass


def play_preview_file(path):
    if shutil.which("afplay") is not None:
        return _ProcessPreview(subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    if shutil.which("ffplay") is not None:
        return _ProcessPreview(subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ))
    return _NullPreview()


def export_all(directory=None, seconds=DEFAULT_SECONDS):
    here = os.path.dirname(os.path.abspath(__file__))
    directory = directory or os.path.join(here, "music")
    songs = load_songs()
    paths = []
    for name in SONG_NAMES:
        paths.append(write_wav(os.path.join(directory, name + ".wav"), songs[name], seconds=seconds))
    return paths


def main():
    parser = argparse.ArgumentParser(description="Render Arduventure ATM music using the bundled ATMlib timing.")
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--song", choices=SONG_NAMES, default="fieldSong")
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    parser.add_argument("--out")
    args = parser.parse_args()

    if args.export:
        for path in export_all(args.out, args.seconds):
            print(path)
        return

    songs = load_songs()
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "music", args.song + ".wav")
    print(write_wav(out, songs[args.song], args.seconds))


if __name__ == "__main__":
    main()
