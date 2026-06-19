import os
import shutil
import subprocess
import tempfile

import pygame


def output_path(script_path):
    directory = os.path.dirname(os.path.abspath(script_path))
    name = os.path.splitext(os.path.basename(script_path))[0] + ".mp4"
    return os.path.join(directory, name)


class VideoRecorder:
    def __init__(self, path, width, height, fps, audio_path=None):
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg is required to write mp4")
        self.path = os.path.abspath(path)
        self.width = width
        self.height = height
        self.fps = fps
        self.audio_path = audio_path
        self.video_path = self.path
        if self.audio_path is not None:
            fd, self.video_path = tempfile.mkstemp(prefix="videoforge-", suffix=".mp4")
            os.close(fd)
        self.proc = subprocess.Popen(
            [
                "ffmpeg",
                "-y",
                "-f",
                "rawvideo",
                "-vcodec",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                "%dx%d" % (width, height),
                "-r",
                str(fps),
                "-i",
                "-",
                "-an",
                "-vcodec",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                self.video_path,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def write(self, surface):
        if surface.get_size() != (self.width, self.height):
            raise ValueError("surface size must be %dx%d" % (self.width, self.height))
        if self.proc.stdin is None:
            raise RuntimeError("video recorder is closed")
        try:
            self.proc.stdin.write(pygame.image.tostring(surface, "RGB"))
        except BrokenPipeError as exc:
            raise RuntimeError("ffmpeg stopped while writing %s" % self.path) from exc

    def close(self):
        if self.proc.stdin is not None:
            self.proc.stdin.close()
            self.proc.stdin = None
        code = self.proc.wait()
        if code != 0:
            raise RuntimeError("ffmpeg failed with exit code %d" % code)
        if self.audio_path is not None:
            try:
                code = subprocess.call(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        self.video_path,
                        "-i",
                        self.audio_path,
                        "-shortest",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-movflags",
                        "+faststart",
                        self.path,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if code != 0:
                    raise RuntimeError("ffmpeg failed to mux audio with exit code %d" % code)
            finally:
                if os.path.exists(self.video_path):
                    os.unlink(self.video_path)
