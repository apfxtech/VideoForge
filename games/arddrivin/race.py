import os
import random
import re
import sys
import tempfile

import numpy as np
import pygame

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sound
import video


SOURCE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".sources", "ardudrivin"))
PICS_DIR = os.path.join(SOURCE_DIR, "game", "pics")

SCALE = 8
VIDEO_W, VIDEO_H = 1080, 1920
GAME_W, GAME_H = 128, 64
VIEW_W = 128
GAME_SCREEN_Y = (VIDEO_H - GAME_H * SCALE) // 2
VIEW_H = (VIDEO_H - GAME_SCREEN_Y) // SCALE
MARGIN = (VIDEO_W - VIEW_W * SCALE) // 2
RENDER_W = VIEW_W * SCALE
RENDER_H = VIEW_H * SCALE
TITLE_TOP_Y = MARGIN
TITLE_TARGET_W = RENDER_W
WINDOW_SCALE = 4
TARGET_FRAMERATE = 30
LOOP_SECONDS = 30
LOOP_FRAMES = TARGET_FRAMERATE * LOOP_SECONDS
WINDDOWN_FRAME = 25 * TARGET_FRAMERATE
GAME_SPEED_SCALE = 0.88
SKY_SCROLL_SHIFT = 3
CLOUD_SCROLL_DIV = 65536
ENEMY_DEPTH_BOTTOM = 455

COLOR_BG = (0xFF, 0x82, 0x00)
COLOR_INK = (0x00, 0x00, 0x00)
COLOR_NTR = (0x8A, 0x48, 0x12)

BG = 0
INK = 1
NTR = 2

UP_BUTTON = 0x80
DOWN_BUTTON = 0x10
LEFT_BUTTON = 0x20
RIGHT_BUTTON = 0x40
A_BUTTON = 0x08

MAX_TREE = 8


def _i8(value):
    value &= 0xFF
    return value - 256 if value & 0x80 else value


def _clamp(value, lo, hi):
    return lo if value < lo else hi if value > hi else value


def _c_div(value, divisor):
    return int(value / divisor)


def _parse_array(path, name):
    text = open(path, encoding="utf-8", errors="ignore").read()
    m = re.search(r"\b%s\s*\[\s*(?:\d+)?\s*\]\s*(?:PROGMEM\s*)?=\s*\{" % re.escape(name), text)
    if not m:
        raise RuntimeError("array %s not found in %s" % (name, path))
    i = m.end()
    depth = 1
    j = i
    while j < len(text) and depth:
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
        j += 1
    body = re.sub(r"//[^\n]*", "", text[i:j - 1])
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    out = []
    for token in re.findall(r"0x[0-9a-fA-F]+|-?\d+", body):
        out.append(int(token, 16) if token.lower().startswith("0x") else int(token) & 0xFF)
    return out


def _load_sprite(name):
    data = _parse_array(os.path.join(PICS_DIR, name + ".h"), name)
    return {"w": data[0], "h": data[1], "hx": _i8(data[2]), "hy": _i8(data[3]), "data": data[4:]}


def _load_font():
    return _parse_array(os.path.join(PICS_DIR, "font.h"), "font")


SPR = {name: _load_sprite(name) for name in (
    "playercar", "playercar_bottom", "playercar_tire2", "skybox",
    "enemycar_z1", "enemycar_z2", "enemycar_z3", "enemycar_z4",
    "palm_1", "palm_2", "palm_3",
    "puff_1", "puff_2", "puff_3",
    "big_1", "big_2", "big_3", "big_go",
)}
FONT = _load_font()
Y_LOOKUP = _parse_array(os.path.join(SOURCE_DIR, "game", "main.cpp"), "yLookup")


def _load_png_rgba(path):
    import zlib

    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
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
        return None
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
                pr = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[x] = (line[x] + pr) & 0xFF
        out[y] = line
        prev = line
    img = out.reshape(height, width, bpp)
    if bpp == 3:
        rgba = np.full((height, width, 4), 255, dtype=np.uint8)
        rgba[:, :, :3] = img
        return rgba
    return img


