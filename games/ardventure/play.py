import os
import re
import sys

import numpy as np
import pygame

import video

SCALE = 8
VIDEO_W, VIDEO_H = 1080, 1920
VIEW_W = 128
MARGIN = (VIDEO_W - VIEW_W * SCALE) // 2
VIEW_H = (VIDEO_H - 2 * MARGIN) // SCALE
RENDER_W = VIEW_W * SCALE
RENDER_H = VIEW_H * SCALE

COLOR_BG = (0xFF, 0x82, 0x00)
COLOR_INK = (0x00, 0x00, 0x00)

BIT_ONE_IS_BG = True

WINDOW_SCALE = 4

TITLE_INVERT = False

TARGET_FRAMERATE = 60
ANIMATION_SPEED = 8
EYES_SPEED = 4

FACING_SOUTH, FACING_WEST, FACING_NORTH, FACING_EAST = 0, 1, 2, 3

ANIM_SEQ = (0, 1, 2, 1)
EYES_SEQ = (2, 1, 0, 1)

COLLISION_POINTS = (
    (12, 16), (3, 16),
    (1, 14), (1, 9),
    (3, 7), (12, 7),
    (14, 9), (14, 14),
)

TOTAL_REGIONS = 20
REGION_FIELDS_SWAMP, REGION_SWAMP_FOREST, REGION_FOREST_CANYON = 0, 1, 2
REGION_FIELDS_CANYONS = 3
REGION_SWAMP_ISLAND_ONE, REGION_SWAMP_ISLAND_TWO = 4, 5
REGION_LONG_ROAD, REGION_APPLE_FARM, REGION_YOUR_GARDEN = 6, 7, 8
REGION_FIELDS, REGION_SWAMP, REGION_FOREST, REGION_CANYONS = 9, 10, 11, 12
REGION_HOUSE_INTERIOR, REGION_INN_INTERIOR, REGION_YOUR_INTERIOR = 13, 14, 15
REGION_SHOP_INTERIOR, REGION_TREE_INTERIOR, REGION_CAVE_INTERIOR = 16, 17, 18
REGION_ALL_BLACK = 19

TILE_ROCK = 31
TILE_SIGN = 56
TILE_CLOSED_CHEST = 57

PLAYER_START_X = 9 * 16
PLAYER_START_Y = 153 * 16 + 12

def _find_source():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", ".."))
    path = os.path.join(root, ".sources", "arduventure", "game", "bitmaps.h")
    if not os.path.isfile(path):
        sys.exit("Не найден исходник: %s" % path)
    return path

def _parse_arrays(text, names):
    out = {}
    for name in names:
        m = re.search(r"\b%s\s*\[\s*\]\s*=\s*\{" % re.escape(name), text)
        if not m:
            sys.exit("Массив %s не найден в bitmaps.h" % name)
        i = m.end()
        depth = 1
        j = i
        while j < len(text) and depth:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        body = text[i:j - 1]
        body = re.sub(r"//[^\n]*", "", body)
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        out[name] = [int(h, 16) for h in re.findall(r"0[xX][0-9a-fA-F]+", body)]
    return out

SRC = _parse_arrays(
    open(_find_source(), encoding="utf-8", errors="ignore").read(),
    [
        "tileSheet",
        "playerHead_plus_mask",
        "playerFeet_plus_mask",
        "eyesBlinking",
        "chunks",
        "regions",
        "solid_map",
    ],
)

CHUNKS = SRC["chunks"]
REGIONS = SRC["regions"]
SOLID_MAP = SRC["solid_map"]

def _overwrite_sprite(data, w, h, frame):
    pages = h // 8
    base = frame * w * pages
    pix = np.zeros((h, w), dtype=np.uint8)
    for p in range(pages):
        for x in range(w):
            b = data[base + p * w + x]
            for bit in range(8):
                pix[p * 8 + bit, x] = (b >> bit) & 1
    return pix

