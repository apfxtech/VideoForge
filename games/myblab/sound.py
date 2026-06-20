import math
import os
import shutil
import subprocess
import wave

import numpy as np


SAMPLE_RATE = 44100
ARDUBOY_TONES_TICK_HZ = 780
GAIN = 0.22


def ms_to_ticks(ms):
    if ms == 0:
        return 0
    ticks = (ms * ARDUBOY_TONES_TICK_HZ + 500) // 1000
    return max(1, min(0xFFFF, ticks))


def ticks_to_ms(ticks):
    return (ticks * 1000 + (ARDUBOY_TONES_TICK_HZ // 2)) // ARDUBOY_TONES_TICK_HZ


class ToneTimeline:
    def __init__(self):
        self.events = []

    def tone(self, start_seconds, frequency, duration_ms):
        ticks = ms_to_ticks(duration_ms)
        dur_ms = ticks_to_ms(ticks)
        if dur_ms == 0:
            dur_ms = 1
        self.events.append((float(start_seconds), float(frequency), dur_ms / 1000.0))

    def write_wav(self, path, seconds):
        samples = int(round(seconds * SAMPLE_RATE))
        out = np.zeros(samples, dtype=np.float32)
        for start, freq, dur in self.events:
            if freq <= 0:
                continue
            start_i = max(0, int(round(start * SAMPLE_RATE)))
            end_i = min(samples, start_i + int(round(dur * SAMPLE_RATE)))
            if end_i <= start_i:
                continue
            t = np.arange(end_i - start_i, dtype=np.float32) / SAMPLE_RATE
            wave_data = np.where(np.sin(2.0 * math.pi * freq * t) >= 0.0, 1.0, -1.0)
            fade = min(len(wave_data) // 2, int(0.002 * SAMPLE_RATE))
            if fade:
                env = np.ones(len(wave_data), dtype=np.float32)
                env[:fade] = np.linspace(0.0, 1.0, fade, dtype=np.float32)
                env[-fade:] = np.linspace(1.0, 0.0, fade, dtype=np.float32)
                wave_data *= env
            out[start_i:end_i] += wave_data * GAIN
        peak = float(np.max(np.abs(out))) if len(out) else 0.0
        if peak > 0.95:
            out *= 0.95 / peak
        pcm = np.clip(out * 32767.0, -32768, 32767).astype("<i2")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
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
