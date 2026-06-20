import os
import random
import re
import sys
import tempfile
import wave

import numpy as np
import pygame
import pygame.sndarray

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import video

SOURCE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".sources", "myblab"))
BITMAPS_PATH = os.path.join(SOURCE_DIR, "game", "bitmaps.h")

SCALE = 8
VIDEO_W, VIDEO_H = 1080, 1920
VIEW_W = 128
MARGIN = (VIDEO_W - VIEW_W * SCALE) // 2
RENDER_W = VIEW_W * SCALE
TITLE_W, TITLE_H = 128, 64
TITLE_TOP_Y = MARGIN
TITLE_RENDER_H = TITLE_H * SCALE
GAME_TOP_Y = TITLE_TOP_Y + TITLE_RENDER_H + MARGIN
GAME_RENDER_H = VIDEO_H - MARGIN - GAME_TOP_Y
VIEW_H = GAME_RENDER_H // SCALE
RENDER_H = VIEW_H * SCALE

WINDOW_SCALE = 4
TARGET_FRAMERATE = 50
LOOP_SECONDS = 30
LOOP_FRAMES = TARGET_FRAMERATE * LOOP_SECONDS

SAMPLE_RATE = 44100
SAMPLES_PER_FRAME = SAMPLE_RATE // TARGET_FRAMERATE
AUDIO_GAIN = 0.22

COLOR_BG = (0xFF, 0x82, 0x00)
COLOR_INK = (0x00, 0x00, 0x00)

UP_BUTTON = 0x01
DOWN_BUTTON = 0x02
LEFT_BUTTON = 0x04
RIGHT_BUTTON = 0x08
A_BUTTON = 0x10
B_BUTTON = 0x20

FACING_RIGHT = 0
FACING_LEFT = 1

LEVEL_WIDTH = 384
LEVEL_HEIGHT = 384
LEVEL_WIDTH_CELLS = 24
LEVEL_HEIGHT_CELLS = 24
LEVEL_ARRAY_SIZE = 576

FIXED_POINT = 5
PLAYER_SPEED_WALKING = 1 << FIXED_POINT
PLAYER_SPEED_AIR = 2
PLAYER_PARTICLES = 3
PLAYER_JUMP_VELOCITY = (1 << FIXED_POINT) + 8
PLAYER_JUMP_TIME = 11
GRAVITY = 3
FRICTION = 1
MAX_XSPEED = PLAYER_SPEED_WALKING
MAX_XSPEED_FAN = 54
MAX_YSPEED = 3 * (1 << FIXED_POINT)
CAMERA_OFFSET = 16
TIMER_AMOUNT = 48

MAX_PER_TYPE = 6
FAN_POWER = 5
FAN_UP = 0
FAN_RIGHT = 1
FAN_LEFT = 2
MAX_FAN_PARTICLES = 4

LSTART = 0
LFINISH = 1 << 5
LWALKER = 2 << 5
LFAN = 3 << 5
LSPIKES = 4 << 5
LCOIN = 5 << 5
LKEY = 6 << 5

FONT_SMALL = 1
FONT_BIG = 2
DATA_SCORE = 1
DATA_LEVEL = 2

CAMERA_CENTER_Y = VIEW_H // 2 - 8

R = random.Random(1234)


def rnd(n):
    if n <= 0:
        return 0
    return R.randrange(n)


def _parse_array(text, name):
    m = re.search(r"\b%s\s*\[\s*\]\s*(?:PROGMEM\s*)?=\s*\{" % re.escape(name), text)
    if not m:
        m = re.search(r"\b%s\s*\[\s*\]\s*=\s*\{" % re.escape(name), text)
    if not m:
        raise RuntimeError("array %s not found" % name)
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
    for token in re.findall(r"0[xX][0-9a-fA-F]+|B[01]+|-?\d+", body):
        if token[0] in "bB" and token[1] in "01":
            out.append(int(token[1:], 2))
        elif token.lower().startswith("0x"):
            out.append(int(token, 16))
        else:
            out.append(int(token) & 0xFF)
    return out


_TEXT = open(BITMAPS_PATH, encoding="utf-8", errors="ignore").read()


def _img_from(dataslice, w, h):
    pages = (h + 7) >> 3
    img = np.zeros((h, w), dtype=np.uint8)
    for p in range(pages):
        for i in range(w):
            b = dataslice[p * w + i]
            for bit in range(8):
                yy = p * 8 + bit
                if yy < h:
                    img[yy, i] = (b >> bit) & 1
    return img