def _plusmask_sprite(data, w, h, frame):
    pages = h // 8
    base = frame * w * pages * 2
    img = np.zeros((h, w), dtype=np.uint8)
    msk = np.zeros((h, w), dtype=np.uint8)
    for p in range(pages):
        for x in range(w):
            idx = base + (x + p * w) * 2
            s = data[idx]
            m = data[idx + 1]
            for bit in range(8):
                img[p * 8 + bit, x] = (s >> bit) & 1
                msk[p * 8 + bit, x] = (m >> bit) & 1
    return img, msk

TILE_COUNT = len(SRC["tileSheet"]) // 32
TILE_PIX = [_overwrite_sprite(SRC["tileSheet"], 16, 16, t) for t in range(TILE_COUNT)]

HEAD_PIX = [_plusmask_sprite(SRC["playerHead_plus_mask"], 12, 8, f) for f in range(4)]

FEET_PIX = [_plusmask_sprite(SRC["playerFeet_plus_mask"], 12, 8, f) for f in range(12)]

EYE_PIX = [_overwrite_sprite(SRC["eyesBlinking"], 4, 8, f) for f in range(3)]

def _load_png_rgba(path):
    import zlib

    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        sys.exit("title.png не является PNG: %s" % path)

    pos = 8
    width = height = bit_depth = color_type = None
    idat = b""
    while pos < len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width = int.from_bytes(chunk[0:4], "big")
            height = int.from_bytes(chunk[4:8], "big")
            bit_depth = chunk[8]
            color_type = chunk[9]
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length

    if bit_depth != 8 or color_type not in (2, 6):
        sys.exit("title.png: поддерживается только 8-бит RGB/RGBA")

    bpp = 4 if color_type == 6 else 3
    raw = zlib.decompress(idat)
    stride = width * bpp
    out = np.zeros((height, stride), dtype=np.uint8)
    prev = np.zeros(stride, dtype=np.int32)
    p = 0
    for y in range(height):
        flt = raw[p]
        p += 1
        line = np.frombuffer(raw[p:p + stride], dtype=np.uint8).astype(np.int32).copy()
        p += stride
        if flt == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 0xFF
        elif flt == 2:
            line = (line + prev) & 0xFF
        elif flt == 3:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif flt == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                c = prev[x - bpp] if x >= bpp else 0
                b = prev[x]
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        out[y] = line
        prev = line

    img = out.reshape(height, width, bpp)
    if bpp == 3:
        rgba = np.full((height, width, 4), 255, dtype=np.uint8)
        rgba[:, :, :3] = img
        return rgba
    return img.astype(np.uint8)

def _build_title():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "title.png")
    if not os.path.isfile(path):
        return None
    rgba = _load_png_rgba(path)
    r = rgba[:, :, 0].astype(np.int32)
    g = rgba[:, :, 1].astype(np.int32)
    b = rgba[:, :, 2].astype(np.int32)
    a = rgba[:, :, 3]

    transparent = (a < 128) | ((g > 200) & (r < 60) & (b < 60))
    mask = ~transparent
    white = (r + g + b) > 384
    bit = white.astype(np.uint8)
    if TITLE_INVERT:
        bit = 1 - bit
    return mask, bit

TITLE = _build_title()

def get_region(cx, cy):
    for i in range(TOTAL_REGIONS):
        x = REGIONS[i * 4]
        y = REGIONS[i * 4 + 1]
        w = REGIONS[i * 4 + 2]
        h = REGIONS[i * 4 + 3]
        if x <= cx < x + w and y <= cy < y + h:
            return i
    return REGION_FOREST

def get_chunk_bit(cx, cy):
    cx &= 0xFF
    cy &= 0xFF
    x = cx // 8
    y = cy * 4
    i = cx % 8
    idx = x + y
    if idx < 0 or idx >= len(SOLID_MAP):
        return True
    return (SOLID_MAP[idx] & (1 << i)) > 0