def _build_png_overlay(path):
    if not os.path.isfile(path):
        return None
    rgba = _load_png_rgba(path)
    if rgba is None:
        return None
    r = rgba[:, :, 0].astype(np.int32)
    g = rgba[:, :, 1].astype(np.int32)
    b = rgba[:, :, 2].astype(np.int32)
    a = rgba[:, :, 3]
    transparent = (a < 128) | ((g > 200) & (r < 60) & (b < 60))
    bit = ((r + g + b) > 384).astype(np.uint8)
    return ~transparent, bit


FLIPPER = _build_png_overlay(os.path.join(os.path.dirname(__file__), "flipper.png"))


class Race:
    def __init__(self):
        self.rng = random.Random(46)
        self.flicker = 0
        self.speed = 0
        self.speed_acc = 0
        self.gear = 0
        self.score = 0
        self.game_timer = 104
        self.last_second = 0
        self.player_x = 0
        self.player_y = 58
        self.road_curve = 0
        self.desired_curve = 0
        self.next_curve = 0
        self.track_centered_x = 0
        self.track_pix_width = [1] * (VIEW_H - 20)
        self.track_start_x = [0] * (VIEW_H - 20)
        self.background_x = 0
        self.enemy_x = 0
        self.enemy_depth = ENEMY_DEPTH_BOTTOM + 1.0
        self.enemy_y = VIEW_H + 32.0
        self.enemy_speed = 150
        self.palms = []
        self.next_palm = 0
        self.buttons = A_BUTTON
        self.collisions = 0
        self.planned_collision_done = False
        self.phase = 0
        self.gear_shift_frame = None
        self.puff_frame = -1
        self.puff_x = 0
        self.puff_y = 0
        self.offroad = 0
        self.audio = sound.ToneTimeline()

    def random(self, n):
        return self.rng.randrange(max(1, n))

    def draw_hline(self, buf, x, y, w, color):
        if y < 0 or y >= VIEW_H or w <= 0:
            return
        x0 = max(0, int(x))
        x1 = min(VIEW_W, int(x + w))
        if x1 > x0:
            buf[y, x0:x1] = color

    def draw_byte(self, buf, x, page, value):
        if x < 0 or x >= VIEW_W or page < 0 or page >= GAME_H // 8:
            return
        y0 = page * 8
        for bit in range(8):
            buf[y0 + bit, x] = INK if ((value >> bit) & 1) else BG

    def draw_char(self, buf, x, y, c):
        code = ord(c)
        if code < 32:
            return
        glyph = (code - 32) * 6
        for i in range(6):
            if glyph + i < len(FONT):
                self.draw_byte(buf, x + i, y >> 3, FONT[glyph + i])

    def print_byte(self, buf, x, y, value):
        value &= 0xFF
        self.draw_char(buf, x, y, chr(ord("0") + (value // 10) % 10))
        self.draw_char(buf, x + 6, y, chr(ord("0") + value % 10))

    def draw_sprite(self, buf, x, y, sprite, hflip=False):
        w, h, hx, hy, data = sprite["w"], sprite["h"], sprite["hx"], sprite["hy"], sprite["data"]
        y -= hy
        x -= (w - hx) if hflip else hx
        pages = (h + 7) >> 3
        for p in range(pages):
            for c in range(w):
                src_col = w - 1 - c if hflip else c
                idx = (p * w + src_col) * 2
                mask = data[idx]
                bits = data[idx + 1]
                sx = x + c
                if sx < 0 or sx >= VIEW_W:
                    continue
                for bit in range(8):
                    sy = y + p * 8 + bit
                    if sy < 0 or sy >= VIEW_H or sy >= y + h:
                        continue
                    if (mask >> bit) & 1:
                        buf[sy, sx] = INK if ((bits >> bit) & 1) else BG

    def screen_x(self, lane_x, y):
        idx = int(_clamp(int(y) - 20, 0, len(self.track_pix_width) - 1))
        ref = int(_clamp(41, 0, len(self.track_pix_width) - 1))
        ref_width = max(1, self.track_pix_width[ref])
        width = self.track_pix_width[idx]
        return int((lane_x * width / ref_width) + width + self.track_start_x[idx])

    def depth_to_y(self, depth):
        if depth <= 255:
            idx = int(_clamp(depth, 0, 255))
            return float(Y_LOOKUP[idx])
        extra = (depth - 255.0) / max(1, ENEMY_DEPTH_BOTTOM - 255)
        return 63.0 + extra * (VIEW_H - 64)

    def update_autopilot(self):
        if self.game_timer > 99:
            self.buttons = A_BUTTON
            self.gear = 0
            return

        if self.phase >= 3:
            buttons = 0
            if self.player_x > 5:
                buttons |= LEFT_BUTTON
            elif self.player_x < -5:
                buttons |= RIGHT_BUTTON
            self.buttons = buttons
            return

        base_target = _clamp(self.road_curve / 360.0, -34, 34)
        target = base_target
        react_y = 40 if self.planned_collision_done else 57
        if self.phase == 0 and react_y <= self.enemy_y <= 72 and abs(self.enemy_x - self.player_x) < 50:
            if self.enemy_x >= self.player_x:
                target = _clamp(self.enemy_x - 62, -96, 96)
            else:
                target = _clamp(self.enemy_x + 62, -96, 96)
        if self.player_x < -100:
            target = 0
        elif self.player_x > 100:
            target = 0

        buttons = A_BUTTON
        if self.player_x - target > 5:
            buttons |= LEFT_BUTTON
        elif self.player_x - target < -5:
            buttons |= RIGHT_BUTTON
        self.buttons = buttons
        if self.speed > 132 and self.gear_shift_frame is None:
            self.gear_shift_frame = 18
        if self.gear_shift_frame is not None:
            if self.gear_shift_frame <= 0:
                self.gear = 1
            else:
                self.gear_shift_frame -= 1
        if self.speed < 118:
            self.gear = 0
            self.gear_shift_frame = None

    def update(self, frame_index):
        now_sec = frame_index // TARGET_FRAMERATE
        if now_sec != self.last_second:
            self.last_second = now_sec
            if self.game_timer > 0:
                self.game_timer -= 1

        if self.phase == 0 and frame_index >= WINDDOWN_FRAME:
            self.phase = 1

        self.flicker = (self.flicker + 1) & 0xFF
        self.update_autopilot()

        if self.buttons & A_BUTTON:
            if self.gear == 0:
                self.speed = min(140, self.speed + 1)
            else:
                if self.speed < 120:
                    if (self.flicker & 3) == 0:
                        self.speed += 1
                else:
                    self.speed = min(210, self.speed + 1)
        else:
            self.speed = max(0, self.speed - 2)

        if self.game_timer <= 99:
            move_speed = 900 if self.speed >= 100 else self.speed * 5 + 400
            if self.buttons & LEFT_BUTTON:
                self.player_x -= move_speed / 128.0
            if self.buttons & RIGHT_BUTTON:
                self.player_x += move_speed / 128.0
            self.player_x -= (self.road_curve * self.speed) / 524288.0
            if self.player_x < -115:
                self.player_x = -115
            if self.player_x > 115:
                self.player_x = 115

            sim_speed = self.speed * GAME_SPEED_SCALE
            self.speed_acc = (self.speed_acc + int(sim_speed)) & 0xFFFF
            self.background_x -= int((self.road_curve * sim_speed) / CLOUD_SCROLL_DIV)
            if self.phase >= 2:
                self.desired_curve = 0
                step = max(350, (self.speed * 3) // 2)
            else:
                self.next_curve -= sim_speed
                if self.next_curve <= 0:
                    self.next_curve = 2000 + self.random(26000)
                    self.desired_curve = self.random(25600) - 12800
                step = max(1, (self.speed * 3) // 2)
            if self.road_curve + step < self.desired_curve:
                self.road_curve += step
            elif self.road_curve - step > self.desired_curve:
                self.road_curve -= step
            else:
                self.road_curve = self.desired_curve
                if self.phase == 2:
                    self.phase = 3

            if self.phase >= 1:
                self.enemy_speed = max(0, self.enemy_speed - 3)
                self.enemy_depth += 12.0
                self.enemy_y = self.depth_to_y(self.enemy_depth)
                if self.enemy_depth > ENEMY_DEPTH_BOTTOM:
                    self.enemy_y = VIEW_H + 32.0
                    if self.phase == 1:
                        self.phase = 2
            else:
                self.enemy_depth += max(0.8, ((self.speed - self.enemy_speed) / 4.0) * GAME_SPEED_SCALE) / 8.0
                self.enemy_y = self.depth_to_y(self.enemy_depth)
                if self.enemy_depth > ENEMY_DEPTH_BOTTOM:
                    self.enemy_speed = 130 + self.random(40)
                    self.enemy_depth = 0.0
                    self.enemy_y = self.depth_to_y(self.enemy_depth)
                    self.enemy_x = self.random(180) - 90
                    self.score = (self.score + 1) & 0xFF

            if not self.planned_collision_done and 52 <= self.enemy_y <= 64 and abs(self.enemy_x - self.player_x) < 38:
                self.collisions += 1
                self.speed = max(45, self.enemy_speed - 20)
                dx = self.player_x - self.enemy_x
                push = _clamp(dx * 0.25, -16, 16)
                self.player_x = _clamp(self.player_x + push, -115, 115)
                self.enemy_x = _clamp(self.enemy_x - push, -115, 115)
                self.planned_collision_done = True

            self.next_palm -= sim_speed
            if self.phase == 0 and self.next_palm <= 0 and len(self.palms) < MAX_TREE:
                side = -1 if self.random(2) == 0 else 1
                self.palms.append([side, 20.0])
                self.next_palm = 400 + self.random(1200)
            for palm in self.palms:
                palm[1] += max(0.25, sim_speed / 45.0)
            self.palms = [p for p in self.palms if p[1] < VIEW_H + 20]

        if (self.flicker & 1) == 0:
            freq = 16 + (self.speed >> 1)
            self.audio.tone(frame_index / TARGET_FRAMERATE, freq, 50)

    def render_scene(self):
        buf = np.full((VIEW_H, VIEW_W), NTR, dtype=np.uint8)

        track_width = 1 << 7
        track_increment = 128 + (47 << 4)
        track_center = 64 << 4
        road_line_acc = (8192 - (self.speed_acc << 6)) & 0xFFFF
        road_curve = int(self.road_curve) >> 1
        track_center += road_curve >> 2
        road_curve_inc = _c_div(int(self.road_curve), 118)

        new_centered_x = int(_clamp(_c_div(int(self.player_x), 8), -45, 45))
        if self.track_centered_x < new_centered_x and self.track_centered_x < 45:
            self.track_centered_x += 1
        elif self.track_centered_x > new_centered_x and self.track_centered_x > -45:
            self.track_centered_x -= 1

        for y in range(20, VIEW_H):
            pix_width = max(1, track_width >> 8)
            idx = y - 20
            self.track_pix_width[idx] = pix_width

            startx = (track_center - int(track_width >> 4)) >> 4
            self.track_start_x[idx] = startx

            sideline = (pix_width >> 4) + 1
            road = max(0, pix_width - ((sideline * 3 + 1) >> 1))
            stripe = INK if (road_line_acc & 8192) else BG

            if road_line_acc & 16384:
                self.draw_hline(buf, 0, y, startx, INK)

            x = startx
            self.draw_hline(buf, x, y, sideline, stripe)
            x += sideline + road

            if road_line_acc & 16384:
                self.draw_hline(buf, x, y, sideline, INK)
            x += sideline + road

            self.draw_hline(buf, x, y, sideline, stripe)
            x += sideline

            if road_line_acc & 16384:
                self.draw_hline(buf, x, y, 256, INK)

            road_line_acc = (road_line_acc + (60000 // max(1, 6 + (track_width >> 9)))) & 0xFFFF
            track_width += track_increment
            track_center -= self.track_centered_x + ((-road_curve_inc + (road_curve >> 4)) >> 2)
            road_curve -= road_curve_inc

        bgx = (self.background_x >> SKY_SCROLL_SHIFT) % 128
        self.draw_sprite(buf, bgx - 128, 8, SPR["skybox"])
        self.draw_sprite(buf, bgx, 8, SPR["skybox"])

        for side, y in self.palms:
            sprite = SPR["palm_1"] if y > 43 else SPR["palm_2"] if y > 32 else SPR["palm_3"]
            x = self.screen_x(side * 145, y)
            self.draw_sprite(buf, x, int(y), sprite, side > 0)

        if 20 <= self.enemy_y <= VIEW_H + 16:
            ey = int(self.enemy_y)
            ex = self.screen_x(self.enemy_x, ey)
            sprite = SPR["enemycar_z1"] if ey > 48 else SPR["enemycar_z2"] if ey > 37 else SPR["enemycar_z3"] if ey > 29 else SPR["enemycar_z4"]
            self.draw_sprite(buf, ex, ey, sprite, ex > 64)

        px = self.screen_x(self.player_x, self.player_y)
        hflip = 1 if self.buttons & LEFT_BUTTON else 0
        self.draw_sprite(buf, px, self.player_y, SPR["playercar_bottom"], hflip)
        if self.game_timer <= 99:
            tire_overlay = -17 if (self.speed_acc & 256) else 13
            self.draw_sprite(buf, px + tire_overlay, self.player_y, SPR["playercar_tire2"])
        bob = -6 if (self.speed_acc & 512) else -5
        self.draw_sprite(buf, px, self.player_y + bob, SPR["playercar"], hflip)
        self.draw_puff(buf, px, self.player_y)

        self.draw_hud(buf)
        self.draw_countdown(buf)
        self.draw_flipper(buf)
        return buf

    def draw_puff(self, buf, px, py):
        road_curve8 = int(self.road_curve) >> 8
        moving_side = bool(self.buttons & (LEFT_BUTTON | RIGHT_BUTTON))
        if self.puff_frame == -1 and moving_side and self.speed > 100 and (road_curve8 > 15 or road_curve8 < -15):
            self.puff_frame = 0
            self.puff_x = px - 16
            self.puff_y = py - 1
        if self.puff_frame != -1:
            sprite = SPR["puff_%d" % ((self.puff_frame >> 4) + 1)]
            self.draw_sprite(buf, self.puff_x, self.puff_y, sprite)
            self.draw_sprite(buf, self.puff_x + 34, self.puff_y, sprite, True)
            self.puff_frame += 8
            if self.puff_frame >= (3 << 4):
                self.puff_frame = -1

    def draw_countdown(self, buf):
        sprite = None
        if self.game_timer == 102:
            sprite = SPR["big_3"]
        elif self.game_timer == 101:
            sprite = SPR["big_2"]
        elif self.game_timer == 100:
            sprite = SPR["big_1"]
        elif self.game_timer in (99, 98):
            sprite = SPR["big_go"]
        if sprite is not None:
            self.draw_sprite(buf, 64, 23, sprite)

    def draw_hud(self, buf):
        self.draw_byte(buf, 0, 0, 0xFF)
        self.draw_byte(buf, 1, 0, 0x81)
        speedometer = min(39, (self.speed * 39) >> 8)
        for i in range(speedometer):
            self.draw_byte(buf, i + 2, 0, 0xBD)
        for i in range(speedometer, 33):
            self.draw_byte(buf, i + 2, 0, 0x81)
        self.draw_byte(buf, 35, 0, 0x81)
        for x in range(36, 57):
            self.draw_byte(buf, x, 0, 0xFF)
        for x in range(69, 116):
            self.draw_byte(buf, x, 0, 0xFF)
        if self.game_timer <= 99:
            self.print_byte(buf, 57, 0, self.game_timer)
        if self.gear == 1 or (self.flicker & 4):
            self.draw_char(buf, 81, 0, "L")
        if self.gear == 0 or (self.flicker & 4):
            self.draw_char(buf, 93, 0, "H")
        self.print_byte(buf, 116, 0, self.score)

    def draw_flipper(self, buf):
        if FLIPPER is None:
            return
        mask, bit = FLIPPER
        y = VIEW_H - mask.shape[0] - 42
        x = (VIEW_W - mask.shape[1]) // 2
        th = min(mask.shape[0], VIEW_H - y)
        tw = min(mask.shape[1], VIEW_W - x)
        if x < 0 or y < 0 or th <= 0 or tw <= 0:
            return
        region = buf[y:y + th, x:x + tw]
        m = mask[:th, :tw]
        region[m] = np.where(bit[:th, :tw][m] > 0, INK, BG)

    def frame(self, frame_index):
        self.update(frame_index)
        return self.render_scene()


def main():
    pygame.init()
    pygame.display.set_caption("ArduDrivin - race loop")

    n = max(1, WINDOW_SCALE)
    win_w, win_h = VIDEO_W // n, VIDEO_H // n
    screen = pygame.display.set_mode((win_w, win_h))
    frame_surf = pygame.Surface((VIDEO_W, VIDEO_H))
    view_surf = pygame.Surface((VIEW_W, VIEW_H))
    clock = pygame.time.Clock()
    palette = np.array([COLOR_INK, COLOR_BG, COLOR_NTR], dtype=np.uint8)
    title_surf = None
    title_path = os.path.join(os.path.dirname(__file__), "title.png")
    if os.path.exists(title_path):
        title_rgba = _load_png_rgba(title_path)
        source_title = pygame.image.frombuffer(
            title_rgba.tobytes(),
            (title_rgba.shape[1], title_rgba.shape[0]),
            "RGBA",
        ).convert_alpha()
        title_h = round(source_title.get_height() * (TITLE_TARGET_W / source_title.get_width()))
        title_surf = pygame.transform.scale(source_title, (TITLE_TARGET_W, title_h))
        title_x = (VIDEO_W - title_surf.get_width()) // 2

    race = Race()
    fd, audio_path = tempfile.mkstemp(prefix="arddrivin-", suffix=".wav")
    os.close(fd)
    preview = None
    recorder = None

    try:
        frames = [race.frame(i) for i in range(LOOP_FRAMES)]
        race.audio.write_wav(audio_path, LOOP_SECONDS)
        preview = sound.play_preview_file(audio_path)
        recorder = video.VideoRecorder(video.output_path(__file__), VIDEO_W, VIDEO_H, TARGET_FRAMERATE, audio_path)

        running = True
        for view in frames:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False
            if not running:
                break
            pygame.surfarray.blit_array(view_surf, np.transpose(palette[view], (1, 0, 2)))
            frame_surf.fill(COLOR_NTR)
            if title_surf is not None:
                frame_surf.blit(title_surf, (title_x, TITLE_TOP_Y))
            scaled = pygame.transform.scale(view_surf, (RENDER_W, RENDER_H))
            frame_surf.blit(scaled, (MARGIN, GAME_SCREEN_Y))
            recorder.write(frame_surf)
            screen.blit(pygame.transform.scale(frame_surf, (win_w, win_h)), (0, 0))
            pygame.display.flip()
            clock.tick(TARGET_FRAMERATE)
    finally:
        if preview is not None:
            preview.stop()
        if recorder is not None:
            recorder.close()
        if os.path.exists(audio_path):
            os.unlink(audio_path)
        pygame.quit()


if __name__ == "__main__":
    main()