class Sprite:
    def __init__(self, name, plusmask=False):
        data = _parse_array(_TEXT, name)
        self.w = data[0]
        self.h = data[1]
        body = data[2:]
        pages = (self.h + 7) >> 3
        self.frames = []
        self.masks = []
        if plusmask:
            fs = self.w * pages * 2
            n = len(body) // fs
            for f in range(n):
                base = f * fs
                img = np.zeros((self.h, self.w), dtype=np.uint8)
                msk = np.zeros((self.h, self.w), dtype=np.uint8)
                for p in range(pages):
                    for i in range(self.w):
                        idx = base + (i + p * self.w) * 2
                        s = body[idx]
                        m = body[idx + 1]
                        for bit in range(8):
                            yy = p * 8 + bit
                            if yy < self.h:
                                img[yy, i] = (s >> bit) & 1
                                msk[yy, i] = (m >> bit) & 1
                self.frames.append(img)
                self.masks.append(msk)
        else:
            fs = self.w * pages
            n = len(body) // fs
            for f in range(n):
                self.frames.append(_img_from(body[f * fs:(f + 1) * fs], self.w, self.h))


SPR_TITLE = Sprite("titleScreen")
SPR_BADGE = Sprite("badgeMysticBalloon")
SPR_STARS = Sprite("stars")
SPR_LEFT_L = Sprite("leftGuyLeftEye")
SPR_LEFT_R = Sprite("leftGuyRightEye")
SPR_RIGHT = Sprite("rightGuyEyes")
BLINK_LEFT = _parse_array(_TEXT, "blinkingEyesLeftGuy")
BLINK_RIGHT = _parse_array(_TEXT, "blinkingEyesRightGuy")

SPR_KID = Sprite("kidSprite")
SPR_BALLOON = Sprite("balloon_plus_mask", plusmask=True)
SPR_PARTICLE = Sprite("particle")
SPR_FAN = Sprite("fan")
SPR_TILES = Sprite("tileSetTwo")
SPR_DOOR = Sprite("door")
SPR_ELEMENTS = Sprite("elements")
SPR_HUD = Sprite("elementsHUD")
SPR_SMALLMASK = Sprite("smallMask")
SPR_NUMBIG = Sprite("numbersBig")
SPR_NUMMASK = Sprite("numbersBigMask")
SPR_NUMMASK01 = Sprite("numbersBigMask01")
SPR_BADGELVL = Sprite("badgeLevel")

LEVEL1 = _parse_array(_TEXT, "level1")


def blit_or(buf, img, x, y):
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
    region[s > 0] = 1


def blit_overwrite(buf, img, x, y):
    h, w = img.shape
    bh, bw = buf.shape
    x0 = max(0, -x)
    y0 = max(0, -y)
    x1 = min(w, bw - x)
    y1 = min(h, bh - y)
    if x0 >= x1 or y0 >= y1:
        return
    buf[y + y0:y + y1, x + x0:x + x1] = img[y0:y1, x0:x1]


def blit_erase(buf, img, x, y):
    h, w = img.shape
    bh, bw = buf.shape
    x0 = max(0, -x)
    y0 = max(0, -y)
    x1 = min(w, bw - x)
    y1 = min(h, bh - y)
    if x0 >= x1 or y0 >= y1:
        return
    region = buf[y + y0:y + y1, x + x0:x + x1]
    region[img[y0:y1, x0:x1] > 0] = 0


def blit_plusmask(buf, img, msk, x, y):
    h, w = img.shape
    bh, bw = buf.shape
    x0 = max(0, -x)
    y0 = max(0, -y)
    x1 = min(w, bw - x)
    y1 = min(h, bh - y)
    if x0 >= x1 or y0 >= y1:
        return
    region = buf[y + y0:y + y1, x + x0:x + x1]
    m = msk[y0:y1, x0:x1] > 0
    region[m] = img[y0:y1, x0:x1][m]


class Vec:
    __slots__ = ("x", "y")

    def __init__(self, x=0, y=0):
        self.x = x
        self.y = y


class Players:
    pass


class Camera:
    pass


class Ardu:
    def __init__(self):
        self.frame_count = 0
        self.cur = 0
        self.prev = 0

    def pressed(self, mask):
        return (self.cur & mask) != 0

    def just_pressed(self, mask):
        return (self.cur & mask) != 0 and (self.prev & mask) == 0

    def every(self, n):
        return n != 0 and (self.frame_count % n) == 0


ardu = Ardu()
kid = Players()
cam = Camera()

PCM = None
CUR_FRAME = 0

coins = [{"pos": Vec(), "active": False} for _ in range(MAX_PER_TYPE)]
fans = [{"pos": Vec(), "height": 0, "active": False, "dir": FAN_UP,
         "particles": [Vec() for _ in range(5)]} for _ in range(MAX_PER_TYPE)]
key = {"pos": Vec(), "active": False, "haveKey": False}
level_exit = Vec()
start_pos = Vec()

