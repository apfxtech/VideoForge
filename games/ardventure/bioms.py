import os
import sys
import tempfile
import wave

import numpy as np
import pygame

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atm
import play
import video

LOCATIONS = (
    ("fieldSong", 810, 1632, 111129, 3),
    ("swampSong", 2296, 2240, 83308, 3),
    ("darkForest", 1962, 1056, 501218, 1),
    ("canyonSong", 712, 672, 153776, 3),
)

TRANSITION_FRAMES = (
    (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
    (0x00, 0x55, 0x00, 0x55, 0x00, 0x55, 0x00, 0x55),
    (0x00, 0xFF, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0xFF),
    (0xAA, 0xFF, 0xAA, 0xFF, 0xAA, 0xFF, 0xAA, 0xFF),
    (0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF),
    (0xFF, 0xAA, 0xFF, 0xAA, 0xFF, 0xAA, 0xFF, 0xAA),
    (0xFF, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0xFF, 0x00),
)

FADE_IN_SEQ = (4, 5, 6, 0)
FADE_OUT_SEQ = (0, 1, 2, 3, 4)
TRANSITION_SPEED = 8


def _build_dither(frame):
    cols = TRANSITION_FRAMES[frame]
    pat = np.zeros((8, 8), dtype=bool)
    for c in range(8):
        for r in range(8):
            pat[r, c] = (cols[c] >> r) & 1
    ty = (play.VIEW_H + 7) // 8
    tx = (play.VIEW_W + 7) // 8
    return np.tile(pat, (ty, tx))[:play.VIEW_H, :play.VIEW_W]


DITHER = [_build_dither(f) for f in range(len(TRANSITION_FRAMES))]


def _build_png_overlay(name):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    if not os.path.isfile(path):
        return None
    rgba = play._load_png_rgba(path)
    r = rgba[:, :, 0].astype(np.int32)
    g = rgba[:, :, 1].astype(np.int32)
    b = rgba[:, :, 2].astype(np.int32)
    a = rgba[:, :, 3]
    transparent = (a < 128) | ((g > 200) & (r < 60) & (b < 60))
    mask = ~transparent
    bit = ((r + g + b) > 384).astype(np.uint8)
    if play.TITLE_INVERT:
        bit = 1 - bit
    return mask, bit


FLIPPER = _build_png_overlay("flipper.png")


def draw_title(buf):
    if play.TITLE is None:
        return
    mask, bit = play.TITLE
    th = min(mask.shape[0], play.VIEW_H)
    tw = min(mask.shape[1], play.VIEW_W)
    m = mask[:th, :tw]
    buf[:th, :tw][m] = bit[:th, :tw][m]


def draw_flipper(buf):
    if FLIPPER is None:
        return
    mask, bit = FLIPPER
    y = play.VIEW_H - mask.shape[0] - 42
    x = (play.VIEW_W - mask.shape[1]) // 2
    th = min(mask.shape[0], play.VIEW_H - y)
    tw = min(mask.shape[1], play.VIEW_W - x)
    if x < 0 or y < 0 or th <= 0 or tw <= 0:
        return
    m = mask[:th, :tw]
    buf[y:y + th, x:x + tw][m] = bit[:th, :tw][m]


def draw_map(buf, cam_x, cam_y, frame_boolean):
    col = cam_x >> 4
    while col * 16 - cam_x < play.VIEW_W:
        row = cam_y >> 4
        while row * 16 - cam_y < play.VIEW_H:
            tile = play.get_tile_id(col * 16, row * 16, frame_boolean)
            if 0 <= tile < play.TILE_COUNT:
                play._blit_overwrite(buf, play.TILE_PIX[tile], col * 16 - cam_x, row * 16 - cam_y)
            row += 1
        col += 1


def draw_player(buf, cam_x, cam_y, x, y, global_frame):
    sprite_frame = play.ANIM_SEQ[1] + 3 * play.FACING_SOUTH
    px = x - cam_x + 2
    py = y - cam_y
    himg, hmsk = play.HEAD_PIX[sprite_frame // 3]
    play._blit_masked(buf, himg, hmsk, px, py)
    fimg, fmsk = play.FEET_PIX[sprite_frame]
    play._blit_masked(buf, fimg, fmsk, px, py + 8)
    blinking = (global_frame % 32) if (global_frame % 32) < 4 else 0
    play._blit_self_masked(buf, play.EYE_PIX[play.EYES_SEQ[blinking]], px + 4, py + 7)


def fade_index(local_frame, seg_frames):
    fin = TRANSITION_SPEED * len(FADE_IN_SEQ)
    if local_frame < fin:
        return FADE_IN_SEQ[local_frame // TRANSITION_SPEED]
    out_start = seg_frames - TRANSITION_SPEED * len(FADE_OUT_SEQ)
    if local_frame >= out_start:
        i = (local_frame - out_start) // TRANSITION_SPEED
        if i >= len(FADE_OUT_SEQ):
            i = len(FADE_OUT_SEQ) - 1
        return FADE_OUT_SEQ[i]
    return None


def build_audio():
    songs = atm.load_songs()
    parts = []
    seg_frames = []
    for name, sx, sy, loop, cycles in LOCATIONS:
        total = loop * cycles
        raw = atm.render_u8(songs[name], (total + atm.LOGICAL_HZ) / atm.LOGICAL_HZ)[:total]
        parts.append(raw)
        seg_frames.append(int(round(total / atm.LOGICAL_HZ * play.TARGET_FRAMERATE)))
    return np.concatenate(parts), seg_frames


def write_wav(path, audio):
    pcm = ((audio.astype(np.int16) - 128) << 8).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(atm.LOGICAL_HZ)
        w.writeframes(pcm.tobytes())


def main():
    pygame.init()
    pygame.display.set_caption("Arduventure — biomes")

    n = max(1, play.WINDOW_SCALE)
    win_w, win_h = play.VIDEO_W // n, play.VIDEO_H // n
    screen = pygame.display.set_mode((win_w, win_h))
    frame_surf = pygame.Surface((play.VIDEO_W, play.VIDEO_H))
    view_surf = pygame.Surface((play.VIEW_W, play.VIEW_H))
    clock = pygame.time.Clock()

    if play.BIT_ONE_IS_BG:
        palette = np.array([play.COLOR_INK, play.COLOR_BG], dtype=np.uint8)
    else:
        palette = np.array([play.COLOR_BG, play.COLOR_INK], dtype=np.uint8)

    audio, seg_frames = build_audio()
    fd, audio_path = tempfile.mkstemp(prefix="bioms-", suffix=".wav")
    os.close(fd)
    write_wav(audio_path, audio)
    preview_audio = atm.play_preview_file(audio_path)

    recorder = video.VideoRecorder(
        video.output_path(__file__),
        play.VIDEO_W,
        play.VIDEO_H,
        play.TARGET_FRAMERATE,
        audio_path=audio_path,
    )

    frame_count = 0
    global_frame = 0
    running = True
    try:
        for (name, sx, sy, loop, cycles), total_frames in zip(LOCATIONS, seg_frames):
            if not running:
                break
            cam_x = sx - (play.VIEW_W // 2 - 8)
            cam_y = sy - (play.VIEW_H // 2 - 8)
            for local_frame in range(total_frames):
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        running = False
                if not running:
                    break

                frame_boolean = 1 if (frame_count % 32) < 16 else 0
                buf = np.zeros((play.VIEW_H, play.VIEW_W), dtype=np.uint8)
                draw_map(buf, cam_x, cam_y, frame_boolean)
                draw_player(buf, cam_x, cam_y, sx, sy, global_frame)

                fade = fade_index(local_frame, total_frames)
                if fade is not None:
                    buf[DITHER[fade]] = 0

                draw_title(buf)
                draw_flipper(buf)

                rgb = palette[buf]
                pygame.surfarray.blit_array(view_surf, np.transpose(rgb, (1, 0, 2)))

                frame_surf.fill(play.COLOR_BG)
                scaled = pygame.transform.scale(view_surf, (play.RENDER_W, play.RENDER_H))
                frame_surf.blit(scaled, (play.MARGIN, play.MARGIN))
                recorder.write(frame_surf)
                screen.blit(pygame.transform.scale(frame_surf, (win_w, win_h)), (0, 0))
                pygame.display.flip()

                if frame_count % play.EYES_SPEED == 0:
                    global_frame = (global_frame + 1) % 80
                frame_count += 1
                clock.tick(play.TARGET_FRAMERATE)
    finally:
        if preview_audio is not None:
            preview_audio.stop()
        recorder.close()
        if os.path.exists(audio_path):
            os.unlink(audio_path)
        pygame.quit()


if __name__ == "__main__":
    main()
