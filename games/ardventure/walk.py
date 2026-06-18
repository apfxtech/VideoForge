import os
import sys
import math
from functools import lru_cache

import numpy as np
import pygame

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import play

GRASS = 0
FLOWER = 2
DOTS = 5
WHEAT = 32
ROCK = 31

MAP_W = 8
PLAYER_PX = (play.VIEW_W - 12) // 2
PLAYER_PY = play.VIEW_H // 2 - 8
PLAYER_COLS = (PLAYER_PX // 16, (PLAYER_PX + 11) // 16)


def _rnd(n):
    n &= 0xFFFFFFFF
    n = (n * 2654435761) & 0xFFFFFFFF
    n ^= n >> 13
    n = (n * 1274126177) & 0xFFFFFFFF
    n ^= n >> 16
    return n


def _border(row, salt):
    v = math.sin(row * 0.18 + salt) + 0.5 * math.sin(row * 0.37 + salt * 1.7)
    w = 1 + int((v + 1.5) / 3.0 * 3)
    return max(1, min(3, w))


@lru_cache(maxsize=4096)
def gen_row(row):
    left_w = _border(row, 0.0)
    right_w = _border(row, 5.0)
    line = [GRASS] * MAP_W
    for c in range(left_w):
        line[c] = WHEAT
    for c in range(right_w):
        line[MAP_W - 1 - c] = WHEAT
    for c in range(PLAYER_COLS[0], PLAYER_COLS[1] + 1):
        line[c] = DOTS
    for c in range(left_w, MAP_W - right_w):
        if PLAYER_COLS[0] <= c <= PLAYER_COLS[1]:
            continue
        m = _rnd(row * 31 + c * 7) % 100
        if m < 12:
            line[c] = FLOWER
        elif m < 20:
            line[c] = DOTS
        elif m < 26:
            line[c] = ROCK
    return tuple(line)


def is_solid(wx, wy):
    col = wx // 16
    if col < 0 or col >= MAP_W:
        return True
    return gen_row(wy // 16)[col] >= 14


def blocked_south(cam_y):
    ox = PLAYER_PX - 2
    oy = cam_y + PLAYER_PY
    return is_solid(ox + 12, oy + 16) or is_solid(ox + 3, oy + 16)


def draw_map(buf, cam_y):
    row = cam_y >> 4
    while row * 16 - cam_y < play.VIEW_H:
        screen_y = row * 16 - cam_y
        line = gen_row(row)
        for col in range(MAP_W):
            play._blit_overwrite(buf, play.TILE_PIX[line[col]], col * 16, screen_y)
        row += 1


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


def main():
    pygame.init()
    pygame.display.set_caption("Arduventure — endless walk")

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

    cam_y = 0
    walk_frame = 1
    global_frame = 0
    frame_count = 0

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        walking = not blocked_south(cam_y)
        if walking:
            cam_y += 1
            if frame_count % play.ANIMATION_SPEED == 0:
                walk_frame = (walk_frame + 1) % 4
        else:
            walk_frame = 1
        if frame_count % play.EYES_SPEED == 0:
            global_frame = (global_frame + 1) % 80

        buf = np.zeros((play.VIEW_H, play.VIEW_W), dtype=np.uint8)
        draw_map(buf, cam_y)
        draw_player(buf, walk_frame, global_frame)
        draw_title(buf)

        rgb = palette[buf]
        pygame.surfarray.blit_array(view_surf, np.transpose(rgb, (1, 0, 2)))

        frame_surf.fill(play.COLOR_BG)
        scaled = pygame.transform.scale(view_surf, (play.RENDER_W, play.RENDER_H))
        frame_surf.blit(scaled, (play.MARGIN, play.MARGIN))
        screen.blit(pygame.transform.scale(frame_surf, (win_w, win_h)), (0, 0))
        pygame.display.flip()

        frame_count += 1
        clock.tick(play.TARGET_FRAMERATE)

    pygame.quit()


if __name__ == "__main__":
    main()
