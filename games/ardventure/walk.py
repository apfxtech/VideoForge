import os
import sys

import numpy as np
import pygame

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import play
import video

LOOP_SECONDS = 30
LOOP_FRAMES = play.TARGET_FRAMERATE * LOOP_SECONDS
ROAD_CHUNK_Y = 20
ROAD_START_CHUNK_X = 1
ROAD_CHUNKS = 14
ROAD_START_X = ROAD_START_CHUNK_X * 96
ROAD_CENTER_Y = ROAD_CHUNK_Y * 96 + 48
LOOP_PIXELS = ROAD_CHUNKS * 96
PLAYER_PX = (play.VIEW_W - 12) // 2
PLAYER_PY = play.VIEW_H // 2 - 8
PLAYER_CENTER_X = PLAYER_PX + 6
ROAD_SOURCE_ROW = ROAD_CHUNK_Y * 6 + 2
ROAD_SCREEN_X = PLAYER_CENTER_X - 8
LOOP_TILES = ROAD_CHUNKS * 6

ROTATED_TILE_FIXUPS = {
    35: 37,
    37: 35,
}


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


def is_solid(wx, wy):
    return play.get_solid(_source_x(wy), _source_y_for_screen_x(wx), 0)


def blocked_south(cam_y):
    ox = PLAYER_PX - 2
    oy = cam_y + PLAYER_PY
    return is_solid(ox + 12, oy + 16) or is_solid(ox + 3, oy + 16)


def _source_x(rotated_y):
    return ROAD_START_X + (rotated_y % LOOP_PIXELS)


def _source_y_for_screen_x(rotated_x):
    source_row = ROAD_SOURCE_ROW + ((rotated_x - ROAD_SCREEN_X) // 16)
    return source_row * 16


def _rotated_tile(tile):
    return ROTATED_TILE_FIXUPS.get(tile, tile)


def draw_map(buf, cam_y, frame_boolean):
    source_col = cam_y >> 4
    while source_col * 16 - cam_y < play.VIEW_H:
        screen_y = source_col * 16 - cam_y
        source_x = ROAD_START_X + (source_col % LOOP_TILES) * 16
        source_row = ROAD_SOURCE_ROW + ((-ROAD_SCREEN_X - 16) // 16)
        while ROAD_SCREEN_X + (source_row - ROAD_SOURCE_ROW) * 16 < play.VIEW_W:
            screen_x = ROAD_SCREEN_X + (source_row - ROAD_SOURCE_ROW) * 16
            tile = play.get_tile_id(source_x, source_row * 16, frame_boolean)
            tile = _rotated_tile(tile)
            if 0 <= tile < play.TILE_COUNT:
                play._blit_overwrite(buf, play.TILE_PIX[tile], screen_x, screen_y)
            source_row += 1
        source_col += 1


def draw_player(buf, walk_frame, global_frame):
    sprite_frame = play.ANIM_SEQ[walk_frame] + 3 * play.FACING_SOUTH
    head_frame = sprite_frame // 3
    feet_frame = sprite_frame

    himg, hmsk = play.HEAD_PIX[head_frame]
    play._blit_masked(buf, himg, hmsk, PLAYER_PX, PLAYER_PY - (walk_frame % 2) + 1)
    fimg, fmsk = play.FEET_PIX[feet_frame]
    play._blit_masked(buf, fimg, fmsk, PLAYER_PX, PLAYER_PY + 8)

    if sprite_frame == 1:
        blinking = (global_frame % 32) if (global_frame % 32) < 4 else 0
        eye_frame = play.EYES_SEQ[blinking]
        play._blit_self_masked(buf, play.EYE_PIX[eye_frame], PLAYER_PX + 4, PLAYER_PY + 7)


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
    y = play.VIEW_H - mask.shape[0] - 40
    x = (play.VIEW_W - mask.shape[1]) // 2
    th = min(mask.shape[0], play.VIEW_H - y)
    tw = min(mask.shape[1], play.VIEW_W - x)
    if x < 0 or y < 0 or th <= 0 or tw <= 0:
        return
    m = mask[:th, :tw]
    buf[y:y + th, x:x + tw][m] = bit[:th, :tw][m]


def frame_state(frame_count):
    cam_y = frame_count * LOOP_PIXELS // LOOP_FRAMES
    walk_frame = (cam_y // play.ANIMATION_SPEED) % 4
    global_frame = frame_count * 32 // LOOP_FRAMES
    frame_boolean = 1 if (frame_count % 32) < 16 else 0
    return cam_y, walk_frame, global_frame, frame_boolean


def main():
    pygame.init()
    pygame.display.set_caption("Arduventure — 30s loop")

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

    frame_count = 0
    recorder = video.VideoRecorder(video.output_path(__file__), play.VIDEO_W, play.VIDEO_H, play.TARGET_FRAMERATE)

    running = True
    try:
        while running and frame_count < LOOP_FRAMES:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False

            cam_y, walk_frame, global_frame, frame_boolean = frame_state(frame_count)
            buf = np.zeros((play.VIEW_H, play.VIEW_W), dtype=np.uint8)
            draw_map(buf, cam_y, frame_boolean)
            draw_player(buf, walk_frame, global_frame)
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

            frame_count += 1
            clock.tick(play.TARGET_FRAMERATE)
    finally:
        recorder.close()
        pygame.quit()


if __name__ == "__main__":
    main()