_SPECIFIC_CHUNKS = {
    59: 162, 84: 162, 253: 162, 340: 162, 520: 162, 802: 162, 843: 162,
    171: 170, 180: 170, 192: 170, 312: 170, 353: 170, 368: 170, 394: 170,
    485: 170, 493: 170, 515: 170, 562: 170, 605: 170, 627: 170, 631: 170,
    716: 170, 737: 170, 776: 170, 873: 170, 881: 170, 910: 170,
    6: 171, 42: 171, 379: 171, 709: 171, 754: 171,
    61: 172, 33: 172, 570: 172, 718: 172,
    209: 173, 43: 173, 513: 173, 756: 173,
    801: 174, 1011: 167, 655: 146,
}

def get_chunk(cx, cy):
    key = cx + cy * 32
    if key in _SPECIFIC_CHUNKS:
        return _SPECIFIC_CHUNKS[key]

    reg = get_region(cx, cy)
    if reg in (REGION_FIELDS, REGION_SWAMP, REGION_FOREST, REGION_YOUR_GARDEN):
        if get_chunk_bit(cx, cy):
            return 31
        b = 0
        b |= get_chunk_bit(cx + 1, cy)
        b |= get_chunk_bit(cx, cy - 1) << 1
        b |= get_chunk_bit(cx - 1, cy) << 2
        b |= get_chunk_bit(cx, cy + 1) << 3
        return b + 16
    if reg == REGION_CANYONS:
        if get_chunk_bit(cx, cy):
            return 15
        b = 0
        b |= get_chunk_bit(cx + 1, cy)
        b |= get_chunk_bit(cx, cy - 1) << 1
        b |= get_chunk_bit(cx - 1, cy) << 2
        b |= get_chunk_bit(cx, cy + 1) << 3
        return b
    if reg == REGION_FOREST_CANYON:
        return 31 if get_chunk_bit(cx, cy) else 32
    if reg in (REGION_SWAMP_FOREST, REGION_FIELDS_SWAMP, REGION_FIELDS_CANYONS):
        if not get_chunk_bit(cx, cy):
            return 21 + 5 * (reg - REGION_SWAMP_FOREST)
        return 31
    if reg in (REGION_SWAMP_ISLAND_ONE, REGION_SWAMP_ISLAND_TWO):
        return 34 if get_chunk_bit(cx, cy) else 15
    if reg == REGION_LONG_ROAD:
        return 31 if get_chunk_bit(cx, cy) else 26
    if reg == REGION_APPLE_FARM:
        return 34 if get_chunk_bit(cx, cy) else 35
    if reg in (REGION_HOUSE_INTERIOR, REGION_INN_INTERIOR, REGION_YOUR_INTERIOR):
        if not get_chunk_bit(cx, cy):
            return 36 + (reg - REGION_HOUSE_INTERIOR)
        return 47
    if reg == REGION_SHOP_INTERIOR:
        return 39 if not get_chunk_bit(cx, cy) else 48
    if reg in (REGION_TREE_INTERIOR, REGION_CAVE_INTERIOR):
        if not get_chunk_bit(cx, cy):
            return 40 + (reg - REGION_TREE_INTERIOR)
        return 33
    if reg == REGION_ALL_BLACK:
        return 33
    return 0

_SIGN_CELLS = {1625, 18422, 23901, 30155}
_ROCK_CELLS = {23709, 18423, 1817}
_CHEST_CELLS = {5145, 18963, 17731, 3909, 34891, 36140}