coins_active = 0
coins_collected = 0
total_coins = 0
balloons_left = 0
score_player = 0
walker_frame = 0
fan_frame = 0
coin_frame = 0
map_timer = 10
blinking_frames = 0
sparkle_frames = 0


def tone(freq, dur):
    if PCM is None or freq <= 0 or dur <= 0:
        return
    off = CUR_FRAME * SAMPLES_PER_FRAME
    n = int(dur * SAMPLE_RATE / 1000)
    end = min(len(PCM), off + n)
    if end <= off:
        return
    m = end - off
    t = np.arange(m, dtype=np.float32) / SAMPLE_RATE
    w = np.where(np.sin(2.0 * np.pi * freq * t) >= 0.0, 1.0, -1.0).astype(np.float32) * AUDIO_GAIN
    fade = min(m // 2, 88)
    if fade:
        w[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
        w[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    PCM[off:end] += w


def wind_noise():
    if ardu.every(2):
        tone(320 + rnd(20), 30)


def grid_solid(x, y):
    if x < 0 or x >= LEVEL_WIDTH_CELLS:
        return 1
    if y < 0 or y >= LEVEL_HEIGHT_CELLS:
        return 0
    b = LEVEL1[(x >> 3) + y * (LEVEL_WIDTH_CELLS >> 3)]
    return (b >> (x % 8)) & 1


def grid_tile(x, y):
    if not grid_solid(x, y):
        return 16
    l = grid_solid(x - 1, y)
    t = grid_solid(x, y - 1)
    r = grid_solid(x + 1, y)
    b = grid_solid(x, y + 1)
    return r | (t << 1) | (l << 2) | (b << 3)


def enemies_init():
    global coins_active
    coins_active = 0
    for i in range(MAX_PER_TYPE):
        fans[i]["pos"] = Vec(0, 0)
        for a in range(MAX_FAN_PARTICLES):
            fans[i]["particles"][a] = Vec(rnd(16), rnd(16))
        fans[i]["height"] = 0
        fans[i]["active"] = False
        fans[i]["dir"] = FAN_UP
        coins[i]["pos"] = Vec(0, 0)
        coins[i]["active"] = False
    key["pos"] = Vec(0, 0)
    key["active"] = False
    key["haveKey"] = False


def coins_create(x, y):
    global coins_active
    for i in range(MAX_PER_TYPE - 1, -1, -1):
        if not coins[i]["active"]:
            coins_active += 1
            coins[i]["pos"] = Vec((x << 4) + 2, y << 4)
            coins[i]["active"] = True
            return


def key_create(x, y):
    key["pos"] = Vec(x << 4, y << 4)
    key["active"] = True
    key["haveKey"] = False


def fans_create(x, y, height, d=FAN_UP):
    for i in range(MAX_PER_TYPE - 1, -1, -1):
        if not fans[i]["active"]:
            fans[i]["pos"] = Vec(x << 4, y << 4)
            fans[i]["height"] = height << 4
            fans[i]["active"] = True
            fans[i]["dir"] = d
            return


def set_kid():
    kid.pos = Vec(0, 0)
    kid.actualpos = Vec(128, 0)
    kid.speed = Vec(0, 0)
    kid.isActive = True
    kid.isImune = True
    kid.imuneTimer = 0
    kid.jumpTimer = 0
    kid.direction = FACING_RIGHT
    kid.isWalking = False
    kid.isJumping = False
    kid.isLanding = False
    kid.isBalloon = False
    kid.jumpLetGo = True
    kid.isSucking = False
    kid.balloons = 3
    kid.balloonOffset = 0
    kid.particles = [Vec(rnd(16), rnd(16)) for _ in range(PLAYER_PARTICLES)]


def level_load():
    global level_exit, start_pos
    i = LEVEL_ARRAY_SIZE >> 3
    while LEVEL1[i] != 0xFF:
        idv = LEVEL1[i] & 0xE0
        y = LEVEL1[i] & 0x1F
        i += 1
        x = LEVEL1[i] & 0x1F
        i += 1
        if idv == LSTART:
            start_pos = Vec(x << (FIXED_POINT + 4), y << (FIXED_POINT + 4))
            kid.actualpos = Vec(start_pos.x, start_pos.y)
        elif idv == LFINISH:
            level_exit = Vec(x << 4, y << 4)
        elif idv == LWALKER:
            pass
        elif idv == LFAN:
            t = LEVEL1[i]
            i += 1
            if t < 64:
                fans_create(x, y, t)
            elif t < 192:
                fans_create(x, y, t & 0x3F, FAN_RIGHT)
            else:
                fans_create(x, y, t & 0x3F, FAN_LEFT)
        elif idv == LSPIKES:
            pass
        elif idv == LCOIN:
            coins_create(x, y)
        else:
            key_create(x, y)


def is_walker_visible():
    return False


def check_inputs():
    global map_timer
    if kid.balloons <= 0:
        return
    cam.offset = Vec(0, 0)
    kid.isWalking = False
    if ardu.pressed(DOWN_BUTTON):
        cam.offset.y = -CAMERA_OFFSET
    elif ardu.pressed(UP_BUTTON):
        cam.offset.y = CAMERA_OFFSET
    if not kid.isSucking:
        if ardu.pressed(LEFT_BUTTON):
            map_timer = TIMER_AMOUNT
            cam.offset.x = CAMERA_OFFSET
            kid.direction = FACING_LEFT
            if not (kid.isJumping or kid.isBalloon or kid.isLanding):
                if not grid_solid((kid.pos.x - 1) >> 4, (kid.pos.y + 8) >> 4):
                    kid.actualpos.x -= PLAYER_SPEED_WALKING
                kid.isWalking = True
                kid.speed.x = -1
            else:
                if kid.speed.x > -MAX_XSPEED:
                    kid.speed.x -= PLAYER_SPEED_AIR
        elif ardu.pressed(RIGHT_BUTTON):
            cam.offset.x = -CAMERA_OFFSET
            kid.direction = FACING_RIGHT
            if not (kid.isJumping or kid.isBalloon or kid.isLanding):
                if not grid_solid((kid.pos.x + 12) >> 4, (kid.pos.y + 8) >> 4):
                    kid.actualpos.x += PLAYER_SPEED_WALKING
                kid.isWalking = True
                kid.speed.x = 1
            else:
                if kid.speed.x < MAX_XSPEED:
                    kid.speed.x += PLAYER_SPEED_AIR
    kid.isSucking = False
    if ardu.pressed(A_BUTTON):
        if is_walker_visible():
            kid.isBalloon = False
            kid.isSucking = True
    if ardu.just_pressed(B_BUTTON):
        if kid.speed.y == 0 and not kid.isJumping and not kid.isLanding:
            tone(200, 100)
            kid.isWalking = False
            kid.isJumping = True
            kid.jumpLetGo = False
            kid.jumpTimer = PLAYER_JUMP_TIME
            kid.speed.y = PLAYER_JUMP_VELOCITY
            if ardu.pressed(RIGHT_BUTTON):
                kid.speed.x = MAX_XSPEED
            elif ardu.pressed(LEFT_BUTTON):
                kid.speed.x = -MAX_XSPEED
        else:
            if kid.balloons > 0:
                kid.isBalloon = True
                kid.balloonOffset = 16
                kid.isJumping = False
                kid.isLanding = True
    if not ardu.pressed(B_BUTTON):
        kid.isBalloon = False
        if kid.isJumping:
            kid.jumpLetGo = True


def check_kid():
    if kid.isImune:
        if ardu.every(2):
            kid.isActive = not kid.isActive
        kid.imuneTimer += 1
        if kid.imuneTimer > 60:
            kid.imuneTimer = 0
            kid.isImune = False
            kid.isActive = True

    if kid.isWalking or kid.isSucking:
        if ardu.every(8):
            kid.frame = (kid.frame + 1) % 4
            if kid.frame % 2 == 0:
                tone(150, 20)
    else:
        kid.frame = 0

    if not kid.isBalloon and kid.speed.y > 0:
        kid.isJumping = True
        kid.isLanding = False
        if not kid.jumpLetGo and kid.jumpTimer > 0:
            kid.speed.y += GRAVITY + 2
            kid.jumpTimer -= 1
    elif kid.speed.y < 0:
        kid.isJumping = False
        kid.isLanding = True

    tx = (kid.pos.x + 6) >> 4
    ty = (kid.pos.y + 8) >> 4
    solidbelow = grid_solid(tx, (kid.pos.y + 16) >> 4)
    tx2 = (((kid.actualpos.x + kid.speed.x) >> FIXED_POINT) - 1 + (kid.speed.x > 0) * 14) >> 4
    solidH = grid_solid(tx2, (kid.pos.y + 2) >> 4) or grid_solid(tx2, (kid.pos.y + 13) >> 4)
    ty2 = (((kid.actualpos.y - kid.speed.y) >> FIXED_POINT) + (kid.speed.y < 0) * 17) >> 4
    solidV = grid_solid((kid.pos.x + 2) >> 4, ty2) or grid_solid((kid.pos.x + 10) >> 4, ty2)

    if kid.balloons == 0 or kid.speed.y > 0 or not solidbelow:
        kid.speed.y = (kid.speed.y - GRAVITY) if kid.speed.y > -MAX_YSPEED else -MAX_YSPEED
        if kid.isBalloon:
            if kid.balloonOffset > 0:
                kid.balloonOffset -= 2
            else:
                kid.speed.y = max(kid.balloons - 5, kid.speed.y)

    if kid.balloons > 0 and kid.speed.y <= 0 and (solidV or solidbelow):
        if kid.isLanding:
            tone(80, 30)
        kid.speed.y = 0
        kid.speed.x = 0
        kid.isLanding = False
        kid.isJumping = False
        kid.isBalloon = False
        ysnap = ((kid.actualpos.y >> FIXED_POINT) + 12) >> 4
        kid.actualpos.y = ysnap << (FIXED_POINT + 4)
        if not ardu.pressed(RIGHT_BUTTON | LEFT_BUTTON):
            yy = (kid.pos.y + 16) >> 4
            sl = grid_solid((kid.pos.x + 4) >> 4, yy)
            sr = grid_solid((kid.pos.x + 8) >> 4, yy)
            if not sl and grid_solid((kid.pos.x + 11) >> 4, yy):
                kid.actualpos.x -= FIXED_POINT << 2
            elif not sr and grid_solid((kid.pos.x) >> 4, yy):
                kid.actualpos.x += FIXED_POINT << 2
    else:
        if abs(kid.speed.x) > FRICTION:
            if ardu.every(4):
                if kid.speed.x > 0:
                    kid.speed.x -= FRICTION
                elif kid.speed.x < 0:
                    kid.speed.x += FRICTION
        else:
            kid.speed.x = 0

    if not grid_solid(tx, ty):
        if grid_solid((kid.pos.x) >> 4, ty):
            kid.actualpos.x += 8
        elif grid_solid((kid.pos.x + 11) >> 4, ty):
            kid.actualpos.x -= 8

    if kid.balloons == 0 or (not solidV and kid.speed.y != 0):
        kid.actualpos.y -= kid.speed.y
    else:
        if solidV and kid.speed.y > 0:
            kid.actualpos.y = ((kid.pos.y + 8) >> 4) << (FIXED_POINT + 4)
            kid.speed.y = 0
            tone(80, 30)

    if kid.speed.x != 0:
        if not solidH:
            kid.actualpos.x += kid.speed.x
        else:
            kid.speed.x = 0
            kid.actualpos.x = ((((kid.pos.x + 6) >> 4) << 4) + ((not kid.direction) * 4)) << FIXED_POINT

    kid.pos = Vec(kid.actualpos.x >> FIXED_POINT, kid.actualpos.y >> FIXED_POINT)

    if kid.isSucking:
        wind_noise()


def update_camera():
    if kid.balloons == 0:
        return
    if cam.offset.x > 0:
        cam.offset.x -= 1
    elif cam.offset.x < 0:
        cam.offset.x += 1
    if cam.offset.y > 0:
        cam.offset.y -= 1
    elif cam.offset.y < 0:
        cam.offset.y += 1
    cpx = cam.pos.x + cam.offset.x
    cpy = cam.pos.y + cam.offset.y
    vx = (kid.pos.x - cpx - 58) >> 2
    vy = (kid.pos.y - cpy - CAMERA_CENTER_Y) >> 2
    cam.pos.x += vx
    cam.pos.y += vy
    cam.pos.x = min(LEVEL_WIDTH - VIEW_W, max(0, cam.pos.x))
    cam.pos.y = min(LEVEL_HEIGHT - VIEW_H, max(0, cam.pos.y))


def draw_grid(buf):
    spacing = 16
    for x in range(0, VIEW_W // 16 + 2):
        for y in range(0, VIEW_H // 16 + 2):
            blit_or(buf, SPR_TILES.frames[16],
                    x * 16 - (cam.pos.x >> 2) % spacing,
                    y * 16 - (cam.pos.y >> 2) % spacing)
    for x in range(cam.pos.x >> 4, (cam.pos.x >> 4) + 9):
        for y in range(cam.pos.y >> 4, (cam.pos.y >> 4) + VIEW_H // 16 + 1):
            tile = grid_tile(x, y)
            if tile != 16:
                blit_overwrite(buf, SPR_TILES.frames[tile], (x << 4) - cam.pos.x, (y << 4) - cam.pos.y)
    blit_overwrite(buf, SPR_DOOR.frames[1 if key["haveKey"] else 0],
                   level_exit.x - cam.pos.x, level_exit.y - cam.pos.y)


def enemies_update(buf):
    global walker_frame, coin_frame, fan_frame
    if ardu.every(8):
        walker_frame = (walker_frame + 1) % 2
        coin_frame = (coin_frame + 1) % 4
    if key["active"]:
        blit_overwrite(buf, SPR_ELEMENTS.frames[4], key["pos"].x - cam.pos.x, key["pos"].y - cam.pos.y)
    if ardu.every(4):
        fan_frame = (fan_frame + 1) % 3
    for i in range(MAX_PER_TYPE):
        f = fans[i]
        if f["active"]:
            if ardu.every(2):
                for a in range(MAX_FAN_PARTICLES):
                    p = f["particles"][a]
                    p.y = p.y + 6 if p.y < f["height"] else rnd(f["height"] >> 2)
                    if f["dir"] == FAN_UP:
                        blit_erase(buf, SPR_PARTICLE.frames[0],
                                   f["pos"].x + p.x - cam.pos.x, f["pos"].y - p.y - cam.pos.y)
                    elif f["dir"] == FAN_RIGHT:
                        blit_erase(buf, SPR_PARTICLE.frames[0],
                                   f["pos"].x + 16 + p.y - cam.pos.x, f["pos"].y + 16 - p.x - cam.pos.y)
                    else:
                        blit_erase(buf, SPR_PARTICLE.frames[0],
                                   f["pos"].x - p.y - cam.pos.x, f["pos"].y + 16 - p.x - cam.pos.y)
            foff = 3 * f["dir"]
            blit_overwrite(buf, SPR_FAN.frames[fan_frame + foff], f["pos"].x - cam.pos.x, f["pos"].y - cam.pos.y)
        c = coins[i]
        if c["active"]:
            blit_overwrite(buf, SPR_ELEMENTS.frames[coin_frame], c["pos"].x - cam.pos.x, c["pos"].y - cam.pos.y)


def draw_kid(buf):
    if not kid.isActive:
        return
    kx = kid.pos.x - cam.pos.x
    ky = kid.pos.y - cam.pos.y
    if kid.isBalloon:
        commonx = kx - (6 * kid.direction)
        commony = ky + kid.balloonOffset
        if kid.balloons > 1:
            blit_plusmask(buf, SPR_BALLOON.frames[0], SPR_BALLOON.masks[0], commonx + 1, commony - 11)
            if kid.balloons > 2:
                blit_plusmask(buf, SPR_BALLOON.frames[0], SPR_BALLOON.masks[0], commonx + 7, commony - 12)
        blit_plusmask(buf, SPR_BALLOON.frames[0], SPR_BALLOON.masks[0], commonx + 4, commony - 9)
    blit_or(buf, SPR_KID.frames[12 + kid.direction], kx, ky)
    erase_frame = kid.frame + 6 * kid.direction + ((kid.isJumping << 2) + 5 * (kid.isLanding or kid.isBalloon))
    blit_erase(buf, SPR_KID.frames[erase_frame], kx, ky)


def draw_balloon_lives(buf):
    for i in range(kid.balloons):
        blit_overwrite(buf, SPR_HUD.frames[10], (i * 7) + 2, 0)


def draw_coin_hud(buf):
    for i in range(MAX_PER_TYPE - 1, -1, -1):
        if i >= MAX_PER_TYPE - coins_active:
            blit_overwrite(buf, SPR_HUD.frames[11], 40 + (i * 6), 0)
        else:
            blit_overwrite(buf, SPR_HUD.frames[12], 40 + (i * 6), 0)


def draw_numbers(buf, nx, ny, font_type, data):
    if data == DATA_SCORE:
        buf_s = str(int(score_player))
        char_len = len(buf_s)
        pad = 6 - char_len
        blit_or(buf, SPR_NUMMASK.frames[0], nx - 2, ny - 2)
        for i in range(5, -1, -1):
            blit_or(buf, SPR_NUMMASK01.frames[0], nx + 7 * i, ny - 2)
        blit_or(buf, SPR_NUMMASK.frames[1], nx + 41, ny - 2)
    elif data == DATA_LEVEL:
        buf_s = "1"
        char_len = 1
        pad = 2 - char_len
        blit_or(buf, SPR_BADGELVL.frames[0], nx - 2, ny - 9)
    else:
        return
    for i in range(pad):
        if font_type == FONT_SMALL:
            blit_overwrite(buf, SPR_HUD.frames[0], nx + 6 * i, ny)
        elif font_type == FONT_BIG:
            blit_or(buf, SPR_NUMBIG.frames[0], nx + 7 * i, ny)
    for i in range(char_len):
        digit = int(buf_s[i])
        if font_type == FONT_SMALL:
            blit_overwrite(buf, SPR_HUD.frames[digit], nx + (pad * 6) + 6 * i, ny)
        elif font_type == FONT_BIG:
            blit_or(buf, SPR_NUMBIG.frames[digit], nx + (pad * 7) + 7 * i, ny)


def draw_hud(buf):
    for i in range(15, -1, -1):
        blit_or(buf, SPR_SMALLMASK.frames[0], i * 8, 0)
    draw_balloon_lives(buf)
    draw_numbers(buf, 91, 0, FONT_SMALL, DATA_SCORE)
    draw_coin_hud(buf)
    if key["haveKey"]:
        blit_overwrite(buf, SPR_HUD.frames[13], 28, 0)


def collide(r1, r2):
    return not (r2[0] >= r1[0] + r1[2] or r2[0] + r2[2] <= r1[0] or
                r2[1] >= r1[1] + r1[3] or r2[1] + r2[3] <= r1[1])


def check_collisions():
    global coins_active, coins_collected, total_coins, score_player
    if kid.balloons == 0:
        return
    player_rect = (kid.pos.x + 2, kid.pos.y + 2, 8, 12)
    key_rect = (key["pos"].x, key["pos"].y, 8, 16)
    if collide(key_rect, player_rect) and key["active"]:
        key["active"] = False
        key["haveKey"] = True
        tone(420, 200)
    for i in range(MAX_PER_TYPE - 1, -1, -1):
        c = coins[i]
        if c["active"]:
            coin_rect = (c["pos"].x, c["pos"].y, 10, 12)
            if collide(player_rect, coin_rect):
                c["active"] = False
                coins_active -= 1
                coins_collected += 1
                total_coins += 1
                tone(400, 200)
                if coins_active == 0:
                    score_player += 500
                else:
                    score_player += 200
        f = fans[i]
        if f["active"]:
            if f["dir"] == FAN_UP:
                fan_rect = (f["pos"].x, f["pos"].y - f["height"], 16, f["height"])
            elif f["dir"] == FAN_RIGHT:
                fan_rect = (f["pos"].x + 16, f["pos"].y, f["height"], 16)
            else:
                fan_rect = (f["pos"].x - f["height"], f["pos"].y, f["height"], 16)
            if collide(player_rect, fan_rect) and kid.isBalloon:
                if f["dir"] == FAN_UP:
                    kid.speed.y = min(kid.speed.y + FAN_POWER, MAX_YSPEED)
                elif f["dir"] == FAN_RIGHT:
                    kid.speed.x = min(kid.speed.x + FAN_POWER, MAX_XSPEED_FAN)
                else:
                    kid.speed.x = max(kid.speed.x - FAN_POWER, -MAX_XSPEED_FAN)
                wind_noise()


def draw_title_screen(buf):
    for i in range(4):
        blit_or(buf, SPR_TITLE.frames[i], 32 * i, 0)
    blit_or(buf, SPR_BADGE.frames[0], 85, 45)
    blit_or(buf, SPR_STARS.frames[sparkle_frames], 79, 43)
    blit_or(buf, SPR_LEFT_L.frames[BLINK_LEFT[blinking_frames]], 9, 9)
    blit_or(buf, SPR_LEFT_R.frames[BLINK_LEFT[blinking_frames]], 15, 13)
    blit_or(buf, SPR_RIGHT.frames[BLINK_RIGHT[blinking_frames]], 109, 34)


SCRIPT = ((8, 18), (40, 11), (8, 18), (40, 11), (8, 2), (40, 33), (8, 2), (40, 22), (8, 12), (40, 11), (8, 2), (40, 22), (32, 11), (0, 8), (40, 11), (8, 30), (40, 11), (8, 2), (40, 22), (8, 12), (40, 11), (8, 6), (40, 11), (8, 2), (40, 22), (32, 11), (0, 8), (36, 11), (4, 12), (32, 11), (0, 8), (36, 11), (4, 6), (36, 11), (4, 2), (36, 33), (4, 2), (36, 22), (4, 6), (36, 11), (4, 2), (36, 22), (4, 12), (36, 11), (4, 2), (36, 22), (4, 12), (36, 11), (4, 6), (36, 11), (4, 2), (36, 22), (4, 12), (36, 11), (4, 12), (36, 11), (4, 6), (36, 11), (4, 6), (36, 11), (4, 2), (36, 22), (32, 11), (0, 8), (40, 11), (8, 6), (40, 11), (8, 12), (40, 11), (8, 6), (40, 11), (8, 6), (40, 11), (8, 2), (40, 22), (8, 12), (40, 11), (8, 6), (40, 11), (8, 6), (40, 11), (8, 18), (40, 11), (8, 6), (40, 11), (8, 6), (40, 11), (8, 6), (40, 11), (8, 6), (40, 11), (8, 6), (40, 11), (8, 2), (40, 33), (8, 2), (40, 22), (4, 12), (40, 11), (8, 6), (36, 11), (4, 30), (36, 11), (4, 6), (36, 11), (4, 6), (36, 11), (4, 18), (36, 11), (4, 2), (36, 33), (4, 2), (36, 33), (4, 2), (36, 33), (4, 6), (36, 11), (4, 2), (36, 22), (4, 24), (36, 11), (4, 2), (36, 22), (4, 12))

SCRIPT_FRAMES = []
for _mask, _count in SCRIPT:
    SCRIPT_FRAMES.extend([_mask] * _count)


def autopilot(f):
    if f < len(SCRIPT_FRAMES):
        return SCRIPT_FRAMES[f]
    return 0


def init_game():
    global score_player, coins_collected, total_coins, coins_active, blinking_frames, sparkle_frames
    set_kid()
    cam.pos = Vec(0, LEVEL_HEIGHT - VIEW_H)
    cam.offset = Vec(0, 0)
    enemies_init()
    level_load()
    kid.frame = 0
    score_player = 0
    coins_collected = 0
    total_coins = 0
    blinking_frames = 0
    sparkle_frames = 0


def step(f):
    global blinking_frames, sparkle_frames, CUR_FRAME
    CUR_FRAME = f
    ardu.prev = ardu.cur
    ardu.cur = autopilot(f)
    ardu.frame_count += 1
    if ardu.every(8):
        blinking_frames = (blinking_frames + 1) % 32
    if ardu.every(10):
        sparkle_frames = (sparkle_frames + 1) % 5
    check_inputs()
    check_kid()
    update_camera()
    game_buf = np.zeros((VIEW_H, VIEW_W), dtype=np.uint8)
    draw_grid(game_buf)
    enemies_update(game_buf)
    draw_kid(game_buf)
    draw_hud(game_buf)
    check_collisions()
    title_buf = np.zeros((TITLE_H, TITLE_W), dtype=np.uint8)
    draw_title_screen(title_buf)
    return game_buf, title_buf


def main():
    global PCM

    pygame.init()
    try:
        pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=2, buffer=512)
        pygame.mixer.set_num_channels(16)
        mixer_ok = True
    except Exception:
        mixer_ok = False
    pygame.display.set_caption("Mystic Balloon - level 1 loop")

    n = max(1, WINDOW_SCALE)
    win_w, win_h = VIDEO_W // n, VIDEO_H // n
    screen = pygame.display.set_mode((win_w, win_h))
    frame_surf = pygame.Surface((VIDEO_W, VIDEO_H))
    game_surf = pygame.Surface((VIEW_W, VIEW_H))
    title_surf = pygame.Surface((TITLE_W, TITLE_H))
    clock = pygame.time.Clock()
    palette = np.array([COLOR_INK, COLOR_BG], dtype=np.uint8)

    PCM = np.zeros(LOOP_FRAMES * SAMPLES_PER_FRAME + SAMPLE_RATE, dtype=np.float32)

    init_game()
    fd, audio_path = tempfile.mkstemp(prefix="myblab-", suffix=".wav")
    os.close(fd)
    recorder = None
    chunks = []

    try:
        recorder = video.VideoRecorder(video.output_path(__file__), VIDEO_W, VIDEO_H, TARGET_FRAMERATE, audio_path)
        running = True
        for i in range(LOOP_FRAMES):
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False
            if not running:
                break
            game_buf, title_buf = step(i)
            pygame.surfarray.blit_array(game_surf, np.transpose(palette[game_buf], (1, 0, 2)))
            pygame.surfarray.blit_array(title_surf, np.transpose(palette[title_buf], (1, 0, 2)))
            frame_surf.fill(COLOR_BG)
            frame_surf.blit(pygame.transform.scale(title_surf, (RENDER_W, TITLE_RENDER_H)), (MARGIN, TITLE_TOP_Y))
            frame_surf.blit(pygame.transform.scale(game_surf, (RENDER_W, RENDER_H)), (MARGIN, GAME_TOP_Y))
            recorder.write(frame_surf)
            screen.blit(pygame.transform.scale(frame_surf, (win_w, win_h)), (0, 0))
            pygame.display.flip()
            if mixer_ok:
                try:
                    seg = PCM[i * SAMPLES_PER_FRAME:(i + 1) * SAMPLES_PER_FRAME]
                    pcm = (np.clip(seg, -1.0, 1.0) * 32767.0).astype(np.int16)
                    snd = pygame.sndarray.make_sound(np.repeat(pcm[:, None], 2, axis=1))
                    chunks.append(snd)
                    snd.play()
                except Exception:
                    pass
            clock.tick(TARGET_FRAMERATE)
        pcm_all = (np.clip(PCM[:LOOP_FRAMES * SAMPLES_PER_FRAME], -1.0, 1.0) * 32767.0).astype("<i2")
        with wave.open(audio_path, "wb") as wv:
            wv.setnchannels(1)
            wv.setsampwidth(2)
            wv.setframerate(SAMPLE_RATE)
            wv.writeframes(pcm_all.tobytes())
    finally:
        if recorder is not None:
            recorder.close()
        if os.path.exists(audio_path):
            os.unlink(audio_path)
        pygame.quit()


if __name__ == "__main__":
    main()