def get_tile_id(wx, wy, frame_boolean):
    wx &= 0xFFFF
    wy &= 0xFFFF
    if wx > 3056 or wy > 3136:
        return TILE_ROCK

    cell = (wx >> 4) + (wy >> 4) * 192
    if cell in _SIGN_CELLS:
        return TILE_SIGN
    if cell in _ROCK_CELLS:
        return TILE_ROCK
    if cell in _CHEST_CELLS:
        return TILE_CLOSED_CHEST

    chunk = get_chunk(wx // 96, wy // 96)
    tpx = (wx % 96) >> 4
    tpy = (wy % 96) >> 4
    pos = tpx + tpy * 6
    cn = chunk & 0x7F

    if cn < 24:
        block = cn
        tile = CHUNKS[block * 36 + pos]
        if cn >= 15:
            tile &= 0x0F
            if tile > 7:
                tile += 24
    elif cn < 32:
        block = cn - 8
        tile = CHUNKS[block * 36 + pos] >> 4
        if tile > 7:
            tile += 24
    else:
        block = cn - 8
        tile = CHUNKS[block * 36 + pos]

    if chunk < 127:
        reg = get_region(wx // 96, wy // 96)
        if reg in (REGION_FIELDS, REGION_YOUR_GARDEN):
            if tile > 30:
                tile = 32
            elif tile == 2:
                tile = 0
            elif tile in (3, 4):
                tile = 5
            elif tile == 7:
                tile = 2
        elif reg == REGION_SWAMP:
            if tile > 30:
                tile = 14
            elif tile == 0:
                tile = 12
            elif tile == 3:
                tile = 5
            elif tile in (1, 2, 4, 7):
                tile = 10

    if tile in (10, 12, 14):
        tile += frame_boolean
    elif tile in (11, 13, 15):
        tile -= frame_boolean
    return tile

def get_solid(wx, wy, frame_boolean):
    return get_tile_id(wx, wy, frame_boolean) >= 14

class State:
    def __init__(self):
        self.x = PLAYER_START_X
        self.y = PLAYER_START_Y
        self.direction = FACING_SOUTH
        self.frame = 1
        self.walking = False
        self.global_frame = 0
        self.frame_count = 0

def check_collision(st, orientation, frame_boolean):
    p1x = st.x + COLLISION_POINTS[2 * orientation][0]
    p1y = st.y + COLLISION_POINTS[2 * orientation][1]
    p2x = st.x + COLLISION_POINTS[2 * orientation + 1][0]
    p2y = st.y + COLLISION_POINTS[2 * orientation + 1][1]
    if not get_solid(p1x, p1y, frame_boolean) and not get_solid(p2x, p2y, frame_boolean):
        st.walking = True
        return True
    return False

def update_movement(st, keys, frame_boolean):
    st.walking = False
    if keys[pygame.K_DOWN] and check_collision(st, FACING_SOUTH, frame_boolean):
        st.direction = FACING_SOUTH
        st.y += 1
    elif keys[pygame.K_LEFT] and check_collision(st, FACING_WEST, frame_boolean):
        st.direction = FACING_WEST
        st.x -= 1
    elif keys[pygame.K_UP] and check_collision(st, FACING_NORTH, frame_boolean):
        st.direction = FACING_NORTH
        st.y -= 1
    elif keys[pygame.K_RIGHT] and check_collision(st, FACING_EAST, frame_boolean):
        st.direction = FACING_EAST
        st.x += 1
    else:

        if keys[pygame.K_DOWN]:
            st.direction = FACING_SOUTH
        elif keys[pygame.K_LEFT]:
            st.direction = FACING_WEST
        elif keys[pygame.K_UP]:
            st.direction = FACING_NORTH
        elif keys[pygame.K_RIGHT]:
            st.direction = FACING_EAST

def _blit_overwrite(buf, pix, x, y):
    h, w = pix.shape
    bh, bw = buf.shape
    x0 = max(0, -x)
    y0 = max(0, -y)
    x1 = min(w, bw - x)
    y1 = min(h, bh - y)
    if x0 >= x1 or y0 >= y1:
        return
    buf[y + y0:y + y1, x + x0:x + x1] = pix[y0:y1, x0:x1]

def _blit_masked(buf, img, msk, x, y):
    h, w = img.shape
    bh, bw = buf.shape
    x0 = max(0, -x)
    y0 = max(0, -y)
    x1 = min(w, bw - x)
    y1 = min(h, bh - y)
    if x0 >= x1 or y0 >= y1:
        return
    region = buf[y + y0:y + y1, x + x0:x + x1]
    s = img[y0:y1, x0:x1]
    m = msk[y0:y1, x0:x1].astype(bool)
    region[m] = s[m]

def _blit_self_masked(buf, pix, x, y):
    h, w = pix.shape
    bh, bw = buf.shape
    x0 = max(0, -x)
    y0 = max(0, -y)
    x1 = min(w, bw - x)
    y1 = min(h, bh - y)
    if x0 >= x1 or y0 >= y1:
        return
    region = buf[y + y0:y + y1, x + x0:x + x1]
    s = pix[y0:y1, x0:x1].astype(bool)
    region[s] = 1

def render(st, buf):
    frame_boolean = 1 if (st.frame_count % 32) < 16 else 0

    cam_x = st.x - (VIEW_W // 2 - 8)
    cam_y = st.y - (VIEW_H // 2 - 8)

    col = cam_x >> 4
    while col * 16 - cam_x < VIEW_W:
        row = cam_y >> 4
        while row * 16 - cam_y < VIEW_H:
            tile = get_tile_id(col * 16, row * 16, frame_boolean)
            if 0 <= tile < TILE_COUNT:
                _blit_overwrite(buf, TILE_PIX[tile], col * 16 - cam_x, row * 16 - cam_y)
            row += 1
        col += 1

    sprite_frame = ANIM_SEQ[st.frame] + 3 * st.direction
    head_frame = sprite_frame // 3
    feet_frame = sprite_frame

    px = st.x - cam_x + 2
    py = st.y - cam_y

    himg, hmsk = HEAD_PIX[head_frame]
    _blit_masked(buf, himg, hmsk, px, py - (st.frame % 2) + 1)
    fimg, fmsk = FEET_PIX[feet_frame]
    _blit_masked(buf, fimg, fmsk, px, py + 8)

    if sprite_frame == 1:
        blinking = (st.global_frame % 32) if (st.global_frame % 32) < 4 else 0
        eye_frame = EYES_SEQ[blinking]
        _blit_self_masked(buf, EYE_PIX[eye_frame], st.x - cam_x + 6, st.y - cam_y + 7)

    if TITLE is not None:
        mask, bit = TITLE
        th = min(mask.shape[0], VIEW_H)
        tw = min(mask.shape[1], VIEW_W)
        m = mask[:th, :tw]
        buf[:th, :tw][m] = bit[:th, :tw][m]

def main():
    pygame.init()
    pygame.display.set_caption("Arduventure — walk demo")

    n = max(1, WINDOW_SCALE)
    win_w, win_h = VIDEO_W // n, VIDEO_H // n
    screen = pygame.display.set_mode((win_w, win_h))
    frame_surf = pygame.Surface((VIDEO_W, VIDEO_H))
    clock = pygame.time.Clock()

    if BIT_ONE_IS_BG:
        palette = np.array([COLOR_INK, COLOR_BG], dtype=np.uint8)
    else:
        palette = np.array([COLOR_BG, COLOR_INK], dtype=np.uint8)

    view_surf = pygame.Surface((VIEW_W, VIEW_H))
    st = State()
    recorder = video.VideoRecorder(video.output_path(__file__), VIDEO_W, VIDEO_H, TARGET_FRAMERATE)

    running = True
    try:
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False

            keys = pygame.key.get_pressed()
            frame_boolean = 1 if (st.frame_count % 32) < 16 else 0

            update_movement(st, keys, frame_boolean)

            if st.walking:
                if st.frame_count % ANIMATION_SPEED == 0:
                    st.frame = (st.frame + 1) % 4
            else:
                st.frame = 1

            if st.frame_count % EYES_SPEED == 0:
                st.global_frame = (st.global_frame + 1) % 80

            buf = np.zeros((VIEW_H, VIEW_W), dtype=np.uint8)
            render(st, buf)

            rgb = palette[buf]
            pygame.surfarray.blit_array(view_surf, np.transpose(rgb, (1, 0, 2)))

            frame_surf.fill(COLOR_BG)
            scaled = pygame.transform.scale(view_surf, (RENDER_W, RENDER_H))
            frame_surf.blit(scaled, (MARGIN, MARGIN))
            recorder.write(frame_surf)
            screen.blit(pygame.transform.scale(frame_surf, (win_w, win_h)), (0, 0))
            pygame.display.flip()

            st.frame_count += 1
            clock.tick(TARGET_FRAMERATE)
    finally:
        recorder.close()
        pygame.quit()

if __name__ == "__main__":
    main()
